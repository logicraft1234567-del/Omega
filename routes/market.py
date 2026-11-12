import os, uuid
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, send_from_directory, current_app, send_file, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_
from models import db, User, Product, ProductImage, Notification, Review, Transaction, Evidence, audit, notify
from services import risk, ai, storage, video as V
from routes.orders import role_required, can_view_tx

bp = Blueprint("market", __name__)


@bp.app_template_global()
def media(path):
    if not path:
        return url_for("static", filename="img/iphone.webp")
    if path.startswith("static:"):
        return url_for("static", filename=path[7:].replace("iphone.png", "iphone.webp"))
    return url_for("market.product_media", name=path)


@bp.route("/")
def index():
    featured = Product.query.filter_by(status="active").order_by(Product.id.desc()).limit(4).all()
    from models import get_setting
    return render_template("index.html", featured=featured, inspection_hours=get_setting("inspection_hours", current_app.config["INSPECTION_HOURS"]))


@bp.route("/media/<path:name>")
def product_media(name):
    return send_from_directory(os.path.join(current_app.config["UPLOAD_ROOT"], "listings"), os.path.basename(name))


@bp.route("/browse")
def browse():
    q = Product.query.filter(Product.status == "active")
    s = request.args.get("q", "").strip()
    if s:
        like = f"%{s}%"
        q = q.filter(or_(Product.title.ilike(like), Product.model.ilike(like), Product.brand.ilike(like)))
    for f in ("category", "condition", "brand"):
        v = request.args.get(f)
        if v:
            q = q.filter(getattr(Product, f) == v)
    try:
        if request.args.get("min"): q = q.filter(Product.price >= Decimal(request.args["min"]))
        if request.args.get("max"): q = q.filter(Product.price <= Decimal(request.args["max"]))
    except InvalidOperation:
        flash("Price filters must be numbers.", "error")
    if request.args.get("seller"):
        q = q.join(User, Product.seller_id == User.id).filter(User.name.ilike(f"%{request.args['seller']}%"))
    order = {"low": Product.price.asc(), "high": Product.price.desc()}.get(request.args.get("sort"), Product.id.desc())
    items = q.order_by(order).all()
    return render_template("browse.html", items=items, args=request.args)


@bp.route("/product/<int:pid>")
def product(pid):
    p = db.session.get(Product, pid) or abort(404)
    if p.status in ("removed",) or (p.status == "flagged" and not (current_user.is_authenticated and (current_user.id == p.seller_id or current_user.role == "admin"))):
        abort(404)
    rep = p.risk_report or risk.score_listing(p)
    reviews = Review.query.filter_by(seller_id=p.seller_id).order_by(Review.id.desc()).limit(5).all()
    return render_template("product.html", p=p, rep=rep, reviews=reviews)


@bp.route("/seller/<int:uid>")
def seller_profile(uid):
    u = db.session.get(User, uid) or abort(404)
    items = Product.query.filter_by(seller_id=uid, status="active").all()
    reviews = Review.query.filter_by(seller_id=uid).order_by(Review.id.desc()).limit(10).all()
    return render_template("seller_profile.html", u=u, items=items, reviews=reviews)


# ---------------- seller: listings ----------------
def _fill(p, f):
    for k in ("title", "category", "brand", "model", "storage", "colour", "condition", "serial", "description"):
        setattr(p, k, f.get(k, "").strip() or None)
    p.price = Decimal(f.get("price", "0"))


@bp.route("/sell/new", methods=["GET", "POST"])
@login_required
@role_required("seller")
def new_listing():
    if request.method == "POST":
        f = request.form
        try:
            price = Decimal(f.get("price", "0"))
            assert price > 0 and price < 100000
        except Exception:
            flash("Enter a valid price.", "error"); return render_template("listing_form.html", p=None, f=f)
        if not f.get("title", "").strip() or not f.get("model", "").strip():
            flash("Title and model are required.", "error"); return render_template("listing_form.html", p=None, f=f)
        files = [x for x in request.files.getlist("images") if x and x.filename][:5]
        if not files:
            flash("Add at least one photo of the actual item.", "error"); return render_template("listing_form.html", p=None, f=f)
        p = Product(seller_id=current_user.id, price=price)
        _fill(p, f)
        try:
            for fs in files:
                rel, sha, size = storage.save_upload(fs, "listings", "image")
                # listing photos are public: move into listings/ folder served by /media
                name = uuid.uuid4().hex + os.path.splitext(rel)[1]
                dest = os.path.join(current_app.config["UPLOAD_ROOT"], "listings", name)
                os.replace(storage.abs_path(rel), dest)
                p.images.append(ProductImage(path=name, phash=V.phash(dest)))
        except storage.UploadError as e:
            flash(str(e), "error"); return render_template("listing_form.html", p=None, f=f)
        db.session.add(p); db.session.flush()
        rep = risk.score_listing(p)
        img_read, water_down = ai.read_listing_images([os.path.join(current_app.config["UPLOAD_ROOT"], "listings", i.path) for i in p.images],
                                          ", ".join(f"{k}: {v}" for k, v in p.spec_snapshot().items() if v))
        if water_down:   # Water couldn't be reached: say so, don't pretend the photos were checked
            rep["missing"] = list(rep.get("missing", [])) + [f"Photo check by Water (Groq image AI) did not run: {water_down['reason']}"]
            rep["ai"] = {"water": water_down}
        if img_read:
            if img_read["looks_like_stock_or_render"]:
                rep["reasons"].append({"label": "Photos look like stock images or renders, not the real item", "points": 20, "severity": "critical"})
            for issue in img_read["issues"][:3]:
                rep["reasons"].append({"label": issue, "points": 5, "severity": "warn"})
            if not img_read["matches_listing"]:
                rep["reasons"].append({"label": "Photos may not match the listed model", "points": 20, "severity": "critical"})
            rep["score"] = max(0, min(100, sum(r["points"] for r in rep["reasons"])))
            rep["level"] = "low" if rep["score"] < 25 else "medium" if rep["score"] < 50 else "high"
        p.risk_report, p.risk_score = rep, rep["score"]
        if rep["score"] >= 50:
            p.status = "flagged"
            for a in User.query.filter_by(role="admin"):
                notify(a.id, "fraud", f"Listing “{p.title}” was flagged (risk {rep['score']}).", f"/admin/products")
            flash("Your listing was submitted and is waiting for a quick admin review because of its risk report.", "ok")
        else:
            flash("Listing published.", "ok")
        audit(current_user.email, "create_listing", p.id, p.title)
        db.session.commit()
        return redirect(url_for("market.product", pid=p.id))
    return render_template("listing_form.html", p=None, f={})


@bp.route("/sell/<int:pid>/remove", methods=["POST"])
@login_required
def remove_listing(pid):
    p = db.session.get(Product, pid) or abort(404)
    if p.seller_id != current_user.id and current_user.role != "admin":
        abort(403)
    if Transaction.query.filter(Transaction.product_id == pid, Transaction.status.notin_(["CANCELLED", "REFUNDED"])).first():
        flash("This item is part of an active order and can't be removed.", "error")
    else:
        p.status = "removed"; audit(current_user.email, "remove_listing", pid); db.session.commit()
        flash("Listing removed.", "ok")
    return redirect(url_for("portal.listings"))


# ---------------- profile & notifications ----------------
@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        f = request.form
        current_user.name = f.get("name", current_user.name).strip() or current_user.name
        current_user.phone, current_user.location = f.get("phone", "").strip(), f.get("location", "").strip()
        current_user.bio = f.get("bio", "").strip()[:400]
        current_user.paypal_email = f.get("paypal_email", "").strip() or None
        ph = request.files.get("photo")
        if ph and ph.filename:
            try:
                rel, _, _ = storage.save_upload(ph, "listings", "image")
                name = uuid.uuid4().hex + os.path.splitext(rel)[1]
                os.replace(storage.abs_path(rel), os.path.join(current_app.config["UPLOAD_ROOT"], "listings", name))
                current_user.photo = name
            except storage.UploadError as e:
                flash(str(e), "error"); return redirect(url_for("market.profile"))
        db.session.commit(); flash("Profile saved.", "ok")
        return redirect(url_for("market.profile"))
    return render_template("profile.html")


@bp.route("/notifications")
@login_required
def notifications():
    rows = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.id.desc()).limit(60).all()
    for n in rows:
        n.read = True
    db.session.commit()
    return render_template("notifications.html", rows=rows)


# ---------------- private evidence (signed, short-lived URLs) ----------------
@bp.route("/evidence/<int:eid>")
@login_required
def evidence_file(eid):
    ev = db.session.get(Evidence, eid) or abort(404)
    if not storage.verify_evidence_token(request.args.get("t", ""), eid, current_user.id):
        abort(403)
    if not can_view_tx(ev.transaction):
        abort(403)
    return send_file(storage.abs_path(ev.path), conditional=True)


@bp.route("/evidence/<int:eid>/frame/<int:i>")
@login_required
def evidence_frame(eid, i):
    ev = db.session.get(Evidence, eid) or abort(404)
    if not can_view_tx(ev.transaction):
        abort(403)
    frames = (ev.meta or {}).get("frames", [])
    if i >= len(frames):
        abort(404)
    return send_file(storage.abs_path(frames[i]), mimetype="image/jpeg")


@bp.route("/healthz")
def health():
    st = ai.status(force=request.args.get("live") == "1")
    return jsonify(ok=True, payments=current_app.config["PAYMENT_MODE"],
                   fire={"ok": st["fire"]["ok"], "provider": "Gemini", "model": st["fire"]["model"], "reason": st["fire"]["reason"]},
                   water={"ok": st["water"]["ok"], "provider": "Groq", "model": st["water"]["model"], "reason": st["water"]["reason"]})
