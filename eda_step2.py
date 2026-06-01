#!/usr/bin/env python3
"""
Aonami Echo — Step 2: cleaning, time/GMT axis, growth features, EDA.

Reads  usfd_long.csv  (Step 1 output) and produces:
  usfd_clean.csv      -- long table, cleaned + parsed dates + per-round GMT
  usfd_defects.csv    -- one row per physical defect with growth features
  EDA_STEP2.md        -- written report
  plots/*.png         -- distributions & sample trajectories

Cleaning rules:
  * amplitude_pct > 100  -> invalid (set NaN)  (8 stray data-entry errors)
  * amplitude_pct == 0   -> kept, but flagged (likely "no echo this round")
  * inspection_date / laying_month / weld_date parsed as dd/mm/yyyy & mm/yyyy
  * per-round cumulative GMT interpolated linearly between laying date and the
    defect's latest inspection date, using gmt_carried as the total.
"""
import os, math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.abspath(__file__))
PLOTS = os.path.join(ROOT, "plots")
os.makedirs(PLOTS, exist_ok=True)

THRESHOLDS = [30, 60, 80]   # RDSO Annexure II-A %FSH response thresholds

def parse_dmy(s):
    return pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")

def parse_my(s):
    # laying month like "02/2005"; weld date may be dd/mm/yyyy or mm/yyyy
    d = pd.to_datetime(s, format="%m/%Y", errors="coerce")
    d2 = pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
    return d.fillna(d2)

# ---------------------------------------------------------------- load + clean
df = pd.read_csv(ROOT + "/usfd_long.csv")

df["amp_raw"] = df["amplitude_pct"]
df["amp"] = df["amplitude_pct"].where((df["amplitude_pct"] >= 0) &
                                      (df["amplitude_pct"] <= 100))
df["amp_is_zero"] = df["amp_raw"] == 0
df["amp_invalid"] = df["amplitude_pct"].notna() & (df["amplitude_pct"] > 100)

df["insp_dt"] = parse_dmy(df["inspection_date"])
df["lay_dt"] = parse_my(df["laying_month"])
df["weld_dt"] = parse_my(df["weld_date"])
df["asset_install_dt"] = df["lay_dt"].fillna(df["weld_dt"])

# stable key for one physical defect = one row in the source wide report
df["defect_key"] = df["source_file"].astype(str) + "#" + df["sr_no"].astype(str)

# --------------------------------------- per-round cumulative GMT (vectorised)
grp = df.groupby("defect_key")
install = grp["asset_install_dt"].transform("min")
gmt_total = grp["gmt_carried"].transform("first")
latest = grp["insp_dt"].transform("max")
span_yrs = (latest - install).dt.days / 365.25
rate = gmt_total / span_yrs
rate = rate.where(span_yrs > 0)
df["gmt_rate_per_yr"] = rate
yrs = (df["insp_dt"] - install).dt.days / 365.25
df["gmt_at_round"] = (rate * yrs).clip(lower=0)

df.to_csv(ROOT + "/usfd_clean.csv", index=False)

# ------------------------------------------- per-defect growth feats (loop)
rows = []
for key, g in df.groupby("defect_key"):
    g = g.sort_values("insp_dt")
    v = g.dropna(subset=["amp", "insp_dt"])
    out = {
        "defect_key": key,
        "railway": g["railway"].iloc[0],
        "section": g["section"].iloc[0],
        "line": g["line"].iloc[0],
        "asset_type": g["asset_type"].iloc[0],
        "km": g["km"].iloc[0],
        "meter": g["meter"].iloc[0],
        "rail": g["rail"].iloc[0],
        "gmt_carried": g["gmt_carried"].iloc[0],
        "gmt_rate_per_yr": g["gmt_rate_per_yr"].iloc[0],
        "n_readings": len(v),
        "has_zero": bool(g["amp_is_zero"].any()),
        "has_invalid": bool(g["amp_invalid"].any()),
        "amp_first": np.nan, "amp_last": np.nan, "amp_max": np.nan, "amp_delta": np.nan,
        "span_days": np.nan, "span_yrs": np.nan, "rate_per_yr": np.nan,
        "gmt_span": np.nan, "rate_per_gmt": np.nan,
        "monotonic_up": False, "any_increase": False, "max_threshold_crossed": 0,
    }
    if len(v):
        amps = v["amp"].to_numpy(); dts = v["insp_dt"]
        out["amp_first"] = amps[0]; out["amp_last"] = amps[-1]
        out["amp_max"] = float(amps.max()); out["amp_delta"] = amps[-1] - amps[0]
        out["max_threshold_crossed"] = max([t for t in THRESHOLDS if amps.max() >= t], default=0)
        if len(v) >= 2:
            days = (dts.iloc[-1] - dts.iloc[0]).days
            out["span_days"] = days
            out["span_yrs"] = days / 365.25 if days else np.nan
            if out["span_yrs"]:
                out["rate_per_yr"] = out["amp_delta"] / out["span_yrs"]
            gv = v["gmt_at_round"].dropna()
            if len(gv) >= 2 and (gv.iloc[-1] - gv.iloc[0]) > 0:
                out["gmt_span"] = gv.iloc[-1] - gv.iloc[0]
                out["rate_per_gmt"] = out["amp_delta"] / out["gmt_span"]
            out["monotonic_up"] = bool(np.all(np.diff(amps) >= 0))
            out["any_increase"] = bool(np.any(np.diff(amps) > 0))
    rows.append(out)

defects = pd.DataFrame(rows)
defects.to_csv(ROOT + "/usfd_defects.csv", index=False)

# ---------------------------------------------------------------------- plots
def save(fig, name):
    fig.tight_layout(); fig.savefig(os.path.join(PLOTS, name), dpi=90); plt.close(fig)

# 1. amplitude distribution (cleaned)
fig, ax = plt.subplots(figsize=(7, 4))
ax.hist(df["amp"].dropna(), bins=range(0, 105, 5), color="#3b6", edgecolor="white")
for t in THRESHOLDS: ax.axvline(t, color="crimson", ls="--", lw=1)
ax.set(title="Echo amplitude (%FSH) — cleaned", xlabel="%FSH", ylabel="readings")
save(fig, "01_amplitude_hist.png")

# 2. growth rate per year distribution (>=2 readings)
gr = defects["rate_per_yr"].dropna()
fig, ax = plt.subplots(figsize=(7, 4))
ax.hist(gr.clip(-60, 60), bins=40, color="#48c", edgecolor="white")
ax.axvline(0, color="k", lw=1)
ax.set(title=f"Growth rate (%FSH/yr), n={len(gr)} defects (clipped ±60)",
       xlabel="%FSH per year", ylabel="defects")
save(fig, "02_growth_rate_hist.png")

# 3. sample trajectories (defects with 3 readings, biggest increases)
multi = defects[(defects.n_readings >= 3)].sort_values("amp_delta", ascending=False)
sample_keys = multi.head(40)["defect_key"].tolist()
fig, ax = plt.subplots(figsize=(7, 5))
for k in sample_keys:
    g = df[df.defect_key == k].dropna(subset=["amp", "insp_dt"]).sort_values("insp_dt")
    if len(g) >= 2:
        ax.plot(g["insp_dt"], g["amp"], marker="o", lw=.8, alpha=.6)
for t in THRESHOLDS: ax.axhline(t, color="crimson", ls="--", lw=.8)
ax.set(title="Top-40 fastest-growing defects — amplitude trajectories",
       xlabel="inspection date", ylabel="%FSH")
save(fig, "03_top_trajectories.png")

# 4. amplitude by railway (boxplot)
fig, ax = plt.subplots(figsize=(9, 4.5))
order = df.groupby("railway")["amp"].median().sort_values().index.tolist()
data = [df[df.railway == r]["amp"].dropna() for r in order]
ax.boxplot(data, labels=[r.replace(" Railway", "") for r in order], showfliers=False)
ax.set(title="Echo amplitude by railway", ylabel="%FSH")
plt.xticks(rotation=30, ha="right")
save(fig, "04_amp_by_railway.png")

# ---------------------------------------------------------------------- report
d2 = defects[defects.n_readings >= 2]
def pct(n, d): return f"{100*n/d:.0f}%" if d else "—"
L = []
A = L.append
A("# USFD Step 2 — cleaning, time/GMT axis, growth EDA\n")
A("## Cleaning")
A(f"- amplitude readings (raw non-null): **{int(df.amp_raw.notna().sum())}**")
A(f"- invalid amplitude >100%FSH set to NaN: **{int(df.amp_invalid.sum())}** "
  f"(values were 101–252)")
A(f"- zero readings kept & flagged: **{int(df.amp_is_zero.sum())}** "
  f"(likely 'no echo above threshold' that round)")
A(f"- inspection dates parsed: **{int(df.insp_dt.notna().sum())} / {int(df.inspection_date.notna().sum())}**")
A(f"- asset install date (laying/weld) parsed: **{int(df.asset_install_dt.notna().sum())} / {len(df)}** rows")
A(f"- per-round GMT computed for: **{int(df.gmt_at_round.notna().sum())}** rows")
A("")
A("## Defect cohort")
A(f"- physical defects (unique source row): **{len(defects)}**")
A(f"- with ≥2 valid amplitude readings (growth-usable): **{len(d2)}** ({pct(len(d2),len(defects))})")
A(f"- with all 3 readings: **{int((defects.n_readings>=3).sum())}**")
A(f"- defects containing a zero reading: **{int(defects.has_zero.sum())}**")
A("")
A("## Growth behaviour (defects with ≥2 readings)")
A(f"- amplitude **increased** over time: **{int(d2.any_increase.sum())}** ({pct(int(d2.any_increase.sum()),len(d2))})")
A(f"- strictly monotonic up: **{int(d2.monotonic_up.sum())}** ({pct(int(d2.monotonic_up.sum()),len(d2))})")
A(f"- net Δ amplitude: mean **{d2.amp_delta.mean():.1f}**, median **{d2.amp_delta.median():.1f}** %FSH")
A(f"- growth rate %FSH/yr: mean **{d2.rate_per_yr.mean():.1f}**, "
  f"median **{d2.rate_per_yr.median():.1f}**, p90 **{d2.rate_per_yr.quantile(.9):.1f}**")
rg = d2.rate_per_gmt.dropna()
A(f"- growth rate %FSH/GMT: median **{rg.median():.3f}** (n={len(rg)})")
A("")
A("## Severity (max amplitude reached, by threshold band)")
for t, label in [(0, "<30 (low)"), (30, "≥30"), (60, "≥60"), (80, "≥80 (near-IMR)")]:
    n = int((defects.amp_max >= t).sum()) if t else int((defects.amp_max < 30).sum())
    A(f"- {label}: **{n}**")
A("")
A("## Plots (in ./plots)")
for p in ["01_amplitude_hist.png", "02_growth_rate_hist.png",
          "03_top_trajectories.png", "04_amp_by_railway.png"]:
    A(f"- {p}")
A("")
A("## Caveats")
A("- ≤3 points/defect → per-defect curve fitting weak; pool by family/section in Step 3.")
A("- GMT axis is interpolated (gmt_carried assumed cumulative to latest inspection); "
  "rescales time roughly linearly per defect.")
A("- amplitude noisy/non-monotonic (probe-angle changes between rounds); "
  "many defects bounce rather than grow monotonically.")
with open(ROOT + "/EDA_STEP2.md", "w") as fh:
    fh.write("\n".join(L) + "\n")

print(f"defects={len(defects)} growth_cohort={len(d2)} "
      f"increased={int(d2.any_increase.sum())} "
      f"median_rate_yr={d2.rate_per_yr.median():.2f}")
