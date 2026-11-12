"""WATER — image AI (Groq vision). Looks at still images: listing photos, and frames extracted from the videos."""
import base64, io
import requests
from flask import current_app
from PIL import Image
from .ai_base import (AIUnavailable, VisualComparison, ListingImageReading, parse_model_json, schema_hint,
                      http_reason, net_reason)

ENGINE, PROVIDER, ENV = "water", "Groq", "GROQ_API_KEY"
BASE = "https://api.groq.com/openai/v1"
# Groq retired Llama 4 Scout/Maverick in 2026; these are its current vision models (newest first).
PREFERRED = ["qwen/qwen3.8-27b", "qwen/qwen3.6-27b"]
MAX_IMAGES = 3   # the smallest per-request image limit among Groq's vision models
_model_cache = {}


def _key():
    return (current_app.config.get("GROQ_API_KEY") or "").strip()


def configured():
    return bool(_key())


def _req(method, path, timeout=60, **kw):
    if not configured():
        raise AIUnavailable(ENGINE, f"{ENV} is not set — add your Groq key to the .env file and restart", "not_configured")
    try:
        r = requests.request(method, BASE + path, headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}, timeout=timeout, **kw)
    except requests.RequestException as e:
        raise AIUnavailable(ENGINE, net_reason(PROVIDER, "api.groq.com", e), "network")
    return r


def _raise(r):
    raise AIUnavailable(ENGINE, http_reason(PROVIDER, r.status_code, r.text, ENV), f"http_{r.status_code}")


def _available():
    r = _req("GET", "/models", timeout=20)
    if r.status_code != 200:
        _raise(r)
    return {m["id"] for m in r.json().get("data", [])}


def model():
    want = (current_app.config.get("GROQ_MODEL") or "").strip()
    if want:
        return want
    k = _key()
    if _model_cache.get(k):
        return _model_cache[k]
    have = _available()
    for m in PREFERRED:
        if m in have:
            _model_cache[k] = m
            return m
    raise AIUnavailable(ENGINE, "none of Groq's current vision models (" + ", ".join(PREFERRED) + ") is available on this key — set GROQ_MODEL in .env to a vision model from console.groq.com/docs/vision", "no_model")


def check():
    try:
        m = model()
        if m not in _available():
            raise AIUnavailable(ENGINE, f"the model “{m}” is not available on this Groq account (Groq retires models often) — set GROQ_MODEL in .env", "no_model")
        return {"ok": True, "model": m, "reason": ""}
    except AIUnavailable as e:
        return {"ok": False, "model": "", "reason": e.reason, "code": e.code}


# ---------------- images ----------------
def _url(img):
    buf = io.BytesIO(); img.convert("RGB").save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def image_url(path, max_side=1280):
    im = Image.open(path); im.thumbnail((max_side, max_side))
    return _url(im)


def sheet(paths, cols=2, cell=(640, 400)):
    """Tile several frames into one contact-sheet image (keeps us under Groq's per-request image limit)."""
    tiles = [Image.open(p).convert("RGB").resize(cell) for p in paths]
    rows = -(-len(tiles) // cols)
    out = Image.new("RGB", (cell[0] * cols, cell[1] * rows), (0, 0, 0))
    for i, t in enumerate(tiles):
        out.paste(t, ((i % cols) * cell[0], (i // cols) * cell[1]))
    return _url(out)


def _pick(frames, n):
    if len(frames) <= n:
        return list(frames)
    return [frames[round(k * (len(frames) - 1) / (n - 1))] for k in range(n)]


def _chat(prompt, images, schema_cls):
    m = model()
    content = [{"type": "text", "text": prompt}] + [{"type": "image_url", "image_url": {"url": u}} for u in images[:MAX_IMAGES]]
    body = {"model": m, "temperature": 0.1, "max_tokens": 2500, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": "You are a careful evidence analyst. Reply with ONE JSON object only with these fields: "
                          + schema_hint(schema_cls) + ". No prose, no markdown."},
                         {"role": "user", "content": content}]}
    r = _req("POST", "/chat/completions", timeout=90, json=body | {"reasoning_format": "hidden"})
    if r.status_code == 400 and "reasoning_format" in r.text:   # model doesn't take that option: retry plainly
        r = _req("POST", "/chat/completions", timeout=90, json=body)
    if r.status_code != 200:
        _raise(r)
    try:
        text = r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        raise AIUnavailable(ENGINE, "Groq returned an empty or malformed response", "bad_output")
    return parse_model_json(ENGINE, text, schema_cls), m


# ---------------- public ----------------
def read_listing_images(paths, listing):
    prompt = (f"Listing: {listing}. Do these photos show the item described? Do they look like a manufacturer render, "
              "stock photo or screenshot taken from another website rather than the seller's own item? List concrete issues only.")
    r, m = _chat(prompt, [image_url(p) for p in paths[:MAX_IMAGES]], ListingImageReading)
    return r.model_dump() | {"engine": ENGINE, "engine_model": m}


def compare_frames(seller_frames, buyer_frames, listing):
    prompt = (f"Image 1 is a contact sheet of frames from the seller's pre-shipment video. Image 2 is a contact sheet of frames from the buyer's "
              f"unboxing video. Listing: {listing}. Decide whether the buyer received the SAME physical device the seller showed: compare model, "
              "colour, scratches, dents, stickers, cases, accessories, screen condition and any serial/IMEI. List concrete differences only; "
              "ignore lighting and camera differences.")
    r, m = _chat(prompt, [sheet(_pick(seller_frames, 4)), sheet(_pick(buyer_frames, 4))], VisualComparison)
    return r.model_dump() | {"engine": ENGINE, "engine_model": m}
