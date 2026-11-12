"""The transaction decision engine. AI output is only one input. Only this module can authorise a payout."""
from datetime import datetime, timedelta
from decimal import Decimal
from flask import current_app
from models import db, get_setting, audit, notify, Disbursement, Payment, InvalidTransition
from .payments import get_provider, PaymentError


def thresholds():
    c = current_app.config
    return {"min_confidence": get_setting("min_confidence", c["MIN_CONFIDENCE"]),
            "max_risk": get_setting("max_risk", c["MAX_RISK_SCORE"])}


def evaluate_release(tx):
    """Returns {eligible, checks:[{label, ok, detail}], blockers:[...]} – rendered as the release checklist in the UI."""
    t = thresholds()
    cmp_ = tx.latest_result("comparison")
    rep = cmp_.report if cmp_ else {}
    checks = []
    def chk(label, ok, detail=""):
        checks.append({"label": label, "ok": bool(ok), "detail": detail})
    chk("Payment captured and held", tx.payment and tx.payment.status == "captured")
    chk("Buyer and seller confirmed the agreement", tx.agreement and tx.agreement.acked_by("buyer") and tx.agreement.acked_by("seller"))
    sv = tx.latest_result("seller_video")
    chk("Seller verification video analysed", sv is not None)
    chk("Parcel dispatched and delivered", tx.shipping and tx.shipping.delivered_at is not None)
    chk("Buyer inspection video analysed", tx.latest_result("buyer_video") is not None)
    chk("AI comparison recommends review for release", cmp_ and cmp_.recommendation == "ELIGIBLE_FOR_REVIEW", cmp_.recommendation if cmp_ else "No comparison yet")
    chk("No discrepancies or missing evidence", cmp_ and not rep.get("discrepancies") and not rep.get("missing_evidence"))
    chk(f"Analysis confidence ≥ {t['min_confidence']:.0%}", cmp_ and (cmp_.confidence or 0) >= t["min_confidence"], f"{(cmp_.confidence or 0):.0%}" if cmp_ else "")
    chk(f"Transaction risk score ≤ {t['max_risk']}", (tx.risk_score or 0) <= t["max_risk"], str(tx.risk_score or 0))
    chk("No open dispute", tx.open_dispute() is None)
    chk("Buyer confirmed the item matches, or an admin approved", tx.buyer_confirmed or tx.admin_approved)
    blockers = [c["label"] for c in checks if not c["ok"]]
    return {"eligible": not blockers, "checks": checks, "blockers": blockers}


def release_payment(tx, actor, override=False):
    """Idempotent. Raises PaymentError/InvalidTransition if not allowed. Never called from AI code or webhooks."""
    if tx.disbursement and tx.disbursement.status in ("sent", "pending"):
        return tx.disbursement  # already released (or in flight): never pay twice
    verdict = evaluate_release(tx)
    if not verdict["eligible"] and not override:
        raise PaymentError("Release conditions not met: " + "; ".join(verdict["blockers"]))
    if tx.status not in ("AWAITING_RELEASE", "RELEASE_PENDING"):
        raise InvalidTransition(f"Cannot release from {tx.status}")
    fee = (Decimal(tx.amount) * Decimal(str(current_app.config["PLATFORM_FEE_PCT"])) / 100).quantize(Decimal("0.01"))
    net = Decimal(tx.amount) - fee
    d = Disbursement(transaction_id=tx.id, provider=get_provider().name, amount=net, fee=fee, released_by=str(actor), status="pending")
    db.session.add(d)
    db.session.flush()  # unique(transaction_id) constraint blocks a concurrent second payout
    tx.transition("RELEASE_PROCESSING", actor, "payout requested")
    try:
        r = get_provider().release(tx, net)
    except PaymentError as e:
        d.status = "failed"; d.raw = {"error": str(e)}
        tx.transition("AWAITING_RELEASE", "system", f"payout failed: {e}")
        db.session.delete(d); db.session.commit()
        raise
    d.payout_id, d.raw, d.status = r["payout_id"], r["raw"], "sent"
    tx.transition("COMPLETED", "system", f"seller paid ({d.payout_id})")
    tx.product.status = "sold"
    audit(actor, "release_payment", tx.ref, f"override={override} payout={d.payout_id}")
    notify(tx.seller_id, "payment", f"Payment of ${net} for {tx.ref} was released to you.", f"/orders/{tx.ref}")
    notify(tx.buyer_id, "payment", f"Order {tx.ref} is complete. Payment released to the seller.", f"/orders/{tx.ref}")
    db.session.commit()
    return d


def refund_payment(tx, actor):
    if not tx.payment or tx.payment.status != "captured":
        raise PaymentError("No captured payment to refund.")
    if tx.disbursement and tx.disbursement.status == "sent":
        raise PaymentError("Payment already released to the seller; refund through the provider dispute process.")
    r = get_provider().refund(tx, tx.payment.amount)
    tx.payment.status = "refunded"
    if tx.status != "REFUND_PENDING":
        tx.transition("REFUND_PENDING", actor, "refund approved")
    tx.transition("REFUNDED", actor, f"refund {r['refund_id']}")
    tx.product.status = "active"
    audit(actor, "refund_payment", tx.ref, r["refund_id"])
    notify(tx.buyer_id, "payment", f"Your payment for {tx.ref} was refunded.", f"/orders/{tx.ref}")
    notify(tx.seller_id, "payment", f"Order {tx.ref} was refunded to the buyer.", f"/orders/{tx.ref}")
    db.session.commit()


def check_deadlines():
    """Timeout policy. Silence is never treated as fraud: late items go to a human, nothing is auto-paid or auto-refunded."""
    from models import Transaction
    n = datetime.utcnow(); moved = 0
    for tx in Transaction.query.filter(Transaction.status.in_(["READY_TO_SHIP", "SELLER_VERIFICATION", "BUYER_INSPECTION"])).all():
        if tx.status in ("READY_TO_SHIP", "SELLER_VERIFICATION") and tx.ship_deadline and tx.ship_deadline < n:
            tx.transition("MANUAL_REVIEW", "system", "shipping deadline passed"); moved += 1
            notify(tx.buyer_id, "deadline", f"The seller missed the shipping deadline on {tx.ref}. An admin will review.", f"/orders/{tx.ref}")
        elif tx.status == "BUYER_INSPECTION" and tx.inspection_deadline and tx.inspection_deadline < n:
            tx.transition("MANUAL_REVIEW", "system", "inspection window ended without buyer evidence"); moved += 1
            notify(tx.seller_id, "deadline", f"The inspection window on {tx.ref} ended. An admin will review.", f"/orders/{tx.ref}")
    db.session.commit()
    return moved
