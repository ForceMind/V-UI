'use strict';
// One DOM view for the administrator and delegated maintenance transports.
// All untrusted names/links use textContent/value; secrets live only in this view.
(() => {
    const {Flow, message, suggestedPort} = globalThis.VUI_OPERATION_FLOW;
    let sequence = 0;
    const views = new Map();
    const labels = {direct:'创建直连节点',relay:'创建中转节点',host:'服务器程序代理',exit:'设置出口服务器',edit:'修改节点设置'};
    const scopeNames = {nodes:'节点管理',relay:'出口配置',certificates:'证书管理',share:'分享连接',destructive:'删除与恢复'};
    const element = (tag, text = null, attrs = {}) => {
        const node = document.createElement(tag);
        if (text !== null) node.textContent = text;
        for (const [key,value] of Object.entries(attrs)) {
            if (key === 'class') node.className = value;
            else if (key in node) node[key] = value;
            else node.setAttribute(key, String(value));
        }
        node._opsDisabled=!!attrs.disabled;return node;
    };
    async function cookieRequest(path, method='GET', body=null, {headers={},fullResponse=false}={}) {
        if (!path.startsWith('/api/') || path.includes('://')) throw new Error('Invalid API destination');
        const allowed = {'X-VUI-Request':'1'};
        for (const key of ['If-Match','Idempotency-Key']) if (headers[key]) allowed[key]=headers[key];
        if (body !== null) allowed['Content-Type']='application/json';
        const controller = new AbortController(), timeout=setTimeout(()=>controller.abort(),20000);
        try {
            const response=await fetch(path,{method,credentials:'same-origin',cache:'no-store',redirect:'error',headers:allowed,
                signal:controller.signal,body:body===null?undefined:JSON.stringify(body)});
            const type=response.headers.get('content-type')||'';
            let value;
            try { value=type.includes('json')?await response.json():await response.text(); }
            catch (_) { throw Object.assign(new Error('Response not confirmed'),{status:response.status}); }
            if (!response.ok) throw Object.assign(new Error('API operation refused'),{status:response.status,detail:value?.detail});
            return fullResponse?{value,nodeRevision:response.headers.get('X-VUI-Node-Revision')}:value;
        } finally { clearTimeout(timeout); }
    }
    class View {
        constructor(root, options={}) {
            this.root=root;this.options=options;this.id='ops-'+(++sequence);this.modalId='simple-operations';this.epoch=0;this.ended=false;
            this.task='direct';this.uiBusy=false;this.fields={};this.secret='';this.info=null;this.dirty=false;this.deliveryEpoch=0;this.savedForm=null;this.certificateTimer=null;this.certificateSubmitted=false;
            this.flow=new Flow(options.request||cookieRequest,{scopes:options.scopes??null,
                changed:event=>this.changed(event),expired:()=>this.expire()});
            this.host=root;
            if (options.dialog) {
                this.dialog=element('dialog',null,{class:'ops-dialog','aria-label':'简单操作'});
                this.root.append(this.dialog);this.host=this.dialog;
                this.dialog.addEventListener('cancel',event=>{event.preventDefault();this.close();});
                this.dialog.addEventListener('close',()=>this.clear());
            }
            this.build();
            this.guard=globalThis.VUI_NAV?.guard(()=>this.canLeave());
            this.pagehide=()=>this.destroy();addEventListener('pagehide',this.pagehide);
            if (!options.dialog) void this.refresh();
        }
        button(label, action, options={}) {
            const button=element('button',label,{type:'button',class:options.primary?'ops-primary':'',...options});
            button._opsDisabled=!!options.disabled;button.addEventListener('click',action);return button;
        }
        field(parent,label,input,hint='') {
            const row=element('div',null,{class:'ops-field'}), id=this.id+'-field-'+Object.keys(this.fields).length+'-'+(++sequence);
            input.id=id;row.append(element('label',label,{htmlFor:id}),input);
            if(hint)row.append(element('small',hint));parent.append(row);return input;
        }
        note(text, kind='') { return element('div',text,{class:'ops-note '+kind,role:kind==='ops-error'?'alert':'status'}); }
        build() {
            this.host.classList.add('ops-view');
            const head=element('div',null,{class:'ops-head'});head.append(element('h2','选择要做的事'));
            head.append(this.button(this.options.dialog?'关闭':'重新读取',()=>this.options.dialog?this.close():this.refresh()));
            this.tabs=element('div',null,{class:'ops-tabs','aria-label':'常用操作'});
            for(const task of ['direct','relay','host'])this.tabs.append(this.button(labels[task],()=>this.open(task),{'data-task':task}));
            this.tabs.append(this.button('订阅分享',()=>this.subscriptions(),{disabled:!this.flow.has('share')}),this.button('证书与域名',()=>this.certificates()));
            this.status=element('p','正在读取服务器实际状态…',{class:'ops-status',role:'status','aria-live':'polite'});
            this.problem=element('div',null,{class:'ops-problem','aria-live':'polite'});
            this.content=element('div');this.list=element('div',null,{class:'ops-nodes'});
            this.host.append(head,this.tabs,this.status,this.problem,this.content,this.list);
        }
        changed(event) {
            if(this.ended)return;
            if(event.stage)this.status.textContent=event.stage;
            if(event.imported&&this.flow.intent){
                if(this.fields.link)this.fields.link.value='';
                if(this.fields.upstream)this.fillUpstreams(this.flow.intent.choice.upstreamId);
            }
            if(event.nodes||event.loaded){
                if(this.form?.parentNode===this.content){
                    if(this.fields.impact?.checked&&this.impactConsent!==this.flow.impactStamp()){this.fields.impact.checked=false;this.impactConsent=null;}
                    if(this.fields.start?.checked&&this.startConsent!==this.flow.stoppedStamp()){this.fields.start.checked=false;this.startConsent=null;}
                    this.updateForm();
                }
            }
            if(event.nodes){this.renderNodes();globalThis.dispatchEvent(new CustomEvent('vui:operations-changed'));}
            this.lock();
        }
        lock() {
            const pending=this.flow.attempt?.state==='uncertain'||this.flow.intent?.importUncertain||this.flow.intent?.saveUncertain||
                this.flow.intent?.nodeSent&&this.problem.childNodes.length>0;
            for(const node of this.host.querySelectorAll('button,input,select,textarea'))
                node.disabled=!!(this.uiBusy||this.flow.busy||node._opsDisabled||(pending&&node.closest('form')));
        }
        clear() {
            clearTimeout(this.certificateTimer);this.certificateTimer=null;this.certificateSubmitted=false;this.savedForm=null;this.certificateJob=null;
            this.epoch++;this.flow.destroy();this.secret='';this.info=null;this.dirty=false;
            this.fields={};this.form=null;this.editor=null;this.editing=null;this.leaveConfirmed=false;
            this.content.replaceChildren();this.problem.replaceChildren();
        }
        destroy() {
            if(this.ended)return;this.ended=true;this.clear();this.host.replaceChildren();
            this.guard?.();removeEventListener('pagehide',this.pagehide);
        }
        expire() {
            this.destroy();this.options.expired?.();
            if(!this.options.remote)globalThis.VUI_NAV?.replaceLogin(location.pathname,location.hash);
        }
        canLeave() {
            if(this.uiBusy||this.flow.busy)return false;
            if(this.leaveConfirmed)return true;
            if(this.flow.attempt?.state==='uncertain'||this.flow.intent?.nodeSent&&!this.info){
                if(!confirm('结果尚未确认。离开不会撤销服务器操作；再次操作前请先查看现有节点。继续离开？'))return false;
            }else if(this.dirty&&!this.info&&!confirm('放弃本次尚未提交的输入？'))return false;
            this.leaveConfirmed=true;return true;
        }
        close() {
            if(!this.canLeave())return;
            this.returnEdit=null;this.clear();if(this.dialog){this.dialog.close();globalThis.VUI_NAV?.modal.close(this.modalId);}
        }
        async perform(action, success=null) {
            if(this.uiBusy||this.ended)return;const epoch=this.epoch;
            this.uiBusy=true;this.problem.replaceChildren();this.lock();
            try {
                const result=await action();if(epoch!==this.epoch||this.ended)return;
                if(success)await success(result);
            }catch(error){if(epoch===this.epoch&&!this.ended)this.showError(error);}
            finally{if(epoch===this.epoch&&!this.ended){this.uiBusy=false;this.lock();}}
        }
        rememberForm() {
            if(!this.form||this.info)return null;
            const values={};for(const [key,input]of Object.entries(this.fields))
                if(['name','port','cert','upstream','link','format','route','localPort'].includes(key))values[key]=input.value;
            return {task:this.task,values,dirty:this.dirty};
        }
        restoreForm(saved) {
            if(!saved||saved.task!==this.task)return;
            for(const [key,value]of Object.entries(saved.values)){const input=this.fields[key];if(!input)continue;
                if(input.tagName.toLowerCase()==='select'&&!Array.from(input.children).some(option=>option.value===value))continue;input.value=value;}
            if(this.fields.paste)this.fields.paste.hidden=this.fields.upstream.value!=='new';
            this.dirty=saved.dirty;this.updateForm();
        }
        async refresh() {
            const saved=this.rememberForm();
            return await this.perform(async()=>{await this.flow.load();this.status.textContent='已读取服务器当前设置。';},()=>{this.renderForm();this.restoreForm(saved);this.renderNodes();});
        }
        async open(task='direct', target=null) {
            if(this.uiBusy||this.flow.busy||this.ended)return;
            if(this.dirty&&!this.info&&!confirm('切换操作会清除本次未提交的连接内容，继续？'))return;
            if(this.flow.attempt?.state==='uncertain'||this.flow.intent?.nodeSent&&!this.info){this.showError({code:'PENDING',message:'请先核对上一操作的结果。'});return;}
            this.clear();this.task=task;this.target=target;
            if(this.dialog&&!this.dialog.open){this.dialog.showModal();globalThis.VUI_NAV?.modal.open(this.modalId,()=>this.close(),()=>this.canLeave());}
            return await this.perform(async()=>{
                await this.flow.load();
                if(task==='edit'){
                    this.editing=await this.flow.exactNode(target.id,target.revision);
                    this.editor=await this.flow.api('/api/inbounds/'+target.id+'/editor');
                    if(this.editor.mutation_revision&&this.editor.mutation_revision!==target.revision)throw new Error('Node changed');
                }
            },()=>{if(task==='share'){this.info={kind:'share'};this.content.replaceChildren();const row=this.flow.nodes.find(value=>value.id===target.id&&value.mutation_revision===target.revision);if(!row||!globalThis.VUI_OPERATION_FLOW.REV.test(target.contentRevision||'')||row.content_revision!==target.contentRevision)throw new globalThis.VUI_OPERATION_FLOW.FlowError('NODE_CHANGED','节点已变化，请重新选择。');void this.delivery(row,target.server||location.hostname,!!target.upstream);}else this.renderForm();this.renderNodes();});
        }
        input(value='',type='text') { return element('input',null,{type,value:value??'',autocomplete:'off'}); }
        fillUpstreams(selected) {
            const select=this.fields.upstream;select.replaceChildren();
            select.append(element('option','请选择出口服务器',{value:''}));
            for(const row of this.flow.snapshot.upstreams)select.append(element('option',row.name+' · '+row.server+':'+row.server_port,{value:row.id}));
            select.append(element('option','粘贴另一台服务器的TCP出口链接',{value:'new'}));
            select.value=selected||(!this.flow.snapshot.upstreams.length?'new':'');
            this.fields.paste.hidden=select.value!=='new';
        }
        renderForm() {
            this.secret='';this.info=null;this.fields={};this.impactConsent=null;this.startConsent=null;this.content.replaceChildren();
            const snapshot=this.flow.snapshot;if(!snapshot)return;
            this.revision=snapshot.revision;this.dirty=false;
            for(const button of this.tabs.children)button.setAttribute('aria-current',String(button.dataset.task===this.task));
            const title=element('h3',labels[this.task]||'节点连接');
            const diagram=this.task==='host'?'服务器上的程序 → 出口服务器 → 网站':this.task==='relay'?'手机 / 电脑 → 这台服务器 → 出口服务器 → 网站':this.task==='exit'?'为中转节点和服务器程序选择统一出口':'手机 / 电脑 → 这台服务器 → 网站';
            this.content.append(title,element('p',diagram,{class:'ops-route'}));
            const form=element('form');this.form=form;
            form.addEventListener('input',()=>{this.leaveConfirmed=false;this.dirty=true;this.updateForm();});
            form.addEventListener('change',()=>{this.leaveConfirmed=false;this.dirty=true;this.updateForm();});
            const nodeTask=['direct','relay'].includes(this.task), edit=this.task==='edit';
            if(nodeTask||edit){
                const nextName=nodeTask?(this.task==='relay'?'中转节点 ':'直连节点 ')+(this.flow.nodes.length+1):this.editing.remark||'';
                this.fields.name=this.field(form,'节点名称（可以直接使用默认名称）',this.input(nextName));
            }
            if(['relay','host','exit'].includes(this.task)){
                this.fields.upstream=this.field(form,'出口服务器',element('select'));
                this.fields.paste=element('div');this.fields.link=this.field(this.fields.paste,'粘贴对端的TCP出口链接',element('textarea',null,{rows:3,spellcheck:false}),
                    '在对端节点选择“给中转服务器使用（仅TCP）”。这里不会读取远程订阅。');
                const advanced=element('details');advanced.append(element('summary','其他格式与多节点配置（高级）'));
                this.fields.format=this.field(advanced,'输入格式',element('select'));
                for(const [value,label]of[['uri','连接链接（推荐）'],['clash-yaml','Clash / Mihomo YAML'],['v2ray-json','V2Ray / Xray JSON']])this.fields.format.append(element('option',label,{value}));
                this.fields.format.addEventListener('change',()=>{this.problem.replaceChildren();this.candidateIndex=null;this.ignored=[];});
                this.fields.paste.append(advanced);form.append(this.fields.paste);
                this.fillUpstreams(snapshot.settings.selected_upstream_id);
                this.fields.upstream.addEventListener('change',()=>{this.fields.paste.hidden=this.fields.upstream.value!=='new';this.fields.link.value='';this.preview=null;this.updateForm();});
                this.fields.link.addEventListener('input',()=>{this.problem.replaceChildren();this.preview=null;this.candidateIndex=null;this.ignored=[];if(this.fields.impact)this.fields.impact.checked=false;});
                this.fields.impactBox=element('div');this.fields.impact=element('input',null,{type:'checkbox'});
                const confirmation=element('label',null,{class:'ops-check'});confirmation.append(this.fields.impact,element('span','我确认把下列对象统一切换到所选出口。'));
                this.fields.impact.addEventListener('change',()=>{this.impactConsent=this.fields.impact.checked?this.displayedImpactStamp:null;});
                this.fields.impactList=element('div');this.fields.impactBox.append(this.fields.impactList,confirmation);form.append(this.fields.impactBox);
                form.append(element('small','当前中转节点和服务器程序代理共用一条出口。仅添加节点不会擅自切换其他出口。'));
            }
            if(nodeTask){
                const certs=this.flow.certificates;
                if(certs.length===1){this.certificateId=certs[0].id;form.append(this.note('连接域名：'+certs[0].domain+'（已自动采用已有证书）'));}
                else if(certs.length>1){
                    this.fields.cert=this.field(form,'要使用哪个域名？',element('select'));this.fields.cert.append(element('option','请选择连接域名',{value:''}));
                    for(const cert of certs)this.fields.cert.append(element('option',cert.domain,{value:cert.id}));this.certificateId=null;
                }else{
                    this.certificateId=null;form.append(this.note('还没有可用的正式证书。先设置域名和证书，完成后再继续；本次输入保留。'));
                    form.append(this.button('设置域名和证书',()=>this.certificateSetup()));
                }
            }
            if(edit){
                form.append(this.note('只修改这里明确选择的字段。原核心、访问凭据、证书路径、到期和高级配置保留。'));
                if(this.editor.profile?.security==='tls'){
                    this.fields.cert=this.field(form,'使用证书（默认保持）',element('select'));
                    this.fields.cert.append(element('option','保持原证书设置',{value:''}));
                    for(const c of this.flow.certificates)this.fields.cert.append(element('option',c.domain,{value:c.id}));
                }
                this.fields.route=this.field(form,'节点出口（默认保持）',element('select'));
                this.fields.route.append(element('option','保持当前出口设置',{value:''}),element('option','本机直接出口',{value:'direct'}));
                if(this.editing.core==='sing-box'&&this.editing.upstream_export_eligible)this.fields.route.append(element('option','通过当前统一出口',{value:'relay'}));
                if(this.flow.has('relay'))form.append(this.button('添加或更换统一出口',()=>{this.returnEdit={target:this.target,saved:this.rememberForm()};void this.open('exit');}));
                if(!this.flow.has('relay')){this.fields.route._opsDisabled=true;form.append(element('small','更改节点出口需要出口配置权限。'));}
            }
            if(this.task==='host')form.append(this.note('本机指这台服务器。仅监听127.0.0.1，供明确配置代理的服务器程序使用，不接管Linux全部流量。首次自动生成独立账号和强密码；已有凭据保留。'));
            const advanced=element('details');advanced.append(element('summary','更多设置'));
            if(nodeTask||edit)this.fields.port=this.field(advanced,'连接端口',this.input(edit?this.editing.port:suggestedPort(this.flow.nodes),'number'),'自动建议避开已有节点；系统占用和公网放行仍需实际核对。');
            if(this.task==='host')this.fields.localPort=this.field(advanced,'服务器本地代理端口',this.input(snapshot.settings.local_port,'number'),'只在服务器127.0.0.1提供入口，不公开监听。');
            if(nodeTask)advanced.append(element('p','推荐配置：sing-box / VLESS / TCP / TLS，连接凭据由服务器生成。其他协议可使用高级新建。'));
            if(this.options.advanced)advanced.append(this.button(edit?'完整高级编辑':'其他协议 / 高级新建',()=>this.options.advanced(edit?this.editing:null)));
            form.append(advanced);
            this.fields.startBox=element('div');this.fields.start=element('input',null,{type:'checkbox'});
            const restore=element('label',null,{class:'ops-check'});restore.append(this.fields.start,element('span','我确认启动该节点组，并恢复下面已启用的节点。'));
            this.fields.start.addEventListener('change',()=>{this.startConsent=this.fields.start.checked?this.displayedStartStamp:null;});
            this.fields.startList=element('div');this.fields.startBox.append(this.fields.startList,restore);form.append(this.fields.startBox);
            this.formWarning=element('div');form.append(this.formWarning);
            const actions=element('div',null,{class:'ops-actions'});
            const name=this.task==='host'?'开启并获取使用方法':this.task==='exit'?'使用这个出口':edit?'保存节点设置':this.flow.has('share')?'创建并获取连接':'创建节点';
            this.submit=element('button',name,{type:'submit',class:'ops-primary'});actions.append(this.submit);
            if(this.task==='host'&&snapshot.active?.settings?.local_proxy_enabled)actions.append(this.button('关闭服务器程序代理',()=>{
                if(confirm('关闭后，正在使用这个本地入口的服务器程序将无法继续通过它访问。节点出口不改变。确认关闭？'))void this.perform(()=>this.flow.disableHost(),r=>this.renderResult(r));
            }));
            form.append(actions);
            form.addEventListener('submit',event=>{event.preventDefault();const choice=this.choice();void this.perform(async()=>{
                if(edit){
                    const changes={};if(choice.name!==this.editing.remark)changes.remark=choice.name;
                    if(choice.port!==this.editing.port){if(!Number.isInteger(choice.port)||choice.port<1024||choice.port>65535||this.flow.nodes.some(row=>row.id!==this.editing.id&&row.port===choice.port))throw new globalThis.VUI_OPERATION_FLOW.FlowError('PORT_INVALID','请使用未被节点占用的1024–65535整数端口。');changes.port=choice.port;}
                    if(this.fields.cert?.value)changes.certificate_id=this.fields.cert.value;
                    if(this.fields.route.value){
                        await this.flow.load();this.flow.clean();if(this.flow.snapshot.revision!==this.revision)throw new globalThis.VUI_OPERATION_FLOW.FlowError('CHANGED','出口设置已变化，请重新确认。');
                        if(this.fields.route.value==='relay'){
                            this.flow.relayAllowed();if(!this.flow.snapshot.active?.settings.selected_upstream_id)throw new globalThis.VUI_OPERATION_FLOW.FlowError('CHOOSE_UPSTREAM','先添加并启用统一出口，再更改这个节点。');
                        }
                        changes.routing_mode=this.fields.route.value;
                    }
                    if(!Object.keys(changes).length)return {kind:'unchanged'};
                    await this.flow.edit(this.editing.id,this.target.revision,changes,'routing_mode'in changes?this.revision:null,this.editor.content_revision);return {kind:'edited'};
                }
                return await this.flow.run(this.task,choice);
            },result=>this.renderResult(result));});
            this.content.append(form,element('small','连接信息含访问凭据，仅在你明确创建并获取或点击分享时显示。服务启动与公网连接验证分别标注。'));
            this.blockedByDraft=false;try{this.flow.clean();}catch(error){this.blockedByDraft=true;this.showError(error);}
            this.updateForm();
        }
        choice() {
            return {revision:this.revision,name:this.fields.name?.value||'',port:Number(this.fields.port?.value),
                certificateId:this.fields.cert?.value||this.certificateId,
                upstreamId:this.fields.upstream?.value==='new'?null:this.fields.upstream?.value,
                link:this.fields.upstream?.value==='new'?this.fields.link.value:'',format:this.fields.format?.value||'uri',
                candidateIndex:this.candidateIndex,ignored:this.ignored||[],
                impactStamp:this.fields.impact?.checked?this.impactConsent:null,
                startStopped:this.fields.start?.checked||false,startStamp:this.fields.start?.checked?this.startConsent:null,
                localPort:this.fields.localPort?Number(this.fields.localPort.value):null};
        }
        updateForm() {
            if(!this.submit)return;const c=this.choice();this.formWarning.replaceChildren();
            this.displayedImpactStamp=this.flow.impactStamp();this.displayedStartStamp=this.flow.stoppedStamp();
            const required=this.task==='edit'?['nodes']:['relay',...(['direct','relay'].includes(this.task)?['nodes','certificates']:[])];
            const missing=required.filter(scope=>!this.flow.has(scope));
            let reason=this.blockedByDraft?'请先处理上次尚未完成的设置。':missing.length?'当前授权缺少：'+missing.map(s=>scopeNames[s]).join('、'):'';
            if(!reason&&['direct','relay'].includes(this.task)&&!c.certificateId)reason='先选择可用的正式证书域名。';
            if(!reason&&this.task==='relay'&&(this.flow.cores.xray?.enabled||this.flow.cores.xray?.running))reason='现有Xray节点组启用或运行中，本版本不能同时开启中转。旧节点不会自动停止。';
            if(!reason&&['host','relay','exit'].includes(this.task)&&!c.upstreamId&&!c.link)reason='选择出口，或粘贴对端的TCP出口链接。';
            if(this.flow.snapshot.pending)reason='有未结束的操作，请先核对结果。';
            const impact=['host','relay','exit'].includes(this.task)&&this.flow.needsImpact(c);
            if(this.fields.impactBox){
                this.fields.impactBox.hidden=!impact;this.fields.impactList.replaceChildren();
                if(impact){
                    this.fields.impactList.append(this.note('更换统一出口将影响：'+[...this.flow.affected().map(n=>n.remark||'节点'+n.id),...(this.flow.snapshot.settings.local_proxy_enabled?['服务器程序代理']:[])].join('、')));
                    if(!reason&&!c.impactStamp)reason='核对将受到影响的对象后，再确认切换出口。';
                }
            }
            const core=this.flow.cores['sing-box'];const stopped=this.task!=='edit'&&core&&!core.running&&!core.enabled&&core.applied_revision&&this.flow.nodes.some(n=>n.core==='sing-box'&&n.enable);
            this.fields.startBox.hidden=!stopped;
            if(stopped){this.fields.startList.textContent='节点组被手动停止。将恢复：'+this.flow.nodes.filter(n=>n.core==='sing-box'&&n.enable).map(n=>n.remark||'节点'+n.id).join('、');if(!reason&&!c.startStopped)reason='是否恢复这些已启用节点，需要你明确确认。';}
            if(reason)this.formWarning.append(this.note(reason));this.submit._opsDisabled=!!reason;this.lock();
        }
        showError(error) {
            this.problem.replaceChildren(this.note(message(error),'ops-error'));
            if(['NODE_UNCERTAIN','NODE_SAVED','APPLY_UNCERTAIN','IMPORT_UNCERTAIN','SETTINGS_UNCERTAIN'].includes(error.code)||this.flow.intent?.importUncertain||this.flow.intent?.saveUncertain)
                this.problem.append(this.button('核对并完成',()=>this.perform(()=>this.flow.resolve(),r=>this.renderResult(r)),{primary:true}));
            if(error.code==='BIND_CONFIRM_REQUIRED')this.problem.append(this.button('确认使用所选证书并完成绑定',()=>this.perform(()=>this.flow.confirmBinding(),r=>this.renderResult(r)),{primary:true}));
            if(['CHOOSE_CANDIDATE','ACK_SECTIONS'].includes(error.code)&&error.data){
                const box=element('div');this.problem.append(box);
                for(const candidate of error.data.candidates.filter(c=>c.supported)){
                    const input=element('input',null,{type:'radio',name:this.id+'-candidate',value:candidate.index,checked:this.candidateIndex===candidate.index});
                    const label=element('label',null,{class:'ops-check'});label.append(input,element('span',candidate.name+' · '+candidate.server+':'+candidate.server_port));box.append(label);
                    input.addEventListener('change',()=>{this.candidateIndex=candidate.index;});
                }
                for(const name of error.data.ignored_sections){
                    const input=element('input',null,{type:'checkbox',checked:(this.ignored||[]).includes(name)}),label=element('label',null,{class:'ops-check'});
                    label.append(input,element('span','确认不导入 '+name));box.append(label);
                    input.addEventListener('change',()=>{this.ignored=Array.from(box.querySelectorAll('input[type=checkbox]:checked'),n=>n._section);});input._section=name;
                }
                box.append(element('p','选择后再次点击原操作按钮；尚未保存或连接。'));
            }
            if(['UNKNOWN_DRAFT','PENDING'].includes(error.code))this.draftReview();
            this.lock();
        }
        draftReview() {
            const s=this.flow.snapshot;if(!s)return;
            this.problem.append(element('p','待处理设置：'+(s.settings.local_proxy_enabled?'服务器程序代理开启':'服务器程序代理关闭')+'；'+(s.settings.relay_enabled?'跟随全局节点中转':'跟随全局节点直连')+'；出口 '+(s.upstreams.find(u=>u.id===s.settings.selected_upstream_id)?.name||'未选择')));
            if(s.pending){this.problem.append(this.button('重新读取操作状态',()=>this.refresh()));const link=element('a','查看维护任务',{href:this.options.remote?'#maintenance-tasks-panel':'/maintenance'});this.problem.append(link);return;}
            this.problem.append(this.button('继续处理这份设置',()=>{
                if(confirm('将检查并启用这份已保存设置；可能影响使用统一出口的节点和服务器程序。确认继续？'))void this.perform(()=>this.flow.applyDraft(),()=>{this.dirty=false;this.status.textContent='已处理上次设置，请重新选择本次操作。';setTimeout(()=>{if(!this.ended)void this.refresh();},0);});
            }));
            if(s.active)this.problem.append(this.button('放弃未应用更改',()=>{
                if(confirm('将草稿恢复为已提交的设置，包含对应访问密码。当前服务不重启，历史记录保留。确认放弃未应用更改？'))void this.perform(()=>this.flow.discardDraft(),()=>{this.dirty=false;const saved=this.rememberForm();this.renderForm();this.restoreForm(saved);});
            }));
        }
        renderResult(result) {
            this.info=result;this.dirty=false;this.problem.replaceChildren();this.content.replaceChildren();
            if(result.kind==='node'){
                this.status.textContent='节点已创建并启动；外部连接待客户端验证。';
                this.content.append(element('h3',result.node.remark||'节点已创建'),this.note('端口 '+result.node.port+' · '+(result.node.routing_mode==='relay'?'通过统一出口':'本机直接出口')));
                if(this.flow.has('share'))void this.delivery(result.node,result.server,false);
                else this.content.append(this.note('节点已创建；当前维护授权没有分享权限。'));
            }else if(result.kind==='host'){
                this.status.textContent='服务器程序代理已开启。';this.content.append(this.note('供服务器上明确使用HTTP/SOCKS代理的程序接入；不接管系统全部流量。'));
                this.content.append(this.button('显示连接信息与使用方法（含访问密码）',()=>this.perform(()=>this.flow.localConnection(),info=>this.localInfo(info)),{disabled:!this.flow.has('relay','share')}));
                if(!this.flow.has('relay','share'))this.content.append(element('p','查看凭据需要出口配置和分享权限。'));
            }else{this.status.textContent={edited:'节点设置已保存，请以列表实际运行状态为准。',unchanged:'没有修改字段，原配置保持。','host-off':'服务器程序代理已关闭，节点出口和保存的凭据保持。',exit:'统一出口已设置，其他开关保持。',settings:'设置结果已核对。'}[result.kind]||'操作已完成。';}
            if(result.kind==='exit'&&this.returnEdit){const previous=this.returnEdit;this.returnEdit=null;this.content.append(this.button('返回刚才的节点设置',async()=>{await this.open('edit',previous.target);this.restoreForm(previous.saved);},{primary:true}));}
            this.content.append(this.button('返回操作列表',()=>this.open('direct')));this.renderNodes();
        }
        async delivery(row, server, upstream) {
            const epoch=this.epoch, deliveryEpoch=++this.deliveryEpoch;this.secret='';this.content.querySelector('.ops-delivery')?.remove();this.status.textContent='正在读取所选连接信息…';
            try {
                const value=await this.flow.share(row.id,row.mutation_revision,server,upstream,'uri',row.content_revision);
                if(epoch!==this.epoch||deliveryEpoch!==this.deliveryEpoch||this.ended)return;
                this.secret=value;const box=element('section',null,{class:'ops-delivery'});
                box.append(element('h3',upstream?'给中转服务器使用（仅TCP）':'连接信息'),element('p','链接含访问凭据，只交给可信使用者。'));
                const text=element('textarea',null,{readOnly:true,rows:4,value});box.append(text);
                box.append(this.button(upstream?'复制TCP出口链接':'复制连接链接',()=>this.copy(value,text)));
                if(!upstream){
                    const qr=element('div',null,{class:'ops-qr'});
                    box.append(this.button('显示二维码',()=>{qr.replaceChildren();if(globalThis.QRCode)new QRCode(qr,{text:value,width:196,height:196});else qr.textContent='二维码组件未加载，可先复制连接链接。';}),qr);
                    if(row.upstream_export_eligible===true)box.append(this.button('给中转服务器使用（仅TCP，含凭据）',()=>this.delivery(row,server,true)));
                }
                this.content.querySelector('.ops-delivery')?.remove();this.content.append(box);
            }catch(error){if(epoch===this.epoch&&deliveryEpoch===this.deliveryEpoch&&!this.ended)this.showError(error);}
        }
        copy(value, field) {
            const epoch=this.epoch;
            Promise.resolve().then(()=>navigator.clipboard.writeText(value)).then(()=>{if(epoch===this.epoch)this.status.textContent='已复制，请妥善保存。';},()=>{
                if(epoch!==this.epoch)return;field.focus();field.select();this.status.textContent='自动复制未成功，已选中文本，请手动复制。';
            });
        }
        localInfo(info) {
            this.secret='';this.content.replaceChildren(element('h3','服务器上的程序如何使用'));
            this.content.append(this.note('127.0.0.1是服务器本地地址，不是你电脑的地址。下面的地址用于程序设置，只有测试命令需要在终端执行。'));
            const box=element('section');
            const values=[['代理地址','http://'+info.host+':'+info.port,'复制代理地址'],['用户名',info.username,'复制代理用户名'],['访问密码',info.password,'复制访问密码']];
            for(const [label,value,caption]of values){
                const input=this.field(box,label,element('input',null,{type:label==='访问密码'?'password':'text',readOnly:true,value,autocomplete:'off'}));
                box.append(this.button(caption,()=>this.copy(value,input)));
                if(label==='访问密码')box.append(this.button('显示 / 隐藏访问密码',()=>{input.type=input.type==='password'?'text':'password';}));
            }
            this.secret=info.password;this.content.append(box);
            this.content.append(element('h3','用curl测试一次'),element('p','复制下面命令，在服务器终端执行。出现代理密码提示后，粘贴上面的访问密码；输入时不显示字符。'));
            const command=element('textarea',null,{readOnly:true,rows:2,value:'curl --proxy http://'+info.host+':'+info.port+' --proxy-user='+info.username+' https://example.com/'});
            this.content.append(command,this.button('复制curl测试命令（不含密码）',()=>this.copy(command.value,command)),element('small','面板不会执行命令或修改全局环境。其他程序请在其HTTP/SOCKS代理设置中填写以上信息。'));
        }
        renderNodes() {
            if(this.options.dialog)return;this.list.replaceChildren();
            this.list.append(element('h3','已有节点'),element('small','服务启动与公网可达分别核验。复制到客户端后测试连接；旧节点不会自动转换核心或路由。'));
            for(const row of this.flow.nodes){
                const card=element('article',null,{class:'ops-node'});
                const live=this.flow.cores[row.core];const state=!row.enable?'已停用':nodeApplied(row)&&live?.running&&!live.dirty?'服务已启动':'已保存，运行待确认';
                card.append(element('strong',row.remark||'节点 '+row.id),element('p','端口 '+row.port+' · '+state+' · '+(row.routing_mode==='relay'?'统一出口':row.routing_mode==='direct'?'本机出口':'跟随旧全局设置')));
                card.append(this.button('分享连接',()=>{this.content.replaceChildren();void this.delivery(row,location.hostname,false);},{primary:true,disabled:!this.flow.has('share')||row.share_eligible!==true}),
                    this.button('修改设置',()=>this.open('edit',{id:row.id,revision:row.mutation_revision}),{disabled:!this.flow.has('nodes')}),
                    this.button(row.enable?'停用节点':'启用节点',()=>{
                        if(row.enable&&!confirm('停用“'+(row.remark||'节点'+row.id)+'”？使用它的连接可能中断，配置和凭据保留。'))return;
                        void this.perform(()=>this.flow.edit(row.id,row.mutation_revision,{enable:!row.enable},null,row.content_revision),()=>{this.status.textContent='已保存启停设置，请查看实际运行状态。';this.renderNodes();});
                    },{disabled:!this.flow.has('nodes')}));
                const more=element('details');more.append(element('summary','更多 / 技术信息'),element('p','ID '+row.id+' · '+row.core+' · '+row.protocol));
                if(!row.upstream_export_eligible)more.append(element('p','此旧配置不能导出为TCP出口，原设置保留。可以另建推荐的兼容节点。'));
                if(this.options.advanced)more.append(this.button('完整高级编辑',()=>this.options.advanced(row)));
                more.append(this.button('删除节点',()=>{
                    if(!confirm('永久删除“'+(row.remark||'节点'+row.id)+'”？配置与该节点连接将删除。'))return;
                    void this.perform(async()=>{await this.flow.exactNode(row.id,row.mutation_revision);await this.flow.api('/api/inbounds/'+row.id+'/delete','POST',{expected_instance_revision:row.mutation_revision});await this.flow.load();},()=>this.renderNodes());
                },{disabled:!this.flow.has('destructive')}));card.append(more);this.list.append(card);
            }
            if(!this.flow.nodes.length)this.list.append(this.note('还没有节点，可先用上面的推荐设置创建。'));
        }
        async subscriptions() {
            if(this.uiBusy||this.flow.busy||this.ended)return;
            if(this.dirty&&!confirm('离开将清除尚未提交的节点输入，继续？'))return;
            this.clear();this.task='subscriptions';
            if(this.dialog&&!this.dialog.open){this.dialog.showModal();globalThis.VUI_NAV?.modal.open(this.modalId,()=>this.close(),()=>this.canLeave());}
            return this.perform(async()=>{
                this.flow.require('share');
                const [response,warnings,grants]=await Promise.all([
                    this.flow.api('/api/inbounds','GET',null,{},true),
                    this.flow.api('/api/subscription/mihomo-warnings'),this.flow.api('/api/subscriptions')]);
                if(!globalThis.VUI_OPERATION_FLOW.REV.test(response.nodeRevision)||!Array.isArray(response.value)||!Array.isArray(warnings)||!Array.isArray(grants))throw new Error('Invalid subscription state');
                return {nodes:response.value,revision:response.nodeRevision,warnings,grants};
            },data=>{
                this.content.replaceChildren(element('h3','多个节点，一条订阅地址'),element('p','在客户端添加一次订阅地址，以后刷新即可取得所选节点。地址包含访问凭据，只交给可信使用者。'));
                const form=element('form'),name=this.field(form,'订阅名称',this.input('我的节点'));
                const eligible=data.nodes.filter(n=>n.enable&&n.share_eligible===true&&!data.warnings.some(w=>w.inbound_id===n.id));
                const picks=[];for(const node of data.nodes){
                    const input=element('input',null,{type:'checkbox',checked:eligible.length===1&&eligible[0].id===node.id,disabled:!eligible.includes(node)});
                    const label=element('label',null,{class:'ops-check'});label.append(input,element('span',(node.remark||'节点'+node.id)+' · 端口 '+node.port+(!eligible.includes(node)?'（停用或暂不支持分享）':'')));form.append(label);picks.push([input,node.id]);
                }
                if(!data.nodes.length)form.append(this.note('还没有节点，请先创建节点。'));
                const days=this.field(form,'有效期',element('select'));for(const [value,label]of[['90','90天（推荐）'],['30','30天'],['180','180天'],['365','1年']])days.append(element('option',label,{value}));
                const advanced=element('details');advanced.append(element('summary','客户端与连接地址'));
                const format=this.field(advanced,'客户端类型',element('select'));for(const [value,label]of[['mihomo.yaml','Clash / Mihomo（推荐）'],['raw','通用链接订阅'],['sing-box.json','sing-box']])format.append(element('option',label,{value}));
                const server=this.field(advanced,'节点连接域名或IP',this.input(location.hostname),'不会改变服务器设置；请使用客户端能连接的地址。');form.append(advanced);
                const submit=element('button','生成订阅地址',{type:'submit',class:'ops-primary',disabled:!eligible.length});form.append(submit);this.content.append(form);
                const output=element('div');this.content.append(output);
                let submitted=false;
                const issued=value=>{
                    this.secret='';output.replaceChildren();
                    const path=value?.paths?.[format.value];
                    if(typeof path!=='string'||!/^\/sub\/[A-Za-z0-9_-]+\/(mihomo\.yaml|raw|sing-box\.json)$/.test(path))throw new Error('Invalid subscription path');
                    const url=location.origin+path;this.secret=url;const field=element('textarea',null,{value:url,readOnly:true,rows:3});output.append(this.note('地址仅本次展示。遗失后请更换地址，旧地址随即失效。'),field,this.button('复制订阅地址',()=>this.copy(url,field)),this.button('清除地址显示',()=>{this.secret='';output.replaceChildren();}));
                };
                form.addEventListener('submit',event=>{event.preventDefault();if(submitted)return;void this.perform(async()=>{
                    const selected=picks.filter(([input,id])=>input.checked&&eligible.some(node=>node.id===id)).map(([,id])=>id);
                    if(!name.value.trim()||!selected.length)throw new globalThis.VUI_OPERATION_FLOW.FlowError('SELECT_NODES','填写名称，并选择至少一个可分享节点。');
                    // The displayed inventory is the authoritative identity precondition.
                    submitted=true;submit._opsDisabled=true;
                    return await this.flow.api('/api/subscriptions','POST',{label:name.value.trim(),server:server.value.trim(),inbound_ids:selected,formats:[format.value],expires_days:Number(days.value),expected_node_revision:data.revision});
                },issued);});
                this.content.append(element('h3','已有订阅'));
                if(!data.grants.length)this.content.append(this.note('暂无订阅。'));
                for(const grant of data.grants){
                    const row=element('article',null,{class:'ops-node'}),invalid=!!(grant.revoked||grant.expired||grant.invalidated);
                    row.append(element('strong',grant.label),element('p',invalid?'已失效，请重新选择节点创建':'有效 · '+grant.inbound_ids.length+'个节点 · 截止 '+new Date(grant.expires_at*1000).toLocaleString('zh-CN')));
                    row.append(this.button('更换订阅地址（旧地址失效）',()=>{if(!confirm('更换后旧订阅地址立即失效，需更新客户端。继续？'))return;void this.perform(()=>this.flow.api('/api/subscriptions/'+grant.id+'/rotate','POST',{expires_days:Number(days.value)}),value=>{format.value=grant.formats[0];issued(value);});},{disabled:invalid||!grant.formats?.length}));
                    row.append(this.button('撤销订阅',()=>{if(!confirm('撤销后客户端不能再拉取此订阅，已导入客户端的节点仍需分别管理。确认撤销？'))return;void this.perform(()=>this.flow.api('/api/subscriptions/'+grant.id,'DELETE'),()=>{this.secret='';output.replaceChildren();row.replaceChildren(this.note('此订阅已撤销。'));});},{disabled:grant.revoked||!this.flow.has('destructive')}));this.content.append(row);
                }
                this.status.textContent='选择节点后生成，其他设置已有默认值。';
            });
        }
        async certificates() {
            if(this.uiBusy||this.flow.busy||this.ended)return;
            if(this.dirty&&!confirm('离开将清除尚未提交的节点输入，继续？'))return;
            this.clear();this.task='certificates';if(this.dialog&&!this.dialog.open){this.dialog.showModal();globalThis.VUI_NAV?.modal.open(this.modalId,()=>this.close(),()=>this.canLeave());}
            return this.perform(async()=>{await this.flow.load();return await this.flow.api('/api/certificates');},rows=>{
                this.content.replaceChildren(element('h3','连接域名与证书'),element('p','已有证书可直接用于新节点。续期保留原证书，已停止的节点组不会自动启动。'));
                if(!this.options.remote)this.content.append(element('a','申请证书 / 更多管理',{href:'/certificates'}));
                else this.content.append(this.note('首次申请及CA条款由管理员确认；本页可维护已有证书。'));
                if(!rows.length)this.content.append(this.note('还没有证书，请让管理员先设置域名。'));
                for(const cert of rows){
                    const card=element('article',null,{class:'ops-node'}),valid=globalThis.VUI_OPERATION_FLOW.usableCertificates([cert]).length===1;
                    card.append(element('strong',cert.domain),element('p',valid?'可用于节点 · 剩余'+cert.days_remaining+'天':cert.environment==='staging'?'测试证书，不能用于正式节点':'尚不可用，请查看证书任务'));
                    const state={queued:'排队中',running:'申请中',succeeded:'申请成功',failed:'申请失败',interrupted:'申请中断'}[cert.latest_job?.state];if(state)card.append(element('p','最近任务：'+state));
                    card.append(this.button('检查并续期',()=>this.perform(()=>this.flow.api('/api/certificates/'+cert.id+'/renew','POST'),()=>{this.status.textContent='续期检查已提交，旧证书保持；稍后重新读取状态。';}),{disabled:!this.flow.has('certificates')||['queued','running'].includes(cert.latest_job?.state)}));
                    card.append(this.button(cert.auto_renew?'暂停自动续期':'启用自动续期',()=>this.perform(()=>this.flow.api('/api/certificates/'+cert.id+'/auto-renew','PUT',{enabled:!cert.auto_renew}),()=>{this.status.textContent='自动续期设置已保存。';}),{disabled:!this.flow.has('certificates')}));
                    card.append(this.button('用于新直连节点',async()=>{await this.open('direct');if(this.fields.cert)this.fields.cert.value=cert.id;this.updateForm();},{disabled:!valid||!this.flow.has('nodes','relay','certificates')}));
                    this.content.append(card);
                }
                this.content.append(this.button('重新读取证书状态',()=>this.certificates()));this.status.textContent='已读取已有域名和证书。';
            });
        }
        certificateSetup() {
            if(this.options.remote){this.problem.replaceChildren(this.note('首次申请证书必须由管理员本人确认CA条款。请让管理员先设置，已有输入保持。'));return;}
            if(this.content.querySelector('.ops-certificate'))return;
            const box=element('section',null,{class:'ops-certificate'});box.append(element('h3','先设置连接域名'));
            const domain=this.field(box,'你控制的域名',this.input(location.hostname),'不带https://、端口或路径；应指向这台服务器。');
            const email=this.field(box,'联系邮箱',this.input('','email'));
            const terms=element('input',null,{type:'checkbox'}),label=element('label',null,{class:'ops-check'});
            label.append(terms,element('span','我控制该域名，并同意Let’s Encrypt订阅协议及公开证书记录。'));
            box.append(label,element('a','查看CA条款',{href:'https://letsencrypt.org/repository/',target:'_blank',rel:'noopener noreferrer'}));
            const result=element('p','申请会检查域名和公网80端口；等待期间可保留本次节点输入。',{role:'status'});
            const errors={ACME_VALIDATION_FAILED:'域名验证失败，请核对域名指向及公网80端口。',RETRY_COOLDOWN:'申请正在冷却，请按证书页提示的时间重试。',INVALID_CERTIFICATE_MATERIAL:'证书材料验证未通过，旧证书保持。',WORKER_INTERRUPTED:'证书服务中断，请在证书页核对任务。'};
            const epoch=this.epoch;
            const observe=async(attempt=0)=>{
                if(this.ended||epoch!==this.epoch)return;
                try{
                    const job=await this.flow.api('/api/certificates/jobs/'+this.certificateJob);
                    if(this.ended||epoch!==this.epoch)return;
                    if(['succeeded','success','completed'].includes(job.state||job.status)){
                        const certs=await this.flow.api('/api/certificates');
                        if(this.ended||epoch!==this.epoch)return;
                        this.flow.certificates=globalThis.VUI_OPERATION_FLOW.usableCertificates(certs);
                        const saved=this.rememberForm();this.renderForm();this.restoreForm(saved);this.status.textContent='证书已准备好，可以继续创建节点。';return;
                    }
                    if(['failed','interrupted'].includes(job.state||job.status)){result.textContent=errors[job.error]||'证书申请未完成，请在证书管理页查看原因；不会重复申请。';return;}
                    result.textContent='证书申请处理中，正在自动读取结果…';
                }catch(error){if(epoch!==this.epoch||this.ended)return;result.textContent=message(error)+' 正在核对已有任务，不会重复申请。';}
                if(attempt<39)this.certificateTimer=setTimeout(()=>observe(attempt+1),3000);
                else result.textContent='本次自动等待已结束。任务可能仍在处理，使用下方按钮读取结果，不会再次申请。';
            };
            const submit=this.button('申请正式证书',()=>this.perform(async()=>{
                if(this.certificateSubmitted)return;
                if(!terms.checked)throw new globalThis.VUI_OPERATION_FLOW.FlowError('TERMS','需要你本人明确同意CA条款。');
                const name=domain.value.trim().toLowerCase(),contact=email.value.trim();
                if(!/^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$/.test(name))throw new globalThis.VUI_OPERATION_FLOW.FlowError('DOMAIN','请填写有效域名，不含协议、端口或路径。');
                if(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contact))throw new globalThis.VUI_OPERATION_FLOW.FlowError('EMAIL','请填写有效联系邮箱。');
                this.certificateSubmitted=true;submit._opsDisabled=true;
                try{const job=await this.flow.api('/api/certificates','POST',{domain:name,email:contact,environment:'production',auto_renew:true,accept_terms:true});
                    if(typeof job.id!=='string'||!job.id)throw new Error('Invalid job');this.certificateJob=job.id;void observe();
                }catch(error){result.textContent='申请结果尚未确认，请到证书管理查看已有申请，不要重复提交。';throw error;}
            }));
            box.append(submit,result,this.button('读取已有申请结果',()=>{if(this.certificateJob){clearTimeout(this.certificateTimer);void observe();}else result.textContent='请到证书管理核对已有申请，再返回继续。';}),
                element('a','打开证书管理',{href:'/certificates'}));this.content.append(box);
        }
    }
    const nodeApplied = row => row.routing_status?.state==='applied';
    function mount(root, options={}) {
        if(!root)return null;if(views.has(root))return views.get(root);
        const view=new View(root,options);views.set(root,view);return view;
    }
    globalThis.VUI_SIMPLE_OPERATIONS={mount,View,cookieRequest,
        open(task, target){const root=document.querySelector('[data-simple-operations="dialog"]');const view=root&&views.get(root);if(view)return view.open(task,target);return Promise.reject(new Error('Operation view unavailable'));}};
    const start=()=>{for(const root of document.querySelectorAll('[data-simple-operations]')){
        if(root.dataset.simpleOperations==='delegated')continue;
        const dialog=root.dataset.simpleOperations==='dialog';mount(root,{dialog,advanced:dialog?row=>{const view=views.get(root);view?.close();if(view?.dialog?.open)return;globalThis.dispatchEvent(new CustomEvent('vui:advanced-node',{detail:row?{id:row.id,revision:row.mutation_revision}:null}));}:null});
    }};
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
