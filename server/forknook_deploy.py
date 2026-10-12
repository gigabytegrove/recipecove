"""Optional Dokploy bridge: image redeploys without Docker socket or sidecars.

The key is admin-only, stored on /data with 0600 mode and never returned by an API.
The Compose application remains managed by Dokploy. No volume deletion is used.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

DATA = Path(os.environ.get('DATA_DIR', '/data')) / 'updates'
CONFIG = DATA / 'dokploy.json'
COMPOSE_ID = re.compile(r'^[a-zA-Z0-9_-]{3,128}$')


def _origin(value: str) -> str:
    raw=value.strip().rstrip('/')
    try:
        parsed=urlsplit(raw)
        host=parsed.hostname
        _=parsed.port
    except ValueError:
        raise HTTPException(422, 'Invalid Dokploy URL')
    if not host or parsed.scheme not in ('http','https') or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise HTTPException(422, 'Enter the Dokploy base URL without a path, credentials, or query')
    if parsed.scheme == 'http':
        # Plain HTTP is never permitted over a public connection.
        try:
            addr=ipaddress.ip_address(host)
        except ValueError:
            if host not in ('localhost',) and not host.endswith('.local'):
                raise HTTPException(422, 'Use HTTPS for Dokploy URLs outside the private network')
        else:
            if not (addr.is_private or addr.is_loopback):
                raise HTTPException(422, 'Use HTTPS for Dokploy URLs outside the private network')
    return raw


def current() -> dict:
    try:
        cfg=json.loads(CONFIG.read_text('utf-8'))
        return {'configured':bool(cfg.get('api_key')),'url':cfg.get('url',''),
                'compose_id':cfg.get('compose_id','')}
    except (OSError, ValueError):
        return {'configured':False,'url':'','compose_id':''}


def _load() -> dict:
    try:
        cfg=json.loads(CONFIG.read_text('utf-8'))
    except (OSError, ValueError):
        raise HTTPException(409, 'Configure the Dokploy connection in Administration → Updates first')
    if not cfg.get('api_key'):
        raise HTTPException(409, 'Configure the Dokploy connection in Administration → Updates first')
    return cfg


def configure(url: str, compose_id: str, api_key: str | None) -> dict:
    url=_origin(url)
    if not COMPOSE_ID.fullmatch(compose_id.strip()):
        raise HTTPException(422, 'Invalid Dokploy Compose ID')
    prior={}
    if CONFIG.exists():
        try:prior=json.loads(CONFIG.read_text('utf-8'))
        except ValueError:pass
    # Reuse a key ONLY for the same URL/Compose ID. Never silently send a saved
    # secret to a different endpoint if an administrator edits the URL.
    key=(api_key or '').strip()
    if not key and prior.get('url')==url and prior.get('compose_id')==compose_id:
        key=prior.get('api_key','')
    if len(key)<12 or len(key)>4096:
        raise HTTPException(422, 'Provide a Dokploy API key (12–4096 characters)')
    DATA.mkdir(parents=True, exist_ok=True)
    temp=CONFIG.with_name('.dokploy-config-tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump({'url':url,'compose_id':compose_id,'api_key':key},f)
            f.flush();os.fsync(f.fileno())
        os.replace(temp,CONFIG)
        os.chmod(CONFIG,0o600)
    finally:
        if temp.exists():temp.unlink()
    return current()


def clear() -> dict:
    CONFIG.unlink(missing_ok=True)
    return current()


async def _dokploy_request(method: str, path: str, payload: dict | None = None) -> dict:
    cfg=_load()
    headers={'x-api-key':cfg['api_key'],'Accept':'application/json'}
    try:
        async with httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False) as client:
            r=await client.request(method,cfg['url']+'/api/'+path,
                                   headers=headers,json=payload)
    except httpx.RequestError as exc:
        raise HTTPException(502, 'Cannot reach Dokploy; check its URL and network access') from exc
    if r.status_code in (401,403):
        raise HTTPException(502, 'Dokploy rejected the API key or its Compose permissions')
    if not r.is_success:
        raise HTTPException(502, f'Dokploy returned HTTP {r.status_code}; check its deployment logs')
    try:
        return r.json()
    except ValueError:
        return {}


async def verify() -> dict:
    cfg=_load()
    info=await _dokploy_request('GET','compose.one?composeId='+cfg['compose_id'])
    obj=info.get('compose',info) if isinstance(info,dict) else {}
    if isinstance(obj,dict) and obj.get('composeId') and obj['composeId']!=cfg['compose_id']:
        raise HTTPException(502, 'Dokploy returned an unexpected Compose deployment')
    compose_text=(obj.get('composeFile') or '') if isinstance(obj,dict) else ''
    if not isinstance(compose_text,str) or 'ghcr.io/gigabytegrove/forknook:' not in compose_text:
        raise HTTPException(409, 'The selected Dokploy Compose application does not contain Forknook')
    # Check the exact service containing the Forknook image, not a different
    # Compose service that happens to have pull_policy: always.
    lines=compose_text.splitlines()
    candidates=[]
    for i,line in enumerate(lines):
        match=re.match(r'^([ \t]*)image:\s*["\']?ghcr\.io/gigabytegrove/forknook:([^"\' \t#]+)',line)
        if match:candidates.append((i,len(match.group(1).expandtabs(2)),match.group(2)))
    if len(candidates)!=1:
        raise HTTPException(409,'Exactly one Forknook image is required in the selected Dokploy Compose app')
    index,indent,tag=candidates[0]
    if tag!='latest':
        raise HTTPException(409,'Set the Forknook image to ghcr.io/gigabytegrove/forknook:latest in Dokploy')
    boundary=len(lines)
    for k in range(index+1,len(lines)):
        ln=lines[k]
        if ln.strip() and not ln.lstrip().startswith('#') and len(ln)-len(ln.lstrip())<indent:
            boundary=k;break
    # Include settings before the image within the same service as well.
    beginning=index
    while beginning>0:
        ln=lines[beginning-1]
        if ln.strip() and not ln.lstrip().startswith('#') and len(ln)-len(ln.lstrip())<indent:
            break
        beginning-=1
    service='\n'.join(lines[beginning:boundary])
    if not re.search(r'(?m)^\s*pull_policy:\s*["\']?always["\']?\s*(?:#.*)?$',service):
        raise HTTPException(409, 'Set pull_policy: always under Forknook in the Dokploy Compose file to ensure image updates are pulled')
    if '/data' not in service:
        raise HTTPException(409, 'Forknook data mount /data is missing from the Dokploy Compose service. Update refused')
    return {'ready':True,'message':'Dokploy connection verified; Compose will pull the newest Forknook image'}


async def deploy(version: str) -> dict:
    # The caller verifies that the source release exists and is newer.
    await verify()
    cfg=_load()
    # No freshVolumes! Preserve existing /data and all other mounts.
    await _dokploy_request('POST','compose.deploy',{
        'composeId':cfg['compose_id'],
        'title':'Forknook '+version,
        'description':'Forknook administrator requested image update',
        'freshVolumes':False,
    })
    return {'started':True,'target_version':version,
            'message':'Dokploy deployment started. The app may disconnect while Docker recreates it. Reload after deployment completes.'}
