from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user
from sqlalchemy import func
from models import (db, User, Product, Evidence, Transaction, Payment, Disbursement, Dispute, AuditLog, WebhookEvent, InvalidTransition,
                    audit, notify, get_setting, set_setting)
from services import policy
from services.payments import PaymentError
from services import ai

bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_only(fn):
    @wraps(fn)
    @login_required
    def inner(*a, **k):
        if current_user.role != "admin":
            abort(403)
        return fn(*a, **k)
    return inner


@bp.route("/")
@admin_only
def overview():
    q = db.session.query
    stat = {
        "users": User.query.count(), "sellers": User.query.filter_by(role="seller").count(),
        "listings": Product.query.filter(Product.status.in_(["active", "reserved"])).count(),
        "flagged": Product.query.filter_by(status="flagged").count(),
        "tx": Transaction.query.count(),
        "active": Transaction.query.filter(Transaction.status.notin_(["COMPLETED", "REFUNDED", "CANCELLED", "RESOLVED"])).count(),
        "review": Transaction.query.filter(Transaction.status.in_(["MANUAL_REVIEW", "NEEDS_MORE_EVIDENCE"])).count(),
        "disputes": Dispute.query.filter(Dispute.status != "resolved").count(),
        "held": float(q(func.coalesce(func.sum(Transaction.amount), 0)).join(Payment, Payment.transaction_id == Transaction.id)
                      .filter(Payment.status == "captured", Transaction.status.notin_(["COMPLETED", "REFUNDED"])).scalar()),
        "released": float(q(func.coalesce(func.sum(Disbursement.amount), 0)).filter(Disbursement.status == "sent").scalar()),
        "refunded": float(q(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == "refunded").scalar()),
        "high_risk": Transaction.query.filter(Transaction.risk_score >= 50).count(),
        "awaiting_ai": Transaction.query.filter(Transaction.evidence.any(Evidence.analysis_status == "unavailable")).count(),
    }
    done = Transaction.query.filter_by(status="COMPLETED").count()
    closed = Transaction.query.filter(Transaction.status.in_(["COMPLETED", "REFUNDED", "RESOLVED"])).count()
    stat["success"] = round(100 * done / closed) if closed else None
    recent = Transaction.query.order_by(Transaction.updated_at.desc()).limit(8).all()
    return render_template("admin/overview.html", s=stat, recent=recent, ai_status=ai.status(force=request.args.get("recheck") == "1"), cfg=_cfg())


def _cfg():
    c = current_app.config
    return {"min_confidence": get_setting("min_confidence", c["MIN_CONFIDENCE"]), "max_risk": get_setting("max_risk", c["MAX_RISK_SCORE"]),
            "inspection_hours": get_setting("inspection_hours", c["INSPECTION_HOURS"])}


@bp.route("/settings", methods=["POST"])
@admin_only
def settings():
    try:
        set_setting("min_confidence", max(0.5, min(0.99, float(request.form["min_confidence"]))))
        set_setting("max_risk", max(0, min(100, int(request.form["max_risk"]))))
        set_setting("inspection_hours", max(1, min(240, int(request.form["inspection_hours"]))))
    except (ValueError, KeyError):
        flash("Enter valid numbers.", "error"); return redirect(url_for("admin.overview"))
    audit(current_user.email, "update_risk_config", "settings", str(dict(request.form)))
    db.session.commit(); flash("Verification thresholds updated.", "ok")
    return redirect(url_for("admin.overview"))


@bp.route("/deadlines", methods=["POST"])
@admin_only
def deadlines():
    n = policy.check_deadlines()
    flash(f"Deadline sweep done — {n} order(s) moved to review.", "ok")
    return redirect(url_for("admin.overview"))


@bp.route("/users")
@admin_only
def users():
    return render_template("admin/users.html", rows=User.query.order_by(User.id.desc()).all())


@bp.route("/users/<int:uid>/<action>", methods=["POST"])
@admin_only
def user_action(uid, action):
    u = db.session.get(User, uid) or abort(404)
    if u.id == current_user.id:
        flash("You can't change your own account here.", "error"); return redirect(url_for("admin.users"))
    if action == "suspend": u.suspended = True
    elif action == "restore": u.suspended = False
    elif action == "verify-id": u.id_verified = True
    else: abort(400)
    audit(current_user.email, f"user_{action}", u.email); db.session.commit()
    flash(f"{u.name}: {action.replace('-', ' ')} done.", "ok")
    return redirect(url_for("admin.users"))


@bp.route("/products")
@admin_only
def products():
    rows = Product.query.filter(Product.status != "removed").order_by(Product.risk_score.desc()).all()
    return render_template("admin/products.html", rows=rows)


@bp.route("/products/<int:pid>/<action>", methods=["POST"])
@admin_only
def product_action(pid, action):
    p = db.session.get(Product, pid) or abort(404)
    if action == "approve": p.status = "active"
    elif action == "flag": p.status = "flagged"
    elif action == "remove": p.status = "removed"
    else: abort(400)
    notify(p.seller_id, "listing", f"Your listing “{p.title}” was {action}d by an admin.".replace("approved", "approved").replace("removed", "removed"), f"/product/{p.id}")
    audit(current_user.email, f"listing_{action}", p.id, p.title); db.session.commit()
    flash("Listing updated.", "ok")
    return redirect(url_for("admin.products"))


@bp.route("/orders")
@admin_only
def orders():
    q = Transaction.query
    if request.args.get("status"):
        q = q.filter_by(status=request.args["status"])
    return render_template("admin/orders.html", rows=q.order_by(Transaction.updated_at.desc()).all(), args=request.args)


@bp.route("/payments")
@admin_only
def payments():
    return render_template("admin/payments.html", rows=Payment.query.order_by(Payment.id.desc()).all(),
                           hooks=WebhookEvent.query.order_by(WebhookEvent.id.desc()).limit(15).all())


@bp.route("/fraud")
@admin_only
def fraud():
    listings = Product.query.filter(Product.risk_score >= 25, Product.status != "removed").order_by(Product.risk_score.desc()).all()
    txs = Transaction.query.filter(Transaction.risk_score >= 25).order_by(Transaction.risk_score.desc()).all()
    return render_template("admin/fraud.html", listings=listings, txs=txs)


@bp.route("/disputes")
@admin_only
def disputes():
    rows = Dispute.query.order_by(Dispute.status.asc(), Dispute.id.desc()).all()
    return render_template("admin/disputes.html", rows=rows)


@bp.route("/audit")
@admin_only
def audit_log():
    return render_template("admin/audit.html", rows=AuditLog.query.order_by(AuditLog.id.desc()).limit(300).all())


# ---------------------------------------------------------------- manual review decisions
@bp.route("/orders/<ref>/action", methods=["POST"])
@admin_only
def order_action(ref):
    tx = Transaction.query.filter_by(ref=ref).first_or_404()
    act, note = request.form.get("action"), request.form.get("note", "").strip()
    try:
        if act == "approve_release":
            if tx.status == "MANUAL_REVIEW":
                tx.transition("AWAITING_RELEASE", current_user.name, note or "approved after review")
            tx.admin_approved = True
            audit(current_user.email, "manual_approve", tx.ref, note)
            policy.release_payment(tx, current_user.name, override=True)
            flash("Payment released to the seller.", "ok")
        elif act == "refund":
            if tx.status == "DISPUTED" or tx.status == "MANUAL_REVIEW":
                pass
            policy.refund_payment(tx, current_user.name)
            for d in tx.disputes:
                if d.status != "resolved":
                    d.status, d.resolution, d.admin_note, d.resolved_at = "resolved", "refund_buyer", note, datetime.utcnow()
            db.session.commit()
            flash("Buyer refunded.", "ok")
        elif act == "manual_review":
            tx.transition("MANUAL_REVIEW", current_user.name, note or "sent to review"); audit(current_user.email, "to_review", tx.ref, note); db.session.commit()
            flash("Moved to manual review.", "ok")
        elif act == "request_evidence":
            who = request.form.get("from", "buyer")
            tx.log("review", f"Admin requested more evidence from the {who}: {note or 'please add more detail'}", current_user.name)
            notify(tx.buyer_id if who == "buyer" else tx.seller_id, "evidence", f"{tx.ref}: an admin asked for more evidence — {note or 'see the order'}", f"/orders/{tx.ref}")
            audit(current_user.email, "request_evidence", tx.ref, f"{who}: {note}"); db.session.commit()
            flash("Request sent.", "ok")
        else:
            abort(400)
    except (PaymentError, InvalidTransition) as e:
        db.session.rollback(); flash(str(e), "error")
    return redirect(url_for("orders.detail", ref=ref))


@bp.route("/disputes/<int:did>/resolve", methods=["POST"])
@admin_only
def resolve_dispute(did):
    d = db.session.get(Dispute, did) or abort(404)
    tx, res, note = d.transaction, request.form.get("resolution"), request.form.get("note", "").strip()
    if d.status == "resolved":
        flash("Already resolved.", "error"); return redirect(url_for("admin.disputes"))
    try:
        d.status, d.resolution, d.admin_note, d.resolved_at = "resolved", res, note, datetime.utcnow()
        if res == "refund_buyer":
            policy.refund_payment(tx, current_user.name)
        elif res == "release_seller":
            tx.transition("RELEASE_PENDING", current_user.name, note or "dispute resolved for seller")
            tx.admin_approved = True
            policy.release_payment(tx, current_user.name, override=True)
        elif res == "close":
            tx.transition("RESOLVED", current_user.name, note or "closed without payment action")
        else:
            abort(400)
        for u in (tx.buyer_id, tx.seller_id):
            notify(u, "dispute", f"The case on {tx.ref} was resolved: {res.replace('_', ' ')}.", f"/orders/{tx.ref}")
        audit(current_user.email, "resolve_dispute", tx.ref, f"{res}: {note}")
        db.session.commit(); flash("Dispute resolved.", "ok")
    except (PaymentError, InvalidTransition) as e:
        db.session.rollback(); flash(str(e), "error")
    return redirect(url_for("admin.disputes"))
