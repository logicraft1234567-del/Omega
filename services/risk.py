"""Fraud & risk intelligence: transparent rule scoring + an IsolationForest anomaly signal once enough listings exist."""
import re, statistics
from datetime import datetime
from models import db, Product, ProductImage, Transaction, Dispute, Evidence
from . import video as V

REFERENCE = {  # typical used-market USD ranges (low, high) – seed table, replace with live comps in production
    "iphone 15 pro": (600, 950), "iphone 15": (450, 750), "iphone 14": (330, 600), "iphone 13": (250, 450),
    "iphone 12": (180, 350), "iphone 11": (130, 260), "galaxy s24": (450, 850), "galaxy s23": (320, 650),
    "pixel 8": (250, 520), "macbook air": (450, 1000), "macbook pro": (700, 1800), "ipad": (200, 650),
    "airpods pro": (100, 230), "ps5": (300, 480), "xbox series": (250, 420), "thinkpad": (250, 900),
}
OFFPLATFORM = re.compile(r"(whatsapp|telegram|\+?\d[\d\s\-]{8,}\d|@[a-z0-9._-]+\.[a-z]{2,}|pay\s*(outside|directly)|bank transfer|western union|gift ?card)", re.I)


def _reference(p):
    key = f"{p.brand or ''} {p.model or ''} {p.title or ''}".lower()
    for name, rng in sorted(REFERENCE.items(), key=lambda kv: -len(kv[0])):
        if name in key:
            return name, rng
    return None, None


def _level(score):
    return "low" if score < 25 else "medium" if score < 50 else "high"


def _isolation_signal(p):
    """Anomaly signal from scikit-learn IsolationForest (only when >=25 comparable listings exist)."""
    try:
        import numpy as np
        from sklearn.ensemble import IsolationForest
        rows = Product.query.filter(Product.status.in_(["active", "sold"])).all()
        if len(rows) < 25:
            return 0
        def feat(x):
            _, rng = _reference(x)
            mid = (rng[0] + rng[1]) / 2 if rng else float(x.price)
            return [float(x.price) / mid, len(x.description or ""), len(x.images), 1 if x.serial else 0]
        X = np.array([feat(r) for r in rows])
        clf = IsolationForest(n_estimators=100, contamination=0.1, random_state=7).fit(X)
        return 10 if clf.predict(np.array([feat(p)]))[0] == -1 else 0
    except Exception:
        return 0


def score_listing(p):
    reasons, missing = [], []
    def add(label, pts, sev="warn"):
        reasons.append({"label": label, "points": pts, "severity": sev})
    if not (p.serial or "").strip():
        add("No serial number or IMEI declared", 12); missing.append("Serial number / IMEI")
    if len(p.images) < 2:
        add("Fewer than 2 photos", 10); missing.append("At least 2 clear photos of the actual item")
    if len((p.description or "").strip()) < 40:
        add("Description is very short", 8); missing.append("A fuller description (battery health, accessories, flaws)")
    if OFFPLATFORM.search(p.description or "") or OFFPLATFORM.search(p.title or ""):
        add("Listing tries to move contact or payment off-platform", 25, "critical")
    name, rng = _reference(p)
    comps = [float(x.price) for x in Product.query.filter(Product.id != (p.id or 0), Product.status == "active",
             Product.model == p.model).all()] if p.model else []
    price = float(p.price)
    if rng:
        if price < rng[0] * 0.55:
            add(f"Price is far below the typical used range for {name} (${rng[0]}–${rng[1]})", 30, "critical")
        elif price < rng[0] * 0.75:
            add(f"Price is below the typical used range for {name}", 15)
        elif price > rng[1] * 1.6:
            add(f"Price is far above the typical used range for {name}", 8)
    if len(comps) >= 3 and price < statistics.median(comps) * 0.6:
        add("Price is 40%+ below comparable Omega listings", 15)
    # duplicate image detection against other sellers
    dup = False
    for im in p.images:
        if not im.phash:
            continue
        for other in ProductImage.query.join(Product).filter(Product.seller_id != p.seller_id, ProductImage.phash.isnot(None)).all():
            if V.hamming(im.phash, other.phash) <= 4:
                dup = True; break
        if dup: break
    if dup:
        add("A photo matches an image used in another seller's listing", 35, "critical")
    s = p.seller
    age = (datetime.utcnow() - s.created_at).days if s.created_at else 0
    if age < 7 and price > 500:
        add("New seller account listing a high-value item", 10)
    if s.disputes_against() >= 2:
        add(f"Seller has {s.disputes_against()} previous disputes", 15)
    if s.id_verified:
        add("Seller identity verified", -10, "good")
    if s.completed_sales() >= 3:
        add(f"{s.completed_sales()} completed protected sales", -8, "good")
    pts = sum(r["points"] for r in reasons) + _isolation_signal(p)
    score = max(0, min(100, pts))
    return {"score": score, "level": _level(score), "reasons": reasons, "missing": missing}


def score_transaction(tx):
    base = tx.product.risk_report or score_listing(tx.product)
    reasons = [r for r in base["reasons"]]
    def add(label, pts, sev="warn"):
        reasons.append({"label": label, "points": pts, "severity": sev})
    b = tx.buyer
    if (datetime.utcnow() - b.created_at).days < 2 and float(tx.amount) > 300:
        add("Brand-new buyer account making a high-value purchase", 8)
    bd = Dispute.query.filter_by(opened_by_id=b.id).count()
    if bd >= 2:
        add(f"Buyer has opened {bd} disputes before", 12)
    sv, bv = tx.latest_evidence("seller_video"), tx.latest_evidence("buyer_video")
    for ev in (sv, bv):
        if ev and ev.meta:
            fl = [f for f in ev.meta.get("flags", []) if "decode" in f or "short" in f.lower()]
            if fl:
                add(f"{ev.kind.replace('_', ' ').title()}: {fl[0]}", 8)
    if sv and bv and sv.sha256 == bv.sha256:
        add("Buyer video is byte-identical to the seller video (reused footage)", 15, "critical")
    for ev in (sv, bv):
        if ev and Evidence.query.filter(Evidence.sha256 == ev.sha256, Evidence.transaction_id != tx.id).first():
            add(f"{ev.kind.replace('_', ' ').title()} was already used in another transaction", 35, "critical")
    score = max(0, min(100, sum(r["points"] for r in reasons)))
    return {"score": score, "level": _level(score), "reasons": reasons}
