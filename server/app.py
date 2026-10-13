"""Forknook: standalone family RMS with preserved legacy importer."""
from __future__ import annotations

import asyncio
import logging
import uuid
import re
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import kitchenowl
import forknook
import forknook_features
import forknook_catalog
import forknook_dataset
import media
import recipes
import settings_store
import updater

VERSION = "2.2.5"
DATA = Path(__file__).parent
_login_attempts: dict[str, deque[float]] = defaultdict(deque)


@asynccontextmanager
async def lifespan(app: FastAPI):
    kitchenowl.initialize()
    forknook.initialize()
    forknook_features.initialize()
    # Purge credentials from the removed platform-specific update integration.
    # This affects no family recipes, users, or updater release state.
    (Path(os.environ.get('DATA_DIR', '/data')) / 'updates' / 'dokploy.json').unlink(missing_ok=True)
    yield


app = FastAPI(title="Forknook", version=VERSION, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    # Avoid leaking tokens, internal filenames or tracebacks to family clients.
    # Container logs retain the traceback and correlation ID for diagnosis.
    ref=uuid.uuid4().hex[:12]
    logging.getLogger('forknook.errors').exception(
        'Unexpected server error %s %s [%s]',request.method,request.url.path,ref,
        exc_info=(type(exc),exc,exc.__traceback__))
    return JSONResponse(status_code=500,content={
        'detail':f'Forknook encountered a server error. Reference: {ref}',
        'error_ref':ref})



class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


class SetupBody(BaseModel):
    setup_code: str = Field(min_length=1, max_length=128)
    kitchenowl_url: str = Field(min_length=7, max_length=2048)
    kitchenowl_llt: str = Field(min_length=12, max_length=4096)
    admin_password: str = Field(min_length=12, max_length=1024)
    openai_api_key: str = Field(default="", max_length=2048)
    openai_model: str = Field(default="gpt-4o-mini", min_length=2, max_length=80)


class ConnectionCheck(BaseModel):
    setup_code: str = Field(default="", max_length=128)
    kitchenowl_url: str = Field(min_length=7, max_length=2048)
    kitchenowl_llt: str = Field(min_length=12, max_length=4096)


class AdminLogin(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class AdminSettings(BaseModel):
    kitchenowl_url: str = Field(min_length=7, max_length=2048)
    kitchenowl_llt: str = Field(default="", max_length=4096)
    openai_api_key: str | None = Field(default=None, max_length=2048)
    openai_model: str | None = Field(default=None, max_length=80)
    new_admin_password: str | None = Field(default=None, max_length=1024)


class RecipeBody(BaseModel):
    recipe: recipes.Recipe


class ReelBody(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


def token_from_header(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in to Recipe Importer", headers={"WWW-Authenticate": "Bearer"})
    token = authorization[7:]
    if not settings_store.configured():
        raise HTTPException(503, "Complete the setup wizard first")
    kitchenowl.session_info(token)
    return token


def require_admin(token: str = Depends(token_from_header)) -> str:
    if not kitchenowl.session_info(token).get("is_admin"):
        raise HTTPException(403, "Administrator access required")
    return token


def guard_login(ip: str) -> None:
    now = time.monotonic()
    # Bound memory of the IP map, using direct peer IP, not client-spoofable forwarded headers.
    if len(_login_attempts) > 10000:
        for key, times in list(_login_attempts.items()):
            if not times or times[-1] + 600 < now:
                del _login_attempts[key]
    attempts = _login_attempts[ip]
    while attempts and attempts[0] < now - 600: attempts.popleft()
    if len(attempts) >= 10:
        raise HTTPException(429, "Too many login attempts; retry later")
    attempts.append(now)


@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION, "image_version": os.environ.get("RI_IMAGE_VERSION", "unknown"), "kitchenowl_configured": settings_store.configured()}


@app.get("/")
def home():
    # Forknook is the primary app on both fresh and upgraded installations.
    # The original KitchenOwl importer remains reachable at /legacy.
    return FileResponse(DATA / "web" / "forknook.html", media_type="text/html", headers=_FORK_HEADERS)


@app.get("/api/setup/status")
def setup_status():
    return settings_store.public_status()


@app.post("/api/setup/test")
async def setup_test(body: ConnectionCheck, request: Request):
    guard_login(request.client.host if request.client else "unknown")
    settings_store.check_claim(body.setup_code)
    return await settings_store.test_connection(body.kitchenowl_url, body.kitchenowl_llt)


@app.post("/api/setup/complete")
async def setup_complete(body: SetupBody, request: Request):
    guard_login(request.client.host if request.client else "unknown")
    settings_store.check_claim(body.setup_code)
    tested = await settings_store.test_connection(body.kitchenowl_url, body.kitchenowl_llt)
    # Serialize concurrent claims. Recheck under the store lock to prevent double initialization.
    with settings_store._LOCK:
        settings_store.check_claim(body.setup_code)
        settings_store.save(url=body.kitchenowl_url, llt=body.kitchenowl_llt,
                            admin_password=body.admin_password, ai_key=body.openai_api_key,
                            ai_model_name=body.openai_model)
        kitchenowl.clear_user_sessions()
        token = kitchenowl.create_admin_session()
    return {"success": True, "connected": tested["connected"], "token": token}


@app.post("/api/admin/login")
async def admin_login(body: AdminLogin, request: Request):
    guard_login(request.client.host if request.client else "unknown")
    if not settings_store.configured():
        raise HTTPException(503, "Complete the setup wizard first")
    if not settings_store.password_valid(body.password):
        raise HTTPException(401, "Incorrect administrator password")
    return {"success": True, "token": kitchenowl.create_admin_session()}


@app.get("/api/admin/settings")
def admin_settings(token: str = Depends(require_admin)):
    return settings_store.admin_status()


@app.post("/api/admin/test")
async def admin_test(body: ConnectionCheck, token: str = Depends(require_admin)):
    return await settings_store.test_connection(body.kitchenowl_url, body.kitchenowl_llt)


@app.get("/api/admin/updates/status")
def update_status(token: str = Depends(require_admin)):
    return {'installed': updater.installed(), 'operation': updater.state()}


@app.post("/api/admin/updates/check")
async def update_check(token: str = Depends(require_admin)):
    try:
        return await run_in_threadpool(updater.check)
    except Exception as exc:
        raise HTTPException(502, 'Cannot check GitHub Releases: ' + str(exc)[:180]) from exc


@app.post("/api/admin/updates/install", status_code=202)
def update_install(token: str = Depends(require_admin)):
    return updater.install()


@app.post("/api/admin/updates/rollback", status_code=202)
def update_rollback(token: str = Depends(require_admin)):
    return updater.rollback()


@app.post("/api/admin/settings")
async def update_settings(body: AdminSettings, token: str = Depends(require_admin)):
    llt = body.kitchenowl_llt.strip() or settings_store.kitchenowl_llt()
    result = await settings_store.test_connection(body.kitchenowl_url, llt)
    settings_store.save(url=body.kitchenowl_url, llt=llt, ai_key=body.openai_api_key,
                        ai_model_name=body.openai_model,
                        admin_password=body.new_admin_password or None)
    kitchenowl.clear_user_sessions()  # Changed KitchenOwl URLs must not leave stale user sessions.
    return {"success": True, "connected": result["connected"]}


@app.post("/api/auth/kitchenowl")
async def login(body: LoginBody, request: Request):
    guard_login(request.client.host if request.client else "unknown")
    token = await kitchenowl.login(body.username, body.password)
    return {"success": True, "token": token}


@app.get("/api/auth/status")
def status(token: str = Depends(token_from_header)):
    session = kitchenowl.session_info(token)
    return {"authenticated": True, "user": session["username"], "is_admin": bool(session.get("is_admin"))}


@app.post("/api/auth/logout")
def logout(token: str = Depends(token_from_header)):
    kitchenowl.logout(token)
    return {"success": True}


@app.get("/api/kitchenowl/households")
async def households(token: str = Depends(token_from_header)):
    return await kitchenowl.kitchenowl_request(token, "GET", "/api/household")


@app.get("/api/kitchenowl/check-duplicate/{household_id}")
async def duplicates(household_id: int, title: str, token: str = Depends(token_from_header)):
    if household_id < 1 or not title or len(title) > 300:
        raise HTTPException(400, "Invalid household or recipe title")
    results = await kitchenowl.kitchenowl_request(token, "GET", f"/api/household/{household_id}/recipe", params={"search": title})
    if not isinstance(results, list): raise HTTPException(502, "Unexpected KitchenOwl recipe list")
    target = title.strip().casefold()
    matches = [r for r in results if isinstance(r, dict) and
               (r.get("name") or "").strip().casefold() == target]
    return {"has_duplicate": bool(matches), "matches": matches[:20]}


@app.post("/api/kitchenowl/recipe/{household_id}")
async def create_recipe(household_id: int, body: RecipeBody, token: str = Depends(token_from_header)):
    if household_id < 1: raise HTTPException(400, "Invalid household")
    payload = kitchenowl.to_kitchenowl(body.recipe.model_dump())
    return await kitchenowl.kitchenowl_request(token, "POST", f"/api/household/{household_id}/recipe", payload=payload)


@app.post("/api/parse/text", response_model=recipes.Recipe)
async def parse_text(body: recipes.ParseRequest, token: str = Depends(token_from_header)):
    try: return await recipes.parse_text(body.text or "")
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc


@app.post("/api/parse/url", response_model=recipes.Recipe)
async def parse_url(body: recipes.ParseRequest, token: str = Depends(token_from_header)):
    if not body.url: raise HTTPException(422, "Missing URL")
    try:
        platform = media.social_provider(body.url)
        if platform:
            extractor = media.extract_facebook if platform == "facebook" else media.extract_instagram
            info = await run_in_threadpool(media.run_exclusive, extractor, body.url)
            return await _complete_media(info, platform)
        html, source = await recipes.page_html(body.url)
        parsed = recipes.parse_document(html, source)
        if not parsed.ingredients and not parsed.instructions:
            raise ValueError("This webpage did not expose recipe ingredients or directions. Paste the recipe text or share a screenshot instead.")
        return parsed
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc


@app.post("/api/parse/image", response_model=recipes.Recipe)
async def parse_image(file: UploadFile = File(...), token: str = Depends(token_from_header)):
    if file.content_type not in ("image/png", "image/jpeg", "image/webp"):
        raise HTTPException(415, "Use a JPG, PNG, or WebP image")
    try:
        data = await file.read(10 * 1024 * 1024 + 1)
        if len(data) > 10 * 1024 * 1024: raise HTTPException(413, "Image exceeds 10 MB")
        try: return await recipes.parse_image(data)
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    finally: await file.close()


async def _complete_media(info: dict, source_type: str) -> recipes.Recipe:
    result = await recipes.parse_text(info["text"], source_url=info.get("source_url", ""), source_type=source_type)
    warning = info.get("warning", "")
    result.evidence = info["text"][:recipes.MAX_TEXT]
    result.image_url = info.get("image_url") or result.image_url
    result.extraction = info.get("extraction", {})
    if result.extraction.get("speech") not in (None, "Transcribed spoken words"):
        detail = "Video speech: " + result.extraction["speech"]
        result.notes = (result.notes + "\n\n" if result.notes else "") + detail
    if not (result.ingredients or result.instructions):
        result.requires_manual_review = True
        result.confidence = "low"
        warning = "Video/post was read, but no structured ingredients or instructions were recognized. Review the extracted source text and enter missing details before saving. " + warning
        # Legacy Android apps have no dedicated source-text panel yet.
        result.notes = (result.notes + "\n\n" if result.notes else "") + "Extracted source text (edit/remove before saving):\n" + result.evidence[:18000]
    result.notes = (result.notes + "\n\n" if result.notes else "") + warning
    if "ingredients" not in result.fields_needing_review: result.fields_needing_review.append("ingredients")
    if "instructions" not in result.fields_needing_review: result.fields_needing_review.append("instructions")
    return result


@app.post("/api/parse/instagram", response_model=recipes.Recipe)
async def parse_instagram(body: ReelBody, token: str = Depends(token_from_header)):
    try:
        source = media.validate_instagram_url(body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    info = await run_in_threadpool(media.run_exclusive, media.extract_instagram, source)
    return await _complete_media(info, "instagram")


@app.post("/api/parse/facebook", response_model=recipes.Recipe)
async def parse_facebook(body: ReelBody, token: str = Depends(token_from_header)):
    try:
        source = media.validate_facebook_url(body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    info = await run_in_threadpool(media.run_exclusive, media.extract_facebook, source)
    return await _complete_media(info, "facebook")


@app.post("/api/parse/video", response_model=recipes.Recipe)
async def parse_video(file: UploadFile = File(...), token: str = Depends(token_from_header)):
    if file.content_type is None or not (file.content_type.startswith("video/") or file.content_type == "application/octet-stream"):
        raise HTTPException(415, "Share a video file")
    try:
        info = await run_in_threadpool(media.run_exclusive, media.process_local_file, file)
        return await _complete_media(info, "video")
    finally: await file.close()


# Native Forknook RMS lives alongside the original import endpoints so existing
# deployments and mobile clients continue to function during migration.
app.include_router(forknook.router)
app.include_router(forknook_features.router)
app.include_router(forknook_catalog.router)
app.include_router(forknook_dataset.router)

@app.get('/api/v2/openapi.json',include_in_schema=False)
def forknook_openapi():
    """Stable v2 contract for Android and future WordPress integrations."""
    routes=[r for r in app.routes if getattr(r,'path','').startswith('/api/v2/') and
            getattr(r,'include_in_schema',False)]
    return get_openapi(title='Forknook API v2',version=VERSION,routes=routes)


_FORK_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob: https:; connect-src 'self'; manifest-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
}

@app.get("/app")
@app.get("/app/")
def forknook_pwa():
    return FileResponse(DATA / "web" / "forknook.html",media_type="text/html",headers=_FORK_HEADERS)

@app.get("/app/{asset_name}")
def forknook_asset(asset_name:str):
    names={"forknook.css":"text/css","forknook.js":"text/javascript",
           "sw.js":"text/javascript", "manifest.json":"application/manifest+json",
           "icon-192.png":"image/png","icon-512.png":"image/png",
           "forknook-logo.png":"image/png"}
    if asset_name not in names:
        if _FORK_PATH.fullmatch(asset_name):
            return FileResponse(DATA / "web" / "forknook.html", media_type="text/html", headers=_FORK_HEADERS)
        raise HTTPException(404,"Not found")
    return FileResponse(DATA / "web" / asset_name,media_type=names[asset_name],
                        headers={"Cache-Control":"no-cache" if asset_name=="sw.js" else "no-store",
                                 "X-Content-Type-Options":"nosniff"})

# Bookmarked and directly loaded SPA routes must return the same app shell.
# Reject any unknown path instead of making every URL return HTTP 200.
_FORK_PATH = re.compile(r"(?:home|recipes(?:/[0-9]+)?|plan|shopping(?:/[0-9]+)?|sources(?:/[0-9]+)?|discover|review|categories|admin(?:/(?:users|backups|updates|ai|imports|review|api))?)")

@app.get("/app/{client_path:path}")
def forknook_route(client_path: str):
    if not _FORK_PATH.fullmatch(client_path):
        raise HTTPException(404, "Unknown Forknook page")
    return FileResponse(DATA / "web" / "forknook.html", media_type="text/html", headers=_FORK_HEADERS)

@app.get("/legacy")
def legacy_importer():
    return FileResponse(DATA / "web" / "index.html",media_type="text/html",headers=_FORK_HEADERS)
