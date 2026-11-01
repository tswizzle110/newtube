import httpx
import pytest
from fastapi.testclient import TestClient

from newtube.auth import SESSION_SECONDS, Auth
from tests.test_api import env  # noqa: F401  (reuse the fully wired app fixture)


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def auth(tmp_path):
    c = Clock()
    a = Auth(tmp_path / "auth.json", now=c)
    a.clock = c
    a.set_password("correct horse")
    return a


def test_password_file_is_private_and_not_plaintext(auth):
    assert oct(auth.path.stat().st_mode & 0o777) == "0o600"
    assert "correct horse" not in auth.path.read_text()


def test_verify(auth):
    assert auth.verify_password("correct horse")
    assert not auth.verify_password("nope")


def test_short_password_rejected(tmp_path):
    with pytest.raises(ValueError):
        Auth(tmp_path / "a.json").set_password("short")


def test_token_valid_expired_and_tampered(auth):
    tok = auth.make_token()
    assert auth.check_token(tok)
    assert not auth.check_token(tok[:-1] + ("0" if tok[-1] != "0" else "1"))
    assert not auth.check_token(None) and not auth.check_token("garbage")
    auth.clock.t += SESSION_SECONDS + 1
    assert not auth.check_token(tok)


def test_changing_password_invalidates_old_sessions(auth):
    tok = auth.make_token()
    auth.set_password("another long one")
    assert not auth.check_token(tok)


def test_escalating_lockout(auth):
    for _ in range(4):
        auth.verify_password("x")
    assert auth.lockout_remaining() == 0
    auth.verify_password("x")                       # 5th failure -> 2s
    assert auth.lockout_remaining() == 2
    auth.clock.t += 3
    assert auth.lockout_remaining() == 0
    auth.verify_password("x")                       # 6th -> 4s
    assert auth.lockout_remaining() == 4
    auth.clock.t += 5
    assert auth.verify_password("correct horse")    # success resets
    auth.verify_password("x")
    assert auth.lockout_remaining() == 0


# ---- through the real app


def test_api_requires_login(env):
    client, ctx, *_ = env
    anon = TestClient(client.app)
    assert anon.get("/api/feed").status_code == 401
    assert anon.get(f"/api/thumb/aaaaaaaaaaa/hqdefault").status_code == 401
    assert anon.get("/api/auth/status").json() == {"authenticated": False}
    assert client.get("/api/auth/status").json() == {"authenticated": True}


def test_login_cookie_flags_and_wrong_password(env):
    client, *_ = env
    anon = TestClient(client.app)
    assert anon.post("/api/login", json={"password": "bad"}).status_code == 401
    r = anon.post("/api/login", json={"password": "correct horse"})
    c = r.headers["set-cookie"].lower()
    assert "httponly" in c and "samesite=strict" in c


def test_lockout_returns_429(env):
    client, *_ = env
    anon = TestClient(client.app)
    codes = [anon.post("/api/login", json={"password": "bad"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_logout_clears_session(env):
    client, *_ = env
    client.post("/api/logout")
    assert client.get("/api/feed").status_code == 401


def test_cross_origin_post_refused(env):
    client, *_ = env
    r = client.post("/api/subscriptions", json={"ref": "@YouTube"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.post("/api/logout", headers={"Origin": "http://testserver"})
    assert ok.status_code == 200


def test_security_headers_on_every_response(env):
    client, *_ = env
    for r in (client.get("/api/feed"), TestClient(client.app).get("/api/feed")):
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY"
