"""Shared RecipeCove folders may organize existing recipes without modifying their content."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app
import forknook as base


@pytest.fixture
def kitchen(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setattr(base, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(base, 'DB_FILE', tmp_path/'forknook.sqlite3')
    monkeypatch.setattr(base, 'CLAIM_FILE', tmp_path/'.claim')
    with TestClient(app.app) as client:
        login=client.post('/api/v2/setup/complete',json={
            'username':'admin',
            'password':'a long, secure admin password',
            'setup_code':base.CLAIM_FILE.read_text()})
        assert login.status_code==200,login.text
        yield client, {'Authorization':'Bearer '+login.json()['token']}


def test_folders_preserve_recipes_and_support_many_to_many_membership(kitchen):
    client,auth=kitchen
    # Existing recipes have no folder property: these must remain readable.
    before=client.post('/api/v2/recipes',headers=auth,json={'title':'Existing recipe'})
    assert before.status_code==201,before.text
    legacy_id=before.json()['id']
    assert client.get('/api/v2/recipes/'+str(legacy_id),headers=auth).json().get('folder_ids',[])==[]
    first=client.post('/api/v2/folders',headers=auth,json={'name':"Jen's Favorites"})
    second=client.post('/api/v2/folders',headers=auth,json={'name':'Holiday Baking'})
    assert first.status_code==201 and second.status_code==201,(first.text,second.text)
    a,b=first.json()['id'],second.json()['id']
    category=client.post('/api/v2/categories',headers=auth,json={'name':'Dessert','color':'#537d56'})
    assert category.status_code==201,category.text
    cid=category.json()['id']
    created=client.post('/api/v2/recipes',headers=auth,json={
        'title':'Cookies','description':'From our family kitchen',
        'category_id':cid,'folder_ids':[a,b]})
    assert created.status_code==201,created.text
    rid=created.json()['id']
    recipe=client.get('/api/v2/recipes/'+str(rid),headers=auth).json()
    assert recipe['folder_ids']==[a,b] and recipe['category_id']==cid
    assert [x['id'] for x in client.get('/api/v2/recipes',headers=auth,params={'folder_id':a}).json()]==[rid]
    assert [x['id'] for x in client.get('/api/v2/recipes',headers=auth,params={'folder_id':b,'category_id':cid}).json()]==[rid]
    assert client.get('/api/v2/folders',headers=auth).json()[0]['recipe_count']==1
    duplicate=client.post('/api/v2/folders',headers=auth,json={'name':"jen's favorites"})
    assert duplicate.status_code==409
    renamed=client.put('/api/v2/folders/'+str(a),headers=auth,json={'name':'Always Make Again'})
    assert renamed.status_code==200
    assert a in client.get('/api/v2/recipes/'+str(rid),headers=auth).json()['folder_ids']
    deleted=client.delete('/api/v2/folders/'+str(a),headers=auth)
    assert deleted.status_code==200 and deleted.json()['recipes_preserved']
    restored=client.get('/api/v2/recipes/'+str(rid),headers=auth).json()
    assert restored['folder_ids']==[b] and restored['title']=='Cookies'
    assert restored['description']=='From our family kitchen'
    assert client.get('/api/v2/recipes/'+str(legacy_id),headers=auth).status_code==200
    assert len(client.get('/api/v2/recipes',headers=auth).json())==2


def test_folders_enforce_valid_membership_without_mutating_saved_recipe(kitchen):
    client,auth=kitchen
    valid=client.post('/api/v2/folders',headers=auth,json={'name':'Dinner'}).json()['id']
    recipe=client.post('/api/v2/recipes',headers=auth,json={'title':'Soup','folder_ids':[valid]})
    assert recipe.status_code==201,recipe.text
    rid=recipe.json()['id']
    missing=client.put('/api/v2/recipes/'+str(rid),headers=auth,json={
        'title':'Changed','folder_ids':[99999]})
    assert missing.status_code==422
    duplicate=client.put('/api/v2/recipes/'+str(rid),headers=auth,json={
        'title':'Changed','folder_ids':[valid,valid]})
    assert duplicate.status_code==422
    after=client.get('/api/v2/recipes/'+str(rid),headers=auth).json()
    assert after['title']=='Soup' and after['folder_ids']==[valid]
    assert client.get('/api/v2/folders').status_code==401
    assert client.post('/api/v2/folders',json={'name':'Unauthorized'}).status_code==401
