import os, sys
os.environ["OMEGA_NO_AUTOCREATE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
from app import create_app
from models import db, User, Product, Transaction, Agreement, Acknowledgement, Payment, Disbursement, Dispute
from seed import seed


def make_app(tmp_path, mode="simulated"):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False, "PAYMENT_MODE": mode,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/t.db", "UPLOAD_ROOT": str(tmp_path / "up")})
    with app.app_context():
        db.drop_all(); db.create_all()
    return app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def login(app, email, pw="omega123"):
    c = app.test_client()
    assert c.post("/login", data={"email": email, "password": pw}).status_code == 302
    return c


def add_tx(buyer, seller, product, status, **kw):
    t = Transaction(product_id=product.id, buyer_id=buyer.id, seller_id=seller.id, amount=product.price, status=status, **kw)
    db.session.add(t); db.session.flush()
    a = Agreement(transaction_id=t.id, specs=product.spec_snapshot(), terms={"release": "x"}); db.session.add(a); db.session.flush()
    db.session.add(Acknowledgement(agreement_id=a.id, user_id=buyer.id, role="buyer"))
    db.session.add(Payment(transaction_id=t.id, provider="paypal_standard", order_id=f"O{t.id}", capture_id=f"C{t.id}", status="captured", amount=product.price, currency="USD"))
    return t


def test_new_accounts_see_empty_states_and_zero_not_samples(app):
    with app.app_context():
        for e, r in (("b@x.test", "buyer"), ("s@x.test", "seller")):
            db.session.add(User(email=e, name="New " + r, role=r, password_hash=generate_password_hash("password1"), email_verified=True))
        db.session.commit()
    b = login(app, "b@x.test", "password1")
    html = b.get("/dashboard").get_data(as_text=True)
    assert "Your transactions will appear here once you make your first purchase." in html
    assert "$" not in html.split("kpis")[1].split("</section>")[0].replace("$0.00", "")  # no invented amounts
    assert "No payments currently held" in html
    for path, text in (("/transactions", "No transactions yet"), ("/verification", "No verification requests yet"),
                       ("/payments", "Your payment activity will appear here"), ("/disputes", "You currently have no disputes")):
        assert text in b.get(path).get_data(as_text=True), path
    s = login(app, "s@x.test", "password1")
    html = s.get("/dashboard").get_data(as_text=True)
    assert "No listings yet" in html and "$0.00" in html
    assert "You haven" in s.get("/listings").get_data(as_text=True)


def test_stats_come_from_real_records(app):
    with app.app_context():
        seed()
        b, s, ps = User.query.filter_by(role="buyer").first(), User.query.filter_by(role="seller").first(), Product.query.all()
        add_tx(b, s, ps[0], "SELLER_VERIFICATION", ship_deadline=datetime.utcnow() + timedelta(hours=5))
        done = add_tx(b, s, ps[1], "COMPLETED"); db.session.add(Disbursement(transaction_id=done.id, provider="p", payout_id="P", status="sent", amount=ps[1].price))
        disp = add_tx(b, s, ps[2], "DISPUTED"); db.session.flush()
        db.session.add(Dispute(transaction_id=disp.id, opened_by_id=b.id, reason="damaged", description="cracked corner on arrival", status="open"))
        db.session.commit(); paid, held = float(ps[1].price), float(ps[0].price + ps[2].price)
    html = login(app, "buyer@omega.test").get("/dashboard").get_data(as_text=True)
    assert f"${held:,.2f} held by Omega" in html
    sh = login(app, "seller@omega.test").get("/dashboard").get_data(as_text=True)
    assert f"${paid:,.2f}" in sh and f"${held:,.2f}" in sh and "1 payout" in sh
    assert "Record the verification video" in sh or "Confirm the listing details" in sh


def test_transactions_filters_search_pagination_and_privacy(app):
    with app.app_context():
        seed()
        b, s, ps = User.query.filter_by(role="buyer").first(), User.query.filter_by(role="seller").first(), Product.query.all()
        other = User(email="o@x.test", name="Other Buyer", role="buyer", password_hash=generate_password_hash("password1"), email_verified=True)
        db.session.add(other); db.session.flush()
        for i in range(12):
            add_tx(b, s, ps[i % 4], "COMPLETED" if i % 2 else "SHIPPED")
        theirs = add_tx(other, s, ps[0], "SHIPPED"); db.session.commit(); their_ref = theirs.ref
    c = login(app, "buyer@omega.test")
    p1 = c.get("/transactions").get_data(as_text=True)
    assert "Showing 1–10 of 12" in p1 and their_ref not in p1
    assert "Showing 11–12 of 12" in c.get("/transactions?page=2").get_data(as_text=True)
    assert "Showing 1–6 of 6" in c.get("/transactions?status=completed").get_data(as_text=True)
    assert "No transactions match" in c.get("/transactions?q=zzzzzz").get_data(as_text=True)
    assert "Other Buyer" not in c.get("/transactions?q=Other").get_data(as_text=True)   # search never reaches other people's orders
    assert c.get(f"/orders/{their_ref}").status_code == 403
    assert "No results for" in c.get(f"/search?q={their_ref}").get_data(as_text=True)


def test_verification_center_separates_ai_from_backend_policy(app):
    with app.app_context():
        seed()
        b, s, ps = User.query.filter_by(role="buyer").first(), User.query.filter_by(role="seller").first(), Product.query.all()
        add_tx(b, s, ps[0], "SELLER_VERIFICATION"); db.session.commit()
    html = login(app, "buyer@omega.test").get("/verification").get_data(as_text=True)
    assert "Not submitted" in html and "No comparison yet" in html
    assert "Backend release policy" in html and "cannot approve, release or refund" in html
    assert "Reported confidence" not in html   # no confidence score is shown without a real comparison


def test_checkout_is_disabled_when_payments_unconfigured(tmp_path):
    app = make_app(tmp_path, "unconfigured")
    with app.app_context():
        seed()
    c = login(app, "buyer@omega.test")
    assert "Checkout is unavailable" in c.get("/checkout/1").get_data(as_text=True)
    r = c.post("/checkout/1", data={"agree": "1"})
    assert r.status_code == 302 and "/checkout" not in r.location and "/pay/" not in r.location
    with app.app_context():
        assert Transaction.query.count() == 0 and Product.query.get(1).status == "active"
    assert c.get("/pay/simulate/OM-X").status_code in (403, 404)
    assert "Not configured" in c.get("/payments").get_data(as_text=True)


def test_logout_requires_post_and_ends_session(app):
    with app.app_context():
        seed()
    c = login(app, "buyer@omega.test")
    assert c.get("/logout").status_code == 405
    assert c.get("/dashboard").status_code == 200
    assert c.post("/logout").status_code == 302
    assert c.get("/dashboard").status_code == 302


def test_change_password_validates_and_persists(app):
    with app.app_context():
        seed()
    c = login(app, "buyer@omega.test")
    c.post("/settings/password", data={"current": "wrong", "new": "newpassword1", "confirm": "newpassword1"})
    c.post("/settings/password", data={"current": "omega123", "new": "short", "confirm": "short"})
    c.post("/settings/password", data={"current": "omega123", "new": "newpassword1", "confirm": "different1"})
    assert app.test_client().post("/login", data={"email": "buyer@omega.test", "password": "newpassword1"}).status_code == 200  # still unchanged
    c.post("/settings/password", data={"current": "omega123", "new": "newpassword1", "confirm": "newpassword1"})
    assert app.test_client().post("/login", data={"email": "buyer@omega.test", "password": "newpassword1"}).status_code == 302


def test_role_navigation_and_guards(app):
    with app.app_context():
        seed()
    b = login(app, "buyer@omega.test"); s = login(app, "seller@omega.test"); a = login(app, "admin@omega.test")
    assert b.get("/listings").status_code == 302                    # buyers have no listings page
    assert "My Listings" not in b.get("/dashboard").get_data(as_text=True)
    assert "My Listings" in s.get("/dashboard").get_data(as_text=True)
    assert "/admin/" in a.get("/admin/").get_data(as_text=True) and b.get("/admin/").status_code == 403
    assert app.test_client().get("/transactions").status_code == 302  # login required
    for c in (b, s, a):
        for p in ("/payments", "/disputes", "/verification", "/settings", "/search?q=iphone", "/notifications"):
            assert c.get(p).status_code == 200, p


def test_landing_page_has_no_invented_figures(app):
    html = app.test_client().get("/").get_data(as_text=True)
    for fake in ("$650", "94%", "47h 12m", "11 of 11 checks passed", "Demo accounts"):
        assert fake not in html
    assert "Demo accounts" not in app.test_client().get("/login").get_data(as_text=True)


def test_home_page_serves_the_product_intro(app):
    html = app.test_client().get("/").get_data(as_text=True)
    assert 'id="intro"' in html and "js/intro.js" in html and "storm" not in html.lower()
    assert app.test_client().get("/static/js/intro.js").status_code == 200


def test_signup_needs_no_email_verification_and_works_immediately(app):
    with app.app_context():
        seed()
    c = app.test_client()
    r = c.post("/signup", data={"name": "Ada Obi", "email": "ada@x.test", "password": "password1", "role": "seller"})
    assert r.status_code == 302
    html = c.get("/dashboard").get_data(as_text=True)
    assert "Verify your email" not in html and "Ada" in html
    assert c.get("/sell/new").status_code == 200          # sellers can list straight away
    for gone in ("/forgot", "/verify/abc", "/reset/abc"):
        assert c.get(gone).status_code == 404
    page = app.test_client().get("/login").get_data(as_text=True)
    assert "Forgot" not in page and "data-eye" in page
    assert 'data-role' in app.test_client().get("/signup?role=seller").get_data(as_text=True)
