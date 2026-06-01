#!/usr/bin/env python3
"""
Aonami Echo — generate a self-contained HTML dashboard from usfd.db.

Produces dashboard.html: a single file (data embedded as JSON, vanilla JS,
inline SVG charts — no server, no CDN, works offline). Open it in a browser.

Run:  .venv/bin/python3 generate_dashboard.py  ->  dashboard.html
"""
import os, sqlite3, json
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
con = sqlite3.connect(ROOT + "/usfd.db")

def q(sql):
    return pd.read_sql_query(sql, con)

kpis = {
    "defects": int(q("SELECT COUNT(*) n FROM triage_queue").n[0]),
    "immediate": int(q("SELECT COUNT(*) n FROM triage_queue WHERE triage_level='IMMEDIATE'").n[0]),
    "urgent": int(q("SELECT COUNT(*) n FROM triage_queue WHERE triage_level='URGENT'").n[0]),
    "near_imr": int(q("SELECT COUNT(*) n FROM triage_queue WHERE latest_amp>=80").n[0]),
    "clusters": int(q("SELECT COUNT(*) n FROM clusters").n[0]),
    "sla_breach": int(q("SELECT COUNT(*) n FROM triage_queue WHERE sla_breach=1").n[0]),
    "inspections": int(q("SELECT COUNT(*) n FROM inspections").n[0]),
}
levels = q("""SELECT triage_level lvl, COUNT(*) n FROM triage_queue
              GROUP BY triage_level""").set_index("lvl")["n"].to_dict()
levels = {k: int(levels.get(k, 0)) for k in ["IMMEDIATE","URGENT","MONITOR","ROUTINE"]}
sev = q("""SELECT severity_band b, COUNT(*) n FROM triage_queue GROUP BY severity_band""")
sev = {r.b: int(r.n) for r in sev.itertuples()}
sev = {k: sev.get(k,0) for k in [">=80 (IMR-level)","60-80","30-60","<30","unknown"]}
by_rail = q("""SELECT railway, COUNT(*) n FROM triage_queue WHERE latest_amp>=80
               GROUP BY railway ORDER BY n DESC""")
by_rail = [{"railway": r.railway.replace(" Railway",""), "n": int(r.n)} for r in by_rail.itertuples()]
queue = q("""SELECT triage_rank rank, triage_level lvl, railway, section, km, meter,
             rail, latest_amp amp, severity_band sev,
             CASE WHEN yrs_to_80>=1e8 THEN NULL ELSE ROUND(yrs_to_80,1) END yrs80, reasons
             FROM triage_queue WHERE triage_level IN ('IMMEDIATE','URGENT')
             ORDER BY triage_rank LIMIT 100""")
queue["railway"] = queue["railway"].str.replace(" Railway","",regex=False)
queue_recs = json.loads(queue.to_json(orient="records"))
clusters = q("""SELECT railway, section, line, ROUND(chainage_start_m) a, ROUND(chainage_end_m) b,
                ROUND(span_m,1) span_m, n_defects, ROUND(max_amp) max_amp
                FROM clusters ORDER BY n_defects DESC LIMIT 30""")
clusters["railway"] = clusters["railway"].str.replace(" Railway","",regex=False)
cluster_recs = json.loads(clusters.to_json(orient="records"))
con.close()

DATA = {"kpis": kpis, "levels": levels, "sev": sev, "by_rail": by_rail,
        "queue": queue_recs, "clusters": cluster_recs}

HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Aonami Echo — USFD Triage Dashboard</title>
<style>
:root{--bg:#0e1116;--card:#171c24;--ink:#e6edf3;--mut:#8b98a9;--line:#2a313c;
--imm:#e5484d;--urg:#f5a524;--mon:#3e9dd6;--rou:#3fb950;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
header{padding:20px 26px;border-bottom:1px solid var(--line)}
h1{margin:0;font-size:19px}.sub{color:var(--mut);font-size:12.5px;margin-top:3px}
.wrap{padding:22px 26px;max-width:1200px;margin:0 auto}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .v{font-size:26px;font-weight:700}.kpi .l{color:var(--mut);font-size:12px;margin-top:2px}
.kpi.alert .v{color:var(--imm)}.kpi.warn .v{color:var(--urg)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:22px}
@media(max-width:820px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.card h2{margin:0 0 12px;font-size:14px;font-weight:600}
.bar{display:flex;align-items:center;gap:8px;margin:6px 0}
.bar .lab{width:120px;color:var(--mut);font-size:12px;text-align:right;flex:none}
.bar .track{flex:1;background:#0c0f14;border-radius:5px;overflow:hidden;height:18px}
.bar .fill{height:100%;border-radius:5px}.bar .num{width:48px;font-variant-numeric:tabular-nums;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mut);font-weight:600;position:sticky;top:0;background:var(--card)}
.tbl-wrap{max-height:460px;overflow:auto;border:1px solid var(--line);border-radius:10px}
.pill{padding:1px 8px;border-radius:20px;font-size:11px;font-weight:600}
.IMMEDIATE{background:rgba(229,72,77,.18);color:var(--imm)}
.URGENT{background:rgba(245,165,36,.18);color:var(--urg)}
.amp{font-weight:700}.amp.hi{color:var(--imm)}.amp.md{color:var(--urg)}
.foot{color:var(--mut);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:12px}
</style></head><body>
<header><h1>Aonami Echo — USFD Run-on-Run Triage</h1>
<div class=sub>Defect-growth prioritisation across Indian Railways zones · generated from usfd.db</div></header>
<div class=wrap>
<div class=kpis id=kpis></div>
<div class=grid>
<div class=card><h2>Triage levels</h2><div id=levels></div></div>
<div class=card><h2>Severity bands (Annexure II-A, %FSH)</h2><div id=sev></div></div>
</div>
<div class=grid>
<div class=card><h2>Near-IMR defects (&ge;80%FSH) by railway</h2><div id=rail></div></div>
<div class=card><h2>Para 6.3.2 four-metre clusters (top)</h2>
<div class=tbl-wrap><table id=clu></table></div></div>
</div>
<div class=card><h2>Morning priority queue — IMMEDIATE &amp; URGENT</h2>
<div class=tbl-wrap><table id=queue></table></div></div>
<div class=foot id=foot></div>
</div>
<script>
const D=__DATA__;
const COL={IMMEDIATE:'var(--imm)',URGENT:'var(--urg)',MONITOR:'var(--mon)',ROUTINE:'var(--rou)'};
const SEVCOL={'>=80 (IMR-level)':'var(--imm)','60-80':'var(--urg)','30-60':'var(--mon)','<30':'var(--rou)','unknown':'#555'};
function kpi(v,l,cls){return `<div class="kpi ${cls||''}"><div class=v>${v.toLocaleString()}</div><div class=l>${l}</div></div>`}
document.getElementById('kpis').innerHTML=
 kpi(D.kpis.defects,'Defects tracked')+kpi(D.kpis.immediate,'IMMEDIATE','alert')+
 kpi(D.kpis.urgent,'URGENT','warn')+kpi(D.kpis.near_imr,'Near-IMR (&ge;80%)','alert')+
 kpi(D.kpis.clusters,'4m clusters')+kpi(D.kpis.sla_breach,'SLA breaches','alert')+
 kpi(D.kpis.inspections,'Inspections');
function bars(el,obj,colf){const max=Math.max(1,...Object.values(obj));
 document.getElementById(el).innerHTML=Object.entries(obj).map(([k,v])=>
 `<div class=bar><div class=lab>${k}</div><div class=track><div class=fill style="width:${100*v/max}%;background:${colf(k)}"></div></div><div class=num>${v.toLocaleString()}</div></div>`).join('')}
bars('levels',D.levels,k=>COL[k]);
bars('sev',D.sev,k=>SEVCOL[k]);
const railObj={};D.by_rail.forEach(r=>railObj[r.railway]=r.n);
bars('rail',railObj,()=>'var(--imm)');
document.getElementById('clu').innerHTML='<tr><th>Railway<th>Section<th>Ln<th>span m<th>#<th>maxFSH</tr>'+
 D.clusters.map(c=>`<tr><td>${c.railway}<td>${c.section}<td>${c.line}<td>${c.span_m}<td>${c.n_defects}<td>${c.max_amp}</tr>`).join('');
function ampcls(a){return a>=80?'hi':a>=60?'md':''}
document.getElementById('queue').innerHTML='<tr><th>#<th>Level<th>Railway<th>Section<th>Km+m<th>Rail<th>%FSH<th>yrs&rarr;80<th>Reasons</tr>'+
 D.queue.map(r=>`<tr><td>${r.rank}<td><span class="pill ${r.lvl}">${r.lvl}</span><td>${r.railway}<td>${r.section}<td>${r.km}+${r.meter}<td>${r.rail}<td class="amp ${ampcls(r.amp)}">${r.amp}<td>${r.yrs80??'—'}<td>${r.reasons}</tr>`).join('');
document.getElementById('foot').innerHTML=
 `Showing top ${D.queue.length} of the IMMEDIATE/URGENT queue. Rules: Annexure II-A bands · Para 6.3.2 4-metre cluster · Para 6.4 IMR SLA. `+
 `Predictions are ranking aids (per-defect forecasting limited by &le;3 noisy readings).`;
</script></body></html>"""

html = HTML.replace("__DATA__", json.dumps(DATA))
open(ROOT + "/dashboard.html", "w").write(html)
print(f"dashboard.html written ({len(html)//1024} KB) | "
      f"immediate={kpis['immediate']} urgent={kpis['urgent']} clusters={kpis['clusters']}")
