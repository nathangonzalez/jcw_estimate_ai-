import os
from flask import Flask, jsonify, request

app = Flask(__name__)

@app.get("/healthz")
def health():
    return jsonify(status="ok")

@app.get("/")
def index():
    return (
        "<h1>Construction Estimator</h1>"
        "<p>POST JSON to <code>/api/estimate</code> to get a rough estimate.</p>"
    )

COST_PER_SQFT = {"basic": 120.0, "standard": 180.0, "premium": 240.0}

@app.post("/api/estimate")
def estimate():
    data = request.get_json(silent=True) or {}
    rooms = data.get("rooms", [])
    total = 0.0
    items = []
    for r in rooms:
        name = r.get("name","Room")
        area = float(r.get("area_sqft",0))
        finish = str(r.get("finish","standard")).lower()
        rate = COST_PER_SQFT.get(finish, COST_PER_SQFT["standard"])
        cost = round(area*rate,2)
        items.append({"name":name,"area_sqft":area,"finish":finish,"rate":rate,"cost":cost})
        total += cost
    return jsonify({"subtotal": round(total,2), "currency":"USD", "items": items})
@app.get("/demo")
def demo():
    return """
<!doctype html>
<html>
  <head><meta name="viewport" content="width=device-width, initial-scale=1" /></head>
  <body style="font-family:sans-serif;max-width:650px;margin:40px auto;line-height:1.4">
    <h2>Estimate Demo</h2>
    <p>Enter rooms (one per line) as <code>Name, sqft, finish</code> (finish: basic|standard|premium)</p>
    <textarea id="rooms" style="width:100%;height:120px;">
Kitchen,150,premium
Bath,80,standard
</textarea>
    <p><label><input id="note" type="checkbox"> Include AI note (needs OPENAI_API_KEY)</label></p>
    <button id="go">Estimate</button>
    <pre id="out" style="white-space:pre-wrap;background:#f6f8fa;padding:12px;border-radius:8px;margin-top:14px;"></pre>
    <script>
      const go = document.getElementById('go');
      const out = document.getElementById('out');
      go.onclick = async () => {
        const lines = document.getElementById('rooms').value.trim().split('\\n').filter(Boolean);
        const rooms = lines.map(l => {
          const [name, sqft, finish] = l.split(',').map(s => s.trim());
          return { name, area_sqft: Number(sqft||0), finish: (finish||'standard').toLowerCase() };
        });
        out.textContent = 'Calculating...';
        const r = await fetch('/api/estimate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ include_note: document.getElementById('note').checked, rooms })
        });
        out.textContent = JSON.stringify(await r.json(), null, 2);
      };
    </script>
  </body>
</html>
"""
