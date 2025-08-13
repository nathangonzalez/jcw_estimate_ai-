import os, re, sqlite3, datetime as dt
from io import BytesIO
from flask import Flask, jsonify, request, g, send_file, render_template_string
from openpyxl import Workbook
from utils.plan_ai import analyze_plans, revise_estimate
from pypdf import PdfReader

app = Flask(__name__)
DB_PATH = os.getenv("DB_PATH","estimates.db")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

COST_PER_SQFT = {"basic":120.0,"standard":180.0,"premium":240.0}

# ---------------- DB (sqlite, simple) ----------------
def db():
    d = getattr(g,"_db",None)
    if d is None:
        d = g._db = sqlite3.connect(DB_PATH)
        d.row_factory = sqlite3.Row
        d.executescript("""
        CREATE TABLE IF NOT EXISTS estimates(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'draft',
          subtotal REAL NOT NULL DEFAULT 0,
          currency TEXT NOT NULL DEFAULT 'USD',
          base_json TEXT,   -- first pass / current JSON blob
          assumptions TEXT, -- json list
          questions  TEXT   -- json list
        );
        CREATE TABLE IF NOT EXISTS items(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          estimate_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          scope TEXT,
          qty REAL,
          unit TEXT,
          finish TEXT,
          unit_cost REAL,
          cost REAL
        );
        """)
        d.commit()
    return d

@app.teardown_appcontext
def close_db(_):
    d = getattr(g,"_db",None)
    if d: d.close()

# ---------------- Utility ----------------
def _store_snapshot(est_json):
    """Persist items+totals to DB and return estimate_id."""
    d = db()
    cur = d.execute(
        "INSERT INTO estimates(created_at,status,subtotal,currency,base_json,assumptions,questions) VALUES(?,?,?,?,?,?,?)",
        (dt.datetime.utcnow().isoformat(),
         "draft",
         float(est_json.get("subtotal",0)),
         est_json.get("currency","USD"),
         json_dumps(est_json),
         json_dumps(est_json.get("assumptions",[])),
         json_dumps(est_json.get("questions",[]))
        )
    )
    eid = cur.lastrowid
    for it in est_json.get("items",[]):
        d.execute("INSERT INTO items(estimate_id,name,scope,qty,unit,finish,unit_cost,cost) VALUES(?,?,?,?,?,?,?,?)",
                  (eid, it.get("name"), it.get("scope"), it.get("qty"), it.get("unit"),
                   it.get("finish"), it.get("unit_cost"), it.get("total_cost")))
    d.commit()
    return eid

def json_dumps(x):
    import json
    return json.dumps(x, ensure_ascii=False)

# ---------------- Existing simple estimator (kept) ----------------
def compute_simple(rooms):
    items=[]; total=0.0
    for r in rooms:
        name=str(r.get("name","Room"))
        area=float(r.get("area_sqft",0))
        finish=str(r.get("finish","standard")).lower()
        rate=float(COST_PER_SQFT.get(finish,COST_PER_SQFT["standard"]))
        cost=round(area*rate,2); total+=cost
        items.append({"name":name,"area_sqft":area,"finish":finish,"rate":rate,"cost":cost})
    return items, round(total,2)

# ---------------- New: plans → draft → clarify ----------------
@app.post("/api/plans")
def api_plans():
    """Upload one or more files 'files[]' (pdf/jpg/png). Returns draft estimate + questions."""
    uploaded = request.files.getlist("files")
    if not uploaded: return jsonify(error="no files"),400
    files = [{"name":f.filename, "bytes": f.read()} for f in uploaded]
    draft = analyze_plans(files, OPENAI_KEY)

    eid = _store_snapshot(draft)
    draft["id"] = eid
    # status depending on if there are pending questions
    status = "needs_clarification" if draft.get("questions") else "final"
    db().execute("UPDATE estimates SET status=? WHERE id=?", (status, eid))
    db().commit()
    return jsonify(draft)

@app.post("/api/clarify/<int:eid>")
def api_clarify(eid: int):
    """POST JSON: {answers:{...}} or {instructions:'free text'} to revise the estimate."""
    d = db()
    row = d.execute("SELECT base_json FROM estimates WHERE id=?", (eid,)).fetchone()
    if not row: return jsonify(error="not found"),404
    import json
    current = json.loads(row["base_json"] or "{}")

    payload = request.get_json(silent=True) or {}
    answers = payload.get("answers") or {"instructions": payload.get("instructions","")}

    revised = revise_estimate(current, answers, OPENAI_KEY)
    # wipe and restore items
    d.execute("DELETE FROM items WHERE estimate_id=?", (eid,))
    for it in revised.get("items",[]):
        d.execute("INSERT INTO items(estimate_id,name,scope,qty,unit,finish,unit_cost,cost) VALUES(?,?,?,?,?,?,?,?)",
                  (eid, it.get("name"), it.get("scope"), it.get("qty"), it.get("unit"),
                   it.get("finish"), it.get("unit_cost"), it.get("total_cost")))
    d.execute("UPDATE estimates SET base_json=?, subtotal=?, currency=?, assumptions=?, questions=?, status=? WHERE id=?",
              (json_dumps(revised), float(revised.get("subtotal",0)), revised.get("currency","USD"),
               json_dumps(revised.get("assumptions",[])), json_dumps(revised.get("questions",[])),
               "needs_clarification" if revised.get("questions") else "final", eid))
    d.commit()
    revised["id"] = eid
    return jsonify(revised)

@app.get("/api/assumptions/<int:eid>")
def api_assumptions(eid:int):
    r = db().execute("SELECT assumptions, questions, status FROM estimates WHERE id=?", (eid,)).fetchone()
    if not r: return jsonify(error="not found"),404
    import json
    return jsonify({
        "status": r["status"],
        "assumptions": json.loads(r["assumptions"] or "[]"),
        "questions": json.loads(r["questions"] or "[]")
    })

# ---------------- Export & history kept ----------------
@app.get("/api/export/<int:eid>")
def api_export(eid:int):
    d=db()
    est=d.execute("SELECT * FROM estimates WHERE id=?", (eid,)).fetchone()
    if not est: return jsonify(error="not found"),404
    rows=d.execute("SELECT name,scope,qty,unit,finish,unit_cost,cost FROM items WHERE estimate_id=?", (eid,)).fetchall()
    wb=Workbook(); ws=wb.active; ws.title="Estimate"
    ws.append(["Estimate ID",eid]); ws.append(["Created",est["created_at"]]); ws.append([])
    ws.append(["Name","Scope","Qty","Unit","Finish","Unit Cost","Line Cost"])
    for r in rows: ws.append([r["name"],r["scope"],r["qty"],r["unit"],r["finish"],r["unit_cost"],r["cost"]])
    ws.append([]); ws.append(["Subtotal",est["subtotal"], est["currency"]])
    bio=BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(bio, as_attachment=True, download_name=f"estimate_{eid}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.get("/api/history")
def api_history():
    d=db()
    rows=d.execute("SELECT id,created_at,status,subtotal,currency FROM estimates ORDER BY id DESC LIMIT 50").fetchall()
    return jsonify([dict(r) for r in rows])

# ---------------- Simple UI at "/" (kept) ----------------
INDEX_HTML = """
<!doctype html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Estimator AI</title>
<style>
 body{font-family:system-ui,Arial,sans-serif;max-width:900px;margin:32px auto;padding:0 12px}
 pre{background:#f6f8fa;padding:12px;border-radius:8px;white-space:pre-wrap}
 .card{border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin:12px 0}
 table{width:100%;border-collapse:collapse} th,td{border-bottom:1px solid #eee;padding:8px;text-align:left}
 button{padding:8px 12px}
</style></head><body>
<h1>Estimator AI</h1>

<div class="card">
  <h3>A) Upload Plans (PDF/Images) → Draft + Questions</h3>
  <input id="files" type="file" multiple accept="application/pdf,image/*">
  <button id="plans">Analyze Plans</button>
  <pre id="plansOut"></pre>
</div>

<div class="card">
  <h3>B) Answer Questions / Send Instructions</h3>
  <textarea id="answers" style="width:100%;height:90px" placeholder='{"kitchen_finish":"premium","roof":"tile"} OR free text'></textarea><br/>
  <input id="estId" placeholder="estimate id" style="width:140px">
  <button id="clarify">Clarify</button>
  <pre id="clarifyOut"></pre>
</div>

<div class="card">
  <h3>C) Quick Manual Estimate (legacy demo)</h3>
  <textarea id="rooms" style="width:100%;height:120px;">Kitchen,150,premium
Bath,80,standard</textarea><br/>
  <button id="go">Estimate</button>
  <div id="result"></div>
</div>

<div class="card">
  <h3>History</h3>
  <ul id="hist"></ul>
</div>

<script>
async function loadHist(){
  const r = await fetch('/api/history'); const list = await r.json();
  document.getElementById('hist').innerHTML = list.map(x =>
    `<li>#${x.id} — $${x.subtotal.toFixed(2)} ${x.currency} — ${x.status}
       <a href="/api/export/${x.id}">.xlsx</a></li>`).join('');
}
document.getElementById('plans').onclick = async ()=>{
  const f = document.getElementById('files').files;
  if(!f.length){ alert('Choose file(s)'); return; }
  const fd = new FormData();
  for (const x of f) fd.append('files', x);
  const r = await fetch('/api/plans', {method:'POST', body:fd});
  const data = await r.json();
  document.getElementById('plansOut').textContent = JSON.stringify(data,null,2);
  if(data.id) document.getElementById('estId').value = data.id;
  loadHist();
};
document.getElementById('clarify').onclick = async ()=>{
  const id = document.getElementById('estId').value.trim();
  if(!id){ alert('Set estimate id'); return; }
  let payload;
  try { payload = JSON.parse(document.getElementById('answers').value); payload = {answers:payload}; }
  catch(e){ payload = {instructions: document.getElementById('answers').value}; }
  const r = await fetch('/api/clarify/'+id, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  document.getElementById('clarifyOut').textContent = JSON.stringify(await r.json(), null, 2);
  loadHist();
};
document.getElementById('go').onclick = async ()=>{
  const lines = document.getElementById('rooms').value.trim().split('\\n').filter(Boolean);
  const rooms = lines.map(l=>{const [n,a,f]=(l.split(',').map(s=>s.trim()));return {name:n,area_sqft:Number(a||0),finish:(f||'standard').toLowerCase()};});
  const r = await fetch('/api/estimate',{method:'POST',headers:{'Content-Type':'application/json'},body: JSON.stringify({rooms})});
  document.getElementById('result').textContent = JSON.stringify(await r.json(), null, 2);
  loadHist();
};
loadHist();
</script>
</body></html>
"""
import json
@app.get("/")
def index(): return render_template_string(INDEX_HTML)

# keep existing simple endpoints for backward compat
@app.post("/api/estimate")
def api_estimate_legacy():
    data = request.get_json(silent=True) or {}
    rooms = data.get("rooms", [])
    items, total = compute_simple(rooms)
    eid = _store_snapshot({"items":[{"name":i["name"],"scope":"room","qty":i["area_sqft"],"unit":"sqft","finish":i["finish"],"unit_cost":i["rate"],"total_cost":i["cost"]} for i in items],
                           "currency":"USD","subtotal":total})
    return jsonify({"currency":"USD","items":items,"subtotal":total,"id":eid})

@app.get("/healthz")
def health(): return jsonify(status="ok")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","5000")))

