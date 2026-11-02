"""Install-to-home-screen: the web manifest and icons are well-formed, in step with the icon script, and
reachable before you log in (a phone fetches them first)."""
import importlib.util
import json
import struct
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from newtube.app import create_app
from tests.test_api import FakeExtractor, make_client

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "frontend" / "public"


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR", f"{path.name} is not a PNG"
    return struct.unpack(">II", data[16:24])


def test_manifest_is_complete_and_its_icons_exist_at_the_declared_sizes():
    m = json.loads((PUBLIC / "manifest.webmanifest").read_text())
    assert m["name"] == "newtube" and m["start_url"] == "/" and m["display"] == "standalone" and m["scope"] == "/"
    sizes = set()
    for icon in m["icons"]:
        f = PUBLIC / icon["src"].lstrip("/")
        assert f.is_file(), icon["src"]
        if icon["type"] == "image/png":
            w, h = (int(x) for x in icon["sizes"].split("x"))
            assert png_size(f) == (w, h)
            sizes.add(w)
    assert {192, 512} <= sizes                                               # what Android requires
    assert png_size(PUBLIC / "apple-touch-icon.png") == (180, 180)           # what iOS uses


def test_the_manifest_colours_match_the_apps_own_dark_background():
    css = (ROOT / "frontend" / "src" / "app.css").read_text()
    bg = css.split("--bg:")[1].split(";")[0].strip()
    m = json.loads((PUBLIC / "manifest.webmanifest").read_text())
    assert m["background_color"] == m["theme_color"] == bg


def test_the_page_links_the_manifest_and_icons_and_is_not_zoom_locked():
    html = (ROOT / "frontend" / "index.html").read_text()
    for needle in ('rel="manifest" href="/manifest.webmanifest"', 'rel="apple-touch-icon" href="/apple-touch-icon.png"',
                   'name="theme-color"', "viewport-fit=cover"):
        assert needle in html, needle
    assert "user-scalable=no" not in html and "maximum-scale" not in html      # pinch-zoom stays available (accessibility)


def test_the_committed_icons_are_exactly_what_the_script_draws():
    spec = importlib.util.spec_from_file_location("make_icons", ROOT / "scripts" / "make_icons.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.png(192) == (PUBLIC / "icon-192.png").read_bytes()
    assert mod.png(180) == (PUBLIC / "apple-touch-icon.png").read_bytes()
    assert mod.SVG == (PUBLIC / "icon.svg").read_text()


def test_manifest_and_icons_are_served_without_logging_in(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>")
    for name in ("manifest.webmanifest", "icon-192.png", "apple-touch-icon.png", "icon.svg"):
        (dist / name).write_bytes((PUBLIC / name).read_bytes())
    with make_client(tmp_path, lambda r: httpx.Response(404), FakeExtractor()) as (client, ctx):
        ctx.settings.frontend_dir = dist
        with TestClient(create_app(ctx)) as c:                               # never logged in
            r = c.get("/manifest.webmanifest")
            assert r.status_code == 200 and json.loads(r.text)["name"] == "newtube"
            assert c.get("/icon-192.png").content[:4] == b"\x89PNG"
            assert c.get("/apple-touch-icon.png").status_code == 200 and c.get("/icon.svg").status_code == 200
            assert c.get("/api/subscriptions").status_code == 401            # ...while the data stays behind the login
