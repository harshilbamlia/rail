# Aonami Echo — Modelling Report
## USFD Rail & Weld Defect Growth Propagation Analysis, Indian Railways

**Challenge:** IC0000000177 — Defect Growth Propagation Analysis in Rails and Welds  
**Date:** June 2026  
**Status:** Pipeline Steps 1–4 Complete · Dashboard Deployed

---

## Part A — For Business Stakeholders

### What We Built and Why It Matters

Indian Railways inspects hundreds of thousands of kilometres of track using Ultrasonic Flaw Detection (USFD) machines. Every defect found is measured by its **echo amplitude (%FSH — Full Screen Height)**. When amplitude reaches 80%FSH, RDSO mandates immediate removal (IMR). The problem: between inspections, nobody knows which observed (OBS) defects are quietly growing toward that threshold, and which are stable. Maintenance gangs treat all defects equally, which means dangerous ones can be missed while resources are wasted on stable ones.

**Aonami Echo solves this.** It reads the historical inspection data, models how each defect has grown across runs, and produces a ranked priority queue — telling field engineers exactly which defects need action today, which need watching next month, and which can wait.

---

### What the Data Covers

| Item | Number |
|------|--------|
| Source files analysed | 56 RDSO USFD reports (.xls/.xlsx) |
| Railways covered | 9 zonal railways |
| Total defects tracked | **3,997** |
| Total inspection readings | **11,153** |
| Defects with 3 inspection rounds | **3,312 (83%)** |
| Rail defects | 2,638 |
| Weld defects | 1,359 |

---

### What the Models Found

#### The Fleet Is Not Uniformly Safe

| Severity Band | Defects | What It Means |
|---|---|---|
| ≥80%FSH — IMR threshold breached | **175** | Must be removed immediately; RDSO clock running |
| 60–80%FSH — approaching IMR | **209** | High priority; plan replacement within weeks |
| 30–60%FSH — active monitoring | **1,965** | Growing but time available |
| <30%FSH — low concern | **1,648** | Stable, routine inspection |

#### Who Needs Action Right Now

| Triage Level | Count | Trigger |
|---|---|---|
| **IMMEDIATE** | **566** | Amplitude ≥80%FSH or 4-metre cluster rule |
| **URGENT** | **186** | Predicted to reach IMR within 2 years |
| **MONITOR** | **320** | Growing; 2–5 years to IMR |
| **ROUTINE** | **2,925** | Stable; no escalation predicted |

**566 defects need immediate attention** — not just the 175 already at IMR amplitude, but also defects in dangerous proximity clusters where RDSO's Para 6.3.2 rule mandates treating them as a single removal action.

#### Dangerous Clusters

When 2 or more defects sit within 4 metres of each other on the same track, RDSO mandates a single replacement action covering the whole cluster. We found:

- **205 clusters** across all railways
- Covering **410 defects** in total
- Largest cluster: **3 defects within 5.2 metres**
- A single work order for each cluster replaces the need for multiple separate actions

#### SLA Breaches — Defects Overdue for Removal

RDSO Para 6.4 requires IMR-class defects to be removed within 3 days of detection. Against the dataset's reference date (May 2026):

- **175 defects** have breached the 3-day SLA
- Some have been in track for over **130 days** past their required removal date
- Top breaches: NWR Jaipur–Phulera (up to 135 days overdue)

#### Which Railways Have the Highest Risk

| Railway | Defects | Near-IMR (≥80%) | IMMEDIATE | Avg %FSH |
|---|---|---|---|---|
| South Western Railway | 434 | **100** | 135 | 54.5% |
| North Western Railway | 1,069 | 39 | 143 | 38.1% |
| Central Railway | 181 | 23 | 32 | 49.0% |
| East Central Railway | 692 | 0 | 93 | 30.4% |
| Western Railway | 529 | 4 | 71 | 35.9% |

**South Western Railway (Bangalore–Mysore section)** has the highest average amplitude and most defects already at the IMR threshold. **North Western Railway** has the highest absolute count of IMMEDIATE-tier defects.

---

### What the System Predicts

Based on current growth rates, the system projects:

| Timeframe | Defects Expected to Reach IMR (80%FSH) |
|---|---|
| Already breached | 175 |
| Within 1 year | 116 |
| Within 2 years | +98 (total 389) |
| Within 5 years | +251 (total 640) |
| Effectively stable | 2,569 (64%) |

This means roughly **1 in 6 currently-OBS defects** is on a trajectory to require removal within 5 years. The system flags these early so replacements can be planned during scheduled maintenance blocks rather than emergency responses.

---

### What the System Cannot Do (Honest Limitations)

1. **Point forecasts are approximate.** With only ≤3 inspection readings per defect, precise amplitude predictions carry uncertainty. The system is most reliable as a **ranking tool** (who needs action first) rather than an exact date calculator.

2. **Amplitude is noisy.** Readings vary depending on probe angle, operator calibration, and track conditions. A single high reading does not always mean a defect is growing.

3. **Missing context.** The dataset does not include rail grade, track geometry, speed limits, or axle loads per section — all of which influence how fast cracks grow. Adding this data would sharpen the predictions.

4. **SLA dates are relative to the dataset.** In production, the 3-day SLA clock starts from the upload date of each inspection run, not a fixed reference date.

---

### Business Value Summary

| Outcome | Without Aonami Echo | With Aonami Echo |
|---|---|---|
| Defect prioritisation | All OBS defects treated equally | Risk-ranked queue; worst first |
| SLA compliance | Manual tracking in registers | Automated breach detection |
| Cluster detection | Manual inspection of nearby defects | Automatic Para 6.3.2 grouping |
| Removal planning | Reactive (after fracture or inspection) | Proactive (before threshold) |
| Engineer cognitive load | Review all 3,997 defects | Focus on 566 IMMEDIATE + 186 URGENT |

---

---

## Part B — For Data Scientists & Engineers

### Architecture Overview

The pipeline has four sequential modelling layers plus a dashboard layer:

```
Raw USFD XLS files
      │
      ▼
[Layer 1] Ingestion & Spatial Identity (parse_usfd.py)
      │  → usfd_long.csv  (11,153 rows, one per defect×round)
      ▼
[Layer 2] Cleaning & GMT Interpolation (eda_step2.py)
      │  → usfd_clean.csv, usfd_defects.csv
      ▼
[Layer 3] Growth Modelling & RUL Projection (growth_model_step3.py)
      │  → usfd_predictions.csv, family_powerlaw.csv, model.pkl
      ▼
[Layer 4] RDSO Triage & Escalation (triage_step4.py)
      │  → triage_queue.csv, clusters.csv, usfd.db
      ▼
[Layer 5] FastAPI + React Dashboard (app.py + public/)
```

---

### Layer 1 — Ingestion & Spatial Identity

**Problem:** RDSO USFD reports are wide-format Excel files with a 3-row merged header under a title block, 5 distinct column layouts across file types (rail OBS / weld OBSW / combined), and filename-encoded metadata.

**Solution:** `parse_usfd.py` uses regex anchored to "Analysis Report" to detect the title block, reads the 3-row multi-level header by column position, and transposes the wide format to a long table.

**Defect UID:** A persistent key is assigned as:
```
defect_uid = railway | section | line | km | meter_point | post_from | rail_side
```
This is the identity anchor for matching across files. In-file matching (the 3 rounds already row-aligned) is exact; cross-file matching uses the same key with ±drift tolerance (not yet exercised — the NER/NR ground-truth validation confirmed 100% exact match for in-file rounds).

**Output statistics:**

| Metric | Value |
|---|---|
| Total rows (defect×round) | 11,153 |
| Unique physical defects | 4,104 |
| Defects with ≥2 amplitude readings | 3,697 |
| Defects with all 3 rounds | 3,336 |
| Railways | 9 |
| Mean amplitude | 36.2 %FSH |
| Amplitude readings | 11,135 |

---

### Layer 2 — Cleaning & GMT Interpolation

**Key operations:**

1. **Amplitude cleaning:** 1 reading >100%FSH (physically impossible — screen overflow) → set to NaN. 226 zero readings flagged but retained (genuine "not detected" state).

2. **Date parsing:** `dd/mm/yyyy` → `datetime64`. Laying month parsed from `mm/yyyy`. Weld dates similarly.

3. **GMT interpolation:** Only a single cumulative `gmt_carried` value is available per defect (total GMT at time of most recent inspection). Per-round GMT is interpolated linearly:

```python
gmt_at_round = gmt_carried * (days_from_install_to_round / days_from_install_to_latest)
```
This assumes constant GMT accumulation rate — a known approximation. Traffic schedules per section per date are not available in the dataset.

**Post-cleaning dataset:**

| Metric | Value |
|---|---|
| Clean defects (physical, post-dedup) | 3,999 |
| Growth cohort (≥2 amplitude readings) | 3,641 (91%) |
| All-3-round cohort | 3,312 |
| Amplitude increase seen | 46% of defects |
| Median amplitude delta (3rd→latest) | 0 %FSH |
| Mean growth rate | 3.3 %FSH/yr |
| p90 growth rate | 21 %FSH/yr |
| Defects ≥60%FSH | 384 |
| Defects ≥80%FSH | 175 |

The key finding: **most OBS defects are stable (median delta = 0)**. The model's signal lives in the tail — the ~10% with elevated growth rates.

---

### Layer 3 — Growth Modelling & RUL

Three distinct models were fitted and benchmarked.

#### 3A. Paris–Erdogan Descriptive Power Law

**Form:** `da/dN = C · a^m`

Where `a` = echo amplitude (%FSH, used as a proxy for crack size), `N` = cumulative GMT (used as the loading cycle axis). This is a Paris-style empirical law — not a full fracture-mechanics derivation (which would require ΔK from geometry, load, and material properties not available in the dataset).

**Fitting:** Pooled log-linear regression on positive-growth segments (amplitude increasing from one round to the next), grouped by **defect family = asset_type × probe_angle_bucket**. Negative-growth segments excluded from fitting (probe noise, genuine healing not modelled).

**Family definitions:**

| Family | C | m | Defects | Avg %FSH | Near-IMR |
|---|---|---|---|---|---|
| weld:0° | 7.16e-03 | 1.69 | 289 | 48.5% | 61 |
| rail:70°NGF | 6.21e-03 | 1.46 | 33 | 38.5% | 1 |
| rail:70°GF | 3.91e-01 | 0.47 | 169 | 37.8% | 4 |
| rail:70° | 1.30e-03 | 1.76 | 1,139 | 37.2% | 29 |
| weld:70°NGF | 1.51e-02 | 1.39 | 25 | 35.8% | 0 |
| rail:0° | 8.32e-03 | 1.46 | 1,294 | 35.2% | 78 |
| weld:70° | 1.65e-02 | 1.08 | 900 | 33.6% | 1 |
| weld:70°GF | 1.84e-02 | 1.22 | 139 | 32.2% | 1 |
| __GLOBAL__ | 4.02e-03 | 1.59 | — | — | — |

**Interpretation of m:**
- `m > 1` (most families): growth rate accelerates as amplitude increases — the larger the defect, the faster it grows. This is physically expected from fracture mechanics (ΔK ∝ crack size).
- `rail:70°GF` has `m = 0.47` (sub-linear): growth rate decreases at high amplitudes for this probe/family, likely a probe-geometry artefact or genuine saturation.
- `weld:0°` has the highest average amplitude (48.5%) and is the highest-risk weld family.

**Caveat:** The `rail:70°GF` outlier (C=0.39, m=0.47) was fitted on only 91 positive segments. Treat with caution — the high C is likely partially offset by the low m.

#### 3B. RUL Projection (Slope Method)

The power-law fit is descriptive across the fleet but over-predicts growth for individual defects when fitted only on positive segments (it ignores the 54% of readings that are flat or decreasing). A simpler per-defect slope is more honest:

**Method:**
1. Fit a linear slope `dA/dGMT` for each defect using its ≤3 points (own slope).
2. Shrink the own slope toward the family median slope using partial pooling (weighted average: `slope_eff = 0.7 × own + 0.3 × family_median`).
3. Project from the latest amplitude to 30/60/80%FSH thresholds.
4. Stable if `rate_eff ≤ 0.05 %FSH/GMT`.

**RUL distribution:**

| Band | Count |
|---|---|
| Already ≥80%FSH | 175 |
| <1 year to IMR | 116 |
| 1–2 years | 98 |
| 2–5 years | 251 |
| >5 years | 788 |
| Stable / no finite RUL | 2,569 |

#### 3C. HistGradientBoosting Next-Reading Predictor

**Purpose:** Predict the amplitude at the next inspection (~1yr/30GMT ahead) for any defect.

**Features:**

| Feature | Type | Description |
|---|---|---|
| `amp_prev` | numeric | Latest amplitude reading |
| `slope_prev` | numeric | GMT-axis slope (own estimate) |
| `dN` | numeric | GMT step ahead (set to 30.0) |
| `dt_days` | numeric | Days ahead (set to 365) |
| `gmt_rate` | numeric | GMT accumulation rate per year |
| `asset` | categorical | rail / weld |
| `probe` | categorical | 0° / 70° / 70°GF / 70°NGF / other |
| `railway` | categorical | NWR / SWR / CR / etc. |

**Architecture:** `sklearn.pipeline.Pipeline` with `OneHotEncoder` (categorical features) + `HistGradientBoostingRegressor`. Target: next reading amplitude in %FSH.

**Training:** All 7,088 transition pairs (from round N to round N+1) across 3,638 defects.

**Validation: 5-fold GroupKFold (grouped by defect_key — no data leakage)**

| Model | MAE (%FSH) | R² |
|---|---|---|
| **Persistence** (predict unchanged) | **4.75** | 0.535 |
| **HistGBM** | 4.89 | **0.687** |
| Linear regression | 6.27 | 0.27 |
| Power-law physics | 8.80 | 0.15 |

**The ML model does not beat persistence on MAE.** Persistence (assume unchanged) achieves MAE 4.75 %FSH; the ML model achieves 4.89 %FSH. This is the honest result from a deterministic hashlib-based train/test split.

**Why persistence is hard to beat with ≤3 readings:**
- Most defects are genuinely flat (median delta = 0) → persistence is correct for ~54% of the dataset.
- The contextual features (GMT rate, probe, railway) add discriminative power (R² 0.687 > 0.535) but do not reduce absolute error because the high-R² comes from fitting the flat majority, not from catching the growing tail.
- With ≥5 readings per defect (not available here), the ML model would likely dominate.

**Correct use of the ML model:** Use it for **ranking** (which defects have the highest predicted next amplitude?) and as one signal in the triage composite score, not as a precise point forecast. The RUL slope projection from Layer 3B is a stronger predictor of the growing tail.

---

### Layer 4 — RDSO Triage & Escalation

A deterministic scoring layer applies RDSO USFD Manual 2022 rules to produce the final triage queue.

#### Severity Bands (Annexure II-A)

| Band | %FSH Range | Count |
|---|---|---|
| IMR-level | ≥80% | 175 |
| High concern | 60–80% | 209 |
| Monitoring | 30–60% | 1,965 |
| Low | <30% | 1,648 |

#### Triage Level Assignment

```
IMMEDIATE if:
  latest_amp >= 80%FSH                    → already at IMR threshold
  OR  para632_escalate = True              → 4-metre cluster rule triggered
  (566 defects)

URGENT if:
  yrs_to_80 <= 2 AND latest_amp < 80      → predicted escalation within 2 years
  (186 defects)

MONITOR if:
  yrs_to_80 <= 5 OR latest_amp >= 60      → worth watching closely
  (320 defects)

ROUTINE otherwise
  (2,925 defects)
```

#### Para 6.3.2 Four-Metre Cluster Detection

**Algorithm:**
1. Group defects by (railway, section, line) — conservative: both rails together.
2. Sort by `chainage_m = km * 1000 + meter`.
3. Greedy scan: if distance between consecutive sorted defects ≤ 4m, add to current cluster. Flush cluster when gap > 4m.
4. Clusters with n_defects ≥ 2 → flag all members `para632_escalate = True`.

**Results:**

| Metric | Value |
|---|---|
| Clusters detected | 205 |
| Total defects in clusters | 410 |
| Average cluster size | 2.0 defects |
| Maximum cluster size | 3 defects |
| Average cluster span | 1.1 m |
| Maximum cluster span | 5.2 m |

**Implication for field teams:** A cluster raises a single work order. 205 clusters = 205 replacement actions (not 410 separate actions).

#### Para 6.4 SLA Breach Detection

IMR-class defects (latest_amp ≥ 80% or classification = IMR) remaining in track after 3 days are flagged as SLA breaches. Reference date: 2026-05-31.

- **175 SLA breaches** found — all defects currently at ≥80%FSH are in breach because the inspection data is historical (the breach count = near-IMR count = 175).
- **Production note:** In live deployment, the 3-day SLA clock starts from the date an inspection run is uploaded to the system, not from a static reference date.

#### Composite Triage Rank

Within each triage level, defects are ranked by:

```
Primary key:  triage_level (IMMEDIATE > URGENT > MONITOR > ROUTINE)
Secondary:    latest_amp DESC
Tertiary:     yrs_to_80 ASC (sooner breach = higher priority)
```

The `risk_score` field stores a composite score for numerical ranking:
- IMMEDIATE/cluster: `1,000,000 + latest_amp` (sentinel, forces to top)
- Others: `100 × (1/yrs_to_80) + latest_amp / 100`

---

### Layer 5 — API & Dashboard

**Backend:** FastAPI 0.136 (Python 3.13), SQLite via `sqlite3` stdlib, `joblib` for model loading.

**Endpoints:**

| Endpoint | Purpose |
|---|---|
| `GET /api/stats` | Fleet-level KPIs |
| `GET /api/queue` | Paginated triage queue with sparklines |
| `GET /api/defect_growth` | Per-defect history + projection |
| `GET /api/model/families` | Paris–Erdogan parameters per family |
| `GET /api/model/rul-distribution` | Fleet RUL histogram |
| `GET /api/cluster/{id}` | Cluster detail + member defects |
| `GET /api/annexure3` | Annexure III pre-fill |
| `POST /api/import` | Parse uploaded USFD XLS |

**Frontend:** React 18 + Babel (client-side transform, no build step), IBM Plex Sans/Mono, inline SVG charts. 8 screens: Priority Queue, Defect Detail, Track Map, Zone Rollup, Cluster View, Inspection Run Import, Annexure III Form, Growth Models.

---

### Validation

#### Ingestion Fidelity (100% confirmed)

The NER Gonda–Daliganj and NR LKO–CNB combined workbooks (analyst hand-built ground truth) were used to validate the parser. Matching by `km + meter ± 3m + rail`:

- NER: **354/354 = 100%** match rate
- NR: **61/61 = 100%** match rate
- Amplitude MAE vs analyst ground truth: **0.0 %FSH**
- Amplitude correlation: **1.00**

**Important caveat:** The ground-truth workbooks are re-organisations of the same 2025–2026 inspection data (not an independent older dataset). This validates **ingestion fidelity**, not cross-run drift matching.

#### Growth Model Holdout (deterministic split)

- Split: 70/30 by defect, using `hashlib.md5(defect_key)` for reproducibility.
- Holdout: **1,008 defects**, predicting their 3rd reading from the 1st and 2nd.
- Best model by MAE: **persistence** (MAE 4.21 %FSH, R² 0.59)
- ML model: MAE 4.58 %FSH, R² 0.70

The 5-fold GroupKFold CV on the full training pipeline (all 7,088 transitions):
- ML MAE: **4.89 %FSH**, R² 0.687
- Persistence MAE: **4.75 %FSH**, R² 0.535

Both evaluation setups agree: the ML model does not beat persistence on MAE. The higher R² (0.69 vs 0.54) reflects that the model correctly handles the non-trivial tail, but the majority flat-defect population dominates the MAE metric.

---

### What Would Improve the Models

Ranked by expected impact:

| Improvement | Expected Impact | Data Required |
|---|---|---|
| More inspection rounds (≥5 per defect) | High — enables reliable per-defect curve fitting | Longitudinal follow-up |
| Real UIC defect codes (111, 211, 135…) | High — sharper family definitions than asset×probe proxy | Defect code field in USFD reports |
| Per-section GMT accumulation rates | Medium — removes the linear-interpolation assumption | Traffic data per section per date |
| Axle load and speed limits per section | Medium — enables true ΔK estimation for Paris law | Infrastructure database |
| Track geometry (curve radius, grade) | Medium — curves concentrate stress | Track geometry database |
| Rail grade and manufacturing batch | Low–medium | USFD report enhancement |
| Operator and machine ID per round | Low — enables probe calibration drift correction | USFD report enhancement |

---

### File Inventory

| File | Description |
|---|---|
| `parse_usfd.py` | Wide-to-long USFD parser |
| `eda_step2.py` | Cleaning, GMT interpolation, EDA |
| `growth_model_step3.py` | Power-law fit, RUL projection, HistGBM |
| `train_model.py` | Persist model.pkl with full GroupKFold CV |
| `triage_step4.py` | RDSO triage, cluster detection, SLA |
| `build_db.py` | Load all CSVs into usfd.db |
| `app.py` | FastAPI backend (v3.0) |
| `usfd_long.csv` | Raw long table (11,153 rows) |
| `usfd_clean.csv` | Cleaned, GMT-interpolated |
| `usfd_defects.csv` | One row per defect with growth features |
| `usfd_predictions.csv` | RUL + risk score per defect |
| `triage_queue.csv` | Final ranked triage queue |
| `clusters.csv` | Para 6.3.2 cluster records |
| `family_powerlaw.csv` | Paris–Erdogan C, m per family |
| `model.pkl` | Trained HistGBM sklearn Pipeline |
| `model_meta.json` | CV metrics, features, training metadata |
| `usfd.db` | SQLite database (all tables + views) |

---

### Key Numbers at a Glance

| Metric | Value |
|---|---|
| Defects in system | 3,997 |
| Already at IMR threshold (≥80%FSH) | 175 |
| IMMEDIATE triage (action now) | 566 |
| URGENT triage (action within 2yr) | 186 |
| 4-metre clusters (Para 6.3.2) | 205 |
| SLA breaches | 175 |
| Growth-usable cohort (≥2 readings) | 3,641 |
| Paris–Erdogan families fitted | 10 |
| Positive-growth segments used in fit | 2,051 (global) |
| HistGBM training transitions | 7,088 |
| CV MAE (HistGBM) | 4.89 %FSH |
| CV MAE (persistence baseline) | 4.75 %FSH |
| Model R² | 0.687 |
| Ingestion validation accuracy | 100% (NER+NR ground truth) |
| Railways covered | 9 |
| Sections covered | 22+ |
