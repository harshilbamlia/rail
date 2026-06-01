#!/usr/bin/env python3
"""
Aonami Echo — Step 4: prioritisation / triage layer with RDSO escalation rules.

Combines the Step-3 predictions with deterministic RDSO rules into a single
SE/JE "morning priority queue":

  * Annexure II-A severity bands   : <30 / 30-60 / 60-80 / >=80 %FSH
  * Para 6.3.2 four-metre cluster  : >=2 defects within 4 m of track -> escalate
                                     (single rail-piece replacement work order)
  * IMR / Para 6.4 SLA clock       : >=80 %FSH (or IMR class) & still in track
                                     -> 3-day replacement SLA; flag breaches
  * Triage level                   : IMMEDIATE / URGENT / MONITOR / ROUTINE
                                     from severity + predicted RUL + cluster

Reference date for SLA: 2026-05-31 (passed in; scripts can't call now()).

Outputs:
  triage_queue.csv     -- every defect with triage level + reasons
  clusters.csv         -- Para 6.3.2 clusters (>=2 defects within 4 m)
  TRIAGE_STEP4.md      -- written report
  (also loaded into usfd.db: triage_queue, clusters, view morning_queue)
"""
import os, sqlite3
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
TODAY = pd.Timestamp("2026-05-31")
CLUSTER_M = 4.0          # Para 6.3.2 window
SLA_DAYS = 3             # Para 6.4 IMR replacement SLA

pred = pd.read_csv(ROOT + "/usfd_predictions.csv")
clean = pd.read_csv(ROOT + "/usfd_clean.csv", parse_dates=["insp_dt"])

# ---- latest inspection date + latest classification per defect -------------
last = (clean.sort_values("insp_dt")
        .groupby("defect_key")
        .agg(last_insp=("insp_dt", "max"),
             last_class=("classification", "last"),
             removed=("removed", "last"),
             line=("line", "first"),
             gmt_carried=("gmt_carried", "first"))
        .reset_index())
df = pred.merge(last, on="defect_key", how="left")

# ---- numeric chainage in metres -------------------------------------------
df["km_n"] = pd.to_numeric(df["km"], errors="coerce")
df["meter_n"] = pd.to_numeric(df["meter"], errors="coerce")
df["chainage_m"] = df["km_n"] * 1000 + df["meter_n"]

# ---- Annexure II-A severity band ------------------------------------------
def band(a):
    if pd.isna(a): return "unknown"
    if a >= 80: return ">=80 (IMR-level)"
    if a >= 60: return "60-80"
    if a >= 30: return "30-60"
    return "<30"
df["severity_band"] = df["latest_amp"].map(band)

# ---- Para 6.3.2 four-metre clustering --------------------------------------
# cluster within the same physical track length: (railway, section, line).
df = df.sort_values(["railway", "section", "line", "chainage_m"]).reset_index(drop=True)
cluster_id = np.full(len(df), -1, dtype=int)
cid = 0
clusters = []
for _, g in df.groupby(["railway", "section", "line"], sort=False):
    g = g.dropna(subset=["chainage_m"]).sort_values("chainage_m")
    if g.empty: continue
    idx = g.index.to_numpy(); ch = g["chainage_m"].to_numpy()
    start = 0
    for i in range(1, len(g) + 1):
        if i == len(g) or (ch[i] - ch[i - 1]) > CLUSTER_M:
            members = idx[start:i]
            if len(members) >= 2:                      # a real cluster
                cluster_id[members] = cid
                clusters.append({
                    "cluster_id": cid,
                    "railway": g["railway"].iloc[0], "section": g["section"].iloc[0],
                    "line": g["line"].iloc[0],
                    "chainage_start_m": ch[start], "chainage_end_m": ch[i - 1],
                    "span_m": ch[i - 1] - ch[start], "n_defects": len(members),
                    "max_amp": float(df.loc[members, "latest_amp"].max()),
                })
                cid += 1
            start = i
df["cluster_id"] = cluster_id
df["para632_escalate"] = df["cluster_id"] >= 0
clusters_df = pd.DataFrame(clusters)

# ---- IMR + Para 6.4 SLA ----------------------------------------------------
df["is_imr_level"] = (df["latest_amp"] >= 80) | \
                     df["last_class"].astype(str).str.contains("IMR", case=False, na=False)
df["in_track"] = ~df["removed"].astype(str).str.contains("remov", case=False, na=False)
df["days_since_insp"] = (TODAY - df["last_insp"]).dt.days
df["sla_breach"] = df["is_imr_level"] & df["in_track"] & (df["days_since_insp"] > SLA_DAYS)

# ---- triage level ----------------------------------------------------------
def triage(r):
    if r["is_imr_level"] or r["para632_escalate"]:
        return "IMMEDIATE"
    y = r["yrs_to_80"]
    if np.isfinite(y) and y <= 2:               return "URGENT"
    if (np.isfinite(y) and y <= 5) or (r["latest_amp"] >= 60): return "MONITOR"
    return "ROUTINE"
df["triage_level"] = df.apply(triage, axis=1)

# reasons (human-readable)
def reasons(r):
    out = []
    if r["latest_amp"] >= 80: out.append("amp>=80%FSH (IMR-level)")
    if r["para632_escalate"]: out.append("Para 6.3.2 cluster")
    if r["sla_breach"]:       out.append(f"SLA breach ({int(r['days_since_insp'])}d)")
    if np.isfinite(r["yrs_to_80"]) and r["yrs_to_80"] <= 2: out.append(f"~{r['yrs_to_80']:.1f}y to IMR")
    if r["latest_amp"] >= 60 and r["latest_amp"] < 80: out.append("60-80%FSH")
    return "; ".join(out) or "monitor"
df["reasons"] = df.apply(reasons, axis=1)

# ---- composite ordering ----------------------------------------------------
LEVELS = {"IMMEDIATE": 0, "URGENT": 1, "MONITOR": 2, "ROUTINE": 3}
df["lvl"] = df["triage_level"].map(LEVELS)
df["yrs80_sort"] = df["yrs_to_80"].replace([np.inf, -np.inf], 1e9).fillna(1e9)
df = df.sort_values(["lvl", "latest_amp", "yrs80_sort"],
                    ascending=[True, False, True]).reset_index(drop=True)
df["triage_rank"] = df.index + 1

cols = ["triage_rank", "triage_level", "railway", "section", "line", "km", "meter",
        "chainage_m", "rail", "asset_type", "latest_amp", "severity_band",
        "yrs_to_80", "para632_escalate", "cluster_id", "is_imr_level", "sla_breach",
        "days_since_insp", "in_track", "gmt_carried", "n_readings", "reasons", "defect_key"]
out = df[cols]
out.to_csv(ROOT + "/triage_queue.csv", index=False)
clusters_df.to_csv(ROOT + "/clusters.csv", index=False)

# ---- load into DB ----------------------------------------------------------
con = sqlite3.connect(ROOT + "/usfd.db")
out.to_sql("triage_queue", con, if_exists="replace", index=False)
clusters_df.to_sql("clusters", con, if_exists="replace", index=False)
cur = con.cursor()
cur.execute("CREATE INDEX IF NOT EXISTS ix_tq_rank ON triage_queue(triage_rank);")
cur.execute("CREATE INDEX IF NOT EXISTS ix_tq_level ON triage_queue(triage_level);")
cur.execute("DROP VIEW IF EXISTS morning_queue;")
cur.execute("""CREATE VIEW morning_queue AS
    SELECT triage_rank, triage_level, railway, section, km, meter, rail,
           latest_amp, severity_band, reasons
    FROM triage_queue WHERE triage_level IN ('IMMEDIATE','URGENT')
    ORDER BY triage_rank;""")
con.commit(); con.close()

# ---- report ----------------------------------------------------------------
lvl_counts = out["triage_level"].value_counts().reindex(
    ["IMMEDIATE", "URGENT", "MONITOR", "ROUTINE"]).fillna(0).astype(int)
L = []; A = L.append
A("# USFD Step 4 — RDSO triage & escalation\n")
A("## Rules applied")
A(f"- **Annexure II-A** severity bands: <30 / 30-60 / 60-80 / ≥80 %FSH")
A(f"- **Para 6.3.2** four-metre cluster: ≥2 defects within {CLUSTER_M:.0f} m of track → escalate")
A(f"- **Para 6.4** IMR SLA: ≥80 %FSH (or IMR class) still in track and >{SLA_DAYS} days → breach")
A(f"- reference date for SLA: {TODAY.date()}\n")
A("## Triage levels")
A("| level | defects | meaning |")
A("|---|---|---|")
A(f"| IMMEDIATE | {lvl_counts['IMMEDIATE']} | IMR-level amplitude or 4-m cluster |")
A(f"| URGENT | {lvl_counts['URGENT']} | predicted ≤2 yrs to IMR |")
A(f"| MONITOR | {lvl_counts['MONITOR']} | ≤5 yrs to IMR or ≥60 %FSH |")
A(f"| ROUTINE | {lvl_counts['ROUTINE']} | stable, low amplitude |")
A("")
A("## Escalation & SLA")
A(f"- Para 6.3.2 clusters (≥2 within 4 m): **{len(clusters_df)}** clusters covering "
  f"**{int(out.para632_escalate.sum())}** defects")
if len(clusters_df):
    A(f"  - largest cluster: **{int(clusters_df.n_defects.max())}** defects in "
      f"{clusters_df.loc[clusters_df.n_defects.idxmax(),'span_m']:.1f} m")
A(f"- IMR-level defects: **{int(out.is_imr_level.sum())}**")
A(f"- IMR still in track past {SLA_DAYS}-day SLA (**breaches**): **{int(out.sla_breach.sum())}**")
A("")
A("## Severity distribution (Annexure II-A)")
for b in [">=80 (IMR-level)", "60-80", "30-60", "<30", "unknown"]:
    A(f"- {b}: **{int((out.severity_band==b).sum())}**")
A("")
A("## Top-20 IMMEDIATE/URGENT queue")
A("| rank | level | railway | section | km+m | rail | %FSH | reasons |")
A("|---|---|---|---|---|---|---|---|")
for _, x in out.head(20).iterrows():
    A(f"| {int(x.triage_rank)} | {x.triage_level} | {str(x.railway).replace(' Railway','')} | "
      f"{x.section} | {x.km}+{x.meter} | {x.rail} | {x.latest_amp:.0f} | {x.reasons} |")
A("")
A("## Notes")
A("- Clustering is track-level (railway×section×line, both rails) → conservative "
  "(flags more). Per-rail clustering would be stricter; switch if RDSO intends per-rail.")
A("- Most SLA 'breaches' reflect that the latest inspection in the dataset is months "
  "old — in production the clock starts at upload, not at this static reference date.")
A("- IMMEDIATE is dominated by already-high-amplitude defects + dense clusters; "
  "URGENT/MONITOR come from the Step-3 RUL projection (weak per-defect; treat as ranking).")
open(ROOT + "/TRIAGE_STEP4.md", "w").write("\n".join(L) + "\n")

print(f"defects={len(out)} IMMEDIATE={lvl_counts['IMMEDIATE']} URGENT={lvl_counts['URGENT']} "
      f"MONITOR={lvl_counts['MONITOR']} ROUTINE={lvl_counts['ROUTINE']} "
      f"clusters={len(clusters_df)} sla_breach={int(out.sla_breach.sum())}")
