import os, re, sqlite3, datetime as dt
from io import BytesIO
from flask import Flask, jsonify, request, g, send_file, render_template_string
from pypdf import PdfReader  # <- needs pypdf in requirements.txt

app = Flask(__name__)
DB_PATH = os.getenv("DB_PATH","estimates.db")

# simple finish -> $/sqft table (tweak anytime)
COST_PER_SQFT = {"basic":120.0,"standard":180.0,"premium":240.0}

# ---------------- DB helpers ----------------
def get_db():
    db = getattr(g,"_db",None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.executescript("""
        CREATE TABLE IF NOT EXISTS estimates(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          subtotal REAL NOT NULL,
          currency TEXT NOT NULL DEFAULT 'USD',
          note TEXT
        );
        CREATE TABLE IF NOT EXISTS items(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          estimate_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          area_sqft REAL NOT NULL,
          finish TEXT NOT NULL,
          rate REAL NOT NULL,
          cost REAL NOT NULL
        );
        """)
        db.commit()
    return db

@app.teardown_appcontext
def close_db(_):
    db = getattr(g,"_db",None)
    if db: db.close()

# ---------------- Optional AI helpers ----------------
def ai_note(total):
    """One-sentence note using OPENAI_API_KEY if present."""
    key = os.getenv("OPENAI_API_KEY")
    if not key: return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user",
                       "content":f"Write a short friendly note for a residential construction estimate totaling ${total:.0f}. Mention finishes and site conditions may change price."}],
            max_tokens=48, temperature=0.4
        )
        return r.choices[0].message.content
    except Exception:
        return None

def ai_suggest(rooms):
    """Suggest up to 3 additional rooms using OpenAI if key present."""
    key = os.getenv("OPENAI_API_KEY")
    if not key: return []
    try:
        from openai import OpenAI
        import json
        client = OpenAI(api_key=key)
        prompt = ("Suggest up to 3 additional rooms commonly included for a residential plan. "
                  "Return JSON list of objects with name, area_sqft, finish (basic|standard|premium). "
                  f"Existing rooms: {rooms}")
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            max_tokens=160, temperature=0.3
        )
        data = json.loads(r.choices[0].message.content)
        out=[]
        for s in data:
            n=str(s.get("name","Room")).strip() or "Room"
            a=float(s.get("area_sqft",0))
            f=str(s.get("finish","standard")).lower()
            if a>0: out.append({"name":n,"area_sqft":a,"finish":f})
        return out[:3]
    except Exception:
        return []

# ---------------- Core logic ----------------
def compute_estimate(rooms):
    items=[]; total=0.0
    for r in rooms:
        name=str(r.get("name","Room"))
        area=float(r.get("area_sqft",0))
        finish=str(r.get("finish","standard")).lower()
        rate=float(COST_PER_SQFT.get(finish,COST_PER_SQFT["standard"]))
        cost=round(area*rate,2); total+=cost
        items.append({"name":name,"area_sqft":area,"finish":finish,"rate":rate,"cost":cost})
    return items, round(total,2)

def store_estimate(items, subtotal, note=None):
    db=get_db()
    cur=db.execute("INSERT INTO estimates(created_at,subtotal,note) VALUES(?,?,?)",
                   (dt.datetime.utcnow().isoformat(), float(subtotal), note))
    est_id=cur.lastrowid
    for it in items:
        db.execute("INSERT INTO items(estimate_id,name,area_sqft,finish,rate,cost) VALUES(?,?,?,?,?,?)",
                   (est_id,it["name"],it["area_sqft"],it["finish"],it["rate"],it["cost"]))
    db.commit()
    return est_id

ROOM_LINE = re.compile(
  r"(?P<name>[A-Za-z ]+)[,: ]+(?P<area>\\d+(?:\\.\\d+)?)\\s*(?:sf|sq\\s*ft|sqft)?[ ,;-]+(?P<finish>basic|standard|premium)",
  flags=re.I,
)

def extract_rooms_from_pdf(path):
    rooms=[]
    try:
        reader=PdfReader(path)
        text = "".join((p.extract_text() or "") for p in reader.pages)
        for line in text.splitlines():
            m=ROOM_LINE.search(line)
            if m:
                rooms.append({
                    "name":m.group("name").strip().title(),
                    "area_sqft":float(m.group("area")),
                    "finish":m.group("finish").lower()
                })
    except Exception:
        pass
    return rooms

# ---------------- Routes ----------------
@app.get("/healthz")
def health(): return jsonify(status="ok")

@app.post("/api/estimate")
def api_estimate():
    data = request.get_json(silent=True) or {}
    rooms = data.get("rooms", [])
    if data.get("ask_suggestions"): rooms += ai_suggest(rooms)
    items, total = compute_estimate(rooms)
    note = ai_note(total) if data.get("include_note") else None
    est_id = store_estimate(items, total, note)
    return jsonify({"id":est_id,"currency":"USD","items":items,"subtotal":total,"note":note})

@app.post("/api/upload")
def api_upload():
    f = request.files.get("file")
    if not f: return jsonify(error="no file"),400
    tmp=f"/tmp/u_{int(dt.datetime.utcnow().timestamp())}.pdf"
    f.save(tmp)
    rooms = extract_rooms_from_pdf(tmp)
    try: os.remove(tmp)
    except Exception: pass
    return jsonify({"rooms":rooms,"count":len(rooms)})

@app.get("/api/history")
def api_history():
    db=get_db()
    rows=db.execute("SELECT id,created_at,subtotal,COALESCE(LENGTH(note),0)>0 as has_note FROM estimates ORDER BY id DESC LIMIT 50").fetchall()
    return jsonify([dict(r) for r in rows])

@app.get("/api/export/<int:estimate_id>")
def api_export(estimate_id:int):
    from openpyxl import Workbook
    db=get_db()
    est=db.execute("SELECT * FROM estimates WHERE id=?",(estimate_id,)).fetchone()
    if not est: return jsonify(error="not found"),404
    items=db.execute("SELECT name,area_sqft,finish,rate,cost FROM items WHERE estimate_id=?",(estimate_id,)).fetchall()
    wb=Workbook(); ws=wb.active; ws.title="Estimate"
    ws.append(["Estimate ID",estimate_id]); ws.append(["Created",est["created_at"]]); ws.append([])
    ws.append(["Name","Area (sqft)","Finish","Rate","Cost"])
    for r in items: ws.append([r["name"],r["area_sqft"],r["finish"],r["rate"],r["cost"]])
    ws.append([]); ws.append(["Subtotal",est["subtotal"]])
    if est["note"]: ws.append(["Note",est["note"]])
    bio=BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(bio, as_attachment=True, download_name=f"estimate_{estimate_id}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------------- Simple UI at "/" ----------------
INDEX_HTML = """
<!doctype html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Estimator AI</title>
<style>
 body{font-family:system-ui,Arial,sans-serif;max-width:900px;margin:32px auto;padding:0 12px}
 pre{background:#f6f8fa;padding:12px;border-radius:8px;white-space:pre-wrap}
 .card{border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin:12px 0}
 table{width:100%;border-collapse:collapse} th,td{border-bottom:1px solid #eee;padding:8px;text-align:left}
</style>
</head><body>
<h1>Estimator AI</h1>

<div class="card">
  <h3>1) Paste Rooms</h3>
  <textarea id="rooms" style="width:100%;height:120px;">Kitchen,150,premium
Bath,80,standard</textarea><br/>
  <label><input id="note" type="checkbox"> Include AI note</label>
  <label style="margin-left:12px"><input id="suggest" type="checkbox"> Ask AI to suggest rooms</label><br/>
  <button id="go">Estimate</button>
</div>

<div class="card">
  <h3>2) Or Upload PDF (lines like "Kitchen, 150 sqft, premium")</h3>
  <input id="file" type="file" accept="application/pdf">
  <button id="upload">Parse PDF</button>
  <div id="uploadOut"></div>
</div>

<div class="card">
  <h3>Result</h3>
  <div id="table"></div>
  <pre id="out"></pre>
  <div id="export"></div>
</div>

<div class="card">
  <h3>History</h3>
  <ul id="history"></ul>
</div>

<script>
async function loadHistory(){
  const r = await fetch('/api/history'); const list = await r.json();
  document.getElementById('history').innerHTML = list.map(x =>
    `<li>#${x.id} — $${x.subtotal.toFixed(2)} — ${new Date(x.created_at).toLocaleString()} ${x.has_note?'📝':''}
      <a href="/api/export/${x.id}">Export .xlsx</a></li>`).join('');
}
function parseTextarea(){
  const lines = document.getElementById('rooms').value.trim().split('\\n').filter(Boolean);
  return lines.map(l=>{const [n,a,f]=l.split(',').map(s=>(s||'').trim()); return {name:n||'Room',area_sqft:Number(a||0),finish:(f||'standard').toLowerCase()};});
}
function renderTable(items, subtotal, note){
  const tbl = `<table><thead><tr><th>Name</th><th>Area</th><th>Finish</th><th>Rate</th><th>Cost</th></tr></thead>
    <tbody>${items.map(r=>`<tr><td>${r.name}</td><td>${r.area_sqft}</td><td>${r.finish}</td><td>${r.rate}</td><td>${r.cost}</td></tr>`).join('')}</tbody>
    <tfoot><tr><th colspan="4" style="text-align:right">Subtotal</th><th>$${subtotal.toFixed(2)}</th></tr></tfoot></table>`;
  document.getElementById('table').innerHTML = tbl + (note?`<p><strong>Note:</strong> ${note}</p>`:'');
}
document.getElementById('go').onclick = async () => {
  const rooms = parseTextarea();
  const r = await fetch('/api/estimate',{method:'POST',headers:{'Content-Type':'application/json'},
    body: JSON.stringify({rooms, include_note:document.getElementById('note').checked, ask_suggestions:document.getElementById('suggest').checked})});
  const data = await r.json(); renderTable(data.items,data.subtotal,data.note);
  document.getElementById('out').textContent = JSON.stringify(data,null,2);
  document.getElementById('export').innerHTML = `<a href="/api/export/${data.id}">Download Excel</a>`;
  loadHistory();
};
document.getElementById('upload').onclick = async () => {
  const f = document.getElementById('file').files[0]; if(!f){alert('Choose a PDF');return;}
  const fd = new FormData(); fd.append('file', f);
  const r = await fetch('/api/upload',{method:'POST',body:fd}); const data = await r.json();
  if(data.rooms?.length){ document.getElementById('rooms').value = data.rooms.map(r=>`${r.name},${r.area_sqft},${r.finish}`).join('\\n');
    document.getElementById('uploadOut').textContent = `Parsed ${data.count} rooms → pasted above.`; }
  else { document.getElementById('uploadOut').textContent = 'No rooms detected.'; }
};
loadHistory();
</script>
</body></html>
"""
@app.get("/")
def index():  # serve the full interactive UI at root
    return render_template_string(INDEX_HTML)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","5000")))
