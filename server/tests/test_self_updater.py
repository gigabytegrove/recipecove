"""The Forknook updater is entirely in-process, independent of host orchestrators."""
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
import forknook as base
import updater


def test_release_check_requires_no_container_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, 'ACTIVE', tmp_path / 'absent.json')
    monkeypatch.setattr(updater, '_IMAGE_VERSION', '2.1.5')
    monkeypatch.setattr(updater, '_release', lambda: {
        'version':'2.1.6', 'url':'https://github.com/gigabytegrove/forknook/releases/tag/v2.1.6'
    })
    observed = []
    def response(url, size=None):
        observed.append(url)
        assert url.endswith('/v2.1.6/server/update_release.json')
        return json.dumps({'version':'2.1.6','requires_image':False}).encode()
    monkeypatch.setattr(updater, '_https_get', response)
    result = updater.check()
    assert result['available'] is True
    assert result['latest_version'] == '2.1.6'
    assert result['code_error'] is None
    assert 'image_available' not in result and 'latest_image_version' not in result
    assert len(observed) == 1


def test_incompatible_runtime_is_not_offered_as_in_app_update(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, 'ACTIVE', tmp_path / 'absent.json')
    monkeypatch.setattr(updater, '_IMAGE_VERSION', '2.1.5')
    monkeypatch.setattr(updater, '_release', lambda: {'version':'2.1.7','url':'https://example.org/release'})
    monkeypatch.setattr(updater, '_https_get', lambda *args: b'{"version":"2.1.7","requires_image":true}')
    result = updater.check()
    assert not result['available']
    assert result['requires_image'] is True


def test_removed_external_deployment_endpoints_and_client(tmp_path):
    with patch.object(base,'DATA_DIR',tmp_path),patch.object(base,'DB_FILE',tmp_path/'forknook.sqlite3'),patch.object(base,'CLAIM_FILE',tmp_path/'.claim'):
        former_credentials=tmp_path / 'updates' / 'dokploy.json'
        former_credentials.parent.mkdir(parents=True,exist_ok=True)
        former_credentials.write_text('{"api_key":"must-not-persist"}',encoding='utf-8')
        with TestClient(app.app) as client:
            assert not former_credentials.exists()
            created=client.post('/api/v2/setup/complete',json={
                'username':'admin','password':'a strong family password','setup_code':base.CLAIM_FILE.read_text()
            })
            assert created.status_code == 200,created.text
            headers={'Authorization':'Bearer '+created.json()['token']}
            for method,path in [
                ('get','/api/v2/admin/updates/config'),
                ('put','/api/v2/admin/updates/config'),
                ('delete','/api/v2/admin/updates/config'),
                ('post','/api/v2/admin/updates/verify'),
                ('post','/api/v2/admin/updates/install-image')
            ]:
                response=getattr(client,method)(path,headers=headers)
                assert response.status_code==404,(method,path,response.text)
            data=client.get('/api/v2/admin/updates/status',headers=headers)
            assert data.status_code==200
            assert 'dokploy' not in data.json()
            assert client.get('/api/v2/openapi.json').status_code==200
            schema=client.get('/api/v2/openapi.json').json()
            assert '/api/v2/admin/updates/install-image' not in schema['paths']


def test_updates_screen_only_offers_native_release_updater():
    script=(Path(__file__).resolve().parents[1]/'web'/'forknook.js').read_text('utf-8')
    assert 'Dokploy' not in script and 'dokploy' not in script
    assert "Update Docker image through" not in script
    assert 'Install update' in script
