"""Private, disk-backed Forknook recipe photographs. Never proxy arbitrary URL requests.

The parser can supply an external source image, but the saved recipe points to
our own /data image, avoiding expired/blocked remote hotlinks. Media access is
session-authorized; WordPress consumers can use the scoped API-key route.
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import logging
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

log = logging.getLogger(__name__)
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 26_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
FOLDER = Path(os.environ.get('DATA_DIR', '/data')) / 'recipe-images'
LOCAL_IMAGE = re.compile(r'^/api/v2/images/([0-9a-f]{64})\.jpg$')


def _store(raw: bytes) -> str:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('Recipe image must be no larger than 8 MB')
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError('Recipe image is too large')
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
            if image.mode not in ('RGB', 'L'):
                canvas = Image.new('RGB', image.size, '#ffffff')
                if image.mode == 'RGBA':
                    canvas.paste(image, mask=image.getchannel('A'))
                else:
                    canvas.paste(image.convert('RGB'))
                image = canvas
            out = io.BytesIO()
            image.convert('RGB').save(out, format='JPEG', quality=85, optimize=True)
            data = out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Uploaded content is not a supported image') from exc
    digest = hashlib.sha256(data).hexdigest()
    FOLDER.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest = FOLDER / (digest + '.jpg')
    if not dest.exists():
        temp = FOLDER / ('.' + digest + '.' + str(os.getpid()) + '.part')
        try:
            with temp.open('wb') as handle:
                os.chmod(temp, 0o600)
                handle.write(data)
            os.replace(temp, dest)
        finally:
            temp.unlink(missing_ok=True)
    return '/api/v2/images/' + digest + '.jpg'


def store_upload(raw: bytes) -> str:
    return _store(raw)


def get_file(path: str) -> Path:
    match = LOCAL_IMAGE.fullmatch(path)
    if not match:
        raise HTTPException(404, 'Unknown recipe image')
    target = FOLDER / (match.group(1) + '.jpg')
    if not target.is_file():
        raise HTTPException(404, 'Recipe image is missing')
    return target


def _public_url(raw: str) -> str:
    try:
        u = urlsplit(raw)
        port = u.port
    except ValueError as exc:
        raise ValueError('Invalid source image URL') from exc
    if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password or port not in (None, 80, 443):
        raise ValueError('Image must use a public HTTP(S) URL on the default port')
    if len(raw) > 2000 or re.search(r'[\r\n]', raw):
        raise ValueError('Invalid image URL')
    host = u.hostname
    try:
        addresses = socket.getaddrinfo(host, port or (443 if u.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError('Cannot resolve image server') from exc
    if not addresses or any(not ipaddress.ip_address(record[4][0]).is_global for record in addresses):
        raise ValueError('Image URL must resolve only to public addresses')
    return raw


def capture_url(raw: str) -> str:
    if not raw:
        return ''
    if LOCAL_IMAGE.fullmatch(raw):
        return raw
    url = _public_url(raw)
    # Redirects are deliberately not followed to avoid redirect-to-LAN SSRF.
    with httpx.Client(timeout=httpx.Timeout(16, connect=6), trust_env=False, follow_redirects=False) as client:
        with client.stream('GET', url, headers={'Accept':'image/jpeg,image/png,image/webp,image/*;q=0.7'}) as r:
            r.raise_for_status()
            if not r.headers.get('content-type','').lower().startswith('image/'):
                raise ValueError('The source did not return an image')
            data = bytearray()
            for chunk in r.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError('Recipe source image exceeds 8 MB')
    return _store(bytes(data))


def capture_best_effort(raw: str) -> str:
    if not raw:
        return ''
    if LOCAL_IMAGE.fullmatch(raw):
        return raw
    try:
        return capture_url(raw)
    except (ValueError, httpx.HTTPError, OSError) as exc:
        log.warning('Could not cache recipe photograph: %s', type(exc).__name__)
        # Keep the URL so the user may retry; don't fail recipe saving.
        return raw
