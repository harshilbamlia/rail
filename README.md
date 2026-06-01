# Aonami Echo — USFD Run-on-Run Defect Pipeline

Turns raw RDSO USFD "Test Analysis Report" `.xls` files into a cleaned dataset,
a defect-growth model, an RDSO-rule triage queue, a database, an API, and a
dashboard.

## Pipeline (run in order)
```bash
.venv/bin/python3 parse_usfd.py          # 1. ingest 50 .xls -> usfd_long.csv
.venv/bin/python3 eda_step2.py           # 2. clean + GMT axis -> usfd_clean.csv, usfd_defects.csv
.venv/bin/python3 growth_model_step3.py  # 3. growth model + RUL -> usfd_predictions.csv
.venv/bin/python3 triage_step4.py        # 4. RDSO triage -> triage_queue.csv, clusters.csv
.venv/bin/python3 build_db.py            #    load all into usfd.db
.venv/bin/python3 matching_validation.py #    validate parsing vs hand-built Combined sheets
.venv/bin/python3 generate_dashboard.py  #    -> dashboard.html (self-contained)
```

## View it
- **Dashboard:** open `dashboard.html` in a browser (no server needed), or
- **API + dashboard:** `.venv/bin/uvicorn app:app --port 8000` then visit
  `http://localhost:8000/` (dashboard) and `/docs` (API).

## Database (`usfd.db`)
Tables: `inspections` (long, defect×round), `defects`, `predictions`,
`family_powerlaw`, `triage_queue`, `clusters`.
Views: `priority_queue`, `morning_queue`.

## What it found (current data)
- **11,153** inspection records · **3,999** physical defects · 9 zones
- **566 IMMEDIATE**, 186 URGENT, 320 MONITOR, 2,925 ROUTINE
- **205** four-metre clusters (Para 6.3.2) · **175** near-IMR (≥80 %FSH)

## Validation
- **Parsing fidelity:** matched **100%** of the analysts' hand-compiled Combined
  defects (NER, NR) with **exact** amplitudes — confirms ingestion is faithful.
  (Those sheets re-organise the same 2026 source, so this validates parsing, not
  cross-run drift matching.)
- **Growth model holdout:** R² ≈ 0.70 (below the spec's 0.75 target); plain
  persistence wins on MAE. The model's value is **ranking**, not precise per-defect
  forecasts — with ≤3 noisy readings/defect that is the honest ceiling.

## Honest limitations
- No true UIC defect codes in the source (family = asset × probe proxy); ΔK not
  computed from rail geometry → this is a Paris-*style* empirical law.
- GMT axis interpolated from a single cumulative `gmt_carried` value.
- SLA clock uses a static reference date (2026-05-31); production should start it
  at upload time.

## Key docs
`PROBLEM_STATEMENT.md`, `DS_PROBLEM_AND_FEATURES.md`, `EDA_STEP1/2.md`,
`MODEL_STEP3.md`, `TRIAGE_STEP4.md`, `MATCHING_VALIDATION.md`.
