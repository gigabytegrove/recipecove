"""Updater release integrity, compatibility, permissions and rollback without network."""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app
import kitchenowl
import launcher
import updater


def make_release(server: Path, version: str = '2.1.0', change_dep=False):
    files = {}
    for path in server.glob('*.py'):
        files[path.name] = path.read_bytes()
    for path in (server/'web').rglob('*'):
        if path.is_file():
            files[path.relative_to(server).as_posix()] = path.read_bytes()
    for name in ('requirements.txt','Dockerfile','update_release.json'):
        files[name] = (server/name).read_bytes()
    files['app.py'] = files['app.py'].replace(b'VERSION = "2.0.1"', b'VERSION = "'+version.encode()+b'"')
    files['update_release.json'] = json.dumps({'version':version}).encode()
    if change_dep:
        files['requirements.txt'] += b'\nextra-package==1.0\n'
    bundle=io.BytesIO()
    with tarfile.open(fileobj=bundle,mode='w:gz') as tf:
        for name, content in files.items():
            info=tarfile.TarInfo(name)
            info.size=len(content)
            tf.addfile(info, io.BytesIO(content))
    blob = bundle.getvalue()
    tag='v'+version
    origin='https://github.com/gigabytegrove/forknook/releases/download/'+tag+'/'
    desc={'tag_name':tag,'html_url':origin,'assets':[
        {'name':f'forknook-server-{version}.tar.gz','browser_download_url':origin+f'forknook-server-{version}.tar.gz'},
        {'name':f'forknook-server-{version}.sha256','browser_download_url':origin+f'forknook-server-{version}.sha256'},
    ]}
    return desc,blob


@pytest.fixture
def patches(tmp_path,monkeypatch):
    server = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(updater,'DATA',tmp_path/'updates')
    monkeypatch.setattr(updater,'BASE',server)
    monkeypatch.setattr(updater,'ACTIVE',updater.DATA/'active.json')
    monkeypatch.setattr(updater,'STATUS',updater.DATA/'status.json')
    monkeypatch.setattr(updater,'REQUEST',updater.DATA/'restart.request')
    monkeypatch.setattr(updater,'_IMAGE_VERSION','2.0.1')
    updater.DATA.mkdir(parents=True)
    release,blob=make_release(server)
    def read(url,maxsize=None):
        if url==updater.API: return json.dumps(release).encode()
        if url.endswith('/server/update_release.json'): return json.dumps({'version':'2.1.0','requires_image':False}).encode()
        if url.endswith('.sha256'):return hashlib.sha256(blob).hexdigest().encode()
        if url.endswith('.tar.gz'):return blob
        raise RuntimeError('Unrecognized download URL')
    monkeypatch.setattr(updater,'_https_get',read)
    return server,release,blob


def test_check_release(patches):
    found=updater.check()
    assert found['available'] and found['latest_version']=='2.1.0'
    assert found['current_version']=='2.0.1'
    assert 'bundle' not in found


def test_update_bundle_integrity_and_docker_compat(patches,monkeypatch):
    server,release,blob=patches
    r=updater._release()
    result=updater._verified_files(r)
    assert result['app.py'] and b'2.1.3' in result['app.py']
    desc,altered=make_release(server,change_dep=True)
    original=updater._https_get
    monkeypatch.setattr(updater,'_https_get',lambda u, n=0: (hashlib.sha256(altered).hexdigest().encode() if u.endswith('.sha256') else altered if u.endswith('.tar.gz') else original(u,n)))
    with pytest.raises(ValueError,match='Python dependencies'):
        updater._verified_files(r)
    monkeypatch.setattr(updater,'_https_get',lambda u, n=0: b'0'*64 if u.endswith('.sha256') else original(u,n))
    with pytest.raises(ValueError,match='checksum mismatch'):
        updater._verified_files(r)


def test_install_switch_and_manual_rollback(patches):
    updater._LOCK.acquire()
    updater._LOCK.release()
    out=updater.install()
    assert out['started']
    for _ in range(120):
        if not updater._LOCK.locked():break
        time.sleep(.05)
    status=updater.state()
    assert status['phase']=='restarting',status
    pointer=updater.installed()
    assert pointer['version']=='2.1.0'
    assert pointer['previous']['version']=='2.0.1'
    assert (updater.DATA/'releases'/pointer['release']/'app.py').is_file()
    assert updater.REQUEST.exists()
    updater.REQUEST.unlink()
    assert updater.rollback()['restarting']
    assert updater.installed()['version']=='2.0.1'
    assert updater.REQUEST.exists()


def test_unauthorized_updater_endpoints(patches):
    with TestClient(app.app) as client:
        for method,path in [('get','/api/admin/updates/status'),('post','/api/admin/updates/check'),
                            ('post','/api/admin/updates/install'),('post','/api/admin/updates/rollback')]:
            assert getattr(client,method)(path).status_code==401
        with patch.object(app.settings_store,'_config',{'kitchenowl_url':'http://example','llt':'example','password_hash':'example'}), \
             patch.object(kitchenowl,'session_info',return_value={'username':'jane','is_admin':False}):
            for path in ('/api/admin/updates/check','/api/admin/updates/install','/api/admin/updates/rollback'):
                assert client.post(path,headers={'Authorization':'Bearer xyz'}).status_code==403


def test_launcher_only_runs_under_saved_release_directory(tmp_path,monkeypatch):
    monkeypatch.setattr(launcher,'DATA',tmp_path)
    monkeypatch.setattr(launcher,'BASE',Path(__file__).resolve().parents[1])
    assert launcher._location({'release':None})==launcher.BASE
    with pytest.raises(RuntimeError,match='Invalid active release'):
        launcher._location({'release':'../../../etc'})
    ident='a'*40
    path=tmp_path/'releases'/ident
    path.mkdir(parents=True)
    (path/'app.py').write_text('hello')
    (path/'web').mkdir()
    (path/'web'/'index.html').write_text('hello')
    assert launcher._location({'release':ident})==path
