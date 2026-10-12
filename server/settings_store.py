"""Persist server configuration securely in /data; no Docker secrets required.

A generated one-time setup code prevents first visitor from claiming an installation.
LLT and optional AI key are encrypted at rest using a locally generated key.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "configuration.enc"
KEY_FILE = DATA_DIR / ".configuration.key"
CLAIM_FILE = DATA_DIR / ".setup-code"
_LOCK = threading.RLock()
_config: dict = {}
_cipher: Fernet | None = None
_LOG = logging.getLogger("recipe_importer.setup")


def _write_private(path: Path, data: bytes) -> None:
    # Atomic replace and 0600 prevent partial configuration after power loss.
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def initialize() -> None:
    global _config, _cipher
    with _LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(DATA_DIR, 0o700)
        if KEY_FILE.exists():
            key = KEY_FILE.read_bytes().strip()
        else:
            if CONFIG_FILE.exists():
                raise RuntimeError("Missing configuration encryption key; restore the /data backup")
            key = Fernet.generate_key()
            _write_private(KEY_FILE, key)
        _cipher = Fernet(key)
        if CONFIG_FILE.exists():
            try:
                _config = json.loads(_cipher.decrypt(CONFIG_FILE.read_bytes()))
                if not isinstance(_config, dict):
                    raise ValueError("Invalid configuration")
            except (InvalidToken, ValueError, UnicodeDecodeError, TypeError) as exc:
                raise RuntimeError("Cannot read encrypted configuration; restore the /data backup") from exc
            # No new setup code is ever generated after successful setup.
            CLAIM_FILE.unlink(missing_ok=True)
        else:
            _config = {}
            if not CLAIM_FILE.exists():
                _write_private(CLAIM_FILE, secrets.token_urlsafe(24).encode("ascii"))
            _LOG.warning("Recipe Importer first-run setup code: %s", CLAIM_FILE.read_text().strip())


def configured() -> bool:
    with _LOCK:
        return bool(_config.get("kitchenowl_url") and _config.get("llt") and _config.get("password_hash"))


def kitchenowl_url() -> str:
    with _LOCK:
        return _config.get("kitchenowl_url", "")


def kitchenowl_llt() -> str:
    with _LOCK:
        return _config.get("llt", "")


def ai_api_key() -> str:
    with _LOCK:
        return _config.get("openai_api_key", "")


def ai_model() -> str:
    with _LOCK:
        return _config.get("openai_model", "gpt-4o-mini")


def public_status() -> dict:
    # Do not leak internal IP addresses to unauthenticated callers.
    return {"configured": configured()}


def admin_status() -> dict:
    with _LOCK:
        return {"configured": configured(), "kitchenowl_url": kitchenowl_url(),
                "llt_configured": bool(kitchenowl_llt()), "openai_key_configured": bool(ai_api_key()),
                "openai_model": ai_model()}


def _password_digest(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 400_000).hex()


def password_valid(password: str) -> bool:
    with _LOCK:
        salt_hex = _config.get("password_salt", "")
        digest = _config.get("password_hash", "")
    if not salt_hex or not digest or not password or len(password) > 1024:
        return False
    result = _password_digest(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(result, digest)


def check_claim(code: str) -> None:
    if configured() or not CLAIM_FILE.is_file():
        raise HTTPException(409, "Recipe Importer has already been configured")
    if not isinstance(code, str) or not hmac.compare_digest(code.strip(), CLAIM_FILE.read_text().strip()):
        raise HTTPException(403, "Incorrect one-time setup code; check the container logs")


def validate_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 2048 or "\n" in url or "\r" in url:
        raise HTTPException(422, "Invalid KitchenOwl URL")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(422, "Invalid KitchenOwl URL") from exc
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path not in ("", "/") or port == 0):
        raise HTTPException(422, "Enter a full http(s) KitchenOwl origin without a path or credentials")
    return url.strip().rstrip("/")


async def test_connection(url: str, llt: str) -> dict:
    safe_url = validate_url(url)
    if not isinstance(llt, str) or not (12 <= len(llt.strip()) <= 4096):
        raise HTTPException(422, "Enter a valid KitchenOwl long-lived token")
    headers = {"Authorization": "Bearer " + llt.strip(), "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=5), trust_env=False, follow_redirects=False) as client:
            user = await client.get(safe_url + "/api/user", headers=headers)
            if user.status_code in (401, 403):
                raise HTTPException(422, "KitchenOwl rejected the long-lived token")
            if user.status_code != 200:
                raise HTTPException(502, f"KitchenOwl /api/user returned HTTP {user.status_code}")
            households = await client.get(safe_url + "/api/household", headers=headers)
            if households.status_code in (401, 403):
                raise HTTPException(422, "Token cannot access KitchenOwl households")
            if households.status_code != 200:
                raise HTTPException(502, f"KitchenOwl /api/household returned HTTP {households.status_code}")
            data = households.json()
            if not isinstance(data, list):
                raise ValueError("Not a household list")
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise HTTPException(502, "Could not connect to KitchenOwl at that URL") from exc
    except ValueError as exc:
        raise HTTPException(502, "KitchenOwl returned an unexpected household response") from exc
    return {"connected": True, "households": len(data)}


def save(*, url: str, llt: str, admin_password: str | None = None,
         ai_key: str | None = None, ai_model_name: str | None = None) -> None:
    global _config
    with _LOCK:
        if _cipher is None:
            raise RuntimeError("Settings store not initialized")
        saved = dict(_config)
        saved["kitchenowl_url"] = validate_url(url)
        saved["llt"] = llt.strip()
        if admin_password is not None:
            if not 12 <= len(admin_password) <= 1024:
                raise HTTPException(422, "Administrator password must contain at least 12 characters")
            salt = secrets.token_bytes(24)
            saved["password_salt"] = salt.hex()
            saved["password_hash"] = _password_digest(admin_password, salt)
        if ai_key is not None:
            if len(ai_key) > 2048:
                raise HTTPException(422, "OpenAI API key is too long")
            saved["openai_api_key"] = ai_key.strip()
        if ai_model_name is not None:
            if not re.fullmatch(r"[A-Za-z0-9._-]{2,80}", ai_model_name):
                raise HTTPException(422, "Invalid AI model name")
            saved["openai_model"] = ai_model_name
        if not all(saved.get(k) for k in ("kitchenowl_url", "llt", "password_hash")):
            raise HTTPException(422, "Complete all required setup fields")
        _write_private(CONFIG_FILE, _cipher.encrypt(json.dumps(saved, separators=(",", ":")).encode()))
        _config = saved
        CLAIM_FILE.unlink(missing_ok=True)
