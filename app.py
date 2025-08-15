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
