const VERSION='forknook-shell-2.1.3';
const ASSETS=['/app/','/app/forknook.css','/app/forknook.js','/app/icon-192.png','/app/icon-512.png','/app/manifest.json'];
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
