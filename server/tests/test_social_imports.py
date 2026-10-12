"""Regression tests for Facebook/Instagram video routing and honest extract states."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import kitchenowl
import media
import recipes
import settings_store

TOKEN = 'social-test-token'
HEADERS = {'Authorization': 'Bearer ' + TOKEN}
FACEBOOK = 'https://www.facebook.com/mrslacysmagnoliahouse/videos/2937484513286051/'
INSTAGRAM = 'https://www.instagram.com/reel/ABC123xyz/'


@pytest.fixture
def client():
    with TestClient(app.app) as c, patch.object(settings_store, '_config', {
        'kitchenowl_url': 'http://127.0.0.1:8765', 'llt': 'testing-long-lived-token', 'password_hash': 'test',
    }), patch.object(kitchenowl, 'session_info', return_value={'username':'test'}):
        yield c


def test_facebook_video_url_recognized_and_normalized():
    assert media.social_provider(FACEBOOK) == 'facebook'
    assert media.validate_facebook_url(FACEBOOK + '?utm_source=copy') == FACEBOOK
    assert media.social_provider('https://fb.watch/Abc12345/') == 'facebook'
    assert media.social_provider('https://m.facebook.com/watch/?v=2937484513286051') == 'facebook'
    assert media.validate_facebook_url('https://facebook.com/share/v/AbcDE1234/') == 'https://www.facebook.com/share/v/AbcDE1234/'
    assert media.validate_facebook_url('https://facebook.com/reel/2937484513286051/') == 'https://www.facebook.com/reel/2937484513286051/'
    assert media.social_provider(INSTAGRAM) == 'instagram'
    assert media.social_provider('https://somefoodblog.com/recipe') is None


@pytest.mark.parametrize('bad', [
    'https://evilfacebook.com/reel/2937484513286051',
    'http://www.facebook.com/reel/2937484513286051',
    'https://www.facebook.com.evil.com/reel/2937484513286051',
    'https://user:pass@www.facebook.com/reel/2937484513286051',
    'https://www.facebook.com:8080/reel/2937484513286051',
    'https://www.facebook.com/myprofile',
    'https://www.facebook.com/groups/any/private',
])
def test_facebook_blocks_invalid_and_non_video_links(bad):
    if 'evilfacebook.com' in bad or 'facebook.com.evil.com' in bad:
        assert media.social_provider(bad) is None
    else:
        with pytest.raises(ValueError):
            media.validate_facebook_url(bad)


def test_generic_url_dispatches_facebook_video_not_webpage(client):
    info = {
        'text': 'Budget crockpot chicken\nIngredients\n2 pounds chicken\n1 cup broth\nInstructions\nAdd everything to the slow cooker.\nCook for 4 hours.',
        'source_url': FACEBOOK,
        'warning': 'Verify video recipe quantities',
    }
    with patch.object(media, 'extract_facebook', return_value=info) as video, patch.object(recipes, 'page_html') as webpage:
        res = client.post('/api/parse/url', headers=HEADERS, json={'url':FACEBOOK})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body['source_type'] == 'facebook'
    assert len(body['ingredients']) == 2
    assert len(body['instructions']) == 2
    assert body['source_url'] == FACEBOOK
    assert body['requires_manual_review'] is False
    assert '2 pounds chicken' in body['evidence']
    video.assert_called_once_with(FACEBOOK)
    webpage.assert_not_called()


def test_generic_url_dispatches_instagram(client):
    with patch.object(media, 'extract_instagram', return_value={
        'text':'Pasta\nIngredients\n2 cups flour\nInstructions\nMix thoroughly.',
        'source_url':INSTAGRAM,'warning':'Check audio.'}) as extractor:
        res=client.post('/api/parse/url',headers=HEADERS,json={'url':INSTAGRAM})
    assert res.status_code == 200
    assert res.json()['source_type'] == 'instagram'
    extractor.assert_called_once_with(INSTAGRAM)


def test_unstructured_video_never_claims_successful_recipe(client):
    with patch.object(media, 'extract_facebook', return_value={
        'text':'Crock-pot dinner\nPOST CAPTION / DESCRIPTION:\nTry this amazing slow cooker chicken!',
        'source_url': FACEBOOK, 'warning':'Audio was unavailable.'}):
        r=client.post('/api/parse/url',headers=HEADERS,json={'url':FACEBOOK})
    assert r.status_code == 200
    data=r.json()
    assert data['requires_manual_review'] is True
    assert data['ingredients'] == [] and data['instructions'] == []
    assert 'Try this amazing' in data['evidence']
    assert 'Extracted source text' in data['notes']  # Older Android app can still see evidence.


def test_facebook_video_extraction_fails_explicitly_if_site_blocks(client):
    with patch.object(media, 'extract_facebook', side_effect=ValueError('Facebook did not provide the video')):
        r=client.post('/api/parse/url',headers=HEADERS,json={'url':FACEBOOK})
    assert r.status_code == 422
    assert 'did not provide the video' in r.json()['detail']


def test_social_malformed_url_does_not_fall_back_to_web_scrape(client):
    with patch.object(recipes, 'page_html') as get:
        r=client.post('/api/parse/url',headers=HEADERS,json={'url':'https://www.facebook.com/myprofile'})
    assert r.status_code==422
    get.assert_not_called()


def test_nonrecipe_blog_returns_error_instead_of_title_only(client):
    with patch.object(recipes, 'page_html', return_value=('<html><title>Popular post</title></html>','https://blog.example.com/post')):
        r=client.post('/api/parse/url',headers=HEADERS,json={'url':'https://blog.example.com/post'})
    assert r.status_code==422
    assert 'did not expose recipe ingredients' in r.json()['detail']


def test_facebook_yt_dlp_download_then_video_pipeline_uses_real_file():
    calls=[]
    def fake_cmd(argv, **kwargs):
        calls.append(argv)
        if '--dump-single-json' in argv:
            return SimpleNamespace(stdout=json.dumps({'title':'Chicken', 'description':'Ingredients\n2 cups broth\nInstructions\nCook everything.'}))
        path=Path(argv[argv.index('--output')+1].replace('%(ext)s','mp4'))
        path.write_bytes(b'fakevideo')
        return SimpleNamespace(stdout='')
    with patch.object(media,'run_command',side_effect=fake_cmd), patch.object(media,'process_video',return_value={'text':'Chicken recipe','source_url':FACEBOOK}) as process:
        result=media.extract_facebook(FACEBOOK)
    assert result['source_url']==FACEBOOK
    assert any('--dump-single-json' in c for c in calls)
    assert any('--output' in c for c in calls)
    assert process.call_args.args[0].name=='source.mp4'


def test_facebook_yt_dlp_failure_returns_explicit_error_not_title():
    with patch.object(media,'run_command',side_effect=ValueError('yt-dlp error')):
        with pytest.raises(ValueError,match='did not provide the video'):
            media.extract_facebook(FACEBOOK)


def test_facebook_yt_dlp_alternate_watch_url_when_page_metadata_blocked():
    urls=[]
    def fake_cmd(argv, **kwargs):
        if '--dump-single-json' in argv:
            url=argv[-1]; urls.append(url)
            if '/videos/' in url: raise ValueError('blocked')
            return SimpleNamespace(stdout=json.dumps({'description':'Ingredients\n2 cups chicken\nInstructions\nCook.'}))
        output=Path(argv[argv.index('--output')+1].replace('%(ext)s','mp4'))
        output.write_bytes(b'fakevideo')
        return SimpleNamespace(stdout='')
    with patch.object(media,'run_command',side_effect=fake_cmd), patch.object(media,'process_video',return_value={'text':'ok'}):
        media.extract_facebook(FACEBOOK)
    assert len(urls)==2
    assert urls[-1]=='https://www.facebook.com/watch/?v=2937484513286051'


def test_bundled_web_interface_shows_source_evidence_and_facebook_support(client):
    html=client.get('/').text
    assert 'Facebook video' in html
    assert 'id="evidence-box"' in html
    assert 'parsed.requires_manual_review' in html
    assert "path=url?'/api/parse/url'" in html
