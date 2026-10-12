"""KitchenOwl API, per-device sessions, and automatically rotated KitchenOwl credentials.

Only opaque random Recipe Importer tokens leave the server.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException
import settings_store

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))

_LOCK = threading.RLock()
_TOKEN_REFRESH_GUARD = asyncio.Lock()


def initialize() -> None:
    settings_store.initialize()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA_DIR, 0o700)
    with _db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            access_token TEXT NOT NULL,
            refresh_token TEXT,
            expires_at REAL NOT NULL,
            ko_expires_at REAL NOT NULL,
            username TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions(expires_at)")
        conn.execute("CREATE TABLE IF NOT EXISTS admin_sessions (token_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL)")
    os.chmod(DATA_DIR / "sessions.sqlite3", 0o600)


def _db():
    conn = sqlite3.connect(DATA_DIR / "sessions.sqlite3", timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(25, connect=8), follow_redirects=False, trust_env=False)


async def login(username: str, password: str) -> str:
    if not (1 <= len(username) <= 200 and 1 <= len(password) <= 1024):
        raise HTTPException(400, "Enter a username and password")
    if not settings_store.configured():
        raise HTTPException(503, "Finish the setup wizard before signing in")
    try:
        async with _client() as client:
            response = await client.post(f"{settings_store.kitchenowl_url()}/api/auth", json={
                "username": username, "password": password, "device": "Recipe Importer"
            })
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Cannot reach KitchenOwl authentication") from exc
    if response.status_code in (400, 401, 403):
        raise HTTPException(401, "Invalid KitchenOwl credentials")
    if response.status_code != 200:
        raise HTTPException(502, f"KitchenOwl login returned HTTP {response.status_code}")
    try:
        data = response.json()
        access, refresh = data["access_token"], data.get("refresh_token")
        if not isinstance(access, str) or not access:
            raise ValueError("Missing access token")
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "Unexpected KitchenOwl login response") from exc
    token = secrets.token_urlsafe(48)
    now = time.time()
    with _LOCK, _db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                     (_key(token), access, refresh, now + 30 * 86400, now + 14 * 60, username))
    return token


def logout(token: str) -> None:
    with _LOCK, _db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_key(token),))
        conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (_key(token),))


def create_admin_session() -> str:
    token = secrets.token_urlsafe(48)
    with _LOCK, _db() as conn:
        conn.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (time.time(),))
        conn.execute("INSERT INTO admin_sessions VALUES (?, ?)", (_key(token), time.time() + 30 * 86400))
    return token


def clear_user_sessions() -> None:
    with _LOCK, _db() as conn:
        conn.execute("DELETE FROM sessions")


def session_info(token: str) -> dict:
    if not token or len(token) > 256:
        raise HTTPException(401, "Authentication required")
    with _LOCK, _db() as conn:
        admin = conn.execute("SELECT token_hash FROM admin_sessions WHERE token_hash = ? AND expires_at > ?",
                             (_key(token), time.time())).fetchone()
        if admin is not None:
            return {"username": "Administrator", "is_admin": True}
        entry = conn.execute("SELECT * FROM sessions WHERE token_hash = ? AND expires_at > ?",
                             (_key(token), time.time())).fetchone()
    if entry is None:
        raise HTTPException(401, "Session expired; sign in again")
    return dict(entry)


async def kitchenowl_request(token: str, method: str, path: str, *, payload: dict | None = None,
                             params: dict | None = None) -> Any:
    if not re.fullmatch(r"/api/[a-zA-Z0-9/_-]+", path):
        raise HTTPException(400, "Invalid KitchenOwl API path")
    session = session_info(token)
    if session.get("is_admin"):
        return await _admin_kitchenowl_request(method, path, payload=payload, params=params)
    if session["ko_expires_at"] - time.time() < 90:
        # A serialized refresh guards against two simultaneous refresh requests rotating the same token.
        async with _TOKEN_REFRESH_GUARD:
            session = session_info(token)
            if session["ko_expires_at"] - time.time() < 90:
                refresh = session.get("refresh_token")
                if not refresh:
                    raise HTTPException(401, "KitchenOwl session expired; sign in again")
                try:
                    async with _client() as client:
                        resp = await client.get(f"{settings_store.kitchenowl_url()}/api/auth/refresh",
                                                headers={"Authorization": f"Bearer {refresh}"})
                except httpx.HTTPError as exc:
                    raise HTTPException(502, "Cannot reach KitchenOwl token refresh") from exc
                if resp.status_code != 200:
                    logout(token)
                    raise HTTPException(401, "KitchenOwl session expired; sign in again")
                try:
                    fresh = resp.json()
                    access = fresh["access_token"]
                    if not isinstance(access, str) or not access:
                        raise ValueError("Missing token")
                    rotated = fresh.get("refresh_token") or refresh
                except (ValueError, KeyError, TypeError) as exc:
                    raise HTTPException(502, "Unexpected KitchenOwl refresh response") from exc
                with _LOCK, _db() as conn:
                    conn.execute("UPDATE sessions SET access_token=?, refresh_token=?, ko_expires_at=? WHERE token_hash=?",
                                 (access, rotated, time.time()+14*60, _key(token)))
                session = session_info(token)
    try:
        async with _client() as client:
            response = await client.request(method, f"{settings_store.kitchenowl_url()}{path}",
                                            headers={"Authorization": f"Bearer {session['access_token']}"},
                                            json=payload, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Cannot reach KitchenOwl") from exc
    if response.status_code == 401:
        raise HTTPException(401, "KitchenOwl rejected the session; reconnect this device")
    if response.status_code >= 400:
        raise HTTPException(502, f"KitchenOwl returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(502, "KitchenOwl returned invalid JSON") from exc



async def _admin_kitchenowl_request(method: str, path: str, *, payload: dict | None = None,
                                    params: dict | None = None) -> Any:
    if not settings_store.configured():
        raise HTTPException(503, "Setup is required")
    try:
        async with _client() as client:
            response = await client.request(method, settings_store.kitchenowl_url() + path,
                headers={"Authorization": "Bearer " + settings_store.kitchenowl_llt()},
                json=payload, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Cannot reach KitchenOwl") from exc
    if response.status_code in (401, 403):
        raise HTTPException(401, "KitchenOwl rejected the configured LLT; reconnect in Settings")
    if response.status_code >= 400:
        raise HTTPException(502, f"KitchenOwl returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(502, "KitchenOwl returned invalid JSON") from exc


def to_kitchenowl(recipe: dict) -> dict:
    title = str(recipe.get("title") or "").strip()
    if not (1 <= len(title) <= 300) or title.lower() in ("untitled recipe", "review this video recipe"):
        raise HTTPException(422, "Enter a descriptive recipe title")
    ingredients = recipe.get("ingredients") or []
    instructions = recipe.get("instructions") or []
    if not isinstance(ingredients, list) or not isinstance(instructions, list):
        raise HTTPException(422, "Ingredients and instructions must be lists")
    if len(ingredients) > 200 or len(instructions) > 100 or not (ingredients or instructions):
        raise HTTPException(422, "Add 1–200 ingredients or 1–100 instructions")
    items = []
    for ing in ingredients:
        if not isinstance(ing, dict):
            raise HTTPException(422, "Invalid ingredient")
        name = str(ing.get("name") or "").strip()
        if not name or len(name) > 500:
            raise HTTPException(422, "Every ingredient needs a name under 500 characters")
        qualifiers = [str(ing.get(k) or "").strip() for k in ("quantity", "unit", "note")]
        items.append({"name": name, "optional": bool(ing.get("optional", False)),
                      "description": " ".join(p for p in qualifiers if p)})
    description = str(recipe.get("description") or "").strip()[:4000]
    steps = [str(x).strip() for x in instructions if str(x).strip()]
    if any(len(x) > 3000 for x in steps):
        raise HTTPException(422, "Instruction step is too long")
    if steps:
        description += ("\n\n" if description else "") + "## Instructions\n\n" + "\n".join(f"1. {s}" for s in steps)
    notes = str(recipe.get("notes") or "").strip()
    if len(notes) > 8000:
        raise HTTPException(422, "Notes are too long")
    if notes:
        description += ("\n\n" if description else "") + "## Notes\n\n" + notes
    payload: dict[str, Any] = {"name": title, "description": description, "items": items}
    for field, target in (("prep_time", "prep_time"), ("cook_time", "cook_time"), ("total_time", "time")):
        value = recipe.get(field)
        if value is not None and value != "":
            try:
                number = int(value)
                if not 0 <= number <= 100000: raise ValueError()
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, f"Invalid {field}") from exc
            payload[target] = number
    if not payload.get("time"):
        payload["time"] = payload.get("prep_time", 0) + payload.get("cook_time", 0)
    servings = str(recipe.get("servings") or "").strip()
    match = re.match(r"^(\d+)", servings)
    if match:
        payload["yields"] = int(match.group(1))
    for field, dest in (("source_url", "source"), ("image_url", "photo")):
        val = str(recipe.get(field) or "").strip()
        if val.startswith(("https://", "http://")) and len(val) <= 2000:
            payload[dest] = val
    return payload
