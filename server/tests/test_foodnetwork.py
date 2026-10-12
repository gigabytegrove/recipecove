"""Regression coverage for sites that reject a crawler-style request."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recipes

URL = "https://www.foodnetwork.com/recipes/aida-mollenkamp/grilled-flank-steak-with-ginger-marinade-recipe-1912292"
HTML = '<html><script type="application/ld+json">{"@type":"Recipe","name":"Grilled Flank Steak with Ginger Marinade","recipeIngredient":["1/4 cup sesame oil","8 medium garlic cloves"],"recipeInstructions":[{"@type":"HowToStep","text":"Combine marinade."}]}</script></html>'


def run_with_mock(handler, url=URL):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    with patch.object(recipes, "validate_external_url") as validator, patch.object(recipes.httpx, "AsyncClient", side_effect=lambda **opts: original(transport=transport, **opts)):
        result = asyncio.run(recipes.page_html(url))
        return result, validator.call_args_list


def test_foodnetwork_first_request_is_browser_like_and_parses_recipe():
    def handler(request):
        assert "Firefox" in request.headers["User-Agent"]
        assert "text/html" in request.headers["Accept"]
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html; charset=utf-8"})
    (html, source), checks = run_with_mock(handler)
    recipe = recipes.parse_document(html, source)
    assert recipe.title == "Grilled Flank Steak with Ginger Marinade"
    assert len(recipe.ingredients) == 2
    assert recipe.instructions == ["Combine marinade."]
    assert source == URL
    assert checks


def test_foodnetwork_retries_once_with_different_browser_profile_after_403():
    uas = []
    def handler(req):
        uas.append(req.headers["User-Agent"])
        if len(uas) == 1:
            return httpx.Response(403, text="Forbidden")
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
    (html, _), _ = run_with_mock(handler)
    assert "Recipe" in html
    assert len(uas) == 2
    assert uas[0] != uas[1]


def test_foodnetwork_persistent_403_fails_with_usable_guidance():
    attempts = []
    def handler(req):
        attempts.append(req)
        return httpx.Response(403, text="Forbidden")
    with pytest.raises(ValueError, match=r"HTTP 403.*screenshot"):
        run_with_mock(handler)
    assert len(attempts) == 2


def test_redirect_still_revalidates_target_before_fetch():
    seen=[]
    def handler(req):
        seen.append(str(req.url))
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8080/private"})
        raise AssertionError("redirect should not be followed without validation")
    original=httpx.AsyncClient
    with patch.object(recipes.httpx, "AsyncClient", side_effect=lambda **opts: original(transport=httpx.MockTransport(handler), **opts)):
        with patch.object(recipes, "validate_external_url", side_effect=lambda url: (_ for _ in ()).throw(ValueError("blocked")) if "127.0.0.1" in url else None):
            with pytest.raises(ValueError, match="blocked"):
                asyncio.run(recipes.page_html(URL))
    assert len(seen) == 1


def test_other_recipe_sites_use_browser_profile_too():
    def handler(req):
        assert "Chrome" in req.headers["User-Agent"]
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
    (_,source),_=run_with_mock(handler,"https://www.example.com/dinner")
    assert source == "https://www.example.com/dinner"
