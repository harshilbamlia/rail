#!/usr/bin/env python3
"""
Aonami Echo — train & PERSIST the next-reading prediction model.

Unlike growth_model_step3.py (which trained a model only to print holdout numbers
and threw it away), this fits the model on ALL inspection transitions and SAVES
it as a reusable artifact:

  model.pkl        -- the trained sklearn pipeline (joblib)
  model_meta.json  -- feature list, training size, honest CV metrics

A "transition" = one (reading_i -> reading_i+1) step of a defect. Target y = the
next reading's %FSH. Features X = current amplitude, recent slope, GMT/time gap,
GMT rate, asset type, probe angle, railway. (Persistence = "predict y = current
amplitude" is included as the benchmark.)

Run:  .venv/bin/python3 train_model.py
"""
import os, re, json
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.model_selection import cross_val_predict, GroupKFold
from sklearn.metrics import mean_absolute_error, r2_score

ROOT = os.path.dirname(os.path.abspath(__file__))

def probe_bucket(p):
    m = re.search(r"/\s*([0-9A-Za-z]+)\s*$", str(p))
    if not m: return "other"
    t = m.group(1).upper()
    return t if t in {"0", "70", "70GF", "70NGF"} else "other"

# ---- build transition-level training table ---------------------------------
df = pd.read_csv(ROOT + "/usfd_clean.csv", parse_dates=["insp_dt"])
df["probe_bucket"] = df["probe"].map(probe_bucket)

rows = []
for key, g in df.groupby("defect_key"):
    g = g.dropna(subset=["amp", "insp_dt", "gmt_at_round"]).sort_values("insp_dt")
    a = g["amp"].to_numpy(); n = g["gmt_at_round"].to_numpy(); d = g["insp_dt"].tolist()
    if len(a) < 2:
        continue
    for i in range(len(a) - 1):
        dN = n[i + 1] - n[i]
        if dN <= 0:
            continue
        slope_prev = (a[i] - a[i - 1]) / (n[i] - n[i - 1]) if i >= 1 and (n[i] - n[i - 1]) > 0 else 0.0
        rows.append({
            "defect_key": key,
            "amp_prev": a[i],
            "slope_prev": slope_prev,
            "dN": dN,
            "dt_days": (d[i + 1] - d[i]).days,
            "gmt_rate": g["gmt_rate_per_yr"].iloc[0],
            "asset": g["asset_type"].iloc[0],
            "probe": g["probe_bucket"].iloc[i],
            "railway": g["railway"].iloc[0],
            "y_next": a[i + 1],
        })
T = pd.DataFrame(rows)
NUM = ["amp_prev", "slope_prev", "dN", "dt_days", "gmt_rate"]
CAT = ["asset", "probe", "railway"]
X, y, groups = T[NUM + CAT], T["y_next"], T["defect_key"]

# ---- model -----------------------------------------------------------------
pipe = Pipeline([
    ("prep", ColumnTransformer([("cat", OneHotEncoder(handle_unknown="ignore"), CAT)],
                               remainder="passthrough")),
    ("gbm", HistGradientBoostingRegressor(max_depth=3, max_iter=300,
                                          learning_rate=0.05, l2_regularization=1.0,
                                          random_state=0)),
])

# ---- honest grouped CV (no defect leaks across folds) ----------------------
cv = GroupKFold(n_splits=5)
yhat = np.clip(cross_val_predict(pipe, X, y, groups=groups, cv=cv), 0, 100)
mae_model = mean_absolute_error(y, yhat); r2_model = r2_score(y, yhat)
mae_persist = mean_absolute_error(y, T["amp_prev"]); r2_persist = r2_score(y, T["amp_prev"])

# ---- fit on ALL data and persist -------------------------------------------
pipe.fit(X, y)
joblib.dump(pipe, ROOT + "/model.pkl")
meta = {
    "target": "next inspection %FSH amplitude",
    "features_numeric": NUM, "features_categorical": CAT,
    "n_training_transitions": int(len(T)),
    "n_defects": int(T["defect_key"].nunique()),
    "cv": "5-fold GroupKFold (grouped by defect — no leakage)",
    "model_mae": round(mae_model, 3), "model_r2": round(r2_model, 3),
    "persistence_mae": round(mae_persist, 3), "persistence_r2": round(r2_persist, 3),
    "beats_persistence_on_mae": bool(mae_model < mae_persist),
    "note": "RUL/time-to-threshold predictions live in usfd_predictions.csv "
            "(slope-projection method); this model predicts the next-reading amplitude.",
}
json.dump(meta, open(ROOT + "/model_meta.json", "w"), indent=2)

print(f"saved model.pkl | transitions={len(T)} defects={T.defect_key.nunique()}")
print(f"  model      MAE={mae_model:.2f}  R2={r2_model:.2f}")
print(f"  persistence MAE={mae_persist:.2f}  R2={r2_persist:.2f}")
print(f"  beats persistence on MAE: {mae_model < mae_persist}")
