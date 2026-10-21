'use strict';
const assert = require('node:assert/strict');
const {createTailscaleCard} = require('../web/js/tailscale-status.js');
function fixture() {
    let now = 0, calls = 0, id = 0, fetcher;
    const timers = new Map(), handlers = {}, elements = {};
    for (const key of ['tailscale-card','tailscale-state','tailscale-time','tailscale-refresh']) {
        elements[key] = {hidden:true, textContent:'', disabled:false, addEventListener(type, fn) { this[type]=fn; }};
    }
    const page = {hidden:false, getElementById:id=>elements[id], addEventListener:(type,fn)=>handlers[type]=fn};
    const card = createTailscaleCard({document:page, fetch:(url,opts)=>{
        calls++; assert.equal(url,'/api/management/tailscale'); assert.equal(opts.cache,'no-store');
        assert.equal(opts.credentials,'same-origin'); return fetcher(opts);
    }, now:()=>now, later:(fn,delay)=>{timers.set(++id,{fn,at:now+delay});return id;},
    cancel:id=>timers.delete(id),listen:(type,fn)=>handlers[type]=fn});
    return {card, page, handlers, elements, timers, count:()=>calls,
        reply:data=>{fetcher=async()=>({ok:true,status:200,json:async()=>data});},
        fetch:fn=>{fetcher=fn;}, advance:delta=>{now+=delta;for(const [key,value] of [...timers]) if(value.at<=now){timers.delete(key);value.fn();}},
        online:()=>elements['tailscale-state'].textContent==='控制平面在线'};
}
const online = {enabled:true,state:'online',reason:'control_connected',observed_at:1234,stale:false,valid_for_seconds:30};
(async()=>{
    let f=fixture();f.reply({enabled:false});await f.card.refresh();assert(f.elements['tailscale-card'].hidden);
    f.reply(online);await f.card.refresh();assert(f.online());assert(!f.elements['tailscale-card'].hidden);
    f.advance(30000);assert(!f.online());assert.equal(f.count(),2); // no automatic network polling
    f=fixture();f.reply({...online,valid_for_seconds:1});await f.card.refresh();f.advance(1000);assert(!f.online());
    for(const payload of [{...online,reason:'<img src=x>'},{...online,state:'offline'}, {...online,stale:true},
        {...online,valid_for_seconds:1000},{...online,valid_for_seconds:-1}, {...online,valid_for_seconds:'30'}]) {
        f=fixture();f.reply(payload);await f.card.refresh();assert(!f.online());
        assert(!f.elements['tailscale-state'].textContent.includes('<img'));
    }
    f=fixture();f.reply({...online,state:'unknown',reason:'cleanup_failed',stale:true,valid_for_seconds:0});
    await f.card.refresh();assert(f.elements['tailscale-state'].textContent.includes('采样已停止'));
    assert(!f.online());
    f=fixture();let resolve;
    f.fetch(()=>new Promise(r=>resolve=r));let first=f.card.refresh();await f.card.refresh();assert.equal(f.count(),1);
    f.advance(2000);resolve({ok:true,status:200,json:async()=>({...online,valid_for_seconds:1})});await first;assert(!f.online());
    f=fixture();f.reply(online);await f.card.refresh();assert(f.online());
    f.fetch(async()=>{throw new Error('secret raw error');});await f.card.refresh();assert(!f.online());
    assert(!f.elements['tailscale-state'].textContent.includes('secret'));
    for(const event of ['pagehide','popstate','pageshow','visibilitychange']) {
        f=fixture();f.fetch(()=>new Promise(r=>resolve=r));first=f.card.refresh();
        if(event==='visibilitychange')f.page.hidden=true;
        f.handlers[event]({persisted:true});
        resolve({ok:true,status:200,json:async()=>online});await first;assert(!f.online());
    }
    for (const event of ['popstate', 'pageshow']) {
        f=fixture();f.reply(online);await f.card.refresh();
        f.handlers.pagehide();f.handlers[event]({persisted:true});assert(!f.online());
        assert(!f.elements['tailscale-card'].hidden);await f.card.refresh();assert(f.online());
    }
    f=fixture();f.reply(online);await f.card.refresh();f.page.hidden=true;f.handlers.visibilitychange();
    f.page.hidden=false;f.handlers.visibilitychange();assert(!f.online());assert.equal(f.count(),1);
    await f.card.refresh();assert(f.online());
    f=fixture();f.reply(online);await f.card.refresh();f.fetch(async()=>({status:401,ok:false}));await f.card.refresh();
    assert(f.elements['tailscale-card'].hidden);assert(!f.online());
    f=fixture();f.fetch(opts=>new Promise((_,reject)=>opts.signal.addEventListener('abort',()=>reject(new Error('abort')))));
    first=f.card.refresh();f.advance(4000);await first;assert(!f.online());assert(!f.elements['tailscale-refresh'].disabled);
    console.log('Tailscale card DOM/time/fetch lifecycle controls passed; no daemon or network qualification.');
})().catch(error=>{console.error(error);process.exitCode=1;});
