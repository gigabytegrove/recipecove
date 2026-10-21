"""Calden-to-RecipeCove meal plan contract: shared authoritative data and least-privilege keys."""
from pathlib import Path
from unittest.mock import patch
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import forknook as base
import forknook_features as features
import meal_sync

@pytest.fixture
def client_with_keys(tmp_path):
    with patch.object(base,"DATA_DIR",tmp_path),patch.object(base,"DB_FILE",tmp_path/"recipecove.sqlite"),
         patch.object(base,"CLAIM_FILE",tmp_path/".claim"):
        base._ATTEMPTS.clear()
        base.initialize()
        features.initialize()
        meal_sync.initialize()
        with TestClient(app.app) as client:
            setup=client.post("/api/v2/setup/complete",json={
                "username":"root","password":"a strong family password",
                "setup_code":base.CLAIM_FILE.read_text()})
            assert setup.status_code==200,setup.text
            admin={"Authorization":"Bearer "+setup.json()["token"]}
            recipe=client.post("/api/v2/recipes",headers=admin,json={
                "title":"Family pasta","ingredients":[],"instructions":[]})
            assert recipe.status_code==201,recipe.text
            recipe_id=recipe.json()["id"]
            read=client.post("/api/v2/admin/api-keys",headers=admin,
                json={"label":"WordPress recipes","scope":"recipes:read"})
            sync=client.post("/api/v2/admin/api-keys",headers=admin,
                json={"label":"Calden family meals","scope":"meals:sync"})
            assert read.status_code==201 and sync.status_code==201
            yield client,admin,recipe_id,read.json(),sync.json()

def test_meals_only_key_is_required(client_with_keys):
    c,admin,recipe,read,sync=client_with_keys
    endpoint="/api/v2/integrations/calden/meal-plan?start=2026-10-21&end=2026-11-04"
    assert c.get(endpoint).status_code==401
    assert c.get(endpoint,headers={"X-Forknook-API-Key":read["key"]}).status_code==401
    key={"X-Forknook-API-Key":sync["key"]}
    assert c.get(endpoint,headers=key).status_code==200
    assert c.get("/api/v2/public/recipes",headers=key).status_code==401
    assert c.get("/api/v2/integrations/calden/recipes",headers=key).json()["items"][0]["id"]==recipe
    assert c.get("/api/v2/integrations/calden/meal-plan?start=2026-10-21&end=2027-01-31",headers=key).status_code==422

def test_create_replay_update_and_delete_reflect_existing_meal_plan(client_with_keys):
    c,admin,recipe,read,sync=client_with_keys
    key={"X-Forknook-API-Key":sync["key"]}
    base_url="/api/v2/integrations/calden/meal-plan"
    body={"day":"2026-10-21","meal":"dinner","recipe_id":recipe,"servings":4,
          "note":"At home","request_id":"calden-first-123"}
    first=c.post(base_url,headers=key,json=body)
    assert first.status_code==201,first.text
    meal_id=first.json()["id"]
    again=c.post(base_url,headers=key,json=body)
    assert again.json()=={"id":meal_id,"created":False}
    conflict=c.post(base_url,headers=key,json={**body,"servings":6})
    assert conflict.status_code==409
    existing=c.get("/api/v2/meal-plan?start=2026-10-21&end=2026-10-21",headers=admin).json()
    assert len(existing)==1 and existing[0]["id"]==meal_id
    change=c.put(f"{base_url}/{meal_id}",headers=key,json={
        **body,"day":"2026-10-22","meal":"lunch","servings":2})
    assert change.status_code==200,change.text
    changed=c.get("/api/v2/meal-plan?start=2026-10-22&end=2026-10-22",headers=admin).json()
    assert len(changed)==1 and changed[0]["meal"]=="lunch"
    deleted=c.delete(f"{base_url}/{meal_id}",headers=key)
    assert deleted.status_code==200
    assert c.get("/api/v2/meal-plan?start=2026-10-22&end=2026-10-22",headers=admin).json()==[]
    assert c.delete(f"{base_url}/{meal_id}",headers=key).status_code==404

def test_revocation_and_input_validation(client_with_keys):
    c,admin,recipe,read,sync=client_with_keys
    key={"X-Forknook-API-Key":sync["key"]}
    url="/api/v2/integrations/calden/meal-plan"
    good={"day":"2026-10-21","meal":"dinner","recipe_id":recipe,
          "servings":4,"request_id":"family-unique-1"}
    assert c.post(url,headers=key,json={**good,"day":"2026-02-30"}).status_code==422
    assert c.post(url,headers=key,json={**good,"meal":"invalid"}).status_code==422
    assert c.post(url,headers=key,json={**good,"recipe_id":999999}).status_code==404
    assert c.post(url,headers=key,json={k:v for k,v in good.items() if k!="request_id"}).status_code==422
    assert c.delete("/api/v2/admin/api-keys/"+str(sync["id"]),headers=admin).status_code==200
    assert c.get(url+"?start=2026-10-21&end=2026-10-31",headers=key).status_code==401
