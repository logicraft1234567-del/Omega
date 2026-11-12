import json
from datetime import datetime, timedelta
from functools import wraps
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify, Response, current_app
from flask_login import login_required, current_user
from models import (db, Product, Transaction, Agreement, Acknowledgement, Payment, ShippingRecord, Evidence, Dispute, Review,
                    Notification, User, InvalidTransition, audit, notify, get_setting)
from services import storage, pipeline, policy, risk
from services.payments import get_provider, PaymentError
from services.util import make_challenge
from services.storage import sign_evidence

bp = Blueprint("orders", __name__)

PRE_SHIP = ("PAYMENT_CAPTURED", "SELLER_VERIFICATION", "READY_TO_SHIP", "NEEDS_MORE_EVIDENCE")
DISPUTABLE = ("SHIPPED", "BUYER_INSPECTION", "AI_VERIFICATION", "AWAITING_RELEASE", "NEEDS_MORE_EVIDENCE", "MANUAL_REVIEW")


def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def inner(*a, **k):
            if current_user.role not in roles and current_user.role != "admin":
                flash(f"That page is for {' / '.join(roles)} accounts.", "error")
                return redirect(url_for("orders.dashboard"))
            return fn(*a, **k)
        return inner
    return deco


def can_view_tx(tx):
    return current_user.is_authenticated and (current_user.role == "admin" or current_user.id in (tx.buyer_id, tx.seller_id))


def side(tx):
    return "admin" if current_user.role == "admin" else "buyer" if current_user.id == tx.buyer_id else "seller" if current_user.id == tx.seller_id else None


def load_tx(ref, sides=None):
    tx = Transaction.query.filter_by(ref=ref).first_or_404()
    if not can_view_tx(tx):
        abort(403)
    if sides and side(tx) not in sides:
        abort(403)
    return tx


def back(tx):
    return redirect(url_for("orders.detail", ref=tx.ref))


# ------------------------------------------------------------------ dashboards
@bp.route("/dashboard")
@login_required
def dashboard():
    from services import portal as P
    u = current_user
    if u.role == "admin":
        return redirect(url_for("admin.overview"))
    recent = [(t, P.next_action(t, P.side_of(u, t))) for t in P.scope(u).order_by(Transaction.updated_at.desc()).limit(5).all()]
    common = dict(tasks=P.pending_tasks(u), acct_tasks=P.account_tasks(u, current_app.config["PAYMENT_MODE"]), recent=recent,
                  total_tx=P.scope(u).count(), mode=current_app.config["PAYMENT_MODE"], pipe=P.pipeline(u))
    if u.role == "seller":
        mine = Product.query.filter(Product.seller_id == u.id, Product.status != "removed")
        return render_template("dashboard_seller.html", st=P.seller_stats(u), listings=mine.order_by(Product.id.desc()).limit(4).all(),
                               listing_count=mine.count(), live_count=mine.filter(Product.status == "active").count(), **common)
    return render_template("dashboard_buyer.html", st=P.buyer_stats(u), prog=P.buyer_progress(u), **common)


# ------------------------------------------------------------------ checkout
@bp.route("/checkout/<int:pid>", methods=["GET", "POST"])
@login_required
def checkout(pid):
    p = db.session.get(Product, pid) or abort(404)
    if request.method == "POST" and current_app.config["PAYMENT_MODE"] == "unconfigured":
        flash("Payments are not configured on this server yet, so this purchase can't be started.", "error"); return redirect(url_for("market.product", pid=pid))
    if p.status != "active":
        flash("This item is no longer available.", "error"); return redirect(url_for("market.browse"))
    if p.seller_id == current_user.id:
        flash("You can't buy your own listing.", "error"); return redirect(url_for("market.product", pid=pid))
    rep = p.risk_report or risk.score_listing(p)
    if request.method == "POST":
        if not request.form.get("agree"):
            flash("Confirm the agreement to continue.", "error")
            return render_template("checkout.html", p=p, rep=rep, deadlines=_deadlines())
        tx = Transaction(product_id=p.id, buyer_id=current_user.id, seller_id=p.seller_id, amount=p.price,
                         currency=current_app.config["CURRENCY"], challenge=make_challenge())
        db.session.add(tx); db.session.flush()
        ag = Agreement(transaction_id=tx.id, specs=p.spec_snapshot(),
                       terms={"ship_within_hours": current_app.config["SHIP_DEADLINE_HOURS"],
                              "inspection_hours": get_setting("inspection_hours", current_app.config["INSPECTION_HOURS"]),
                              "release": "Payment is released to the seller only after verification passes and the buyer (or an admin) confirms the item matches.",
                              "timeouts": "If a party goes silent, an admin reviews the case. Silence is never treated as fraud, and money is never released or refunded automatically."})
        db.session.add(ag); db.session.flush()
        db.session.add(Acknowledgement(agreement_id=ag.id, user_id=current_user.id, role="buyer", ip=request.remote_addr))
        tx.log("agreement", "Buyer accepted the agreement", current_user.name)
        tx.transition("AWAITING_PAYMENT", current_user.name)
        r0 = risk.score_transaction(tx); tx.risk_score, tx.risk_reasons = r0["score"], r0["reasons"]
        try:
            o = get_provider().create_order(tx)
        except PaymentError as e:
            db.session.rollback()
            flash(f"Payment couldn't be started: {e}", "error"); return redirect(url_for("market.product", pid=pid))
        db.session.add(Payment(transaction_id=tx.id, provider=get_provider().name, order_id=o["order_id"], amount=tx.amount,
                               currency=tx.currency, approve_url=o["approve_url"], raw=o["raw"] if isinstance(o["raw"], dict) else None))
        p.status = "reserved"
        tx.log("payment", "Payment order created", "system")
        audit(current_user.email, "checkout", tx.ref, f"${tx.amount}")
        db.session.commit()
        return redirect(o["approve_url"])
    return render_template("checkout.html", p=p, rep=rep, deadlines=_deadlines())


def _deadlines():
    return {"ship": current_app.config["SHIP_DEADLINE_HOURS"], "inspect": get_setting("inspection_hours", current_app.config["INSPECTION_HOURS"])}


# ------------------------------------------------------------------ transaction detail
@bp.route("/orders/<ref>")
@login_required
def detail(ref):
    tx = load_tx(ref)
    me = side(tx)
    from services import portal as P
    verdict = policy.evaluate_release(tx)
    cmp_ = tx.latest_result("comparison")
    ev = {e.id: sign_evidence(e.id, current_user.id) for e in tx.evidence}
    pending = any(e.analysis_status in ("queued", "running") for e in tx.evidence)
    ai_down = [dict(i, kind=e.kind) for e in tx.evidence if e.analysis_status == "unavailable" for i in (e.meta or {}).get("ai_error", [])]
    return render_template("transaction.html", tx=tx, me=me, verdict=verdict, stages=P.stage_timeline(tx), cmp=cmp_.report if cmp_ else None,
                           tokens=ev, pending=pending, ai_down=ai_down, sv=tx.latest_result("seller_video"), bv=tx.latest_result("buyer_video"),
                           review=Review.query.filter_by(transaction_id=tx.id).first())


@bp.route("/orders/<ref>/status.json")
@login_required
def status_json(ref):
    tx = load_tx(ref)
    return jsonify(status=tx.status, pending=any(e.analysis_status in ("queued", "running") for e in tx.evidence), events=len(tx.events))


@bp.route("/orders/<ref>/ack", methods=["POST"])
@login_required
def ack(ref):
    tx = load_tx(ref, ("seller",))
    if tx.agreement.acked_by("seller"):
        return back(tx)
    db.session.add(Acknowledgement(agreement_id=tx.agreement.id, user_id=current_user.id, role="seller", ip=request.remote_addr))
    tx.log("agreement", "Seller confirmed the product description and terms", current_user.name)
    pipeline.advance_if_ready_to_ship(tx)
    db.session.commit(); flash("Agreement confirmed.", "ok")
    return back(tx)


# ------------------------------------------------------------------ seller actions
def _save_evidence(tx, fs, kind, media, dispute=None):
    rel, sha, size = storage.save_upload(fs, f"evidence/{tx.ref}", media)
    ev = Evidence(transaction_id=tx.id, uploader_id=current_user.id, kind=kind, media=media, path=rel, sha256=sha, size=size,
                  dispute_id=dispute.id if dispute else None, analysis_status="queued" if media == "video" and kind.endswith("_video") else "n/a")
    db.session.add(ev); db.session.flush()
    tx.log("evidence", f"{kind.replace('_', ' ').capitalize()} uploaded ({size // 1024} KB, sha256 {sha[:10]}…)", current_user.name, {"sha256": sha})
    return ev


@bp.route("/orders/<ref>/seller-video", methods=["POST"])
@login_required
def seller_video(ref):
    tx = load_tx(ref, ("seller",))
    if tx.status == "NEEDS_MORE_EVIDENCE" and tx.needs_evidence_from == "seller":
        tx.transition("SELLER_VERIFICATION", current_user.name, "re-recording")
    if tx.status != "SELLER_VERIFICATION":
        flash("A verification video isn't needed right now.", "error"); return back(tx)
    try:
        ev = _save_evidence(tx, request.files.get("video"), "seller_video", "video")
    except storage.UploadError as e:
        flash(str(e), "error"); return back(tx)
    db.session.commit()
    pipeline.submit(ev.id)
    flash("Video uploaded. Omega is analysing it — you can leave this page.", "ok")
    return back(tx)


@bp.route("/orders/<ref>/ship", methods=["POST"])
@login_required
def ship(ref):
    tx = load_tx(ref, ("seller",))
    if tx.status != "READY_TO_SHIP":
        flash("Add tracking after your verification video is accepted and both parties confirm the agreement.", "error"); return back(tx)
    f = request.form
    if not f.get("courier", "").strip() or not f.get("tracking", "").strip():
        flash("Enter the courier and tracking number.", "error"); return back(tx)
    sr = tx.shipping or ShippingRecord(transaction_id=tx.id)
    sr.courier, sr.tracking, sr.package_id = f["courier"].strip(), f["tracking"].strip(), f.get("package_id", "").strip() or None
    sr.seal_ok_dispatch = bool(f.get("sealed")); sr.dispatched_at = datetime.utcnow()
    sr.courier_events = [{"at": sr.dispatched_at.isoformat(), "event": "Seller handed the parcel to the courier"}]
    db.session.add(sr)
    ph = request.files.get("photo")
    if ph and ph.filename:
        try:
            _save_evidence(tx, ph, "dispatch_photo", "image")
        except storage.UploadError as e:
            flash(str(e), "error"); return back(tx)
    tx.transition("SHIPPED", current_user.name, f"{sr.courier} · {sr.tracking}")
    notify(tx.buyer_id, "shipping", f"{tx.ref} has shipped via {sr.courier} ({sr.tracking}).", f"/orders/{tx.ref}")
    db.session.commit(); flash("Marked as shipped.", "ok")
    return back(tx)


# ------------------------------------------------------------------ buyer actions
@bp.route("/orders/<ref>/delivered", methods=["POST"])
@login_required
def delivered(ref):
    tx = load_tx(ref, ("buyer", "admin"))
    if tx.status != "SHIPPED":
        flash("This order isn't in transit.", "error"); return back(tx)
    sr = tx.shipping
    sr.delivered_at = datetime.utcnow(); sr.seal_ok_receipt = bool(request.form.get("sealed"))
    ev = list(sr.courier_events or []); ev.append({"at": sr.delivered_at.isoformat(), "event": "Delivered to buyer"}); sr.courier_events = ev
    ph = request.files.get("photo")
    if ph and ph.filename:
        try:
            _save_evidence(tx, ph, "delivery_photo", "image")
        except storage.UploadError as e:
            flash(str(e), "error"); return back(tx)
    hours = get_setting("inspection_hours", current_app.config["INSPECTION_HOURS"])
    tx.transition("DELIVERED", current_user.name, "delivery confirmed")
    tx.inspection_deadline = datetime.utcnow() + timedelta(hours=hours)
    tx.transition("BUYER_INSPECTION", "system", f"{hours}h inspection window")
    if not sr.seal_ok_receipt:
        tx.log("shipping", "Buyer reported the package seal was broken or missing on arrival", current_user.name)
    notify(tx.seller_id, "shipping", f"{tx.ref} was delivered. The buyer has {hours}h to inspect.", f"/orders/{tx.ref}")
    db.session.commit(); flash(f"Delivery confirmed. Record yourself opening the package within {hours} hours.", "ok")
    return back(tx)


@bp.route("/orders/<ref>/inspection", methods=["POST"])
@login_required
def inspection(ref):
    tx = load_tx(ref, ("buyer",))
    if tx.status == "NEEDS_MORE_EVIDENCE" and tx.needs_evidence_from == "buyer":
        pass
    elif tx.status != "BUYER_INSPECTION":
        flash("An inspection video isn't needed right now.", "error"); return back(tx)
    try:
        ev = _save_evidence(tx, request.files.get("video"), "buyer_video", "video")
    except storage.UploadError as e:
        flash(str(e), "error"); return back(tx)
    db.session.commit()
    pipeline.submit(ev.id)
    flash("Video uploaded. Omega is comparing it with the seller's evidence.", "ok")
    return back(tx)


@bp.route("/orders/<ref>/retry-analysis", methods=["POST"])
@login_required
def retry_analysis(ref):
    tx = load_tx(ref)
    what = pipeline.retry(tx, current_user.name)
    flash(f"Retrying the {what}…" if what else "Nothing is waiting for the AI on this order.", "ok" if what else "error")
    return back(tx)


@bp.route("/orders/<ref>/confirm", methods=["POST"])
@login_required
def confirm(ref):
    tx = load_tx(ref, ("buyer",))
    if tx.status not in ("AI_VERIFICATION", "MANUAL_REVIEW", "AWAITING_RELEASE", "NEEDS_MORE_EVIDENCE"):
        flash("You can confirm the item once your inspection video has been analysed.", "error"); return back(tx)
    tx.buyer_confirmed = True
    tx.log("agreement", "Buyer confirmed the item matches the agreement", current_user.name)
    audit(current_user.email, "buyer_confirm", tx.ref)
    db.session.commit()
    if tx.status == "AWAITING_RELEASE":
        try:
            policy.release_payment(tx, current_user.name)
            flash("Payment released to the seller. Thanks for confirming.", "ok")
        except (PaymentError, InvalidTransition) as e:
            flash(f"Confirmation saved, but payment wasn't released yet: {e}", "error")
    else:
        flash("Confirmation saved. Release still depends on Omega's verification and review.", "ok")
    return back(tx)


@bp.route("/orders/<ref>/review", methods=["POST"])
@login_required
def review(ref):
    tx = load_tx(ref, ("buyer",))
    if tx.status != "COMPLETED" or Review.query.filter_by(transaction_id=tx.id).first():
        abort(400)
    stars = max(1, min(5, int(request.form.get("stars", 5))))
    db.session.add(Review(transaction_id=tx.id, seller_id=tx.seller_id, buyer_id=tx.buyer_id, stars=stars, comment=request.form.get("comment", "")[:400]))
    db.session.commit(); flash("Review posted.", "ok")
    return back(tx)


# ------------------------------------------------------------------ disputes / refunds
@bp.route("/orders/<ref>/problem", methods=["POST"])
@login_required
def problem(ref):
    tx = load_tx(ref, ("buyer", "seller"))
    if tx.open_dispute():
        flash("There's already an open case on this order. Add evidence to it below.", "error"); return back(tx)
    if tx.status not in DISPUTABLE:
        flash("A problem can be reported once the item has shipped.", "error"); return back(tx)
    desc = request.form.get("description", "").strip()
    if len(desc) < 15:
        flash("Describe what's wrong in a sentence or two.", "error"); return back(tx)
    d = Dispute(transaction_id=tx.id, opened_by_id=current_user.id, reason=request.form.get("reason", "other"), description=desc, kind="problem", status="open")
    db.session.add(d); db.session.flush()
    for fs in request.files.getlist("files")[:5]:
        if fs and fs.filename:
            try:
                _save_evidence(tx, fs, "dispute_file", "video" if fs.filename.lower().endswith(("mp4", "mov", "webm", "m4v")) else "image", d)
            except storage.UploadError as e:
                flash(str(e), "error")
    d.ai_report = pipeline.build_case_report(tx)
    tx.transition("DISPUTED", current_user.name, d.reason)
    other = tx.seller_id if current_user.id == tx.buyer_id else tx.buyer_id
    notify(other, "dispute", f"A problem was reported on {tx.ref}. Payment is held while we review.", f"/orders/{tx.ref}")
    for a in User.query.filter_by(role="admin"):
        notify(a.id, "dispute", f"New dispute on {tx.ref}.", f"/admin/disputes")
    audit(current_user.email, "open_dispute", tx.ref, d.reason)
    db.session.commit(); flash("Case opened. Payment will not be released while it's under review.", "ok")
    return back(tx)


@bp.route("/orders/<ref>/dispute-evidence", methods=["POST"])
@login_required
def dispute_evidence(ref):
    tx = load_tx(ref, ("buyer", "seller"))
    d = tx.open_dispute() or abort(400)
    n = 0
    for fs in request.files.getlist("files")[:5]:
        if fs and fs.filename:
            try:
                _save_evidence(tx, fs, "dispute_file", "video" if fs.filename.lower().endswith(("mp4", "mov", "webm", "m4v")) else "image", d); n += 1
            except storage.UploadError as e:
                flash(str(e), "error")
    note = request.form.get("note", "").strip()
    if note:
        tx.log("dispute", f"{current_user.name}: {note[:300]}", current_user.name); n += 1
    d.ai_report = pipeline.build_case_report(tx)
    db.session.commit()
    flash("Added to the case." if n else "Nothing was added.", "ok" if n else "error")
    return back(tx)


@bp.route("/orders/<ref>/refund", methods=["POST"])
@login_required
def refund(ref):
    tx = load_tx(ref, ("buyer",))
    pre_ship = tx.status in ("PAYMENT_CAPTURED", "SELLER_VERIFICATION", "READY_TO_SHIP") or (tx.status == "NEEDS_MORE_EVIDENCE" and not tx.shipping)
    if pre_ship:
        try:  # nothing has left the seller: safe to refund immediately through the provider
            policy.refund_payment(tx, current_user.name)
            flash("Order cancelled and your payment was refunded.", "ok")
        except PaymentError as e:
            flash(str(e), "error")
        return back(tx)
    if tx.status in DISPUTABLE and not tx.open_dispute():
        d = Dispute(transaction_id=tx.id, opened_by_id=current_user.id, reason="refund_request", kind="refund", status="open",
                    description=request.form.get("description", "Buyer requested a refund.")[:600])
        db.session.add(d); d.ai_report = pipeline.build_case_report(tx)
        tx.transition("DISPUTED", current_user.name, "refund requested")
        for a in User.query.filter_by(role="admin"):
            notify(a.id, "dispute", f"Refund requested on {tx.ref}.", "/admin/disputes")
        db.session.commit(); flash("Refund requested. An admin will review the evidence.", "ok")
    else:
        flash("A refund can't be requested at this stage.", "error")
    return back(tx)


# ------------------------------------------------------------------ evidence report
def _report_data(tx):
    cmp_ = tx.latest_result("comparison")
    return {"transaction": tx.ref, "status": tx.status, "created": tx.created_at.isoformat(), "amount": str(tx.amount), "currency": tx.currency,
            "product": tx.product.spec_snapshot(), "buyer": tx.buyer.name, "seller": tx.seller.name,
            "agreement": {"specs": tx.agreement.specs, "terms": tx.agreement.terms,
                          "acknowledgements": [{"role": a.role, "user": a.user.name, "at": a.created_at.isoformat(), "ip": a.ip} for a in tx.agreement.acks]},
            "payment": {"provider": tx.payment.provider, "order": tx.payment.order_id, "capture": tx.payment.capture_id, "status": tx.payment.status} if tx.payment else None,
            "disbursement": {"payout": tx.disbursement.payout_id, "status": tx.disbursement.status, "amount": str(tx.disbursement.amount)} if tx.disbursement else None,
            "shipping": {"courier": tx.shipping.courier, "tracking": tx.shipping.tracking, "events": tx.shipping.courier_events} if tx.shipping else None,
            "evidence": [{"kind": e.kind, "sha256": e.sha256, "size": e.size, "uploaded": e.created_at.isoformat(), "by": e.uploader.name if e.uploader else None, "metadata": {k: v for k, v in (e.meta or {}).items() if k != "frames"}} for e in tx.evidence],
            "ai_comparison": cmp_.report if cmp_ else None, "risk": {"score": tx.risk_score, "reasons": tx.risk_reasons},
            "disputes": [{"reason": d.reason, "status": d.status, "resolution": d.resolution, "note": d.admin_note, "ai_case_report": d.ai_report} for d in tx.disputes],
            "timeline": [{"at": e.created_at.isoformat(), "actor": e.actor, "event": e.message} for e in tx.events]}


@bp.route("/orders/<ref>/report")
@login_required
def report(ref):
    tx = load_tx(ref)
    return render_template("report.html", tx=tx, r=_report_data(tx))


@bp.route("/orders/<ref>/report.json")
@login_required
def report_json(ref):
    tx = load_tx(ref)
    return Response(json.dumps(_report_data(tx), indent=2, default=str), mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename=omega-{tx.ref}-evidence.json"})
