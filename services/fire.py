"""FIRE — video AI (Google Gemini). Watches the actual video files: reads the device, condition and identifiers,
checks the seller's random challenge, and compares the seller's video with the buyer's video."""
import os, re, time
import requests
from flask import current_app
from .ai_base import (AIUnavailable, VideoReading, VisualComparison, parse_model_json, schema_hint,
                      http_reason, net_reason)

ENGINE, PROVIDER, ENV = "fire", "Gemini", "GEMINI_API_KEY"
BASE = "https://generativelanguage.googleapis.com"
MIME = {"mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}
_model_cache = {}


def _key():
    return (current_app.config.get("GEMINI_API_KEY") or "").strip()


def configured():
    return bool(_key())


def _req(method, url, headers=None, timeout=60, **kw):
    if not configured():
        raise AIUnavailable(ENGINE, f"{ENV} is not set — add your Gemini key to the .env file and restart", "not_configured")
    h = {"x-goog-api-key": _key(), **(headers or {})}
    try:
        r = requests.request(method, url, headers=h, timeout=timeout, **kw)
    except requests.RequestException as e:
        raise AIUnavailable(ENGINE, net_reason(PROVIDER, "generativelanguage.googleapis.com", e), "network")
    if r.status_code >= 400:
        raise AIUnavailable(ENGINE, http_reason(PROVIDER, r.status_code, r.text, ENV), f"http_{r.status_code}")
    return r


def model():
    """GEMINI_MODEL if set; otherwise discover the newest Flash model this key can use (model names change often)."""
    want = (current_app.config.get("GEMINI_MODEL") or "").strip()
    if want:
        return want
    k = _key()
    if _model_cache.get(k):
        return _model_cache[k]
    r = _req("GET", f"{BASE}/v1beta/models?pageSize=200", timeout=20)
    names = [m["name"].split("/", 1)[1] for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
    if "gemini-flash-latest" in names:
        pick = "gemini-flash-latest"
    else:
        flash = [(float(m.group(1)), n) for n in names if (m := re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash", n))]
        if not flash:
            raise AIUnavailable(ENGINE, "this Gemini key can't see any Flash model that supports video — set GEMINI_MODEL in .env", "no_model")
        pick = max(flash)[1]
    _model_cache[k] = pick
    return pick


def check():
    """Cheap live probe used by the admin panel. Never raises."""
    try:
        m = model()
        _req("GET", f"{BASE}/v1beta/models/{m}", timeout=15)
        return {"ok": True, "model": m, "reason": ""}
    except AIUnavailable as e:
        return {"ok": False, "model": "", "reason": e.reason, "code": e.code}


# ---------------- Files API ----------------
def _upload(path):
    size = os.path.getsize(path)
    mime = MIME.get(path.rsplit(".", 1)[-1].lower(), "video/mp4")
    r = _req("POST", f"{BASE}/upload/v1beta/files", json={"file": {"display_name": os.path.basename(path)}}, headers={
        "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(size), "X-Goog-Upload-Header-Content-Type": mime})
    url = r.headers.get("X-Goog-Upload-URL")
    if not url:
        raise AIUnavailable(ENGINE, "Gemini did not accept the video upload (no upload address returned)", "upload")
    with open(path, "rb") as f:
        info = _req("POST", url, data=f, timeout=600, headers={
            "Content-Length": str(size), "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"}).json().get("file", {})
    t0 = time.time()
    while info.get("state") == "PROCESSING":
        if time.time() - t0 > 240:
            raise AIUnavailable(ENGINE, "Gemini took too long to process the video file", "timeout")
        time.sleep(2)
        info = _req("GET", f"{BASE}/v1beta/{info['name']}", timeout=30).json()
    if info.get("state") != "ACTIVE":
        raise AIUnavailable(ENGINE, f"Gemini could not process this video file (state: {info.get('state', 'unknown')}) — the format or codec may be unsupported", "bad_media")
    return {"name": info["name"], "uri": info["uri"], "mime": info.get("mimeType", mime)}


def _delete(f):
    try:
        _req("DELETE", f"{BASE}/v1beta/{f['name']}", timeout=15)
    except AIUnavailable:
        pass  # best effort; Gemini also auto-deletes uploads after 48 h


def _generate(parts, schema_cls, system):
    body = {"systemInstruction": {"parts": [{"text": system + " Reply with ONE JSON object only with these fields: " + schema_hint(schema_cls) + ". No prose, no markdown."}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json", "maxOutputTokens": 4096}}
    d = _req("POST", f"{BASE}/v1beta/models/{model()}:generateContent", json=body, timeout=240).json()
    cands = d.get("candidates") or []
    if not cands:
        why = (d.get("promptFeedback") or {}).get("blockReason", "no reason given")
        raise AIUnavailable(ENGINE, f"Gemini returned no answer (blocked: {why})", "blocked")
    text = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
    return parse_model_json(ENGINE, text, schema_cls)


def _vid(f):
    return {"file_data": {"mime_type": f["mime"], "file_uri": f["uri"]}}


# ---------------- public ----------------
def read_video(path, listing, role, challenge=None):
    prompt = (f"You are the evidence analyst for a used-electronics escrow marketplace. This is the {role}'s video of a device. "
              f"The listing claims: {listing}. "
              + (f"The seller was told to perform this random challenge during recording: '{challenge}'. Set challenge_satisfied true only if you clearly see/hear it. " if challenge else "")
              + "Report ONLY what is visibly supported. Read any serial number / IMEI / model number literally (OCR); never guess. "
                "Use empty strings when something is not visible. confidence = how well the footage supports your reading (0-1).")
    f = _upload(path)
    try:
        r = _generate([_vid(f), {"text": prompt}], VideoReading, "You are a careful, literal video evidence analyst.")
    finally:
        _delete(f)
    return r.model_dump() | {"engine": ENGINE, "engine_model": model()}


def compare_videos(seller_path, buyer_path, listing):
    prompt = (f"VIDEO 1 is the seller's pre-shipment video. VIDEO 2 is the buyer's unboxing video. Listing: {listing}. "
              "Decide whether the buyer received the SAME physical device the seller showed: compare model, colour, scratches, dents, stickers, "
              "cases, accessories, screen condition and any serial/IMEI. List concrete differences only; do not list lighting or camera differences.")
    fs, fb = _upload(seller_path), None
    try:
        fb = _upload(buyer_path)
        r = _generate([{"text": "VIDEO 1 (seller):"}, _vid(fs), {"text": "VIDEO 2 (buyer):"}, _vid(fb), {"text": prompt}],
                      VisualComparison, "You are a careful, literal video evidence analyst.")
    finally:
        _delete(fs)
        if fb:
            _delete(fb)
    return r.model_dump() | {"engine": ENGINE, "engine_model": model()}
