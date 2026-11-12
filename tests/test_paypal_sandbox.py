"""End-to-end test of the *real* PayPal code path (PAYMENT_MODE=paypal_standard).

Only the HTTP edge is faked: FakePayPal answers like the PayPal REST API documents it (OAuth token, Orders v2,
Payouts, capture refunds, webhook signature verification) and rejects malformed requests. Everything inside Omega
(checkout route, capture on return, policy, payout, refund, webhook handler) runs for real.
"""
import base64, itertools, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import pytest, requests
from test_flow import env, ai, login, up, run_to_delivery, Resp, FakeAI   # noqa: F401  (fixtures)
from models import db, Transaction, Disbursement, Payment, WebhookEvent, Product

CID, SECRET, WEBHOOK_ID = "sb-client-id", "sb-client-secret", "WH-TEST-1"


class FakePayPal:
    def __init__(self, ai_fake):
        self.ai, self.calls = ai_fake, []
        self.orders, self.batches, self.refunds = {}, {}, {}
        self.idem = {}
        self.n = itertools.count(1)
        self.reject_auth = False

    # requests.post -> OAuth token
    def post(self, url, headers=None, data=None, timeout=None, **kw):
        assert url == "https://api-m.sandbox.paypal.com/v1/oauth2/token", url
        want = "Basic " + base64.b64encode(f"{CID}:{SECRET}".encode()).decode()
        assert data == {"grant_type": "client_credentials"}
        if self.reject_auth or headers["Authorization"] != want:
            return Resp(401, {"error": "invalid_client"})
        return Resp(200, {"access_token": "AT-1", "expires_in": 32400, "token_type": "Bearer"})

    # requests.request -> everything else (PayPal here, Gemini/Groq delegated to the AI fake)
    def __call__(self, method, url, headers=None, timeout=None, json=None, data=None, **kw):
        if "paypal.com" not in url:
            return self.ai(method, url, headers=headers, timeout=timeout, json=json, data=data, **kw)
        assert url.startswith("https://api-m.sandbox.paypal.com/"), "sandbox host expected"
        assert headers["Authorization"] == "Bearer AT-1" and headers["Content-Type"] == "application/json"
        path = url.split("paypal.com", 1)[1]
        self.calls.append((method, path))
        rid = headers.get("PayPal-Request-Id")
        if rid and (method, path, rid) in self.idem:            # PayPal replays the original response for the same request id
            return self.idem[(method, path, rid)]
        res = self._route(method, path, json, rid)
        if rid and res.status_code < 300:
            self.idem[(method, path, rid)] = res
        return res

    def _route(self, method, path, body, rid):
        if (method, path) == ("POST", "/v2/checkout/orders"):
            assert rid, "orders must carry PayPal-Request-Id"
            assert body["intent"] == "CAPTURE"
            unit = body["purchase_units"][0]
            assert isinstance(unit["amount"]["value"], str) and len(unit["amount"]["value"].split(".")[1]) == 2
            ctx = body["payment_source"]["paypal"]["experience_context"]
            assert "/pay/return/" in ctx["return_url"] and "/pay/cancel/" in ctx["cancel_url"]
            oid = f"ORD{next(self.n)}"
            self.orders[oid] = {"unit": unit, "approved": False, "captured": None}
            return Resp(201, {"id": oid, "status": "PAYER_ACTION_REQUIRED", "links": [
                {"rel": "self", "href": f"https://api.sandbox.paypal.com/v2/checkout/orders/{oid}"},
                {"rel": "payer-action", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}]})
        if method == "POST" and path.startswith("/v2/checkout/orders/") and path.endswith("/capture"):
            oid = path.split("/")[4]
            o = self.orders.get(oid)
            if not o: return Resp(404, {"name": "RESOURCE_NOT_FOUND", "message": "order not found"})
            if not o["approved"]:
                return Resp(422, {"name": "UNPROCESSABLE_ENTITY", "message": "Order not approved by the payer"})
            o["captured"] = o["captured"] or f"CAP{next(self.n)}"
            return Resp(201, {"id": oid, "status": "COMPLETED", "purchase_units": [{
                "custom_id": o["unit"]["custom_id"],
                "payments": {"captures": [{"id": o["captured"], "status": "COMPLETED", "amount": o["unit"]["amount"]}]}}]})
        if (method, path) == ("POST", "/v1/payments/payouts"):
            h, item = body["sender_batch_header"], body["items"][0]
            assert h["sender_batch_id"] and item["recipient_type"] == "EMAIL" and "@" in item["receiver"]
            assert isinstance(item["amount"]["value"], str) and item["amount"]["currency"] == "USD"
            b = self.batches.setdefault(h["sender_batch_id"], {"id": f"PB{next(self.n)}", "receiver": item["receiver"], "value": item["amount"]["value"]})
            return Resp(201, {"batch_header": {"payout_batch_id": b["id"], "batch_status": "PENDING", "sender_batch_header": h}})
        if method == "POST" and path.startswith("/v2/payments/captures/") and path.endswith("/refund"):
            assert body["amount"]["currency_code"] == "USD"
            cid = path.split("/")[4]
            self.refunds.setdefault(cid, f"REF{next(self.n)}")
            return Resp(201, {"id": self.refunds[cid], "status": "COMPLETED"})
        if (method, path) == ("POST", "/v1/notifications/verify-webhook-signature"):
            ok = body["webhook_id"] == WEBHOOK_ID and body["transmission_sig"] == "good-signature" and body["auth_algo"] and body["cert_url"]
            return Resp(200, {"verification_status": "SUCCESS" if ok else "FAILURE"})
        raise AssertionError(f"unexpected PayPal call {method} {path}")

    def buyer_approves(self, oid):
        self.orders[oid]["approved"] = True


@pytest.fixture()
def pp(env, ai, monkeypatch):
    app, vids = env
    app.config.update(PAYMENT_MODE="paypal_standard", PAYPAL_CLIENT_ID=CID, PAYPAL_CLIENT_SECRET=SECRET,
                      PAYPAL_ENV="sandbox", PAYPAL_WEBHOOK_ID=WEBHOOK_ID, BASE_URL="http://localhost:5000")
    fake = FakePayPal(ai)
    monkeypatch.setattr(requests, "request", fake)
    monkeypatch.setattr(requests, "post", fake.post)
    return app, vids, fake


def checkout(app, fake, product=1):
    b = login(app, "buyer@omega.test")
    r = b.post(f"/checkout/{product}", data={"agree": "1"})
    assert r.status_code == 302 and r.location.startswith("https://www.sandbox.paypal.com/checkoutnow?token="), r.location
    oid = r.location.split("token=")[1]
    with app.app_context():
        tx = Transaction.query.order_by(Transaction.id.desc()).first()
        assert tx.status == "AWAITING_PAYMENT" and tx.payment.order_id == oid and tx.payment.provider == "paypal_standard"
        return b, tx.ref, oid


def test_full_sandbox_flow_capture_hold_release_once(pp, ai):
    app, v, fake = pp
    b, ref, oid = checkout(app, fake)
    # the buyer has NOT approved yet: coming back must not mark the order paid
    r = b.get(f"/pay/return/{ref}?token={oid}", follow_redirects=True)
    assert b"couldn" in r.data.lower()
    with app.app_context():
        assert Transaction.query.filter_by(ref=ref).first().status == "AWAITING_PAYMENT"
    fake.buyer_approves(oid)                                   # buyer approves in the PayPal sandbox window
    b.get(f"/pay/return/{ref}?token={oid}")                    # PayPal sends the buyer back with ?token=ORDER_ID
    b.get(f"/pay/return/{ref}?token={oid}")                    # refresh / double click: still one capture
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        assert tx.status == "SELLER_VERIFICATION" and tx.payment.status == "captured" and tx.payment.capture_id.startswith("CAP")
    assert sum(1 for o in fake.orders.values() if o["captured"]) == 1
    run_to_delivery(app, v, b, ref, v["a"])
    up(b, f"/orders/{ref}/inspection", v["a"])                 # real Gemini+Groq stand-ins compare both videos
    assert fake.batches == {}                                  # AI passing does NOT pay anyone
    b.post(f"/orders/{ref}/confirm"); b.post(f"/orders/{ref}/confirm")   # buyer confirms (twice)
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        assert tx.status == "COMPLETED" and Disbursement.query.count() == 1 and tx.product.status == "sold"
        d = tx.disbursement
        assert d.provider == "paypal_standard" and d.payout_id.startswith("PB") and d.status == "sent"
        from services import policy
        policy.release_payment(tx, "again")
        assert Disbursement.query.count() == 1
    assert len(fake.batches) == 1
    batch = next(iter(fake.batches.values()))
    assert batch["receiver"] == "seller-sandbox@omega.test" and batch["value"] == "650.00"


def test_swapped_item_is_refunded_through_paypal(pp, ai):
    app, v, fake = pp
    ai.fire_same = ai.water_same = False
    b, ref, oid = checkout(app, fake)
    fake.buyer_approves(oid); b.get(f"/pay/return/{ref}?token={oid}")
    run_to_delivery(app, v, b, ref, v["a"])
    up(b, f"/orders/{ref}/inspection", v["b"])
    with app.app_context():
        assert Transaction.query.filter_by(ref=ref).first().status == "DISPUTED"
    a = login(app, "admin@omega.test")
    a.post(f"/admin/orders/{ref}/action", data={"action": "refund", "note": "item swapped"}, follow_redirects=True)
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        assert tx.status == "REFUNDED" and tx.payment.status == "refunded" and tx.product.status == "active"
        assert Disbursement.query.count() == 0
    assert len(fake.refunds) == 1 and fake.batches == {}


def test_cancelling_at_paypal_charges_nothing_and_frees_the_listing(pp):
    app, v, fake = pp
    b, ref, oid = checkout(app, fake)
    b.get(f"/pay/cancel/{ref}")
    with app.app_context():
        assert Transaction.query.filter_by(ref=ref).first().status == "CANCELLED"
        assert db.session.get(Product, 1).status == "active"
    assert not any(p.endswith("/capture") for _, p in fake.calls)


def test_wrong_credentials_fail_safely(pp):
    app, v, fake = pp
    fake.reject_auth = True
    b = login(app, "buyer@omega.test")
    r = b.post("/checkout/1", data={"agree": "1"}, follow_redirects=True)
    assert b"PayPal auth failed (401)" in r.data
    with app.app_context():
        assert db.session.get(Product, 1).status == "active" and Payment.query.count() == 0


def test_someone_elses_return_url_cannot_capture(pp):
    app, v, fake = pp
    b, ref, oid = checkout(app, fake)
    fake.buyer_approves(oid)
    other = login(app, "seller@omega.test")
    assert other.get(f"/pay/return/{ref}?token={oid}").status_code == 403
    assert b.get(f"/pay/return/{ref}?token=ORD-FORGED").status_code == 400


def _event(eid, etype, ref, cap="CAPX"):
    return {"id": eid, "event_type": etype, "resource": {"id": cap, "custom_id": ref}}


def test_webhook_needs_a_valid_paypal_signature_and_is_idempotent(pp):
    app, v, fake = pp
    b, ref, oid = checkout(app, fake)
    c = app.test_client()
    sig = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert", "PAYPAL-TRANSMISSION-ID": "t1",
           "PAYPAL-TRANSMISSION-TIME": "2026-11-12T10:00:00Z"}
    bad = c.post("/webhooks/paypal", json=_event("WH-E1", "PAYMENT.CAPTURE.COMPLETED", ref), headers={**sig, "PAYPAL-TRANSMISSION-SIG": "forged"})
    assert bad.status_code == 400
    with app.app_context():
        assert WebhookEvent.query.count() == 0 and Transaction.query.filter_by(ref=ref).first().status == "AWAITING_PAYMENT"
    good = {**sig, "PAYPAL-TRANSMISSION-SIG": "good-signature"}
    for _ in range(3):                                          # PayPal retries: processed exactly once
        assert c.post("/webhooks/paypal", json=_event("WH-E2", "PAYMENT.CAPTURE.COMPLETED", ref, "CAPWH"), headers=good).status_code == 200
    with app.app_context():
        tx = Transaction.query.filter_by(ref=ref).first()
        assert WebhookEvent.query.count() == 1 and tx.status == "SELLER_VERIFICATION" and tx.payment.capture_id == "CAPWH"
        assert Disbursement.query.count() == 0                  # a webhook never pays anyone


def test_webhook_rejected_when_webhook_id_not_configured(pp):
    app, v, fake = pp
    app.config["PAYPAL_WEBHOOK_ID"] = ""
    r = app.test_client().post("/webhooks/paypal", json=_event("WH-E9", "PAYMENT.CAPTURE.COMPLETED", "X"))
    assert r.status_code == 400
