import os
from dotenv import load_dotenv
load_dotenv()
BASE = os.path.abspath(os.path.dirname(__file__))


def _db_url():
    url = os.getenv("DATABASE_URL")
    if not url:
        os.makedirs(os.path.join(BASE, "instance"), exist_ok=True)
        return "sqlite:///" + os.path.join(BASE, "instance", "omega.db")
    return url.replace("postgres://", "postgresql://", 1)


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = _db_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    MAX_CONTENT_LENGTH = 250 * 1024 * 1024
    UPLOAD_ROOT = os.getenv("UPLOAD_ROOT", os.path.join(BASE, "instance", "uploads"))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"

    BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")

    # --- AI: Fire = Gemini (video), Water = Groq (images). No fallback: if one can't be reached, the app says why. ---
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")      # empty = auto-pick the newest Flash model your key can use
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "")          # empty = auto-pick a current Groq vision model
    FRAMES_PER_VIDEO = int(os.getenv("FRAMES_PER_VIDEO", "6"))

    # --- Policy thresholds (admin "risk configuration" edits these at runtime via Setting table) ---
    MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.80"))
    MAX_RISK_SCORE = int(os.getenv("MAX_RISK_SCORE", "50"))
    SHIP_DEADLINE_HOURS = int(os.getenv("SHIP_DEADLINE_HOURS", "72"))
    INSPECTION_HOURS = int(os.getenv("INSPECTION_HOURS", "48"))
    PLATFORM_FEE_PCT = float(os.getenv("PLATFORM_FEE_PCT", "0"))

    # --- Payments ---
    # unconfigured (default, checkout is disabled) | paypal_standard | paypal_marketplace | simulated (explicit dev/test opt-in only)
    PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
    PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
    PAYPAL_ENV = os.getenv("PAYPAL_ENV", "sandbox")  # sandbox | live
    PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")
    PAYPAL_BN_CODE = os.getenv("PAYPAL_BN_CODE", "")  # partner attribution id (marketplace mode)
    PAYMENT_MODE = os.getenv("PAYMENT_MODE") or ("paypal_standard" if os.getenv("PAYPAL_CLIENT_ID") and os.getenv("PAYPAL_CLIENT_SECRET") else "unconfigured")
    CURRENCY = os.getenv("CURRENCY", "USD")
