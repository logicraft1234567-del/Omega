"""Payment-provider abstraction. Money movement is executed by the provider; *whether* it may move is decided by services/policy.py.

Modes
  unconfigured         default when no PayPal credentials exist: checkout is disabled and says so (nothing is simulated)
  simulated            local sandbox simulator, ONLY when PAYMENT_MODE=simulated is set explicitly (development / automated tests)
  paypal_standard      PayPal Orders v2 capture to the platform account, release via PayPal Payouts to the seller's PayPal email
  paypal_marketplace   PayPal Complete Payments: seller as payee + disbursement_mode=DELAYED, release via referenced payouts
                       (needs PayPal partner approval and seller onboarding; Nigeria is send-only, so use an eligible market/partner)
"""
import base64, time, uuid
from decimal import Decimal
import requests
from flask import current_app, url_for


class PaymentError(Exception):
    pass


def money(x):
    return f"{Decimal(x):.2f}"


class UnconfiguredProvider:
    """Used when no payment provider is configured. Every money action fails loudly; nothing is ever faked."""
    name = "unconfigured"
    MSG = "Payments are not configured on this server yet. An administrator must add PayPal credentials."

    def _no(self, *a, **k):
        raise PaymentError(self.MSG)
    create_order = capture = release = refund = _no

    def verify_webhook(self, headers, body):
        return False


class SimulatedProvider:
    name = "simulated"

    def create_order(self, tx):
        oid = "SIM-" + uuid.uuid4().hex[:12].upper()
        return {"order_id": oid, "approve_url": url_for("payments.simulate", ref=tx.ref), "raw": {"simulated": True}}

    def capture(self, order_id, tx):
        return {"capture_id": "SIMCAP-" + uuid.uuid4().hex[:10].upper(), "status": "COMPLETED", "raw": {"simulated": True}}

    def release(self, tx, amount):
        return {"payout_id": "SIMPAY-" + uuid.uuid4().hex[:10].upper(), "status": "SUCCESS", "raw": {"simulated": True}}

    def refund(self, tx, amount):
        return {"refund_id": "SIMREF-" + uuid.uuid4().hex[:10].upper(), "status": "COMPLETED", "raw": {"simulated": True}}

    def verify_webhook(self, headers, body):
        return True


class PayPalProvider:
    def __init__(self, marketplace=False):
        self.marketplace = marketplace
        self.name = "paypal_marketplace" if marketplace else "paypal_standard"
        self._tok = None

    @property
    def base(self):
        return "https://api-m.paypal.com" if current_app.config["PAYPAL_ENV"] == "live" else "https://api-m.sandbox.paypal.com"

    def _token(self):
        if self._tok and self._tok[1] > time.time() + 30:
            return self._tok[0]
        c = current_app.config
        auth = base64.b64encode(f"{c['PAYPAL_CLIENT_ID']}:{c['PAYPAL_CLIENT_SECRET']}".encode()).decode()
        r = requests.post(f"{self.base}/v1/oauth2/token", headers={"Authorization": f"Basic {auth}"},
                          data={"grant_type": "client_credentials"}, timeout=20)
        if r.status_code != 200:
            raise PaymentError(f"PayPal auth failed ({r.status_code})")
        j = r.json(); self._tok = (j["access_token"], time.time() + j.get("expires_in", 300))
        return self._tok[0]

    def _call(self, method, path, json=None, request_id=None, extra=None):
        h = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        if request_id:
            h["PayPal-Request-Id"] = request_id   # PayPal-side idempotency
        if extra:
            h.update(extra)
        r = requests.request(method, f"{self.base}{path}", json=json, headers=h, timeout=30)
        try:
            data = r.json() if r.text else {}
        except ValueError:
            data = {"raw": r.text}
        if r.status_code >= 300:
            raise PaymentError(data.get("message") or data.get("details") or f"PayPal error {r.status_code}")
        return data

    def _partner_headers(self):
        bn = current_app.config.get("PAYPAL_BN_CODE")
        return {"PayPal-Partner-Attribution-Id": bn} if bn else {}

    def create_order(self, tx):
        unit = {"reference_id": tx.ref, "custom_id": tx.ref, "description": tx.product.title[:120],
                "amount": {"currency_code": tx.currency, "value": money(tx.amount)}}
        if self.marketplace:
            if not tx.seller.paypal_merchant_id:
                raise PaymentError("Seller has not completed PayPal onboarding (merchant id missing).")
            unit["payee"] = {"merchant_id": tx.seller.paypal_merchant_id}
            unit["payment_instruction"] = {"disbursement_mode": "DELAYED"}
        body = {"intent": "CAPTURE", "purchase_units": [unit],
                "payment_source": {"paypal": {"experience_context": {
                    "brand_name": "Omega", "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING",
                    "return_url": url_for("payments.paypal_return", ref=tx.ref, _external=True),
                    "cancel_url": url_for("payments.paypal_cancel", ref=tx.ref, _external=True)}}}}
        d = self._call("POST", "/v2/checkout/orders", body, request_id=f"{tx.ref}-order", extra=self._partner_headers() if self.marketplace else None)
        link = next((l["href"] for l in d.get("links", []) if l["rel"] in ("payer-action", "approve")), None)
        if not link:
            raise PaymentError("PayPal did not return an approval link.")
        return {"order_id": d["id"], "approve_url": link, "raw": d}

    def capture(self, order_id, tx):
        d = self._call("POST", f"/v2/checkout/orders/{order_id}/capture", {}, request_id=f"{tx.ref}-capture",
                       extra=self._partner_headers() if self.marketplace else None)
        cap = d["purchase_units"][0]["payments"]["captures"][0]
        return {"capture_id": cap["id"], "status": cap["status"], "raw": d}

    def release(self, tx, amount):
        rid = f"{tx.ref}-release"   # same id on retry => PayPal returns the original result, not a second payout
        if self.marketplace:
            d = self._call("POST", "/v1/payments/referenced-payouts-items",
                           {"reference_id": tx.payment.capture_id, "reference_type": "TRANSACTION_ID"},
                           request_id=rid, extra=self._partner_headers())
            return {"payout_id": d.get("payout_item_id") or d.get("item_id") or rid, "status": d.get("item_status", "PENDING"), "raw": d}
        if not tx.seller.paypal_email:
            raise PaymentError("Seller has no PayPal email on file.")
        body = {"sender_batch_header": {"sender_batch_id": rid, "email_subject": "You have been paid on Omega"},
                "items": [{"recipient_type": "EMAIL", "receiver": tx.seller.paypal_email, "sender_item_id": tx.ref,
                           "amount": {"value": money(amount), "currency": tx.currency}, "note": f"Omega order {tx.ref}"}]}
        d = self._call("POST", "/v1/payments/payouts", body, request_id=rid)
        return {"payout_id": d["batch_header"]["payout_batch_id"], "status": d["batch_header"]["batch_status"], "raw": d}

    def refund(self, tx, amount):
        d = self._call("POST", f"/v2/payments/captures/{tx.payment.capture_id}/refund",
                       {"amount": {"value": money(amount), "currency_code": tx.currency}, "note_to_payer": f"Omega refund for {tx.ref}"},
                       request_id=f"{tx.ref}-refund")
        return {"refund_id": d["id"], "status": d.get("status"), "raw": d}

    def verify_webhook(self, headers, body):
        wid = current_app.config.get("PAYPAL_WEBHOOK_ID")
        if not wid:
            return False
        payload = {"auth_algo": headers.get("PAYPAL-AUTH-ALGO"), "cert_url": headers.get("PAYPAL-CERT-URL"),
                   "transmission_id": headers.get("PAYPAL-TRANSMISSION-ID"), "transmission_sig": headers.get("PAYPAL-TRANSMISSION-SIG"),
                   "transmission_time": headers.get("PAYPAL-TRANSMISSION-TIME"), "webhook_id": wid, "webhook_event": body}
        try:
            return self._call("POST", "/v1/notifications/verify-webhook-signature", payload).get("verification_status") == "SUCCESS"
        except PaymentError:
            return False


def get_provider():
    mode = current_app.config["PAYMENT_MODE"]
    if mode == "paypal_marketplace":
        return PayPalProvider(marketplace=True)
    if mode == "paypal_standard":
        return PayPalProvider()
    if mode == "simulated":
        return SimulatedProvider()
    return UnconfiguredProvider()
