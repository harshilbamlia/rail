# Aonami Echo — Problem Statement

*Challenge **IC0000000177** — "Defect Growth Propagation Analysis in Rails and Welds". Sources: Aonami Echo Detailed Solutioning v1.0 + Engineering Solution Document v1.0. Framed 2026-05-31.*

---

## 1. One-line statement
USFD inspection is excellent at **detecting** rail/weld defects but has no system for tracking how a **specific defect grows across successive inspection runs**. Aonami Echo is the **progression layer**: it links the same defect across runs, models its growth, predicts when it will cross the danger threshold, and ranks defects for maintenance.

## 2. Context (the domain)
- Indian Railways runs the world's largest periodic **USFD (Ultrasonic Flaw Detection)** programme: >1.1 lakh route-km tested in 2024–25, ~12,000 IMR/IMRW classifications raised, ~500 reportable rail/weld fractures.
- A USFD trolley logs defects by **chainage (KM + TP)**, **UIC defect code**, **classification** (IMR / OBS / IMRW / OBSW / DFWO / DFWR / DFWN), and an **amplitude in % FSH** (Full Screen Height — the proxy for crack size).
- Time is measured in **GMT** (Gross Million Tonnes of cumulative traffic), not calendar days.
- Each run is filed as an **independent snapshot** in the Annexure VI (rail) / VII (weld) master registers — indexed by location, **not by defect identity**.

## 3. The core problem — the "Detection–Progression Gap"
When an operator finds an OBS defect at, say, KM 482 + TP 1200, they clamp a fish plate, log it, and move on. The next inspection is weeks/months later. **Nobody can answer:** *Is this defect bigger than last time? By how much? At this rate, when does it cross the IMR threshold — before the next scheduled inspection?*

Today every OBS defect is treated identically (same SLA, same cadence, same priority) even though a 28% FSH defect under 5 GMT/day on straight track is operationally inert, while a 28% FSH defect under 50 GMT/day on a curve will become IMR before the next run.

## 4. Why this is technically non-trivial — **chainage drift**
You cannot just compare the same KM+TP across runs. Two trolleys scanning the same kilometre on different dates produce different chainage readings for the same physical spot — typically **±3 m**, occasionally ±5 m. Causes: encoder slip on curves, operator start-marker variation, GPS dropout in tunnels/cuttings, wheel slip on wet rail. On a section with ~50 defects/km, matching Run-2 readings to Run-1 defects by hand is infeasible. **A matching algorithm is required.**

## 5. The four modelling problems (solved in sequence — each feeds the next)
| # | Question | Method | Output |
|---|----------|--------|--------|
| 1 — **Identity** | Is this the same defect as last run? | Hungarian assignment (`scipy.linear_sum_assignment`) over a cost function of chainage distance + UIC-family compatibility + probe-set agreement | Persistent defect ID across runs |
| 2 — **Growth** | How fast is it growing? | Physics-informed **Paris–Erdogan law** (`da/dN = C·(ΔK)^m`) with FSH as crack-size proxy & GMT as cycle axis, + **XGBoost** residual correction | Growth curve on a GMT axis |
| 3 — **Prediction** | When does it cross IMR? | Regression to the 30/60/80% FSH thresholds + bootstrap confidence interval | GMT-to-IMR estimate (RUL) with 1σ/2σ band |
| 4 — **Prioritisation** | Which defect needs attention first? | Multi-criteria deterministic ranking + RDSO escalation rules (Para 6.3.1/6.3.2 4-metre cluster rule, Para 6.4 SLA clock) | Morning priority queue for the SE/JE |

**Why physics before ML:** failures are sparse (~20–40/division/year — too few for pure ML), predictions must extrapolate beyond historical analogues, and engineers need an interpretable, challengeable curve — not a black-box score. Paris law = physics skeleton; XGBoost = residual correction.

## 6. What "good" looks like (outcome definition)
- Every detected defect gets a **stable identity** across all historical & future runs.
- Every defect carries a **predicted amplitude trajectory** (% FSH vs cumulative GMT) with confidence bands.
- Every defect has a **predicted breach point** at the 30/60/80% FSH thresholds.
- Every 2+ defect cluster within 4 m auto-escalates per Para 6.3.2 with a single work order.
- Every IMR auto-starts the Para 6.4 SLA clock (24-hr fish plate, 3-day replacement).
- Model target: **R² ≥ 0.75** on a held-out most-recent inspection cycle.

## 7. Solution architecture (5 layers)
1. **Ingestion** — normalize data from the 4 RDSO-approved machine families (ECIL DRT/SRT, Vibronics, EEC RAIL-SCAN, Paras DRT) and their formats (CSV, JSON, XML, .dat, .vib, .pdrt).
2. **Spatial alignment** *(the core IP)* — Hungarian matching across runs despite chainage drift.
3. **Growth modeling** — Paris–Erdogan + XGBoost per tracked defect.
4. **Risk & escalation** — deterministic RDSO rules → RUL & priority.
5. **Echo dashboard** — 8 UI pages (Priority Queue, Defect Detail, Track Map, Zone Rollup, etc.).

## 8. The data we actually have (`Data run on run/`)
Real run-on-run USFD exports across zonal railways — paired `.xls` **OBS / OBSW** observation files — to validate the matching + growth pipeline:
- **CR** Londa–Miraj · **NER/NR** Gonda–Burhwal–Daliganj, LKO–CNB (3-round) · **NWR** Jaipur–Phulera, Jodhpur–Jaisalmer · **SR** · **ECR/NFR** (TMS data: SEE–BJU–HJP, SPJ–BCA, KGG–SPJ, APDJ, Katihar) · **Bangalore–Mysore** · **WR** Sambhari–Bhidi
- Plus summary/analysis `.pptx`/`.xlsx` of zones already analysed.

## 9. Pilot / commercials (context)
120-day pilot, 1 division, 200–500 route-km, ₹1.78 cr (50/50 innovator/grant). Live prototype filed. Primary standard: RDSO Manual for Ultrasonic Testing of Rails and Welds (Revised 2022, ACS-1/2).

---

## Status & next steps
- **Done:** venv set up; `.xls` probe scripts written; problem statement framed (this doc).
- **Next, in pipeline order:**
  1. **Ingestion/standardization** — parse the `.xls` OBS/OBSW files into one unified schema (KM+TP → 8-digit canonical chainage, UIC code, classification, % FSH, run/date, GMT).
  2. **Identity** — implement Hungarian cross-run matching to assign persistent defect IDs.
  3. **Growth + Prediction** — fit Paris–Erdogan per matched defect, predict GMT-to-IMR.
  4. **Prioritisation/escalation** — deterministic ranking + RDSO rules.
