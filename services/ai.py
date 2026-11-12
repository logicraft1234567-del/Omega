"""AI orchestration: Fire (Gemini, video) + Water (Groq, images). Neither engine has a fallback.
When an engine can't be reached the caller gets an AIUnavailable / an "unavailable" entry with the reason.
The AI only *recommends*. services/policy.py decides whether money may move."""
import time
from flask import current_app
from .ai_base import AIUnavailable, ENGINES, VideoReading, VisualComparison, ListingImageReading  # noqa: F401 (re-exported)
from . import fire, water

_status_cache = {"at": 0, "val": None}


def status(force=False):
    """Live health of both engines (cached 45 s): {'fire': {...,'ok':bool,'reason':str}, 'water': {...}}."""
    if not force and _status_cache["val"] and time.time() - _status_cache["at"] < 45:
        return _status_cache["val"]
    val = {"fire": ENGINES["fire"] | fire.check(), "water": ENGINES["water"] | water.check()}
    _status_cache.update(at=time.time(), val=val)
    return val


def keys_configured():
    return {"fire": fire.configured(), "water": water.configured()}


def read_video(path, listing, role, challenge=None):
    """Fire reads one video. Raises AIUnavailable."""
    return fire.read_video(path, listing, role, challenge)


def compare(seller_path, buyer_path, seller_frames, buyer_frames, listing):
    """Fire compares the two videos; Water independently compares frames from them.
    Returns {'fire': dict|None, 'water': dict|None, 'unavailable': [AIUnavailable.as_dict(), ...]}."""
    out = {"fire": None, "water": None, "unavailable": []}
    for key, fn in (("fire", lambda: fire.compare_videos(seller_path, buyer_path, listing)),
                    ("water", lambda: water.compare_frames(seller_frames, buyer_frames, listing))):
        try:
            out[key] = fn()
        except AIUnavailable as e:
            current_app.logger.warning("%s compare unavailable: %s", key, e)
            out["unavailable"].append(e.as_dict())
    return out


def read_listing_images(paths, listing):
    """Water reads listing photos. Returns (reading|None, unavailable_dict|None)."""
    if not paths:
        return None, None
    try:
        return water.read_listing_images(paths, listing), None
    except AIUnavailable as e:
        current_app.logger.warning("water listing read unavailable: %s", e)
        return None, e.as_dict()
