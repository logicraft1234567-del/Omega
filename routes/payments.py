from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from models import db, Transaction, Payment, Disbursement, WebhookEvent, notify, audit
from services.payments import get_provider, PaymentError
from extensions import csrf

bp = Blueprint("payments", __name__)


def mark_captured(tx, capture_id, raw=None):
    """Idempotent: safe to call from the return URL *and* from webhooks, any number of times."""
    p = tx.payment
    if p.status == "captured":
        return False
    p.status, p.capture_id = "captured", capture_id
    if raw: p.raw = raw
    tx.transition("PAYMENT_CAPTURED", "system", f"capture {capture_id}")
    tx.ship_deadline = datetime.utcnow() + timedelta(hours=current_app.config["SHIP_DEADLINE_HOURS"])
    tx.transition("SELLER_VERIFICATION", "system", "record your verification video")
    tx.log("payment", f"Payment of ${tx.amount} captured and held by Omega", "system")
    notify(tx.seller_id, "payment", f"Payment secured for {tx.ref}. Record your verification video, then ship.", f"/orders/{tx.ref}")
    notify(tx.buyer_id, "payment", f"Your payment for {tx.ref} is secured. The seller is not paid until you've received and confirmed the item.", f"/orders/{tx.ref}")
    return True


def _tx_for_buyer(ref):
    tx = Transaction.query.filter_by(ref=ref).first_or_404()
    if tx.buyer_id != current_user.id:
        abort(403)
    return tx


@bp.route("/pay/simulate/<ref>", methods=["GET", "POST"])
@login_required
def simulate(ref):
    """Local stand-in for the PayPal sandbox approval page (used when no PayPal credentials are configured)."""
    if current_app.config["PAYMENT_MODE"] != "simulated":
        abort(404)
    tx = _tx_for_buyer(ref)
    if request.method == "POST":
        if request.form.get("action") == "cancel":
            return redirect(url_for("payments.paypal_cancel", ref=ref))
        if tx.status == "AWAITING_PAYMENT":
            cap = get_provider().capture(tx.payment.order_id, tx)
            mark_captured(tx, cap["capture_id"], cap["raw"]); db.session.commit()
        flash("Payment approved. Your money is held until the item is verified.", "ok")
        return redirect(url_for("orders.detail", ref=ref))
    return render_template("pay_simulate.html", tx=tx)


@bp.route("/pay/return/<ref>")
@login_required
def paypal_return(ref):
    tx = _tx_for_buyer(ref)
    order_id = request.args.get("token")
    if order_id != tx.payment.order_id:
        abort(400)
    if tx.status == "AWAITING_PAYMENT":
        try:
            cap = get_provider().capture(order_id, tx)
        except PaymentError as e:
            flash(f"PayPal couldn't capture the payment: {e}", "error")
            return redirect(url_for("orders.detail", ref=ref))
        mark_captured(tx, cap["capture_id"], cap["raw"]); db.session.commit()
        flash("Payment approved. Your money is held until the item is verified.", "ok")
    return redirect(url_for("orders.detail", ref=ref))


@bp.route("/pay/cancel/<ref>")
@login_required
def paypal_cancel(ref):
    tx = _tx_for_buyer(ref)
    if tx.status == "AWAITING_PAYMENT":
        tx.transition("CANCELLED", current_user.name, "payment cancelled")
        tx.product.status = "active"; db.session.commit()
    flash("Payment cancelled. You haven't been charged.", "ok")
    return redirect(url_for("market.product", pid=tx.product_id))


@bp.route("/webhooks/paypal", methods=["POST"])
@csrf.exempt
def webhook():
    """Signature-verified, idempotent. Webhooks update records; they can never trigger a payout."""
    body = request.get_json(silent=True) or {}
    eid = body.get("id")
    if not eid:
        return jsonify(error="no id"), 400
    provider = get_provider()
    ok = provider.verify_webhook(request.headers, body) if current_app.config["PAYMENT_MODE"] != "simulated" else bool(current_app.config.get("TESTING"))
    if not ok:
        return jsonify(error="signature verification failed"), 400
    try:
        db.session.add(WebhookEvent(event_id=eid, event_type=body.get("event_type"), verified=True, payload=body))
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return jsonify(status="duplicate ignored"), 200   # delivered 5 times => processed once
    res = body.get("resource", {})
    et = body.get("event_type", "")
    ref = res.get("custom_id") or (res.get("purchase_units") or [{}])[0].get("custom_id")
    tx = Transaction.query.filter_by(ref=ref).first() if ref else None
    if tx and tx.payment:
        if et == "PAYMENT.CAPTURE.COMPLETED" and tx.status == "AWAITING_PAYMENT":
            mark_captured(tx, res.get("id"), res)
        elif et in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
            tx.payment.status = "refunded"; tx.log("payment", f"Provider reported {et.split('.')[-1].lower()}", "paypal")
            if et.endswith("REVERSED") and tx.status not in ("COMPLETED", "REFUNDED") and tx.can_go("MANUAL_REVIEW"):
                tx.transition("MANUAL_REVIEW", "paypal", "capture reversed")
        elif et.startswith("PAYMENT.PAYOUTS-ITEM") and tx.disbursement:
            tx.disbursement.raw = res; tx.log("payment", f"Payout event: {et.split('.')[-1].lower()}", "paypal")
            if et.endswith("FAILED") or et.endswith("RETURNED"):
                tx.disbursement.status = "failed"
    db.session.commit()
    return jsonify(status="ok"), 200
