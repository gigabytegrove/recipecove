"""Stable, unprivileged process manager for in-container application code updates.

This process never touches the Docker API or the image filesystem. It runs one
Uvicorn child, watches for a local update request, health-checks the replacement,
and rolls back to the previously working code on startup failure.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

DATA = Path(os.environ.get('DATA_DIR', '/data')) / 'updates'
BASE = Path(os.environ.get('RI_BASE_DIR', '/app'))
ACTIVE = DATA / 'active.json'
REQUEST = DATA / 'restart.request'
STATUS = DATA / 'status.json'
EXITING = False
LOG = logging.getLogger('recipe-importer.launcher')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def _signal(signum, frame):
    global EXITING
    EXITING = True


def _write(path: Path, value: dict) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value), encoding='utf-8')
    os.replace(tmp, path)


def _status(phase: str, pct: int, detail: str, **extra):
    value = {'phase': phase, 'progress': pct, 'message': detail, 'updated_at': time.time(), **extra}
    _write(STATUS, value)


def _location(pointer: dict | None) -> Path:
    if not pointer or not pointer.get('release'):
        return BASE
    sha = pointer['release']
    if not isinstance(sha, str) or len(sha) != 40 or any(x not in '0123456789abcdef' for x in sha):
        raise RuntimeError('Invalid active release identifier')
    path = DATA / 'releases' / sha
    if not (path / 'app.py').is_file() or not (path / 'web' / 'index.html').is_file():
        raise RuntimeError('Installed update files missing')
    return path


def _read_pointer() -> dict:
    try:
        return json.loads(ACTIVE.read_text())
    except FileNotFoundError:
        return {'release': None, 'version': os.environ.get('RI_IMAGE_VERSION', '2.0.0')}


def _ready(process: subprocess.Popen, expected: str, timeout: float = 60) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not EXITING:
        if process.poll() is not None:
            return False
        try:
            with urlopen('http://127.0.0.1:8999/health', timeout=2) as resp:
                payload = json.load(resp)
            if payload.get('status') == 'ok' and payload.get('version') == expected:
                return True
        except (OSError, ValueError):
            pass
        time.sleep(1)
    return False


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'releases').mkdir(exist_ok=True)
    previous: dict | None = None
    first = True
    while not EXITING:
        pointer = _read_pointer()
        # A Docker image upgrade must win over an older persistent in-app
        # code overlay, even if the operator redeployed through Dokploy rather
        # than our manual script. Preserve the pointer for diagnostics.
        image_version = os.environ.get('RI_IMAGE_VERSION', '2.0.0')
        if pointer.get('release') and pointer.get('version'):
            import re
            def numbers(value):
                match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value))
                return tuple(map(int, match.groups())) if match else None
            selected, image = numbers(pointer.get('version')), numbers(image_version)
            if selected and image and selected < image:
                backup = ACTIVE.with_name('active.before-image-update-' + str(int(time.time())) + '.json')
                backup.write_bytes(ACTIVE.read_bytes())
                LOG.warning('Saved old update pointer %s; newer image %s will be used', backup, image_version)
                pointer = {'release': None, 'version': image_version}
                _write(ACTIVE, pointer)
                _status('ready', 100, f'Forknook {image_version} running from newer Docker image', version=image_version)
        try:
            path = _location(pointer)
        except RuntimeError as exc:
            LOG.error('Invalid installed release: %s', exc)
            pointer = {'release': None, 'version': os.environ.get('RI_IMAGE_VERSION', '2.0.0')}
            _write(ACTIVE, pointer)
            path = BASE
        expected = pointer.get('version') or os.environ.get('RI_IMAGE_VERSION', '2.0.0')
        LOG.info('Starting Forknook %s from %s', expected, path)
        env = dict(os.environ)
        env['PYTHONPATH'] = str(path)
        proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host', '0.0.0.0',
                                 '--port', '8999', '--workers', '1'], cwd=path, env=env)
        if not _ready(proc, expected):
            if EXITING:
                _stop(proc)
                break
            LOG.error('Health check failed for %s', expected)
            _stop(proc)
            if pointer.get('release'):
                recovery = pointer.get('previous')
                if not isinstance(recovery, dict):
                    recovery = {'release': None, 'version': os.environ.get('RI_IMAGE_VERSION', '2.0.0')}
                _write(ACTIVE, recovery)
                _status('rolled_back', 100, f'Update {expected} failed health check; previous version restored')
                continue
            _status('failed', 0, 'The base Docker image failed to start. Check container logs.')
            return 1
        if not first or STATUS.exists():
            _status('ready', 100, f'Forknook {expected} running', version=expected)
        first = False
        while not EXITING:
            if proc.poll() is not None:
                LOG.error('Application exited unexpectedly: %s', proc.returncode)
                return proc.returncode or 1
            if REQUEST.exists():
                REQUEST.unlink(missing_ok=True)
                _status('restarting', 95, 'Restarting Forknook; saved configuration is preserved')
                _stop(proc)
                break
            time.sleep(.4)
        if EXITING:
            _stop(proc)
            break
    return 0


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)
    sys.exit(main())
