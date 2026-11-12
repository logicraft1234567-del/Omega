"""Demo data: 3 accounts (password omega123) and a handful of used-electronics listings using the site imagery."""
import os, shutil
from datetime import datetime, timedelta
from flask import current_app
from werkzeug.security import generate_password_hash
from models import db, User, Product, ProductImage, Review
from services import risk, video as V

DEMO = [
    ("iPhone 15 Pro 256GB — Natural Titanium", "Phones", "Apple", "iPhone 15 Pro", "256GB", "Natural Titanium", "Good", "F2LXK1234567",
     "Used for 8 months, always in a case. Minor hairline scratches on the frame, screen is spotless. Battery health 92%. Includes original box and USB-C cable.", 650),
    ("iPhone 14 128GB — Midnight", "Phones", "Apple", "iPhone 14", "128GB", "Midnight", "Like new", "G6QHX7654321",
     "Barely used, battery health 98%. Never dropped, screen protector fitted since day one. Box and cable included.", 455),
    ("iPhone 13 128GB — Starlight", "Phones", "Apple", "iPhone 13", "128GB", "Starlight", "Fair", "",
     "Works fine.", 180),
    ("iPhone 15 Pro Max 512GB — Natural Titanium", "Phones", "Apple", "iPhone 15 Pro", "512GB", "Natural Titanium", "Good", "H9TTY5550001",
     "Immaculate condition, original packaging, 3 months of use. Battery 96%. Selling because I upgraded for work.", 780),
]


def seed():
    pw = generate_password_hash("omega123")
    admin = User(email="admin@omega.test", name="Omega Admin", role="admin", password_hash=pw, email_verified=True, location="Lagos")
    seller = User(email="seller@omega.test", name="Amara Okafor", role="seller", password_hash=pw, email_verified=True, id_verified=True,
                  location="Berlin, DE", paypal_email="seller-sandbox@omega.test", bio="Refurbished phones, honestly graded.",
                  created_at=datetime.utcnow() - timedelta(days=120))
    buyer = User(email="buyer@omega.test", name="Daniel Reyes", role="buyer", password_hash=pw, email_verified=True, location="Madrid, ES",
                 created_at=datetime.utcnow() - timedelta(days=30))
    db.session.add_all([admin, seller, buyer]); db.session.flush()
    src = os.path.join(current_app.root_path, "static", "img", "iphone.png")
    for t, cat, br, mo, st, col, cond, ser, desc, price in DEMO:
        p = Product(seller_id=seller.id, title=t, category=cat, brand=br, model=mo, storage=st, colour=col, condition=cond,
                    serial=ser, description=desc, price=price)
        p.images.append(ProductImage(path="static:img/iphone.png", phash=V.phash(src)))
        db.session.add(p); db.session.flush()
        rep = risk.score_listing(p); p.risk_report, p.risk_score = rep, rep["score"]
        if rep["score"] >= 50: p.status = "flagged"
    db.session.commit()
