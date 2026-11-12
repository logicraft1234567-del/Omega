import secrets
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


def now():
    return datetime.utcnow()


def new_ref(prefix="OM"):
    return f"{prefix}-{secrets.token_hex(4).upper()}"


# ---------- Transaction state machine (backend-validated; users can never jump states) ----------
TRANSITIONS = {
    "CREATED": ["AWAITING_PAYMENT", "CANCELLED"],
    "AWAITING_PAYMENT": ["PAYMENT_CAPTURED", "CANCELLED"],
    "PAYMENT_CAPTURED": ["SELLER_VERIFICATION", "REFUND_PENDING"],
    "SELLER_VERIFICATION": ["READY_TO_SHIP", "NEEDS_MORE_EVIDENCE", "MANUAL_REVIEW", "REFUND_PENDING"],
    "NEEDS_MORE_EVIDENCE": ["SELLER_VERIFICATION", "AI_VERIFICATION", "MANUAL_REVIEW", "DISPUTED", "REFUND_PENDING"],
    "READY_TO_SHIP": ["SHIPPED", "REFUND_PENDING", "MANUAL_REVIEW"],
    "SHIPPED": ["DELIVERED", "DISPUTED", "REFUND_PENDING", "MANUAL_REVIEW"],
    "DELIVERED": ["BUYER_INSPECTION"],
    "BUYER_INSPECTION": ["AI_VERIFICATION", "DISPUTED", "MANUAL_REVIEW"],
    "AI_VERIFICATION": ["AWAITING_RELEASE", "NEEDS_MORE_EVIDENCE", "MANUAL_REVIEW", "DISPUTED"],
    "MANUAL_REVIEW": ["AWAITING_RELEASE", "DISPUTED", "REFUND_PENDING", "NEEDS_MORE_EVIDENCE"],
    "AWAITING_RELEASE": ["RELEASE_PROCESSING", "DISPUTED", "MANUAL_REVIEW"],
    "RELEASE_PROCESSING": ["COMPLETED", "AWAITING_RELEASE"],
    "DISPUTED": ["REFUND_PENDING", "RELEASE_PENDING", "RESOLVED"],
    "RELEASE_PENDING": ["RELEASE_PROCESSING"],
    "REFUND_PENDING": ["REFUNDED", "DISPUTED"],
    "RESOLVED": [], "COMPLETED": [], "REFUNDED": [], "CANCELLED": [],
}

STATUS_LABELS = {
    "CREATED": "Created", "AWAITING_PAYMENT": "Awaiting payment", "PAYMENT_CAPTURED": "Payment secured",
    "SELLER_VERIFICATION": "Seller verification", "READY_TO_SHIP": "Ready to ship", "SHIPPED": "Shipped",
    "DELIVERED": "Delivered", "BUYER_INSPECTION": "Buyer inspection", "AI_VERIFICATION": "AI verification",
    "AWAITING_RELEASE": "Awaiting release", "RELEASE_PROCESSING": "Releasing payment", "COMPLETED": "Completed",
    "NEEDS_MORE_EVIDENCE": "Needs more evidence", "MANUAL_REVIEW": "Manual review", "DISPUTED": "Disputed",
    "REFUND_PENDING": "Refund pending", "RELEASE_PENDING": "Release pending", "RESOLVED": "Resolved",
    "REFUNDED": "Refunded", "CANCELLED": "Cancelled",
}
MAIN_FLOW = ["AWAITING_PAYMENT", "PAYMENT_CAPTURED", "SELLER_VERIFICATION", "READY_TO_SHIP", "SHIPPED",
             "DELIVERED", "BUYER_INSPECTION", "AI_VERIFICATION", "AWAITING_RELEASE", "COMPLETED"]


class InvalidTransition(Exception):
    pass


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(190), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(12), nullable=False, default="buyer")  # buyer | seller | admin
    phone = db.Column(db.String(40))
    location = db.Column(db.String(120))
    photo = db.Column(db.String(255))
    bio = db.Column(db.String(400))
    paypal_email = db.Column(db.String(190))
    paypal_merchant_id = db.Column(db.String(60))
    email_verified = db.Column(db.Boolean, default=True)   # legacy column, no longer used (email verification removed for development)
    suspended = db.Column(db.Boolean, default=False)
    id_verified = db.Column(db.Boolean, default=False)  # seller verification (advanced) – admin sets
    created_at = db.Column(db.DateTime, default=now)

    @property
    def is_active(self):
        return not self.suspended

    @property
    def initials(self):
        return "".join(p[0] for p in self.name.split()[:2]).upper()

    def completed_sales(self):
        return Transaction.query.filter_by(seller_id=self.id, status="COMPLETED").count()

    def rating(self):
        rows = Review.query.filter_by(seller_id=self.id).all()
        return (sum(r.stars for r in rows) / len(rows), len(rows)) if rows else (None, 0)

    def disputes_against(self):
        return (Dispute.query.join(Transaction, Dispute.transaction_id == Transaction.id)
                .filter(Transaction.seller_id == self.id).count())


class Setting(db.Model):
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(200))


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(40), default="Phones")
    brand = db.Column(db.String(60))
    model = db.Column(db.String(80))
    storage = db.Column(db.String(30))
    colour = db.Column(db.String(40))
    condition = db.Column(db.String(30), default="Good")
    serial = db.Column(db.String(60))
    description = db.Column(db.Text)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), default="active")  # active | sold | flagged | removed
    risk_report = db.Column(db.JSON)
    risk_score = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=now)
    seller = db.relationship("User", backref="products")
    images = db.relationship("ProductImage", backref="product", cascade="all,delete-orphan", order_by="ProductImage.id")

    @property
    def cover(self):
        return self.images[0].path if self.images else None

    def spec_snapshot(self):
        return {k: getattr(self, k) for k in ["title", "category", "brand", "model", "storage", "colour", "condition", "serial"]} | {"price": str(self.price)}


class ProductImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    path = db.Column(db.String(255), nullable=False)  # relative to static/ (seed) or media/ (uploads)
    phash = db.Column(db.String(32), index=True)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ref = db.Column(db.String(20), unique=True, default=new_ref)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(3), default="USD")
    status = db.Column(db.String(24), default="CREATED", index=True)
    challenge = db.Column(db.String(120))          # randomized seller-video challenge
    needs_evidence_from = db.Column(db.String(10))  # seller | buyer
    buyer_confirmed = db.Column(db.Boolean, default=False)   # "item matches agreement"
    admin_approved = db.Column(db.Boolean, default=False)
    risk_score = db.Column(db.Integer, default=0)
    risk_reasons = db.Column(db.JSON)
    ship_deadline = db.Column(db.DateTime)
    inspection_deadline = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=now)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)

    product = db.relationship("Product")
    buyer = db.relationship("User", foreign_keys=[buyer_id], backref="purchases")
    seller = db.relationship("User", foreign_keys=[seller_id], backref="sales")
    agreement = db.relationship("Agreement", uselist=False, backref="transaction")
    payment = db.relationship("Payment", uselist=False, backref="transaction")
    disbursement = db.relationship("Disbursement", uselist=False, backref="transaction")
    shipping = db.relationship("ShippingRecord", uselist=False, backref="transaction")
    events = db.relationship("TimelineEvent", backref="transaction", order_by="TimelineEvent.id", cascade="all,delete-orphan")
    evidence = db.relationship("Evidence", backref="transaction", order_by="Evidence.id")
    results = db.relationship("VerificationResult", backref="transaction", order_by="VerificationResult.id")
    disputes = db.relationship("Dispute", backref="transaction", order_by="Dispute.id")

    @property
    def label(self):
        return STATUS_LABELS.get(self.status, self.status)

    def can_go(self, new):
        return new in TRANSITIONS.get(self.status, [])

    def transition(self, new, actor="system", note=None):
        if not self.can_go(new):
            raise InvalidTransition(f"{self.status} -> {new} is not allowed")
        old, self.status = self.status, new
        self.log("status", f"{STATUS_LABELS[old]} → {STATUS_LABELS[new]}" + (f" · {note}" if note else ""), actor)

    def log(self, kind, message, actor="system", data=None):
        self.events.append(TimelineEvent(kind=kind, message=message, actor=actor, data=data))

    def latest_result(self, kind=None):
        rows = [r for r in self.results if kind is None or r.kind == kind]
        return rows[-1] if rows else None

    def latest_evidence(self, kind):
        rows = [e for e in self.evidence if e.kind == kind]
        return rows[-1] if rows else None

    def open_dispute(self):
        return next((d for d in self.disputes if d.status in ("open", "reviewing")), None)

    def flow_index(self):
        return MAIN_FLOW.index(self.status) if self.status in MAIN_FLOW else None


class Agreement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), unique=True)
    specs = db.Column(db.JSON)
    terms = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=now)
    acks = db.relationship("Acknowledgement", backref="agreement", order_by="Acknowledgement.id")
    changes = db.relationship("ChangeRecord", backref="agreement", order_by="ChangeRecord.id")

    def acked_by(self, role):
        return any(a.role == role for a in self.acks)


class Acknowledgement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agreement_id = db.Column(db.Integer, db.ForeignKey("agreement.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    role = db.Column(db.String(10))
    ip = db.Column(db.String(60))
    created_at = db.Column(db.DateTime, default=now)
    user = db.relationship("User")


class ChangeRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agreement_id = db.Column(db.Integer, db.ForeignKey("agreement.id"))
    description = db.Column(db.String(300))
    approved_by = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=now)


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), unique=True)
    provider = db.Column(db.String(30))
    order_id = db.Column(db.String(80), index=True)
    capture_id = db.Column(db.String(80), index=True)
    status = db.Column(db.String(20), default="created")  # created | approved | captured | refunded | failed
    amount = db.Column(db.Numeric(10, 2))
    currency = db.Column(db.String(3))
    approve_url = db.Column(db.String(500))
    raw = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=now)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)


class Disbursement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), unique=True)  # unique => never two payouts
    provider = db.Column(db.String(30))
    payout_id = db.Column(db.String(80))
    status = db.Column(db.String(20), default="pending")  # pending | sent | failed | refunded
    amount = db.Column(db.Numeric(10, 2))
    fee = db.Column(db.Numeric(10, 2), default=0)
    released_by = db.Column(db.String(120))
    raw = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=now)


class WebhookEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(80), unique=True)  # idempotency key
    event_type = db.Column(db.String(80))
    verified = db.Column(db.Boolean, default=False)
    payload = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=now)


class Evidence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), index=True)
    dispute_id = db.Column(db.Integer, db.ForeignKey("dispute.id"))
    uploader_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    kind = db.Column(db.String(30))  # seller_video | buyer_video | dispatch_photo | delivery_photo | dispute_file
    media = db.Column(db.String(10))  # video | image
    path = db.Column(db.String(255))
    sha256 = db.Column(db.String(64), index=True)
    size = db.Column(db.Integer)
    meta = db.Column(db.JSON)
    analysis_status = db.Column(db.String(14), default="n/a")  # queued | running | done | failed | n/a
    created_at = db.Column(db.DateTime, default=now)
    uploader = db.relationship("User")


class VerificationResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), index=True)
    kind = db.Column(db.String(20))  # seller_video | buyer_video | comparison
    engine = db.Column(db.String(20))
    recommendation = db.Column(db.String(30))
    confidence = db.Column(db.Float)
    report = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=now)


class ShippingRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), unique=True)
    courier = db.Column(db.String(60))
    tracking = db.Column(db.String(80))
    package_id = db.Column(db.String(60))
    seal_ok_dispatch = db.Column(db.Boolean)
    seal_ok_receipt = db.Column(db.Boolean)
    dispatched_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)
    courier_events = db.Column(db.JSON, default=list)


class Dispute(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), index=True)
    opened_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reason = db.Column(db.String(40))
    description = db.Column(db.Text)
    kind = db.Column(db.String(12), default="problem")  # problem | refund
    status = db.Column(db.String(12), default="open")  # open | reviewing | resolved
    ai_report = db.Column(db.JSON)
    resolution = db.Column(db.String(30))  # refund_buyer | release_seller | dismissed
    admin_note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now)
    resolved_at = db.Column(db.DateTime)
    opened_by = db.relationship("User")
    files = db.relationship("Evidence", backref="dispute", foreign_keys="Evidence.dispute_id")


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    kind = db.Column(db.String(20))
    message = db.Column(db.String(300))
    link = db.Column(db.String(200))
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now)


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), unique=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    stars = db.Column(db.Integer)
    comment = db.Column(db.String(400))
    created_at = db.Column(db.DateTime, default=now)
    buyer = db.relationship("User", foreign_keys=[buyer_id])


class TimelineEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), index=True)
    kind = db.Column(db.String(20))
    message = db.Column(db.String(400))
    actor = db.Column(db.String(80))
    data = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=now)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor = db.Column(db.String(120))
    action = db.Column(db.String(80))
    target = db.Column(db.String(120))
    detail = db.Column(db.String(400))
    created_at = db.Column(db.DateTime, default=now)


def audit(actor, action, target="", detail=""):
    db.session.add(AuditLog(actor=str(actor), action=action, target=str(target), detail=detail[:400]))


def notify(user_id, kind, message, link=None):
    db.session.add(Notification(user_id=user_id, kind=kind, message=message, link=link))


def get_setting(key, default):
    s = db.session.get(Setting, key)
    if not s:
        return default
    try:
        return type(default)(s.value)
    except Exception:
        return default


def set_setting(key, value):
    s = db.session.get(Setting, key) or Setting(key=key)
    s.value = str(value)
    db.session.add(s)
