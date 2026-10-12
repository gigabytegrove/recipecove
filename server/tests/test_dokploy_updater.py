"""Secure Forknook updates: raw releases, image deployment and error diagnostics."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import forknook as base
import forknook_deploy as deploy
import updater


@pytest.fixture
def auth(tmp_path,monkeypatch):
    monkeypatch.setattr(base,'DATA_DIR',tmp_path)
    monkeypatch.setattr(base,'DB_FILE',tmp_path/'forknook.sqlite3')
    monkeypatch.setattr(base,'CLAIM_FILE',tmp_path/'.claim')
    monkeypatch.setattr(deploy,'DATA',tmp_path/'updates')
    monkeypatch.setattr(deploy,'CONFIG',deploy.DATA/'dokploy.json')
    base._ATTEMPTS.clear()
    with TestClient(app.app) as client:
        setup=client.post('/api/v2/setup/complete',json={'username':'admin','password':'a nice secure password 234',
                           'setup_code':base.CLAIM_FILE.read_text()})
        assert setup.status_code==200,setup.text
        yield client,{'Authorization':'Bearer '+setup.json()['token']}


def test_dokploy_config_is_secret_and_admin_only(auth):
    client,admin=auth
    assert client.get('/api/v2/admin/updates/config').status_code==401
    saved=client.put('/api/v2/admin/updates/config',headers=admin,json={
        'url':'https://dokploy.example.org','compose_id':'forknook-abc','api_key':'this-is-a-private-example-token'})
    assert saved.status_code==200,saved.text
    assert saved.json()=={'configured':True,'url':'https://dokploy.example.org','compose_id':'forknook-abc'}
    assert 'this-is-a-private' not in json.dumps(saved.json())
    assert deploy.CONFIG.stat().st_mode & 0o077==0
    # Changing an endpoint must not silently reuse a key and send it elsewhere.
    moved=client.put('/api/v2/admin/updates/config',headers=admin,json={
        'url':'https://attacker.example.org','compose_id':'forknook-abc'})
    assert moved.status_code==422
    assert deploy.current()['url']=='https://dokploy.example.org'
    assert client.delete('/api/v2/admin/updates/config',headers=admin).json()['configured'] is False


def test_dokploy_requires_safe_origin():
    for url in ('http://public.example.org','http://8.8.8.8:3000','http://user:pass@192.168.1.1:3000',
                'http://192.168.1.1:3000/api/compose.deploy'):
        with pytest.raises(HTTPException):
            deploy._origin(url)
    assert deploy._origin('http://192.168.1.14:3000')=='http://192.168.1.14:3000'


@pytest.mark.asyncio
async def test_dokploy_detects_cached_image_redeploy(tmp_path,monkeypatch):
    monkeypatch.setattr(deploy,'DATA',tmp_path)
    monkeypatch.setattr(deploy,'CONFIG',tmp_path/'dokploy.json')
    deploy.configure('https://dokploy.example.org','forknook-123','safe-access-token-1234')
    seen=[]
    def respond(request):
        seen.append((request.method,request.url.path,request.headers.get('x-api-key'),request.content))
        if request.url.path.endswith('/compose.one'):
            return httpx.Response(200,json={'composeId':'forknook-123','composeFile':
                'services:\n  forknook:\n    image: ghcr.io/gigabytegrove/forknook:latest\n    pull_policy: always\n    volumes: [forknook-data:/data]\n'})
        if request.url.path.endswith('/compose.deploy'):
            return httpx.Response(200,json={})
        return httpx.Response(404)
    original=httpx.AsyncClient
    def factory(*args,**kwargs):
        return original(*args,transport=httpx.MockTransport(respond),**kwargs)
    with patch.object(deploy.httpx,'AsyncClient',side_effect=factory):
        assert (await deploy.verify())['ready'] is True
        assert (await deploy.deploy('2.1.3'))['started'] is True
    assert seen[-1][1]=='/api/compose.deploy'
    assert json.loads(seen[-1][3])['freshVolumes'] is False
    assert all(x[2]=='safe-access-token-1234' for x in seen)
    # Cached images must not be offered as a successful update.
    with patch.object(deploy.httpx,'AsyncClient',side_effect=lambda *a,**k: original(*a,transport=httpx.MockTransport(
      lambda request:httpx.Response(200,json={'composeFile':'services:\n  forknook:\n    image: ghcr.io/gigabytegrove/forknook:latest'})),**k)):
        with pytest.raises(HTTPException,match='pull_policy'):
            await deploy.verify()


def test_update_check_degrades_gracefully_when_release_fails(tmp_path,monkeypatch):
    monkeypatch.setattr(updater,'ACTIVE',tmp_path/'absent.json')
    monkeypatch.setattr(updater,'_IMAGE_VERSION','2.1.0')
    def fake(url,size=None):
        if url==updater.API: raise OSError('GitHub release request failed')
        if url==updater.IMAGE_METADATA: return b'{"version":"2.1.3","requires_image":true}'
        raise AssertionError(url)
    monkeypatch.setattr(updater,'_https_get',fake)
    monkeypatch.setattr(updater,'_published_image',lambda v: v=='2.1.3')
    result=updater.check()
    assert result['image_available'] and result['latest_image_version']=='2.1.3'
    assert not result['available']
    assert 'Could not read published code' in result['code_error']


def test_unexpected_500_has_support_reference(auth):
    client,headers=auth
    # Inject a known-failing database function; no exception text reaches client.
    with patch.object(base,'database',side_effect=RuntimeError('secret exception with internal path')):
        safe=TestClient(app.app,raise_server_exceptions=False)
        response=safe.get('/api/v2/recipes',headers=headers)
        assert response.status_code==500
        text=response.json()['detail']
        assert 'Reference:' in text and 'secret exception' not in text
