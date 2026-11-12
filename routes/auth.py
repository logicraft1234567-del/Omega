import re
from urllib.parse import urlparse
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, audit
from extensions import limiter

bp = Blueprint("auth", __name__)


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
_DUMMY_HASH = generate_password_hash("omega-timing-equaliser")   # so unknown emails cost the same time as wrong passwords


def _safe_next(default):
    """Only ever redirect to a path on this site. Rejects //host, /\\host, absolute URLs and anything with a scheme."""
    nxt = request.args.get("next") or request.form.get("next") or ""
    p = urlparse(nxt)
    if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt and not p.scheme and not p.netloc:
        return nxt
    return default


def _home():
    return url_for("orders.dashboard")


@bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("20 per hour", methods=["POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(_home())
    role = request.values.get("role", "buyer")
    role = role if role in ("buyer", "seller") else "buyer"
    if request.method == "POST":
        f = request.form
        email, name, pw = f.get("email", "").strip().lower(), " ".join(f.get("name", "").split()), f.get("password", "")
        paypal = f.get("paypal_email", "").strip().lower() if role == "seller" else ""
        if not name or not EMAIL_RE.match(email) or len(email) > 190:
            flash("Enter your name and a valid email address.", "error")
        elif len(name) > 100:
            flash("That name is too long.", "error")
        elif len(pw) < 8:
            flash("Use a password with at least 8 characters.", "error")
        elif len(pw) > 200:
            flash("That password is too long.", "error")
        elif paypal and not EMAIL_RE.match(paypal):
            flash("The PayPal email doesn't look right. Fix it, or leave it blank and add it later in Settings.", "error")
        elif User.query.filter_by(email=email).first():
            flash("That email already has an account. Try logging in.", "error")
        else:
            u = User(email=email, name=name, role=role, password_hash=generate_password_hash(pw),
                     location=f.get("location", "").strip()[:120], phone=f.get("phone", "").strip()[:40],
                     paypal_email=paypal or None)
            db.session.add(u); audit(email, "signup", email, role); db.session.commit()
            login_user(u)
            flash(f"Welcome to Omega, {u.name.split()[0]}.", "ok")
            return redirect(_safe_next(_home()))
    return render_template("auth/signup.html", form=request.form, role=role, nxt=_safe_next(""))


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("30 per hour", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_safe_next(_home()))
    if request.method == "POST":
        email, pw = request.form.get("email", "").strip().lower(), request.form.get("password", "")
        u = User.query.filter_by(email=email).first()
        ok = check_password_hash(u.password_hash if u else _DUMMY_HASH, pw)
        if not u or not ok:
            flash("Email or password is incorrect.", "error")
        elif u.suspended:
            flash("This account is suspended. Contact support.", "error")
        else:
            login_user(u, remember=bool(request.form.get("remember")))
            return redirect(_safe_next(_home()))
    return render_template("auth/login.html", form=request.form, nxt=_safe_next(""))


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You're logged out.", "ok")
    return redirect(url_for("market.index"))


@bp.route("/settings/password", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def change_password():
    f = request.form
    if not check_password_hash(current_user.password_hash, f.get("current", "")):
        flash("Your current password is incorrect.", "error")
    elif len(f.get("new", "")) < 8:
        flash("Use a new password with at least 8 characters.", "error")
    elif f.get("new") != f.get("confirm"):
        flash("The new password and its confirmation don't match.", "error")
    elif f.get("new") == f.get("current"):
        flash("Choose a password you haven't used here before.", "error")
    else:
        current_user.password_hash = generate_password_hash(f["new"])
        audit(current_user.email, "password_change", current_user.email)
        db.session.commit()
        flash("Password updated.", "ok")
    return redirect(url_for("portal.settings"))
