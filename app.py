import os, secrets
from markupsafe import Markup, escape
from datetime import datetime
from flask import Flask, render_template, request, g
from flask_login import current_user
from extensions import login_manager, csrf, limiter, migrate
from config import Config
from models import db, User, Notification, Transaction, Agreement, STATUS_LABELS, MAIN_FLOW

def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    if app.config["SECRET_KEY"] in ("dev-secret-change-me", "change-me", "") and not app.config.get("TESTING"):
        app.config["SECRET_KEY"] = secrets.token_hex(32)   # never run on a publicly known key; sessions reset on restart until SECRET_KEY is set
        app.logger.warning("SECRET_KEY is not set: using a temporary random key. Set SECRET_KEY in the environment.")
    os.makedirs(app.config["UPLOAD_ROOT"], exist_ok=True)
    db.init_app(app); login_manager.init_app(app); csrf.init_app(app); limiter.init_app(app); migrate.init_app(app, db)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Log in to continue."


    # ---- icons: server-rendered SVG sprite (replaces the 350 KB client-side icon library) ----
    sprite = {}

    def icon_svg(name, cls="", style=""):
        if "u" not in sprite:
            from flask import url_for
            sprite["u"] = url_for("static", filename="vendor/icons.svg")
        c = "lucide " + cls if cls else "lucide"
        st = f' style="{escape(style)}"' if style else ""
        return Markup(f'<svg class="{escape(c)}"{st} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                      f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                      f'<use href="{sprite["u"]}#{escape(name)}"/></svg>')
    app.jinja_env.globals["icon_svg"] = icon_svg

    # ---- static files: content-hashed URLs + long cache; gzip/brotli when flask-compress is installed ----
    _ver = {}

    @app.url_defaults
    def _static_version(endpoint, values):
        if endpoint != "static" or "filename" not in values:
            return
        fn = values["filename"]
        if fn not in _ver:
            try:
                _ver[fn] = int(os.stat(os.path.join(app.static_folder, fn)).st_mtime)
            except OSError:
                _ver[fn] = 0
        if _ver[fn]:
            values["v"] = _ver[fn]

    @app.after_request
    def _static_cache(resp):
        if request.endpoint == "static" and request.args.get("v"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    try:
        from flask_compress import Compress
        app.config.setdefault("COMPRESS_MIMETYPES", ["text/html", "text/css", "application/javascript", "text/javascript", "image/svg+xml", "application/json"])
        Compress(app)
    except ImportError:
        pass

    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(User, int(uid))

    from routes.auth import bp as auth_bp
    from routes.market import bp as market_bp
    from routes.orders import bp as orders_bp
    from routes.payments import bp as pay_bp
    from routes.admin import bp as admin_bp
    from routes.portal import bp as portal_bp
    for b in (auth_bp, market_bp, orders_bp, pay_bp, admin_bp, portal_bp):
        app.register_blueprint(b)

    @app.context_processor
    def inject():
        unread = 0
        if current_user.is_authenticated:
            unread = Notification.query.filter_by(user_id=current_user.id, read=False).count()
        action_count = 0
        if current_user.is_authenticated and current_user.role != "admin":
            from services import portal
            from sqlalchemy.orm import selectinload   # one extra query instead of one per order
            rows = portal.scope(current_user).filter(Transaction.status.notin_(portal.TERMINAL)).options(selectinload(Transaction.agreement).selectinload(Agreement.acks)).all()
            action_count = sum(1 for t in rows if t.status != "CREATED" and portal.next_action(t, portal.side_of(current_user, t))["mine"])
        mode = app.config["PAYMENT_MODE"]
        label = {"unconfigured": "not configured", "simulated": "development simulator", "paypal_standard": "PayPal", "paypal_marketplace": "PayPal marketplace"}[mode]
        return {"unread": unread, "action_count": action_count, "STATUS_LABELS": STATUS_LABELS, "MAIN_FLOW": MAIN_FLOW,
                "payment_mode": mode, "payment_label": label, "year": datetime.utcnow().year}

    @app.template_filter("ago")
    def ago(dt):
        if not dt: return ""
        s = (datetime.utcnow() - dt).total_seconds()
        for n, u in ((86400, "d"), (3600, "h"), (60, "m")):
            if s >= n: return f"{int(s // n)}{u} ago"
        return "just now"

    @app.template_filter("until")
    def until(dt):
        if not dt: return ""
        s = (dt - datetime.utcnow()).total_seconds()
        a = abs(s)
        span = f"{int(a // 86400)}d {int(a % 86400 // 3600)}h" if a >= 86400 else f"{int(a // 3600)}h {int(a % 3600 // 60)}m" if a >= 3600 else f"{max(1, int(a // 60))}m"
        return f"overdue by {span}" if s < 0 else f"in {span}"

    @app.template_filter("dt")
    def dtf(dt):
        return dt.strftime("%d %b %Y, %H:%M") if dt else "—"

    @app.template_filter("money")
    def money(v):
        return f"${float(v):,.2f}"

    @app.errorhandler(403)
    def e403(e): return render_template("error.html", code=403, msg="You don't have access to this page."), 403

    @app.errorhandler(404)
    def e404(e): return render_template("error.html", code=404, msg="We couldn't find that page."), 404

    @app.errorhandler(413)
    def e413(e): return render_template("error.html", code=413, msg="That file is too large (limit 250 MB)."), 413

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.cli.command("seed")
    def seed_cmd():
        """DEVELOPMENT ONLY: creates three demo accounts and sample listings. Never run this against a real database."""
        from seed import seed
        seed(); print("Seeded development demo data (accounts buyer/seller/admin@omega.test).")

    @app.cli.command("create-admin")
    def create_admin_cmd():
        """Create the first admin account (prompts for the password)."""
        import click
        from werkzeug.security import generate_password_hash
        email = click.prompt("Admin email").strip().lower()
        name = click.prompt("Full name").strip()
        pw = click.prompt("Password (min 8 chars)", hide_input=True, confirmation_prompt=True)
        if len(pw) < 8 or User.query.filter_by(email=email).first():
            raise click.ClickException("Password too short, or that email already exists.")
        db.session.add(User(email=email, name=name, role="admin", password_hash=generate_password_hash(pw)))
        db.session.commit(); print("Admin created.")

    @app.cli.command("doctor")
    def doctor_cmd():
        """Preflight check: tells you which keys are still missing and tests each one live (PayPal, Gemini, Groq)."""
        import click
        from services import ai
        from services.payments import PayPalProvider, PaymentError
        c, bad = app.config, 0

        def line(ok, label, detail=""):
            nonlocal bad
            bad += 0 if ok else 1
            click.echo((click.style("  OK   ", fg="green") if ok else click.style("  FAIL ", fg="red")) + label + (f"  - {detail}" if detail else ""))

        click.echo("Omega preflight")
        line(os.getenv("SECRET_KEY", "") not in ("", "dev-secret-change-me", "change-me"), "SECRET_KEY is set", "set a long random value (python -c \"import secrets;print(secrets.token_hex(32))\")")
        click.echo(f"       BASE_URL={c['BASE_URL']}  (must be your public https URL when deployed: PayPal sends the buyer back here)")
        click.echo(f"       PAYMENT_MODE={c['PAYMENT_MODE']}  PAYPAL_ENV={c['PAYPAL_ENV']}")
        if c["PAYMENT_MODE"] in ("paypal_standard", "paypal_marketplace"):
            try:
                PayPalProvider()._token()
                line(True, f"PayPal {c['PAYPAL_ENV']} credentials accepted (OAuth token received)")
            except (PaymentError, Exception) as e:
                line(False, "PayPal credentials", f"{e}. Check PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET and that PAYPAL_ENV matches the app (sandbox vs live)")
        else:
            line(False, "PayPal credentials", "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are empty, so checkout is disabled")
        click.echo("       PAYPAL_WEBHOOK_ID is " + ("set" if c["PAYPAL_WEBHOOK_ID"] else "empty (optional: checkout works without it; webhooks are rejected until it is set)"))
        st = ai.status(force=True)
        for key, name, env in (("fire", "Fire (Google Gemini, video)", "GEMINI_API_KEY"), ("water", "Water (Groq, images)", "GROQ_API_KEY")):
            line(st[key]["ok"], f"{name} reachable" + (f", model {st[key]['model']}" if st[key]["ok"] else ""), "" if st[key]["ok"] else f"{st[key]['reason']} ({env})")
        click.echo(click.style("\nAll set." if not bad else f"\n{bad} thing(s) to fix above.", fg="green" if not bad else "red"))
        raise SystemExit(1 if bad else 0)

    @app.cli.command("check-deadlines")
    def deadlines_cmd():
        from services.policy import check_deadlines
        print("Moved to review:", check_deadlines())

    with app.app_context():
        db.create_all()
    return app


app = create_app() if os.getenv("OMEGA_NO_AUTOCREATE") != "1" else None

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", 5000)))
