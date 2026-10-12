"""Small, Docker-independent GitHub Release updater for the server's Python/UI layer.

Runs code from an atomic persisted directory and asks the stable /app/launcher.py
process to restart it. Updates that modify the Dockerfile, dependencies or the
launcher are deliberately refused: those need a new Docker image deployment.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import re
import secrets
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import HTTPException

DATA = Path(os.environ.get('DATA_DIR', '/data')) / 'updates'
BASE = Path('/app')
REPO = 'gigabytegrove/recipe-importer'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
ACTIVE = DATA / 'active.json'
STATUS = DATA / 'status.json'
REQUEST = DATA / 'restart.request'
_IMAGE_VERSION = os.environ.get('RI_IMAGE_VERSION', '1.4.0')
_LOCK = threading.Lock()
_MAX_PACKAGE = 6 * 1024 * 1024
_ALLOWED = re.compile(r'^(?:[a-z_][a-z0-9_]*\.py|web/[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*|requirements\.txt|Dockerfile|update_release\.json)$')


def _atomic(path: Path, content: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(content), encoding='utf-8')
    os.replace(tmp, path)


def _status(phase: str, progress: int, message: str, **more):
    _atomic(STATUS, {'phase': phase, 'progress': progress, 'message': message,
                     'updated_at': time.time(), **more})


def state() -> dict:
    try:
        return json.loads(STATUS.read_text())
    except (FileNotFoundError, ValueError, OSError):
        return {'phase': 'idle', 'progress': 0, 'message': 'Ready to check for updates'}


def installed() -> dict:
    try:
        return json.loads(ACTIVE.read_text())
    except (FileNotFoundError, ValueError, OSError):
        return {'version': _IMAGE_VERSION, 'release': None}


def _semver(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError('Unsupported release version')
    return tuple(int(n) for n in match.groups())


def _https_get(url: str, size: int = _MAX_PACKAGE) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'RecipeImporterUpdater/1.4', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=40) as response:
        # GitHub release asset URLs redirect to GitHub's asset CDN.
        result = response.read(size + 1)
        if len(result) > size:
            raise ValueError('Update response exceeds maximum size')
        return result


def _release() -> dict:
    info = json.loads(_https_get(API, 250_000))
    version = info.get('tag_name', '')
    _semver(version)
    assets = info.get('assets', [])
    prefix = f'recipe-importer-server-{version.lstrip("v")}'
    files = {asset.get('name'): asset.get('browser_download_url') for asset in assets if isinstance(asset, dict)}
    bundle = files.get(prefix + '.tar.gz')
    checksum = files.get(prefix + '.sha256')
    if not bundle or not checksum:
        raise ValueError('Latest release is missing its required server update files')
    # Do not accept external download URLs from API response; validate asset paths.
    root = f'https://github.com/{REPO}/releases/download/{version}/'
    for url in (bundle, checksum):
        if not isinstance(url, str) or not url.startswith(root):
            raise ValueError('Unexpected release asset URL')
    return {'version': version.lstrip('v'), 'tag': version, 'bundle': bundle,
            'checksum': checksum, 'url': info.get('html_url', '')}


def check() -> dict:
    remote = _release()
    local = installed()
    remote['current_version'] = local.get('version', _IMAGE_VERSION)
    remote['available'] = _semver(remote['version']) > _semver(remote['current_version'])
    # Keep sensitive download URLs out of frontend data.
    return {'current_version': remote['current_version'], 'latest_version': remote['version'],
            'available': remote['available'], 'release_url': remote['url'],
            'note': 'In-app updates cover Python/UI files. Base image and dependency changes require Docker redeployment.'}


def _verified_files(release: dict) -> dict[str, bytes]:
    _status('downloading', 20, 'Downloading server update from GitHub Releases')
    checksum = _https_get(release['checksum'], 300).decode('ascii').strip()
    if not re.fullmatch(r'[0-9a-fA-F]{64}', checksum):
        raise ValueError('Invalid release checksum')
    blob = _https_get(release['bundle'])
    if hashlib.sha256(blob).hexdigest().lower() != checksum.lower():
        raise ValueError('Release checksum mismatch; update refused')
    _status('verifying', 48, 'Verifying release compatibility and file contents')
    entries: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as tar:
        for member in tar.getmembers():
            if not member.isfile() or not _ALLOWED.fullmatch(member.name):
                raise ValueError('Unsafe or unexpected file in release package')
            if member.size > 1024 * 1024 or member.name in entries or len(entries) >= 40:
                raise ValueError('Update package contains oversized or duplicate files')
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError('Invalid update package')
            entries[member.name] = stream.read(1024 * 1024 + 1)
    needed = {'app.py', 'recipes.py', 'media.py', 'kitchenowl.py', 'settings_store.py',
              'transcribe_worker.py', 'updater.py', 'web/index.html',
              'requirements.txt', 'Dockerfile', 'launcher.py', 'update_release.json'}
    if not needed.issubset(entries):
        raise ValueError('Incomplete server update bundle')
    metadata = json.loads(entries['update_release.json'])
    if metadata.get('version') != release['version']:
        raise ValueError('Release version does not match update contents')
    for file in ('requirements.txt', 'Dockerfile', 'launcher.py'):
        if entries[file] != (BASE / file).read_bytes():
            raise ValueError(f'This release changes {file}; update the Docker image via Dokploy instead')
    return entries


def _install():
    try:
        _status('checking', 5, 'Checking the latest published version')
        release = _release()
        previous = installed()
        if _semver(release['version']) <= _semver(previous['version']):
            _status('ready', 100, 'Already on the latest version', version=previous['version'])
            return
        files = _verified_files(release)
        sha = hashlib.sha256(('v' + release['version'] + '\n').encode() +
                             hashlib.sha256(files['app.py']).digest()).hexdigest()[:40]
        target = DATA / 'releases' / sha
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = DATA / ('staging-' + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for name, content in files.items():
                file = stage / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(content)
            # Check compilation BEFORE switching away from the working version.
            import ast
            for name, content in files.items():
                if name.endswith('.py'):
                    ast.parse(content.decode('utf-8'), filename=name)
            if target.exists():
                # A previously verified bundle may be reused after rollback.
                if not (target / 'app.py').is_file():
                    raise ValueError('Incomplete existing release directory; update refused')
            else:
                os.replace(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        _status('installing', 82, 'Update verified and installed; preparing application restart')
        _atomic(ACTIVE, {'version': release['version'], 'release': sha, 'previous': previous})
        REQUEST.write_text('restart\n', encoding='utf-8')
        _status('restarting', 95, 'Restart requested; verifying the updated server')
    except Exception as exc:
        _status('failed', 0, str(exc)[:350])
    finally:
        _LOCK.release()


def install() -> dict:
    if not _LOCK.acquire(blocking=False):
        raise HTTPException(409, 'An update is already in progress')
    threading.Thread(target=_install, daemon=True, name='recipe-importer-updater').start()
    return {'started': True}


def rollback() -> dict:
    if not _LOCK.acquire(blocking=False):
        raise HTTPException(409, 'An update is already in progress')
    try:
        current = installed()
        previous = current.get('previous')
        if not isinstance(previous, dict):
            raise HTTPException(409, 'No previously installed release to restore')
        _atomic(ACTIVE, previous)
        _status('restarting', 95, 'Rolling back to previous server version')
        REQUEST.write_text('restart\n', encoding='utf-8')
        return {'restarting': True}
    finally:
        _LOCK.release()
