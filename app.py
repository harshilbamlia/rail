#!/usr/bin/env python3
"""
Aonami Echo — web app + API over usfd.db and model.pkl.

Run:   .venv/bin/uvicorn app:app --reload --port 8000
Open:  http://localhost:8000/        -> the Echo dashboard
       http://localhost:8000/docs    -> API docs
"""
import os, re, math, sqlite3, json, io, tempfile
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

ROOT = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(ROOT, "usfd.db")
# Prefer root public/ (Vercel), fall back to prototype-echo-app/public/ (local dev)
PUB  = os.path.join(ROOT, "public") if os.path.isdir(os.path.join(ROOT, "public")) \
       else os.path.join(ROOT, "prototype-echo-app", "public")

app = FastAPI(title="Aonami Echo API", version="3.0",
              description="USFD run-on-run defect triage — dashboard + API.")

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ───────────────────────────── helpers ──────────────────────────────

_MODEL = None
def get_model():
    global _MODEL
    if _MODEL is None:
        import joblib
        _MODEL = joblib.load(os.path.join(ROOT, "model.pkl"))
    return _MODEL

def rows(sql, params=()):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()

def short_probe(p: str) -> str:
    """Clean raw probe string → canonical label like '70°CF', '37°CB', '0°'."""
    if not p:
        return "—"
    p = str(p).strip()
    if re.search(r"\b0\b", p) or p.startswith("0"):
        return "0°"
    if re.search(r"37.*NGF|37.*NGB", p, re.I):
        return "37°NGF"
    if re.search(r"37.*GF|37.*GaugeF", p, re.I):
        return "37°GF"
    if re.search(r"37.*B|37.*back", p, re.I):
        return "37°CB"
    if re.search(r"37", p):
        return "37°CF"
    if re.search(r"70.*NGF|70.*NGB", p, re.I):
        return "70°NGF"
    if re.search(r"70.*GF|70.*GaugeF", p, re.I):
        return "70°GF"
    if re.search(r"70.*B|70.*back", p, re.I):
        return "70°CB"
    if re.search(r"70", p):
        return "70°CF"
    return p[:8]

def classify_ui(classification: str, asset_type: str) -> str:
    """Map DB classification + asset to UI badge string."""
    if not classification:
        return "OBS"
    c = classification.strip().upper()
    w = "weld" in (asset_type or "").lower()
    if c in ("IMR", "IMRW", "IMR(W)"):
        return "IMRW" if w else "IMR"
    if c in ("OBS", "OBS(JP)", "OBS(W)", "OBSW"):
        return "OBSW" if w else "OBS"
    if c in ("DFWO",): return "DFWO"
    if c in ("DFWR",): return "DFWR"
    if c in ("DFWN",): return "DFWN"
    if c in ("GW", "GR", "GCC"): return "GCC"
    return "OBS"

def sla_status(is_imr: int, sla_breach: int, days_since_insp):
    """Derive per-SLA status from triage_queue fields."""
    if not is_imr:
        return None, None
    if sla_breach:
        return "overdue", "overdue"
    return "pending", "pending"

def fin(x):
    if x is None: return None
    try:
        v = float(x)
        return None if (math.isnan(v) or math.isinf(v) or v > 1e8) else round(v, 3)
    except Exception:
        return None

def fin_risk(x):
    """Normalize risk_score: sentinel values >1e5 mean already-breached → map to 1.0."""
    if x is None: return None
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v): return None
        if v > 1e5: return 1.0
        return round(max(0.0, min(1.0, v)), 3)
    except Exception:
        return None

def r2_of(amps, gmts):
    """R² of amplitude vs GMT linear fit for evidence quality."""
    pts = [(g, a) for g, a in zip(gmts, amps)
           if g is not None and a is not None]
    n = len(pts)
    if n < 2:
        return None
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    mx = sum(xs)/n; my = sum(ys)/n
    ssxy = sum((x-mx)*(y-my) for x,y in zip(xs, ys))
    ssxx = sum((x-mx)**2 for x in xs)
    ssto = sum((y-my)**2 for y in ys)
    if ssxx == 0 or ssto == 0:
        return None
    b = ssxy / ssxx
    ss_res = sum((y - (my + b*(x-mx)))**2 for x,y in zip(xs,ys))
    r2 = 1 - ss_res/ssto
    return round(max(0, min(1, r2)), 2)

def build_defect_ui(tq: dict, hist: list, pred: dict | None) -> dict:
    """Convert a triage_queue row + inspection history to prototype-compatible shape."""
    cls_ui = classify_ui(
        hist[0].get("classification","") if hist else "",
        tq.get("asset_type","")
    )
    # predicted class — use triage_level
    lvl = tq.get("triage_level", "ROUTINE")
    is_weld = "weld" in (tq.get("asset_type","") or "").lower()
    predicted_cls = ("IMRW" if is_weld else "IMR") if lvl in ("IMMEDIATE","URGENT") else cls_ui

    # amplitude data for sparkline and growth chart
    amp_data = []
    for h in hist:
        g = fin(h.get("gmt_at_round"))
        a = fin(h.get("amplitude_pct"))
        if g is not None and a is not None:
            amp_data.append({
                "gmt": g,
                "fsh": a,
                "probe": short_probe(h.get("probe",""))
            })

    # sla status
    sla24h, sla3d = sla_status(
        tq.get("is_imr_level",0),
        tq.get("sla_breach",0),
        tq.get("days_since_insp")
    )

    # cluster
    cid = tq.get("cluster_id")
    cluster = None
    if cid and cid > 0:
        cluster = {"id": f"CL-{cid:02d}", "span": 4.0, "defectCount": 2}

    # evidence
    amps = [h.get("amplitude_pct") for h in hist]
    gmts = [h.get("gmt_at_round") for h in hist]
    r2 = r2_of(amps, gmts)

    # probe (use last reading's probe)
    probe = short_probe(hist[-1].get("probe","")) if hist else "—"

    # action narrative from triage level
    reasons = (tq.get("reasons") or "").replace("|"," · ")
    action_map = {
        "IMMEDIATE": "Immediate action required — remove defect from track per RDSO Para 6.2.3. Place joggled fish plate.",
        "URGENT": "Plan removal before next inspection cycle. GMT budget limited. Dispatch joggled fish plate.",
        "MONITOR": "Increased monitoring. Re-inspect at next scheduled run.",
        "ROUTINE": "Routine monitoring. Re-inspect at next scheduled run.",
    }
    action = action_map.get(lvl, "Monitor and re-inspect.")
    if tq.get("para632_escalate"):
        action = f"Para 6.3.2 cluster escalation. {action}"

    # last inspection date
    last_date = hist[-1].get("inspection_date","—") if hist else "—"

    # gmt to threshold
    gmt_thresh = fin(pred.get("gmt_to_80") if pred else None)
    gmt_consumed = fin(tq.get("gmt_carried"))
    yrs_to_80 = fin(pred.get("yrs_to_80") if pred else None)

    # derive id
    km = tq.get("km", 0)
    meter = tq.get("meter", 0)
    rail = tq.get("rail","L")
    uid = f"{km:05d}-{int(meter):02d}-{rail}"

    return {
        "id": uid,
        "defect_key": tq.get("defect_key",""),
        "km": f"{km:05d}+{meter:.0f}",
        "uic": cls_ui,
        "type": "weld" if is_weld else "rail",
        "currentClass": cls_ui,
        "predictedClass": predicted_cls,
        "slaStatus": "overdue" if sla24h == "overdue" else ("pending" if sla24h == "pending" else None),
        "sla24hFP": sla24h,
        "sla3dReplace": sla3d,
        "gmtToThreshold": gmt_thresh if gmt_thresh is not None else 0,
        "gmtConsumed": gmt_consumed,
        "lastInspDate": last_date,
        "route": tq.get("section","—"),
        "section": tq.get("asset_type","—"),
        "grade": "—",
        "trackGeom": "—",
        "activeSR": None,
        "probe": probe,
        "evidence": {"runs": len(hist), "r2": r2},
        "cluster": cluster,
        "action": action,
        "wtAgency": None,
        "amplitudeData": amp_data,
        "calibRef": f"{probe} probe",
        # model-derived extras
        "triageLevel": lvl,
        "triageRank": tq.get("triage_rank"),
        "yrs_to_80": yrs_to_80,
        "gmt_to_60": fin(pred.get("gmt_to_60") if pred else None),
        "yrs_to_60": fin(pred.get("yrs_to_60") if pred else None),
        "gmt_to_30": fin(pred.get("gmt_to_30") if pred else None),
        "yrs_to_30": fin(pred.get("yrs_to_30") if pred else None),
        "riskScore": fin_risk(pred.get("risk_score") if pred else None),
        "family": pred.get("family","—") if pred else "—",
        "famC": fin(pred.get("fam_C") if pred else None),
        "famM": fin(pred.get("fam_m") if pred else None),
        "ownSlope": fin(pred.get("own_slope_per_gmt") if pred else None),
        "reasons": reasons,
        "railway": tq.get("railway","—"),
        "paraCluster": bool(tq.get("para632_escalate")),
        "slaBreach": bool(tq.get("sla_breach")),
        "n_readings": tq.get("n_readings",0),
        "severity_band": tq.get("severity_band","—"),
        "spark": [h.get("amplitude_pct") for h in hist
                  if h.get("amplitude_pct") is not None],
    }

# ───────────────────────────── static / home ──────────────────────────────

@app.get("/")
def home():
    p = os.path.join(PUB, "index.html")
    if os.path.exists(p):
        return FileResponse(p, media_type="text/html")
    p2 = os.path.join(PUB, "Priority Queue.html")
    return FileResponse(p2, media_type="text/html")

# Mount /app/ — serves all screens locally at /app/ScreenName.html
if os.path.isdir(PUB):
    app.mount("/app", StaticFiles(directory=PUB), name="prototype")

# Serve assets at /assets/ so local dev matches Vercel paths
if os.path.isdir(os.path.join(PUB, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(PUB, "assets")),
              name="assets")

# ───────────────────────────── stats / zones ──────────────────────────────

@app.get("/api/stats")
def stats():
    one = lambda s: rows(s)[0]["n"]
    return {
        "defects": one("SELECT COUNT(*) n FROM triage_queue"),
        "inspections": one("SELECT COUNT(*) n FROM inspections"),
        "by_level": {r["triage_level"]: r["n"] for r in
                     rows("SELECT triage_level, COUNT(*) n FROM triage_queue GROUP BY triage_level")},
        "near_imr": one("SELECT COUNT(*) n FROM triage_queue WHERE latest_amp>=80"),
        "clusters": one("SELECT COUNT(*) n FROM clusters"),
        "sla_breaches": one("SELECT COUNT(*) n FROM triage_queue WHERE sla_breach=1"),
        "unique_railways": one("SELECT COUNT(DISTINCT railway) n FROM triage_queue"),
        "total_defects_with_3_readings": one(
            "SELECT COUNT(*) n FROM triage_queue WHERE n_readings>=3"),
    }

@app.get("/api/zones")
def zones():
    return rows("""SELECT railway,
                   COUNT(*) defects,
                   SUM(CASE WHEN latest_amp>=80 THEN 1 ELSE 0 END) near_imr,
                   SUM(CASE WHEN triage_level='IMMEDIATE' THEN 1 ELSE 0 END) immediate,
                   SUM(CASE WHEN sla_breach=1 THEN 1 ELSE 0 END) sla_breach,
                   ROUND(AVG(latest_amp),1) avg_amp,
                   SUM(CASE WHEN triage_level='URGENT' THEN 1 ELSE 0 END) urgent,
                   SUM(CASE WHEN triage_level='MONITOR' THEN 1 ELSE 0 END) monitor,
                   SUM(CASE WHEN triage_level='ROUTINE' THEN 1 ELSE 0 END) routine,
                   COUNT(DISTINCT section) sections
                   FROM triage_queue GROUP BY railway ORDER BY near_imr DESC""")

# ───────────────────────────── priority queue ──────────────────────────────

@app.get("/api/queue")
def queue(level: str | None = Query(None),
          railway: str | None = None,
          asset: str | None = None,
          limit: int = 200,
          offset: int = 0):
    sql = "SELECT * FROM triage_queue WHERE 1=1"; p = []
    if level:
        sql += " AND triage_level=?"; p.append(level.upper())
    if railway:
        sql += " AND railway LIKE ?"; p.append(f"%{railway}%")
    if asset:
        sql += " AND asset_type LIKE ?"; p.append(f"%{asset}%")
    sql += " ORDER BY triage_rank LIMIT ? OFFSET ?"; p += [limit, offset]
    tq_rows = rows(sql, tuple(p))

    keys = [r["defect_key"] for r in tq_rows]
    # fetch inspection history in bulk
    hist_map: dict[str, list] = {}
    pred_map: dict[str, dict] = {}
    if keys:
        ph = ",".join("?"*len(keys))
        for r in rows(f"""SELECT defect_key, amplitude_pct, gmt_at_round, probe,
                          inspection_date, classification FROM inspections
                          WHERE defect_key IN ({ph}) AND amplitude_pct IS NOT NULL
                          ORDER BY insp_dt""", tuple(keys)):
            hist_map.setdefault(r["defect_key"], []).append(r)
        for r in rows(f"""SELECT defect_key, gmt_to_80, yrs_to_80, gmt_to_60, yrs_to_60,
                          gmt_to_30, yrs_to_30, risk_score, family, fam_C, fam_m,
                          own_slope_per_gmt FROM predictions
                          WHERE defect_key IN ({ph})""", tuple(keys)):
            pred_map[r["defect_key"]] = r

    result = []
    for tq in tq_rows:
        key = tq["defect_key"]
        result.append(build_defect_ui(tq, hist_map.get(key, []), pred_map.get(key)))
    return result

# ───────────────────────────── defect detail + growth ──────────────────────────────

@app.get("/api/defect")
def defect(key: str):
    defect_key = key
    pred = rows("SELECT * FROM predictions WHERE defect_key=?", (defect_key,))
    if not pred:
        raise HTTPException(404, "defect not found")
    triage = rows("SELECT * FROM triage_queue WHERE defect_key=?", (defect_key,))
    hist = rows("""SELECT round_label, inspection_date, classification, probe,
                   amplitude_pct, gmt_at_round, insp_dt FROM inspections
                   WHERE defect_key=? ORDER BY insp_dt""", (defect_key,))
    return {"prediction": pred[0], "triage": triage[0] if triage else None,
            "inspection_history": hist}

@app.get("/api/defect_growth")
def growth(key: str):
    defect_key = key
    p = rows("SELECT * FROM predictions WHERE defect_key=?", (defect_key,))
    if not p: raise HTTPException(404, "defect not found")
    p = p[0]
    hist = rows("""SELECT inspection_date, amplitude_pct amp, gmt_at_round gmt,
                   probe, classification, insp_dt FROM inspections
                   WHERE defect_key=? AND amplitude_pct IS NOT NULL ORDER BY insp_dt""",
                (defect_key,))
    pts = [{"date": h["inspection_date"], "amp": h["amp"],
            "t": h["insp_dt"], "gmt": h["gmt"],
            "probe": short_probe(h["probe"]),
            "classification": h["classification"]} for h in hist]
    proj = []
    imr_date = None
    if pts:
        last = pts[-1]
        last_dt = pd.to_datetime(last["t"])
        y80 = fin(p.get("yrs_to_80"))
        if y80 is not None and 0 < y80 < 50:
            imr_dt = last_dt + pd.Timedelta(days=365.25 * y80)
            imr_date = str(imr_dt.date())
            proj = [{"date": str(last_dt.date()), "amp": last["amp"]},
                    {"date": imr_date, "amp": 80}]
        elif last["amp"] >= 80:
            imr_date = str(last_dt.date())
        else:
            proj = [{"date": str(last_dt.date()), "amp": last["amp"]},
                    {"date": str((last_dt + pd.Timedelta(days=365*5)).date()), "amp": last["amp"]}]
    # synthetic audit trail from inspection history
    audit = []
    for i, h in enumerate(hist):
        cls_label = h.get("classification","OBS")
        probe_label = short_probe(h.get("probe",""))
        amp = h.get("amp")
        msg = f"Run {i+1}: {cls_label} — {amp:.0f}%FSH via {probe_label}"
        if i == 0:
            msg = f"First detection. {msg}"
        audit.append({"ts": h.get("inspection_date","—"),
                       "actor": "System",
                       "msg": msg})
    return {
        "defect_key": defect_key,
        "points": pts,
        "projection": proj,
        "thresholds": [30, 60, 80],
        "predicted_imr_date": imr_date,
        "yrs_to_80": fin(p.get("yrs_to_80")),
        "yrs_to_60": fin(p.get("yrs_to_60")),
        "yrs_to_30": fin(p.get("yrs_to_30")),
        "gmt_to_80": fin(p.get("gmt_to_80")),
        "latest_amp": fin(p.get("latest_amp")),
        "section": p.get("section"),
        "railway": p.get("railway"),
        "km": p.get("km"),
        "meter": p.get("meter"),
        "rail": p.get("rail"),
        "asset_type": p.get("asset_type"),
        "family": p.get("family","—"),
        "fam_C": fin(p.get("fam_C")),
        "fam_m": fin(p.get("fam_m")),
        "own_slope": fin(p.get("own_slope_per_gmt")),
        "risk_score": fin_risk(p.get("risk_score")),
        "n_readings": p.get("n_readings"),
        "audit": audit,
    }

# ───────────────────────────── track map ──────────────────────────────

@app.get("/api/trackmap")
def trackmap(railway: str, section: str):
    defs = rows("""SELECT km, meter, chainage_m, rail, latest_amp, triage_level,
                   cluster_id, defect_key, asset_type, n_readings, sla_breach
                   FROM triage_queue
                   WHERE railway LIKE ? AND section=? AND chainage_m IS NOT NULL
                   ORDER BY chainage_m""", (f"%{railway}%", section))
    cl = rows("""SELECT cluster_id, chainage_start_m, chainage_end_m, n_defects, max_amp
                 FROM clusters WHERE railway LIKE ? AND section=?
                 ORDER BY chainage_start_m""", (f"%{railway}%", section))
    return {"defects": defs, "clusters": cl}

@app.get("/api/sections")
def sections():
    return rows("""SELECT railway, section, COUNT(*) n,
                   SUM(CASE WHEN latest_amp>=80 THEN 1 ELSE 0 END) near_imr,
                   SUM(CASE WHEN triage_level='IMMEDIATE' THEN 1 ELSE 0 END) immediate
                   FROM triage_queue GROUP BY railway, section ORDER BY near_imr DESC""")

@app.get("/api/clusters")
def clusters_list(limit: int = 100):
    cl = rows("SELECT * FROM clusters ORDER BY n_defects DESC LIMIT ?", (limit,))
    return cl

# ───────────────────────────── cluster detail ──────────────────────────────

@app.get("/api/cluster/{cluster_id}")
def cluster_detail(cluster_id: int):
    cl = rows("SELECT * FROM clusters WHERE cluster_id=?", (cluster_id,))
    if not cl:
        raise HTTPException(404, "cluster not found")
    cl = cl[0]
    members = rows("""SELECT tq.defect_key, tq.km, tq.meter, tq.rail, tq.asset_type,
                      tq.latest_amp, tq.triage_level, tq.chainage_m, tq.sla_breach,
                      tq.para632_escalate, tq.n_readings, tq.is_imr_level,
                      p.family, p.own_slope_per_gmt, p.yrs_to_80
                      FROM triage_queue tq
                      LEFT JOIN predictions p ON tq.defect_key=p.defect_key
                      WHERE tq.cluster_id=?
                      ORDER BY tq.triage_rank""", (cluster_id,))
    # add inspection data for each member
    member_details = []
    for m in members:
        key = m["defect_key"]
        hist = rows("""SELECT amplitude_pct, gmt_at_round, probe, inspection_date
                       FROM inspections WHERE defect_key=? AND amplitude_pct IS NOT NULL
                       ORDER BY insp_dt""", (key,))
        is_weld = "weld" in (m.get("asset_type","") or "").lower()
        cls_ui = classify_ui(
            hist[-1].get("classification","OBS") if hist else "",
            m.get("asset_type","")
        )
        offset_m = None
        if m.get("chainage_m") and cl.get("chainage_start_m"):
            offset_m = round(m["chainage_m"] - cl["chainage_start_m"], 1)
        member_details.append({
            **m,
            "offset_m": offset_m,
            "currentClass": cls_ui,
            "probe": short_probe(hist[-1]["probe"]) if hist else "—",
            "yrs_to_80": fin(m.get("yrs_to_80")),
            "own_slope": fin(m.get("own_slope_per_gmt")),
            "amp_history": [{"gmt": fin(h["gmt_at_round"]), "fsh": h["amplitude_pct"],
                             "probe": short_probe(h["probe"])} for h in hist],
        })
    return {
        "cluster": cl,
        "members": member_details,
    }

# ───────────────────────────── model endpoints ──────────────────────────────

@app.get("/api/model")
def model_info():
    p = os.path.join(ROOT, "model_meta.json")
    if not os.path.exists(p): raise HTTPException(404, "run train_model.py first")
    meta = json.load(open(p))
    # attach family power-law data
    meta["families"] = rows("SELECT * FROM family_powerlaw ORDER BY family")
    return meta

@app.get("/api/model/families")
def model_families():
    fams = rows("SELECT * FROM family_powerlaw ORDER BY family")
    # enrich with per-family defect counts and avg amplitude from predictions
    enriched = []
    for f in fams:
        if f["family"] == "__GLOBAL__":
            continue
        stats = rows("""SELECT COUNT(*) n, ROUND(AVG(latest_amp),1) avg_amp,
                        SUM(CASE WHEN latest_amp>=80 THEN 1 ELSE 0 END) near_imr
                        FROM predictions WHERE family=?""", (f["family"],))
        enriched.append({
            **f,
            "n_defects": stats[0]["n"] if stats else 0,
            "avg_amp": stats[0]["avg_amp"] if stats else None,
            "near_imr": stats[0]["near_imr"] if stats else 0,
            "growth_per_gmt_at_50pct": round(
                f["C"] * (50 ** f["m"]), 3) if f["C"] and f["m"] else None,
        })
    return enriched

@app.get("/api/model/rul-distribution")
def rul_distribution():
    """Fleet-level RUL histogram for the dashboard."""
    buckets = {
        "already_imr": 0,
        "lt_1yr": 0,
        "1_to_2yr": 0,
        "2_to_5yr": 0,
        "gt_5yr": 0,
        "stable": 0,
    }
    data = rows("SELECT latest_amp, yrs_to_80 FROM predictions")
    for r in data:
        amp = r["latest_amp"] or 0
        y = fin(r["yrs_to_80"])
        if amp >= 80:
            buckets["already_imr"] += 1
        elif y is None or y >= 50:
            buckets["stable"] += 1
        elif y < 1:
            buckets["lt_1yr"] += 1
        elif y < 2:
            buckets["1_to_2yr"] += 1
        elif y < 5:
            buckets["2_to_5yr"] += 1
        else:
            buckets["gt_5yr"] += 1
    # amplitude distribution
    amp_hist = rows("""
        SELECT
          SUM(CASE WHEN latest_amp < 30 THEN 1 ELSE 0 END) lt_30,
          SUM(CASE WHEN latest_amp >= 30 AND latest_amp < 60 THEN 1 ELSE 0 END) band_30_60,
          SUM(CASE WHEN latest_amp >= 60 AND latest_amp < 80 THEN 1 ELSE 0 END) band_60_80,
          SUM(CASE WHEN latest_amp >= 80 THEN 1 ELSE 0 END) gte_80
        FROM predictions
    """)
    # growth rate distribution (slope per GMT)
    slope_data = rows("""SELECT own_slope_per_gmt FROM predictions
                         WHERE own_slope_per_gmt IS NOT NULL
                         ORDER BY own_slope_per_gmt""")
    slopes = [fin(r["own_slope_per_gmt"]) for r in slope_data if fin(r["own_slope_per_gmt"]) is not None]
    # per-family risk summary
    family_risk = rows("""SELECT family,
                          COUNT(*) n,
                          ROUND(AVG(latest_amp),1) avg_amp,
                          SUM(CASE WHEN latest_amp>=80 THEN 1 ELSE 0 END) near_imr,
                          ROUND(AVG(CASE WHEN yrs_to_80<50 AND yrs_to_80 IS NOT NULL
                                        THEN yrs_to_80 END),1) avg_yrs_to_80
                          FROM predictions
                          WHERE family != '__GLOBAL__'
                          GROUP BY family ORDER BY avg_amp DESC""")
    return {
        "rul_buckets": buckets,
        "amplitude_bands": amp_hist[0] if amp_hist else {},
        "family_risk": family_risk,
        "slope_p50": round(slopes[len(slopes)//2], 4) if slopes else None,
        "slope_p90": round(slopes[int(len(slopes)*0.9)], 4) if slopes else None,
        "slope_p99": round(slopes[int(len(slopes)*0.99)], 4) if slopes else None,
    }

@app.get("/api/predict")
def predict(key: str):
    defect_key = key
    hist = rows("""SELECT probe, amplitude_pct, gmt_at_round FROM inspections
                   WHERE defect_key=? AND amplitude_pct IS NOT NULL ORDER BY insp_dt""",
                (defect_key,))
    meta = rows("SELECT asset_type, railway, gmt_rate_per_yr FROM predictions WHERE defect_key=?",
                (defect_key,))
    if not hist or not meta:
        raise HTTPException(404, "defect not found or no readings")
    m = meta[0]; amp_prev = hist[-1]["amplitude_pct"]; slope = 0.0
    if len(hist) >= 2 and hist[-1]["gmt_at_round"] and hist[-2]["gmt_at_round"] is not None:
        dg = hist[-1]["gmt_at_round"] - hist[-2]["gmt_at_round"]
        if dg: slope = (hist[-1]["amplitude_pct"] - hist[-2]["amplitude_pct"]) / dg
    pb = re.search(r"/\s*([0-9A-Za-z]+)\s*$", str(hist[-1]["probe"]))
    probe = (pb.group(1).upper() if pb else "other")
    probe = probe if probe in {"0", "70", "70GF", "70NGF"} else "other"
    X = pd.DataFrame([{"amp_prev": amp_prev, "slope_prev": slope, "dN": 30.0,
                       "dt_days": 365, "gmt_rate": m["gmt_rate_per_yr"],
                       "asset": m["asset_type"], "probe": probe, "railway": m["railway"]}])
    pred = float(min(100, max(0, get_model().predict(X)[0])))
    return {"defect_key": defect_key, "latest_amp": amp_prev,
            "predicted_next_amp": round(pred, 1), "persistence_baseline": amp_prev,
            "assumes": "~1 year / 30 GMT ahead"}

# ───────────────────────────── annexure III pre-fill ──────────────────────────────

@app.get("/api/annexure3")
def annexure3(key: str):
    tq = rows("SELECT * FROM triage_queue WHERE defect_key=?", (key,))
    pred = rows("SELECT * FROM predictions WHERE defect_key=?", (key,))
    hist = rows("""SELECT inspection_date, amplitude_pct, probe, classification
                   FROM inspections WHERE defect_key=? ORDER BY insp_dt""", (key,))
    if not pred:
        raise HTTPException(404, "defect not found")
    tq = tq[0] if tq else {}
    p = pred[0]
    last = hist[-1] if hist else {}
    return {
        "defect_key": key,
        "railway": p.get("railway","—"),
        "division": "—",
        "section": p.get("section","—"),
        "km": p.get("km"),
        "meter": p.get("meter"),
        "rail": p.get("rail","—"),
        "asset_type": p.get("asset_type","rail"),
        "latest_amp": fin(p.get("latest_amp")),
        "triage_level": tq.get("triage_level","ROUTINE"),
        "classification": last.get("classification","OBS"),
        "probe": short_probe(last.get("probe","—")),
        "last_insp_date": last.get("inspection_date","—"),
        "sla_breach": bool(tq.get("sla_breach")),
        "cluster_id": tq.get("cluster_id"),
        "para632": bool(tq.get("para632_escalate")),
        "yrs_to_80": fin(p.get("yrs_to_80")),
        "gmt_carried": fin(tq.get("gmt_carried")),
        "n_readings": p.get("n_readings",0),
        "family": p.get("family","—"),
        "risk_score": fin_risk(p.get("risk_score")),
    }

# ───────────────────────────── import (parse only) ──────────────────────────────

@app.post("/api/import")
async def import_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "No file provided")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xls", ".xlsx"):
        raise HTTPException(400, "Only .xls/.xlsx files are supported")
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content); tmp_path = tmp.name
    try:
        import sys
        sys.path.insert(0, os.path.join(ROOT, "ds"))
        try:
            from parse_usfd import parse_file
        except ImportError:
            return {"filename": file.filename, "rows_parsed": 0, "sample": [],
                    "errors": ["XLS parser not available in this environment"],
                    "status": "unavailable"}
        df = parse_file(tmp_path)
        n = len(df)
        sample = df.head(20).to_dict(orient="records") if n else []
        railways = df["railway"].dropna().unique().tolist() if "railway" in df.columns else []
        return {
            "filename": file.filename,
            "rows_parsed": n,
            "sample": sample[:20],
            "railways": railways,
            "errors": [],
            "status": "ok",
        }
    except Exception as e:
        return {"filename": file.filename, "rows_parsed": 0,
                "sample": [], "errors": [str(e)], "status": "error"}
    finally:
        os.unlink(tmp_path)
