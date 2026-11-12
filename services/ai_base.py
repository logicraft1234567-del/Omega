"""Shared pieces for the two AI engines.

  FIRE  (flame icon)    = Google Gemini  -> understands VIDEO
  WATER (droplets icon) = Groq vision    -> understands IMAGES (listing photos, extracted video frames)

There is no fallback data. If an engine can't be reached, it raises AIUnavailable with a plain-English
reason, and that reason is shown to the user / admin instead of an invented result.
"""
import json, re
from typing import Optional
from pydantic import BaseModel, Field, ValidationError

ENGINES = {
    "fire": {"name": "Fire", "kind": "video AI", "provider": "Gemini", "icon": "flame", "env": "GEMINI_API_KEY"},
    "water": {"name": "Water", "kind": "image AI", "provider": "Groq", "icon": "droplets", "env": "GROQ_API_KEY"},
}


class AIUnavailable(Exception):
    """An engine could not produce a real answer. `reason` is safe to show to users (never contains keys)."""

    def __init__(self, engine, reason, code="error"):
        super().__init__(reason)
        self.engine, self.reason, self.code = engine, reason, code

    def as_dict(self):
        e = ENGINES[self.engine]
        return {"engine": self.engine, "name": e["name"], "provider": e["provider"], "reason": self.reason, "code": self.code}

    def __str__(self):
        e = ENGINES[self.engine]
        return f"{e['name']} ({e['provider']}): {self.reason}"


# ---------------- structured outputs ----------------
class VideoReading(BaseModel):
    product_visible: bool = False
    brand: str = ""
    model: str = ""
    storage: str = ""
    colour: str = ""
    condition: str = ""                      # like new | good | fair | poor
    visible_damage: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)   # OCR: serial / IMEI / model numbers
    challenge_satisfied: Optional[bool] = None
    quality_issues: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    notes: str = ""


class VisualComparison(BaseModel):
    same_device_likely: bool = False
    differences: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    notes: str = ""


class ListingImageReading(BaseModel):
    matches_listing: bool = True
    looks_like_stock_or_render: bool = False
    issues: list[str] = Field(default_factory=list)
    confidence: float = 0.0


def schema_hint(model_cls):
    """Compact field list for the prompt (works across providers, no provider-specific schema dialect)."""
    props = model_cls.model_json_schema().get("properties", {})
    return json.dumps({k: (v.get("type") or "see description") for k, v in props.items()})


def parse_model_json(engine, text, model_cls):
    """Turn a model reply into a validated object, or say clearly that the reply was unusable."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b <= a:
        raise AIUnavailable(engine, "the model answered, but not with usable JSON (try again; if it keeps happening check the model name)", "bad_output")
    try:
        return model_cls.model_validate_json(t[a:b + 1])
    except ValidationError as e:
        raise AIUnavailable(engine, f"the model's answer didn't match the expected format ({e.error_count()} field problems)", "bad_output")


# ---------------- plain-English error reasons ----------------
def _msg(body):
    try:
        j = json.loads(body)
        m = j.get("error", j)
        m = m.get("message", m) if isinstance(m, dict) else m
        return str(m)[:220]
    except Exception:
        return (body or "")[:220]


def http_reason(provider, status, body, env):
    m = _msg(body)
    if status == 400 and re.search(r"api key|api_key", m, re.I):
        return f"{provider} rejected the API key as invalid — check {env} in your .env file ({m})"
    if status == 401:
        return f"{provider} rejected the API key (401) — it is wrong, expired or revoked; check {env} ({m})"
    if status == 403:
        return f"{provider} refused access (403) — the key may lack permission, billing may be off, or the region is blocked ({m})"
    if status == 404:
        return f"{provider} says the model or endpoint was not found (404) — set the model name in .env ({m})"
    if status == 413:
        return f"{provider} says the request was too large (413) — the file is bigger than its limit"
    if status == 429:
        return f"{provider} rate limit or quota reached (429) — wait a minute or check your plan ({m})"
    if status >= 500:
        return f"{provider} is having a server problem ({status}) — try again shortly"
    return f"{provider} rejected the request ({status}): {m}"


def net_reason(provider, host, exc):
    name = type(exc).__name__
    if "Timeout" in name:
        return f"the request to {provider} timed out — slow connection or {provider} is busy"
    if "Connection" in name or "SSL" in name:
        return f"could not connect to {host} — check your internet connection, DNS, VPN or firewall"
    return f"network error talking to {provider} ({name})"
