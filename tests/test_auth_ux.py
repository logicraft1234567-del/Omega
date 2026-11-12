import os, re, sys
os.environ["OMEGA_NO_AUTOCREATE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from test_portal import make_app, login
from models import db, User
from seed import seed

ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture
def app(tmp_path):
    a = make_app(tmp_path)
    with a.app_context():
        seed()
    return a


@pytest.mark.parametrize("evil", ["//evil.com", "/\\evil.com", "https://evil.com", "javascript:alert(1)", "\\\\evil.com"])
def test_login_never_redirects_off_site(app, evil):
    r = app.test_client().post("/login?next=" + evil, data={"email": "buyer@omega.test", "password": "omega123"})
    assert r.status_code == 302 and "evil" not in r.headers["Location"] and r.headers["Location"].startswith("/")


def test_login_follows_safe_next(app):
    r = app.test_client().post("/login?next=/transactions", data={"email": "buyer@omega.test", "password": "omega123"})
    assert r.headers["Location"].endswith("/transactions")


def test_logged_in_users_skip_login_and_signup(app):
    c = login(app, "buyer@omega.test")
    assert c.get("/login").status_code == 302 and c.get("/signup").status_code == 302


def test_failed_login_keeps_email_but_never_password(app):
    html = app.test_client().post("/login", data={"email": "buyer@omega.test", "password": "secret-wrong"}).get_data(as_text=True)
    assert 'value="buyer@omega.test"' in html and "secret-wrong" not in html and "incorrect" in html


def test_failed_signup_keeps_fields(app):
    html = app.test_client().post("/signup", data={"role": "seller", "name": "Ada Obi", "email": "ada@x.test", "password": "short"}).get_data(as_text=True)
    assert 'value="Ada Obi"' in html and 'value="ada@x.test"' in html and "at least 8" in html and "show-seller" in html


def test_signup_rejects_bad_email_and_bad_paypal_and_creates_account(app):
    c = app.test_client()
    assert "valid email" in c.post("/signup", data={"name": "A B", "email": "nope", "password": "longenough1"}).get_data(as_text=True)
    assert "PayPal" in c.post("/signup", data={"role": "seller", "name": "A B", "email": "a@b.co", "password": "longenough1", "paypal_email": "bad"}).get_data(as_text=True)
    r = c.post("/signup", data={"role": "seller", "name": "A B", "email": "a@b.co", "password": "longenough1", "paypal_email": "pay@b.co"})
    assert r.status_code == 302
    with app.app_context():
        u = User.query.filter_by(email="a@b.co").first()
        assert u.role == "seller" and u.paypal_email == "pay@b.co"


def test_buyer_signup_ignores_hidden_paypal_field(app):
    app.test_client().post("/signup", data={"role": "buyer", "name": "B B", "email": "b@b.co", "password": "longenough1", "paypal_email": "x@y.co"})
    with app.app_context():
        assert User.query.filter_by(email="b@b.co").first().paypal_email is None


def test_auth_pages_have_no_orbit(app):
    for url in ("/login", "/signup"):
        html = app.test_client().get(url).get_data(as_text=True)
        assert "orbit" not in html


def test_every_icon_used_in_templates_exists_in_sprite():
    sprite = open(os.path.join(ROOT, "static/vendor/icons.svg")).read()
    have = set(re.findall(r'<symbol id="([^"]+)"', sprite))
    used = set()
    for base, _, files in os.walk(os.path.join(ROOT, "templates")):
        for f in files:
            used |= set(re.findall(r"icon\(\s*'([a-z0-9-]+)'", open(os.path.join(base, f)).read()))
    assert not (used - have), f"run tools/build_icons.py, missing: {used - have}"


def test_static_assets_are_versioned_and_cached(app):
    c = app.test_client()
    html = c.get("/login").get_data(as_text=True)
    href = re.search(r'href="(/static/css/omega\.css\?v=\d+)"', html)
    assert href, "css url should carry a version"
    assert "immutable" in c.get(href.group(1)).headers["Cache-Control"]
