import httpx
from fastapi.testclient import TestClient

from newtube.app import create_app
from tests.test_api import FakeExtractor, make_client


def test_spa_serving(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "a-123.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    with make_client(tmp_path, lambda r: httpx.Response(404), FakeExtractor()) as (client, ctx):
        ctx.settings.frontend_dir = dist
        app = create_app(ctx)                       # rebuild so the new frontend_dir is used
        with TestClient(app) as c:
            assert c.get("/").text == "<html>app</html>"
            assert c.get("/watch?v=x").text == "<html>app</html>"            # client-side route
            r = c.get("/assets/a-123.js")
            assert r.text == "console.log(1)" and "immutable" in r.headers["cache-control"]
            assert c.get("/..%2Fsecret.txt").text == "<html>app</html>"       # traversal -> index
            assert c.get("/%2e%2e/secret.txt").text == "<html>app</html>"
            assert "default-src 'self'" in c.get("/").headers["content-security-policy"]
            c.post("/api/login", json={"password": "correct horse"})
            assert c.get("/api/does-not-exist").status_code == 404


def test_missing_build_gives_helpful_503(tmp_path):
    with make_client(tmp_path, lambda r: httpx.Response(404), FakeExtractor()) as (client, ctx):
        ctx.settings.frontend_dir = tmp_path / "nope"
        with TestClient(create_app(ctx)) as c:
            r = c.get("/")
            assert r.status_code == 503 and "npm run build" in r.text
