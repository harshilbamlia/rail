#!/usr/bin/env python3
"""
Aonami Echo — Step 3: growth modelling + RUL + honest holdout validation.

Approach (fitted to what the data can actually support):
  * Per defect we have <=3 (GMT, %FSH) points -> can't fit a curve per defect.
  * So we fit a POOLED, physics-informed power-law per defect FAMILY:
        da/dN = C * a^m            (Paris-style; a = %FSH proxy, N = GMT)
    estimated by regressing log(growth-rate) on log(mid-amplitude) over all
    positive-growth segments in the family.
  * RUL = integrate that law from each defect's latest amplitude up to the
    30/60/80 %FSH thresholds  ->  GMT-to-threshold, then years via gmt_rate.
  * Validation: hold out each 3-reading defect's LAST point, predict it from the
    earlier points, and compare physics vs persistence vs linear vs ML-residual.

Outputs:
  usfd_predictions.csv   -- per-defect RUL + risk rank
  family_powerlaw.csv    -- fitted C, m per family
  MODEL_STEP3.md         -- written report (incl. holdout accuracy)
  plots/1x_*.png
"""
import os, re, warnings
import numpy as np
import pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score
warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.abspath(__file__))
PLOTS = os.path.join(ROOT, "plots"); os.makedirs(PLOTS, exist_ok=True)
THRESHOLDS = [30, 60, 80]
EPS = 1e-9

# -------------------------------------------------------------- load + series
df = pd.read_csv(ROOT + "/usfd_clean.csv", parse_dates=["insp_dt"])

def probe_bucket(p):
    s = str(p)
    m = re.search(r"/\s*([0-9A-Za-z]+)\s*$", s)
    if not m: return "other"
    t = m.group(1).upper()
    return t if t in {"0", "70", "70GF", "70NGF"} else "other"

df["probe_bucket"] = df["probe"].map(probe_bucket)

# build a chronological per-defect record
recs = {}
for key, g in df.groupby("defect_key"):
    g = g.dropna(subset=["amp", "insp_dt", "gmt_at_round"]).sort_values("insp_dt")
    if len(g) == 0:
        continue
    recs[key] = {
        "defect_key": key,
        "railway": g["railway"].iloc[0], "section": g["section"].iloc[0],
        "asset_type": g["asset_type"].iloc[0], "km": g["km"].iloc[0],
        "meter": g["meter"].iloc[0], "rail": g["rail"].iloc[0],
        "probe_bucket": g["probe_bucket"].iloc[-1],
        "gmt_rate_per_yr": g["gmt_rate_per_yr"].iloc[0],
        "gmt_carried": g["gmt_carried"].iloc[0],
        "amps": g["amp"].to_numpy(), "gmts": g["gmt_at_round"].to_numpy(),
        "dts": g["insp_dt"].tolist(),
    }

def family_of(r):  # family proxy: asset + probe-angle (orientation/location proxy)
    return f"{r['asset_type']}:{r['probe_bucket']}"

# -------------------------------------------------- pooled power-law per family
# collect positive-growth segments: (mid_amp, rate=da/dN) with rate>0
seg_rows = []
for r in recs.values():
    a, n = r["amps"], r["gmts"]
    fam = family_of(r)
    for i in range(len(a) - 1):
        dN = n[i + 1] - n[i]
        if dN <= 0: continue
        rate = (a[i + 1] - a[i]) / dN
        mid = (a[i + 1] + a[i]) / 2
        seg_rows.append((fam, mid, rate))
segs = pd.DataFrame(seg_rows, columns=["family", "mid_amp", "rate"])

def fit_powerlaw(s):
    """log(rate)=log C + m log(a) on positive segments -> (C, m, n_pos)."""
    pos = s[(s.rate > 0) & (s.mid_amp > 0)]
    if len(pos) < 8:
        return None
    x = np.log(pos.mid_amp.to_numpy()); y = np.log(pos.rate.to_numpy())
    m, b = np.polyfit(x, y, 1)
    m = float(np.clip(m, 0.3, 4.0))
    return float(np.exp(b)), m, len(pos)

global_fit = fit_powerlaw(segs) or (np.nan, np.nan, 0)
fam_fit = {}
for fam, s in segs.groupby("family"):
    fam_fit[fam] = fit_powerlaw(s) or global_fit

fp = pd.DataFrame([(f, c, m, n) for f, (c, m, n) in fam_fit.items()],
                  columns=["family", "C", "m", "n_pos_segments"])
fp.loc[len(fp)] = ["__GLOBAL__", *global_fit]
fp.to_csv(ROOT + "/family_powerlaw.csv", index=False)

# ------------------------------------------------------------ RUL projection
# NOTE: a pure power-law RUL (fit on positive segments only) over-predicts growth
# for the mostly-flat population (selection bias). Instead project each defect's
# OWN observed amplitude slope (vs GMT), shrunk toward its family-median slope.
def own_slope(r):
    a, n = r["amps"], r["gmts"]
    if len(a) < 2 or (n[-1] - n[0]) <= 0:
        return np.nan
    return float(np.polyfit(n, a, 1)[0])      # %FSH per GMT

for r in recs.values():
    r["own_slope"] = own_slope(r)

# family-median own slope (typically ~0: most defects stable)
fam_slope = {}
tmp = {}
for r in recs.values():
    tmp.setdefault(family_of(r), []).append(r["own_slope"])
for f, vals in tmp.items():
    v = [x for x in vals if not np.isnan(x)]
    fam_slope[f] = float(np.median(v)) if v else 0.0
global_slope = float(np.nanmedian([r["own_slope"] for r in recs.values()
                                   if not np.isnan(r["own_slope"])]))

MIN_RATE = 0.05  # %FSH per GMT below this => treat as stable (RUL infinite)

pred_rows = []
for r in recs.values():
    fam = family_of(r); C, m, _ = fam_fit.get(fam, global_fit)
    a0 = r["amps"][-1]; rate_yr = r["gmt_rate_per_yr"]
    n = len(r["amps"]); os_ = r["own_slope"]
    fmed = fam_slope.get(fam, global_slope)
    # shrink own slope toward family median; weight grows with #readings
    if np.isnan(os_):
        rate_gmt = fmed
    else:
        w = (n - 1) / ((n - 1) + 1.0)
        rate_gmt = w * os_ + (1 - w) * fmed
    row = {"defect_key": r["defect_key"], "railway": r["railway"], "section": r["section"],
           "asset_type": r["asset_type"], "km": r["km"], "meter": r["meter"], "rail": r["rail"],
           "probe_bucket": r["probe_bucket"], "family": fam, "n_readings": n,
           "latest_amp": a0, "fam_C": C, "fam_m": m,
           "own_slope_per_gmt": os_, "rate_gmt_eff": rate_gmt, "gmt_rate_per_yr": rate_yr}
    for T in THRESHOLDS:
        if a0 >= T:
            g = 0.0
        elif rate_gmt is None or rate_gmt <= MIN_RATE:
            g = np.inf                                   # stable / not growing
        else:
            g = (T - a0) / rate_gmt                      # linear projection in GMT
        row[f"gmt_to_{T}"] = g
        row[f"yrs_to_{T}"] = (g / rate_yr) if (rate_yr and rate_yr > 0 and np.isfinite(g)) \
                              else (0.0 if g == 0 else np.inf)
    pred_rows.append(row)
pred = pd.DataFrame(pred_rows)

# risk score: nearer to IMR (80) sooner = higher risk; already-breached = top.
def risk(row):
    y80 = row["yrs_to_80"]
    if row["latest_amp"] >= 80: return 1e6 + row["latest_amp"]      # already near-IMR
    if not np.isfinite(y80):    return -1.0                          # effectively stable
    return 1.0 / (y80 + 0.25)                                        # sooner -> bigger
pred["risk_score"] = pred.apply(risk, axis=1)
pred = pred.sort_values("risk_score", ascending=False).reset_index(drop=True)
pred["risk_rank"] = pred.index + 1
pred.to_csv(ROOT + "/usfd_predictions.csv", index=False)

# ------------------------------------------------- holdout validation (3-pt defects)
val = [r for r in recs.values() if len(r["amps"]) >= 3]
rows = []
for r in val:
    a, n = r["amps"], r["gmts"]
    a_true = a[-1]
    # predictors from the first len-1 points
    a_prev = a[-2]; dN = n[-1] - n[-2]
    slope = (a[-2] - a[-3]) / (n[-2] - n[-3]) if (n[-2] - n[-3]) > 0 else 0.0
    fam = family_of(r); C, m, _ = fam_fit.get(fam, global_fit)
    # physics: integrate from a_prev over dN
    if C and C > 0 and not np.isnan(m):
        if abs(m - 1) < 1e-3:
            a_phys = a_prev * np.exp(C * dN)
        else:
            val_in = a_prev ** (1 - m) + C * (1 - m) * dN
            a_phys = val_in ** (1 / (1 - m)) if val_in > 0 else a_prev
    else:
        a_phys = a_prev
    rows.append({
        "defect_key": r["defect_key"], "a_true": a_true,
        "a_persist": a_prev,
        "a_linear": float(np.clip(a_prev + slope * dN, 0, 100)),
        "a_physics": float(np.clip(a_phys, 0, 100)),
        # ML features
        "amp_prev": a_prev, "slope_prev": slope, "dN": dN,
        "gmt_rate": r["gmt_rate_per_yr"], "asset": r["asset_type"],
        "probe": r["probe_bucket"], "railway": r["railway"],
    })
V = pd.DataFrame(rows)

# ML model predicts amplitude DIRECTLY (persistence supplied as a feature, so it
# can't do much worse than the baseline). Deterministic split via stable hash.
import hashlib
def stable_bucket(s):
    return int(hashlib.md5(str(s).encode()).hexdigest(), 16) % 10
V["h"] = V["defect_key"].map(stable_bucket)
tr, te = V[V.h < 7].copy(), V[V.h >= 7].copy()
feat_num = ["amp_prev", "slope_prev", "dN", "gmt_rate", "a_physics", "a_linear"]
feat_cat = ["asset", "probe", "railway"]
Xtr = pd.get_dummies(tr[feat_num + feat_cat], columns=feat_cat)
Xte = pd.get_dummies(te[feat_num + feat_cat], columns=feat_cat).reindex(columns=Xtr.columns, fill_value=0)
gbm = HistGradientBoostingRegressor(max_depth=3, max_iter=300, learning_rate=0.05,
                                    l2_regularization=1.0, random_state=0)
gbm.fit(Xtr, tr["a_true"])
te["a_ml"] = np.clip(gbm.predict(Xte), 0, 100)

def scores(d, col):
    return mean_absolute_error(d["a_true"], d[col]), r2_score(d["a_true"], d[col])

acc = {name: scores(te, col) for name, col in
       [("persistence", "a_persist"), ("linear", "a_linear"),
        ("physics", "a_physics"), ("ML (direct)", "a_ml")]}
best_model = min(acc, key=lambda k: acc[k][0])   # lowest MAE wins

# ------------------------------------------------------------------- plots
def save(fig, n): fig.tight_layout(); fig.savefig(os.path.join(PLOTS, n), dpi=90); plt.close(fig)

# power-law fit (global) on segments
pos = segs[(segs.rate > 0) & (segs.mid_amp > 0)]
fig, ax = plt.subplots(figsize=(7, 4.5))
ax.scatter(pos.mid_amp, pos.rate, s=6, alpha=.25)
xs = np.linspace(pos.mid_amp.min(), pos.mid_amp.max(), 50)
Cg, mg, _ = global_fit
ax.plot(xs, Cg * xs ** mg, "r-", lw=2, label=f"global  da/dN={Cg:.2e}·a^{mg:.2f}")
ax.set(title="Pooled power-law growth fit (positive segments)",
       xlabel="amplitude %FSH", ylabel="growth rate %FSH per GMT"); ax.legend()
save(fig, "11_powerlaw_fit.png")

# holdout predicted vs actual (ML direct)
fig, ax = plt.subplots(figsize=(5.5, 5.5))
ax.scatter(te["a_true"], te["a_ml"], s=10, alpha=.4)
ax.plot([0, 100], [0, 100], "k--", lw=1)
ax.set(title=f"Holdout: ML direct  (MAE={acc['ML (direct)'][0]:.1f}, R²={acc['ML (direct)'][1]:.2f}); "
             f"best={best_model}",
       xlabel="actual round-3 %FSH", ylabel="predicted %FSH")
save(fig, "12_holdout_pred_vs_actual.png")

# years-to-80 distribution (finite, not already breached)
y80 = pred.loc[(pred.latest_amp < 80) & np.isfinite(pred.yrs_to_80), "yrs_to_80"]
fig, ax = plt.subplots(figsize=(7, 4))
ax.hist(y80.clip(0, 30), bins=30, color="#c5468a", edgecolor="white")
ax.set(title=f"Predicted years to 80%FSH (IMR), n={len(y80)} (clipped 30y)",
       xlabel="years", ylabel="defects")
save(fig, "13_years_to_imr.png")

# ------------------------------------------------------------------- report
def fmt(t): return f"MAE **{t[0]:.2f} %FSH**, R² **{t[1]:.2f}**"
n_breach = int((pred.latest_amp >= 80).sum())
soon = pred[(pred.latest_amp < 80) & np.isfinite(pred.yrs_to_80)]
L = []; A = L.append
A("# USFD Step 3 — growth model, RUL & validation\n")
A("## Method")
A("Two parts, each fitted to what ≤3 noisy points/defect can support:")
A("1. **Descriptive growth law** — pooled power-law `da/dN = C·a^m` per defect "
  "family (asset_type × probe-angle) on positive-growth segments → characterises "
  "*how fast defects grow when they grow*.")
A("2. **RUL projection** — project each defect's OWN amplitude slope (vs GMT), "
  "shrunk toward its family-median slope, to the 30/60/80 %FSH thresholds. "
  "Stable defects (rate ≤ 0.05 %FSH/GMT) ⇒ no finite RUL. "
  "(Earlier pure power-law RUL was discarded: fit on positive segments only, it "
  "over-predicted growth for the mostly-flat population.)")
A("3. **Next-reading model** — HistGBM predicts amplitude directly (with "
  "persistence/linear/physics as features) and is benchmarked against baselines.\n")
A("## Family power-law fits (descriptive)")
A("| family | C | m | +segments |")
A("|---|---|---|---|")
for _, x in fp.sort_values("n_pos_segments", ascending=False).iterrows():
    A(f"| {x.family} | {x.C:.2e} | {x.m:.2f} | {int(x.n_pos_segments)} |")
A("")
A("## Holdout validation (predict each 3-reading defect's last point)")
A(f"- test defects: **{len(te)}** (of {len(V)} with 3 readings; deterministic 70/30 split)")
for name in ["persistence", "linear", "physics", "ML (direct)"]:
    A(f"- {name}: {fmt(acc[name])}")
A(f"- **best by MAE: `{best_model}`**")
A("> Persistence = 'assume unchanged'. A model is only useful if it beats it on MAE.\n")
A("## Remaining-life predictions")
A(f"- defects scored: **{len(pred)}**")
A(f"- already ≥80 %FSH (near-IMR, act now): **{n_breach}**")
A(f"- predicted to reach 80 %FSH within 2 yrs: **{int((soon.yrs_to_80<=2).sum())}**, "
  f"within 5 yrs: **{int((soon.yrs_to_80<=5).sum())}**")
A(f"- effectively stable (no positive growth / never reaches 80): "
  f"**{int((~np.isfinite(pred.yrs_to_80) & (pred.latest_amp<80)).sum())}**")
A("")
A("## Top-15 priority queue")
A("| rank | railway | section | km+m | rail | latest %FSH | yrs→80 |")
A("|---|---|---|---|---|---|---|")
for _, x in pred.head(15).iterrows():
    y = x.yrs_to_80
    ys = "breached" if x.latest_amp >= 80 else ("—" if not np.isfinite(y) else f"{y:.1f}")
    A(f"| {int(x.risk_rank)} | {str(x.railway).replace(' Railway','')} | {x.section} | "
      f"{x.km}+{x.meter} | {x.rail} | {x.latest_amp:.0f} | {ys} |")
A("")
A("## Plots")
for p in ["11_powerlaw_fit.png", "12_holdout_pred_vs_actual.png", "13_years_to_imr.png"]:
    A(f"- {p}")
A("")
A("## Honest read")
A(f"- Best next-reading model by MAE was **`{best_model}`** "
  f"(MAE {acc[best_model][0]:.2f} %FSH). If that is `persistence`, the contextual "
  f"model adds little for *forecasting* — its value is in *ranking*, not point prediction.")
A("- Most monitored OBS defects are stable; the model's value is ranking the "
  "growing tail and the already-near-IMR set, not precise per-defect curves.")
A("- No true UIC defect codes in source data → 'family' is a proxy (asset×probe). "
  "Real UIC codes would sharpen the physics fit.")
A("- GMT axis interpolated; ΔK not computed from geometry (no wheel-load/section "
  "modulus in data) → this is a Paris-*style* empirical law, not a full fracture-mechanics fit.")
open(ROOT + "/MODEL_STEP3.md", "w").write("\n".join(L) + "\n")

print(f"families={len(fam_fit)} holdout_test={len(te)} "
      f"MAE_persist={acc['persistence'][0]:.2f} MAE_ml={acc['ML (direct)'][0]:.2f} "
      f"R2_ml={acc['ML (direct)'][1]:.2f} best={best_model} near_IMR={n_breach}")
