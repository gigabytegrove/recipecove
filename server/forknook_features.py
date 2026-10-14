"""Forknook 2.1: approvals, migration, integration APIs.

This module deliberately extends the existing SQLite store instead of replacing it.
Old members become contributors and every pre-existing recipe stays published
inside the *family* collection (never to external API consumers).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

import httpx
from fastapi.responses import FileResponse
import forknook_images
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

import forknook as base

router=APIRouter(prefix='/api/v2',tags=['Forknook family workflows'])
ROLES=('admin','approver','contributor','viewer')
DEFAULT_CATEGORIES=(
    ('Breakfast','#E7A44B'),('Lunch','#A6B65B'),('Dinner','#59755A'),
    ('Appetizers','#CF8973'),('Side Dishes','#9EAE73'),('Soups & Stews','#BF835F'),
    ('Salads','#78A49E'),('Desserts','#C47B98'),('Baking & Bread','#BB955D'),
    ('Slow Cooker','#9D8667'),('Drinks','#6FA4AB'),('Snacks','#C9A66B'),
    ('Sauces & Dressings','#C67E58'),('Holiday & Special Occasion','#AF889E'),
)


def initialize():
    with base.LOCK,base.database() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS role_assignments(
           user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
           role TEXT NOT NULL CHECK(role IN ('admin','approver','contributor','viewer')));
        CREATE TABLE IF NOT EXISTS family_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS user_preferences(
           user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
           submit_for_review INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS recipe_submissions(
           id INTEGER PRIMARY KEY, data TEXT NOT NULL, title TEXT NOT NULL,
           submitted_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
           status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled')),
           submitted_at REAL NOT NULL, reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
           reviewed_at REAL, decision_note TEXT NOT NULL DEFAULT '', recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL);
        CREATE INDEX IF NOT EXISTS sub_status_idx ON recipe_submissions(status,submitted_at);
        CREATE TABLE IF NOT EXISTS api_keys(
           id INTEGER PRIMARY KEY, label TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
           scope TEXT NOT NULL DEFAULT 'recipes:read', created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
           created_at REAL NOT NULL, last_used REAL, revoked_at REAL);
        ''')
        db.execute("INSERT OR IGNORE INTO family_settings(key,value) VALUES ('require_approval','false')")
        for name,color in DEFAULT_CATEGORIES:
            db.execute('INSERT OR IGNORE INTO categories(name,color) VALUES (?,?)',(name,color))
        # Existing accounts and roles are left untouched. Legacy member -> contributor.
        db.commit()


def effective_role(db,user):
    result=db.execute('SELECT role FROM role_assignments WHERE user_id=?',(user['id'],)).fetchone()
    return result['role'] if result else ('admin' if user['role']=='admin' else 'contributor')


def can_approve(who):
    return who['role'] in ('admin','approver')


def can_contribute(who):
    return who['role'] in ('admin','approver','contributor')


def require_contributor(who):
    if not can_contribute(who):raise HTTPException(403,'This account can view recipes but cannot submit or modify them')


def approval_required(db,who):
    global_setting=db.execute("SELECT value FROM family_settings WHERE key='require_approval'").fetchone()
    pref=db.execute('SELECT submit_for_review FROM user_preferences WHERE user_id=?',(who['id'],)).fetchone()
    return bool((global_setting and global_setting['value']=='true' and not can_approve(who)) or
                (pref and pref['submit_for_review'] and who['role']!='admin'))


def submit_recipe(db,model,who):
    row=db.execute('''INSERT INTO recipe_submissions(data,title,submitted_by,submitted_at)
        VALUES (?,?,?,?)''',(model.model_dump_json(),model.title.strip(),who['id'],time.time()))
    return {'id':row.lastrowid,'pending':True,'message':'Sent for family approval; not yet in the recipe collection.'}


class ApprovalSetting(BaseModel):
    require_approval:bool

class UserPreference(BaseModel):
    submit_for_review:bool

class Decision(BaseModel):
    reason:str=Field(default='',max_length=1000)

@router.get('/family/settings')
def get_family_settings(who:dict=Depends(base.principal)):
    with base.database() as db:
        global_setting=db.execute("SELECT value FROM family_settings WHERE key='require_approval'").fetchone()
        pref=db.execute('SELECT submit_for_review FROM user_preferences WHERE user_id=?',(who['id'],)).fetchone()
        return {'require_approval':bool(global_setting and global_setting['value']=='true'),
                'my_submit_for_review':bool(pref and pref['submit_for_review']),
                'can_approve':can_approve(who),'can_contribute':can_contribute(who)}

@router.put('/admin/family/settings')
def save_family_settings(body:ApprovalSetting,who:dict=Depends(base.admin)):
    with base.LOCK,base.database() as db:
        db.execute("INSERT INTO family_settings(key,value) VALUES('require_approval',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",('true' if body.require_approval else 'false',))
    return {'require_approval':body.require_approval}

@router.put('/me/preferences')
def save_preferences(body:UserPreference,who:dict=Depends(base.principal)):
    with base.LOCK,base.database() as db:
        db.execute('''INSERT INTO user_preferences(user_id,submit_for_review) VALUES (?,?)
        ON CONFLICT(user_id) DO UPDATE SET submit_for_review=excluded.submit_for_review''',(who['id'],int(body.submit_for_review)))
    return {'submit_for_review':body.submit_for_review}

@router.get('/submissions')
def list_submissions(status:str='pending',who:dict=Depends(base.principal)):
    if status not in ('pending','approved','rejected','cancelled','all'):
        raise HTTPException(422,'Invalid review status')
    with base.database() as db:
        where=[];args=[]
        if not can_approve(who):where.append('s.submitted_by=?');args.append(who['id'])
        if status!='all':where.append('s.status=?');args.append(status)
        sql='''SELECT s.*,u.display_name AS requested_by, r.display_name AS reviewed_by_name
                 FROM recipe_submissions s JOIN users u ON u.id=s.submitted_by
                 LEFT JOIN users r ON r.id=s.reviewed_by'''
        if where:sql+=' WHERE '+' AND '.join(where)
        sql+=' ORDER BY s.submitted_at DESC LIMIT 200'
        return [{**dict(row),'data':json.loads(row['data'])} for row in db.execute(sql,args)]

@router.post('/submissions/{submission_id}/approve')
def approve(submission_id:int,body:Decision,who:dict=Depends(base.principal)):
    if not can_approve(who):raise HTTPException(403,'Recipe approval permission required')
    with base.LOCK,base.database() as db:
        s=db.execute('SELECT * FROM recipe_submissions WHERE id=?',(submission_id,)).fetchone()
        if not s:raise HTTPException(404,'Submission not found')
        if s['status']!='pending':raise HTTPException(409,'Submission has already been reviewed')
        model=base.RecipePayload.model_validate_json(s['data'])
        base._category_exists(db,model.category_id)
        base._folders_exist(db,model.folder_ids)
        now=time.time()
        recipe=db.execute('''INSERT INTO recipes(title,data,category_id,created_by,created_at,updated_at)
           VALUES (?,?,?,?,?,?)''',(model.title.strip(),model.model_dump_json(),model.category_id,s['submitted_by'],now,now))
        db.execute('''UPDATE recipe_submissions SET status='approved',reviewed_by=?,reviewed_at=?,decision_note=?,recipe_id=? WHERE id=? AND status='pending' ''',
                   (who['id'],now,body.reason,recipe.lastrowid,submission_id))
        return {'approved':True,'recipe_id':recipe.lastrowid}

@router.post('/submissions/{submission_id}/reject')
def reject(submission_id:int,body:Decision,who:dict=Depends(base.principal)):
    if not can_approve(who):raise HTTPException(403,'Recipe approval permission required')
    if not body.reason.strip():raise HTTPException(422,'Please provide a reason')
    with base.LOCK,base.database() as db:
        n=db.execute("UPDATE recipe_submissions SET status='rejected',reviewed_by=?,reviewed_at=?,decision_note=? WHERE id=? AND status='pending'",
                     (who['id'],time.time(),body.reason,submission_id)).rowcount
        if not n:raise HTTPException(409,'Submission is missing or already reviewed')
    return {'rejected':True}

@router.post('/submissions/{submission_id}/cancel')
def cancel(submission_id:int,who:dict=Depends(base.principal)):
    with base.LOCK,base.database() as db:
        n=db.execute("UPDATE recipe_submissions SET status='cancelled' WHERE id=? AND submitted_by=? AND status='pending'",
                     (submission_id,who['id'])).rowcount
        if not n:raise HTTPException(409,'Submission is missing or cannot be cancelled')
    return {'cancelled':True}


class ApiKeyCreate(BaseModel):
    label:str=Field(min_length=3,max_length=100)

@router.post('/admin/api-keys',status_code=201)
def create_api_key(body:ApiKeyCreate,who:dict=Depends(base.admin)):
    key='fnk_'+secrets.token_urlsafe(42)
    with base.LOCK,base.database() as db:
        row=db.execute('INSERT INTO api_keys(label,token_hash,created_by,created_at) VALUES (?,?,?,?)',
                       (body.label.strip(),hashlib.sha256(key.encode()).hexdigest(),who['id'],time.time()))
    return {'id':row.lastrowid,'key':key,'scope':'recipes:read','warning':'Save this key now. It cannot be retrieved again.'}

@router.get('/admin/api-keys')
def list_api_keys(who:dict=Depends(base.admin)):
    with base.database() as db:
        return [dict(r) for r in db.execute('SELECT id,label,scope,created_at,last_used,revoked_at FROM api_keys ORDER BY id DESC')]

@router.delete('/admin/api-keys/{key_id}')
def revoke_api_key(key_id:int,who:dict=Depends(base.admin)):
    with base.LOCK,base.database() as db:
        result=db.execute('UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL',(time.time(),key_id))
        if not result.rowcount:raise HTTPException(404,'Active API key not found')
    return {'revoked':True}


def public_key(x_forknook_api_key:str|None=Header(default=None)):
    key=x_forknook_api_key or ''
    if not key.startswith('fnk_') or len(key)>256:raise HTTPException(401,'Valid Forknook API key required')
    digest=hashlib.sha256(key.encode()).hexdigest()
    with base.LOCK,base.database() as db:
        row=db.execute('SELECT id FROM api_keys WHERE token_hash=? AND revoked_at IS NULL AND scope=?',
                       (digest,'recipes:read')).fetchone()
        if not row:raise HTTPException(401,'Invalid or revoked API key')
        db.execute('UPDATE api_keys SET last_used=? WHERE id=?',(time.time(),row['id']))
    return row['id']


def public_recipe(row):
    parsed=json.loads(row['data'])
    # Never publish notes, import source evidence, submitter identities,
    # shopping/meal data, internal timestamps or review metadata.
    fields=('title','description','ingredients','instructions','servings','prep_time','cook_time','total_time','image_url','tags')
    result={'id':row['id'],'slug':re.sub(r'[^a-z0-9]+','-',row['title'].lower()).strip('-')+'-'+str(row['id']),
            **{name:parsed.get(name) for name in fields if parsed.get(name) is not None}}
    if forknook_images.LOCAL_IMAGE.fullmatch(result.get('image_url') or ''):
        result['image_url']='/api/v2/public/recipes/'+str(row['id'])+'/image'
    return result

@router.get('/public/recipes')
def published_recipes(page:int=Query(1,ge=1,le=100000),limit:int=Query(20,ge=1,le=100),q:str=Query('',max_length=100),key:int=Depends(public_key)):
    with base.database() as db:
        rows=db.execute('SELECT id,title,data FROM recipes ORDER BY updated_at DESC').fetchall()
    visible=[public_recipe(row) for row in rows if json.loads(row['data']).get('published') is True]
    if q:visible=[r for r in visible if q.casefold() in r['title'].casefold()]
    return {'page':page,'limit':limit,'total':len(visible),'items':visible[(page-1)*limit:page*limit]}

@router.get('/public/recipes/{recipe_id}')
def published_recipe(recipe_id:int,key:int=Depends(public_key)):
    with base.database() as db:
        row=db.execute('SELECT id,title,data FROM recipes WHERE id=?',(recipe_id,)).fetchone()
    if not row or json.loads(row['data']).get('published') is not True:raise HTTPException(404,'Published recipe not found')
    return public_recipe(row)


@router.get('/public/recipes/{recipe_id}/image')
def public_recipe_image(recipe_id:int,key:int=Depends(public_key)):
    with base.database() as db:
        row=db.execute('SELECT data FROM recipes WHERE id=?',(recipe_id,)).fetchone()
    if not row:raise HTTPException(404,'Published recipe not found')
    recipe=json.loads(row['data'])
    if recipe.get('published') is not True:raise HTTPException(404,'Published recipe not found')
    img=forknook_images.get_file(recipe.get('image_url',''))
    return FileResponse(img,media_type='image/jpeg',headers={'Cache-Control':'private, max-age=3600','X-Content-Type-Options':'nosniff'})


def validate_kitchenowl_llt(token: str) -> str:
    """Check JWT formatting without inspecting or trusting claims."""
    value=token.strip()
    if value.lower().startswith('bearer '):
        raise HTTPException(422,'Enter the complete KitchenOwl token without Bearer.')
    parts=value.split('.')
    if len(parts)!=3 or any(not re.fullmatch(r'[A-Za-z0-9_-]+',part) for part in parts):
        raise HTTPException(422,'The KitchenOwl long-lived token is incomplete or malformed. Copy the entire token directly from KitchenOwl.')
    try:
        head=json.loads(base64.urlsafe_b64decode(parts[0]+'='*(-len(parts[0])%4)))
        if not isinstance(head,dict) or head.get('alg') not in ('HS256','HS384','HS512'):
            raise ValueError('Unsupported token header')
        sig=base64.urlsafe_b64decode(parts[2]+'='*(-len(parts[2])%4))
        if len(sig)!={'HS256':32,'HS384':48,'HS512':64}[head['alg']]:
            raise ValueError('Incomplete token signature')
    except (ValueError,UnicodeError):
        raise HTTPException(422,'The KitchenOwl long-lived token is incomplete or malformed (invalid JWT header or signature). Generate a new token and copy the full value.')
    return value


class KitchenOwlSource(BaseModel):
    url:str=Field(min_length=8,max_length=2048)
    token:str=Field(min_length=12,max_length=4096)
    household_id:int|None=Field(default=None,ge=1)
    dry_run:bool=True


def ko_origin(raw:str)->str:
    try:
        parsed=urlsplit(raw.strip())
        port=parsed.port
    except ValueError as exc:raise HTTPException(422,'Invalid KitchenOwl URL') from exc
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment or port==0:
        raise HTTPException(422,'Enter the KitchenOwl server URL, without a path or credentials')
    try:
        addr=ipaddress.ip_address(parsed.hostname)
        if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified or addr.is_reserved:
            raise HTTPException(422,'Disallowed KitchenOwl server address')
    except ValueError:pass
    return raw.rstrip('/')


async def ko_get(client:httpx.AsyncClient,origin:str,token:str,path:str):
    # The administrator supplies a deliberate KitchenOwl URL. Never follow
    # redirects, keep tokens in request headers, and limit response bytes.
    try:
        async with client.stream('GET',origin+path,headers={'Authorization':'Bearer '+token,'Accept':'application/json'}) as r:
            if r.status_code==404 and re.fullmatch(r'/api/recipe/\d+',path):
                # A recipe can disappear between listing and retrieval.
                # Report it without aborting all the other recipes.
                return None
            if r.status_code!=200:
                # Flask-JWT-Extended commonly returns 422 for an invalid JWT.
                # Read only a bounded diagnostic and never echo credentials.
                sample=bytearray()
                async for chunk in r.aiter_bytes():
                    sample.extend(chunk[:2048-len(sample)])
                    if len(sample)>=2048:break
                reason=''
                if len(sample)<2048:
                    try:
                        message=json.loads(sample)
                        if isinstance(message,dict):
                            reason=str(message.get('msg') or message.get('message') or '')
                    except (ValueError,UnicodeError):pass
                reason=re.sub(r'[^a-zA-Z0-9 ,.:;!?_()\/-]',' ',reason)[:120].strip()
                body_forbidden=r.status_code in (401,403,422)
                if body_forbidden:
                    detail=f'KitchenOwl rejected the authentication token (HTTP {r.status_code}). Confirm the long-lived token is from this instance; paste the token only, without Bearer or quotes.'
                else:
                    detail=f'KitchenOwl API returned HTTP {r.status_code} for {path.split("/")[-1] or "households"}'
                known_auth_errors={
                    'Signature verification failed',
                    'Subject must be a string',
                    'Not enough segments',
                    'Invalid header padding',
                    'The token has expired',
                    'Token has expired',
                    'Token has been revoked',
                    'Only non-refresh tokens are allowed',
                    'The specified alg value is not allowed',
                    'Missing Authorization Header',
                }
                if reason and (not body_forbidden or reason in known_auth_errors):
                    detail+=': '+reason
                if reason=='Subject must be a string':
                    detail+='. KitchenOwl may need JWT_VERIFY_SUB=False for numeric subject compatibility.'
                raise HTTPException(502,detail)
            data=bytearray()
            async for chunk in r.aiter_bytes():
                data.extend(chunk)
                if len(data)>4_000_000:raise HTTPException(413,'KitchenOwl response exceeded 4 MB')
        return json.loads(data)
    except (httpx.HTTPError,ValueError) as exc:
        raise HTTPException(502,'Could not read the KitchenOwl API response') from exc

@router.post('/admin/kitchenowl/households')
async def kitchenowl_households(body:KitchenOwlSource,who:dict=Depends(base.admin)):
    origin=ko_origin(body.url)
    token=validate_kitchenowl_llt(body.token)
    async with httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False) as client:
        data=await ko_get(client,origin,token,'/api/household')
    if isinstance(data,dict):data=data.get('results',data.get('households',[]))
    if not isinstance(data,list):raise HTTPException(502,'KitchenOwl did not return a household list')
    return [{'id':int(x['id']),'name':str(x.get('name','Household'))[:100]} for x in data if isinstance(x,dict) and isinstance(x.get('id'),int)][:100]


def _ko_recipe(record:dict)->base.RecipePayload:
    """Convert an actual KitchenOwl record, including Markdown instructions.

    KitchenOwl stores recipe instructions in `description` on some versions;
    older Recipe Importer writes `## Instructions` and `## Notes` there. Never
    mistake that entire Markdown block for the recipe's short description.
    """
    title=str(record.get('name') or record.get('title') or '').strip()[:300]
    if not title:raise ValueError('Recipe has no title')
    raw=record.get('items') or record.get('ingredients') or []
    if isinstance(raw,dict):raw=list(raw.keys())
    ingredients=[]
    for item in raw if isinstance(raw,list) else []:
        if isinstance(item,str):text=item
        elif isinstance(item,dict):
            name=str(item.get('name') or item.get('text') or item.get('raw') or '').strip()
            qualifier=str(item.get('description') or item.get('quantity') or '').strip()
            unit=str(item.get('unit') or '').strip()
            text=' '.join(x for x in (qualifier,unit,name) if x).strip()
            if item.get('optional'):text+=' (optional)'
        else:continue
        if text.strip():ingredients.append({'name':text[:500],'raw':text[:500]})

    full=str(record.get('description') or '')
    parts=re.split(r'(?im)^\s*#{1,6}\s+(Ingredients|Instructions|Directions|Steps|Method|Notes)\s*$',full)
    summary=parts[0].strip()
    embedded_steps=[]
    embedded_notes=[]
    for index in range(1,len(parts)-1,2):
        heading=parts[index].casefold()
        section=parts[index+1].strip()
        if heading=='notes':
            if section:embedded_notes.append(section)
        elif heading=='ingredients':
            if not ingredients:
                for line in section.splitlines():
                    line=line.lstrip(' -*•0123456789.)').strip()
                    if line:ingredients.append({'name':line[:500],'raw':line[:500]})
        else:
            for line in section.splitlines():
                line=re.sub(r'^\s*(?:\d+[.)]|[-*•])\s+','',line).strip()
                if line:embedded_steps.append(line)
    if not embedded_steps and '\n\n' in full:
        # KitchenOwl exports often have an introductory paragraph followed by
        # plain-text directions rather than a Markdown Instructions heading.
        intro, remainder=full.split('\n\n',1)
        action=re.compile(r'^(?:in |add |mix |stir |place |heat |cook |bake |preheat |pour |'
                          r'combine |remove |transfer |knead |roll |fill |cut |'
                          r'whisk |melt |serve |brush |cover |let |put |'
                          r'beat |mash |soften |gather |wash |drain |'
                          r'using |while |once |after |when |working )',re.I)
        candidate_lines=[line.strip() for line in remainder.splitlines() if line.strip()]
        if candidate_lines and sum(bool(action.match(line)) for line in candidate_lines)>=2 and sum(bool(action.match(line)) for line in candidate_lines)/len(candidate_lines)>=0.3:
            summary=intro.strip()
            embedded_steps=candidate_lines
    steps=record.get('instructions') or record.get('steps') or embedded_steps
    if isinstance(steps,str):steps=[x.strip() for x in steps.splitlines() if x.strip()]
    if isinstance(steps,list):
        steps=[str(x.get('text') or x.get('description') or '') if isinstance(x,dict) else str(x) for x in steps]
    else:steps=[]
    notes='\n\n'.join(x for x in (str(record.get('notes') or '').strip(),*embedded_notes) if x)
    photo=record.get('image_url') or record.get('photo') or record.get('image') or record.get('picture') or ''
    if isinstance(photo,dict):photo=photo.get('url') or photo.get('src') or ''
    if not isinstance(photo,str):photo=''
    servings=record.get('servings') or record.get('yields') or ''
    if isinstance(servings,int):servings=str(servings)+' servings'
    def safe_time(key):
        try:return max(0,int(record[key])) if record.get(key) is not None else None
        except (TypeError,ValueError,OverflowError):return None
    return base.RecipePayload(title=title,image_url=photo[:2000],description=summary[:6000],
        ingredients=ingredients[:200],instructions=[x.strip()[:3000] for x in steps if x.strip()][:100],
        notes=notes[:30000],servings=str(servings)[:200] or None,
        prep_time=safe_time('prep_time'),cook_time=safe_time('cook_time'),
        total_time=safe_time('total_time') or safe_time('time'),
        source_url=str(record.get('source_url') or record.get('source') or '')[:2000])


@router.post('/admin/kitchenowl/import')
async def kitchenowl_import(body:KitchenOwlSource,who:dict=Depends(base.admin)):
    if body.household_id is None:raise HTTPException(422,'Select a KitchenOwl household')
    origin=ko_origin(body.url)
    token=validate_kitchenowl_llt(body.token)
    async with httpx.AsyncClient(timeout=25,trust_env=False,follow_redirects=False) as client:
        data=await ko_get(client,origin,token,f'/api/household/{body.household_id}/recipe')
        if isinstance(data,dict):data=data.get('results',data.get('recipes',[]))
        if not isinstance(data,list):raise HTTPException(502,'KitchenOwl did not return a recipe list')
        if len(data)>base.MAX_IMPORTED_RECIPES:raise HTTPException(422,'More than 1000 recipes; migrate in batches')
        items=[];invalid=0;unavailable_ids=[]
        for summary in data:
            if not isinstance(summary,dict):invalid+=1;continue
            item_id=summary.get('id')
            if not isinstance(item_id,int) or item_id<1:invalid+=1;continue
            # KitchenOwl lists a household's recipes here, but its DETAIL
            # endpoint lives at /api/recipe/{id} (no household in path).
            detail=await ko_get(client,origin,token,f'/api/recipe/{item_id}')
            if detail is None:
                unavailable_ids.append(item_id)
                continue
            if not isinstance(detail,dict):invalid+=1;continue
            try:
                candidate=_ko_recipe(detail)
                # Some KitchenOwl versions supply the photo only in the list response.
                if not candidate.image_url:
                    summary_photo=summary.get('photo') or summary.get('image_url') or summary.get('image') or ''
                    if isinstance(summary_photo,dict):
                        summary_photo=summary_photo.get('url') or summary_photo.get('src') or ''
                    if isinstance(summary_photo,str) and summary_photo:
                        candidate=candidate.model_copy(update={'image_url':summary_photo[:2000]})
                photo=candidate.image_url
                if photo:
                    parts=urlsplit(photo)
                    ko_parts=urlsplit(origin)
                    # Fetch relative or same-origin authenticated KitchenOwl images.
                    # Never send the KitchenOwl token to a different host.
                    same_origin=(parts.scheme,parts.netloc)==(ko_parts.scheme,ko_parts.netloc)
                    relative=not parts.scheme and not parts.netloc and bool(photo.strip())
                    media_path=(('/'+photo.lstrip('/')) if relative else parts.path if same_origin else '')
                    media_query=parts.query if (relative or same_origin) else ''
                    if media_path.startswith('/') and '..' not in media_path.split('/') and not parts.fragment and not body.dry_run:
                        try:
                            image_request=origin+media_path+('?' + media_query if media_query else '')
                            async with client.stream('GET',image_request,headers={'Authorization':'Bearer '+token}) as img_response:
                                if img_response.status_code==200 and img_response.headers.get('content-type','').startswith('image/'):
                                    buf=bytearray()
                                    async for chunk in img_response.aiter_bytes():
                                        buf.extend(chunk)
                                        if len(buf)>forknook_images.MAX_BYTES:raise ValueError('KitchenOwl image is too large')
                                    candidate=candidate.model_copy(update={'image_url':forknook_images.store_upload(bytes(buf))})
                        except (ValueError,httpx.HTTPError,OSError):
                            pass  # Never block recipe migration because a photo is unavailable.
                    elif same_origin and photo.startswith('http'):
                        # Do not preserve an internal KitchenOwl URL if it is not fetchable.
                        candidate=candidate.model_copy(update={'image_url':photo})
                items.append((item_id,candidate))
            except (ValueError,TypeError):invalid+=1
    if not body.dry_run:
        items=[(idx, recipe.model_copy(update={'image_url':forknook_images.capture_best_effort(recipe.image_url)})
                if recipe.image_url else recipe) for idx,recipe in items]
    source_id=hashlib.sha256(origin.casefold().encode()).hexdigest()[:24]
    with base.LOCK,base.database() as db:
        new=[];skipped=0
        for recipe_id,item in items:
            ref=f'kitchenowl:{source_id}:{body.household_id}:{recipe_id}'
            existing=db.execute('SELECT id,data FROM recipes WHERE external_ref=?',(ref,)).fetchone()
            if existing:
                skipped+=1
                # Repair missing import fields, preserving existing family edits.
                if not body.dry_run:
                    saved=json.loads(existing['data'])
                    changed=False
                    previous=str(saved.get('image_url') or '')
                    if str(item.image_url).startswith('/api/v2/images/') and not previous.startswith('/api/v2/images/'):
                        saved['image_url']=item.image_url
                        changed=True
                    for field in ('ingredients','instructions','notes'):
                        if not saved.get(field) and getattr(item,field,None):
                            saved[field]=getattr(item,field)
                            changed=True
                    old_description=str(saved.get('description') or '')
                    if ('## Instructions' in old_description or '## Ingredients' in old_description) and item.description:
                        saved['description']=item.description
                        changed=True
                    if changed:
                        db.execute('UPDATE recipes SET data=?,updated_at=? WHERE id=?',
                            (json.dumps(saved),time.time(),existing['id']))
            else:new.append((ref,item))
        if body.dry_run:
            return {'dry_run':True,'found':len(data),'valid':len(items),'invalid':invalid,
                    'would_import':len(new),'already_imported':skipped,'unavailable':len(unavailable_ids),
                    'unavailable_ids':unavailable_ids,'source_unchanged':True}
        now=time.time()
        for ref,item in new:
            db.execute('''INSERT INTO recipes(title,data,created_by,created_at,updated_at,external_ref)
                 VALUES(?,?,?,?,?,?)''',(item.title,item.model_dump_json(),who['id'],now,now,ref))
        db.execute('INSERT INTO migration_runs(source,attempted,imported,skipped,created_at) VALUES (?,?,?,?,?)',
                   ('kitchenowl:'+source_id+':'+str(body.household_id),len(items),len(new),skipped+invalid+len(unavailable_ids),time.time()))
        return {'dry_run':False,'found':len(data),'valid':len(items),'invalid':invalid,
                'imported':len(new),'already_imported':skipped,'unavailable':len(unavailable_ids),
                'unavailable_ids':unavailable_ids,'source_unchanged':True}
