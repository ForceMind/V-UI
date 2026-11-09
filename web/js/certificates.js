'use strict';
(() => {
    const $ = id => document.getElementById(id);
    let busy = false, polling = false, active = true, refreshGeneration = 0, timer, nodes = [], capabilities = {}, editingDNS = null, writeNeedsReadback = false;
    const messages = {
        INVALID_DOMAIN:'域名格式错误，请不要填写协议、端口、路径或通配符。',
        INVALID_EMAIL:'邮箱格式错误。', CERTIFICATE_ALREADY_EXISTS:'已有同域名、同环境、同验证方式和覆盖范围的证书，请在列表中检查续期。',
        RENEWAL_NOT_DUE:'证书尚未到续期时间，没有向 CA 发起重复请求。',
        RETRY_COOLDOWN:'正在重试冷却期，请按列表中的下次重试时间再操作。',
        ACME_VALIDATION_FAILED:'CA 验证失败。请检查 A/AAAA、80端口、HTTP-01服务和CAA记录；详细诊断保存在服务器受控日志目录。',
        STAGING_CANNOT_BE_DEPLOYED:'测试证书不能绑定到面板或节点。', PANEL_DOMAIN_MISMATCH:'该证书域名与当前面板域名不一致。',
        PANEL_HOT_RELOAD_UNAVAILABLE:'此启动方式未启用面板证书热更新，请使用受管 HTTPS 启动入口。',
        CERTIFICATE_APPLY_FAILED:'证书已签发，但应用未完成；请检查核心状态并重试应用。',
        CORE_STOPPED_PENDING_APPLY:'证书路径已保存并验证，但核心处于停止状态。请先启动核心，再重试应用。',
        BINDING_CHANGED_MANUALLY:'节点证书路径已被手动修改，自动续期未覆盖你的设置。请重新明确绑定。',
        TLS_NODE_REQUIRED:'请选择已启用 TLS 的节点；不适用于 REALITY。',
        ISSUANCE_INTERRUPTED_OR_TIMED_OUT:'签发被中断或超时，旧证书未替换。',
        INVALID_CERTIFICATE_MATERIAL:'返回证书未通过域名、密钥、有效期或信任链检查。',
        WORKER_INTERRUPTED:'上次签发因进程退出而中断，稍后按退避计划重试。',
        INVALID_DNS_TOKEN:'API Token格式无效，请重新粘贴Cloudflare API Token。',
        INVALID_DNS_ZONE:'请填写Cloudflare中实际托管的域名，不含协议或通配符。',
        DNS_PROVIDER_FORBIDDEN:'Cloudflare拒绝访问，请核对Token有效期、指定Zone及DNS Edit/Zone Read权限。',
        DNS_ZONE_NOT_FOUND:'该Token没有找到此托管域名，请填写Cloudflare中的Zone名称并核对授权范围。',
        DNS_ZONE_NOT_UNIQUE:'托管域名匹配不唯一，未保存；请核对Token只授权了目标Zone。',
        DNS_OWNER_OUTSIDE_ZONE:'证书域名不在所选Cloudflare托管域名下，请重新选择凭据。',
        DNS01_UNAVAILABLE:'此版本的DNS签发组件尚未就绪，未发起申请。',
        CONNECTION_DOMAIN_REQUIRED:'请填写节点实际连接域名，并将其A/AAAA记录指向本机；不要填写通配符。',
        INVALID_CONNECTION_DOMAIN:'连接域名格式无效，请填写具体域名，不含协议、端口或通配符。',
        CERTIFICATE_DOMAIN_MISMATCH:'连接域名不在所选证书覆盖范围内，请核对后保存。',
        DNS_CLEANUP_PENDING:'DNS验证记录尚待清理，旧证书保持；请在下方清理遗留验证记录后重试。',
        DNS_CHALLENGE_OWNER_BUSY:'此域名有尚未清理的验证任务，请先处理下方的遗留验证记录。',
        DNS_PROVIDER_RATE_LIMITED:'Cloudflare请求受限，请稍后核对任务状态。',
        DNS_PROVIDER_TIMEOUT:'Cloudflare查询超时，请稍后核对状态。',
        DNS_PROVIDER_WRITE_UNKNOWN:'DNS写入结果尚不能确认，系统保留清理记录；请先查看验证记录，勿重复申请。',
        DNS_DELEGATION_UNSUPPORTED:'验证记录使用了CNAME或NS委派，本轮暂不支持；请使用直接托管于所选Zone的域名。',
        DNS_CREDENTIAL_ZONE_CHANGED:'新Token对应的托管域名与原凭据不一致，未替换。',
        DNS_CREDENTIAL_DISABLED:'所选Cloudflare凭据已禁用，请由管理员验证并启用。',
        DNS_CREDENTIAL_RESTORE_PENDING:'凭据从备份恢复，须由管理员验证并启用后才能续期。',
        DNS_CREDENTIAL_REVISION_CONFLICT:'凭据已被其他操作修改，请刷新后重新选择；本次未覆盖。',
        DNS_CREDENTIAL_BUSY:'另一项凭据操作正在进行，请稍后刷新。',
        DNS_CREDENTIALS_UNSAFE:'凭据文件或权限异常，系统已停止读取，请检查服务器受控数据目录。',
        DNS_CREDENTIALS_INVALID:'凭据数据不完整，未发起DNS写入，请检查或恢复完整私有备份。',
        DNS_PROVIDER_TLS_FAILED:'Cloudflare的TLS证书校验失败，未绕过验证；请核对服务器时间和信任链。',
        DNS_CHALLENGE_REVISION_CONFLICT:'验证记录已被其他操作更新，请刷新后重新核对；本次未覆盖。',
        DNS_CHALLENGE_ALREADY_CLOSED:'该验证任务已结束，请刷新查看最新状态。',
        DNS_CHALLENGE_NOT_FOUND:'验证记录已不存在，请刷新后重新核对。',
        DNS_JOURNAL_BUSY:'另一项验证记录操作正在进行，请先刷新核对，勿重复提交。',
        DNS_CHALLENGE_CREATE_UNKNOWN:'仍无法确认此前写入没有残留，验证记录继续保留为待清理；查无记录不等于自动清理成功。',
        DNS_CHALLENGE_AMBIGUOUS:'发现多条可能相关的验证记录，系统未猜测删除；请在Cloudflare核对本任务记录。',
        DNS_CHALLENGE_RECORD_STILL_PRESENT:'Cloudflare中仍有本次验证记录，未作人工无残留确认；请核对后再处理。'

    };
    const text = (value,method) => value==='ACME_VALIDATION_FAILED'&&method==='dns-01'?'DNS证书验证失败，请核对Zone、Token、TXT传播及CAA记录；旧材料保持。':typeof value==='string'&&Object.hasOwn(messages,value)?messages[value]:(value?'操作未完成，请核对任务与配置状态。':'无');
    const time = stamp => stamp ? new Date(stamp * 1000).toLocaleString() : '—';
    const notice = (message, error=false) => { if(!active)return; $('notice').textContent=message; $('notice').className=error?'error':''; };
    const element=(tag,content,attrs={}) => { const node=document.createElement(tag); if(content!==null)node.textContent=content; Object.entries(attrs).forEach(([k,v])=>node.setAttribute(k,v));return node; };
    const READ_FAILED='读取失败，请刷新后重试。';
    const WRITE_UNKNOWN='写入结果暂时无法确认，请先刷新核对状态，勿重复提交。';
    const FORBIDDEN='当前授权不允许此操作，请使用管理员账号或核对维护授权。';
    const failure=message=>{const error=new Error(message);error.userMessage=message;return error;};
    const report=error=>notice(error&&typeof error.userMessage==='string'?error.userMessage:'操作未完成，请刷新核对状态。',true);
    async function requestJSON(url,method='GET',data){
        if(!active)throw failure('页面已离开。');
        const writing=method!=='GET';
        if(writing&&writeNeedsReadback)throw failure('上次写入结果尚未确认，请先刷新核对状态后再操作。');
        let response,payload;
        const uncertain=()=>{if(writing)writeNeedsReadback=true;return failure(writing?WRITE_UNKNOWN:READ_FAILED);};
        try{response=await fetch(url,{method,credentials:'same-origin',cache:'no-store',
            headers:{'X-VUI-Request':'1',...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data)});}
        catch{throw uncertain();}
        if(response.status===401){if(active)globalThis.VUI_NAV?VUI_NAV.replaceLogin():location.replace('/login');throw failure('登录已过期');}
        if(response.status===403)throw failure(FORBIDDEN);
        try{payload=await response.json();}catch{throw uncertain();}
        if(payload===null||typeof payload!=='object')throw uncertain();
        if(!response.ok){
            if(!writing)throw failure(READ_FAILED);
            if(response.status>=500)throw uncertain();
            if(response.status===409||response.status===412||response.status===428)writeNeedsReadback=true;
            const code=typeof payload.detail==='string'?payload.detail:null;
            throw failure(code&&Object.hasOwn(messages,code)?messages[code]:'输入或状态不符合要求，请刷新核对后再操作。');
        }
        return payload;
    }
    const api=(path,method='GET',data)=>requestJSON('/api/certificates'+path,method,data);
    function literalHost(value){
        if(typeof value!=='string')return null;
        value=value.trim();
        if(!value||/[\s/:?@#*%\[\]\\]/u.test(value))return null;
        try{
            const host=new URL('https://'+value).hostname.toLowerCase().replace(/\.$/,'');
            if(host.length>253||!host.includes('.')||/^[0-9]+$/.test(host.split('.').at(-1))
                ||!host.split('.').every(part=>/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(part)))return null;
            return host;
        }catch{return null;}
    }
    function covers(names,value){
        const host=literalHost(value);
        return !!host&&Array.isArray(names)&&names.some(name=>typeof name==='string'&&(name===host||name.startsWith('*.')
            &&host.endsWith('.'+name.slice(2))&&host.split('.').length===name.slice(2).split('.').length+1));
    }
    async function action(fn){
        // A started action supersedes reads already in flight, even before its write returns.
        if(busy||!active)return;busy=true;refreshGeneration++;
        document.querySelectorAll('button').forEach(b=>{b.dataset.wasDisabled=String(b.disabled);b.disabled=true;});
        try{await fn();await refresh();}
        catch(error){report(error);}
        finally{busy=false;document.querySelectorAll('button').forEach(b=>{if(b.dataset.wasDisabled!==undefined){b.disabled=b.dataset.wasDisabled==='true';delete b.dataset.wasDisabled;}});}
    }
    function button(label,handler,disabled=false,attrs={}){const b=element('button',label,{type:'button',...attrs});b.disabled=disabled;b.addEventListener('click',()=>action(handler));return b;}
    function dnsMode(){
        const dns=$('method').value==='dns-01';
        $('dns-options').hidden=!dns;$('http-guide').hidden=dns;$('dns-profile').required=dns;
        const domain=$('domain').value.trim();
        $('dns-names').textContent=domain?'实际申请：'+domain+($('coverage').value==='wildcard'?'、*.'+domain:''):'先填写证书域名，再确认覆盖范围。';
    }
    function clearDNSEdit(){
        editingDNS=null;$('dns-credential-form').reset();$('dns-zone').disabled=false;$('dns-label').disabled=false;
        $('dns-form-title').textContent='添加Cloudflare凭据';$('save-dns-profile').textContent='验证并保存';
    }
    function renderDNS(profiles){
        const choice=$('dns-profile').value;
        $('dns-profile').replaceChildren(element('option','请选择Cloudflare凭据',{value:''}));
        const usable=profiles.filter(p=>p.enabled===true&&p.restore_pending===false);
        for(const profile of usable)$('dns-profile').append(element('option',profile.label+' · '+profile.zone_name,{value:profile.id}));
        if(usable.some(p=>p.id===choice))$('dns-profile').value=choice;
        else if(usable.length===1)$('dns-profile').value=usable[0].id;
        $('dns-credential-list').replaceChildren();
        for(const profile of profiles){
            const card=element('fieldset');
            card.append(element('legend',profile.label+' · '+profile.zone_name));
            card.append(element('p',(profile.restore_pending===true?'恢复后待管理员确认':profile.enabled===true?'已配置':'已禁用')+' · 关联证书 '+profile.references+' 张'));
            card.append(button('替换Token',async()=>{
                editingDNS={id:profile.id,revision:profile.revision};$('dns-label').value=profile.label;$('dns-zone').value=profile.zone_name;
                $('dns-label').disabled=true;$('dns-zone').disabled=true;$('dns-token').value='';
                $('dns-form-title').textContent='替换“'+profile.label+'”的Token';$('save-dns-profile').textContent='验证并替换Token';$('dns-token').focus();
            }));
            if(profile.enabled===true&&profile.restore_pending===false)card.append(button('禁用后续签发与续期',async()=>{
                await api('/dns-credentials/'+profile.id+'/disable','POST',{expected_revision:profile.revision});
                notice('凭据已禁用。现有证书和节点继续保留；已创建的验证记录仍可清理。');
            }));
            else card.append(button('验证并启用续期凭据',async()=>{
                await api('/dns-credentials/'+profile.id+'/enable','POST',{expected_revision:profile.revision,confirmed:true});
                notice('已核对Cloudflare域名并启用凭据；没有立即重复申请证书。');
            }));
            $('dns-credential-list').append(card);
        }
    }
    function renderChallenges(profiles,jobs){
        const list=$('dns-challenge-list'), byId=new Map(profiles.map(profile=>[profile.id,profile]));
        list.replaceChildren();
        for(const job of jobs){
            const domain=typeof job.domain==='string'?job.domain:'域名待核对';
            const zone=typeof job.zone_name==='string'?job.zone_name:'托管域名待核对';
            const card=element('fieldset',null,{'data-dns-challenge':job.job_id});
            card.append(element('legend',domain+' · '+zone));
            const state={active:'验证任务尚未结束或未完成清理',cleanup_pending:'待清理',cleaned:'已自动清理',
                manual_confirmed:'已人工确认无残留（不是自动清理成功）'}[job.phase]||'状态待核对';
            card.append(element('p',state+' · 验证记录 '+(Number.isSafeInteger(job.count)&&job.count>=0?job.count:'待核对')+' 条'));
            card.append(element('p','最近更新：'+time(job.updated_at||job.created_at),{class:'hint'}));
            if(job.error)card.append(element('p',text(job.error,'dns-01'),{class:'hint'}));
            const profile=byId.get(job.credential_id), closed=['cleaned','manual_confirmed'].includes(job.phase);
            if(!profile)card.append(element('p','关联凭据不可用，请管理员核对完整私有备份；不会换用其他凭据。',{class:'hint'}));
            else if(profile.restore_pending!==false){
                card.append(element('p','凭据恢复后待确认。请先到Cloudflare凭据列表验证并启用，再处理遗留记录。',{class:'hint'}));
                card.append(button('先验证并启用凭据',async()=>{
                    $('dns-credentials').open=true;notice('请在Cloudflare凭据列表点击“验证并启用续期凭据”，确认后再清理；不会自动重签。');
                }));
            }else if(!profile.enabled)card.append(element('p','凭据虽已禁用，仍可仅用于清理原任务记录；不会启用新签发。',{class:'hint'}));
            if(!closed){
                const unavailable=!profile||profile.restore_pending!==false;
                const payload=()=>({expected_revision:job.revision,expected_profile_revision:profile.revision,confirmed:true});
                card.append(button('使用当前凭据清理验证记录',async()=>{
                    if(capabilities.dns_credentials_manage!==true)throw failure(FORBIDDEN);
                    if(!confirm('确认原签发已结束，并使用当前凭据仅清理本任务自己的TXT记录？不会重新申请证书，也不会删除其他TXT。'))return;
                    const result=await api('/dns-challenges/'+encodeURIComponent(job.job_id)+'/cleanup','POST',payload());
                    if(result.queued!==true){writeNeedsReadback=true;throw failure(WRITE_UNKNOWN);}
                    notice('清理任务已受理，后台将核对并只清理本任务TXT；请等待状态更新，勿重复提交。');
                },unavailable,{'data-dns-cleanup':job.job_id}));
                if(['DNS_CHALLENGE_CREATE_UNKNOWN','DNS_PROVIDER_WRITE_UNKNOWN','DNS_CHALLENGE_AMBIGUOUS'].includes(job.error)){
                    card.append(button('我已在Cloudflare确认没有残留',async()=>{
                        if(capabilities.dns_credentials_manage!==true)throw failure(FORBIDDEN);
                        if(!confirm('请先在Cloudflare核对该域名的验证TXT，确认本次任务没有任何残留。继续只记录人工确认，不代表系统自动清理成功，也不会重新申请证书。确认没有残留吗？'))return;
                        notice('服务器正在进行最多60秒的只读核对，请等待，勿重复提交。');
                        const result=await api('/dns-challenges/'+encodeURIComponent(job.job_id)+'/confirm-absence','POST',payload());
                        if(result.phase!=='manual_confirmed'){writeNeedsReadback=true;throw failure(WRITE_UNKNOWN);}
                        notice('已记录你在Cloudflare确认无残留。这是人工确认，不是系统自动清理成功；没有重新申请证书。');
                    },unavailable,{'data-dns-confirm-absence':job.job_id}));
                }
            }
            list.append(card);
        }
        if(!jobs.length)list.append(element('p','当前没有待处理的验证记录。人工确认与自动清理分别记录，不代表重新签发。'));
    }
    async function refresh(){
        if(!active)return;
        const generation=++refreshGeneration;
        let certs,profiles=[],challenges=[];
        try{certs=await api('');if(capabilities.dns_credentials_manage===true){profiles=await api('/dns-credentials');challenges=await api('/dns-challenges');}}

        catch(error){if(active&&generation===refreshGeneration)throw error;return;}
        if(!active||generation!==refreshGeneration)return;
        if(capabilities.dns_credentials_manage===true){renderDNS(profiles);renderChallenges(profiles,challenges);}
        // Read choices at commit time, not request time: selects remain editable during I/O.
        const choices=new Map(Array.from($('certificate-list').querySelectorAll('[data-bind-select]'),select=>[select.dataset.bindSelect,select.value]));
        const hosts=new Map(Array.from($('certificate-list').querySelectorAll('[data-bind-host]'),input=>[input.dataset.bindHost,input.value]));
        $('certificate-list').replaceChildren();
        for(const cert of certs){
            const card=element('fieldset',null,{'data-certificate':cert.id});card.append(element('legend',cert.domain+' · '+(cert.method==='dns-01'?'DNS-01':'HTTP-01')+' · '+(cert.wildcard?'根域名和一级子域名':'单个域名')+' · '+(cert.environment==='production'?'正式':'测试')));
            if(cert.names)card.append(element('p','覆盖：'+cert.names.join('、'),{class:'hint'}));
            const status={pending:'正在申请',expired:'已过期',renewal_failed:'续期失败，旧证书仍保留',failed:'申请失败',staging_only:'测试签发成功（不能上线）',valid:'有效',not_issued:'尚未签发'}[cert.status];
            card.append(element('p',status+(cert.days_remaining===null?'':' · 剩余 '+cert.days_remaining+' 天')));
            card.append(element('p','到期：'+time(cert.not_after)+'　续期窗口：'+time(cert.renew_at),{class:'hint'}));
            if(cert.error)card.append(element('p',text(cert.error,cert.method)+' 下次重试：'+time(cert.retry_at),{class:'hint'}));
            if(cert.latest_job)card.append(element('p','最近任务：'+({queued:'排队中',running:'处理中',succeeded:'成功',failed:'失败',interrupted:'已中断'}[cert.latest_job.state]||'状态待确认')+'　'+time(cert.latest_job.finished_at||cert.latest_job.created_at),{class:'hint','data-job-state':cert.latest_job.state}));
            for(const binding of cert.bindings)card.append(element('p',binding.target+'：'+(binding.error?text(binding.error):binding.pending?'待应用':'已配置'),{class:'hint'}));
            const actions=element('div',null,{class:'actions'});
            const dnsReadOnly=cert.method==='dns-01'&&capabilities.dns_credentials_manage!==true;
            actions.append(button('检查续期',async()=>{await api('/'+cert.id+'/renew','POST');notice('已进入续期队列。');},cert.status==='pending'||dnsReadOnly));
            actions.append(button(cert.auto_renew?'暂停自动续期':'启用自动续期',async()=>{await api('/'+cert.id+'/auto-renew','PUT',{enabled:!cert.auto_renew});notice('自动续期设置已更新。');},dnsReadOnly));
            if(dnsReadOnly)card.append(element('p','DNS签发和续期设置由管理员操作；维护授权可使用已签发材料。',{class:'hint'}));
            const deployable=cert.environment==='production'&&cert.revision&&cert.days_remaining>=0;
            actions.append(button('应用到面板',async()=>{if(!confirm('确认将该证书用于当前面板域名？新连接将使用新证书。'))return;await api('/'+cert.id+'/bind-panel','POST');notice('面板证书已更新。');},!deployable||!capabilities.panel_hot_reload));
            const select=element('select',null,{'aria-label':'选择绑定节点','data-bind-select':cert.id});
            select.append(element('option','选择 TLS 节点',{value:''}));
            nodes.filter(n=>n.managed_certificate_eligible===true).forEach(n=>select.append(element('option',(n.remark||'节点 '+n.id)+' · 端口 '+n.port+' · '+n.core,{value:String(n.id)})));
            const choice=choices.get(String(cert.id));
            if(Array.from(select.options).some(option=>option.value===choice))select.value=choice;
            actions.append(select);
            let host=null;
            if(cert.method==='dns-01'){
                const id='bind-host-'+cert.id;
                actions.append(element('label','实际连接域名',{for:id}));
                host=element('input',null,{id,maxlength:'253',placeholder:'node.example.com','data-bind-host':cert.id,
                    'aria-label':'实际连接域名',autocomplete:'off',spellcheck:'false'});
                // Presence, including an intentionally empty draft, wins over a new suggestion.
                host.value=hosts.has(String(cert.id))?hosts.get(String(cert.id)):
                    covers(cert.names,location.hostname)?literalHost(location.hostname):'';
                actions.append(host);
                actions.append(element('p','填写具体域名，例如node.example.com；请配置指向本机的A/AAAA记录。此域名也作为客户端TLS的SNI，并须在证书覆盖范围内；不能填写*.通配符。',{class:'hint'}));
            }
            actions.append(button('绑定节点',async()=>{
                if(!select.value)throw failure('请先选择 TLS 节点');
                const payload={inbound_id:Number(select.value)};
                if(host){
                    if(!host.value.trim())throw failure('请填写实际连接域名，并配置指向本机的A/AAAA记录。');
                    const domain=literalHost(host.value);
                    if(!domain)throw failure(messages.INVALID_CONNECTION_DOMAIN);
                    if(!covers(cert.names,domain))throw failure(messages.CERTIFICATE_DOMAIN_MISMATCH);
                    payload.server_name=domain;
                }
                await api('/'+cert.id+'/bind-inbound','POST',payload);
                notice('证书已绑定节点。未运行的核心不会被自动启动。');
            },!deployable));
            actions.append(button('重试应用',async()=>{const results=await api('/'+cert.id+'/apply','POST');if(results.some(r=>!r.applied))throw failure('仍有绑定未应用，请查看卡片状态');notice('已检查所有绑定。');},!deployable||!cert.bindings.length));
            if(cert.revision)actions.append(element('a','下载公有证书链',{href:'/api/certificates/'+cert.id+'/fullchain.pem',download:'fullchain.pem'}));
            card.append(actions);$('certificate-list').append(card);
        }
        if(!certs.length)$('certificate-list').append(element('p','尚未申请证书。填写自己的域名并申请正式证书，成功后即可在节点中选择。'));
        writeNeedsReadback=false;
    }
    $('request-certificate').addEventListener('submit',event=>{event.preventDefault();action(async()=>{
        const payload={domain:$('domain').value.trim(),email:$('email').value.trim(),environment:$('environment').value,auto_renew:$('auto-renew').checked,accept_terms:$('terms').checked};
        if($('method').value==='dns-01'){
            if(capabilities.dns_credentials_manage!==true)throw failure(FORBIDDEN);
            Object.assign(payload,{method:'dns-01',wildcard:$('coverage').value==='wildcard',credential_id:$('dns-profile').value});
        }
        await api('','POST',payload);
        notice($('environment').value==='production'?'正式证书申请已排队，正在自动读取进度；成功后到节点选择这个域名。':'测试申请已排队，测试证书不能用于正式连接。');
    });});
    $('method').addEventListener('change',dnsMode);$('domain').addEventListener('input',dnsMode);$('coverage').addEventListener('change',dnsMode);
    $('add-dns-profile').addEventListener('click',()=>{$('dns-credentials').open=true;$('dns-label').focus();});
    $('cancel-dns-edit').addEventListener('click',()=>{if(!busy)clearDNSEdit();});
    $('dns-credential-form').addEventListener('submit',event=>{event.preventDefault();action(async()=>{
        if(capabilities.dns_credentials_manage!==true){$('dns-token').value='';throw failure(FORBIDDEN);}
        const editing=editingDNS?{...editingDNS}:null;
        const payload=editing?{expected_revision:editing.revision,api_token:$('dns-token').value}:{label:$('dns-label').value.trim(),zone_name:$('dns-zone').value.trim(),api_token:$('dns-token').value};
        $('dns-token').value='';$('dns-token').disabled=true;$('dns-label').disabled=true;$('dns-zone').disabled=true;
        try{
            await api('/dns-credentials'+(editing?'/'+editing.id:''),editing?'PUT':'POST',payload);
            if(active){clearDNSEdit();notice('Cloudflare凭据已保存；Token不会回显。可以选择凭据申请证书。');}
        }finally{
            payload.api_token='';
            if(active){$('dns-token').value='';$('dns-token').disabled=false;$('dns-label').disabled=!!editingDNS;$('dns-zone').disabled=!!editingDNS;}
        }
    });});
    $('refresh').addEventListener('click',()=>action(async()=>{}));
    addEventListener('pagehide',()=>{active=false;refreshGeneration++;clearInterval(timer);$('dns-token').value='';});
    addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
    action(async()=>{
        capabilities=await api('/capabilities');
        if(!active)return;
        $('dns-credentials').hidden=capabilities.dns_credentials_manage!==true;
        $('dns-challenges').hidden=capabilities.dns_credentials_manage!==true;
        $('dns-method').disabled=capabilities.dns01!==true||capabilities.dns_credentials_manage!==true;
        dnsMode();
        nodes=await requestJSON('/api/inbounds');
        if(!active)return;
        $('cert-workspace').hidden=false;
        notice(capabilities.worker_running?'证书服务运行中。':'证书自动处理器未启动：队列会保留，但需使用受管服务启动。',!capabilities.worker_running);
        timer=setInterval(()=>{
            if(!active||busy||polling||document.hidden)return;
            polling=true;
            refresh().catch(report).finally(()=>{polling=false;});
        },5000);
    });
})();
