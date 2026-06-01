# Aonami Echo — Data Science Problem Statement & Feature Inventory

*Grounded in the actual `Data run on run/` files. Profiled & corrected 2026-05-31.*

> **Correction note:** an earlier draft of this file claimed there was *no %FSH amplitude* in the data. That was wrong — it came from a header-detection bug that read a data row as the header. The correct reading (header at rows 3–5 under the report title block) shows the files **do** contain echo amplitude (%FSH) and **up to 3 inspection rounds per defect already aligned in one row.** This version supersedes that claim.

---

## 0. TL;DR
The files are **RDSO "USFD Rail/Weld Test Analysis Reports"** — one row per monitored defect, in a **wide multi-inspection layout**: each row carries the defect's location + asset metadata, then **3 repeated inspection blocks** (Latest / 2nd-Last / 3rd-Last), each with `Inspection date · Classification · Probe Used · Echo Amplitude (%)`. So **the run-on-run amplitude time-series already exists** (≤3 points per defect), and the spec's amplitude-vs-time growth modelling **is feasible on this data**.

## 1. The true file structure (wide format)
```
Row 0 : Title block  -> Railway / Division / Section / Line / Defect Classification (OBS(Rail)/OBSW...) / "Last 3 Inspection"
Row 1 : Reporting Date
Row 3 : SR.No. | KM | Meter Point | POST From | POST To | LR/RR | Laying Month | GMT Carried | Removed or Not | [Inspection Details ->]
Row 4 :  (merged spans)                                                              Latest Insp. | 2nd Last Insp. | 3rd Last Insp.
Row 5 :  Inspection date | Classification | Probe Used | Echo Amplitude (in %)   x3 (one set per round)
Row 6+: data rows
```
Weld (OBSW) files swap `Laying Month` for **`Weld Type | Weld Date | Departmental Agency | GMT Carried | Initial Testing Date`**.

## 2. Dataset at a glance
- **50 `.xls` report files** + **6 `.xlsx`** analysis/summary files, across ~10 zones (CR, NER, NR, NWR, SR, ECR, NFR, WR, SWR Bangalore–Mysore).
- **~3,500+ defect rows** total (largest single files: Jodhpur–Jaisalmer 355, WR Sambhari 294/249, SR 230, NWR Jaipur–Phulera 212).
- Each file is **pre-filtered to one defect class** (the title says e.g. `Defect Classification: OBS(Rail)`), so within a file the class is ~constant — **the varying severity signal is the Echo Amplitude trend across the 3 rounds**, not the class label.
- Files come as **OBS (open-rail)** and **OBSW (weld)** pairs per section/direction (UP/DN/SL).
- **NER Gonda–Daliganj** and **NR LKO–CNB** also have hand-built `Combined … 3 round` + `analysis` workbooks (peak-height increase/decrease, banded by **temperature zone** and **GMT band**) → analyst ground-truth for what "growth" should look like.

## 3. Feature inventory
### A. Identity / location (per defect)
| Feature | Type | Example | Use |
|---|---|---|---|
| KM | int | 559, 638, 665 | chainage km |
| Meter Point | int | 471, 765, 285 | metre within km |
| POST From / POST To | str | 4→5, HP2→HP3 | bounding track posts (refines location) |
| LR/RR | cat | LR (Left Rail), RR (Right Rail) | which rail |
| Railway / Division / Section / Line / Direction | cat | CR / Pune / Londa–Miraj / DN | from title block + filename |

### B. Asset / loading metadata (per defect — the physics covariates)
| Feature | Type | Example | Use |
|---|---|---|---|
| **Laying Month** | date | 02/2005, 11/2018 | rail age (rail files) |
| **GMT Carried** | int | 50, 265, 472, 541 | cumulative tonnage on the rail — the cycle/loading axis |
| Weld Type / Weld Date / Dept. Agency / Initial Testing Date | mixed | AT/FB… | weld files only |
| Removed or Not | cat | Exist / Removed | censoring / outcome flag |

### C. Per-inspection block (×3: Latest, 2nd-Last, 3rd-Last)
| Feature | Type | Example | Use |
|---|---|---|---|
| **Inspection date** | date | 24/02/2026, 03/11/2025, 10/07/2025 | time axis |
| **Classification** | cat | OBS(JP), OBS(W), GR | per-round status (JP=joggle plate, GR=ground) |
| **Probe Used** | cat | Fixed Angle Probe / 0, /70, /70GF, /70NGF | probe channel/angle (defect orientation sensitivity) |
| **Echo Amplitude (in %)** | int 0–100 | 22, 57, 99, 60→47→42 | **THE %FSH growth signal (≈ crack size proxy)** |

### D. Pre-computed by analysts (extra sheets in each file)
- Sheet1/2/3 hold **amplitude deltas between rounds** (e.g. Latest−2ndLast, 2ndLast−3rdLast), sorted — the run-on-run growth increments.
- Summary workbook: **temperature-zone** and **GMT-band** breakdowns + "increased/decreased peak height" cohorts.

## 4. Data realities & caveats
- **Short series:** ≤3 inspections per defect (often 1–2; blanks where no prior round). Limits curve-fitting per defect → expect to **pool defects** by family/section for robust growth estimates.
- **GMT is a single cumulative value**, not per-round → must **interpolate GMT at each inspection date** (e.g. from Laying Month + total GMT ⇒ average GMT/month ⇒ GMT at each date) to get the Paris-law cycle axis.
- **Amplitude is noisy / non-monotonic** (probe angle changes between rounds, operator variation; values bounce e.g. 60→47→42 or 31→58→38). Probe-used must be controlled for.
- **Amplitude saturates at ~99–100%** (screen clipping) → right-censored signal.
- **5 raw column layouts**; headers offset under a merged title block; multiple helper sheets per file; messy casing/typos in filenames & categories (LR vs RR consistent here; `OBS(JP)` vs `OBS(W)`).
- Class is ~constant per file (mostly OBS-class monitored defects); few/no IMR files (IMR defects are removed immediately, so under-represented).

## 5. The DS problem — formulations that fit this data
| # | Problem | Formulation | Feasible now? |
|---|---------|-------------|---------------|
| 1 | **Identity / matching** | Within-file the 3 rounds are already row-aligned; *across files/zones* and for new uploads, match defects by (KM, Meter Point, post, rail, probe) with ±drift tolerance. NER/NR combined sheets = validation. | ✅ |
| 2 | **Growth modelling** | Model **Echo Amplitude (%FSH) vs GMT/time** per defect; pool by UIC/defect family + section for the Paris–Erdogan `da/dN=C·ΔK^m` baseline + ML residual on covariates (rail age, GMT rate, probe, track, temp zone). | ✅ (pooled; per-defect limited by ≤3 pts) |
| 3 | **Prediction (RUL)** | Extrapolate amplitude trajectory → **GMT/time until it crosses 30/60/80% FSH** thresholds, with confidence band. | ✅ |
| 4 | **Prioritisation** | Rank by predicted breach time × consequence (GMT/traffic, rail age, weld vs rail, location). | ✅ |

## 6. Step-by-step plan (one by one)
1. **Ingestion & standardization** ← *start here.* Parser for the wide multi-inspection layout (all 5 variants) → **tidy LONG table**: one row per (defect × inspection round) with columns `[zone, division, section, line, direction, km, meter, post_from, post_to, rail, asset_type(rail/weld), laying_month/weld_date, gmt_carried, defect_uid, round_label, inspection_date, classification, probe, amplitude_pct, removed]`. Parse zone/section/line/direction from the title block + filename.
2. **EDA** — amplitude distributions, per-round counts, growth-increment distributions, probe effects, coverage by zone, monotonicity checks.
3. **GMT-at-date interpolation** + defect family tagging.
4. **Matching/identity** validation against NER/NR combined sheets.
5. **Growth model** (pooled Paris-Erdogan + residual) → **RUL prediction** → **prioritisation** output.

---
### Artifacts this session
- `SCHEMAS.md`, `DATA_PROFILE_raw.md` — raw layout profiles (note: these used the naive header guess; the true header is rows 3–5 as documented above).
- This file — corrected DS problem statement + feature inventory.
