"""Runs without network, Docker, live KitchenOwl credentials, or paid AI."""
import os
import sys
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='recipe_importer_test_')

import app
import kitchenowl
import media
import recipes
import settings_store

TOKEN = 's' * 48
HEADERS = {'Authorization': 'Bearer ' + TOKEN}


@pytest.fixture
def client():
    with TestClient(app.app) as c, patch.object(settings_store, "_config", {"kitchenowl_url": "http://127.0.0.1:8765", "llt": "testing-token-1234567890123456", "password_hash": "test"}):
        yield c


def test_health_and_web(client):
    r = client.get('/health')
    assert r.json()['version'] == '2.2.1'
    web = client.get('/')
    assert web.status_code == 200
    assert 'Forknook' in web.text
    assert 'KO-Pellet' not in web.text
    assert 'frame-ancestors' in web.headers['content-security-policy']


def test_unauthenticated_calls_blocked(client):
    for method, path in [('get', '/api/kitchenowl/households'), ('post', '/api/parse/url'),
                         ('post', '/api/parse/text'), ('post', '/api/parse/instagram'),
                         ('post', '/api/parse/video')]:
        res = getattr(client, method)(path)
        assert res.status_code == 401, (method, path, res.text)


def test_login_token_and_logout(client):
    async def fake_login(username, password):
        assert username == 'jane' and password == 'correct'
        return TOKEN
    with patch.object(kitchenowl, 'login', fake_login):
        result = client.post('/api/auth/kitchenowl', json={'username':'jane','password':'correct'})
    assert result.json()['token'] == TOKEN
    assert result.json()['success'] is True
    with patch.object(kitchenowl, 'session_info', return_value={'username':'jane'}):
        assert client.get('/api/auth/status', headers=HEADERS).json()['authenticated']
        assert client.post('/api/auth/logout', headers=HEADERS).json()['success']


def test_sessions_persist_and_logout():
    kitchenowl.initialize()
    with patch.object(settings_store, '_config', {'kitchenowl_url': 'http://127.0.0.1:8765', 'llt':'example-123456789012', 'password_hash': 'test'}), patch.object(kitchenowl, '_client') as factory:
        import httpx
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={
            'access_token': 'a' * 32,'refresh_token': 'b' * 32
        }))
        factory.side_effect = lambda: httpx.AsyncClient(transport=transport)
        import asyncio
        t = asyncio.run(kitchenowl.login('jane','pass'))
    assert kitchenowl.session_info(t)['access_token'] == 'a'*32
    assert kitchenowl.session_info(t)['username'] == 'jane'
    kitchenowl.logout(t)
    with pytest.raises(Exception): kitchenowl.session_info(t)


def test_sample_html_jsonld():
    text = '<html><script type="application/ld+json">{"@graph": [{"@type": "Recipe", "name": "Soup", "prepTime":"PT10M", "cookTime":"PT1H", "recipeIngredient":["2 cups broth"], "recipeInstructions":[{"@type":"HowToStep", "text":"Simmer."}], "image":"/soup.jpg"}]}</script></html>'
    r = recipes.parse_document(text, 'https://example.com/soup')
    assert r.title == 'Soup' and r.prep_time == 10 and r.cook_time == 60
    assert r.ingredients[0]['name'] == '2 cups broth'
    assert r.instructions == ['Simmer.']
    assert r.image_url == 'https://example.com/soup.jpg'
    assert r.confidence == 'high'


def test_manual_text_parser():
    r = recipes.manual_text('My Pasta\nIngredients\n2 cups pasta\n1 tsp salt\nInstructions\nBoil water.\nAdd pasta.')
    assert len(r.ingredients) == 2 and len(r.instructions) == 2
    assert r.confidence == 'low'


def test_url_blocks_private_and_malformed():
    for u in ('http://localhost/a', 'http://127.0.0.1/a', 'file:///etc/passwd',
              'https://internal.local/x', 'http://169.254.169.254/latest/meta-data/'):
        with pytest.raises(ValueError): recipes.validate_external_url(u)
    for u in ('https://instagram.com/reel/ABC123/?x=1',):
        assert media.validate_instagram_url(u).startswith('https://www.instagram.com/reel/')


def test_save_mapping_validates_title_and_preserves_steps():
    payload = kitchenowl.to_kitchenowl({'title':'Pasta','ingredients':[{'name':'flour','quantity':'2','unit':'cups'}],
                                        'instructions':['Boil','Stir'],'servings':'4 servings', 'total_time':25})
    assert payload['name'] == 'Pasta'
    assert payload['items'][0]['description'] == '2 cups'
    assert '1. Boil' in payload['description']
    assert payload['yields'] == 4 and payload['time'] == 25
    with pytest.raises(Exception): kitchenowl.to_kitchenowl({'title':'Untitled Recipe','ingredients':[{'name':'x'}]})


def test_authenticated_kitchenowl_routes(client):
    async def fake(*args, **kwargs):
        if args[1] == 'GET' and args[2].endswith('/recipe'):
            return [{'name': 'Pasta', 'id': 1}, {'name': 'Rice','id':2}]
        if args[1] == 'GET': return [{'id':1,'name':'Home'}]
        return {'id':99,'name':kwargs['payload']['name']}
    with patch.object(kitchenowl,'session_info',return_value={'username':'jane'}), patch.object(kitchenowl,'kitchenowl_request',fake):
        hs = client.get('/api/kitchenowl/households',headers=HEADERS)
        assert hs.json()[0]['name']=='Home'
        dup = client.get('/api/kitchenowl/check-duplicate/1?title=Pasta',headers=HEADERS).json()
        assert dup['has_duplicate']
        req={'recipe':{'title':'Pasta','ingredients':[{'name':'flour'}],'instructions':['Cook.']}}
        assert client.post('/api/kitchenowl/recipe/1',headers=HEADERS,json=req).json()['id']==99


def test_instagram_to_preview(client):
    with patch.object(kitchenowl,'session_info',return_value={}), patch.object(media,'extract_instagram',return_value={
        'text':'My Pasta\nIngredients\n2 cups flour\nInstructions\nMix thoroughly.',
        'source_url':'https://www.instagram.com/reel/ABC123/',
        'warning':'Review transcription' }):
        r=client.post('/api/parse/instagram',headers=HEADERS,json={'url':'https://instagram.com/reel/ABC123/'})
    assert r.status_code==200, r.text
    body=r.json()
    assert body['source_type']=='instagram' and 'ingredients' in body['fields_needing_review']
    assert body['source_url'].startswith('https://www.instagram.com')


def test_image_rejects_nonimage(client):
    with patch.object(kitchenowl,'session_info',return_value={}):
        r=client.post('/api/parse/image',headers=HEADERS,files={'file':('bad.txt',b'xyz','text/plain')})
        assert r.status_code==415


def test_short_video_ocr():
    import shutil
    if not all(shutil.which(s) for s in ('ffmpeg','ffprobe','tesseract')):
        pytest.skip('Media utilities not available')
    with tempfile.TemporaryDirectory() as d:
        video=Path(d)/'test.mp4'
        result=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi',
            '-i','color=c=white:s=1280x720:d=3:r=2','-vf',
            "drawtext=text='2 CUPS FLOUR':fontcolor=black:fontsize=96:x=140:y=250",
            '-c:v','libx264','-pix_fmt','yuv420p','-y',str(video)],capture_output=True)
        if result.returncode: pytest.skip('FFmpeg lacks drawtext or libx264')
        assert 'CUPS FLOUR' in media.ocr_video(video,Path(d))
