"""Forknook household review, roles, migration and future WordPress contract."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app, forknook as base, forknook_features as ext

@pytest.fixture
def family(tmp_path):
    with patch.object(base,'DATA_DIR',tmp_path),patch.object(base,'DB_FILE',tmp_path/'forknook.sqlite3'),patch.object(base,'CLAIM_FILE',tmp_path/'.claim'):
        base._ATTEMPTS.clear();base.initialize();ext.initialize()
        with TestClient(app.app) as client:
            claim=base.CLAIM_FILE.read_text()
            done=client.post('/api/v2/setup/complete',json={'username':'root','password':'root strong secret 123','setup_code':claim})
            assert done.status_code==200,done.text
            admin={'Authorization':'Bearer '+done.json()['token']}
            roles={}
            for role in ('approver','contributor','viewer'):
                username={'approver':'jen','contributor':'brad','viewer':'visitor'}[role]
                result=client.post('/api/v2/admin/users',headers=admin,json={'username':username,'password':'a very strong password','role':role})
                assert result.status_code==201,result.text
                session=client.post('/api/v2/auth/login',json={'username':username,'password':'a very strong password'})
                roles[role]={'Authorization':'Bearer '+session.json()['token']}
            yield client,admin,roles

SAMPLE={'title':'Dinner Potatoes','ingredients':[{'name':'Potatoes','raw':'Potatoes'}],'instructions':['Bake for 40 minutes']}

def test_default_categories_and_roles(family):
    client,admin,roles=family
    cats=client.get('/api/v2/categories',headers=admin).json()
    assert {'Dinner','Breakfast','Desserts','Slow Cooker','Side Dishes'}.issubset({c['name'] for c in cats})
    assert client.post('/api/v2/categories',headers=roles['viewer'],json={'name':'Private'}).status_code==403
    people=client.get('/api/v2/admin/users',headers=admin).json()
    assert {p['role'] for p in people}=={'admin','approver','contributor','viewer'}
    assert client.get('/api/v2/admin/users',headers=roles['approver']).status_code==403
    assert client.post('/api/v2/recipes',headers=roles['viewer'],json=SAMPLE).status_code==403


def test_approval_end_to_end_no_premature_recipe(family):
    c,admin,r=family
    assert c.put('/api/v2/admin/family/settings',headers=admin,json={'require_approval':True}).status_code==200
    sent=c.post('/api/v2/recipes',headers=r['contributor'],json=SAMPLE)
    assert sent.status_code==201,sent.text
    assert sent.json()['pending'] is True
    assert c.get('/api/v2/recipes',headers=admin).json()==[]
    queue=c.get('/api/v2/submissions',headers=r['approver']).json()
    assert len(queue)==1 and queue[0]['title']=='Dinner Potatoes'
    assert c.get('/api/v2/submissions',headers=r['viewer']).json()==[]
    sid=sent.json()['id']
    assert c.post(f'/api/v2/submissions/{sid}/approve',headers=r['contributor'],json={}).status_code==403
    approved=c.post(f'/api/v2/submissions/{sid}/approve',headers=r['approver'],json={'reason':'Good dinner'})
    assert approved.status_code==200,approved.text
    assert len(c.get('/api/v2/recipes',headers=admin).json())==1
    assert c.post(f'/api/v2/submissions/{sid}/approve',headers=admin,json={}).status_code==409
    # A contributor cannot edit a previously approved recipe to bypass review.
    assert c.put('/api/v2/recipes/'+str(approved.json()['recipe_id']),headers=r['contributor'],json=SAMPLE).status_code==403


def test_reject_opt_in_and_switch_off(family):
    c,admin,r=family
    member=r['contributor']
    assert c.put('/api/v2/me/preferences',headers=member,json={'submit_for_review':True}).status_code==200
    a=c.post('/api/v2/recipes',headers=member,json=SAMPLE)
    assert a.json()['pending']
    assert c.post(f'/api/v2/submissions/{a.json()["id"]}/reject',headers=r['approver'],json={}).status_code==422
    assert c.post(f'/api/v2/submissions/{a.json()["id"]}/reject',headers=r['approver'],json={'reason':'Missing quantities'}).status_code==200
    assert c.get('/api/v2/submissions?status=rejected',headers=member).json()[0]['decision_note']=='Missing quantities'
    assert c.put('/api/v2/me/preferences',headers=member,json={'submit_for_review':False}).status_code==200
    b=c.post('/api/v2/recipes',headers=member,json={**SAMPLE,'title':'Other recipe'})
    assert b.status_code==201 and b.json().get('pending') is None
    assert c.put('/api/v2/admin/family/settings',headers=member,json={'require_approval':True}).status_code==403


def test_wordpress_api_published_only_and_revocation(family):
    c,admin,r=family
    private=c.post('/api/v2/recipes',headers=admin,json={**SAMPLE,'title':'Private draft'}).json()['id']
    marked=c.post('/api/v2/recipes',headers=r['contributor'],json={**SAMPLE,'title':'Requested public','published':True}).json()['id']
    assert not c.get(f'/api/v2/recipes/{marked}',headers=admin).json()['published']
    public=c.post('/api/v2/recipes',headers=r['approver'],json={**SAMPLE,'title':'Published dinner','published':True}).json()['id']
    k=c.post('/api/v2/admin/api-keys',headers=admin,json={'label':'Gigabyte Grove WordPress'})
    assert k.status_code==201
    key=k.json()['key']
    assert key.startswith('fnk_')
    assert key not in str(c.get('/api/v2/admin/api-keys',headers=admin).json())
    headers={'X-Forknook-API-Key':key}
    assert c.get('/api/v2/public/recipes').status_code==401
    data=c.get('/api/v2/public/recipes?page=1&limit=10',headers=headers).json()
    assert data['total']==1 and data['items'][0]['id']==public
    assert c.get('/api/v2/public/recipes/'+str(private),headers=headers).status_code==404
    assert 'notes' not in data['items'][0] and 'evidence' not in data['items'][0]
    assert c.delete('/api/v2/admin/api-keys/'+str(k.json()['id']),headers=admin).status_code==200
    assert c.get('/api/v2/public/recipes',headers=headers).status_code==401
    schema=c.get('/api/v2/openapi.json')
    assert schema.status_code==200 and '/api/v2/public/recipes' in schema.json()['paths']


def test_kitchenowl_read_only_migration_dry_run_idempotent(family):
    c,admin,r=family
    paths=[]
    async def ko_get(client,origin,token,path):
        paths.append(path)
        assert token=='t'*20
        if path=='/api/household':return [{'id':7,'name':'Home'}]
        if path=='/api/household/7/recipe':return [{'id':12,'name':'Old Soup'}]
        if path=='/api/household/7/recipe/12':return {'id':12,'name':'Old Soup','items':{'1 cup water':None},'instructions':['Boil']}
        raise AssertionError('Unexpected KitchenOwl path: '+path)
    credentials={'url':'http://192.168.3.12:8080','token':'t'*20,'household_id':7}
    with patch.object(ext,'ko_get',side_effect=ko_get):
        hs=c.post('/api/v2/admin/kitchenowl/households',headers=admin,json=credentials)
        assert hs.json()==[{'id':7,'name':'Home'}]
        dry=c.post('/api/v2/admin/kitchenowl/import',headers=admin,json={**credentials,'dry_run':True})
        assert dry.status_code==200,dry.text
        assert dry.json()['would_import']==1
        assert not c.get('/api/v2/recipes',headers=admin).json()
        applied=c.post('/api/v2/admin/kitchenowl/import',headers=admin,json={**credentials,'dry_run':False})
        assert applied.status_code==200,applied.text
        assert applied.json()['imported']==1
        again=c.post('/api/v2/admin/kitchenowl/import',headers=admin,json={**credentials,'dry_run':False})
        assert again.json()['imported']==0 and again.json()['already_imported']==1
        assert len(c.get('/api/v2/recipes',headers=admin).json())==1
        assert 'token' not in str(c.get('/api/v2/admin/export',headers=admin).json())
    assert all(p.startswith('/api/household') for p in paths)
    assert c.post('/api/v2/admin/kitchenowl/import',headers=r['approver'],json=credentials).status_code==403
    assert c.post('/api/v2/admin/kitchenowl/import',headers=admin,json={'url':'http://127.0.0.1','token':'t'*20,'household_id':1}).status_code==422


def test_real_kitchenowl_markdown_roundtrip():
    import kitchenowl
    original={'title':'Sunday Roast','description':'A family classic',
      'ingredients':[{'name':'potatoes','quantity':'2','unit':'cups','optional':False},{'name':'salt','quantity':'1','unit':'tsp'}],
      'instructions':['Preheat the oven','Roast the potatoes'],'notes':'Serve with gravy',
      'prep_time':15,'cook_time':40,'total_time':55,'servings':'4 servings',
      'source_url':'https://recipes.example.org/roast','image_url':'https://recipes.example.org/roast.jpg'}
    ko=kitchenowl.to_kitchenowl(original)
    restored=ext._ko_recipe(ko)
    assert restored.title=='Sunday Roast'
    assert restored.description=='A family classic'
    assert restored.instructions==['Preheat the oven','Roast the potatoes']
    assert restored.notes=='Serve with gravy'
    assert restored.ingredients[0]['raw']=='2 cups potatoes'
    assert restored.ingredients[1]['raw']=='1 tsp salt'
    assert restored.servings=='4 servings'
    assert restored.total_time==55 and restored.prep_time==15 and restored.cook_time==40
    assert restored.source_url==original['source_url']
    assert restored.image_url==original['image_url']
