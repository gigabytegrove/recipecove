"""Narrow, revocable Calden meal-plan integration; RecipeCove is the only source of truth."""
from __future__ import annotations

import hashlib
import re
import time
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
import forknook as base

router = APIRouter(prefix="/api/v2/integrations/calden",tags=["Calden meal planning"])

def integration_key(x_forknook_api_key: str | None = Header(default=None)) -> int:
    key = x_forknook_api_key or ""
    if not key.startswith("fnk_") or len(key) > 256:
        raise HTTPException(401,"Valid meal sync key required")
    digest = hashlib.sha256(key.encode()).hexdigest()
    with base.LOCK,base.database() as db:
        record = db.execute("""SELECT id FROM api_keys
             WHERE token_hash=? AND revoked_at IS NULL AND scope='meals:sync'""",(digest,)).fetchone()
        if record is None:
            raise HTTPException(401,"Invalid or revoked meal sync key")
        db.execute("UPDATE api_keys SET last_used=? WHERE id=?",(time.time(),record["id"]))
        return int(record["id"])

def initialize() -> None:
    with base.LOCK,base.database() as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS calden_meal_requests(
            key_id INTEGER NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
            request_id TEXT NOT NULL,
            meal_id INTEGER NOT NULL REFERENCES meal_plan(id) ON DELETE CASCADE,
            PRIMARY KEY(key_id,request_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS calden_meal_request_target
          ON calden_meal_requests(key_id,meal_id);""")

class MealSyncInput(BaseModel):
    day: str
    meal: Literal["anytime","breakfast","lunch","dinner","snack"] = "dinner"
    recipe_id: int = Field(gt=0)
    servings: float = Field(default=1,gt=0,le=100)
    note: str = Field(default="",max_length=500)
    request_id: str | None = Field(default=None,min_length=8,max_length=100,pattern=r"^[a-zA-Z0-9_-]+$")

def checked_date(value: str) -> str:
    try:
        if date.fromisoformat(value).isoformat() == value:
            return value
    except (TypeError,ValueError):
        pass
    raise HTTPException(422,"Use a valid YYYY-MM-DD date")

def recipe_exists(db,recipe_id: int) -> None:
    if not db.execute("SELECT 1 FROM recipes WHERE id=?",(recipe_id,)).fetchone():
        raise HTTPException(404,"Recipe not found")

@router.get("/status")
def status(_: int = Depends(integration_key)):
    return {"ready":True,"provider":"RecipeCove","role":"meal-plan"}

@router.get("/recipes")
def recipes(_: int = Depends(integration_key)):
    with base.database() as db:
        return {"items":[dict(x) for x in db.execute(
            "SELECT id,title FROM recipes ORDER BY title COLLATE NOCASE,id LIMIT 2000")]}

@router.get("/meal-plan")
def meal_plan(start: str,end: str,_: int = Depends(integration_key)):
    start=checked_date(start);end=checked_date(end)
    if end<start or (date.fromisoformat(end)-date.fromisoformat(start)).days>92:
        raise HTTPException(422,"Choose a date range up to 93 days")
    with base.database() as db:
        return {"items":[dict(x) for x in db.execute("""SELECT m.id,m.day,m.meal,m.recipe_id,
              r.title AS recipe_title,m.servings,m.note
              FROM meal_plan m JOIN recipes r ON r.id=m.recipe_id
              WHERE m.day>=? AND m.day<=? ORDER BY m.day,m.meal,m.id LIMIT 1200""",
              (start,end))]}

@router.post("/meal-plan",status_code=201)
def create_meal(body: MealSyncInput,key_id: int = Depends(integration_key)):
    day=checked_date(body.day)
    if not body.request_id:
        raise HTTPException(422,"request_id is required to avoid duplicate meals")
    with base.LOCK,base.database() as db:
        # Serialize idempotency-key checks across multiple server processes too.
        db.execute("BEGIN IMMEDIATE")
        existing=db.execute("""SELECT m.id,m.day,m.meal,m.recipe_id,m.servings,m.note
             FROM calden_meal_requests l JOIN meal_plan m ON m.id=l.meal_id
             WHERE l.key_id=? AND l.request_id=?""",
             (key_id,body.request_id)).fetchone()
        if existing:
            expected=(day,body.meal,body.recipe_id,body.servings,body.note)
            previous=(existing["day"],existing["meal"],existing["recipe_id"],existing["servings"],existing["note"])
            if previous!=expected:
                raise HTTPException(409,"This request ID was previously used for a different meal")
            return {"id":existing["id"],"created":False}
        recipe_exists(db,body.recipe_id)
        cursor=db.execute("""INSERT INTO meal_plan(day,meal,recipe_id,servings,note,created_by)
             VALUES (?,?,?,?,?,NULL)""",(day,body.meal,body.recipe_id,body.servings,body.note))
        db.execute("INSERT INTO calden_meal_requests(key_id,request_id,meal_id) VALUES(?,?,?)",
                   (key_id,body.request_id,cursor.lastrowid))
        db.commit()
        return {"id":cursor.lastrowid,"created":True}

@router.put("/meal-plan/{meal_id}")
def change_meal(meal_id: int,body: MealSyncInput,_: int = Depends(integration_key)):
    day=checked_date(body.day)
    with base.LOCK,base.database() as db:
        recipe_exists(db,body.recipe_id)
        result=db.execute("""UPDATE meal_plan SET day=?,meal=?,recipe_id=?,servings=?,note=?
             WHERE id=?""",(day,body.meal,body.recipe_id,body.servings,body.note,meal_id))
        if not result.rowcount:
            raise HTTPException(404,"Meal no longer exists")
        db.commit()
        return {"id":meal_id,"updated":True}

@router.delete("/meal-plan/{meal_id}")
def remove_meal(meal_id: int,_: int = Depends(integration_key)):
    with base.LOCK,base.database() as db:
        result=db.execute("DELETE FROM meal_plan WHERE id=?",(meal_id,))
        if not result.rowcount:
            raise HTTPException(404,"Meal no longer exists")
        db.commit()
        return {"id":meal_id,"deleted":True}
