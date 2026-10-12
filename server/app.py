"""Recipe Importer: one-process recipe processing server and KitchenOwl integration."""
from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import kitchenowl
import media
import recipes

VERSION = "1.1.1"
DATA = Path(__file__).parent
_login_attempts: dict[str, deque[float]] = defaultdict(deque)


@asynccontextmanager
async def lifespan(app: FastAPI):
    kitchenowl.initialize()
    yield


app = FastAPI(title="Recipe Importer", version=VERSION, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


class RecipeBody(BaseModel):
    recipe: recipes.Recipe


class ReelBody(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


def token_from_header(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in to Recipe Importer", headers={"WWW-Authenticate": "Bearer"})
    token = authorization[7:]
    kitchenowl.session_info(token)
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
    return {"status": "ok", "version": VERSION, "kitchenowl_configured": bool(kitchenowl.KITCHENOWL_URL)}


@app.get("/")
def home():
    return FileResponse(DATA / "web" / "index.html", media_type="text/html", headers={
        "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})


@app.post("/api/auth/kitchenowl")
async def login(body: LoginBody, request: Request):
    guard_login(request.client.host if request.client else "unknown")
    token = await kitchenowl.login(body.username, body.password)
    return {"success": True, "token": token}


@app.get("/api/auth/status")
def status(token: str = Depends(token_from_header)):
    session = kitchenowl.session_info(token)
    return {"authenticated": True, "user": session["username"]}


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
        html, source = await recipes.page_html(body.url)
        return recipes.parse_document(html, source)
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


@app.post("/api/parse/video", response_model=recipes.Recipe)
async def parse_video(file: UploadFile = File(...), token: str = Depends(token_from_header)):
    if file.content_type is None or not (file.content_type.startswith("video/") or file.content_type == "application/octet-stream"):
        raise HTTPException(415, "Share a video file")
    try:
        info = await run_in_threadpool(media.run_exclusive, media.process_local_file, file)
        return await _complete_media(info, "video")
    finally: await file.close()
