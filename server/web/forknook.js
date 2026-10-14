'use strict';
const $=id=>document.getElementById(id);
const content=$('content');
const model={token:localStorage.getItem('forknook-token')||sessionStorage.getItem('forknook-token')||'',user:null,page:'home',recipes:[],categories:[],folders:[],recipeLibraryFilter:{category:'',folder:'',status:''},lists:[],selected:new Set(),editing:null};
const node=(tag,props={},...children)=>{let e=document.createElement(tag);for(let [k,v] of Object.entries(props)){if(k==='class')e.className=v;else if(k==='text')e.textContent=v;else if(k==='value')e.value=v;else if(k==='type')e.type=v;else if(k==='onClick')e.addEventListener('click',v);else if(k==='onChange')e.addEventListener('change',v);else if(k==='hidden')e.hidden=!!v;else e.setAttribute(k,v)}for(let child of children.flat()){if(child!==null&&child!==undefined)e.append(child instanceof Node?child:document.createTextNode(String(child)))}return e};
function approveUnsecuredConnection(){
 if(location.protocol!=='http:')return true;
 const key='forknook-insecure-consent-origin';
 if(localStorage.getItem(key)===location.origin)return true;
 const accepted=confirm('Unencrypted HTTP connection\\n\\nRecipeCove is using HTTP instead of HTTPS. Anyone who can intercept traffic between this device and the server could read your login credentials, session token and recipes, or modify data in transit. Avoid public and untrusted networks. HTTPS is strongly recommended.\\n\\nSelect OK to accept these risks for this server, or Cancel to stop.');
 if(accepted)localStorage.setItem(key,location.origin);
 return accepted;
}
function announce(message,error=false){const e=$('toast');e.textContent=message;e.className=error?'error':'';e.hidden=false;clearTimeout(announce.timeout);announce.timeout=setTimeout(()=>e.hidden=true,6000)}
async function api(path,{method='GET',data,form}={}){const headers={};if(model.token)headers.Authorization='Bearer '+model.token;let body;if(form){body=form}else if(data!==undefined){headers['Content-Type']='application/json';body=JSON.stringify(data)}let response;try{response=await fetch('/api/v2'+path,{method,headers,body,cache:'no-store'})}catch(e){throw Error('Cannot reach RecipeCove. Check your connection.')}const payload=await response.json().catch(()=>({}));if(!response.ok){if(response.status===401&&model.token){model.token='';localStorage.removeItem('forknook-token');sessionStorage.removeItem('forknook-token');$('shell').hidden=true;$('auth').hidden=false}throw Error(typeof payload.detail==='string'?payload.detail:('HTTP '+response.status))}return payload}
async function start(){
 try{
  if(!approveUnsecuredConnection()){announce('Connection blocked until you accept the HTTP security risks or switch to HTTPS.',true);return}
  if(model.token){
   // Only a confirmed authentication failure invalidates the saved session.
   // A failed page render, network outage or updater restart must not log out.
   try{
    model.user=await api('/auth/me');
    localStorage.setItem('forknook-token',model.token);
    sessionStorage.removeItem('forknook-token');
   }catch(error){
    if(model.token){announce('Unable to verify your session. Please retry when RecipeCove is available.',true);return}
   }
   if(model.user){await activate();return}
  }
  const setup=await api('/setup/status');
  $('auth').hidden=false;$('shell').hidden=true;
  const first=!setup.configured;
  $('code-field').hidden=!first;$('display-field').hidden=!first;
  $('password').autocomplete=first?'new-password':'current-password';
  $('auth-title').textContent=first?'Set up your kitchen':'Welcome back';
  $('auth-hint').textContent=first?'Use the one-time setup code shown in your RecipeCove container logs. No KitchenOwl account needed.':'Sign in to your family kitchen';
  $('auth-submit').textContent=first?'Create administrator':'Sign in';model.setup=first
 }catch(error){announce(error.message,true)}
}
$('auth-form').addEventListener('submit',async e=>{e.preventDefault();if(!approveUnsecuredConnection())return;const username=$('username').value.trim(),password=$('password').value;try{const request={username,password};if(model.setup){request.setup_code=$('setup-code').value;request.display_name=$('display-name').value||'Administrator'}const answer=await api(model.setup?'/setup/complete':'/auth/login',{method:'POST',data:request});model.token=answer.token;localStorage.setItem('forknook-token',model.token);sessionStorage.removeItem('forknook-token');$('password').value='';$('setup-code').value='';model.user=await api('/auth/me');await activate()}catch(err){announce(err.message,true)}});
async function activate(){$('auth').hidden=true;$('shell').hidden=false;$('current-user').textContent=model.user.display_name+' · '+model.user.role;$('admin-nav').hidden=model.user.role!=='admin';await openLocation();if(new URLSearchParams(location.search).has('shared_file')){await navigate('sources');announce('Photo/video received. Tap the file field to attach the shared file, then choose Import file.')}const share=new URLSearchParams(location.search).get('share');if(share){history.replaceState(null,'','/app/sources');await navigate('sources',{push:false});$('import-url').value=share;announce('Shared link received. Select Import from URL to add it.')}}
async function logout(){for(const url of recipePhotoCache.values())URL.revokeObjectURL(url);recipePhotoCache.clear();try{await api('/auth/logout',{method:'POST'})}catch(e){}model.token='';model.user=null;localStorage.removeItem('forknook-token');sessionStorage.removeItem('forknook-token');start()}
$('logout').addEventListener('click',logout);$('mobile-logout').addEventListener('click',logout);
// Every section has a real URL. Browser back/forward, refresh and copied links work.
const pagePaths={home:'/app/',recipes:'/app/recipes',plan:'/app/plan',shopping:'/app/shopping',sources:'/app/sources',discover:'/app/discover',categories:'/app/categories',admin:'/app/admin','admin-users':'/app/admin/users','admin-backups':'/app/admin/backups','admin-updates':'/app/admin/updates','admin-ai':'/app/admin/ai','admin-imports':'/app/admin/imports','admin-review':'/app/admin/review','admin-api':'/app/admin/api',review:'/app/review',about:'/app/about'};
const labels={home:'Your kitchen',recipes:'Recipe collection',plan:'Plan to Make',shopping:'Shopping lists',sources:'Import a recipe',discover:'Discover recipes',categories:'Categories',admin:'Administration','admin-users':'Family accounts','admin-backups':'Backups & migration','admin-updates':'Application updates','admin-ai':'AI settings','admin-imports':'Import & feeds','admin-review':'Recipe approvals','admin-api':'Developer API',review:'My submissions',about:'About RecipeCove'};
function routeLocation(){const p=location.pathname.replace(/\/$/,'')||'/app';if(p==='/app')return{page:'home'};for(const [name,path] of Object.entries(pagePaths)){if(path.replace(/\/$/,'')===p)return{page:name}}let m=p.match(/^\/app\/recipes\/(\d+)$/);if(m)return{page:'recipes',detail:Number(m[1])};m=p.match(/^\/app\/shopping\/(\d+)$/);if(m)return{page:'shopping',detail:Number(m[1])};m=p.match(/^\/app\/sources\/(\d+)$/);if(m)return{page:'sources',detail:Number(m[1])};return{page:'home'}}
function urlFor(page,detail){const base=pagePaths[page]||pagePaths.home;return detail?base+'/'+detail:base}
async function openLocation(){const route=routeLocation();await navigate(route.page,{push:false,detail:route.detail})}
window.addEventListener('popstate',async()=>{if($('editor').open)$('editor').close();if(model.user)await openLocation()});
document.querySelectorAll('[data-page]').forEach(btn=>btn.addEventListener('click',()=>navigate(btn.dataset.page)));
$('brand-home').addEventListener('click',event=>{event.preventDefault();navigate('home')});
async function navigate(page,{push=true,detail=null}={}){
 if(!pagePaths[page])page='home';
 if(page.startsWith('admin')&&model.user?.role!=='admin')page='home';
 if($('editor').open)$('editor').close();
 model.page=page;
 if(push && location.pathname!==urlFor(page,detail))history.pushState({},'',urlFor(page,detail));
 document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===(page.startsWith('admin')?'admin':page)));
 $('page-title').textContent=labels[page]||'RecipeCove';
 content.replaceChildren(node('div',{class:'empty',text:'Loading…'}));
 try{
  const routes={home:renderHome,recipes:renderRecipes,plan:renderPlan,shopping:renderShopping,sources:renderSources,discover:renderDiscover,categories:renderCategories,admin:renderAdmin,'admin-users':renderAdmin,'admin-backups':renderAdmin,'admin-updates':renderAdmin,'admin-ai':renderAdmin,'admin-imports':renderAdmin,'admin-review':renderAdmin,'admin-api':renderAdmin,review:renderReview,about:renderAbout};
  await routes[page]();
  if(detail && page==='recipes')await viewRecipe(detail,{push:false});
  if(detail && page==='shopping')await openShopping(detail,{push:false});
  if(detail && page==='sources'){const all=await api('/feeds');const feed=all.find(x=>x.id===detail);if(feed)await browseFeed(feed,{push:false});else throw Error('Feed not found')}
 }catch(e){content.replaceChildren(node('div',{class:'warning',text:e.message}));announce(e.message,true)}
}
async function renderAbout(){
 const about=card(node('div',{class:'about-brand'},node('img',{src:'/app/recipecove-logo.svg',alt:'RecipeCove by Gigabyte Grove'})),
  node('h2',{text:'About RecipeCove'}),
  node('p',{text:'RecipeCove is a family recipe management system developed and released by Gigabyte Grove. It brings recipes, planning what to make, and shopping lists together in one self-hosted kitchen.'}),
  node('h3',{text:'Built for your kitchen'}),
  node('p',{text:'Save and organize recipes, import from compatible sources, discover something new, plan recipes for specific days, and generate shopping lists.'}),
  node('h3',{text:'Privacy and control'}),
  node('p',{text:'Your RecipeCove server runs in your own environment. Family accounts and recipes are managed by your instance administrator. Optional external import and discovery features may contact the source you choose.'}),
  node('h3',{text:'Software information'}),
  node('p',{text:'RecipeCove is published by Gigabyte Grove. Release and update information is available under Administration → Updates for administrators.'}),
  node('a',{href:'https://github.com/gigabytegrove/forknook',target:'_blank',rel:'noopener noreferrer',text:'RecipeCove on GitHub ↗'}));
 content.replaceChildren(about);
}
async function renderHome(){const [recipes,plan,shopping]=await Promise.all([api('/recipes'),api('/meal-plan'),api('/shopping-lists')]);const soon=plan.filter(x=>x.day>=new Date().toISOString().slice(0,10)).slice(0,5);const headlines=node('div',{class:'page-heading'},node('h2',{text:'Welcome to your family kitchen'}),node('p',{text:'Find something delicious, plan meals for the week and build a grocery list from the recipes you love.'}));const metrics=node('div',{class:'grid'},card(node('div',{class:'stat',text:String(recipes.length)}),node('h3',{text:'Recipes in your collection'}),button('Browse recipes',()=>navigate('recipes'),'primary')),card(node('div',{class:'stat',text:String(plan.length)}),node('h3',{text:'Recipes planned'}),button('Open Plan to Make',()=>navigate('plan'),'primary')),card(node('div',{class:'stat',text:String(shopping.length)}),node('h3',{text:'Shopping lists'}),button('View shopping lists',()=>navigate('shopping'),'primary')));const upcoming=card(node('h2',{text:'Upcoming recipes'}));if(soon.length){for(const e of soon)upcoming.append(node('div',{class:'list-row'},node('strong',{text:e.day+' · '+e.meal}),button(e.recipe_title,()=>viewRecipe(e.recipe_id))))}else upcoming.append(node('p',{class:'muted',text:'Nothing planned yet. Choose a recipe and a date to get started.'}));const quick=card(node('h2',{text:'Get started'}),node('div',{class:'toolbar'},button('Import a recipe',()=>navigate('sources'),'accent'),button('Discover recipes',()=>navigate('discover'),'primary'),button('Create a recipe',()=>openEditor(null),'primary'),button('My submissions',()=>navigate('review')),button('Manage categories',()=>navigate('categories'))));const [homeFolders,homeCats]=await Promise.all([api('/folders'),api('/categories')]);
 const shortcuts=card(node('h2',{text:'Browse your recipes'}),
  node('p',{class:'muted',text:'Jump straight to a folder or category.'}),
  node('div',{class:'folder-shortcuts'},
    ...homeFolders.map(f=>button('📁 '+f.name+' ('+f.recipe_count+')',()=>{
      model.recipeLibraryFilter={folder:String(f.id),category:'',status:''};
      navigate('recipes');
    })),
    ...homeCats.map(c=>button(c.name,()=>{
      model.recipeLibraryFilter={folder:'',category:String(c.id),status:''};
      navigate('recipes');
    }))));
 content.replaceChildren(headlines,metrics,shortcuts,upcoming,quick)}
const field=(name,placeholder,value='',type='text')=>node('label',{},name,node('input',{placeholder,value,type}));
const button=(text,onclick,kind='secondary')=>node('button',{class:kind,text,onClick:async()=>{try{await onclick()}catch(e){announce(e.message,true)}}});
function empty(message){return node('div',{class:'empty',text:message})}
function card(...children){return node('article',{class:'card'},...children)}
async function reloadLibrary(){[model.recipes,model.categories,model.folders]=await Promise.all([api('/recipes'),api('/categories'),api('/folders')])}
const recipePhotoCache=new Map();
async function setPicture(container,imageUrl,description){
 if(!imageUrl)return;
 try{
  let src=imageUrl;
  if(imageUrl.startsWith('/api/v2/images/')){
   if(!recipePhotoCache.has(imageUrl)){
    const response=await fetch(imageUrl,{headers:{Authorization:'Bearer '+model.token},cache:'no-store'});
    if(!response.ok)throw Error('Recipe photo unavailable');
    recipePhotoCache.set(imageUrl,URL.createObjectURL(await response.blob()));
   }
   src=recipePhotoCache.get(imageUrl);
  }else if(!/^https?:\/\//i.test(imageUrl))return;
  const image=node('img',{alt:description||'Recipe photo',loading:'lazy'});
  image.src=src;
  image.addEventListener('error',()=>image.remove(),{once:true});
  container.replaceChildren(image);
 }catch(e){/* A missing image never hides the recipe. */}
}
function recipeStatus(recipe){return recipe.state==='made'?'Made before':recipe.state==='want'?'Want to make':'Not marked'}

async function renderRecipes(){
 await reloadLibrary();model.selected=new Set();
 const saved=model.recipeLibraryFilter;
 const search=node('input',{placeholder:'Search recipes…',type:'search'});
 const status=node('select');
 [['','All recipes'],['want','Want to make'],['made','Made before']].forEach(([value,title])=>status.add(node('option',{value,text:title})));
 status.value=saved.status||'';
 const category=node('select');
 category.add(node('option',{value:'',text:'All categories'}));
 model.categories.forEach(c=>category.add(node('option',{value:String(c.id),text:c.name})));
 category.value=saved.category||'';
 const folder=node('select');
 folder.add(node('option',{value:'',text:'All folders'}));
 folder.add(node('option',{value:'none',text:'Not in a folder'}));
 model.folders.forEach(f=>folder.add(node('option',{value:String(f.id),text:f.name+' ('+f.recipe_count+')'})));
 folder.value=saved.folder||'';
 const grid=node('div',{class:'grid'}),count=node('p',{class:'muted small',role:'status'});
 const selectedInfo=node('span',{class:'muted small',text:'Choose recipes for a shopping list'});
 function draw(){
  const q=search.value.toLowerCase(),st=status.value,cid=category.value,fid=folder.value;
  Object.assign(saved,{category:cid,folder:fid,status:st});
  grid.replaceChildren();
  const found=model.recipes.filter(r=>(!st||r.state===st)&&(!cid||String(r.category_id)===cid)&&
   (!fid||(fid==='none'?!((r.folder_ids||[]).length):(r.folder_ids||[]).includes(Number(fid))))&&
   (!q||(r.title+' '+(r.description||'')+' '+(r.tags||[]).join(' ')).toLowerCase().includes(q)));
  count.textContent=found.length+' of '+model.recipes.length+' recipes';
  if(!found.length)grid.append(empty('No recipes match. Try a different folder, category or search.'));
  for(const r of found){
   const cb=node('input',{type:'checkbox'});
   cb.checked=model.selected.has(r.id);
   cb.addEventListener('change',()=>{
     if(cb.checked)model.selected.add(r.id);else model.selected.delete(r.id);
     selectedInfo.textContent=model.selected.size+' recipe(s) selected';
   });
   const names=(r.folder_ids||[]).map(id=>model.folders.find(f=>f.id===id)?.name).filter(Boolean);
   const tile=card(
    node('div',{class:'recipe-tile-head'},node('span',{class:'pill',text:r.category||'Uncategorized'}),node('label',{class:'recipe-select'},cb,'Select')),
    ...(names.length?[node('div',{class:'folder-tags'},...names.map(n=>node('span',{class:'folder-chip',text:'📁 '+n})))]:[]),
    node('div',{class:'recipe-tile-copy'},node('h3',{text:r.title}),
      node('p',{class:'recipe-excerpt',text:(r.description||'Open to see ingredients and directions.').slice(0,145)}),
      node('span',{class:'small muted',text:recipeStatus(r)})),
    node('footer',{},button('View recipe',()=>viewRecipe(r.id),'primary'),button('Want to make',()=>mark(r.id,'want')),button('Made before',()=>mark(r.id,'made'))));
   tile.classList.add('recipe-tile');
   const picture=node('div',{class:'recipe-tile-photo'},node('span',{class:'recipe-placeholder',text:'🍽️'}));
   tile.insertBefore(picture,tile.firstChild);
   if(r.image_url)setPicture(picture,r.image_url,r.title);
   grid.append(tile);
  }
 }
 async function mark(id,state){await api('/recipes/'+id+'/state',{method:'PUT',data:{state}});announce('Recipe status updated');await renderRecipes()}
 [search,status,category,folder].forEach(element=>element.addEventListener(element===search?'input':'change',draw));
 const toolbar=node('div',{class:'toolbar'},
   node('label',{},'Find a recipe',search),node('label',{},'Folder',folder),
   node('label',{},'Category',category),node('label',{},'Status',status));
 const actions=node('div',{class:'toolbar'},
   button('+ New recipe',()=>openEditor(null),'primary'),button('Import recipe',()=>navigate('sources')),
   button('Manage folders & categories',()=>navigate('categories')),
   button('Build shopping list from selected',async()=>{
     const ids=[...model.selected];if(!ids.length){announce('Select recipes first',true);return}
     const name=prompt('Name this shopping list','Recipes shopping list');if(!name)return;
     await api('/shopping-lists/build',{method:'POST',data:{recipe_ids:ids,name}});
     announce('Shopping list created');await navigate('shopping');
   },'accent'),selectedInfo);
 content.replaceChildren(toolbar,actions,count,grid);draw();
}

async function viewRecipe(id,{push=true}={}){
 if(push&&location.pathname!==urlFor('recipes',id))history.pushState({},'',urlFor('recipes',id));
 model.page='recipes';
 $('page-title').textContent='Recipe';
 content.replaceChildren(empty('Loading recipe…'));
 const item=await api('/recipes/'+id);
 const [catList,folderList]=await Promise.all([api('/categories'),api('/folders')]);
 const category=catList.find(c=>c.id===item.category_id);
 const photo=node('div',{class:'recipe-hero-image'},node('span',{text:'🍲'}));
 if(item.image_url)setPicture(photo,item.image_url,item.title);
 const ingredients=node('ul',{class:'ingredient-list'});
 for(const raw of item.ingredients||[]){
   const name=typeof raw==='string'?raw:(raw.raw||[raw.quantity,raw.unit,raw.name].filter(Boolean).join(' ')||raw.name||'');
   ingredients.append(node('li',{},node('label',{class:'ingredient-check'},node('input',{type:'checkbox'}),node('span',{text:name}))));
 }
 if(!ingredients.children.length)ingredients.append(node('li',{class:'muted',text:'No ingredients have been added yet.'}));
 const steps=node('ol',{class:'instruction-list'});
 for(const step of item.instructions||[])steps.append(node('li',{text:typeof step==='string'?step:step.text||''}));
 if(!steps.children.length)steps.append(node('li',{text:'No directions have been added yet.'}));
 const facts=node('div',{class:'recipe-facts'});
 for(const [label,value] of [['Servings',item.servings],['Prep time',item.prep_time!=null?item.prep_time+' min':null],['Cook time',item.cook_time!=null?item.cook_time+' min':null],['Total time',item.total_time!=null?item.total_time+' min':null]]){
  if(value)facts.append(node('div',{class:'recipe-fact'},node('span',{text:label}),node('strong',{text:String(value)})));
 }
 const actions=node('div',{class:'recipe-view-actions'},button('← All recipes',()=>navigate('recipes')),
  ...(model.user.role==='admin'||model.user.role==='approver'?[button('Edit recipe',()=>openEditor(id,null,{push:false}),'primary'),
  ...(item.source_url&&/^https?:/i.test(item.source_url)?[button('Recover from original source',async()=>{
    if(!confirm('Read the original recipe website and prepare suggested corrections? Your saved recipe will not change until you review and save it.'))return;
    const preview=await api('/imports/preview-url',{method:'POST',data:{url:item.source_url}});
    const recoveredIngredients=Array.isArray(preview.ingredients)?preview.ingredients:[];
    const recoveredSteps=Array.isArray(preview.instructions)?preview.instructions:[];
    const existingIngredients=Array.isArray(item.ingredients)?item.ingredients:[];
    const existingSteps=Array.isArray(item.instructions)?item.instructions:[];
    const merged={...item,
      ingredients:recoveredIngredients.length>existingIngredients.length?recoveredIngredients:existingIngredients,
      instructions:existingSteps.length>recoveredSteps.length?existingSteps:recoveredSteps,
      image_url:(item.image_url&&item.image_url.startsWith('/api/v2/images/'))?item.image_url:(preview.image_url||item.image_url),
      description:item.description||preview.description||'',
      notes:item.notes||preview.notes||''};
    await openEditor(id,merged,{push:false});
    announce('Source suggestions loaded. Review ingredients, directions and photo before saving.');
  })]:[])]:[]),
  button('Want to make',async()=>{await api('/recipes/'+id+'/state',{method:'PUT',data:{state:'want'}});announce('Added to Want to Make')}),
  button('Made before',async()=>{await api('/recipes/'+id+'/state',{method:'PUT',data:{state:'made'}});announce('Marked as Made Before')}),
  button('Add ingredients to shopping list',async()=>{const name=prompt('Name your shopping list',item.title+' ingredients');if(!name)return;await api('/shopping-lists/build',{method:'POST',data:{recipe_ids:[id],name}});navigate('shopping')},'accent'));
 const header=node('header',{class:'recipe-hero'},photo,node('div',{class:'recipe-hero-content'},node('span',{class:'pill',text:category?.name||'Family recipe'}),node('h2',{text:item.title}),node('p',{class:'muted',text:item.description||'Made for your family kitchen.'}),facts));
 const panel=node('div',{class:'recipe-content-grid'},card(node('h2',{text:'Ingredients'}),node('p',{class:'muted small',text:'Tick off ingredients as you prepare them.'}),ingredients),card(node('h2',{text:'Directions'}),steps));
 const details=[];
 if(item.notes)details.push(card(node('h3',{text:'Notes'}),node('p',{class:'recipe-notes',text:item.notes})));
 if((item.folder_ids||[]).length)details.push(card(node('h3',{text:'Folders'}),
   node('div',{class:'folder-tags'},...(item.folder_ids||[]).map(id=>folderList.find(f=>f.id===id)?.name).filter(Boolean).map(name=>node('span',{class:'folder-chip',text:'📁 '+name})))));
 if(item.tags?.length)details.push(card(node('h3',{text:'Tags'}),node('div',{class:'tag-list'},...item.tags.map(tag=>node('span',{class:'pill',text:tag})))));
 if(item.source_url&&/^https?:\/\//.test(item.source_url))details.push(card(node('h3',{text:'Original source'}),node('a',{href:item.source_url,target:'_blank',rel:'noopener noreferrer',text:'Open original recipe ↗'})));
 if(item.evidence)details.push(card(node('details',{},node('summary',{text:'Original imported text and transcription'}),node('pre',{class:'evidence-text',text:item.evidence}))));
 content.replaceChildren(actions,header,panel,...details);
}
async function openEditor(id,imported=null,{push=true}={}){await reloadLibrary();model.editing=id;let item=imported||(id?await api('/recipes/'+id):{title:'',description:'',ingredients:[],instructions:[],tags:[]});const form=$('recipe-form');const fields=['title','description','servings','prep_time','cook_time','source_url','image_url','notes'];for(const name of fields)form.elements[name].value=item[name]??'';form.elements.ingredients.value=(item.ingredients||[]).map(i=>typeof i==='string'?i:(i.raw||i.name||'')).join('\n');form.elements.instructions.value=(item.instructions||[]).join('\n');form.elements.tags.value=(item.tags||[]).join(', ');const select=form.elements.category_id;select.replaceChildren(node('option',{value:'',text:'Uncategorized'}));model.categories.forEach(c=>select.add(node('option',{value:String(c.id),text:c.name})));select.value=item.category_id==null?'':String(item.category_id);
 const choices=$('recipe-folder-choices');choices.replaceChildren();
 if(!model.folders.length)choices.append(node('p',{class:'muted small',text:'No folders yet. Create one under Manage folders & categories.'}));
 for(const folder of model.folders){
  const checkbox=node('input',{type:'checkbox',name:'folder_ids',value:String(folder.id)});
  checkbox.checked=(item.folder_ids||[]).includes(folder.id);
  choices.append(node('label',{class:'check-row folder-select'},checkbox,folder.name));
 }
 $('editor-title').textContent=id?'Edit recipe':'Create recipe';$('recipe-photo-status').textContent=item.image_url?'Image will be included with the recipe.':'No recipe image selected';$('publish-recipe').checked=!!item.published;const photoPreview=$('recipe-photo-preview');photoPreview.replaceChildren();if(item.image_url)setPicture(photoPreview,item.image_url,item.title);$('recipe-photo-file').value='';$('editor-evidence').textContent=item.evidence||'';$('manual-warning').hidden=!item.requires_manual_review;$('extraction-details').hidden=!item.evidence;$('editor').showModal()}
$('close-editor').addEventListener('click',()=>$('editor').close());
$('editor').addEventListener('close',()=>{if(!model.editing&&location.pathname.match(/^\/app\/recipes\/\d+$/))history.replaceState({},'',urlFor('recipes'))});
$('recipe-form').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget;const value=n=>form.elements[n].value.trim();const obj={title:value('title'),description:value('description'),servings:value('servings')||null,prep_time:value('prep_time')?Number(value('prep_time')):null,cook_time:value('cook_time')?Number(value('cook_time')):null,source_url:value('source_url'),image_url:value('image_url'),published:$('publish-recipe').checked,notes:value('notes'),category_id:value('category_id')?Number(value('category_id')):null,folder_ids:[...form.querySelectorAll('input[name="folder_ids"]:checked')].map(x=>Number(x.value)),ingredients:value('ingredients').split('\n').map(s=>s.trim()).filter(Boolean).map(s=>({name:s,raw:s})),instructions:value('instructions').split('\n').map(s=>s.trim()).filter(Boolean),tags:value('tags').split(',').map(s=>s.trim()).filter(Boolean),evidence:$('editor-evidence').textContent,requires_manual_review:false};try{
 const imageFile=$('recipe-photo-file').files[0];
 if(imageFile){const data=new FormData();data.append('file',imageFile);const uploaded=await api('/images/upload',{method:'POST',form:data});obj.image_url=uploaded.image_url}
 const result=await api(model.editing?'/recipes/'+model.editing:'/recipes',{method:model.editing?'PUT':'POST',data:obj});
 $('editor').close();announce(result.pending?'Submitted for approval. The recipe is not in the shared collection yet.':'Recipe saved with its photo');
 if(result.pending){await navigate('review')}else await navigate('recipes',{detail:result.id||model.editing})
 }catch(err){announce(err.message,true)}});

async function renderCategories(){
 [model.categories,model.folders]=await Promise.all([api('/categories'),api('/folders')]);
 const manage=['admin','approver'].includes(model.user?.role);
 const folderInput=node('input',{placeholder:'e.g. Jen’s Favorites or Holiday Baking'});
 const folderGrid=node('div',{class:'grid'});
 for(const folder of model.folders){
  const entry=card(node('h3',{text:'📁 '+folder.name}),
    node('p',{class:'muted small',text:folder.recipe_count+' recipes'}),
    button('Show recipes',()=>{
      model.recipeLibraryFilter={folder:String(folder.id),category:'',status:''};
      navigate('recipes');
    },'primary'));
  if(manage)entry.append(
    button('Rename',async()=>{
      const name=prompt('Rename folder',folder.name);
      if(name===null)return;
      await api('/folders/'+folder.id,{method:'PUT',data:{name:name.trim()}});
      announce('Folder renamed');renderCategories();
    }),
    button('Delete folder',async()=>{
      if(!confirm('Delete "'+folder.name+'"? The recipes themselves will NOT be deleted.'))return;
      await api('/folders/'+folder.id,{method:'DELETE'});
      announce('Folder deleted. Your recipes are safe.');renderCategories();
    })
  );
  folderGrid.append(entry);
 }
 const folderTools=card(node('h2',{text:'Folders'}),
   node('p',{class:'muted',text:'Put a recipe in several folders without making copies. Everyone in your family can browse folders.'}));
 if(manage)folderTools.append(node('div',{class:'toolbar'},
    node('label',{class:'grow'},'Folder name',folderInput),
    button('Create folder',async()=>{
      const name=folderInput.value.trim();
      if(!name){announce('Enter a folder name',true);return}
      await api('/folders',{method:'POST',data:{name}});
      announce('Folder created');renderCategories();
    },'primary')));
 const catInput=node('input',{placeholder:'e.g. Dinner, Dessert, Slow cooker'});
 const color=node('input',{type:'color',value:'#537d56'});
 const categoryGrid=node('div',{class:'grid'});
 for(const c of model.categories){
  const row=card(node('h3',{text:c.name}),
    button('Show recipes',()=>{
      model.recipeLibraryFilter={category:String(c.id),folder:'',status:''};
      navigate('recipes');
    },'primary'));
  if(manage)row.append(button('Delete',async()=>{
    if(!confirm('Delete category? Your recipes will remain.'))return;
    await api('/categories/'+c.id,{method:'DELETE'});
    renderCategories();
  }));
  categoryGrid.append(row);
 }
 const categoryTools=card(node('h2',{text:'Categories'}),
   node('p',{class:'muted',text:'Organize by the type of food — Dinner, Dessert, Breakfast and more.'}));
 if(manage)categoryTools.append(node('div',{class:'toolbar'},
   node('label',{class:'grow'},'Category name',catInput),
   node('label',{},'Color',color),
   button('Add category',async()=>{
     await api('/categories',{method:'POST',data:{name:catInput.value,color:color.value}});
     announce('Category created');renderCategories();
   },'primary')));
 content.replaceChildren(folderTools,folderGrid,categoryTools,categoryGrid);
}

function isoDate(date){return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,10)}
async function renderPlan(){const [entries,recipesList]=await Promise.all([api('/meal-plan'),api('/recipes')]);const start=new Date();start.setDate(start.getDate()-((start.getDay()+6)%7));const date=node('input',{type:'date',value:isoDate(start)});const select=node('select');recipesList.forEach(r=>select.add(node('option',{value:String(r.id),text:r.title})));const meal=node('select');['anytime','breakfast','lunch','dinner','snack'].forEach(v=>meal.add(node('option',{value:v,text:v[0].toUpperCase()+v.slice(1)})));const day=node('input',{type:'date',value:isoDate(new Date())});const servings=node('input',{type:'number',value:'1'});servings.min='0.1';servings.step='0.5';const create=card(node('h2',{text:'Plan to Make'}),node('div',{class:'toolbar'},node('label',{},'Day',day),node('label',{},'Meal',meal),node('label',{class:'grow'},'Recipe',select),node('label',{},'Portions',servings),button('Add to planner',async()=>{await api('/meal-plan',{method:'POST',data:{day:day.value,meal:meal.value,recipe_id:Number(select.value),servings:Number(servings.value)}});announce('Recipe added to your plan');renderPlan()},'primary')));const week=node('div',{class:'week-grid'});const range=node('span',{class:'small muted',text:'Select a Monday to see another week'});function draw(){week.replaceChildren();const monday=new Date(date.value+'T12:00:00');for(let n=0;n<7;n++){const d=new Date(monday);d.setDate(d.getDate()+n);const key=isoDate(d);const block=node('div',{class:'day-cell'+(key===isoDate(new Date())?' today':'')},node('strong',{text:d.toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'})}));for(const e of entries.filter(x=>x.day===key)){block.append(button((e.meal==='anytime'?'Plan to make':e.meal)+': '+e.recipe_title,async()=>{if(confirm('Remove this planned meal?')){await api('/meal-plan/'+e.id,{method:'DELETE'});await renderPlan()}},'meal-chip'))}week.append(block)}}date.addEventListener('change',draw);content.replaceChildren(create,node('div',{class:'row between',class:'plan-week-heading'},node('h2',{text:'Your upcoming recipes'}),date,range),week);draw()}
async function renderShopping(){model.lists=await api('/shopping-lists');const listArea=node('div',{class:'grid'});for(const item of model.lists){const count=node('span',{class:'small muted',text:'Tap to open and check off items'});listArea.append(card(node('h3',{text:item.name}),count,button('Open list',()=>openShopping(item.id),'primary'),button('Delete list',async()=>{if(!confirm('Delete this shopping list?'))return;await api('/shopping-lists/'+item.id,{method:'DELETE'});renderShopping()})))}const name=node('input',{placeholder:'e.g. Saturday groceries'});content.replaceChildren(card(node('h2',{text:'Shopping lists'}),node('p',{class:'muted',text:'Select multiple recipes in Recipes to generate a shopping list automatically.'}),node('div',{class:'row'},node('div',{class:'grow'},name),button('+ New list',async()=>{if(!name.value.trim())return;const created=await api('/shopping-lists',{method:'POST',data:{name:name.value.trim()}});openShopping(created.id)},'primary'))),listArea)}
async function openShopping(id,{push=true}={}){if(push && location.pathname!==urlFor('shopping',id))history.pushState({},'',urlFor('shopping',id));const result=await api('/shopping-lists/'+id);const list=node('div',{class:'card'});const newName=node('input',{placeholder:'Add an item'}),qty=node('input',{placeholder:'Quantity / units'});const visibleItems=node('div');
const hiddenItems=node('div');
const haveItems=result.items.filter(it=>it.checked);
const showHave=node('input',{type:'checkbox'});
const haveToggle=node('label',{class:'check-row'},showHave,node('span',{text:'Show Have It ('+haveItems.length+')'}));
showHave.addEventListener('change',()=>{hiddenItems.hidden=!showHave.checked});
hiddenItems.hidden=true;
for(const it of result.items){const check=node('input',{type:'checkbox'});check.checked=!!it.checked;const description=node('span',{class:it.checked?'done':''},it.name+(it.quantity?' ('+it.quantity+')':''));check.addEventListener('change',async()=>{await api('/shopping-lists/'+id+'/items/'+it.id,{method:'PATCH',data:{name:it.name,quantity:it.quantity,checked:check.checked}});await openShopping(id,{push:false})});(it.checked?hiddenItems:visibleItems).append(node('div',{class:'list-row'},node('label',{class:'check-row'},check,description),button('Remove',async()=>{await api('/shopping-lists/'+id+'/items/'+it.id,{method:'DELETE'});openShopping(id)})))}
list.append(node('p',{class:'muted',text:(result.items.length-haveItems.length)+' items to buy'}),visibleItems,haveToggle,hiddenItems);content.replaceChildren(node('div',{class:'toolbar'},button('← All lists',renderShopping),node('h2',{text:result.name})),list,card(node('h3',{text:'Add an item'}),node('div',{class:'toolbar'},node('div',{class:'grow'},newName),node('div',{class:'grow'},qty),button('Add item',async()=>{if(!newName.value.trim())return;await api('/shopping-lists/'+id+'/items',{method:'POST',data:{name:newName.value.trim(),quantity:qty.value}});openShopping(id)},'primary'))))}

async function renderDiscover(){
 const entries=await api('/discovery/providers');
 const q=node('input',{type:'search',placeholder:'Try bourbon chicken, sesame chicken, garlic, rice…',maxLength:90});
 const provider=node('select');
 for(const item of entries)provider.add(node('option',{value:item.id,text:item.name+(item.id==='foodcom'?' ('+(item.available?(item.count||0).toLocaleString()+' recipes':'not installed')+')':'')}));
 const mode=node('select');mode.add(node('option',{value:'name',text:'Recipe name'}));mode.add(node('option',{value:'ingredient',text:'Ingredient'}));
 const results=node('div',{class:'discover-grid grid'}),information=node('p',{class:'small muted',text:'Search a recipe directory, preview the ingredients and directions, then review/edit before saving. Nothing is imported automatically.'});
 const search=button('Search recipes',async()=>{
   if(q.value.trim().length<2)throw Error('Enter at least two characters');
   search.disabled=true;results.replaceChildren(empty('Searching recipe directories…'));
   try{
      const r=await api('/discovery/search?'+new URLSearchParams({provider:provider.value,mode:mode.value,q:q.value.trim()}));
      information.textContent=r.notice||'';
      results.replaceChildren();
      if(!r.items.length)results.append(empty('No matches from the currently available sources. Try All recipe sources, a broader ingredient, or enable the optional Food.com library in Administration → Imports & feeds.'));
      for(const item of r.items){
        const picture=node('div',{class:'recipe-tile-photo'},node('span',{class:'recipe-placeholder',text:'🥗'}));
        if(item.image_url)setPicture(picture,item.image_url,item.title);
        const tile=card(picture,node('div',{class:'recipe-tile-copy'},node('span',{class:'pill',text:entries.find(e=>e.id===item.provider)?.name||item.provider}),
             node('h3',{text:item.title}),node('p',{class:'recipe-excerpt',text:item.description||'Preview available ingredients and directions.'})),
             node('footer',{},button('View recipe',async()=>{
                const draft=await api('/discovery/preview?'+new URLSearchParams({provider:item.provider,id:item.id}));
                const modal=node('dialog',{class:'recipe-preview-dialog'});
                const lines=(draft.ingredients||[]).map(i=>node('li',{text:typeof i==='string'?i:(i.raw||[i.quantity,i.unit,i.name].filter(Boolean).join(' '))}));
                const directions=(draft.instructions||[]).map((step,i)=>node('li',{text:typeof step==='string'?step:String(step.text||'')}));
                const photo=node('div',{class:'recipe-tile-photo'});
                if(draft.image_url)setPicture(photo,draft.image_url,draft.title||item.title);
                modal.append(node('div',{class:'recipe-preview-body'},
                  node('div',{class:'row between'},node('h2',{text:draft.title||item.title}),
                    button('Close',()=>modal.close())),
                  photo,
                  node('p',{text:draft.description||'No description available.'}),
                  node('h3',{text:'Ingredients'}),node('ul',{},...lines),
                  node('h3',{text:'Directions'}),node('ol',{},...directions),
                  draft.requires_manual_review?node('p',{class:'muted',text:'Some details may need review before saving.'}):null,
                  node('div',{class:'toolbar'},
                    button('Close',()=>modal.close()),
                    button('Import this recipe',async()=>{modal.close();await openEditor(null,draft)},'primary'))));
                modal.addEventListener('close',()=>modal.remove());
                document.body.append(modal);modal.showModal();
             },'primary')));
        tile.classList.add('recipe-tile');results.append(tile)
      }
   }finally{search.disabled=false}
 },'primary');
 q.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();search.click()}});
 const intro=card(node('h2',{text:'Search thousands of recipes'}),node('p',{class:'muted',text:'Search multiple recipe websites and the optional Food.com community collection in one search. Browse results, review ingredients and directions, and submit your favorites to the family collection.'}),
     node('div',{class:'form-grid'},node('label',{},'Find recipes',q),node('label',{},'Recipe directory',provider),node('label',{},'Search by',mode)),
     node('div',{class:'toolbar'},search,button('Import from a website instead',()=>navigate('sources'))),information);
 content.replaceChildren(intro,results);
}
async function renderSources(){const feeds=model.page==='admin-imports'?await api('/feeds'):[];const url=node('input',{id:'import-url',type:'url',placeholder:'https://example.com/recipe or Facebook/Instagram video'}),text=node('textarea',{rows:'5',placeholder:'Paste a complete recipe with Ingredients and Instructions headings'}),file=node('input',{type:'file'});file.accept='image/jpeg,image/png,image/webp,video/*';const importCard=card(node('h2',{text:'Import a recipe'}),node('p',{class:'muted',text:'The existing importer handles recipe websites, public social videos and images. Imports are saved to RecipeCove, not KitchenOwl.'}),node('label',{},'Recipe or video link',url),node('div',{class:'row'},button('Import from URL',async()=>{if(!url.value.trim())return;announce('Importing. Social videos can take several minutes.');const preview=await api('/imports/preview-url',{method:'POST',data:{url:url.value.trim()}});await openEditor(null,preview);announce('Review the extracted recipe, then select Save recipe')},'primary')),node('label',{},'Recipe text',text),button('Import text',async()=>{if(!text.value.trim())return;const preview=await api('/imports/preview-text',{method:'POST',data:{text:text.value}});await openEditor(null,preview);announce('Review the extracted recipe, then save')}),node('label',{},'Photo, screenshot or video',file),button('Import file',async()=>{if(!file.files.length)return;const f=file.files[0];const form=new FormData();form.append('file',f);announce('Processing uploaded file…');const preview=await api(f.type.startsWith('video/')?'/imports/preview-video':'/imports/preview-image',{method:'POST',form});await openEditor(null,preview);announce('Review the extracted text and save when ready')}));
const feedurl=node('input',{type:'url',placeholder:'https://example.com/feed.xml'}),feedtitle=node('input',{placeholder:'Feed name'}),feedsBlock=card(node('h2',{text:'Recipe feeds'}),node('p',{class:'muted',text:'Subscribe to public RSS or Atom feeds, preview recent entries, then choose recipes to import.'}),node('div',{class:'toolbar'},node('div',{class:'grow'},feedtitle),node('div',{class:'grow'},feedurl),button('Add feed',async()=>{await api('/feeds',{method:'POST',data:{url:feedurl.value,title:feedtitle.value||'Recipe feed'}});renderSources()},'primary')));for(const feed of feeds){feedsBlock.append(node('div',{class:'list-row'},node('div',{},node('strong',{text:feed.title}),node('p',{class:'small muted feed-link',text:feed.url})),button('Browse entries',()=>browseFeed(feed)),button('Remove',async()=>{if(!confirm('Remove this feed subscription?'))return;await api('/feeds/'+feed.id,{method:'DELETE'});renderSources()})))}content.replaceChildren(importCard,...(model.page==='admin-imports'?[feedsBlock]:[]))}
async function browseFeed(feed,{push=true}={}){if(push && location.pathname!==urlFor('sources',feed.id))history.pushState({},'',urlFor('sources',feed.id));content.replaceChildren(node('div',{class:'toolbar'},button('← Feeds',renderSources),node('h2',{text:feed.title})),empty('Loading entries…'));const entries=await api('/feeds/'+feed.id+'/entries'),list=node('div',{class:'stack'});if(!entries.length)list.append(empty('No entries found in this feed.'));for(const item of entries)list.append(card(node('h3',{text:item.title}),node('p',{class:'muted feed-link',text:item.url}),button('Import recipe',async()=>{const preview=await api('/imports/preview-url',{method:'POST',data:{url:item.url}});await openEditor(null,preview);announce('Review the recipe before saving')},'primary')));content.replaceChildren(node('div',{class:'toolbar'},button('← Feeds',renderSources),node('h2',{text:feed.title})),list)}

function adminSectionNav(){
 const nav=node('nav',{class:'section-nav','aria-label':'Administration sections'});
 for(const [name,label] of [['admin','Overview'],['admin-users','Family accounts'],['admin-review','Approval workflow'],['admin-imports','Imports & feeds'],['admin-backups','Backups'],['admin-api','Developer API'],['admin-updates','Updates'],['admin-ai','AI']])nav.append(button(label,()=>navigate(name),model.page===name?'primary':'secondary'));
 return nav;
}
async function renderReview(){
 const [settings,pending,history]=await Promise.all([api('/family/settings'),api('/submissions?status=pending'),api('/submissions?status=all')]);
 const isAdmin=model.user.role==='admin',mayApprove=settings.can_approve;
 const wrapper=node('div',{class:'stack'});
 wrapper.append(node('div',{class:'page-heading'},node('h2',{text:mayApprove?'Family recipe approval':'My recipe submissions'}),node('p',{text:mayApprove?'Review suggestions before they appear in the shared family recipe collection.':'Recipes you submit are visible to approvers until they approve or decline them.'})));
 if(mayApprove){
  const row=node('label',{class:'check-row'},node('input',{type:'checkbox'}),'Send my new recipes for review');row.querySelector('input').checked=settings.my_submit_for_review;
  row.querySelector('input').addEventListener('change',async e=>{try{await api('/me/preferences',{method:'PUT',data:{submit_for_review:e.target.checked}});announce('Your preference was updated')}catch(err){announce(err.message,true)}});
  wrapper.append(card(node('h3',{text:'My preferences'}),row));
 }else if(settings.can_contribute){
  const row=node('label',{class:'check-row'},node('input',{type:'checkbox'}),'Request approval before my recipes enter the family collection');row.querySelector('input').checked=settings.my_submit_for_review;
  row.querySelector('input').addEventListener('change',async e=>{try{await api('/me/preferences',{method:'PUT',data:{submit_for_review:e.target.checked}});announce('Your preference was updated')}catch(err){announce(err.message,true)}});
  wrapper.append(card(node('h3',{text:'My preferences'}),row));
 }
 if(isAdmin){
   const toggle=node('input',{type:'checkbox'});toggle.checked=settings.require_approval;
   toggle.addEventListener('change',async()=>{try{await api('/admin/family/settings',{method:'PUT',data:{require_approval:toggle.checked}});announce('Family approval policy updated')}catch(err){announce(err.message,true)}});
   wrapper.append(card(node('h3',{text:'Family-wide approval policy'}),node('p',{class:'muted',text:'When enabled, contributors can submit recipes, but only approvers and administrators can add them to the family collection.'}),node('label',{class:'check-row'},toggle,'Require approval for family contributors')));
 }
 const queue=card(node('h2',{text:mayApprove?'Waiting for approval':'My pending submissions'}));
 if(!pending.length)queue.append(empty('No pending recipes.'));
 for(const submission of pending){
   const preview=submission.data||{},detail=node('details',{},node('summary',{text:'Preview ingredients & directions'}),node('div',{class:'submission-preview'},node('h4',{text:'Ingredients'}),node('ul',{},...(preview.ingredients||[]).map(v=>node('li',{text:typeof v==='string'?v:(v.raw||v.name||'')}))),node('h4',{text:'Directions'}),node('ol',{},...(preview.instructions||[]).map(v=>node('li',{text:String(v)})))));
   const actions=node('div',{class:'toolbar'});
   if(mayApprove){
      actions.append(button('Approve for family',async()=>{if(!confirm('Add this recipe to the family collection?'))return;await api('/submissions/'+submission.id+'/approve',{method:'POST',data:{}});announce('Recipe approved');renderReview()},'primary'),button('Decline',async()=>{const reason=prompt('What should be changed or why was it declined?');if(!reason?.trim())return;await api('/submissions/'+submission.id+'/reject',{method:'POST',data:{reason}});announce('Recipe declined with feedback');renderReview()}));
   }else actions.append(button('Withdraw',async()=>{await api('/submissions/'+submission.id+'/cancel',{method:'POST'});renderReview()}));
   const previewImage=node('div',{class:'submission-image'});if(preview.image_url)setPicture(previewImage,preview.image_url,submission.title);
   queue.append(node('section',{class:'submission-card'},previewImage,node('div',{},node('h3',{text:submission.title}),node('p',{class:'muted small',text:'From '+(submission.requested_by||'a family member')}),detail,actions)));
 }
 wrapper.append(queue);
 const recent=card(node('h2',{text:'Recent decisions'}));const decided=history.filter(x=>x.status!=='pending').slice(0,15);
 if(!decided.length)recent.append(empty('No review history yet.'));
 for(const x of decided)recent.append(node('div',{class:'list-row'},node('strong',{text:x.title}),node('span',{class:'pill',text:x.status}),node('span',{class:'small muted',text:x.decision_note||''})));
 wrapper.append(recent);
 if(model.page.startsWith('admin'))content.replaceChildren(adminSectionNav(),wrapper);else content.replaceChildren(wrapper);
}
async function renderAdminImports(){
 const container=node('div',{class:'stack'});
 const url=node('input',{type:'url',placeholder:'http://kitchenowl.local:8080',autocomplete:'off'});
 const token=node('input',{type:'password',placeholder:'KitchenOwl long-lived token',autocomplete:'off'});
 const household=node('select');household.append(node('option',{value:'',text:'Choose a household'}));
 const status=node('p',{class:'muted',role:'status',text:'Connect to read available KitchenOwl households.'});
 const payload=dry_run=>({url:url.value.trim(),token:token.value.trim(),household_id:Number(household.value),dry_run});
 const connect=button('Find households',async()=>{if(!url.value||!token.value)throw Error('Enter KitchenOwl URL and token');const sections=token.value.trim().split('.');if(sections.length!==3||sections[2].length<32)throw Error('KitchenOwl long-lived token appears incomplete. Copy the entire token, including its signature.');status.textContent='Reading households (read only)…';const response=await api('/admin/kitchenowl/households',{method:'POST',data:{url:url.value,token:token.value}});household.replaceChildren(node('option',{value:'',text:'Choose a household'}),...response.map(h=>node('option',{value:String(h.id),text:h.name})));status.textContent=response.length+' household(s) found.'},'primary');
 const preview=button('Preview migration',async()=>{if(!household.value)throw Error('Choose a household');status.textContent='Fetching recipes for review…';const r=await api('/admin/kitchenowl/import',{method:'POST',data:payload(true)});status.textContent=`Found ${r.found} recipes, ${r.would_import} new, ${r.already_imported} previously copied, ${r.invalid} unsupported, ${r.unavailable||0} unavailable (IDs: ${(r.unavailable_ids||[]).join(', ')||'none'}). KitchenOwl was not modified.`},'primary');
 const importBtn=button('Copy recipes into RecipeCove',async()=>{if(!household.value)throw Error('Choose a household');if(!confirm('Copy recipes from this KitchenOwl household into RecipeCove? Your KitchenOwl data will be untouched.'))return;status.textContent='Copying recipes…';const r=await api('/admin/kitchenowl/import',{method:'POST',data:payload(false)});status.textContent=`Copied ${r.imported} recipe(s), ${r.already_imported} already present, ${r.invalid} unsupported, ${r.unavailable||0} unavailable (IDs: ${(r.unavailable_ids||[]).join(', ')||'none'}). KitchenOwl remains unchanged.`;announce('Migration complete')},'accent');
 container.append(card(node('h2',{text:'Import directly from KitchenOwl'}),node('p',{class:'muted',text:'Connect to a KitchenOwl instance, select a household, preview everything, then copy recipes. Existing KitchenOwl recipes are never changed or deleted.'}),node('div',{class:'form-grid'},node('label',{},'KitchenOwl server URL',url),node('label',{},'Long-lived API token',token),node('label',{},'Household',household)),node('div',{class:'toolbar'},connect,preview,importBtn),status));
 const catalogFile=node('input',{type:'file',accept:'.csv,.zip'});
 const licensed=node('input',{type:'checkbox'}),dbProgress=node('p',{class:'muted small',role:'status',text:'Reading local community collection…'});
 const upload=button('Install community recipe collection',async()=>{
  if(!catalogFile.files.length)throw Error('Select RAW_recipes.csv or the ZIP containing it first.');
  if(!licensed.checked)throw Error('Confirm that you have permission to use the dataset.');
  if(!confirm('Build a separate, searchable Food.com community collection? This never modifies your family recipes.'))return;
  const form=new FormData();form.append('file',catalogFile.files[0]);form.append('accept_license','true');
  upload.disabled=true;dbProgress.textContent='Uploading dataset. Large CSV files can take several minutes…';
  try{
   const result=await api('/discovery/dataset/import',{method:'POST',form});
   dbProgress.textContent=result.message||'Building searchable index…';announce('Dataset indexing started');
  }finally{upload.disabled=false}
 },'primary');
 const updateDatasetStatus=async()=>{
  if(model.page!=='admin-imports')return;
  try{
   const p=await api('/discovery/dataset/status');
   dbProgress.textContent=(p.ready?p.count.toLocaleString()+' searchable Food.com recipes. ':'')+
     (p.message||'')+(p.phase==='indexing'?' ('+(p.progress||0)+'%)':'');
  }catch(e){dbProgress.textContent='Cannot read dataset status: '+e.message}
 };
 container.append(card(node('h2',{text:'Expanded recipe discovery'}),
   node('p',{class:'muted',text:'Search Spend With Pennies, Skinnytaste, The Chunky Chef, Well Plated, The Recipe Critic, Salt & Lavender, Ambitious Kitchen, Wikibooks and TheMealDB together, plus an optional local Food.com database of roughly 230,000 recipes. Budget Bytes is available individually when its public API allows access. Search is separate from family recipes; selected items are reviewed before saving.'}),
   button('Search all recipe sources',()=>navigate('discover'),'primary')));
 container.append(card(node('h2',{text:'Optional offline Food.com collection'}),
   node('p',{class:'muted',text:'Download the Food.com Recipes and User Interactions dataset yourself from Kaggle and upload RAW_recipes.csv or its ZIP archive. RecipeCove streams the import into a separate SQLite search index within /data. No new container, paid service or API key is needed. Expect several hundred megabytes of storage.'}),
   node('a',{href:'https://www.kaggle.com/datasets/shuyangli94/food-com-recipes-and-user-interactions',target:'_blank',rel:'noopener noreferrer',text:'Food.com community dataset (Kaggle)'}),
   node('label',{},'Food.com RAW_recipes.csv or ZIP',catalogFile),
   node('label',{class:'check-row'},licensed,'I have permission to use this dataset and understand its license/attribution requirements.'),
   node('div',{class:'toolbar'},upload,button('Refresh import status',updateDatasetStatus)),dbProgress));
 await updateDatasetStatus();
 const imports=node('div');await renderSources();
 while(content.firstChild)imports.append(content.firstChild);
 container.append(imports);
 content.replaceChildren(adminSectionNav(),container);
}
async function renderUpdaterPanel(){
 // Keep progress contextual: after an update succeeds, show the current version
 // rather than leaving behind a permanent completed progress bar.
 const busyPhases=new Set(['checking','downloading','verifying','installing','restarting']);
 const statusTag=node('span',{class:'updater-status',role:'status',text:'Checking for updates…'});
 const installedVersion=node('strong',{class:'updater-version-number',text:'—'});
 const newestVersion=node('strong',{class:'updater-version-number',text:'—'});
 const headline=node('h2',{class:'updater-state-heading',text:'Checking your version…'});
 const explanation=node('p',{class:'muted updater-description',text:'Looking for the latest compatible RecipeCove release.'});
 const warning=node('p',{class:'warning',role:'alert',hidden:true});
 const releaseLink=node('a',{class:'updater-release-link',target:'_blank',rel:'noopener noreferrer',text:'Read release details ↗',hidden:true});
 const notesList=node('ul',{class:'updater-notes-list'});
 const notesBox=node('section',{class:'updater-notes',hidden:true},
   node('h3',{text:"What's new in this release"}),notesList);
 const progress=node('progress',{value:'0',max:'100',class:'update-progress update-progress-large'});
 const percent=node('strong',{class:'updater-percent',text:'0%'});
 const task=node('p',{class:'muted small',role:'status',text:''});
 const milestones=[
   ['Checking','Checking release'],['Downloading','Downloading files'],
   ['Verifying','Verifying update'],['Installing','Installing safely'],['Restarting','Restarting']
 ];
 const steps=milestones.map(([key,label])=>{
   const mark=node('span',{class:'updater-step-mark',text:'○'});
   const item=node('li',{class:'updater-step'},mark,node('span',{text:label}));
   return {key:key.toLowerCase(),item,mark};
 });
 const progressSection=node('section',{class:'updater-running',hidden:true},
   node('div',{class:'row between'},node('strong',{text:'Updating RecipeCove'}),percent),
   progress,task,node('ol',{class:'updater-steps'},...steps.map(x=>x.item)));
 const checkBtn=button('Check again',check);
 const installBtn=button('Install update',install,'primary');
 const rollbackBtn=button('Restore previous version',rollback);
 installBtn.disabled=true;rollbackBtn.hidden=true;rollbackBtn.disabled=true;
 const main=card(
   node('div',{class:'row between updater-header'},
     node('div',{},node('p',{class:'eyebrow',text:'SYSTEM MAINTENANCE'}),
       node('h2',{text:'Keep RecipeCove up to date'}),
       node('p',{class:'muted',text:'Updates are checked automatically when you open this page.'})),statusTag),
   node('div',{class:'updater-versions'},
     node('section',{class:'updater-version-tile'},node('span',{class:'updater-version-caption',text:'Installed on this server'}),installedVersion),
     node('section',{class:'updater-version-tile'},node('span',{class:'updater-version-caption',text:'Latest published release'}),newestVersion)),
   node('section',{class:'updater-summary'},headline,explanation,releaseLink),
   notesBox,progressSection,warning,
   node('div',{class:'updater-actions'},installBtn,checkBtn,rollbackBtn));
 const detail=card(node('h3',{text:'How updates work'}),
   node('p',{class:'muted',text:'RecipeCove verifies the update, installs it on your existing server, and restarts automatically. Your recipes, members, photos, and settings remain in persistent storage. If the new version fails its health check, RecipeCove restores the previous version.'}),
   node('p',{class:'small muted',text:'Some releases need a newer base Docker image or operating-system packages. RecipeCove will tell you when those updates cannot be installed here; it will never silently replace the container.'}));
 content.replaceChildren(adminSectionNav(),main,detail);

 let available=null,latestCheck=null,targetVersion=null,checking=false,updating=false,seenRunning=null,completedRefresh=false;
 let operation='idle';
 const setStatus=(kind,label)=>{
   statusTag.className='updater-status updater-status-'+kind;
   statusTag.textContent=label;
 };
 const showProblem=(message)=>{
   warning.hidden=false;warning.textContent=message;
   headline.textContent='Something needs attention';
   explanation.textContent=message;
   setStatus('error','Needs attention');
 };
 const synchronizeButtons=()=>{
   const locked=updating||busyPhases.has(operation)||checking;
   checkBtn.disabled=locked;
   installBtn.disabled=locked||!available?.available;
   installBtn.hidden=!available?.available&&!locked;
   installBtn.textContent=available?.available?'Install v'+available.latest_version:'Install update';
   rollbackBtn.disabled=locked;
 };
 const setAvailability=(data)=>{
   latestCheck=data;available=data;
   newestVersion.textContent=data.latest_version?'v'+data.latest_version:'Unavailable';
   const releaseNotes=Array.isArray(data.release_notes)?data.release_notes.filter(x=>typeof x==='string'&&x.trim()):[];
   notesList.replaceChildren(...releaseNotes.map(text=>node('li',{text})));
   notesBox.hidden=!data.available||releaseNotes.length===0;
   releaseLink.hidden=true;
   if(data.release_url){
     try{
       const u=new URL(data.release_url);
       if(u.protocol==='https:'&&u.hostname==='github.com'){
         releaseLink.href=u.href;releaseLink.hidden=false;
       }
     }catch(e){}
   }
   if(data.code_error){
     showProblem('Could not check the latest release: '+data.code_error);
   }else if(data.requires_image){
     setStatus('manual','Manual update needed');
     headline.textContent='A container update is required';
     explanation.textContent='The new release changes dependencies or the base runtime. Your server cannot safely install it from inside the application. Your recipes and settings stay protected.';
   }else if(data.available){
     setStatus('available','Update available');
     headline.textContent='A newer version is ready';
     explanation.textContent='RecipeCove v'+data.latest_version+' is available. You are using v'+(data.current_version||'unknown')+'. Install it here without needing an external update service.';
   }else{
     setStatus('current','Up to date');
     headline.textContent='You are up to date';
     explanation.textContent='Your RecipeCove installation is running the newest compatible release.';
   }
   synchronizeButtons();
 };
 async function refresh(){
   if(model.page!=='admin-updates'||!model.user)return;
   try{
     const result=await api('/admin/updates/status');
     const installed=result.installed||{},op=result.operation||{};
     const running=result.running_version||installed.version||'unknown';
     installedVersion.textContent='v'+running;
     rollbackBtn.hidden=!installed.previous;
     const phase=op.phase||'idle';
     operation=phase;
     const active=busyPhases.has(phase);
     progressSection.hidden=!active;
     if(active){
       const amount=Math.max(0,Math.min(100,Number(op.progress)||0));
       progress.value=amount;
       percent.textContent=amount+'%';
       task.textContent=op.message||'Working on your update…';
       const index=Math.max(0,steps.findIndex(x=>x.key===phase));
       steps.forEach((s,i)=>{
         s.item.className='updater-step'+(i<index?' is-done':i===index?' is-active':'');
         s.mark.textContent=i<index?'✓':i===index?'●':'○';
       });
       setStatus('running','Updating…');
       headline.textContent=phase==='restarting'?'Restarting RecipeCove…':'Installing your update…';
       explanation.textContent='Please leave this page open while RecipeCove finishes the update.';
     }else if(phase==='failed'||phase==='rolled_back'){
       updating=false;targetVersion=null;
       showProblem(phase==='rolled_back'?'The update did not start successfully; the previous version has been restored.':(op.message||'The update could not be installed.'));
     }else if(targetVersion&&running===targetVersion&&phase==='ready'){
       // The server is healthy again. Refresh to load the newly installed UI.
       targetVersion=null;updating=false;
       location.reload();
       return;
     }else if(targetVersion&&phase==='ready'&&running!==targetVersion){
       updating=false;targetVersion=null;
       showProblem('RecipeCove restarted, but the expected version is not running. Check server logs before trying again.');
     }else if(latestCheck&&!checking){
       setAvailability(latestCheck);
     }
     if(!seenRunning)seenRunning=running;
     else if(running!==seenRunning&&phase==='ready'&&!completedRefresh){
       completedRefresh=true;
       location.reload();
       return;
     }
     synchronizeButtons();
   }catch(e){
     if(updating||busyPhases.has(operation)){
       setStatus('running','Reconnecting…');
       headline.textContent='Waiting for RecipeCove to restart…';
       explanation.textContent='The connection may be briefly interrupted during the update. Retrying automatically.';
     }else showProblem('Could not read the update status: '+e.message);
   }
 }
 async function check(){
   if(checking||updating||busyPhases.has(operation))return;
   checking=true;warning.hidden=true;
   setStatus('checking','Checking…');
   headline.textContent='Looking for a new release…';
   explanation.textContent='Connecting securely to the published RecipeCove releases.';
   synchronizeButtons();
   try{
     const result=await api('/admin/updates/check',{method:'POST'});
     setAvailability(result);
   }catch(e){
     available=null;showProblem('Unable to check for updates: '+e.message);
   }finally{checking=false;synchronizeButtons();}
 }
 async function install(){
   if(!available?.available||updating||busyPhases.has(operation))return;
   if(!confirm('Install RecipeCove v'+available.latest_version+'? The app will restart automatically. Your recipes and settings will be preserved.'))return;
   try{
     warning.hidden=true;targetVersion=available.latest_version;updating=true;
     await api('/admin/updates/install',{method:'POST'});
     operation='checking';progressSection.hidden=false;
     setStatus('running','Update started');
     headline.textContent='Preparing your update…';
     explanation.textContent='The server is checking and verifying this release.';
     synchronizeButtons();
     await refresh();
   }catch(e){
     updating=false;targetVersion=null;operation='failed';
     showProblem('The update could not start: '+e.message);
     synchronizeButtons();
   }
 }
 async function rollback(){
   if(updating||busyPhases.has(operation))return;
   if(!confirm('Restore the previous RecipeCove version? Your saved recipes and data will be preserved, and the app will restart.'))return;
   try{
     warning.hidden=true;updating=true;
     await api('/admin/updates/rollback',{method:'POST'});
     operation='restarting';targetVersion=null;
     setStatus('running','Restoring previous version…');
     headline.textContent='Restoring your previous version…';
     explanation.textContent='RecipeCove will restart automatically. Your data will not be removed.';
     synchronizeButtons();
     await refresh();
   }catch(e){
     updating=false;operation='failed';showProblem('Rollback could not start: '+e.message);
     synchronizeButtons();
   }
 }
 // Immediate status and discovery, then follow the actual backend operation.
 await refresh();
 if(!busyPhases.has(operation))await check();
 if(model.updaterTimer)clearInterval(model.updaterTimer);
 model.updaterTimer=setInterval(()=>{
   if(model.page==='admin-updates'&&model.user)refresh();
   else clearInterval(model.updaterTimer);
 },2000);
}

async function renderApiPanel(){
 const keys=await api('/admin/api-keys');const box=card(node('h2',{text:'Developer API for future integrations'}),node('p',{class:'muted',text:'RecipeCove v2 provides a stable, read-only API for published recipes. Keep family notes and unapproved recipes private. API keys are shown only once.'}));
 const field=node('input',{placeholder:'e.g. Website recipe integration'});
 box.append(node('div',{class:'toolbar'},field,button('Create read-only API key',async()=>{if(!field.value.trim())throw Error('Name the API key');const result=await api('/admin/api-keys',{method:'POST',data:{label:field.value}});const message=node('div',{class:'warning'},node('strong',{text:'Copy this key now; it cannot be shown again.'}),node('pre',{class:'evidence-text',text:result.key}));box.append(message,button('Copy key',()=>navigator.clipboard.writeText(result.key)));announce('API key created')},'primary')));
 for(const key of keys)box.append(node('div',{class:'list-row'},node('strong',{text:key.label}),node('span',{class:'small muted',text:key.revoked_at?'Revoked':'Active · '+key.scope}),button('Revoke',async()=>{if(!confirm('Revoke this key immediately?'))return;await api('/admin/api-keys/'+key.id,{method:'DELETE'});renderApiPanel()})));
 box.append(node('h3',{text:'API endpoints'}),node('p',{class:'muted',text:'GET /api/v2/public/recipes?page=1&limit=20  ·  GET /api/v2/public/recipes/{id}  ·  GET /api/v2/openapi.json'}),node('p',{class:'small muted',text:'Send X-RecipeCove-API-Key in the request header. Publication is opt-in and requires an approver.'}));
 content.replaceChildren(adminSectionNav(),box);
}
async function renderAdmin(){if(model.user.role!=='admin'){content.replaceChildren(empty('Administrator access required'));return}
if(model.page==='admin-imports')return renderAdminImports();
if(model.page==='admin-review')return renderReview();
if(model.page==='admin-api')return renderApiPanel();
if(model.page==='admin-updates')return renderUpdaterPanel();
const [stats,users,aiSettings]=await Promise.all([api('/admin/overview'),api('/admin/users'),api('/admin/ai')]);const metrics=node('div',{class:'grid'});for(const [label,key] of [['Family members','users'],['Saved recipes','recipes'],['Planned meals','meal_plan'],['Shopping lists','shopping_lists'],['Subscribed feeds','feeds']])metrics.append(card(node('div',{class:'stat',text:String(stats[key]||0)}),node('strong',{text:label})));const userlist=card(node('h2',{text:'Family accounts'}));for(const u of users)userlist.append(node('div',{class:'list-row'},node('div',{},node('strong',{text:u.display_name+' ('+u.username+')'}),node('p',{class:'muted small',text:u.role+' · '+(u.enabled?'Enabled':'Disabled')})),button('Edit',()=>editUser(u))));const uname=node('input',{placeholder:'Username'}),display=node('input',{placeholder:'Display name'}),password=node('input',{type:'password',placeholder:'Password (12+ characters)'}),role=node('select');for(const [value,title] of [['viewer','Viewer'],['contributor','Contributor — can submit recipes'],['approver','Approver — can approve & edit'],['admin','Administrator']])role.append(node('option',{value,text:title}));role.value='contributor';userlist.append(node('h3',{text:'Add family member'}),node('div',{class:'toolbar'},uname,display,password,role,button('Create user',async()=>{await api('/admin/users',{method:'POST',data:{username:uname.value,display_name:display.value,password:password.value,role:role.value}});announce('User added');navigate('admin-users',{push:false})},'primary')));
const migrationInput=node('input',{type:'file'});migrationInput.accept='.json,application/json';const migration=card(node('h2',{text:'Migration & backups'}),node('p',{class:'muted',text:'Export your RecipeCove data as JSON, or preview and copy recipes from an existing KitchenOwl JSON export. Existing KitchenOwl data is never altered.'}),button('Download RecipeCove JSON backup',async()=>{const backup=await api('/admin/export'),blob=new Blob([JSON.stringify(backup,null,2)],{type:'application/json'}),href=URL.createObjectURL(blob),a=node('a',{href,download:'forknook-export.json'});document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(href),1000)},'primary'),node('label',{},'KitchenOwl recipe JSON export',migrationInput),button('Preview migration',()=>runMigration(migrationInput,true)),button('Preview repair',()=>runMigration(migrationInput,true,true)),button('Repair imported recipes',async()=>{if(!migrationInput.files.length){announce('Choose your original KitchenOwl JSON export first.',true);return}if(!confirm('Fill missing fields in matching RecipeCove recipes? Existing edits and photographs will be preserved.'))return;await runMigration(migrationInput,false,true)},'secondary'),button('Import compatible recipes',async()=>{if(!confirm('Copy valid recipes into RecipeCove? KitchenOwl will remain unchanged.'))return;await runMigration(migrationInput,false)}));const updateStatus=node('p',{id:'updater-data',class:'muted',text:'Checking installed version…'}),updateBar=node('progress',{id:'updater-progress',value:'0',max:'100',class:'update-progress update-progress-large'});
const updateCheck=button('Check for updates',async()=>{updateStatus.textContent='Checking GitHub Releases…';const data=await api('/admin/updates/check',{method:'POST'});updateStatus.textContent=data.available?'Version '+data.latest_version+' available. Installed: '+data.current_version:'Running '+data.current_version+' · newest in-app release '+data.latest_version+' (Docker image updates are separate)';model.updateAvailable=Boolean(data.available);updateInstall.disabled=!model.updateAvailable},'primary');
const updateInstall=button('Install update',async()=>{if(!model.updateAvailable)throw Error('Check for updates first');if(!confirm('Install and restart RecipeCove?'))return;await api('/admin/updates/install',{method:'POST'});model.updateAvailable=false;updateInstall.disabled=true;announce('Verified update install started; checking restart status…');refreshRecipeCoveUpdater()},'secondary');updateInstall.disabled=true;
const updateRollback=button('Roll back',async()=>{if(!confirm('Roll back to the previous server version?'))return;await api('/admin/updates/rollback',{method:'POST'});announce('Rollback requested');refreshRecipeCoveUpdater()},'secondary');updateRollback.disabled=true;
const info=card(node('h2',{text:'Application updates'}),node('p',{class:'muted',text:'Install compatible RecipeCove server and web UI updates here. Changes to Docker, Python dependencies, or system packages require an image redeploy.'}),updateStatus,updateBar,node('div',{class:'toolbar'},updateCheck,updateInstall,updateRollback),node('p',{class:'small muted',text:'If the in-app updater is inaccessible, use the manual Docker update block from the repository documentation. Your /data volume stays mounted.'}));
async function refreshRecipeCoveUpdater(){if(model.page!=='admin-updates')return;try{const result=await api('/admin/updates/status');const op=result.operation||{},installed=result.installed||{};updateBar.value=Number(op.progress||0);updateStatus.textContent='Running '+(result.running_version||'unknown')+' · Docker image '+(result.image_version||'unknown')+' · updater '+(installed.version||'unknown')+' · '+(op.phase||'idle')+' · '+(op.message||'Ready');updateRollback.disabled=!installed.previous; if(['checking','downloading','verifying','installing','restarting'].includes(op.phase)){updateCheck.disabled=true;updateInstall.disabled=true}else{updateCheck.disabled=false;updateInstall.disabled=!model.updateAvailable}}catch(e){updateStatus.textContent='Cannot read update status: '+e.message}}
if(model.page==='admin-updates'){refreshRecipeCoveUpdater(); clearInterval(model.updaterTimer); model.updaterTimer=setInterval(()=>{if(model.page==='admin-updates'&&model.user)refreshRecipeCoveUpdater();else clearInterval(model.updaterTimer)},5000)}
const aiKey=node('input',{type:'password',placeholder:'Leave blank to keep the saved key'});
const aiModel=node('input',{value:aiSettings.model||'gpt-4o-mini'});
const aiPanel=card(node('h2',{text:'Optional AI recipe structuring'}),
 node('p',{class:'muted',text:'Speech transcription and OCR run locally. For difficult conversational recipes, an optional external AI parser can help organize ingredients and directions; API charges may apply.'}),
 node('p',{class:'small',text:aiSettings.api_key_configured?'An API key is configured.':'No API key configured. The offline rules-based parser remains available.'}),
 node('div',{class:'form-grid'},node('label',{},'OpenAI API key',aiKey),node('label',{},'Model',aiModel)),
 node('div',{class:'toolbar'},button('Save AI settings',async()=>{await api('/admin/ai',{method:'POST',data:{api_key:aiKey.value||null,model:aiModel.value}});aiKey.value='';announce('AI settings saved encrypted on your server');navigate('admin-ai',{push:false})},'primary'),button('Clear stored API key',async()=>{if(!confirm('Disable external AI processing?'))return;await api('/admin/ai',{method:'POST',data:{api_key:'',model:aiModel.value}});navigate('admin-ai',{push:false})})));
const tabs=adminSectionNav();
 const section={'admin':[metrics,card(node('h2',{text:'Manage RecipeCove'}),node('p',{class:'muted',text:'Choose a section above to manage family accounts, safely migrate recipes, configure AI, or update the app.'}))], 'admin-users':[userlist],'admin-backups':[migration],'admin-updates':[info],'admin-ai':[aiPanel]};
 const chosen=section[model.page]||section.admin;
 content.replaceChildren(tabs,...chosen)}
async function runMigration(input,dry,repair=false){if(!input.files.length){announce('Select a JSON export first',true);return}const data=new FormData();data.append('file',input.files[0]);const result=await api('/admin/import-json?dry_run='+(dry?'true':'false')+'&repair_existing='+(repair?'true':'false'),{method:'POST',form:data});announce(dry?(repair?`${result.would_repair||0} existing recipes can be repaired; no data changed.`:`${result.valid} valid recipes found; no data changed.`):`Imported ${result.imported} recipes; repaired ${result.repaired||0}; ${result.skipped} already present.`);if(!dry)navigate('admin-backups',{push:false})}
async function editUser(u){const display=prompt('Display name',u.display_name);if(display===null)return;const role=prompt('Role: admin, approver, contributor, viewer',u.role);if(role===null)return;const enabled=confirm('Should this account remain enabled? OK = enabled, Cancel = disabled');const password=prompt('New password (leave blank to keep current password)','');if(password===null)return;const data={display_name:display,role,enabled};if(password)data.password=password;await api('/admin/users/'+u.id,{method:'PATCH',data});announce('Account updated');navigate('admin-users',{push:false})}
if('serviceWorker'in navigator&&location.protocol==='https:')navigator.serviceWorker.register('/app/sw.js',{scope:'/app/'}).catch(()=>{});
start();
