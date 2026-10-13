"""Full Forknook RMS integration tests without KitchenOwl, network or Docker."""
from __future__ import annotations
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app, forknook

@pytest.fixture
def client(tmp_path):
    with patch.object(forknook,'DATA_DIR',tmp_path), patch.object(forknook,'DB_FILE',tmp_path/'forknook.sqlite3'), patch.object(forknook,'CLAIM_FILE',tmp_path/'.forknook-claim'):
        forknook._ATTEMPTS.clear()
        forknook.initialize()
        with TestClient(app.app) as c:
            # Test app initialization against the same temporary, isolated database.
            r=c.post('/api/v2/setup/complete',json={'setup_code':forknook.CLAIM_FILE.read_text(),
                  'username':'familyadmin','password':'a long testing password 123','display_name':'Family admin'})
            assert r.status_code==200,r.text
            token=r.json()['token']
            yield c,{'Authorization':'Bearer '+token}

SAMPLE={'title':'Homemade Soup','ingredients':[{'name':'2 cups broth','raw':'2 cups broth'},
        {'name':'1 onion','raw':'1 onion'}],'instructions':['Mix','Simmer'],'servings':'4'}

def test_setup_requires_code(tmp_path):
    with patch.object(forknook,'DATA_DIR',tmp_path),patch.object(forknook,'DB_FILE',tmp_path/'forknook.sqlite3'),patch.object(forknook,'CLAIM_FILE',tmp_path/'.forknook-claim'):
        forknook.initialize()
        with TestClient(app.app) as c:
            assert c.get('/api/v2/setup/status').json()['configured'] is False
            assert c.post('/api/v2/setup/complete',json={'setup_code':'wrong','username':'badname','password':'long enough password'}).status_code==403
            assert c.get('/api/v2/recipes').status_code==401

def test_native_account_and_auth(client):
    c,h=client
    assert c.get('/api/v2/auth/me',headers=h).json()['role']=='admin'
    assert c.get('/api/v2/setup/status').json()['configured']
    assert c.post('/api/v2/setup/complete',json={'setup_code':'x','username':'newadmin','password':'long enough password'}).status_code==409
    assert c.post('/api/v2/auth/login',json={'username':'familyadmin','password':'wrong'}).status_code==401
    valid=c.post('/api/v2/auth/login',json={'username':'familyadmin','password':'a long testing password 123'})
    assert valid.status_code==200
    t={'Authorization':'Bearer '+valid.json()['token']}
    assert c.get('/api/v2/auth/me',headers=t).status_code==200
    assert c.post('/api/v2/auth/logout',headers=t).status_code==200
    assert c.get('/api/v2/auth/me',headers=t).status_code==401

def test_users_roles_permissions_and_last_admin(client):
    c,h=client
    assert c.post('/api/v2/admin/users',headers=h,json={'username':'jen','display_name':'Jen','password':'a strong secret pass','role':'member'}).status_code==201
    people=c.get('/api/v2/admin/users',headers=h).json()
    assert len(people)==2
    member=c.post('/api/v2/auth/login',json={'username':'jen','password':'a strong secret pass'}).json()['token']
    m={'Authorization':'Bearer '+member}
    assert c.get('/api/v2/admin/users',headers=m).status_code==403
    assert c.patch('/api/v2/admin/users/'+str(people[0]['id']),headers=h,json={'role':'member'}).status_code==409
    assert c.patch('/api/v2/admin/users/'+str(people[1]['id']),headers=h,json={'enabled':False}).status_code==200
    assert c.get('/api/v2/auth/me',headers=m).status_code==401

def test_recipes_categories_and_states(client):
    c,h=client
    category=c.post('/api/v2/categories',headers=h,json={'name':'Family Sunday Dinner','color':'#ff3300'})
    assert category.status_code==201
    cid=category.json()['id']
    created=c.post('/api/v2/recipes',headers=h,json={**SAMPLE,'category_id':cid})
    assert created.status_code==201,created.text
    rid=created.json()['id']
    assert c.get('/api/v2/recipes',headers=h).json()[0]['category']=='Family Sunday Dinner'
    assert c.get('/api/v2/recipes?q=homemade',headers=h).json()[0]['id']==rid
    assert c.put(f'/api/v2/recipes/{rid}/state',headers=h,json={'state':'want'}).status_code==200
    assert c.get('/api/v2/recipes?state=want',headers=h).json()[0]['state']=='want'
    assert c.put(f'/api/v2/recipes/{rid}/state',headers=h,json={'state':'made'}).status_code==200
    assert c.get('/api/v2/recipes?state=made',headers=h).json()[0]['made_count']==1
    assert c.put(f'/api/v2/recipes/{rid}',headers=h,json={**SAMPLE,'title':'Soup & Toast'}).status_code==200
    assert c.get(f'/api/v2/recipes/{rid}',headers=h).json()['title']=='Soup & Toast'
    assert c.delete('/api/v2/categories/'+str(cid),headers=h).status_code==200
    assert c.get(f'/api/v2/recipes/{rid}',headers=h).json()['category_id'] is None

def test_shopping_from_multiple_recipes_and_checkoff(client):
    c,h=client
    a=c.post('/api/v2/recipes',headers=h,json=SAMPLE).json()['id']
    b=c.post('/api/v2/recipes',headers=h,json={'title':'Rice','ingredients':['1 cup rice'],'instructions':['Boil']}).json()['id']
    made=c.post('/api/v2/shopping-lists/build',headers=h,json={'recipe_ids':[a,b],'name':'Saturday groceries'})
    assert made.status_code==201,made.text
    lid=made.json()['id']
    contents=c.get(f'/api/v2/shopping-lists/{lid}',headers=h).json()
    assert len(contents['items'])==3
    it=contents['items'][0]
    assert c.patch(f'/api/v2/shopping-lists/{lid}/items/{it["id"]}',headers=h,json={'name':it['name'],'quantity':it['quantity'],'checked':True}).status_code==200
    assert c.get(f'/api/v2/shopping-lists/{lid}',headers=h).json()['items'][0]['checked']==1
    assert c.delete(f'/api/v2/shopping-lists/{lid}/items/{it["id"]}',headers=h).status_code==200
    assert len(c.get(f'/api/v2/shopping-lists/{lid}',headers=h).json()['items'])==2

def test_meal_planning(client):
    c,h=client
    rid=c.post('/api/v2/recipes',headers=h,json=SAMPLE).json()['id']
    r=c.post('/api/v2/meal-plan',headers=h,json={'day':'2026-10-14','meal':'dinner','recipe_id':rid,'servings':4})
    assert r.status_code==201,r.text
    assert c.get('/api/v2/meal-plan?start=2026-10-14&end=2026-10-14',headers=h).json()[0]['recipe_title']=='Homemade Soup'
    assert c.post('/api/v2/meal-plan',headers=h,json={'day':'bad','meal':'dinner','recipe_id':rid}).status_code==422
    assert c.delete('/api/v2/meal-plan/'+str(r.json()['id']),headers=h).status_code==200

def test_migration_dry_run_copy_and_duplicates(client):
    c,h=client
    incoming=json.dumps({'recipes':[{'name':'Old Soup','items':[{'name':'2 cups water'}],'description':'Cook and serve.'}]}).encode()
    files={'file':('kitchenowl.json',incoming,'application/json')}
    dry=c.post('/api/v2/admin/import-json?dry_run=true',headers=h,files=files)
    assert dry.status_code==200,dry.text
    assert dry.json()['would_import']==1 and c.get('/api/v2/recipes',headers=h).json()==[]
    done=c.post('/api/v2/admin/import-json?dry_run=false',headers=h,files=files)
    assert done.status_code==200 and done.json()['imported']==1,done.text
    assert c.post('/api/v2/admin/import-json?dry_run=false',headers=h,files=files).json()['skipped']==1
    assert c.get('/api/v2/admin/overview',headers=h).json()['migration_runs']==2
    assert len(c.get('/api/v2/admin/export',headers=h).json()['recipes'])==1

def test_web_pwa_assets_and_legacy(client):
    c,h=client
    assert c.get('/').status_code==200
    html=c.get('/app/')
    assert html.status_code==200 and 'Forknook' in html.text
    assert 'default-src' in html.headers['content-security-policy']
    assert c.get('/app/manifest.json').status_code==200
    assert c.get('/app/forknook.js').status_code==200
    assert c.get('/app/icon-192.png').status_code==200
    assert c.get('/app/../../etc/passwd').status_code==404
    assert c.get('/legacy').status_code==200

def test_import_preview_must_not_save(client):
    c,h=client
    with patch.object(forknook.recipes,'parse_text',return_value=forknook.recipes.Recipe(title='Cake',ingredients=[{'name':'Flour'}],instructions=['Bake'])):
        preview=c.post('/api/v2/imports/preview-text',headers=h,json={'text':'cake ingredients and steps'})
    assert preview.status_code==200,preview.text
    assert preview.json()['title']=='Cake'
    assert c.get('/api/v2/recipes',headers=h).json()==[]
    assert c.post('/api/v2/recipes',headers=h,json=preview.json()).status_code==201


def test_rss_atom_reader_no_network(client):
    c,h=client
    assert c.get('/api/v2/feeds').status_code==401
    # Never fetch internal hosts; no data leak from loopback services.
    assert c.post('/api/v2/feeds',headers=h,json={'title':'Local','url':'http://127.0.0.1/rss'}).status_code==422


def test_standalone_ai_settings_encrypted_and_admin_protected(client,tmp_path):
    c,h=client
    import settings_store
    with patch.object(settings_store,'DATA_DIR',tmp_path):
        assert c.get('/api/v2/admin/ai',headers=h).status_code==200
        saved=c.post('/api/v2/admin/ai',headers=h,json={'api_key':'sk-dummy-test-secret','model':'gpt-4o-mini'})
        assert saved.status_code==200,saved.text
        assert saved.json()=={'api_key_configured':True,'model':'gpt-4o-mini'}
        assert b'sk-dummy-test-secret' not in (tmp_path/'forknook-ai.enc').read_bytes()
        assert settings_store.ai_api_key()=='sk-dummy-test-secret'
        assert c.get('/api/v2/admin/ai').status_code==401
        assert c.post('/api/v2/admin/ai',headers=h,json={'api_key':'','model':'gpt-4o-mini'}).status_code==200
        assert not (tmp_path/'forknook-ai.enc').read_bytes().find(b'sk-dummy-test-secret')>=0


def test_database_connections_commit_and_close(client):
    c,h=client
    import sqlite3
    row=c.post('/api/v2/recipes',headers=h,json=SAMPLE)
    assert row.status_code==201
    # No active write transactions linger after the response; another connection
    # can open an immediate write transaction and commit it.
    with sqlite3.connect(forknook.DB_FILE,timeout=1) as connection:
        connection.execute('BEGIN IMMEDIATE')
        assert connection.execute('SELECT COUNT(*) FROM recipes').fetchone()[0]==1
        connection.commit()


def test_bookmarkable_pwa_routes_and_static_assets(client):
    c,headers=client
    for path in ('/app/', '/app/recipes', '/app/recipes/1', '/app/plan',
                 '/app/shopping', '/app/shopping/15', '/app/sources', '/app/sources/15',
                 '/app/categories', '/app/admin', '/app/admin/users',
                 '/app/admin/backups', '/app/admin/updates', '/app/admin/ai'):
        resp=c.get(path)
        assert resp.status_code==200,(path,resp.text)
        assert 'RecipeCove' in resp.text
        assert resp.headers['cache-control']=='no-store'
    for path in ('/app/../../etc/passwd','/app/unknown','/app/admin/failure',
                 '/app/recipes/garbage','/app/shopping/wrong'):
        assert c.get(path).status_code==404,path
    for file in ('forknook.css','forknook.js','sw.js'):
        assert c.get('/app/'+file).status_code==200
        assert c.get('/app/'+file).headers['cache-control']!='public, max-age=300'


def test_dom_regressions_for_setup_admin_navigation_spacing(client):
    c,_=client
    html=c.get('/app/').text
    css=c.get('/app/forknook.css').text
    js=c.get('/app/forknook.js').text
    assert '[hidden]' in css and 'display:none!important' in css
    assert 'id="brand-home"' in html and 'href="/app/"' in html
    assert 'pagePaths=' in js and "'/app/admin/updates'" in js
    assert "role.append(node('option'" in js
    assert "role.add(node('option',{value:'member'" not in js
    assert 'renderHome()' in js
    assert '.card{padding:clamp(' in css
