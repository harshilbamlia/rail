#!/usr/bin/env python3
"""
Aonami Echo — Step 1: Ingestion parser.

Reads the RDSO "USFD Rail/Weld Test Analysis Report" .xls files in
`Data run on run/` (wide, multi-inspection layout) and emits one tidy LONG
table: one row per (defect x inspection round).

Output:
  usfd_long.csv         -- the tidy long table
  usfd_ingest_summary.md -- per-file row counts + parse notes

Run:  .venv/bin/python3 parse_usfd.py
"""
import os, re, glob, sys
import xlrd
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "Data run on run")

# ---------- helpers ----------------------------------------------------------
def clean(x):
    """ASCII-normalise a cell to a trimmed single-space string."""
    if x is None:
        return ""
    s = str(x)
    s = "".join(c if 32 <= ord(c) < 127 else " " for c in s)
    return re.sub(r"\s+", " ", s).strip()

def to_num(s):
    s = clean(s).replace("%", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None

# map a raw base-header label -> canonical field name
BASE_MAP = [
    ("sr.no",          "sr_no"),
    ("sr no",          "sr_no"),
    ("km",             "km"),
    ("meter point",    "meter"),
    ("post from",      "post_from"),
    ("post to",        "post_to"),
    ("lr/rr",          "rail"),
    ("lr /rr",         "rail"),
    ("laying month",   "laying_month"),
    ("weld type",      "weld_type"),
    ("weld date",      "weld_date"),
    ("departmental",   "agency"),
    ("gmt carried",    "gmt_carried"),
    ("initial testing","initial_testing_date"),
    ("removed",        "removed"),
]

def canon_base(label):
    l = label.lower()
    for key, field in BASE_MAP:
        if key in l:
            return field
    return None

def parse_title_block(text):
    """Pull railway/division/section/line/asset_type/class from the row-0 title."""
    t = clean(text)
    out = {"railway": "", "division": "", "section": "", "line": "",
           "asset_type": "", "file_defect_class": ""}
    m = re.search(r"USFD\s+(\w+)\s+Test", t, re.I)
    if m:
        out["asset_type"] = "weld" if m.group(1).lower().startswith("weld") else "rail"
    # anchor after the "... Analysis Report" prefix so the report title isn't
    # swept into the division; then split "<division> Division/<railway>".
    rest = re.split(r"Analysis Report", t, maxsplit=1, flags=re.I)
    rest = rest[1] if len(rest) > 1 else t
    # railway names may contain '.', '&' (e.g. "N.F. Railway", "S.E.C. Railway")
    m = re.search(r"^\s*(.+?)\s+Division\s*/\s*([A-Za-z.& ]+?Railway)\b", rest, re.I)
    if m:
        out["division"] = m.group(1).strip()
        out["railway"]  = m.group(2).strip()
    m = re.search(r"Section\s*:\s*(.+?)\s+Line\s*:\s*([A-Za-z]+)", t, re.I)
    if m:
        out["section"] = m.group(1).strip()
        out["line"]    = m.group(2).strip()
    m = re.search(r"Defect Classification\s*:\s*([^\n]+?)(?:\s+Number of|\s*$)", t, re.I)
    if m:
        out["file_defect_class"] = m.group(1).strip()
    return out

ROUND_LABELS = {0: "latest", 1: "2nd_last", 2: "3rd_last"}

def pick_data_sheet(wb):
    """The sheet whose row 0 holds the USFD report title; else the largest."""
    for sh in wb.sheets():
        if sh.nrows and "USFD" in clean(sh.cell_value(0, 0)).upper():
            return sh
    return max(wb.sheets(), key=lambda s: s.nrows)

def find_header_row(sh):
    """Row index containing 'SR.No.' (the base header)."""
    for r in range(min(sh.nrows, 15)):
        row = [clean(sh.cell_value(r, c)).lower() for c in range(sh.ncols)]
        if any("sr.no" in v or "sr no" in v for v in row) and any(v == "km" for v in row):
            return r
    return None

def parse_file(path):
    rel = os.path.relpath(path, ROOT)
    notes = []
    try:
        wb = xlrd.open_workbook(path)
    except Exception as e:
        return [], f"OPEN-ERR: {e}"
    sh = pick_data_sheet(wb)

    title = parse_title_block(sh.cell_value(0, 0) if sh.nrows else "")
    # zone abbrev from top-level folder under Data run on run
    parts = rel.split(os.sep)
    zone_folder = parts[1] if len(parts) > 1 else ""

    hr = find_header_row(sh)
    if hr is None:
        return [], "NO-HEADER (skipped)"

    # base column positions
    base_cols = {}
    for c in range(sh.ncols):
        f = canon_base(clean(sh.cell_value(hr, c)))
        if f and f not in base_cols:
            base_cols[f] = c

    # inspection blocks: sub-header row is hr+2; find each 'Inspection date'
    sub_r = hr + 2 if hr + 2 < sh.nrows else hr + 1
    block_starts = []
    for c in range(sh.ncols):
        if "inspection date" in clean(sh.cell_value(sub_r, c)).lower():
            block_starts.append(c)
    if not block_starts:
        notes.append("no-inspection-blocks")

    # data starts after the sub-header (+ a usual blank row)
    data_start = sub_r + 1
    # skip leading fully-blank rows
    while data_start < sh.nrows and not any(clean(sh.cell_value(data_start, c)) for c in range(sh.ncols)):
        data_start += 1

    records = []
    for r in range(data_start, sh.nrows):
        def g(field):
            c = base_cols.get(field)
            return clean(sh.cell_value(r, c)) if c is not None else ""
        sr_no = g("sr_no"); km = g("km")
        if not sr_no and not km:
            continue  # not a data row
        rail = g("rail")
        meter = g("meter")
        post_from, post_to = g("post_from"), g("post_to")
        defect_uid = "|".join([title["railway"], title["section"], title["line"],
                               f"{km}+{meter}", post_from, rail]).strip("|")
        base = {
            "source_file": rel,
            "zone_folder": zone_folder,
            "railway": title["railway"],
            "division": title["division"],
            "section": title["section"],
            "line": title["line"],
            "asset_type": title["asset_type"],
            "file_defect_class": title["file_defect_class"],
            "sr_no": sr_no,
            "km": km,
            "meter": meter,
            "post_from": post_from,
            "post_to": post_to,
            "rail": rail,
            "laying_month": g("laying_month"),
            "weld_type": g("weld_type"),
            "weld_date": g("weld_date"),
            "gmt_carried": to_num(g("gmt_carried")),
            "removed": g("removed"),
            "defect_uid": defect_uid,
        }
        emitted = 0
        for i, s in enumerate(block_starts):
            insp_date = clean(sh.cell_value(r, s)) if s < sh.ncols else ""
            classif   = clean(sh.cell_value(r, s + 1)) if s + 1 < sh.ncols else ""
            probe     = clean(sh.cell_value(r, s + 2)) if s + 2 < sh.ncols else ""
            amp       = to_num(sh.cell_value(r, s + 3)) if s + 3 < sh.ncols else None
            if not insp_date and amp is None and not classif:
                continue  # empty round
            rec = dict(base)
            rec.update({
                "round_label": ROUND_LABELS.get(i, f"round{i+1}"),
                "round_index": i + 1,
                "inspection_date": insp_date,
                "classification": classif,
                "probe": probe,
                "amplitude_pct": amp,
            })
            records.append(rec)
            emitted += 1
        if emitted == 0:
            # keep the defect even if no inspection blocks parsed
            rec = dict(base)
            rec.update({"round_label": None, "round_index": None,
                        "inspection_date": "", "classification": "",
                        "probe": "", "amplitude_pct": None})
            records.append(rec)

    note = ("OK" if records else "EMPTY") + (f" [{';'.join(notes)}]" if notes else "")
    note += f" | rounds={len(block_starts)} base_cols={len(base_cols)}"
    return records, note

# ---------- main -------------------------------------------------------------
def main():
    files = sorted(glob.glob(os.path.join(DATA, "**", "*.xls"), recursive=True))
    all_recs, summary = [], []
    for f in files:
        recs, note = parse_file(f)
        all_recs.extend(recs)
        summary.append((os.path.relpath(f, ROOT), len(recs), note))

    df = pd.DataFrame(all_recs)
    out_csv = os.path.join(ROOT, "usfd_long.csv")
    df.to_csv(out_csv, index=False)

    # summary md
    lines = ["# USFD ingestion summary", "",
             f"- files parsed: **{len(files)}**",
             f"- total long rows (defect x round): **{len(df)}**",
             f"- unique defects (defect_uid): **{df['defect_uid'].nunique() if len(df) else 0}**",
             f"- rows with amplitude: **{df['amplitude_pct'].notna().sum() if len(df) else 0}**",
             f"- asset_type split: {df['asset_type'].value_counts().to_dict() if len(df) else {}}",
             "", "## per-file", "", "| file | long_rows | note |", "|---|---|---|"]
    for rel, n, note in summary:
        lines.append(f"| {rel} | {n} | {note} |")
    with open(os.path.join(ROOT, "usfd_ingest_summary.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"files={len(files)} long_rows={len(df)} "
          f"defects={df['defect_uid'].nunique() if len(df) else 0} "
          f"amp_rows={int(df['amplitude_pct'].notna().sum()) if len(df) else 0}")

if __name__ == "__main__":
    main()
