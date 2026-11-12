"""Dashboard pages shared by buyers, sellers and admins: transactions, verification centre, payments, disputes, listings, settings, search."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_
from models import db, User, Product, Transaction, Payment, Disbursement, Dispute, Evidence
from services import portal as P, ai
from routes.orders import role_required

bp = Blueprint("portal", __name__)
PER_PAGE = 10


def _page():
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


@bp.route("/transactions")
@login_required
def transactions():
    q, f = P.apply_tx_filters(P.scope(current_user), current_user, request.args)
    pg = q.order_by(Transaction.updated_at.desc(), Transaction.id.desc()).paginate(page=_page(), per_page=PER_PAGE, error_out=False)
    rows = [(t, P.next_action(t, P.side_of(current_user, t))) for t in pg.items]
    total_all = P.scope(current_user).count()
    return render_template("portal/transactions.html", rows=rows, pg=pg, f=f, filters=P.FILTERS, q=request.args.get("q", "").strip(), total_all=total_all)


@bp.route("/verification")
@login_required
def verification():
    q = P.scope(current_user).filter(Transaction.status.notin_(P.PRE_PAYMENT))
    f = request.args.get("show", "all")
    if f == "open":
        q = q.filter(Transaction.status.notin_(P.TERMINAL))
    pg = q.order_by(Transaction.updated_at.desc(), Transaction.id.desc()).paginate(page=_page(), per_page=6, error_out=False)
    items = [(t, P.verification_view(t), P.side_of(current_user, t)) for t in pg.items]
    st = ai.status() if current_user.role == "admin" else None
    return render_template("portal/verification.html", items=items, pg=pg, show=f, ai_status=st)


@bp.route("/payments")
@login_required
def payments():
    u = current_user
    txs = P.scope(u).filter(Transaction.payment.has()).order_by(Transaction.updated_at.desc()).all()
    paid = [t for t in txs if t.payment.status in ("captured", "refunded")]
    summary = {
        "held": sum(float(t.amount) for t in txs if t.payment.status == "captured" and t.status not in ("COMPLETED", "REFUNDED")),
        "released": sum(float(t.disbursement.amount) for t in txs if t.disbursement and t.disbursement.status == "sent"),
        "refunded": sum(float(t.payment.amount or 0) for t in txs if t.payment.status == "refunded"),
        "paid_in": sum(float(t.payment.amount or 0) for t in paid if t.payment.status == "captured"),
    }
    mode = current_app.config["PAYMENT_MODE"]
    info = {"unconfigured": ("Not configured", "bad", "Checkout is disabled until an administrator adds PayPal credentials. No payment is simulated."),
            "simulated": ("Development simulator", "warn", "Payments are simulated locally. No real money moves. This mode is for development and testing only."),
            "paypal_standard": (f"PayPal ({current_app.config['PAYPAL_ENV']})", "ok", "Payments are captured through PayPal and held by Omega; payouts go to the seller's PayPal email."),
            "paypal_marketplace": (f"PayPal marketplace ({current_app.config['PAYPAL_ENV']})", "ok", "The seller is the payee with delayed disbursement.")}[mode]
    webhook_ready = bool(current_app.config.get("PAYPAL_WEBHOOK_ID")) if mode.startswith("paypal") else None
    return render_template("portal/payments.html", txs=txs, summary=summary, mode=mode, info=info, webhook_ready=webhook_ready, currency=current_app.config["CURRENCY"])


@bp.route("/disputes")
@login_required
def disputes():
    q = Dispute.query.join(Transaction, Dispute.transaction_id == Transaction.id)
    if current_user.role == "buyer":
        q = q.filter(Transaction.buyer_id == current_user.id)
    elif current_user.role == "seller":
        q = q.filter(Transaction.seller_id == current_user.id)
    f = request.args.get("show", "all")
    if f == "open":
        q = q.filter(Dispute.status != "resolved")
    elif f == "resolved":
        q = q.filter(Dispute.status == "resolved")
    pg = q.order_by(Dispute.id.desc()).paginate(page=_page(), per_page=PER_PAGE, error_out=False)
    return render_template("portal/disputes.html", pg=pg, show=f)


@bp.route("/listings")
@login_required
@role_required("seller")
def listings():
    q = Product.query.filter(Product.status != "removed")
    if current_user.role == "seller":
        q = q.filter(Product.seller_id == current_user.id)
    pg = q.order_by(Product.id.desc()).paginate(page=_page(), per_page=PER_PAGE, error_out=False)
    return render_template("portal/listings.html", pg=pg)


@bp.route("/settings")
@login_required
def settings():
    return render_template("portal/settings.html")


@bp.route("/search")
@login_required
def search():
    s = request.args.get("q", "").strip()[:80]
    tx = disputes = products = users = []
    if s:
        like = f"%{s}%"
        q, _ = P.apply_tx_filters(P.scope(current_user), current_user, {"q": s})
        tx = q.order_by(Transaction.updated_at.desc()).limit(8).all()
        dq = Dispute.query.join(Transaction, Dispute.transaction_id == Transaction.id).join(Product, Transaction.product_id == Product.id)
        if current_user.role == "buyer":
            dq = dq.filter(Transaction.buyer_id == current_user.id)
        elif current_user.role == "seller":
            dq = dq.filter(Transaction.seller_id == current_user.id)
        disputes = dq.filter(or_(Transaction.ref.ilike(like), Product.title.ilike(like), Dispute.description.ilike(like))).order_by(Dispute.id.desc()).limit(6).all()
        pq = Product.query.filter(or_(Product.title.ilike(like), Product.brand.ilike(like), Product.model.ilike(like)))
        pq = pq.filter(Product.status != "removed")
        if current_user.role != "admin":
            pq = pq.filter(or_(Product.status == "active", Product.seller_id == current_user.id))
        products = pq.order_by(Product.id.desc()).limit(8).all()
        if current_user.role == "admin":
            users = User.query.filter(or_(User.name.ilike(like), User.email.ilike(like))).limit(6).all()
    return render_template("portal/search.html", q=s, tx=tx, disputes=disputes, products=products, users=users,
                           sides={t.id: P.side_of(current_user, t) for t in tx})
