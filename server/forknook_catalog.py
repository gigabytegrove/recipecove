"""Read-only recipe discovery providers for the standalone Forknook RMS.

Only hardcoded public catalog APIs are queried (no user-supplied request URLs).
Searching and previewing never saves a recipe; the normal /recipes endpoint
remains responsible for approval, visibility, and durable image capture.
"""
from __future__ import annotations

import asyncio
import os
import re
import html as html_util
from urllib.parse import quote, urlsplit, unquote

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

import forknook as base
import forknook_dataset as dataset
import forknook_images

router = APIRouter(prefix='/api/v2/discovery', tags=['Recipe discovery'])
MEALDB = 'https://www.themealdb.com/api/json/v1'
WIKIBOOKS = 'https://en.wikibooks.org/w/api.php'
USER_AGENT = 'Forknook/2.1 (self-hosted recipe discovery; contact via GitHub gigabytegrove/forknook)'
WP_SITES = {
    'budgetbytes': ('Budget Bytes','www.budgetbytes.com'),
    'spendwithpennies': ('Spend With Pennies','www.spendwithpennies.com'),
    'skinnytaste': ('Skinnytaste','www.skinnytaste.com'),
    'thechunkychef': ('The Chunky Chef','www.thechunkychef.com'),
    'wellplated': ('Well Plated','www.wellplated.com'),
    'therecipecritic': ('The Recipe Critic','www.therecipecritic.com'),
    'saltandlavender': ('Salt & Lavender','www.saltandlavender.com'),
    'ambitiouskitchen': ('Ambitious Kitchen','www.ambitiouskitchen.com'),
}
PROVIDERS = [
    {'id':'all','name':'All recipe sources','search_by':['name','ingredient'],
     'description':'Search the entire installed community index and online recipe publishers at once.',
     'website':''},
    {'id':'foodcom','name':'Food.com community collection','search_by':['name','ingredient'],
     'description':'Optional locally indexed Food.com collection of ~230,000 recipes; install through Administration.',
     'website':'https://www.kaggle.com/datasets/shuyangli94/food-com-recipes-and-user-interactions'},
    *[{'id':key,'name':label,'search_by':['name','ingredient'],
        'description':'Search public recipe articles using the publisher\u0027s WordPress API.',
        'website':'https://'+host}
       for key,(label,host) in WP_SITES.items()],
    {'id':'wikibooks','name':'Wikibooks Cookbook','search_by':['name','ingredient'],
     'description':'Community recipe library with thousands of recipes; imported pages may need manual review.',
     'website':'https://en.wikibooks.org/wiki/Cookbook:Recipes'},
    {'id':'themealdb','name':'TheMealDB','search_by':['name','ingredient'],
     'description':'Search meals with ingredient amounts, directions and photos. Free API test access is for personal/testing use.',
     'website':'https://www.themealdb.com/api.php'},
]

def _allowed_publisher_url(provider:str,url:str) -> bool:
    if provider not in WP_SITES:return False
    try:
        target=urlsplit(url)
        return (target.scheme=='https' and target.hostname in (
                    WP_SITES[provider][1],WP_SITES[provider][1].removeprefix('www.'))
                and not target.username and not target.password and target.port is None
                and target.path.startswith('/') and len(url)<=2000
                and not target.fragment and not target.query)
    except ValueError:return False


def _wp_image(row: dict) -> str:
    """Find a public featured thumbnail in an embedded WordPress post."""
    media=(row.get('_embedded') or {}).get('wp:featuredmedia') or []
    if isinstance(media,list) and media and isinstance(media[0],dict):
        photo=media[0]
        sizes=(photo.get('media_details') or {}).get('sizes') or {}
        value=(sizes.get('medium_large') or sizes.get('medium') or {}).get('source_url') or photo.get('source_url')
        if isinstance(value,str) and value.startswith('https://'):
            return value[:2000]
    seo=row.get('yoast_head_json') or {}
    images=seo.get('og_image') or []
    if isinstance(images,list) and images and isinstance(images[0],dict):
        value=images[0].get('url')
        if isinstance(value,str) and value.startswith('https://'):
            return value[:2000]
    return ''


async def _wp_search_thumbnails(host: str, rows: list) -> dict[int,str]:
    """One best-effort REST call for images; do not fetch each result page."""
    ids=[]
    for row in rows:
        if not isinstance(row,dict):continue
        value=row.get('id')
        if isinstance(value,int) and value>0:
            ids.append(value)
    if not ids:return {}
    try:
        posts=await _get_json('https://'+host+'/wp-json/wp/v2/posts',
                              {'include':','.join(map(str,ids[:40])),
                               'per_page':str(min(len(ids),40)),
                               '_embed':'wp:featuredmedia'})
    except (HTTPException,OSError,ValueError):
        return {}
    if not isinstance(posts,list):return {}
    thumbnails={}
    for post in posts:
        if isinstance(post,dict) and isinstance(post.get('id'),int):
            image=_wp_image(post)
            if image:thumbnails[post['id']]=image
    return thumbnails


async def _get_json(url: str, params: dict) -> dict | list:
    """Provider URLs are fixed constants; never follow redirects or proxy LAN hosts."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(14,connect=5),
                                     follow_redirects=False,trust_env=False) as client:
            async with client.stream('GET',url,params=params,headers={'User-Agent':USER_AGENT,
                                                                       'Accept':'application/json'}) as result:
                if result.status_code!=200:
                    raise HTTPException(502,'Recipe catalog returned HTTP '+str(result.status_code))
                chunks=bytearray()
                async for chunk in result.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks)>1_500_000:
                        raise HTTPException(502,'Recipe catalog response is too large')
        import json
        data=json.loads(chunks)
        if not isinstance(data,(dict,list)):raise ValueError('invalid catalog JSON')
        return data
    except (httpx.HTTPError,ValueError) as exc:
        raise HTTPException(502,'Recipe directory unavailable; try again later') from exc


def _meal_key():
    value=os.getenv('FORKNOOK_MEALDB_KEY','1').strip()
    if not re.fullmatch(r'[A-Za-z0-9]{1,45}',value):
        raise HTTPException(503,'TheMealDB API key is not configured correctly')
    return value


def _meal_to_result(meal):
    if not isinstance(meal,dict):return None
    meal_id=str(meal.get('idMeal') or '')
    if not meal_id.isdecimal():return None
    return {'provider':'themealdb','id':meal_id,'title':str(meal.get('strMeal') or 'Recipe')[:200],
            'image_url':str(meal.get('strMealThumb') or '')[:2000],
            'description':', '.join(str(meal.get(k) or '').strip() for k in ('strCategory','strArea') if meal.get(k))[:220],
            'source_url':'https://www.themealdb.com/meal/'+meal_id}


@router.get('/providers')
def providers(who:dict=Depends(base.principal)):
    return [{**item,'available':dataset.ready() if item['id']=='foodcom' else True,
             'count':dataset.stats().get('count',0) if item['id']=='foodcom' else None} for item in PROVIDERS]


@router.get('/search')
async def search(provider:str=Query(...,max_length=30),mode:str=Query('name',max_length=20),
                 q:str=Query(...,min_length=2,max_length=90),who:dict=Depends(base.principal)):
    term=q.strip()
    if len(term)<2:raise HTTPException(422,'Enter at least two characters')
    if mode not in ('name','ingredient'):raise HTTPException(422,'Search by recipe name or ingredient')
    if provider=='foodcom':
        return {'provider':provider,'mode':mode,'query':q,'items':dataset.search(term,mode),
                'notice':'Local Food.com collection. Results are separate from approved family recipes; every import is reviewed.'}
    if provider in WP_SITES:
        label,host=WP_SITES[provider]
        rows=await _get_json('https://'+host+'/wp-json/wp/v2/search',
                             {'search':term,'per_page':'40','type':'post'})
        if not isinstance(rows,list):raise HTTPException(502,'Unexpected recipe publisher response')
        thumbnails=await _wp_search_thumbnails(host,rows)
        items=[]
        for row in rows:
            if not isinstance(row,dict):continue
            url=str(row.get('url') or '')
            if not _allowed_publisher_url(provider,url):continue
            title=BeautifulSoup(str(row.get('title') or ''),'html.parser').get_text(' ',strip=True)
            if not title:continue
            image=_wp_image(row) or thumbnails.get(row.get('id'),'')
            items.append({'provider':provider,'id':url,'title':html_util.unescape(title)[:200],
               'image_url':image,'description':'Recipe article from '+label,'source_url':url})
        return {'provider':provider,'mode':mode,'query':q,'items':items[:40],
                'notice':'Searches '+label+'\u0027s public articles. Ingredient searches use the publisher\u0027s full-text index; preview retrieves the actual recipe and photo.'}
    if provider=='all':
        sources=['foodcom','spendwithpennies','skinnytaste','thechunkychef','wellplated',
                 'therecipecritic','saltandlavender','ambitiouskitchen','themealdb','wikibooks']
        async def fetch(source):
            try:return source,await search(source,mode,term,who)
            except (HTTPException,OSError,ValueError) as exc:
                error=exc.detail if isinstance(exc,HTTPException) else 'unavailable'
                return source,{'items':[],'error':str(error)[:150]}
        received=await asyncio.gather(*(fetch(source) for source in sources))
        pool=[];errors=[];seen=set()
        for source,data in received:
            if data.get('error'):
                errors.append(source+': '+data['error']);continue
            for recipe in data['items']:
                key=recipe.get('source_url') or (source+':'+str(recipe['id']))
                if key in seen:continue
                seen.add(key);pool.append(recipe)
        tokens=[w for w in re.split(r'\s+',term.lower()) if w]
        def rank(recipe):
            title=recipe['title'].casefold()
            return (int(title==term.casefold()),int(term.casefold() in title),
                    sum(w in title for w in tokens))
        pool.sort(key=rank,reverse=True)
        return {'provider':'all','mode':mode,'query':q,'items':pool[:120],
                'sources_searched':len(sources),'errors':errors,
                'notice':f'{len(pool)} results across {len(sources)} sources.'+
                    (f' {len(errors)} unavailable: '+', '.join(errors) if errors else '')}
    if provider=='themealdb':
        # V1 free tier permits a single ingredient. A supporter key may be
        # configured by the admin; never issue unauthenticated generic scrapes.
        if mode=='ingredient':
            term=term.replace(' ','_')
            if ',' in term:raise HTTPException(422,'Search one main ingredient at a time')
        endpoint='search.php' if mode=='name' else 'filter.php'
        data=await _get_json(f'{MEALDB}/{_meal_key()}/{endpoint}',
                             {'s' if mode=='name' else 'i':term})
        meals=data.get('meals') or []
        if not isinstance(meals,list):raise HTTPException(502,'Unexpected response from TheMealDB')
        items=[x for row in meals[:60] if (x:=_meal_to_result(row)) is not None]
        return {'provider':provider,'mode':mode,'query':q,'items':items,'notice':
          'This catalog uses TheMealDB. Its free key is intended for personal use and development; public/app-store deployments need a supporter key.'}
    if provider=='wikibooks':
        # MediaWiki prefix: search restricts to Cookbook articles, not arbitrary pages.
        params={'action':'query','list':'search','format':'json',
                'srsearch':('intitle:'+term if mode=='name' else term),
                # Cookbook: is a custom MediaWiki namespace (102), not mainspace.
                'srnamespace':'102','srlimit':'30','srprop':'snippet','utf8':'1'}
        data=await _get_json(WIKIBOOKS,params)
        rows=data.get('query',{}).get('search',[])
        if not isinstance(rows,list):raise HTTPException(502,'Unexpected Wikibooks search response')
        items=[]
        for row in rows:
            title=str(row.get('title') or '')
            if not title.startswith('Cookbook:') or len(title)>220:continue
            snippet=BeautifulSoup(str(row.get('snippet') or ''),'html.parser').get_text(' ',strip=True)
            items.append({'provider':'wikibooks','id':title,'title':title[9:].replace('_',' '),
                          'image_url':'','description':snippet[:250],
                          'source_url':'https://en.wikibooks.org/wiki/'+quote(title.replace(' ','_'),safe=':/()')})
        return {'provider':provider,'mode':mode,'query':q,'items':items,'notice':
                'Wikibooks recipes are community-written. Review ingredients and instructions before saving.'}
    raise HTTPException(422,'Unsupported recipe directory')


def _meal_recipe(meal):
    if not isinstance(meal,dict) or not str(meal.get('idMeal') or '').isdecimal():
        raise HTTPException(404,'Recipe not found in TheMealDB')
    ingredients=[]
    for i in range(1,21):
        name=str(meal.get('strIngredient'+str(i)) or '').strip()
        measure=str(meal.get('strMeasure'+str(i)) or '').strip()
        if name:
            raw=(measure+' '+name).strip()
            ingredients.append({'name':name[:500], 'raw':raw[:500]})
    directions=re.split(r'(?<=[.!?])\s+(?=[A-Z])|\r?\n+',str(meal.get('strInstructions') or '').strip())
    directions=[x.strip()[:3000] for x in directions if x.strip()][:100]
    item=base.RecipePayload(title=str(meal.get('strMeal') or 'Recipe')[:300],
      description=', '.join(str(meal.get(k) or '').strip() for k in ('strCategory','strArea') if meal.get(k)),
      ingredients=ingredients,instructions=directions,image_url=str(meal.get('strMealThumb') or '')[:2000],
      source_url='https://www.themealdb.com/meal/'+str(meal['idMeal']),
      tags=[x.strip() for x in str(meal.get('strTags') or '').split(',') if x.strip()][:50],
      requires_manual_review=not bool(ingredients and directions))
    return item.model_dump()


def _wikibooks_recipe(title,html):
    doc=BeautifulSoup(html,'html.parser')
    ingredients=[];steps=[];current=None
    # MediaWiki output contains headings followed by lists. Keep the rendered
    # list text rather than full page chrome/navigation or markup.
    for element in doc.find_all(['h2','h3','li','p'],limit=2000):
        if element.name in ('h2','h3'):
            label=element.get_text(' ',strip=True).casefold()
            if any(n in label for n in ('ingredient','equipment')):
                current='ingredients' if 'ingredient' in label else None
            elif any(n in label for n in ('preparation','procedure','direction','method','instruction','step','cooking')):
                current='steps'
            elif current and label:
                current=None
        elif element.name=='li' and current:
            text=element.get_text(' ',strip=True)
            if not text or len(text)>600:continue
            if current=='ingredients' and len(ingredients)<200:
                ingredients.append({'name':text[:500],'raw':text[:500]})
            elif current=='steps' and len(steps)<100:
                steps.append(text[:3000])
    if not ingredients or not steps:
        # Explicit review required; don't invent absent ingredient quantities.
        steps=steps or []
    image_url=''
    for image in doc.select('img.mw-file-element[src], a.image img[src]')[:15]:
        raw=str(image.get('src') or '')
        url='https:'+raw if raw.startswith('//upload.wikimedia.org/') else raw
        if url.startswith('https://upload.wikimedia.org/'):
            image_url=url[:2000]
            break
    item=base.RecipePayload(title=title[9:].replace('_',' ')[:300],ingredients=ingredients,
      instructions=steps,image_url=image_url,
      source_url='https://en.wikibooks.org/wiki/'+quote(title.replace(' ','_'),safe=':/()'),
      description='Community recipe from Wikibooks Cookbook',
      requires_manual_review=not bool(ingredients and steps))
    return item.model_dump()


async def _prepare_preview_photo(recipe: dict) -> dict:
    """Provide a session-protected photo for Android and web recipe previews.

    Source photographs may redirect or refuse hotlinking from a phone.
    Reuse the same public-IP-pinned, size-limited image capture already used
    when saving a recipe. This caches an image, not a recipe or approval.
    If a publisher blocks capture, retain its original URL for client fallback.
    """
    url=str(recipe.get('image_url') or '')
    if not url.startswith(('https://','http://')):
        return recipe
    local=await run_in_threadpool(forknook_images.capture_best_effort,url)
    if local!=url and forknook_images.LOCAL_IMAGE.fullmatch(local):
        return {**recipe,'image_url':local}
    return recipe


def _incomplete_publisher_preview(provider: str, url: str, html: str = '', fallback_title: str = '',
                                  photo: str = '') -> dict:
    """Never claim a blocked/incomplete publisher article contains a full recipe."""
    soup=BeautifulSoup(html,'html.parser') if html else None
    title=(soup.find('h1') if soup else None)
    label=(title.get_text(' ',strip=True) if title else '') or fallback_title
    if not label:
        slug=unquote(urlsplit(url).path.rstrip('/').split('/')[-1])
        label=slug.replace('-',' ').replace('_',' ').strip().title()
    if not label:label='Recipe to review'
    desc=''
    if soup:
        description=soup.select_one('meta[property="og:description"],meta[name="description"]')
        desc=str(description.get('content') or '') if description else ''
        if not photo:
            image=soup.select_one('meta[property="og:image"],meta[name="twitter:image"]')
            photo=str(image.get('content') or '') if image else ''
    note='This publisher did not provide complete recipe details automatically. Open the original recipe to check the ingredients and directions before you save.'
    result=base.RecipePayload(
        title=label[:300], description=desc[:6000], source_url=url,
        image_url=photo[:2000] if photo.startswith('https://') else '',
        ingredients=[],instructions=[],requires_manual_review=True
    ).model_dump()
    result['preview_notice']=note
    return result


async def _wp_rest_preview(provider: str, url: str) -> dict | None:
    """Try a publisher's public post JSON if its HTML recipe page blocks parsing."""
    import recipes
    host=WP_SITES[provider][1]
    slug=unquote(urlsplit(url).path.rstrip('/').split('/')[-1])
    if not slug or '/' in slug or len(slug)>160:return None
    try:
        posts=await _get_json('https://'+host+'/wp-json/wp/v2/posts',
                              {'slug':slug,'per_page':'1','_embed':'wp:featuredmedia'})
    except (HTTPException,OSError,ValueError):
        return None
    if not isinstance(posts,list):return None
    for post in posts[:2]:
        if not isinstance(post,dict):continue
        link=str(post.get('link') or '')
        if not _allowed_publisher_url(provider,link):continue
        raw_content=post.get('content') or {}
        html=str(raw_content.get('rendered') or '') if isinstance(raw_content,dict) else ''
        if not html:continue
        parsed=recipes.parse_document(html,url)
        draft=base.from_parsed(parsed)
        draft['source_url']=url
        draft['image_url']=draft.get('image_url') or _wp_image(post)
        if draft.get('ingredients') and draft.get('instructions'):
            return draft
        title=post.get('title') or {}
        readable=BeautifulSoup(str(title.get('rendered') or ''),'html.parser').get_text(' ',strip=True) if isinstance(title,dict) else ''
        return _incomplete_publisher_preview(provider,url,html,readable,_wp_image(post))
    return None


@router.get('/preview')
async def preview(provider:str=Query(...,max_length=30),id:str=Query(...,min_length=1,max_length=220),
                  who:dict=Depends(base.principal)):
    if provider=='foodcom':
        return await _prepare_preview_photo(dataset.preview(id))
    if provider in WP_SITES:
        if not _allowed_publisher_url(provider,id):
            raise HTTPException(422,'Invalid recipe publisher URL')
        import recipes
        source_html=''
        try:
            source_html,source_url=await recipes.page_html(id)
            parsed=recipes.parse_document(source_html,source_url)
            draft=base.from_parsed(parsed)
            draft['source_url']=id
            if draft.get('ingredients') and draft.get('instructions'):
                return await _prepare_preview_photo(draft)
            if draft.get('ingredients') or draft.get('instructions'):
                draft['requires_manual_review']=True
                draft['preview_notice']='This publisher did not provide complete recipe details automatically. Check the original ingredients and directions before you save.'
                return await _prepare_preview_photo(draft)
        except (ValueError,TypeError):
            pass  # Publishers can deny automated HTML access or omit recipe cards.
        # A publisher post API can still contain the recipe when the HTML does not.
        rest=await _wp_rest_preview(provider,id)
        if rest and rest.get('ingredients') and rest.get('instructions'):
            return await _prepare_preview_photo(rest)
        incomplete=rest or _incomplete_publisher_preview(provider,id,source_html)
        return await _prepare_preview_photo(incomplete)
    if provider=='themealdb':
        if not re.fullmatch(r'\d{1,12}',id):raise HTTPException(422,'Invalid meal ID')
        info=await _get_json(f'{MEALDB}/{_meal_key()}/lookup.php',{'i':id})
        meals=info.get('meals') or []
        if not isinstance(meals,list) or not meals:raise HTTPException(404,'Recipe no longer available')
        return await _prepare_preview_photo(_meal_recipe(meals[0]))
    if provider=='wikibooks':
        if not id.startswith('Cookbook:') or re.search(r'[\[\]{}|#?\x00-\x1f]',id):
            raise HTTPException(422,'Invalid Cookbook title')
        info=await _get_json(WIKIBOOKS,{'action':'parse','page':id,'prop':'text',
                                       'format':'json','disableeditsection':'1'})
        payload=info.get('parse',{}).get('text',{})
        if not isinstance(payload,dict) or not isinstance(payload.get('*'),str):
            raise HTTPException(404,'Cookbook page is unavailable')
        return await _prepare_preview_photo(_wikibooks_recipe(id,payload['*']))
    raise HTTPException(422,'Unsupported recipe directory')
