"""Tablet Mode keeps the RecipeCove API contract and supports direct PWA links."""
from pathlib import Path
from fastapi.testclient import TestClient
import app

WEB = Path(__file__).resolve().parents[1] / "web"

def test_tablet_mode_routes_are_bookmarkable_and_assets_served():
    with TestClient(app.app) as client:
        recipe = client.get("/app/tablet/123")
        assert recipe.status_code == 200, recipe.text
        assert "RecipeCove" in recipe.text
        assert "/app/tablet-mode.js" in recipe.text
        assert "/app/tablet-mode.css" in recipe.text

        for asset, content_type in [
            ("tablet-mode.js", "text/javascript"),
            ("tablet-mode.css", "text/css"),
        ]:
            result = client.get("/app/" + asset)
            assert result.status_code == 200, result.text
            assert content_type in result.headers["content-type"]
            assert result.headers["x-content-type-options"] == "nosniff"
            assert result.text.strip()

        for invalid in ("/app/tablet/", "/app/tablet/abc",
                        "/app/tablet/123/unexpected", "/app/tablet/-1"):
            assert client.get(invalid).status_code == 404

def test_existing_recipe_routes_remain_valid():
    with TestClient(app.app) as client:
        for valid in ("/app/", "/app/recipes", "/app/recipes/7", "/app/plan",
                      "/app/shopping", "/app/about"):
            assert client.get(valid).status_code == 200
        assert client.get("/app/tablet/4").status_code == 200

def test_tablet_mode_uses_safe_dom_and_explicit_orientation_layout():
    js = (WEB / "tablet-mode.js").read_text()
    css = (WEB / "tablet-mode.css").read_text()
    app_js = (WEB / "forknook.js").read_text()
    assert "window.RecipeCoveTablet" in js
    assert "navigator.wakeLock.request('screen')" in js
    assert "document.visibilityState" in js
    assert "localStorage.setItem" in js
    assert "options.finish(id)" in js
    assert "textContent = " in js
    assert "innerHTML" not in js
    assert "(orientation:landscape)" in css
    assert "tablet-mode-active" in css
    assert "renderTablet(detail)" in app_js
