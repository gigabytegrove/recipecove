"""First boot setup, shared LLT permissions, admin settings and restart persistence."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import kitchenowl
import settings_store


def setup_isolated_store(tmp_path):
    for name, value in (('DATA_DIR', tmp_path), ('KEY_FILE', tmp_path / '.configuration.key'),
                        ('CONFIG_FILE', tmp_path / 'configuration.enc'), ('CLAIM_FILE', tmp_path / '.setup-code')):
        setattr(settings_store, name, value)
    kitchenowl.DATA_DIR = tmp_path
    kitchenowl.initialize()


def mock_ko(request):
    assert request.headers['Authorization'] == 'Bearer test-long-lived-token-ABCXYZ'
    if request.url.path == '/api/user':
        return httpx.Response(200, json={'id': 1})
    if request.url.path == '/api/household':
        return httpx.Response(200, json=[{'id':5,'name':'Home'}])
    if request.url.path.endswith('/recipe'):
        if request.method == 'GET':return httpx.Response(200, json=[])
        return httpx.Response(200, json={'id': 33, 'name': 'Lunch'})
    return httpx.Response(404)


def test_first_boot_setup_claim_persists_and_is_not_repeatable(tmp_path):
    real_async_client = httpx.AsyncClient
    with patch.object(settings_store, 'DATA_DIR', tmp_path), patch.object(settings_store, 'KEY_FILE', tmp_path/'.configuration.key'), patch.object(settings_store, 'CONFIG_FILE', tmp_path/'configuration.enc'), patch.object(settings_store, 'CLAIM_FILE', tmp_path/'.setup-code'), patch.object(kitchenowl, 'DATA_DIR', tmp_path):
        setup_isolated_store(tmp_path)
        assert not settings_store.configured()
        code = settings_store.CLAIM_FILE.read_text()
        assert code and not settings_store.CONFIG_FILE.exists()
        with TestClient(app.app) as client:
            assert client.get('/health').json()['status'] == 'ok'
            assert client.get('/api/setup/status').json() == {'configured': False}
            assert client.post('/api/auth/kitchenowl', json={'username':'x','password':'x'}).status_code == 503
            assert client.post('/api/setup/complete', json={'setup_code':'wrong', 'kitchenowl_url':'http://localhost:8080','kitchenowl_llt':'test-long-lived-token-ABCXYZ','admin_password':'a-strong-password!'}).status_code == 403
            def make_client(*args, **kwargs):
                kwargs['transport'] = httpx.MockTransport(mock_ko)
                return real_async_client(*args, **kwargs)
            with patch.object(settings_store.httpx, 'AsyncClient', side_effect=make_client):
                r = client.post('/api/setup/test', json={'setup_code':code,'kitchenowl_url':'http://localhost:8080','kitchenowl_llt':'test-long-lived-token-ABCXYZ'})
                assert r.status_code == 200, r.text
                assert r.json()['households'] == 1
                r = client.post('/api/setup/complete', json={'setup_code':code,'kitchenowl_url':'http://localhost:8080','kitchenowl_llt':'test-long-lived-token-ABCXYZ','admin_password':'a-strong-password!', 'openai_api_key': 'sk-fake-private-key'})
                assert r.status_code == 200, r.text
            token = r.json()['token']
            assert settings_store.configured()
            assert not settings_store.CLAIM_FILE.exists()
            assert b'test-long-lived-token-ABCXYZ' not in settings_store.CONFIG_FILE.read_bytes()
            assert b'sk-fake-private-key' not in settings_store.CONFIG_FILE.read_bytes()
            assert client.get('/api/setup/status').json() == {'configured': True}
            assert client.get('/api/admin/settings').status_code == 401
            assert client.get('/api/admin/settings', headers={'Authorization': f'Bearer {token}'}).json()['kitchenowl_url'] == 'http://localhost:8080'
            assert client.get('/api/auth/status',headers={'Authorization':f'Bearer {token}'}).json()['is_admin'] is True
            assert client.post('/api/setup/complete', json={'setup_code':code,'kitchenowl_url':'http://localhost:8080','kitchenowl_llt':'test-long-lived-token-ABCXYZ','admin_password':'a-strong-password!'}).status_code == 409
            assert client.post('/api/admin/login',json={'password':'incorrect'}).status_code == 401
            assert client.post('/api/admin/login',json={'password':'a-strong-password!'}).status_code == 200
            assert client.post('/api/admin/settings',json={'kitchenowl_url':'http://localhost:8080','kitchenowl_llt':''}).status_code == 401
            with patch.object(settings_store.httpx,'AsyncClient', side_effect=make_client):
                saved = client.post('/api/admin/settings',json={'kitchenowl_url':'http://localhost:8080','kitchenowl_llt':''},headers={'Authorization':f'Bearer {token}'})
                assert saved.status_code == 200, saved.text
                hs = client.get('/api/kitchenowl/households', headers={'Authorization':f'Bearer {token}'})
                assert hs.status_code == 200, hs.text
                assert hs.json()[0]['name'] == 'Home'
        # Fresh process initialization must recover persisted configuration and never regenerate claim.
        settings_store._config = {}
        settings_store.initialize()
        assert settings_store.configured()
        assert settings_store.password_valid('a-strong-password!')
        assert not settings_store.CLAIM_FILE.exists()
        assert settings_store.ai_api_key() == 'sk-fake-private-key'
        assert os.stat(settings_store.CONFIG_FILE).st_mode & 0o077 == 0
        assert os.stat(settings_store.KEY_FILE).st_mode & 0o077 == 0


def test_bad_url_is_rejected():
    for value in ('file:///etc/passwd', 'https://admin:pass@example.com/', 'http://example.com/api',
                  'https://example.com?x=1', 'ftp://example.com', 'http://example.com:bad'):
        try:
            settings_store.validate_url(value)
            assert False, value
        except Exception:
            pass


def test_setup_isolated_from_recipe_routes_when_unconfigured(tmp_path):
    with patch.object(settings_store,'DATA_DIR', tmp_path), patch.object(settings_store,'KEY_FILE', tmp_path/'.configuration.key'),patch.object(settings_store,'CONFIG_FILE', tmp_path/'configuration.enc'),patch.object(settings_store,'CLAIM_FILE', tmp_path/'.setup-code'),patch.object(kitchenowl, 'DATA_DIR', tmp_path):
        with TestClient(app.app) as client:
            assert client.get('/api/setup/status').json() == {'configured': False}
            assert client.post('/api/parse/url',headers={'Authorization':'Bearer dummy'},json={'url':'https://example.com'}).status_code == 503
