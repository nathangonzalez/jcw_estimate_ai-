import io, json, re
from typing import List, Dict, Any
from pypdf import PdfReader
from openai import OpenAI

def _extract_pdf_text_and_images(file_bytes: bytes) -> Dict[str, Any]:
    """Return text and (some) embedded images as bytes arrays."""
    out_text = []
    images = []  # list of (bytes, mime)
    try:
        r = PdfReader(io.BytesIO(file_bytes))
        for page in r.pages:
            t = page.extract_text() or ""
            if t.strip():
                out_text.append(t)

            # Try to pull image XObjects if present
            try:
                xobjs = page.images
                for img in xobjs:
                    data = img.data
                    mime = "image/jpeg" if img.name.lower().endswith(("jpg","jpeg")) else "image/png"
                    images.append((data, mime))
            except Exception:
                pass
    except Exception:
        # Not a PDF? Treat as image-only upload
        images.append((file_bytes, "image/png"))
    return {"text": "\n".join(out_text), "images": images}

SYSTEM_PRIMER = """You are a senior construction cost estimator.
Given residential plans (text + images), produce a DRAFT estimate, assumptions and clarification questions.
- Extract rooms/scopes (kitchen, baths, flooring, roofing, etc.)
- Estimate quantities (sqft, LF, count) and finish tier when indicated.
- If dimensions are missing, infer with clearly stated assumptions and confidence.
- Prefer US units. Keep pricing realistic but conservative for a rough order of magnitude.
Return JSON ONLY with keys: items, assumptions, questions, currency, subtotal.
items[]: {name, scope, qty, unit, finish?, unit_cost, total_cost, notes?}
assumptions[]: {topic, assumption, confidence (0-1)}
questions[]: strings to ask the client to firm up pricing (max 5).
"""

def analyze_plans(files: List[Dict[str, bytes]], openai_api_key: str) -> Dict[str, Any]:
    """files: list of dicts {'name': 'plan.pdf', 'bytes': b'...'}"""
    client = OpenAI(api_key=openai_api_key) if openai_api_key else None
    if not client:
        # Fallback: simple placeholder from text-only extraction
        all_text = []
        for f in files:
            payload = _extract_pdf_text_and_images(f["bytes"])
            if payload["text"]:
                all_text.append(payload["text"])
        text = "\n".join(all_text)[:5000]
        return {
            "items": [{"name":"General Scope","scope":"Rough estimate","qty":1,"unit":"ls","unit_cost":50000,"total_cost":50000,"notes":"No AI key set; placeholder."}],
            "assumptions":[{"topic":"Global","assumption":"No finishes or quantities in plans","confidence":0.3}],
            "questions":["Provide total heated sqft","Provide finish level for kitchen and baths"],
            "currency":"USD","subtotal":50000
        }

    # Build a vision message: text + up to 4 images (budget-friendly)
    messages = [{"role":"system","content":SYSTEM_PRIMER}]
    content = []
    # Merge text from all files
    all_text = []
    for f in files:
        payload = _extract_pdf_text_and_images(f["bytes"])
        if payload["text"]:
            all_text.append(f"FILE: {f['name']}\n{payload['text']}")
        for img_bytes, mime in payload["images"][:2]:  # cap per file for cost
            content.append({"type":"input_image","image": {"bytes": img_bytes, "mime_type": mime}})
    if all_text:
        content.insert(0, {"type":"text","text":"\n\n".join(all_text)[:24000]})
    messages.append({"role":"user","content":content})

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.2,
        response_format={"type":"json_object"},
        max_tokens=1200
    )
    j = json.loads(resp.choices[0].message.content)
    # sanity
    items = j.get("items", [])
    subtotal = sum(float(i.get("total_cost", 0)) for i in items)
    j["subtotal"] = float(j.get("subtotal", subtotal))
    j["currency"] = j.get("currency","USD")
    return j

def revise_estimate(current: Dict[str, Any], answers: Dict[str, Any], openai_api_key: str) -> Dict[str, Any]:
    client = OpenAI(api_key=openai_api_key) if openai_api_key else None
    if not client:
        return current  # no-op
    prompt = {
        "role":"user",
        "content":[
            {"type":"text","text":(
                "Refine the estimate using the client's answers. "
                "Maintain JSON schema from before (items, assumptions, questions, currency, subtotal). "
                f"\n\nCURRENT:\n{json.dumps(current)}\n\nANSWERS:\n{json.dumps(answers)}"
            )}
        ]
    }
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"system","content":SYSTEM_PRIMER}, prompt],
        temperature=0.2,
        response_format={"type":"json_object"},
        max_tokens=1000
    )
    j = json.loads(resp.choices[0].message.content)
    items = j.get("items", [])
    subtotal = sum(float(i.get("total_cost", 0)) for i in items)
    j["subtotal"] = float(j.get("subtotal", subtotal))
    j["currency"] = j.get("currency","USD")
    return j
