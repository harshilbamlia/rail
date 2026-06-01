#!/usr/bin/env python3
"""
Aonami Echo — load the pipeline outputs into a single SQLite database (usfd.db).

Tables:
  inspections      -- long: one row per (defect x inspection round)   [usfd_clean.csv]
  defects          -- one row per physical defect + growth features   [usfd_defects.csv]
  predictions      -- per-defect RUL + risk rank                        [usfd_predictions.csv]
  family_powerlaw  -- fitted C, m per defect family                     [family_powerlaw.csv]
View:
  priority_queue   -- predictions ordered by risk_rank (the SE/JE morning list)

SQLite needs no install (Python stdlib). Migrates 1:1 to Postgres later.
"""
import os, sqlite3
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "usfd.db")

TABLES = {
    "inspections":     "usfd_clean.csv",
    "defects":         "usfd_defects.csv",
    "predictions":     "usfd_predictions.csv",
    "family_powerlaw": "family_powerlaw.csv",
}

INDEXES = [
    ("inspections", "ix_insp_defect", "defect_key"),
    ("inspections", "ix_insp_loc",    "railway, section, km, meter"),
    ("inspections", "ix_insp_date",   "insp_dt"),
    ("defects",     "ix_def_key",     "defect_key"),
    ("predictions", "ix_pred_key",    "defect_key"),
    ("predictions", "ix_pred_rank",   "risk_rank"),
    ("predictions", "ix_pred_loc",    "railway, section"),
]

def main():
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    counts = {}
    for table, csv in TABLES.items():
        path = os.path.join(ROOT, csv)
        if not os.path.exists(path):
            print(f"  skip {table}: {csv} missing"); continue
        df = pd.read_csv(path)
        df.to_sql(table, con, if_exists="replace", index=False)
        counts[table] = len(df)
    cur = con.cursor()
    for table, name, cols in INDEXES:
        try:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({cols});")
        except sqlite3.OperationalError as e:
            print(f"  index {name} skipped: {e}")
    cur.execute("DROP VIEW IF EXISTS priority_queue;")
    cur.execute("""
        CREATE VIEW priority_queue AS
        SELECT risk_rank, railway, section, km, meter, rail,
               latest_amp, yrs_to_80, yrs_to_60, n_readings
        FROM predictions
        ORDER BY risk_rank;
    """)
    con.commit()

    # quick verification
    print("DB:", os.path.relpath(DB, ROOT))
    for t, n in counts.items():
        print(f"  {t}: {n} rows")
    top = cur.execute(
        "SELECT railway, section, latest_amp FROM priority_queue LIMIT 3;").fetchall()
    print("  priority_queue head:", top)
    con.close()

if __name__ == "__main__":
    main()
