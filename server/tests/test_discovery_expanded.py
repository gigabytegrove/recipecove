"""Expanded recipe catalogs, WordPress publisher safety and offline FTS dataset."""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app
import forknook as base
import forknook_catalog as catalog
import forknook_dataset as dataset


@pytest.fixture
def family(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    monkeypatch.setattr(base,'DATA_DIR',tmp_path)
    monkeypatch.setattr(base,'DB_FILE',tmp_path/'forknook.sqlite3')
    monkeypatch.setattr(base,'CLAIM_FILE',tmp_path/'.claim')
    with TestClient(app.app) as client:
        setup=client.post('/api/v2/setup/complete',json={
            'username':'admin','password':'a strong admin password',
            'setup_code':base.CLAIM_FILE.read_text()})
        assert setup.status_code==200,setup.text
        admin={'Authorization':'Bearer '+setup.json()['token']}
        yield client,admin


def make_csv():
    out=io.StringIO()
    writer=csv.DictWriter(out,fieldnames=['id','name','minutes','ingredients','steps','tags'])
    writer.writeheader()
    for number,name,ingredients in [
        (14,'Bourbon Chicken',['chicken breast','bourbon','soy sauce','brown sugar']),
        (15,'Sesame Chicken',['chicken thighs','sesame oil','sesame seeds','garlic']),
        (16,'Chicken and Rice',['chicken thighs','rice','chicken broth'])]:
        writer.writerow({'id':number,'name':name,'minutes':'35',
            'ingredients':repr(ingredients),
            'steps':repr(['Mix the sauce','Cook the chicken','Serve warm']),
            'tags':repr(['chicken','dinner'])})
    return out.getvalue().encode('utf-8')


def test_foodcom_local_index_bourbon_sesame_and_ingredient(family,tmp_path):
    client,admin=family
    before=client.get('/api/v2/discovery/providers',headers=admin)
    assert before.status_code==200,before.text
    assert next(p for p in before.json() if p['id']=='foodcom')['available'] is False
    result=dataset._index_csv(io.BytesIO(make_csv()),dataset._path())
    assert result==3
    assert dataset.stats()['count']==3
    for query,expected in [('bourbon chicken','Bourbon Chicken'),('sesame chicken','Sesame Chicken')]:
        result=client.get('/api/v2/discovery/search',headers=admin,
                          params={'provider':'foodcom','mode':'name','q':query})
        assert result.status_code==200,result.text
        assert result.json()['items'][0]['title']==expected
        detail=client.get('/api/v2/discovery/preview',headers=admin,
                          params={'provider':'foodcom','id':result.json()['items'][0]['id']})
        assert detail.status_code==200,detail.text
        assert detail.json()['requires_manual_review'] is True
        assert len(detail.json()['ingredients'])>=3
        assert len(detail.json()['instructions'])==3
    ingredients=client.get('/api/v2/discovery/search',headers=admin,
                           params={'provider':'foodcom','mode':'ingredient','q':'sesame oil'})
    assert ingredients.status_code==200,ingredients.text
    assert ingredients.json()['items'][0]['title']=='Sesame Chicken'
    # Discovery rows stay separate until someone chooses to import/approve.
    assert client.get('/api/v2/recipes',headers=admin).json()==[]


def test_publisher_name_search_both_example_dishes(family,monkeypatch):
    client,admin=family
    async def fake(url, params):
        if 'spendwithpennies' in url:
            return [{'id':3,'title':'Sticky Bourbon Chicken','url':'https://www.spendwithpennies.com/sticky-bourbon-chicken/'},
                    {'id':4,'title':'Sesame Chicken','url':'https://www.spendwithpennies.com/sesame-chicken/'}]
        if 'budgetbytes' in url:
            return [{'id':8,'title':'Easy Sesame Chicken','url':'https://www.budgetbytes.com/easy-sesame-chicken/'}]
        if 'skinnytaste' in url:return []
        if '/wp-json/wp/v2/search' in url:return []
        if 'themealdb' in url:return {'meals':None}
        if 'wikibooks' in url:return {'query':{'search':[]}}
        raise AssertionError(url)
    monkeypatch.setattr(catalog,'_get_json',fake)
    for title in ['bourbon chicken','sesame chicken']:
        result=client.get('/api/v2/discovery/search',headers=admin,
                          params={'provider':'all','mode':'name','q':title})
        assert result.status_code==200,result.text
        data=result.json()
        assert data['sources_searched']==10 and data['items']
        assert any(title in item['title'].lower() for item in data['items'])
    result=client.get('/api/v2/discovery/search',headers=admin,
                      params={'provider':'budgetbytes','mode':'name','q':'sesame chicken'})
    assert result.status_code==200 and result.json()['items'][0]['provider']=='budgetbytes'


def test_all_search_preserves_partial_results_when_catalog_unavailable(family,monkeypatch):
    client,admin=family
    async def fail_or_results(url,params):
        if 'spendwithpennies' in url:
            return [{'title':'Bourbon Chicken','url':'https://www.spendwithpennies.com/sticky-bourbon-chicken/'}]
        raise Exception('offline')
    # Only known failures should be suppressed. Real HTTP/provider errors use HTTPException.
    from fastapi import HTTPException
    async def degrade(url,params):
        if 'spendwithpennies' in url:
            return [{'title':'Bourbon Chicken','url':'https://www.spendwithpennies.com/sticky-bourbon-chicken/'}]
        raise HTTPException(502,'Catalog unavailable')
    monkeypatch.setattr(catalog,'_get_json',degrade)
    got=client.get('/api/v2/discovery/search',headers=admin,
                   params={'provider':'all','mode':'name','q':'bourbon chicken'})
    assert got.status_code==200,got.text
    assert len(got.json()['items'])==1
    assert len(got.json()['errors'])>=3


def test_publisher_rejects_untrusted_urls(family):
    client,admin=family
    for link in ['http://www.spendwithpennies.com/sesame-chicken/',
                 'https://internal.spendwithpennies.com/sesame-chicken/',
                 'https://www.spendwithpennies.com.evil.example.com/chicken',
                 'https://www.spendwithpennies.com:12345/chicken/',
                 'https://127.0.0.1/']:
        result=client.get('/api/v2/discovery/preview',headers=admin,
             params={'provider':'spendwithpennies','id':link})
        assert result.status_code==422,(link,result.status_code,result.text)
    assert client.get('/api/v2/discovery/search',params={'provider':'all','q':'sesame chicken'}).status_code==401


def test_foodcom_import_requires_admin_and_dataset_rights(family):
    client,admin=family
    payload={'file':('RAW_recipes.csv',make_csv(),'text/csv')}
    assert client.post('/api/v2/discovery/dataset/import',files=payload).status_code==401
    no_rights=client.post('/api/v2/discovery/dataset/import',headers=admin,files=payload)
    assert no_rights.status_code==422
    status=client.get('/api/v2/discovery/dataset/status',headers=admin)
    assert status.status_code==200


def test_invalid_foodcom_dataset_does_not_overwrite_existing_index(family,tmp_path):
    dataset._index_csv(io.BytesIO(make_csv()),dataset._path())
    count=dataset.stats()['count']
    with pytest.raises(ValueError,match='Expected Food.com'):
        dataset._index_csv(io.BytesIO(b'name,bad-column\nfoo,bar\n'),dataset._path())
    assert dataset.stats()['count']==count
    assert dataset.search('sesame chicken')[0]['title']=='Sesame Chicken'

def test_preview_photo_cache_falls_back_gracefully_when_source_refuses(monkeypatch):
    import asyncio
    import forknook_catalog as catalog
    source = 'https://www.themealdb.com/images/recipe.jpg'
    requests = []
    def refuse(url):
        requests.append(url)
        return url
    monkeypatch.setattr(catalog.forknook_images, 'capture_best_effort', refuse)
    result = asyncio.run(catalog._prepare_preview_photo({'image_url': source, 'title': 'Soup'}))
    assert result['image_url'] == source
    assert requests == [source]
    missing = asyncio.run(catalog._prepare_preview_photo({'image_url': '', 'title': 'No photo'}))
    assert missing['image_url'] == ''
    assert requests == [source]


def test_preview_photo_cache_returns_private_photo_when_captured(monkeypatch):
    import asyncio
    import forknook_catalog as catalog
    image = '/api/v2/images/' + 'a'*64 + '.jpg'
    monkeypatch.setattr(catalog.forknook_images, 'capture_best_effort', lambda _: image)
    result = asyncio.run(catalog._prepare_preview_photo(
        {'image_url': 'https://www.themealdb.com/images/recipe.jpg'}))
    assert result['image_url'] == image


def test_recipe_critic_denied_html_is_reviewable_not_422(family,monkeypatch):
    client,admin=family
    import recipes
    async def blocked(url):
        raise ValueError("Recipe site denied automated access (HTTP 403)")
    async def empty_api(url,params):
        assert url == 'https://www.therecipecritic.com/wp-json/wp/v2/posts'
        return []
    monkeypatch.setattr(recipes,'page_html',blocked)
    monkeypatch.setattr(catalog,'_get_json',empty_api)
    source='https://therecipecritic.com/creamy-lemon-chicken/'
    answer=client.get('/api/v2/discovery/preview',headers=admin,
                      params={'provider':'therecipecritic','id':source})
    assert answer.status_code==200,answer.text
    draft=answer.json()
    assert draft['title']=='Creamy Lemon Chicken'
    assert draft['source_url']==source
    assert draft['requires_manual_review'] is True
    assert draft['ingredients']==[] and draft['instructions']==[]
    assert 'original recipe' in draft['preview_notice'].lower()
    assert client.get('/api/v2/recipes',headers=admin).json()==[]


def test_wordpress_featured_thumbnails_from_one_batch_call(family,monkeypatch):
    client,admin=family
    seen=[]
    source='https://therecipecritic.com/creamy-lemon-chicken/'
    async def fake(url,params):
        seen.append((url,params))
        if url.endswith('/wp/v2/search'):
            return [{'id':9123,'title':'Creamy Lemon Chicken','url':source}]
        assert url.endswith('/wp/v2/posts')
        assert params['include']=='9123'
        return [{'id':9123,'_embedded':{'wp:featuredmedia':[{
           'source_url':'https://therecipecritic.com/images/photo.jpg',
           'media_details':{'sizes':{'medium':{
             'source_url':'https://therecipecritic.com/images/thumb.jpg'}}}}]}}]
    monkeypatch.setattr(catalog,'_get_json',fake)
    response=client.get('/api/v2/discovery/search',headers=admin,
        params={'provider':'therecipecritic','mode':'name','q':'lemon chicken'})
    assert response.status_code==200,response.text
    assert response.json()['items'][0]['image_url']=='https://therecipecritic.com/images/thumb.jpg'
    assert len(seen)==2
