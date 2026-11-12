"""Read-only view helpers for the dashboards. Everything here is derived from real database records; nothing is invented."""
from datetime import datetime
from sqlalchemy import or_
from models import (db, Transaction, Payment, Disbursement, Dispute, Evidence, Product, STATUS_LABELS, MAIN_FLOW)
from . import policy

TERMINAL = ("COMPLETED", "REFUNDED", "CANCELLED", "RESOLVED")
PRE_PAYMENT = ("CREATED", "AWAITING_PAYMENT", "CANCELLED")
ACTIVE_FILTER = ("PAYMENT_CAPTURED", "SELLER_VERIFICATION", "NEEDS_MORE_EVIDENCE", "READY_TO_SHIP", "SHIPPED", "DELIVERED", "BUYER_INSPECTION",
                 "AI_VERIFICATION", "AWAITING_RELEASE", "RELEASE_PROCESSING", "MANUAL_REVIEW", "RELEASE_PENDING", "REFUND_PENDING", "DISPUTED")

FILTERS = {
    "all": ("All", None),
    "action": ("Needs my action", None),          # computed in Python (depends on who is looking)
    "active": ("In progress", ACTIVE_FILTER),
    "unpaid": ("Awaiting payment", ("CREATED", "AWAITING_PAYMENT")),
    "disputed": ("Disputed", ("DISPUTED", "MANUAL_REVIEW")),
    "completed": ("Completed", ("COMPLETED",)),
    "closed": ("Refunded / cancelled", ("REFUNDED", "CANCELLED", "RESOLVED")),
}

# Who is responsible for each stage of the main flow (a property of the process, not data).
STAGE_OWNER = {"AWAITING_PAYMENT": "Buyer", "PAYMENT_CAPTURED": "Omega", "SELLER_VERIFICATION": "Seller", "READY_TO_SHIP": "Seller",
               "SHIPPED": "Seller", "DELIVERED": "Buyer", "BUYER_INSPECTION": "Buyer", "AI_VERIFICATION": "Omega",
               "AWAITING_RELEASE": "Buyer", "COMPLETED": "Omega"}


def side_of(user, tx):
    if user.role == "admin":
        return "admin"
    return "buyer" if user.id == tx.buyer_id else "seller" if user.id == tx.seller_id else None


def scope(user):
    """Transactions the user is allowed to see."""
    q = Transaction.query
    if user.role == "admin":
        return q
    if user.role == "seller":
        return q.filter(Transaction.seller_id == user.id)
    return q.filter(Transaction.buyer_id == user.id)


def next_action(tx, side):
    """What happens next on this transaction and who has to do it. `mine` is True when the viewer must act."""
    s, other = tx.status, ("seller" if side == "buyer" else "buyer")
    def A(label, who, deadline=None):
        return {"label": label, "who": who, "mine": who == side, "deadline": deadline}
    if s in ("CREATED", "AWAITING_PAYMENT"):
        return A("Complete the payment" if side == "buyer" else "Waiting for the buyer to pay", "buyer")
    if s in ("PAYMENT_CAPTURED", "SELLER_VERIFICATION"):
        if tx.agreement and not tx.agreement.acked_by("seller"):
            return A("Confirm the listing details and record the verification video" if side == "seller" else "Waiting for the seller to confirm and film the item", "seller", tx.ship_deadline)
        return A("Record the verification video" if side == "seller" else "Waiting for the seller's verification video", "seller", tx.ship_deadline)
    if s == "NEEDS_MORE_EVIDENCE":
        who = tx.needs_evidence_from or "seller"
        return A("Upload the evidence that was requested" if who == side else f"Waiting for the {who}'s additional evidence", who)
    if s == "READY_TO_SHIP":
        return A("Add the courier and tracking number" if side == "seller" else "Waiting for the seller to ship", "seller", tx.ship_deadline)
    if s == "SHIPPED":
        return A("Confirm delivery when the parcel arrives" if side == "buyer" else "Parcel is in transit, waiting for the buyer to confirm delivery", "buyer")
    if s in ("DELIVERED", "BUYER_INSPECTION"):
        return A("Film the unboxing video" if side == "buyer" else "Waiting for the buyer's unboxing video", "buyer", tx.inspection_deadline)
    if s == "AI_VERIFICATION":
        return A("Evidence is being analysed", "omega")
    if s == "AWAITING_RELEASE":
        if tx.buyer_confirmed or tx.admin_approved:
            return A("Payment release is being processed", "omega")
        return A("Confirm the item matches the agreement" if side == "buyer" else "Waiting for the buyer to confirm the item", "buyer")
    if s in ("MANUAL_REVIEW", "REFUND_PENDING", "RELEASE_PENDING"):
        return A("Waiting for an Omega admin to review", "admin")
    if s == "DISPUTED":
        return A("A dispute is open and under admin review", "admin")
    if s == "RELEASE_PROCESSING":
        return A("Payout is being processed", "omega")
    return {"label": None, "who": None, "mine": False, "deadline": None}


def stage_timeline(tx):
    """Main-flow stages with state, owner, the real timestamp the stage was reached (from the event log) and a deadline only where one exists."""
    reached = {}
    for e in tx.events:
        if e.kind == "status" and " → " in e.message:
            new = e.message.split(" → ", 1)[1].split(" · ", 1)[0]
            reached.setdefault(new, e.created_at)
    idx = tx.flow_index()
    done_all = tx.status == "COMPLETED"
    rows = []
    for i, s in enumerate(MAIN_FLOW):
        state = "done" if done_all or (idx is not None and i < idx) else "current" if idx is not None and i == idx else "upcoming"
        deadline = tx.ship_deadline if s in ("SELLER_VERIFICATION", "READY_TO_SHIP") else tx.inspection_deadline if s == "BUYER_INSPECTION" else None
        rows.append({"key": s, "label": STATUS_LABELS[s], "state": state, "owner": STAGE_OWNER[s], "at": reached.get(STATUS_LABELS[s]),
                     "deadline": deadline if state != "done" else None})
    return rows


def _ev_state(tx, kind):
    e = tx.latest_evidence(kind)
    if not e:
        return {"submitted": False, "status": "Not submitted", "evidence": None}
    label = {"queued": "Queued for analysis", "running": "Analysis running", "done": "Analysed", "failed": "Analysis failed",
             "unavailable": "AI unreachable, waiting", "n/a": "Stored"}.get(e.analysis_status, e.analysis_status)
    return {"submitted": True, "status": label, "evidence": e, "analysis": e.analysis_status}


def verification_view(tx):
    """The honest state of verification for one transaction. AI output is reported as-is; backend authorisation is shown separately."""
    cmp_ = tx.latest_result("comparison")
    rep = (cmp_.report or {}) if cmp_ else {}
    verdict = policy.evaluate_release(tx)
    unavailable = [i for e in tx.evidence if e.analysis_status == "unavailable" for i in (e.meta or {}).get("ai_error", [])]
    human = ("Admin review in progress" if tx.status in ("MANUAL_REVIEW", "REFUND_PENDING", "RELEASE_PENDING", "DISPUTED")
             else "Additional evidence requested" if tx.status == "NEEDS_MORE_EVIDENCE"
             else "An admin approved the release" if tx.admin_approved else "Not needed so far")
    return {"seller": _ev_state(tx, "seller_video"), "buyer": _ev_state(tx, "buyer_video"),
            "analysing": any(e.analysis_status in ("queued", "running") for e in tx.evidence),
            "comparison": cmp_, "report": rep, "discrepancies": rep.get("discrepancies") or [], "missing": rep.get("missing_evidence") or [],
            "unavailable": unavailable, "human": human, "verdict": verdict}


def buyer_stats(user):
    q = Transaction.query.filter(Transaction.buyer_id == user.id)
    txs = q.all()
    return {
        "purchases": sum(1 for t in txs if t.status not in PRE_PAYMENT),
        "active": sum(1 for t in txs if t.payment and t.payment.status == "captured" and t.status not in TERMINAL),
        "completed": sum(1 for t in txs if t.status == "COMPLETED"),
        "disputes": Dispute.query.join(Transaction, Dispute.transaction_id == Transaction.id)
                           .filter(Transaction.buyer_id == user.id, Dispute.status != "resolved").count(),
        "held": sum(float(t.amount) for t in txs if t.payment and t.payment.status == "captured" and t.status not in ("COMPLETED", "REFUNDED")),
    }


def seller_stats(user, side="seller"):
    txs = Transaction.query.filter(Transaction.seller_id == user.id).all()
    paid = [t for t in txs if t.disbursement and t.disbursement.status == "sent"]
    return {
        "sales": sum(1 for t in txs if t.status == "COMPLETED"),
        "action": sum(1 for t in txs if next_action(t, "seller")["mine"] and t.status not in PRE_PAYMENT),
        "awaiting": sum(float(t.amount) for t in txs if t.payment and t.payment.status == "captured" and t.status not in ("COMPLETED", "REFUNDED")),
        "awaiting_n": sum(1 for t in txs if t.payment and t.payment.status == "captured" and t.status not in ("COMPLETED", "REFUNDED")),
        "paid_total": sum(float(t.disbursement.amount) for t in paid),
        "paid_n": len(paid),
        "disputes": Dispute.query.join(Transaction, Dispute.transaction_id == Transaction.id)
                           .filter(Transaction.seller_id == user.id, Dispute.status != "resolved").count(),
    }


def pending_tasks(user, limit=6):
    """Real tasks for this user: transactions waiting on them, soonest deadline first."""
    rows = []
    for t in scope(user).filter(Transaction.status.notin_(TERMINAL)).all():
        a = next_action(t, side_of(user, t))
        if a["mine"] and t.status not in ("CREATED",):
            rows.append((t, a))
    rows.sort(key=lambda r: (r[1]["deadline"] is None, r[1]["deadline"] or datetime.max, -r[0].id))
    return rows[:limit]


def account_tasks(user, payment_mode):
    """Account-level tasks that are true right now."""
    out = []
    if user.role == "seller":
        if not user.paypal_email and payment_mode in ("paypal_standard", "simulated"):
            out.append({"icon": "wallet", "text": "Add the PayPal email where your payouts should go.", "cta": "Add PayPal email", "ep": "market.profile"})
        flagged = Product.query.filter_by(seller_id=user.id, status="flagged").count()
        if flagged:
            out.append({"icon": "shield-alert", "text": f"{flagged} listing{'s are' if flagged != 1 else ' is'} waiting for an admin review.", "cta": "View listings", "ep": "portal.listings"})
    return out


def apply_tx_filters(q, user, args):
    """Search and status filters for the transaction list. Returns (query, active_filter_key)."""
    from sqlalchemy.orm import aliased
    from models import User
    s = (args.get("q") or "").strip()
    if s:
        like = f"%{s}%"
        Buyer, Seller = aliased(User), aliased(User)
        q = (q.join(Product, Transaction.product_id == Product.id).join(Buyer, Buyer.id == Transaction.buyer_id).join(Seller, Seller.id == Transaction.seller_id))
        who = ([Seller.name.ilike(like)] if user.role == "buyer" else [Buyer.name.ilike(like)] if user.role == "seller"
               else [Buyer.name.ilike(like), Seller.name.ilike(like)])
        q = q.filter(or_(Transaction.ref.ilike(like), Product.title.ilike(like), *who))
    f = args.get("status", "all")
    if f not in FILTERS:
        f = "all"
    if f == "action":
        ids = [t.id for t in scope(user).filter(Transaction.status.notin_(TERMINAL)).all() if next_action(t, side_of(user, t))["mine"]]
        q = q.filter(Transaction.id.in_(ids or [0]))
    elif FILTERS[f][1]:
        q = q.filter(Transaction.status.in_(FILTERS[f][1]))
    return q, f


PIPELINE = [  # (label, icon, statuses): where the user's transactions are, grouped for the overview strip
    ("Payment", "credit-card", ("CREATED", "AWAITING_PAYMENT")),
    ("Seller proof", "video", ("PAYMENT_CAPTURED", "SELLER_VERIFICATION", "NEEDS_MORE_EVIDENCE")),
    ("Shipping", "truck", ("READY_TO_SHIP", "SHIPPED")),
    ("Inspection", "scan-search", ("DELIVERED", "BUYER_INSPECTION", "AI_VERIFICATION")),
    ("Release", "unlock-keyhole", ("AWAITING_RELEASE", "RELEASE_PROCESSING", "RELEASE_PENDING", "MANUAL_REVIEW")),
    ("Completed", "circle-check-big", ("COMPLETED",)),
]


def pipeline(user):
    """Real counts of the user's transactions per stage group, plus disputed ones."""
    txs = scope(user).all()
    steps = [{"label": l, "icon": ic, "n": sum(1 for t in txs if t.status in st)} for l, ic, st in PIPELINE]
    return {"steps": steps, "disputed": sum(1 for t in txs if t.status == "DISPUTED"), "total": sum(x["n"] for x in steps)}


def buyer_progress(user):
    """Average progress (0-100) of the buyer's active protected purchases along the main flow, or None when there are none."""
    vals = []
    for t in scope(user).filter(Transaction.status.notin_(TERMINAL + PRE_PAYMENT)).all():
        i = t.flow_index()
        if i is not None:
            vals.append(i / (len(MAIN_FLOW) - 1))
    return {"pct": round(100 * sum(vals) / len(vals)) if vals else None, "n": len(vals)}
