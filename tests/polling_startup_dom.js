'use strict';
// Actual unchanged controller/template execution with checksum-pinned Vue/Axios.
// The Axios adapter, clock, visibility events and renderer host are synthetic.
// This is finite startup/lifecycle coverage, not native browser or host evidence.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const {test} = require('node:test');
const root = path.resolve(__dirname, '..');
const assets = process.env.VUI_FRONTEND_ASSETS;
assert(assets, 'VUI_FRONTEND_ASSETS must point to checksum-pinned frontend assets');
const pins = JSON.parse(fs.readFileSync(path.join(root, 'deploy/frontend-pins.json')));
const provenance = JSON.parse(fs.readFileSync(path.join(assets, 'PROVENANCE.json')));
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const source = fs.readFileSync(path.join(root, 'web/js/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const assetSources = {};
for (const name of ['vue', 'axios']) {
    for (const [key, value] of Object.entries(pins[name]))
        assert.deepEqual(provenance[name][key], value, name + ' provenance ' + key);
    assetSources[name] = fs.readFileSync(path.join(assets, name + '.js'), 'utf8');
    assert.equal(hash(assetSources[name]), provenance[name].selected_sha256[name + '.js']);
}
const SYSTEM = '/api/system/status', CORE = '/api/cores/status', AUTH = '/api/auth/me';
const unrelated = ['/api/inbounds', '/api/routing/mihomo/catalog', '/api/routing/mihomo',
    '/api/routing/mihomo/preview', '/api/subscription/mihomo-warnings'];
function renderer(V) {
    const node = (type,text='') => ({type,text,children:[],parent:null,props:{}});
    const remove = n => {if(n.parent)n.parent.children.splice(n.parent.children.indexOf(n),1);n.parent=null;};
    return {host:node('root'), ...V.createRenderer({createElement:node,createText:t=>node('#text',t),createComment:t=>node('#comment',t),
        insert(n,p,a=null){remove(n);n.parent=p;p.children.splice(a?p.children.indexOf(a):p.children.length,0,n);},remove,
        setText(n,t){n.text=t;},setElementText(n,t){n.text=t;n.children=[];},patchProp(n,k,p,v){n.props[k]=v;},
        parentNode:n=>n.parent,nextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1]||null})};
}
const walk=n=>[n,...n.children.flatMap(walk)];
const text=n=>((n.type==='#comment'?'':n.text)+n.children.map(text).join(' ')).replace(/\s+/g,' ').trim();
function fixture(t, held = [], initiallyHidden = false) {
    let now=0, timerId=0, options, state, mountedDone=false, mounting, generation=0, authStatus=200;
    const timers=new Map(),listeners=new Set(),requests=[],errors=[],renderErrors=[],redirects=[],channels=[],readers=[];
    const holds=new Set(held);
    const document={hidden:initiallyHidden,getElementById(){return null;},addEventListener(name,fn){assert.equal(name,'visibilitychange');listeners.add(fn);},
        removeEventListener(name,fn){assert.equal(name,'visibilitychange');listeners.delete(fn);}};
    const later=(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay,due:now+delay});return id;};
    const cancel=id=>timers.delete(id);
    const context=vm.createContext({console:{error:(...args)=>errors.push(args),warn:(...args)=>errors.push(args)},
        setTimeout:later,clearTimeout:cancel,addEventListener(){},
        location:{hash:'',replace:to=>redirects.push(to)},
        BroadcastChannel:class {constructor(){channels.push(this);}postMessage(){}close(){}},
        ElementPlus:{ElMessage:{error:(...args)=>errors.push(args),success(){},warning(){}},ElMessageBox:{}},ElementPlusIconsVue:{}});
    vm.runInContext(assetSources.vue,context,{filename:'pinned/vue.js'});
    vm.runInContext(assetSources.axios,context,{filename:'pinned/axios.js'});
    context.document=document;
    const V=context.Vue,A=context.axios;
    assert.equal(V.version,pins.vue.version);assert.equal(A.VERSION,pins.axios.version);assert.equal(A.defaults.timeout,0);
    const dataFor=url=>url===AUTH?{username:'synthetic-admin'}:url===SYSTEM?
        {os:'Synthetic Linux',cpu:{percent:generation?12:37,cores:2},memory:{percent:generation?18:64,used:'64 MiB',total:'100 MiB'}}:
        url===CORE?{xray:{running:!generation,binary_exists:true},'sing-box':{running:false,binary_exists:true}}:
        url==='/api/inbounds'||url.endsWith('warnings')?[]:{};
    A.defaults.adapter=config=>{
        assert.equal(config.method,'get','read-only controller');assert([AUTH,SYSTEM,CORE,...unrelated].includes(config.url),config.url);
        const request={url:config.url,timeout:config.timeout,started:now,settled:false,config};requests.push(request);
        if([SYSTEM,CORE].includes(config.url))assert.equal(requests.filter(r=>r.url===config.url&&!r.settled).length,1,'status single flight');
        return new Promise((resolve,reject)=>{
            let timer;
            request.finish=(status=200,data=dataFor(config.url))=>{
                assert(!request.settled,'one settlement');request.settled=true;request.finished=now;request.status=status;
                if(timer)cancel(timer);
                const response={status,data,headers:{},config,statusText:String(status)};
                if(config.validateStatus&&!config.validateStatus(status))reject(new A.AxiosError('Synthetic HTTP '+status,'ERR_BAD_RESPONSE',config,null,response));
                else resolve(response);
            };
            if(config.timeout>0)timer=later(()=>{request.settled=true;request.finished=now;request.timedOut=true;
                reject(new A.AxiosError('Synthetic timeout','ECONNABORTED',config));},config.timeout);
            if(!holds.has(config.url))request.finish(config.url===AUTH?authStatus:200);
        });
    };
    // Production createApp is captured, then its setup/template use real pinned Vue.
    // onMounted is registered with real Vue, with only the returned promise observed.
    const stub={component(){},use(){},mount(){}};
    context.Vue={...V,createApp:o=>{options=o;return stub;},onMounted:cb=>V.onMounted(()=>{
        mounting=cb();mounting.then(()=>{mountedDone=true;});return mounting;
    })};
    vm.runInContext(source,context,{filename:'web/js/app.js'});
    // Observe the factory during setup, retaining the exact production reader
    // objects and their fresh() methods. No controller or helper is rewritten.
    const actualSingleStatusRequest = context.singleStatusRequest;
    context.singleStatusRequest = task => {
        const reader = actualSingleStatusRequest(task); readers.push(reader); return reader;
    };
    const h1=html.indexOf('<div class="header-left">'),h2=html.indexOf('<div class="header-right">');
    const d1=html.indexOf('<div v-if="currentView === \'dashboard\'"'),d2=html.indexOf('<div v-if="currentView === \'inbounds\'"');
    assert(h1>=0&&h2>h1&&d1>h2&&d2>d1);
    const render=V.compile('<section>'+html.slice(h1,h2)+html.slice(d1,d2)+'</section>',{decodeEntities(value) {
        assert.doesNotMatch(value, /&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);/i);
        return value;
    }});
    const host=renderer(V),app=host.createApp({setup(){state=options.setup();return state;},render});
    app.config.errorHandler=e=>renderErrors.push(e);
    for(const name of ['el-tag','el-col','el-row','el-card','el-progress'])app.component(name,{setup(p,{attrs,slots}){
        return()=>V.h(name,attrs,[slots.header?.(),slots.default?.()]);}});
    app.mount(host.host);
    context.singleStatusRequest = actualSingleStatusRequest;
    assert.equal(readers.length, 2);
    const flush=async()=>{for(let i=0;i<100;i++)await Promise.resolve();await V.nextTick();};
    const snapshot=()=>({cpu:state.systemStatus.value.cpu?.percent,xray:state.coreStatus.value.xray?.running,
        systemKnown:state.systemOsKnown.value,coreKnown:state.coreFieldKnown('xray','running'),
        percentages:walk(host.host).filter(n=>n.type==='el-progress').map(n=>n.props.percentage),
        xrayTag:text(walk(host.host).find(n=>n.props.id==='xray-status-tag'))});
    const count=url=>requests.filter(r=>r.url===url).length;
    const pollers=()=>vm.runInContext('statusPollers.length',context);
    const advance=async delta=>{const end=now+delta;let steps=0;await flush();for(;;){
        const next=[...timers].filter(([,t])=>t.due<=end).sort((a,b)=>a[1].due-b[1].due||a[0]-b[0])[0];
        if(!next)break;assert(++steps<1000,'bounded clock');now=next[1].due;timers.delete(next[0]);next[1].fn();await flush();
    }now=end;await flush();};
    const release=(url,status=200)=>{holds.delete(url);const pending=requests.filter(r=>r.url===url&&!r.settled);assert.equal(pending.length,1,url);pending[0].finish(status);};
    const show=async hidden=>{document.hidden=hidden;for(const cb of listeners)cb();await flush();};
    const dispose=async()=>{vm.runInContext('statusPollers.forEach(p=>p.stop())',context);for(const r of requests.filter(r=>!r.settled))r.finish();
        await flush();vm.runInContext('statusPollers.forEach(p=>p.stop())',context);await flush();app.unmount();
        assert.equal(timers.size,0);assert.equal(listeners.size,0);assert(requests.every(r=>r.settled));
        assert.deepEqual(renderErrors,[]);};
    t.after(dispose);
    return {readers,requests,errors,redirects,listeners,timers,flush,advance,release,show,count,pollers,snapshot,
        holds,get done(){return mountedDone;},setGeneration(v){generation=v;},setAuth(v){authStatus=v;},
        invalidate(){channels[0].onmessage({data:'session-ended'});}};
}

const delays = f => [...f.timers.values()].map(timer => timer.delay).sort((a, b) => a - b);
const statusCounts = f => [f.count(SYSTEM), f.count(CORE)];
const assertPollers = f => {
    assert.equal(f.pollers(), 2, 'exactly one poller per status endpoint');
    assert.equal(f.listeners.size, 2);
    assert.deepEqual(delays(f), [3000, 10000]);
};
for (const held of unrelated) test('status polling starts while unrelated GET is pending: ' + held, async t => {
    const f = fixture(t, [held]); await f.flush();
    assert.equal(f.done, false, 'outer initialization still awaits every initial task');
    assert.deepEqual(statusCounts(f), [1, 1]); assertPollers(f);
    assert(f.requests.filter(r => [SYSTEM, CORE].includes(r.url)).every(r => r.settled && r.timeout === 5000));
    assert.equal(f.requests.find(r => r.url === held).timeout, 0, 'no new auxiliary timeout');
    assert.deepEqual(f.snapshot().percentages, [37, 64]); assert.equal(f.snapshot().xrayTag, 'Xray Running');
    f.setGeneration(1); await f.advance(2999); assert.deepEqual(statusCounts(f), [1, 1]);
    await f.advance(1); assert.deepEqual(statusCounts(f), [2, 1]);
    assert.deepEqual(f.snapshot().percentages, [12, 18]); assert.equal(f.snapshot().xrayTag, 'Xray Running');
    await f.advance(7000); assert.deepEqual(statusCounts(f), [4, 2]);
    assert.equal(f.snapshot().xrayTag, 'Xray Stopped'); assert.equal(f.done, false);
    assert.equal(f.requests.filter(r => r.url === held && !r.settled).length, 1);
    assertPollers(f); f.release(held); await f.flush();
    assert.equal(f.done, true); assertPollers(f); assert.deepEqual(statusCounts(f), [4, 2], 'no restart on auxiliary completion');
    await f.advance(10000); assert.deepEqual(statusCounts(f), [7, 3]); assert.deepEqual(f.errors, []);
});

test('authenticated bootstrap preserves actual request start order and existing cadence', async t => {
    const f = fixture(t); await f.flush(); assert.equal(f.done, true);
    assert.deepEqual(f.requests.map(r => r.url), [AUTH, '/api/inbounds', '/api/routing/mihomo/catalog',
        '/api/routing/mihomo', '/api/subscription/mihomo-warnings', SYSTEM, CORE, '/api/routing/mihomo/preview']);
    assertPollers(f); await f.advance(10000); assert.deepEqual(statusCounts(f), [4, 2]);
    assert.deepEqual(f.errors, []);
});

for (const held of [SYSTEM, CORE]) test('both initial status reads must settle before polling: ' + held, async t => {
    const f = fixture(t, [held]); await f.flush();
    assert.equal(f.done, false); assert.equal(f.pollers(), 0); assert.equal(f.listeners.size, 0);
    assert.deepEqual(delays(f), [5000]); await f.advance(4000);
    assert.deepEqual(statusCounts(f), [1, 1]); assert.equal(f.pollers(), 0);
    f.release(held); await f.flush(); assert.equal(f.done, true); assertPollers(f);
    await f.advance(2999); assert.deepEqual(statusCounts(f), [1, 1]);
    await f.advance(1); assert.deepEqual(statusCounts(f), [2, 1]); assert.deepEqual(f.errors, []);
});

test('existing modeled 5000ms status timeout starts polling and later reads recover', async t => {
    const f = fixture(t, [SYSTEM, CORE, '/api/inbounds']); await f.flush();
    await f.advance(4999); assert.equal(f.pollers(), 0); assert.equal(f.done, false);
    await f.advance(1); assertPollers(f); assert.equal(f.done, false);
    assert.equal(f.snapshot().systemKnown, false); assert.equal(f.snapshot().coreKnown, false);
    assert.equal(f.errors.length, 2);
    assert(f.requests.filter(r => [SYSTEM, CORE].includes(r.url)).every(r => r.timedOut && r.timeout === 5000));
    f.holds.delete(SYSTEM); f.holds.delete(CORE); await f.advance(10000);
    assert.deepEqual(statusCounts(f), [4, 2]); assert.equal(f.snapshot().systemKnown, true);
    assert.equal(f.snapshot().coreKnown, true); f.release('/api/inbounds'); await f.flush(); assertPollers(f);
});

test('pending real controller authentication gates every auxiliary and status request', async t => {
    const f = fixture(t, [AUTH]); await f.flush(); await f.advance(60000);
    assert.deepEqual(f.requests.map(r => r.url), [AUTH]); assert.equal(f.done, false);
    assert.equal(f.pollers(), 0); assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0);
    f.release(AUTH); await f.flush(); assert.equal(f.done, true); assertPollers(f); assert.deepEqual(f.errors, []);
});

for (const status of [401, 503]) test('failed authentication and failed current verification never poll: ' + status, async t => {
    const f = fixture(t, [AUTH]); await f.flush(); f.setAuth(status); f.release(AUTH, status); await f.flush();
    assert.equal(f.done, true); assert.equal(f.count(AUTH), 2); assert.deepEqual(statusCounts(f), [0, 0]);
    assert(f.requests.every(r => r.url === AUTH)); assert.equal(f.requests[1].timeout, 5000);
    assert.deepEqual(f.redirects, status === 401 ? ['/login'] : []);
    await f.advance(60000); assert.equal(f.pollers(), 0); assert.equal(f.listeners.size, 0);
    assert.equal(f.timers.size, 0); assert.deepEqual(f.errors, []);
});

test('successful current verification after an initial authentication error still boots once', async t => {
    const f = fixture(t, [AUTH]); await f.flush(); f.release(AUTH, 503); await f.flush();
    assert.equal(f.count(AUTH), 2); assert.equal(f.done, true); assertPollers(f);
    assert.deepEqual(f.redirects, []); assert.deepEqual(statusCounts(f), [1, 1]); assert.deepEqual(f.errors, []);
});

test('session end before initial status settlement cannot install new pollers', async t => {
    const f = fixture(t, [SYSTEM, CORE]); await f.flush(); assert.equal(f.pollers(), 0);
    f.setAuth(401); f.invalidate(); await f.flush(); assert.deepEqual(f.redirects, ['/login']);
    f.release(SYSTEM); f.release(CORE); await f.flush(); assert.equal(f.done, true);
    assert.equal(f.pollers(), 0); assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0);
    await f.advance(60000); assert.deepEqual(statusCounts(f), [1, 1]); assert.deepEqual(f.errors, []);
});

test('session end while auxiliary bootstrap is pending stops polling without resurrection', async t => {
    const f = fixture(t, ['/api/inbounds']); await f.flush(); assertPollers(f);
    f.setAuth(401); f.invalidate(); await f.flush(); assert.deepEqual(f.redirects, ['/login']);
    assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0);
    f.release('/api/inbounds'); await f.flush(); assert.equal(f.done, true); assert.equal(f.pollers(), 2);
    await f.show(true); await f.show(false); await f.advance(60000);
    assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0);
    assert.deepEqual(statusCounts(f), [1, 1]); assert.deepEqual(f.errors, []);
});

test('synthetic hidden pause and visible fresh reads are unchanged with auxiliary pending', async t => {
    const f = fixture(t, ['/api/inbounds']); await f.flush(); await f.advance(10000);
    assert.deepEqual(statusCounts(f), [4, 2]); await f.show(true); assert.equal(f.timers.size, 0);
    await f.advance(60000); assert.deepEqual(statusCounts(f), [4, 2]); f.setGeneration(1);
    await f.show(false); assert.deepEqual(statusCounts(f), [5, 3]); assertPollers(f);
    assert.deepEqual(f.snapshot().percentages, [12, 18]); assert.equal(f.snapshot().xrayTag, 'Xray Stopped');
    f.release('/api/inbounds'); await f.flush(); assertPollers(f); assert.deepEqual(f.errors, []);
});

test('initial synthetic hidden state installs listeners but no periodic timer until shown', async t => {
    const f = fixture(t, ['/api/inbounds'], true); await f.flush();
    assert.equal(f.pollers(), 2); assert.equal(f.listeners.size, 2); assert.equal(f.timers.size, 0);
    await f.advance(60000); assert.deepEqual(statusCounts(f), [1, 1]);
    await f.show(false); assert.deepEqual(statusCounts(f), [2, 2]); assertPollers(f);
    f.release('/api/inbounds'); await f.flush(); assertPollers(f); assert.deepEqual(f.errors, []);
});

test('existing manual fresh callers share one flight and one queued fresh read', async t => {
    const f = fixture(t); await f.flush(); f.holds.add(CORE);
    f.readers[1].fresh(); await f.flush(); assert.equal(f.count(CORE), 2);
    const queued = f.readers[1].fresh();
    assert.equal(f.readers[1].fresh(), queued, 'manual callers share the same queued fresh promise');
    await f.flush();
    assert.equal(f.count(CORE), 2); f.release(CORE); await f.flush();
    assert.equal(f.count(CORE), 3); assert.equal(f.requests.filter(r => !r.settled).length, 0);
    assertPollers(f); assert.deepEqual(f.errors, []);
});

test('synthetic visible resumption during a status flight stays single-flight then refreshes once', async t => {
    const f = fixture(t); await f.flush(); f.holds.add(CORE); await f.advance(10000);
    assert.deepEqual(statusCounts(f), [4, 2]); await f.show(true); await f.show(false); await f.show(true); await f.show(false);
    assert.equal(f.count(CORE), 2); assert.equal(f.requests.filter(r => r.url === CORE && !r.settled).length, 1);
    f.setGeneration(1); f.release(CORE); await f.flush(); assert.equal(f.count(CORE), 3);
    assert.equal(f.snapshot().xrayTag, 'Xray Stopped'); assertPollers(f); assert.deepEqual(f.errors, []);
});
