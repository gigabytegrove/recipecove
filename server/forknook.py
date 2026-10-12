"""Forknook standalone family RMS. No KitchenOwl login, database or dependency.

Uses one SQLite database on the existing persistent /data volume. All operations
are scoped to authenticated family members, with admin-only account management.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import recipes
import media
import updater
import settings_store

router = APIRouter(prefix="/api/v2", tags=["Forknook"])
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_FILE = DATA_DIR / "forknook.sqlite3"
CLAIM_FILE = DATA_DIR / ".forknook-claim"
LOCK = threading.RLock()
MAX_IMPORTED_RECIPES = 1000
_ATTEMPTS: dict[str, list[float]] = {}


@contextmanager
def database():
    # sqlite3.Connection.__exit__ commits but does NOT close connections.
    # Explicitly close them after every request to prevent long-running FD leaks.
    con = sqlite3.connect(DB_FILE, timeout=15)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 15000")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def initialize():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK, database() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          display_name TEXT NOT NULL, salt TEXT NOT NULL, digest TEXT NOT NULL,
          role TEXT NOT NULL CHECK (role IN ('admin','member')), enabled INTEGER NOT NULL DEFAULT 1,
          created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS categories (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, color TEXT NOT NULL DEFAULT '#537d56');
        CREATE TABLE IF NOT EXISTS recipes (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, data TEXT NOT NULL,
          category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
          created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
          created_at REAL NOT NULL, updated_at REAL NOT NULL,
          external_ref TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS recipe_states (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
          state TEXT NOT NULL CHECK (state IN ('want','made','none')),
          made_count INTEGER NOT NULL DEFAULT 0,
          last_made TEXT, PRIMARY KEY (user_id,recipe_id));
        CREATE TABLE IF NOT EXISTS meal_plan (
          id INTEGER PRIMARY KEY, day TEXT NOT NULL, meal TEXT NOT NULL,
          recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
          servings REAL NOT NULL DEFAULT 1,
          note TEXT NOT NULL DEFAULT '', created_by INTEGER REFERENCES users(id) ON DELETE SET NULL);
        CREATE INDEX IF NOT EXISTS meal_day_idx ON meal_plan(day);
        CREATE TABLE IF NOT EXISTS shopping_lists (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
          created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS shopping_items (
          id INTEGER PRIMARY KEY, list_id INTEGER NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
          name TEXT NOT NULL, quantity TEXT NOT NULL DEFAULT '', checked INTEGER NOT NULL DEFAULT 0,
          recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
          position INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS feeds (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL UNIQUE, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS migration_runs (
          id INTEGER PRIMARY KEY, source TEXT NOT NULL, attempted INTEGER NOT NULL,
          imported INTEGER NOT NULL, skipped INTEGER NOT NULL, created_at REAL NOT NULL);
        """)
        count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count == 0:
        if not CLAIM_FILE.exists():
            CLAIM_FILE.write_text(secrets.token_urlsafe(24), encoding="ascii")
            os.chmod(CLAIM_FILE, 0o600)
        # Only shown in container logs on first boot; never exposed via HTTP.
        import logging
        logging.getLogger("forknook").warning("Forknook first-run setup code: %s", CLAIM_FILE.read_text().strip())
    else:
        CLAIM_FILE.unlink(missing_ok=True)
    os.chmod(DB_FILE, 0o600)


def configured() -> bool:
    if not DB_FILE.exists(): return False
    with database() as db:
        return bool(db.execute("SELECT 1 FROM users LIMIT 1").fetchone())


def password_hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 400_000).hex()


def throttle(request: Request):
    ip=request.client.host if request.client else "unknown"
    now=time.monotonic()
    with LOCK:
        if len(_ATTEMPTS)>5000:
            for key in list(_ATTEMPTS):
                if not _ATTEMPTS[key] or _ATTEMPTS[key][-1]<now-600:_ATTEMPTS.pop(key,None)
        attempts=[ts for ts in _ATTEMPTS.get(ip,[]) if ts>now-600]
        if len(attempts)>=10:raise HTTPException(429,"Too many login attempts; try again later")
        attempts.append(now);_ATTEMPTS[ip]=attempts

def validate_password(password: str):
    if not 12 <= len(password) <= 1024:
        raise HTTPException(422, "Password must be between 12 and 1024 characters")


def validate_username(username: str):
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,48}", username):
        raise HTTPException(422, "Username must contain 3–48 letters, digits, dots, underscores or hyphens")


def new_session(db, user_id: int) -> str:
    token = secrets.token_urlsafe(48)
    db.execute("INSERT INTO sessions(token_hash,user_id,expires) VALUES (?,?,?)",
               (hashlib.sha256(token.encode()).hexdigest(),user_id,time.time()+30*86400))
    return token


def principal(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in to Forknook")
    token = authorization[7:]
    if not (20 <= len(token) <= 256):
        raise HTTPException(401, "Invalid session")
    with database() as db:
        row = db.execute("""SELECT u.id,u.username,u.display_name,u.role FROM sessions s
                JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.enabled=1""",
                (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        if not row: raise HTTPException(401, "Session expired; sign in again")
        return dict(row)


def admin(who: dict = Depends(principal)) -> dict:
    if who["role"] != "admin": raise HTTPException(403,"Administrator access required")
    return who


def mutate(db, sql: str, args: tuple=()):
    return db.execute(sql,args)


class Credentials(BaseModel):
    username: str = Field(min_length=1,max_length=200)
    password: str = Field(min_length=1,max_length=1024)

class Setup(Credentials):
    setup_code: str
    display_name: str = "Administrator"

class UserCreate(Credentials):
    display_name: str = "Family member"
    role: str = "member"

class UserPatch(BaseModel):
    display_name: str | None = None
    role: str | None = None
    enabled: bool | None = None
    password: str | None = None

class RecipePayload(BaseModel):
    title: str = Field(min_length=1,max_length=300)
    description: str = Field(default="",max_length=6000)
    ingredients: list[dict | str] = Field(default_factory=list,max_length=200)
    instructions: list[str] = Field(default_factory=list,max_length=100)
    servings: str | None = None
    prep_time: int | None = Field(default=None,ge=0,le=100000)
    cook_time: int | None = Field(default=None,ge=0,le=100000)
    total_time: int | None = Field(default=None,ge=0,le=100000)
    notes: str = Field(default="",max_length=30000)
    source_url: str = Field(default="",max_length=2000)
    image_url: str = Field(default="",max_length=2000)
    tags: list[str] = Field(default_factory=list,max_length=50)
    category_id: int | None = None
    evidence: str = Field(default="",max_length=47000)
    requires_manual_review: bool = False

class CategoryPayload(BaseModel):
    name: str = Field(min_length=1,max_length=80)
    color: str = "#537d56"

class StatePayload(BaseModel):
    state: str

class MealPayload(BaseModel):
    day: str
    meal: str
    recipe_id: int
    servings: float = Field(default=1,gt=0,le=100)
    note: str = Field(default="",max_length=500)

class ShoppingPayload(BaseModel):
    name: str = Field(min_length=1,max_length=150)

class ShoppingItemPayload(BaseModel):
    name: str = Field(min_length=1,max_length=500)
    quantity: str = Field(default="",max_length=150)
    checked: bool = False

class ShoppingBuild(BaseModel):
    recipe_ids: list[int] = Field(min_length=1,max_length=100)
    name: str = "Recipes shopping list"

class FeedPayload(BaseModel):
    url: str
    title: str = "Recipe feed"

class ExternalImport(BaseModel):
    url: str
    category_id: int | None = None


def _user_record(row):
    return {k:row[k] for k in ("id","username","display_name","role","enabled")}

@router.get("/setup/status")
def setup_status():
    return {"configured":configured(),"product":"Forknook","requires_kitchenowl":False}

@router.post("/setup/complete")
def setup(body: Setup, request: Request):
    throttle(request)
    validate_username(body.username);validate_password(body.password)
    if not 1 <= len(body.display_name.strip()) <= 100:raise HTTPException(422,"Invalid display name")
    with LOCK, database() as db:
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(409,"Forknook has already been configured")
        if not CLAIM_FILE.exists() or not hmac.compare_digest(body.setup_code,CLAIM_FILE.read_text().strip()):
            raise HTTPException(403,"Incorrect first-run setup code")
        salt = secrets.token_bytes(24)
        row=mutate(db,"INSERT INTO users(username,display_name,salt,digest,role,created_at) VALUES (?,?,?,?,?,?)",
                   (body.username,body.display_name.strip(),salt.hex(),password_hash(body.password,salt),"admin",time.time()))
        token=new_session(db,row.lastrowid)
        db.commit()
        CLAIM_FILE.unlink(missing_ok=True)
    return {"success":True,"token":token}

@router.post("/auth/login")
def login(body: Credentials,request:Request):
    throttle(request)
    # Fixed-cost digest and a bounded delay even for nonexistent users.
    with LOCK, database() as db:
        row=db.execute("SELECT * FROM users WHERE username=? AND enabled=1",(body.username,)).fetchone()
        salt=bytes.fromhex(row["salt"]) if row else b'\x00'*24
        actual=password_hash(body.password,salt)
        if not row or not hmac.compare_digest(actual,row["digest"]):
            raise HTTPException(401,"Invalid Forknook username or password")
        token=new_session(db,row["id"])
        db.commit()
        return {"token":token,"user":_user_record(row)}

@router.get("/auth/me")
def me(who:dict=Depends(principal)):
    return who

@router.post("/auth/logout")
def logout(authorization:str|None=Header(default=None),who:dict=Depends(principal)):
    with LOCK,database() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(authorization[7:].encode()).hexdigest(),))
        db.commit()
    return {"success":True}

@router.get("/admin/users")
def users(who:dict=Depends(admin)):
    with database() as db:
        return [_user_record(r) for r in db.execute("SELECT * FROM users ORDER BY username")]

@router.post("/admin/users",status_code=201)
def create_user(body:UserCreate,who:dict=Depends(admin)):
    validate_username(body.username);validate_password(body.password)
    if body.role not in ("admin","member"):raise HTTPException(422,"Invalid role")
    with LOCK,database() as db:
        try:
            row=db.execute("INSERT INTO users(username,display_name,salt,digest,role,created_at) VALUES (?,?,?,?,?,?)",
                (body.username,body.display_name[:100],(salt:=secrets.token_bytes(24)).hex(),password_hash(body.password,salt),body.role,time.time()))
            db.commit()
        except sqlite3.IntegrityError as exc:raise HTTPException(409,"Username already exists") from exc
        return {"id":row.lastrowid}

@router.patch("/admin/users/{user_id}")
def patch_user(user_id:int,body:UserPatch,who:dict=Depends(admin)):
    with LOCK,database() as db:
        row=db.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone()
        if not row:raise HTTPException(404,"User not found")
        role=body.role if body.role is not None else row["role"]
        enabled=int(body.enabled) if body.enabled is not None else row["enabled"]
        if role not in ("admin","member"):raise HTTPException(422,"Invalid role")
        if row["role"]=="admin" and (role!="admin" or not enabled):
            count=db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]
            if count<2:raise HTTPException(409,"Cannot disable the last active administrator")
        display=body.display_name.strip() if body.display_name is not None else row["display_name"]
        if not 1<=len(display)<=100:raise HTTPException(422,"Invalid display name")
        if body.password is not None:
            validate_password(body.password)
            salt=secrets.token_bytes(24)
            db.execute("UPDATE users SET salt=?,digest=? WHERE id=?",(salt.hex(),password_hash(body.password,salt),user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?",(user_id,))
        db.execute("UPDATE users SET display_name=?,role=?,enabled=? WHERE id=?",(display,role,enabled,user_id))
        if not enabled:db.execute("DELETE FROM sessions WHERE user_id=?",(user_id,))
        db.commit()
    return {"success":True}

@router.get("/categories")
def categories(who:dict=Depends(principal)):
    with database() as db:return [dict(x) for x in db.execute("SELECT * FROM categories ORDER BY name")]

@router.post("/categories",status_code=201)
def create_category(body:CategoryPayload,who:dict=Depends(principal)):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}",body.color):raise HTTPException(422,"Invalid color")
    with LOCK,database() as db:
        try:
            r=db.execute("INSERT INTO categories(name,color) VALUES (?,?)",(body.name.strip(),body.color));db.commit()
            return {"id":r.lastrowid}
        except sqlite3.IntegrityError as exc:raise HTTPException(409,"Category already exists") from exc

@router.delete("/categories/{category_id}")
def delete_category(category_id:int,who:dict=Depends(admin)):
    with LOCK,database() as db:
        db.execute("DELETE FROM categories WHERE id=?",(category_id,));db.commit()
    return {"success":True}


def _recipe(row):
    data=json.loads(row["data"])
    data.update({"id":row["id"],"title":row["title"],"category_id":row["category_id"],
                 "created_at":row["created_at"],"updated_at":row["updated_at"]})
    return data

def _recipe_exists(db,recipe_id:int):
    if not db.execute("SELECT 1 FROM recipes WHERE id=?",(recipe_id,)).fetchone():
        raise HTTPException(404,"Recipe not found")

def _category_exists(db,category_id:int|None):
    if category_id is not None and not db.execute("SELECT 1 FROM categories WHERE id=?",(category_id,)).fetchone():
        raise HTTPException(422,"Category does not exist")

@router.get("/recipes")
def list_recipes(q:str="",category_id:int|None=None,state:str="",who:dict=Depends(principal)):
    with database() as db:
        rows=db.execute("""SELECT r.*,coalesce(s.state,'none') AS personal_state,s.made_count, c.name AS category
           FROM recipes r LEFT JOIN recipe_states s ON s.recipe_id=r.id AND s.user_id=?
           LEFT JOIN categories c ON c.id=r.category_id ORDER BY r.updated_at DESC""",(who["id"],)).fetchall()
    result=[]
    for r in rows:
        if category_id is not None and r["category_id"]!=category_id:continue
        if state and r["personal_state"]!=state:continue
        item=_recipe(r);item.update({"state":r["personal_state"],"made_count":r["made_count"] or 0,"category":r["category"]})
        if q and q.casefold() not in (item["title"]+" "+item.get("description","")+" "+" ".join(item.get("tags",[]))).casefold():continue
        result.append(item)
    return result

@router.get("/recipes/{recipe_id}")
def get_recipe(recipe_id:int,who:dict=Depends(principal)):
    with database() as db:
        row=db.execute("SELECT * FROM recipes WHERE id=?",(recipe_id,)).fetchone()
        if not row:raise HTTPException(404,"Recipe not found")
        return _recipe(row)

@router.post("/recipes",status_code=201)
def create_recipe(body:RecipePayload,who:dict=Depends(principal)):
    with LOCK,database() as db:
        _category_exists(db,body.category_id)
        now=time.time()
        result=db.execute("INSERT INTO recipes(title,data,category_id,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                          (body.title.strip(),body.model_dump_json(),body.category_id,who["id"],now,now))
        db.commit()
        return {"id":result.lastrowid}

@router.put("/recipes/{recipe_id}")
def update_recipe(recipe_id:int,body:RecipePayload,who:dict=Depends(principal)):
    with LOCK,database() as db:
        _recipe_exists(db,recipe_id);_category_exists(db,body.category_id)
        db.execute("UPDATE recipes SET title=?,data=?,category_id=?,updated_at=? WHERE id=?",
                   (body.title.strip(),body.model_dump_json(),body.category_id,time.time(),recipe_id));db.commit()
    return {"success":True,"id":recipe_id}

@router.delete("/recipes/{recipe_id}")
def delete_recipe(recipe_id:int,who:dict=Depends(admin)):
    with LOCK,database() as db:
        db.execute("DELETE FROM recipes WHERE id=?",(recipe_id,));db.commit()
    return {"success":True}

@router.put("/recipes/{recipe_id}/state")
def set_recipe_state(recipe_id:int,body:StatePayload,who:dict=Depends(principal)):
    if body.state not in ("want","made","none"):raise HTTPException(422,"Choose want, made or none")
    with LOCK,database() as db:
        _recipe_exists(db,recipe_id)
        db.execute("""INSERT INTO recipe_states(user_id,recipe_id,state,made_count,last_made)
           VALUES (?,?,?,?,?) ON CONFLICT(user_id,recipe_id) DO UPDATE SET state=excluded.state,
           made_count=CASE WHEN excluded.state='made' AND recipe_states.state!='made' THEN recipe_states.made_count+1 ELSE recipe_states.made_count END,
           last_made=CASE WHEN excluded.state='made' THEN excluded.last_made ELSE recipe_states.last_made END""",
           (who["id"],recipe_id,body.state,int(body.state=="made"),time.strftime("%Y-%m-%d") if body.state=="made" else None))
        db.commit()
    return {"success":True}


def validate_day(day:str):
    from datetime import date
    try:
        if date.fromisoformat(day).isoformat()!=day:raise ValueError
    except ValueError:raise HTTPException(422,"Use YYYY-MM-DD for the day")

@router.get("/meal-plan")
def meal_plan(start:str|None=None,end:str|None=None,who:dict=Depends(principal)):
    if start:validate_day(start)
    if end:validate_day(end)
    with database() as db:
        rows=db.execute("""SELECT m.*,r.title AS recipe_title FROM meal_plan m
          JOIN recipes r ON r.id=m.recipe_id WHERE (? IS NULL OR m.day>=?) AND (? IS NULL OR m.day<=?)
          ORDER BY m.day,m.id LIMIT 600""",(start,start,end,end)).fetchall()
        return [dict(r) for r in rows]

@router.post("/meal-plan",status_code=201)
def add_meal(body:MealPayload,who:dict=Depends(principal)):
    validate_day(body.day)
    if body.meal not in ("breakfast","lunch","dinner","snack"):raise HTTPException(422,"Invalid meal")
    with LOCK,database() as db:
        _recipe_exists(db,body.recipe_id)
        r=db.execute("INSERT INTO meal_plan(day,meal,recipe_id,servings,note,created_by) VALUES (?,?,?,?,?,?)",
           (body.day,body.meal,body.recipe_id,body.servings,body.note,who["id"]));db.commit()
        return {"id":r.lastrowid}

@router.delete("/meal-plan/{meal_id}")
def delete_meal(meal_id:int,who:dict=Depends(principal)):
    with LOCK,database() as db:
        db.execute("DELETE FROM meal_plan WHERE id=?",(meal_id,));db.commit()
    return {"success":True}

@router.get("/shopping-lists")
def shopping_lists(who:dict=Depends(principal)):
    with database() as db:
        return [dict(r) for r in db.execute("SELECT * FROM shopping_lists ORDER BY created_at DESC")]

@router.post("/shopping-lists",status_code=201)
def create_list(body:ShoppingPayload,who:dict=Depends(principal)):
    with LOCK,database() as db:
        r=db.execute("INSERT INTO shopping_lists(name,created_by,created_at) VALUES (?,?,?)",(body.name,who["id"],time.time()));db.commit()
        return {"id":r.lastrowid}

@router.get("/shopping-lists/{list_id}")
def shopping_list(list_id:int,who:dict=Depends(principal)):
    with database() as db:
        row=db.execute("SELECT * FROM shopping_lists WHERE id=?",(list_id,)).fetchone()
        if not row:raise HTTPException(404,"Shopping list not found")
        return {**dict(row),"items":[dict(r) for r in db.execute("SELECT * FROM shopping_items WHERE list_id=? ORDER BY position,id",(list_id,))]}

@router.delete("/shopping-lists/{list_id}")
def delete_list(list_id:int,who:dict=Depends(principal)):
    with LOCK,database() as db:
        db.execute("DELETE FROM shopping_lists WHERE id=?",(list_id,));db.commit()
    return {"success":True}

@router.post("/shopping-lists/{list_id}/items",status_code=201)
def add_shopping_item(list_id:int,body:ShoppingItemPayload,who:dict=Depends(principal)):
    with LOCK,database() as db:
        if not db.execute("SELECT 1 FROM shopping_lists WHERE id=?",(list_id,)).fetchone():raise HTTPException(404,"Shopping list not found")
        row=db.execute("INSERT INTO shopping_items(list_id,name,quantity,checked,position) VALUES (?,?,?,?,?)",
           (list_id,body.name,body.quantity,int(body.checked),int(time.time()*1000)));db.commit()
        return {"id":row.lastrowid}

@router.patch("/shopping-lists/{list_id}/items/{item_id}")
def patch_item(list_id:int,item_id:int,body:ShoppingItemPayload,who:dict=Depends(principal)):
    with LOCK,database() as db:
        r=db.execute("UPDATE shopping_items SET name=?,quantity=?,checked=? WHERE list_id=? AND id=?",
                     (body.name,body.quantity,int(body.checked),list_id,item_id));db.commit()
        if not r.rowcount:raise HTTPException(404,"Shopping item not found")
    return {"success":True}

@router.delete("/shopping-lists/{list_id}/items/{item_id}")
def remove_item(list_id:int,item_id:int,who:dict=Depends(principal)):
    with LOCK,database() as db:
        db.execute("DELETE FROM shopping_items WHERE list_id=? AND id=?",(list_id,item_id));db.commit()
    return {"success":True}

@router.post("/shopping-lists/build",status_code=201)
def build_shopping_list(body:ShoppingBuild,who:dict=Depends(principal)):
    """Build from chosen recipes; keep quantities as written to avoid unsafe unit conversions."""
    with LOCK,database() as db:
        unique=list(dict.fromkeys(body.recipe_ids))
        recipes_found=[db.execute("SELECT * FROM recipes WHERE id=?",(rid,)).fetchone() for rid in unique]
        if any(r is None for r in recipes_found):raise HTTPException(404,"One or more recipes are missing")
        list_row=db.execute("INSERT INTO shopping_lists(name,created_by,created_at) VALUES (?,?,?)",
                       (body.name[:150],who["id"],time.time()))
        for recipe_row in recipes_found:
            values=json.loads(recipe_row["data"]).get("ingredients",[])
            for ingredient in values:
                if isinstance(ingredient,str):name=ingredient[:500];amount=""
                elif isinstance(ingredient,dict):
                    name=str(ingredient.get("name") or ingredient.get("raw") or "")[:500]
                    quantity=ingredient.get("quantity")
                    unit=ingredient.get("unit")
                    amount=" ".join(str(s) for s in (quantity,unit) if s is not None and str(s).strip())[:150]
                    # Old importers store a complete ingredient sentence in name; preserve it.
                    if name==str(ingredient.get("raw") or ""):amount=""
                else:continue
                if name.strip():db.execute("""INSERT INTO shopping_items
                  (list_id,name,quantity,recipe_id,position) VALUES (?,?,?,?,?)""",
                  (list_row.lastrowid,name,amount,recipe_row["id"],int(time.time()*1000)))
        db.commit()
        return {"id":list_row.lastrowid,"recipes":len(recipes_found)}

@router.get("/admin/overview")
def admin_overview(who:dict=Depends(admin)):
    with database() as db:
        tables=("users","recipes","categories","meal_plan","shopping_lists","shopping_items","feeds","migration_runs")
        return {key:db.execute(f"SELECT COUNT(*) FROM {key}").fetchone()[0] for key in tables}

@router.get("/admin/export")
def export_data(who:dict=Depends(admin)):
    with database() as db:
        return {"format":"forknook-export-v1","exported_at":time.time(),
             "recipes":[_recipe(r) for r in db.execute("SELECT * FROM recipes ORDER BY id")],
             "categories":[dict(r) for r in db.execute("SELECT * FROM categories ORDER BY id")],
             "meal_plan":[dict(r) for r in db.execute("SELECT * FROM meal_plan ORDER BY id")],
             "shopping_lists":[dict(r) for r in db.execute("SELECT * FROM shopping_lists ORDER BY id")],
             "shopping_items":[dict(r) for r in db.execute("SELECT * FROM shopping_items ORDER BY id")],
             "feeds":[dict(r) for r in db.execute("SELECT * FROM feeds ORDER BY id")]}

@router.get("/feeds")
def feeds(who:dict=Depends(principal)):
    with database() as db:return [dict(r) for r in db.execute("SELECT * FROM feeds ORDER BY title")]

@router.post("/feeds",status_code=201)
def add_feed(body:FeedPayload,who:dict=Depends(principal)):
    # Feed URL validation is repeated at request time to defend against rebinding.
    try:recipes.validate_external_url(body.url)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    with LOCK,database() as db:
        try:
            r=db.execute("INSERT INTO feeds(title,url,created_at) VALUES (?,?,?)",
                     (body.title[:150],body.url,time.time()));db.commit()
            return {"id":r.lastrowid}
        except sqlite3.IntegrityError as exc:raise HTTPException(409,"Feed already added") from exc

@router.delete("/feeds/{feed_id}")
def remove_feed(feed_id:int,who:dict=Depends(admin)):
    with LOCK,database() as db:db.execute("DELETE FROM feeds WHERE id=?",(feed_id,));db.commit()
    return {"success":True}

async def _feed_preview(url:str):
    # Only public URLs; validate every redirect and cap size; never fetch LAN targets.
    current=url
    async with httpx.AsyncClient(timeout=httpx.Timeout(20,connect=6),trust_env=False,follow_redirects=False) as client:
        for _ in range(5):
            try:recipes.validate_external_url(current)
            except ValueError as exc:raise HTTPException(422,str(exc)) from exc
            try:
                async with client.stream("GET",current,headers={"User-Agent":"Forknook/2.0 (recipe feed reader)"}) as response:
                    if response.status_code in (301,302,303,307,308):
                        dest=response.headers.get("location")
                        if not dest:raise HTTPException(422,"Feed redirect missing location")
                        current=urljoin(current,dest);continue
                    if response.status_code!=200:raise HTTPException(422,f"Feed returned HTTP {response.status_code}")
                    buffer=bytearray()
                    async for chunk in response.aiter_bytes():
                        buffer.extend(chunk)
                        if len(buffer)>2*1024*1024:raise HTTPException(413,"Feed larger than 2 MB")
            except httpx.HTTPError as exc:raise HTTPException(502,"Cannot retrieve recipe feed") from exc
            if b"<!DOCTYPE" in buffer.upper() or b"<!ENTITY" in buffer.upper():
                raise HTTPException(422,"Feed contains unsupported XML declarations")
            try:root=ET.fromstring(buffer)
            except ET.ParseError as exc:raise HTTPException(422,"Invalid RSS or Atom feed") from exc
            found=[]
            for el in list(root.iter()):
                if el.tag.rsplit('}',1)[-1] not in ("item","entry"):continue
                fields={child.tag.rsplit('}',1)[-1]:child for child in el}
                title=(fields.get('title').text if fields.get('title') is not None else '') or 'Recipe'
                link=fields.get('link')
                href=link.get('href') if link is not None else None
                if not href and link is not None:href=link.text
                if not href:continue
                href=urljoin(current,href.strip())
                try:recipes.validate_external_url(href)
                except ValueError:continue
                found.append({"title":title[:200],"url":href})
                if len(found)>=50:break
            return found
    raise HTTPException(422,"Too many redirects")

@router.get("/feeds/{feed_id}/entries")
async def feed_entries(feed_id:int,who:dict=Depends(principal)):
    with database() as db:
        row=db.execute("SELECT * FROM feeds WHERE id=?",(feed_id,)).fetchone()
    if not row:raise HTTPException(404,"Feed not found")
    return await _feed_preview(row["url"])

def from_parsed(result) -> dict:
    data=result.model_dump()
    data['description']=data.get('description') or ''
    data['notes']=data.get('notes') or ''
    data['source_url']=data.get('source_url') or ''
    data['image_url']=data.get('image_url') or ''
    data['evidence']=data.get('evidence') or ''
    return RecipePayload.model_validate(data).model_dump()

@router.post("/imports/preview-url")
async def preview_url(body:ExternalImport,who:dict=Depends(principal)):
    try:
        provider=media.social_provider(body.url)
        if provider:
            details=await run_in_threadpool(media.run_exclusive,
                       media.extract_facebook if provider=="facebook" else media.extract_instagram,body.url)
            result=await recipes.parse_text(details['text'],source_url=details.get('source_url',''),source_type=provider)
            result.evidence=details['text'][:recipes.MAX_TEXT]
            if not (result.ingredients and result.instructions):result.requires_manual_review=True
        else:
            document,final_url=await recipes.page_html(body.url)
            result=recipes.parse_document(document,final_url)
            if not result.ingredients and not result.instructions:
                raise ValueError("Page did not expose a recipe; paste the recipe text or upload a screenshot")
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    data=from_parsed(result);data['category_id']=body.category_id
    return data

@router.post("/imports/preview-text")
async def preview_text(body:dict,who:dict=Depends(principal)):
    content=body.get('text','')
    if not isinstance(content,str) or len(content)>47000:raise HTTPException(422,"Invalid import text")
    parsed=await recipes.parse_text(content)
    return from_parsed(parsed)

@router.post("/imports/preview-image")
async def preview_image(file:UploadFile=File(...),who:dict=Depends(principal)):
    if file.content_type not in ('image/jpeg','image/png','image/webp'):raise HTTPException(415,"Use a JPG, PNG or WebP image")
    content=await file.read(10*1024*1024+1)
    if len(content)>10*1024*1024:raise HTTPException(413,"Image exceeds 10 MB")
    return from_parsed(await recipes.parse_image(content))

@router.post("/imports/preview-video")
async def preview_video(file:UploadFile=File(...),who:dict=Depends(principal)):
    if not (file.content_type or '').startswith('video/'):raise HTTPException(415,"Use a video file")
    try:info=await run_in_threadpool(media.run_exclusive,media.process_local_file,file)
    finally:await file.close()
    parsed=await recipes.parse_text(info['text'],source_type='video')
    parsed.evidence=info['text']
    parsed.requires_manual_review=not(bool(parsed.ingredients) and bool(parsed.instructions))
    return from_parsed(parsed)

@router.post("/imports/url",status_code=201)
async def import_url(body:ExternalImport,who:dict=Depends(principal)):
    try:
        provider=media.social_provider(body.url)
        if provider:
            details=await run_in_threadpool(media.run_exclusive,
                       media.extract_facebook if provider=="facebook" else media.extract_instagram,body.url)
            result=await recipes.parse_text(details['text'],source_url=details.get('source_url',''),source_type=provider)
            result.evidence=details['text'][:recipes.MAX_TEXT]
            if not (result.ingredients and result.instructions):result.requires_manual_review=True
        else:
            document,final_url=await recipes.page_html(body.url)
            result=recipes.parse_document(document,final_url)
            if not result.ingredients and not result.instructions:
                raise ValueError("Page did not expose a recipe; paste the recipe text or upload a screenshot")
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    data=result.model_dump()
    data['category_id']=body.category_id
    data['description']=data.get('description') or ''
    data['notes']=data.get('notes') or ''
    data['source_url']=data.get('source_url') or ''
    data['image_url']=data.get('image_url') or ''
    try:recipe=RecipePayload.model_validate(data)
    except ValueError as exc:raise HTTPException(422,"Could not validate extracted recipe") from exc
    return create_recipe(recipe,who)

@router.post("/imports/text",status_code=201)
async def import_text(body:dict,who:dict=Depends(principal)):
    content=body.get('text','')
    if not isinstance(content,str) or len(content)>47000:raise HTTPException(422,"Invalid import text")
    parsed=await recipes.parse_text(content)
    data=parsed.model_dump();data['notes']=data.get('notes') or '';data['description']=data.get('description') or ''
    return create_recipe(RecipePayload.model_validate(data),who)

@router.post("/imports/image",status_code=201)
async def import_image(file:UploadFile=File(...),who:dict=Depends(principal)):
    if file.content_type not in ('image/jpeg','image/png','image/webp'):raise HTTPException(415,"Use a JPG, PNG or WebP image")
    content=await file.read(10*1024*1024+1)
    if len(content)>10*1024*1024:raise HTTPException(413,"Image exceeds 10 MB")
    parsed=await recipes.parse_image(content)
    data=parsed.model_dump();data['notes']=data.get('notes') or '';data['description']=data.get('description') or ''
    return create_recipe(RecipePayload.model_validate(data),who)

@router.post("/imports/video",status_code=201)
async def import_video(file:UploadFile=File(...),who:dict=Depends(principal)):
    if not (file.content_type or '').startswith('video/'):raise HTTPException(415,"Use a video file")
    try:info=await run_in_threadpool(media.run_exclusive,media.process_local_file,file)
    finally:await file.close()
    parsed=await recipes.parse_text(info['text'],source_type='video')
    data=parsed.model_dump();data['notes']=data.get('notes') or '';data['description']=data.get('description') or ''
    data['evidence']=info['text'];data['requires_manual_review']=not(bool(parsed.ingredients) and bool(parsed.instructions))
    return create_recipe(RecipePayload.model_validate(data),who)

@router.post("/admin/import-json")
async def import_json(file:UploadFile=File(...),dry_run:bool=True,who:dict=Depends(admin)):
    """Copy-only KitchenOwl/Forknook JSON import; dry-run by default.

    Accepted formats: arrays of recipe objects, or {recipes:[...]}, or a
    Forknook export. Existing rows are never overwritten or deleted.
    """
    content=await file.read(8*1024*1024+1)
    if len(content)>8*1024*1024:raise HTTPException(413,"Import file exceeds 8 MB")
    try:data=json.loads(content)
    except (ValueError,UnicodeDecodeError) as exc:raise HTTPException(422,"Invalid JSON file") from exc
    entries=data if isinstance(data,list) else data.get('recipes',[]) if isinstance(data,dict) else []
    if not isinstance(entries,list) or len(entries)>MAX_IMPORTED_RECIPES:
        raise HTTPException(422,"Expected a recipe list with at most 1,000 entries")
    candidates=[]
    for record in entries:
        if not isinstance(record,dict):continue
        title=str(record.get('title') or record.get('name') or '').strip()
        if not title:continue
        raw=record.get('ingredients',record.get('items',[]))
        ingredients=[]
        for item in raw if isinstance(raw,list) else []:
            if isinstance(item,str):ingredients.append({'name':item,'raw':item})
            elif isinstance(item,dict):
                name=str(item.get('name') or item.get('text') or item.get('description') or '')
                if name:ingredients.append({'name':name,'raw':name})
        direction=record.get('instructions',record.get('steps',[]))
        if not isinstance(direction,list):
            direction=[s.strip() for s in str(direction or record.get('description') or '').splitlines() if s.strip()]
        try:
            model=RecipePayload(title=title[:300],description=str(record.get('description') or '')[:2000],
                  ingredients=ingredients[:200],instructions=[str(s)[:3000] for s in direction][:100],
                  notes=str(record.get('notes') or '')[:2000],source_url=str(record.get('source_url') or '')[:2000])
            candidates.append(model)
        except ValueError:continue
    if dry_run:
        return {'dry_run':True,'found':len(entries),'valid':len(candidates),'would_import':len(candidates),'changed':False}
    with LOCK,database() as db:
        added=0;now=time.time()
        for item in candidates:
            existing=db.execute('SELECT 1 FROM recipes WHERE title=? COLLATE NOCASE',(item.title,)).fetchone()
            if existing:continue
            db.execute("INSERT INTO recipes(title,data,created_by,created_at,updated_at) VALUES (?,?,?,?,?)",
                       (item.title,item.model_dump_json(),who['id'],now,now));added+=1
        db.execute('INSERT INTO migration_runs(source,attempted,imported,skipped,created_at) VALUES (?,?,?,?,?)',
            ('json',len(candidates),added,len(candidates)-added,time.time()))
        db.commit()
    return {'dry_run':False,'found':len(entries),'valid':len(candidates),'imported':added,'skipped':len(candidates)-added,
            'source_unchanged':True}

@router.get("/admin/updates/status")
def updates_status(who:dict=Depends(admin)):
    return {'installed':updater.installed(),'operation':updater.state()}

@router.post("/admin/updates/check")
async def updates_check(who:dict=Depends(admin)):
    return await run_in_threadpool(updater.check)

@router.post("/admin/updates/install",status_code=202)
def updates_install(who:dict=Depends(admin)):
    return updater.install()

@router.post("/admin/updates/rollback",status_code=202)
def updates_rollback(who:dict=Depends(admin)):
    return updater.rollback()

class AISettings(BaseModel):
    api_key: str | None = Field(default=None,max_length=2048)
    model: str = Field(default="gpt-4o-mini",min_length=2,max_length=80)

@router.get("/admin/ai")
def ai_status(who:dict=Depends(admin)):
    return settings_store.forknook_ai_status()

@router.post("/admin/ai")
def ai_save(body:AISettings,who:dict=Depends(admin)):
    settings_store.save_forknook_ai(api_key=body.api_key,model=body.model)
    return settings_store.forknook_ai_status()
