import os, sys, tempfile, json as jsonlib
os.environ["OMEGA_NO_AUTOCREATE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np, cv2, pytest, requests
from io import BytesIO
from app import create_app
from models import db, User, Product, Transaction, Disbursement, WebhookEvent, InvalidTransition
from seed import seed


def make_video(path, hue, seconds=10, fps=15):
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (640, 480))
    for i in range(seconds * fps):
        f = np.zeros((480, 640, 3), np.uint8)
        f[:] = hue
        cv2.rectangle(f, (100 + i, 120), (360 + i, 400), (240, 240, 240), -1)
        cv2.circle(f, (500, 120 + (i % 80)), 40, (30, 30, 30), -1)
        w.write(f)
    w.release()


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._b, self.headers = status, body, headers or {}
        self.text = jsonlib.dumps(body) if body is not None else ""
    def json(self): return self._b


READING = {"product_visible": True, "brand": "Apple", "model": "iPhone 15 Pro", "storage": "256GB", "colour": "Natural Titanium",
           "condition": "good", "visible_damage": [], "identifiers": ["F2LXK1234567"], "challenge_satisfied": True, "confidence": 0.93}


class FakeAI:
    """Stands in for the two HTTP APIs only (Google Gemini = Fire, Groq = Water). Everything else in Omega runs for real."""
    def __init__(self):
        self.fire_same = self.water_same = True
        self.fire_err = self.water_err = None          # (status, message) or "net"
        self.groq_models = ["qwen/qwen3.8-27b", "llama-3.3-70b-versatile"]
        self.calls, self.uploads, self.deleted = [], 0, 0

    def __call__(self, method, url, headers=None, timeout=None, json=None, data=None, **kw):
        self.calls.append((method, url))
        if "generativelanguage.googleapis.com" in url:
            assert headers["x-goog-api-key"] == "gk"
            return self.gemini(method, url, headers, json)
        if "api.groq.com" in url:
            assert headers["Authorization"] == "Bearer qk"
            return self.groq(method, url, json)
        raise AssertionError("unexpected network call to " + url)

    @staticmethod
    def _err(e):
        if e == "net":
            raise requests.ConnectionError("getaddrinfo failed")
        return Resp(e[0], {"error": {"message": e[1]}})

    def gemini(self, method, url, headers, body):
        if self.fire_err: return self._err(self.fire_err)
        if method == "GET" and "/v1beta/models?" in url:
            return Resp(200, {"models": [{"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]}]})
        if method == "GET" and "/v1beta/models/" in url: return Resp(200, {"name": "models/gemini-flash-latest"})
        if method == "POST" and url.endswith("/upload/v1beta/files"):
            return Resp(200, {}, {"X-Goog-Upload-URL": "https://generativelanguage.googleapis.com/upload/session/1"})
        if method == "POST" and "/upload/session/" in url:
            self.uploads += 1
            return Resp(200, {"file": {"name": f"files/v{self.uploads}", "uri": f"https://generativelanguage.googleapis.com/v1beta/files/v{self.uploads}", "state": "ACTIVE", "mimeType": "video/mp4"}})
        if method == "DELETE": self.deleted += 1; return Resp(200, {})
        if ":generateContent" in url:
            parts = body["contents"][0]["parts"]
            assert sum("file_data" in p for p in parts) in (1, 2)           # real video files are attached
            assert body["generationConfig"]["responseMimeType"] == "application/json"
            two = sum("file_data" in p for p in parts) == 2
            out = ({"same_device_likely": self.fire_same, "differences": [] if self.fire_same else ["Seller phone has a cracked corner; buyer phone does not"], "confidence": 0.94}
                   if two else READING)
            return Resp(200, {"candidates": [{"content": {"parts": [{"text": jsonlib.dumps(out)}]}}]})
        raise AssertionError((method, url))

    def groq(self, method, url, body):
        if self.water_err: return self._err(self.water_err)
        if url.endswith("/models"): return Resp(200, {"data": [{"id": m} for m in self.groq_models]})
        if url.endswith("/chat/completions"):
            imgs = [c for c in body["messages"][1]["content"] if c["type"] == "image_url"]
            assert 1 <= len(imgs) <= 3 and body["response_format"] == {"type": "json_object"}
            sysmsg = body["messages"][0]["content"]
            if "same_device_likely" in sysmsg:
                out = {"same_device_likely": self.water_same, "differences": [] if self.water_same else ["Different case and a scratch on the screen"], "confidence": 0.91}
            else:
                out = {"matches_listing": True, "looks_like_stock_or_render": False, "issues": [], "confidence": 0.9}
            return Resp(200, {"choices": [{"message": {"content": jsonlib.dumps(out)}}]})
        raise AssertionError(url)


@pytest.fixture()
def ai(monkeypatch):
    from services import fire, water, ai as ai_mod
    fire._model_cache.clear(); water._model_cache.clear(); ai_mod._status_cache.update(at=0, val=None)
    fake = FakeAI()
    monkeypatch.setattr(requests, "request", fake)
    return fake


@pytest.fixture()
def env(tmp_path, ai):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "SYNC_ANALYSIS": True, "RATELIMIT_ENABLED": False,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/t.db", "UPLOAD_ROOT": str(tmp_path / "up"),
                      "PAYMENT_MODE": "simulated", "GEMINI_API_KEY": "gk", "GROQ_API_KEY": "qk", "GEMINI_MODEL": "", "GROQ_MODEL": "",
                      "SHOW_DEV_LINKS": False})
    with app.app_context():
        db.drop_all(); db.create_all(); seed()
    vids = {}
    for name, col in (("a", (90, 50, 20)), ("b", (20, 40, 160))):
        p = str(tmp_path / f"{name}.mp4"); make_video(p, col); vids[name] = p
    return app, vids


def login(app, email):
    c = app.test_client()
    assert c.post("/login", data={"email": email, "password": "omega123"}).status_code == 302
    return c


def up(c, url, path, field="video"):
    with open(path, "rb") as f:
        return c.post(url, data={field: (BytesIO(f.read()), os.path.basename(path))}, content_type="multipart/form-data", follow_redirects=True)


def buy(app):
    b = login(app, "buyer@omega.test")
    r = b.post("/checkout/1", data={"agree": "1"})
    assert r.status_code == 302 and "/pay/simulate/" in r.location
    ref = r.location.rsplit("/", 1)[1]
    b.post(r.location, data={"action": "approve"})
    return b, ref


def run_to_delivery(app, vids, buyer, ref, seller_vid):
    s = login(app, "seller@omega.test")
    s.post(f"/orders/{ref}/ack")
    r = up(s, f"/orders/{ref}/seller-video", seller_vid)
    with app.app_context():
        assert Transaction.query.filter_by(ref=ref).first().status == "READY_TO_SHIP", r.get_data(as_text=True)[:500]
    s.post(f"/orders/{ref}/ship", data={"courier": "DHL", "tracking": "123", "sealed": "1"})
    buyer.post(f"/orders/{ref}/delivered", data={"sealed": "1"})
    return s


def test_public_pages(env):
    app, _ = env
    c = app.test_client()
    for u in ["/", "/browse", "/product/1", "/login", "/signup", "/seller/2", "/healthz"]:
        r = c.get(u); assert r.status_code == 200, u
    assert b"data-hero" in c.get("/").data


def test_happy_path_pays_exactly_once(env, ai):
    app, v = env
    b, ref = buy(app)
    s = run_to_delivery(app, v, b, ref, v["a"])
    up(b, f"/orders/{ref}/inspection", v["a"])
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        assert tx.status == "AWAITING_RELEASE", (tx.status, [e.message for e in tx.events])
        c = tx.latest_result("comparison")
        assert c.recommendation == "ELIGIBLE_FOR_REVIEW" and c.engine == "fire+water"
        assert c.report["engines"]["fire"]["ok"] and c.report["engines"]["water"]["ok"]
    assert ai.uploads == 4 and ai.deleted == 4        # seller read (1) + buyer read (1) + compare (2): videos really sent to Fire, then deleted
    assert any(u.endswith("/chat/completions") for _, u in ai.calls)   # Water really looked at the frames
    # seller cannot release; AI result alone does not pay
    assert s.post(f"/orders/{ref}/confirm").status_code == 403
    b.post(f"/orders/{ref}/confirm")
    b.post(f"/orders/{ref}/confirm")  # double click
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        assert tx.status == "COMPLETED" and Disbursement.query.count() == 1
        from services import policy
        policy.release_payment(tx, "again")  # idempotent
        assert Disbursement.query.count() == 1
        assert tx.product.status == "sold"
    for u in (f"/orders/{ref}", f"/orders/{ref}/report", f"/orders/{ref}/report.json"):
        assert b.get(u).status_code == 200


def test_swapped_item_is_held_and_disputed(env, ai):
    app, v = env
    ai.fire_same = ai.water_same = False
    b, ref = buy(app)
    run_to_delivery(app, v, b, ref, v["a"])
    up(b, f"/orders/{ref}/inspection", v["b"])
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        assert tx.status == "DISPUTED" and tx.disputes and tx.disputes[0].ai_report["what_does_not_match"]
        from services import policy
        from services.payments import PaymentError
        with pytest.raises(PaymentError): policy.release_payment(tx, "x")
    b.post(f"/orders/{ref}/confirm")  # buyer can't force release while disputed
    with app.app_context():
        assert Disbursement.query.count() == 0
    a = login(app, "admin@omega.test")
    assert a.get(f"/orders/{ref}").status_code == 200
    with app.app_context():
        did = Transaction.query.filter_by(ref=ref).first().disputes[0].id
    a.post(f"/admin/disputes/{did}/resolve", data={"resolution": "refund_buyer", "note": "swapped"})
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        assert tx.status == "REFUNDED" and tx.payment.status == "refunded" and tx.product.status == "active"


def test_state_machine_blocks_jumps(env):
    app, _ = env
    b, ref = buy(app)
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        with pytest.raises(InvalidTransition): tx.transition("COMPLETED")
    s = login(app, "seller@omega.test")
    assert s.post(f"/orders/{ref}/ship", data={"courier": "x", "tracking": "y"}, follow_redirects=True).status_code == 200
    with app.app_context():
        assert Transaction.query.filter_by(ref=ref).first().status == "SELLER_VERIFICATION"


def test_webhook_idempotent_and_never_pays(env):
    app, _ = env
    c = app.test_client()
    ev = {"id": "WH-1", "event_type": "PAYMENT.CAPTURE.REFUNDED", "resource": {"id": "CAP", "custom_id": "NOPE"}}
    for _ in range(5):
        assert c.post("/webhooks/paypal", json=ev).status_code == 200
    with app.app_context():
        assert WebhookEvent.query.count() == 1 and Disbursement.query.count() == 0


def test_access_control(env):
    app, _ = env
    b, ref = buy(app)
    other = app.test_client()
    with app.app_context():
        u = User(email="x@x.io", name="X Y", role="buyer", password_hash=User.query.first().password_hash, email_verified=True)
        db.session.add(u); db.session.commit()
    o = login(app, "x@x.io")
    assert o.get(f"/orders/{ref}").status_code == 403
    assert o.get("/admin/").status_code == 403
    assert other.get(f"/orders/{ref}").status_code == 302  # anon -> login
    assert b.get("/admin/").status_code == 403


def test_risk_flags_cheap_offplatform_listing(env):
    app, _ = env
    with app.app_context():
        from services import risk
        p = Product.query.get(1)
        p.price = 90; p.description = "Text me on whatsapp +234 801 234 5678"; p.serial = ""
        rep = risk.score_listing(p)
        assert rep["level"] == "high" and any("off-platform" in r["label"] for r in rep["reasons"])


def test_engines_disagree_goes_to_a_human_not_a_dispute(env, ai):
    app, v = env
    ai.water_same = False                      # Fire says same device, Water says different
    b, ref = buy(app)
    run_to_delivery(app, v, b, ref, v["a"])
    up(b, f"/orders/{ref}/inspection", v["b"])
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        c = tx.latest_result("comparison")
        assert tx.status == "MANUAL_REVIEW" and c.recommendation == "UNCERTAIN" and not tx.disputes
        assert any("disagree" in n for n in c.report["notes"])
        assert Disbursement.query.count() == 0


def test_no_keys_says_exactly_why_and_invents_nothing(env, ai):
    app, v = env
    app.config["GEMINI_API_KEY"] = ""; app.config["GROQ_API_KEY"] = ""
    b, ref = buy(app)
    s = login(app, "seller@omega.test")
    s.post(f"/orders/{ref}/ack")
    page = up(s, f"/orders/{ref}/seller-video", v["a"]).get_data(as_text=True)
    assert "GEMINI_API_KEY is not set" in page and "Retry analysis" in page
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        assert tx.status == "SELLER_VERIFICATION" and tx.latest_result("seller_video") is None   # no made-up reading
        assert tx.latest_evidence("seller_video").analysis_status == "unavailable"
        assert any("GEMINI_API_KEY is not set" in e.message for e in tx.events)
    assert ai.calls == []                      # and it never pretended to call anything
    a = login(app, "admin@omega.test")
    ov = a.get("/admin/").get_data(as_text=True)
    assert "GEMINI_API_KEY is not set" in ov and "GROQ_API_KEY is not set" in ov and "Fire" in ov and "Water" in ov
    h = app.test_client().get("/healthz?live=1").get_json()
    assert h["fire"]["ok"] is False and "GEMINI_API_KEY" in h["fire"]["reason"] and h["water"]["ok"] is False


@pytest.mark.parametrize("err,expect", [((401, "Invalid API Key"), "rejected the API key (401)"),
                                        ((429, "Quota exceeded"), "rate limit or quota reached (429)"),
                                        ((400, "API key not valid. Please pass a valid API key."), "rejected the API key as invalid"),
                                        ((503, "overloaded"), "server problem (503)"),
                                        ("net", "could not connect to generativelanguage.googleapis.com")])
def test_fire_failure_reasons_and_retry(env, ai, err, expect):
    app, v = env
    ai.fire_err = err
    b, ref = buy(app)
    s = login(app, "seller@omega.test")
    s.post(f"/orders/{ref}/ack")
    page = up(s, f"/orders/{ref}/seller-video", v["a"]).get_data(as_text=True)
    assert expect in page
    with app.app_context():
        assert Transaction.query.filter_by(ref=ref).first().status == "SELLER_VERIFICATION"
    ai.fire_err = None                          # problem fixed -> retry finishes the job for real
    assert s.post(f"/orders/{ref}/retry-analysis", follow_redirects=True).status_code == 200
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        assert tx.status == "READY_TO_SHIP" and tx.latest_result("seller_video").engine == "fire"


def test_water_down_blocks_comparison_until_retry(env, ai):
    app, v = env
    b, ref = buy(app)
    run_to_delivery(app, v, b, ref, v["a"])
    ai.water_err = (401, "Invalid API Key")
    page = up(b, f"/orders/{ref}/inspection", v["a"]).get_data(as_text=True)
    assert "Water" in page and "rejected the API key (401)" in page
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        c = tx.latest_result("comparison")
        assert tx.status == "AI_VERIFICATION" and c.recommendation == "AI_UNAVAILABLE"
        assert [u["engine"] for u in c.report["unavailable"]] == ["water"] and not tx.disputes and Disbursement.query.count() == 0
        from services import policy
        from services.payments import PaymentError
        with pytest.raises(PaymentError): policy.release_payment(tx, "x")
    b.post(f"/orders/{ref}/confirm")            # buyer can't release on a missing AI result
    with app.app_context():
        assert Disbursement.query.count() == 0
    ai.water_err = None
    b.post(f"/orders/{ref}/retry-analysis")
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        assert tx.status == "AWAITING_RELEASE" and tx.latest_result("comparison").recommendation == "ELIGIBLE_FOR_REVIEW"


def test_groq_model_discovery_and_retired_models(env, ai):
    from services import water
    app, _ = env
    with app.app_context():
        assert water.model() == "qwen/qwen3.8-27b"           # picked from the live model list
        water._model_cache.clear(); ai.groq_models = ["llama-3.3-70b-versatile"]
        from services.ai_base import AIUnavailable
        with pytest.raises(AIUnavailable, match="none of Groq's current vision models"): water.model()


def test_listing_photo_check_reports_when_water_is_down(env, ai):
    from PIL import Image
    app, _ = env
    ai.water_err = (429, "Rate limit reached")
    s = login(app, "seller@omega.test")
    buf = BytesIO(); Image.new("RGB", (400, 300), (30, 90, 160)).save(buf, "PNG"); buf.seek(0)
    r = s.post("/sell/new", data={"title": "iPhone 12", "model": "iPhone 12", "price": "300", "category": "Phones", "brand": "Apple",
                                  "condition": "Good", "serial": "ABC123456789", "description": "Works well, battery 90 percent, box and cable included.",
                                  "images": (buf, "p.png")}, content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        p = Product.query.filter_by(title="iPhone 12").first()
        assert any("Photo check by Water" in m and "rate limit or quota reached (429)" in m for m in p.risk_report["missing"])
    ai.water_err = None
    buf2 = BytesIO(); Image.new("RGB", (400, 300), (90, 30, 160)).save(buf2, "PNG"); buf2.seek(0)
    s.post("/sell/new", data={"title": "iPhone 11", "model": "iPhone 11", "price": "250", "category": "Phones", "brand": "Apple",
                              "condition": "Good", "serial": "XYZ123456789", "description": "Works well, battery 90 percent, box and cable included.",
                              "images": (buf2, "q.png")}, content_type="multipart/form-data", follow_redirects=True)
    with app.app_context():
        p = Product.query.filter_by(title="iPhone 11").first()
        assert not any("Water" in m for m in p.risk_report["missing"])
