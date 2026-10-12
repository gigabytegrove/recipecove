"""Optional Food.com community recipe catalogue, indexed locally using SQLite FTS5.

Never mixes discovery rows with the family's approved recipes. Only an
administrator can upload a dataset they have obtained and are entitled to use.
All processing stays inside the existing Forknook container and /data volume.
"""
from __future__ import annotations

import ast
import csv
import io
import json
import os
import re
import sqlite3
import threading
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

import forknook as base

router = APIRouter(prefix='/api/v2/discovery/dataset', tags=['Local recipe directory'])
_LOCK = threading.RLock()
_STATUS = {'phase':'idle','progress':0,'message':'No import running','processed':0}
MAX_UPLOAD = 610 * 1024 * 1024
MAX_UNPACKED = 610 * 1024 * 1024
MAX_ROWS = 260_000
MAX_FIELD_SIZE = 1_000_000

def _path() -> Path:
    return Path(os.environ.get('DATA_DIR','/data')) / 'discovery-foodcom.sqlite3'

def _state(**values):
    with _LOCK:
        _STATUS.update(values)

def ready() -> bool:
    return _path().is_file()

def stats() -> dict:
    result = {'ready': False,'count':0,'phase':'idle','progress':0,'message':'No Food.com collection installed'}
    with _LOCK:
        result.update(_STATUS)
    if _path().exists():
        try:
            with sqlite3.connect(f'file:{_path()}?mode=ro',uri=True,timeout=3) as db:
                count=db.execute("SELECT value FROM metadata WHERE key='count'").fetchone()
            result.update(ready=True,count=int(count[0]) if count else 0)
        except (sqlite3.Error,OSError,TypeError,ValueError):
            result.update(ready=False,count=0)
    return result

def _array(raw, max_entries=200):
    if isinstance(raw,list):
        seq=raw
    elif isinstance(raw,str):
        if len(raw)>MAX_FIELD_SIZE: return []
        try:
            seq=json.loads(raw)
        except (ValueError,TypeError):
            try: seq=ast.literal_eval(raw)
            except (ValueError,SyntaxError,TypeError,MemoryError,RecursionError): return []
    else:return []
    if not isinstance(seq,list):return []
    return [str(v).strip()[:3000] for v in seq[:max_entries] if isinstance(v,(str,int,float)) and str(v).strip()]

def _source(recipe_id:int,title:str):
    slug=re.sub(r'[^a-z0-9]+','-',title.lower()).strip('-')[:110]
    return f'https://www.food.com/recipe/{slug}-{recipe_id}'

def _connection(path:Path):
    con=sqlite3.connect(path,timeout=45)
    con.execute('PRAGMA journal_mode=DELETE')
    con.execute('PRAGMA synchronous=NORMAL')
    con.execute('CREATE TABLE recipes (recipe_id INTEGER PRIMARY KEY,title TEXT NOT NULL,ingredients TEXT NOT NULL,steps TEXT NOT NULL,tags TEXT NOT NULL,minutes INTEGER)')
    con.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    return con

def _index_csv(stream, destination:Path) -> int:
    """Stream a licensed RAW_recipes.csv into a temporary SQLite database."""
    destination.parent.mkdir(parents=True,exist_ok=True)
    work=destination.with_name('.foodcom-'+uuid.uuid4().hex+'.sqlite3')
    count=0
    con=None
    try:
        csv.field_size_limit(MAX_FIELD_SIZE)
        con=_connection(work)
        with io.TextIOWrapper(stream,encoding='utf-8-sig',errors='replace',newline='') as text_stream:
            reader=csv.DictReader(text_stream)
            if not reader.fieldnames or not {'id','name','ingredients','steps'}.issubset(set(reader.fieldnames)):
                raise ValueError('Expected Food.com RAW_recipes.csv with id, name, ingredients and steps columns')
            for row in reader:
                if count>=MAX_ROWS:raise ValueError('Dataset exceeds the supported 260,000 rows')
                try:
                    ident=int(row.get('id') or 0)
                    if ident<=0 or ident>2_000_000_000:continue
                    title=str(row.get('name') or '').strip()[:300]
                    ingredients=_array(row.get('ingredients'))
                    steps=_array(row.get('steps'),100)
                    tags=_array(row.get('tags'),50)
                    if not title or not ingredients or not steps:continue
                    minutes=int(row.get('minutes') or 0)
                    if minutes<0 or minutes>100_000:minutes=0
                except (ValueError,TypeError,OverflowError):continue
                con.execute('INSERT OR IGNORE INTO recipes VALUES(?,?,?,?,?,?)',
                   (ident,title,json.dumps(ingredients),json.dumps(steps),json.dumps(tags),minutes))
                count+=1
                if count%3000==0:
                    con.commit()
                    _state(phase='indexing',processed=count,progress=min(85,5+count*80//230000),
                           message=f'Indexing recipes: {count:,} processed')
        if count==0:raise ValueError('No valid Food.com recipes found')
        _state(phase='indexing',processed=count,progress=87,message='Building fast searchable index')
        # Keep the index distinct from the family's own SQLite database.
        con.execute("CREATE VIRTUAL TABLE recipe_index USING fts5(title,ingredients,tags,content='recipes',content_rowid='recipe_id',tokenize='unicode61 remove_diacritics 2')")
        con.execute("INSERT INTO recipe_index(recipe_index) VALUES('rebuild')")
        con.execute("INSERT INTO metadata(key,value) VALUES('count',?)",(str(count),))
        con.commit()
        con.close();con=None
        os.replace(work,destination)
        return count
    finally:
        if con is not None:con.close()
        work.unlink(missing_ok=True)

def _import_file(location:Path):
    try:
        if zipfile.is_zipfile(location):
            with zipfile.ZipFile(location) as archive:
                matches=[m for m in archive.infolist() if m.filename.split('/')[-1]=='RAW_recipes.csv' and not m.is_dir()]
                if len(matches)!=1:raise ValueError('ZIP must contain exactly one RAW_recipes.csv')
                entry=matches[0]
                if entry.file_size>MAX_UNPACKED or entry.file_size<32:
                    raise ValueError('Compressed dataset has invalid expanded size')
                with archive.open(entry) as stream:
                    count=_index_csv(stream,_path())
        else:
            with location.open('rb') as stream:
                count=_index_csv(stream,_path())
        _state(phase='complete',progress=100,processed=count,
               message=f'{count:,} recipes indexed. Search them in Discover recipes.')
    except Exception as exc:
        _state(phase='failed',progress=0,
               message='Dataset import failed: '+str(exc)[:180])
    finally:
        location.unlink(missing_ok=True)

def search(term:str,mode:str='name',limit:int=60) -> list[dict]:
    if not ready():return []
    words=re.findall(r'[^\W_]+',term.casefold(),re.UNICODE)[:8]
    if not words:return []
    # FTS expression is built only from quoted alphanumeric tokens.
    expression=' AND '.join(
        ('title:' if mode=='name' else 'ingredients:')+'"'+word.replace('"','')+'"' for word in words
    )
    try:
        with sqlite3.connect(f'file:{_path()}?mode=ro',uri=True,timeout=5) as con:
            con.row_factory=sqlite3.Row
            rows=con.execute("""
             SELECT r.recipe_id,r.title,r.ingredients,r.steps,r.tags,r.minutes
             FROM recipe_index JOIN recipes AS r ON r.recipe_id=recipe_index.rowid
             WHERE recipe_index MATCH ? ORDER BY bm25(recipe_index,5,1,0.3) LIMIT ?
             """,(expression,max(1,min(limit,80)))).fetchall()
    except sqlite3.Error:return []
    out=[]
    for r in rows:
        count=len(_array(r['ingredients']))
        out.append({'provider':'foodcom','id':str(r['recipe_id']),'title':r['title'],
           'image_url':'','description':f'{count} ingredients · {r["minutes"]} min · Food.com community',
           'source_url':_source(r['recipe_id'],r['title'])})
    return out

def preview(recipe_id:str) -> dict:
    if not re.fullmatch(r'\d{1,10}',recipe_id):raise HTTPException(422,'Invalid Food.com recipe ID')
    if not ready():raise HTTPException(409,'Install the Food.com collection first')
    try:
        with sqlite3.connect(f'file:{_path()}?mode=ro',uri=True,timeout=5) as con:
            con.row_factory=sqlite3.Row
            row=con.execute('SELECT * FROM recipes WHERE recipe_id=?',(int(recipe_id),)).fetchone()
    except sqlite3.Error as exc:
        raise HTTPException(503,'Community recipe index is unavailable') from exc
    if row is None:raise HTTPException(404,'Recipe not found in the local collection')
    return base.RecipePayload(
        title=row['title'],ingredients=[{'name':text[:500],'raw':text[:500]} for text in _array(row['ingredients'])],
        instructions=_array(row['steps'],100),tags=_array(row['tags'],50),
        total_time=row['minutes'] or None,source_url=_source(row['recipe_id'],row['title']),
        description='Food.com community recipe. Review the source, ingredients and directions before adding to your family.',
        requires_manual_review=True
    ).model_dump()

@router.get('/status')
def status(who:dict=Depends(base.admin)):
    return stats()

@router.post('/import',status_code=202)
async def start_import(file:UploadFile=File(...),accept_license:bool=Form(False),
                       who:dict=Depends(base.admin)):
    if not accept_license:
        raise HTTPException(422,'Confirm that you have permission to use the supplied recipe dataset')
    if not file.filename or not file.filename.lower().endswith(('.csv','.zip')):
        raise HTTPException(422,'Upload RAW_recipes.csv or a ZIP containing it')
    with _LOCK:
        if _STATUS['phase'] in ('uploading','indexing'):
            raise HTTPException(409,'A dataset import is already running')
        _STATUS.update(phase='uploading',progress=0,processed=0,message='Receiving recipe dataset')
    location=_path().parent / ('.foodcom-upload-'+uuid.uuid4().hex)
    total=0
    try:
        location.parent.mkdir(parents=True,exist_ok=True)
        with location.open('xb') as output:
            while True:
                part=await file.read(1024*1024)
                if not part:break
                total+=len(part)
                if total>MAX_UPLOAD:raise HTTPException(413,'Dataset exceeds the maximum upload size')
                output.write(part)
        if total<32:raise HTTPException(422,'Dataset file is empty')
    except Exception:
        location.unlink(missing_ok=True)
        _state(phase='failed',progress=0,message='Dataset upload was interrupted')
        raise
    finally:
        await file.close()
    _state(phase='indexing',progress=4,message='Building searchable recipe collection')
    thread=threading.Thread(target=_import_file,args=(location,),daemon=True,name='forknook-foodcom-import')
    thread.start()
    return {'started':True,'message':'Import started. Existing family recipes are unchanged.'}
