"""Image durability, family-only access, publication, and full-page view regression."""
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import forknook as base
import forknook_images as photos
import recipes


def png_bytes():
    im = Image.new('RGB', (40, 30), '#d96a46')
    o = io.BytesIO()
    im.save(o, 'PNG')
    return o.getvalue()


def test_recipe_photo_upload_and_private_public_access(tmp_path):
    with patch.object(base, 'DATA_DIR', tmp_path), patch.object(base, 'DB_FILE', tmp_path/'db.sqlite3'), patch.object(base, 'CLAIM_FILE', tmp_path/'claim'), patch.object(photos,'FOLDER',tmp_path/'recipe-images'):
        base.initialize()
        with TestClient(app.app) as c:
            signup=c.post('/api/v2/setup/complete',json={'username':'photo_admin','password':'photoadmin secret 123','setup_code':(tmp_path/'claim').read_text()})
            assert signup.status_code==200,signup.text
            h={'Authorization':'Bearer '+signup.json()['token']}
            files={'file':('meal.png',png_bytes(),'image/png')}
            assert c.post('/api/v2/images/upload',files=files).status_code==401
            image=c.post('/api/v2/images/upload',headers=h,files=files)
            assert image.status_code==201,image.text
            url=image.json()['image_url']
            assert url.startswith('/api/v2/images/')
            assert c.get(url).status_code==401
            received=c.get(url,headers=h)
            assert received.status_code==200 and received.headers['content-type']=='image/jpeg'
            saved=c.post('/api/v2/recipes',headers=h,json={'title':'Orange Pie','image_url':url,'ingredients':['pie'], 'instructions':['Bake'], 'published':True})
            assert saved.status_code==201,saved.text
            rid=saved.json()['id']
            assert c.get(f'/api/v2/recipes/{rid}',headers=h).json()['image_url']==url
            key=c.post('/api/v2/admin/api-keys',headers=h,json={'label':'My blog read access'}).json()['key']
            external={'X-Forknook-API-Key':key}
            public=c.get('/api/v2/public/recipes',headers=external)
            assert public.status_code==200
            photo_url=public.json()['items'][0]['image_url']
            assert photo_url==f'/api/v2/public/recipes/{rid}/image'
            assert c.get(photo_url).status_code==401
            assert c.get(photo_url,headers=external).headers['content-type']=='image/jpeg'
            unpublished=c.post('/api/v2/recipes',headers=h,json={'title':'Private supper','image_url':url})
            assert c.get('/api/v2/public/recipes/'+str(unpublished.json()['id'])+'/image',headers=external).status_code==404


def test_website_image_fallback_and_private_image_rejection():
    doc='''<html><meta property="og:image" content="/photos/casserole.jpg">
      <h1>Some Casserole</h1><div class="recipe-ingredients"><li>1 egg</li></div>
      <div class="recipe-instructions"><li>Cook it</li></div></html>'''
    recipe=recipes.parse_document(doc,'https://recipes.example.org/food.html')
    assert recipe.image_url=='https://recipes.example.org/photos/casserole.jpg'
    from unittest.mock import patch
    import socket
    private=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',80))]
    with patch.object(socket,'getaddrinfo',return_value=private):
        try:
            photos._public_url('http://example.com/private.png')
            assert False, 'must reject local HTTP images'
        except ValueError:pass


def test_invalid_image_not_saved(tmp_path):
    with patch.object(photos,'FOLDER',tmp_path):
        try:
            photos.store_upload(b'not-an-image')
            assert False
        except ValueError:pass
    assert list(tmp_path.iterdir())==[]


def test_full_page_navigation_and_admin_imports_in_web():
    html=Path(__file__).parents[1].joinpath('web/forknook.html').read_text()
    js=Path(__file__).parents[1].joinpath('web/forknook.js').read_text()
    assert 'async function viewRecipe(' in js
    assert "if(detail && page==='recipes')await viewRecipe(detail" in js
    assert 'recipe-hero' in js and 'ingredient-list' in js and 'instruction-list' in js
    assert 'image_url:value' in js and 'recipe-photo-file' in html
    assert '/admin/kitchenowl/import' in js and 'renderReview' in js and '/admin/api-keys' in js
    assert "'admin-imports':'/app/admin/imports'" in js
