const VERSION='recipecove-shell-2.3.7';
const ASSETS=['/app/','/app/forknook.css','/app/forknook.js','/app/recipecove-appicon-192.webp','/app/recipecove-appicon-512.webp','/app/recipecove-logo.svg','/app/manifest.json'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(VERSION).then(c=>c.addAll(ASSETS)).then(()=>self.skipWaiting()))});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==VERSION).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',e=>{
 const req=e.request,url=new URL(req.url);
 if(req.method!=='GET'||url.origin!==self.location.origin||url.pathname.startsWith('/api/'))return;
 if(!url.pathname.startsWith('/app/'))return;
 e.respondWith(fetch(req).then(response=>{
  if(response.ok&&ASSETS.includes(url.pathname)){
   const copy=response.clone();caches.open(VERSION).then(cache=>cache.put(req,copy));
  }return response;
 }).catch(()=>caches.match(req)));
});
