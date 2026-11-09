'use strict';
const assert=require('node:assert/strict');
const {Flow}=require('../../web/js/operation-flow.js');
const clone=value=>JSON.parse(JSON.stringify(value));
const rev=number=>number.toString(16).padStart(64,'0');
const problem=(status,detail)=>Object.assign(new Error('synthetic API failure'),{status,detail});
function fixture(options={}) {
    let n=1, applied=null, source=1;
    const base={schema_version:1,local_proxy_enabled:false,relay_enabled:false,selected_upstream_id:null,
        local_port:7891,local_username:'',local_password_set:false,dns_policy_version:1,deny_policy_version:1};
    const state={revision:rev(n),settings:{...base,...options.settings},upstreams:[],active:null,pending:null};
    const nodes=clone(options.nodes||[]), cores={xray:{enabled:false,running:false},'sing-box':{enabled:true,running:false,dirty:false,applied_revision:null}};
    const certs=[{id:'a'.repeat(32),domain:'vpn.example.test',environment:'production',revision:rev(100),days_remaining:90,not_after:Date.now()/1000+86400}];
    const editors=new Map(), calls=[], transactions=new Map(), events=[];
    let password=null, loseApply=options.loseApply||false, loseCreate=options.loseCreate||false, loseImport=options.loseImport||false;
    if(options.active){state.active={revision:state.revision,settings:clone(state.settings),core_revision:rev(600+source)};applied=state.revision;cores['sing-box'].applied_revision=state.active.core_revision;cores['sing-box'].running=true;cores['sing-box'].enabled=true;}
    if(options.upstream){state.upstreams.push({id:'up_a',name:'示例出口',server:'exit.example.test',server_port:443});state.settings.selected_upstream_id='up_a';if(state.active)state.active.settings=clone(state.settings);}
    if(options.xray)cores.xray={enabled:true,running:true};
    if(options.stopped){cores['sing-box']={enabled:false,running:false,dirty:false,applied_revision:rev(80)};if(state.active)state.active.core_revision=rev(80);}
    const advance=()=>{state.revision=rev(++n)};
    const api=async(path,method='GET',body=null,{headers={}}={})=>{
        calls.push({path,method,body:body===null?null:clone(body),headers:clone(headers)});
        if(options.onCall)await options.onCall({path,method,body,state,nodes,cores});
        if(path==='/api/proxy/settings'&&method==='GET')return clone(state);
        if(path==='/api/inbounds'&&method==='GET')return clone(nodes);
        if(path==='/api/cores/status')return clone(cores);
        if(path==='/api/certificates')return clone(certs);
        if(path==='/api/proxy/check'){
            assert.equal(body.base_revision,state.revision);return {valid:true,applied:false,phase:'checked',revision:state.revision,source_fingerprint:rev(source),runtime_fingerprint:rev(888),snapshot:clone(state)};
        }
        if(path==='/api/proxy/apply'){
            const key=headers['Idempotency-Key'];assert.match(key,/^[0-9a-f]{64}$/);
            if(!transactions.has(key)){
                assert.equal(body.base_revision,state.revision);assert.equal(body.source_fingerprint,rev(source));
                state.active={revision:state.revision,settings:clone(state.settings),core_revision:rev(600+source)};applied=state.revision;cores['sing-box'].applied_revision=state.active.core_revision;cores['sing-box'].running=true;cores['sing-box'].enabled=true;
                transactions.set(key,{applied:true,phase:'committed',snapshot:clone(state)});
            }
            if(loseApply){loseApply=false;throw new Error('synthetic lost committed reply');}
            return clone(transactions.get(key));
        }
        if(path==='/api/proxy/import/preview')return {candidates:[{index:0,name:'示例出口',supported:true}],ignored_sections:[],source_fingerprint:rev(900)};
        if(path==='/api/proxy/profiles'){
            assert.equal(headers['If-Match'],'"'+state.revision+'"');state.upstreams.push({id:'up_'+n,name:'新出口',source_fingerprint:rev(900),source_format:'uri'});advance();
            if(loseImport){loseImport=false;throw new Error('synthetic lost import reply');}
            return {snapshot:clone(state),upstream_id:state.upstreams.at(-1).id};
        }
        if(path==='/api/proxy/settings'&&method==='PUT'){
            assert.equal(headers['If-Match'],'"'+state.revision+'"');
            const values=clone(body);delete values.local_password;
            if(body.local_password!=null)password=body.local_password;
            state.settings={...values,local_password_set:password!==null||state.settings.local_password_set};advance();return clone(state);
        }
        if(path==='/api/inbounds'&&method==='POST'){
            assert.ok(state.active,'Direct creation must prepare the first explicit OFF baseline');assert.equal(body.expected_proxy_revision,state.active.revision);
            assert.ok(!('settings'in body)&&!('stream_settings'in body)&&!('user_id'in body));
            const row={id:Math.max(0,...nodes.map(r=>r.id))+1,core:'sing-box',protocol:'vless',port:body.port,enable:true,
                routing_mode:body.routing_mode,remark:body.remark,tag:body.tag,expiry_time:0,mutation_revision:rev(1000+nodes.length),
                content_revision:rev(2000+nodes.length),share_eligible:true,upstream_export_eligible:true,routing_status:{state:'applied'}};
            nodes.push(row);editors.set(row.id,{mutation_revision:row.mutation_revision,content_revision:row.content_revision,certificate_id:body.certificate_id,profile:{security:'tls',server_name:body.profile?.server_name||'vpn.example.test'}});
            if(!options.stopped)cores['sing-box'].running=true;
            source++;state.active.core_revision=rev(600+source);cores['sing-box'].applied_revision=state.active.core_revision;
            if(loseCreate){loseCreate=false;throw new Error('synthetic lost create reply');}
            return {inbound:clone(row)};
        }
        if(/^\/api\/inbounds\/\d+\/editor$/.test(path))return clone(editors.get(Number(path.split('/')[3])));
        if(path==='/api/cores/sing-box/restart'){cores['sing-box'].running=true;cores['sing-box'].enabled=true;return {};}
        if(path.startsWith('/api/subscription/link/'))return 'vless://fake-owned-secret';
        if(path==='/api/proxy/local-connection'){assert.equal(body.base_revision,applied);return {host:'127.0.0.1',port:state.settings.local_port,username:state.settings.local_username,password:password||'old-private-secret',revision:applied};}
        if(path==='/api/proxy/discard-draft'){assert.equal(body.active_revision,state.active.revision);state.revision=state.active.revision;state.settings=clone(state.active.settings);return clone(state);}
        throw new Error('Unexpected request '+method+' '+path);
    };
    const flow=new Flow(api,{scopes:options.scopes??null,changed:event=>events.push(clone(event))});
    const choice=()=>({revision:state.revision,name:'我的节点',port:10443,certificateId:certs[0].id,upstreamId:state.settings.selected_upstream_id});
    return {flow,state,nodes,cores,certs,calls,events,choice,advance,editors,transactions,api};
}

module.exports={fixture,rev,clone,problem};
