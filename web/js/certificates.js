'use strict';
(() => {
    const $ = id => document.getElementById(id);
    let busy = false, timer, nodes = [], capabilities = {};
    const messages = {
        INVALID_DOMAIN:'域名格式错误，请不要填写协议、端口、路径或通配符。',
        INVALID_EMAIL:'邮箱格式错误。', CERTIFICATE_ALREADY_EXISTS:'已有同域名、同环境证书，请在列表中检查续期。',
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
        WORKER_INTERRUPTED:'上次签发因进程退出而中断，稍后按退避计划重试。'
    };
    const text = value => messages[value] || value || '无';
    const time = stamp => stamp ? new Date(stamp * 1000).toLocaleString() : '—';
    const notice = (message, error=false) => { $('notice').textContent=message; $('notice').className=error?'error':''; };
    const element=(tag,content,attrs={}) => { const node=document.createElement(tag); if(content!==null)node.textContent=content; Object.entries(attrs).forEach(([k,v])=>node.setAttribute(k,v));return node; };
    async function api(path, method='GET', data) {
        const response=await fetch('/api/certificates'+path,{method,credentials:'same-origin',cache:'no-store',
            headers:{'X-VUI-Request':'1',...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data)});
        if(response.status===401){location.replace('/login');throw new Error('登录已过期');}
        const payload=await response.json();
        if(!response.ok)throw new Error(text(typeof payload.detail==='string'?payload.detail:'输入格式不正确'));
        return payload;
    }
    async function action(fn){
        if(busy)return;busy=true;
        document.querySelectorAll('button').forEach(b=>{b.dataset.wasDisabled=String(b.disabled);b.disabled=true;});
        try{await fn();await refresh();}
        catch(error){notice(error.message,true);}
        finally{busy=false;document.querySelectorAll('button').forEach(b=>{if(b.dataset.wasDisabled!==undefined){b.disabled=b.dataset.wasDisabled==='true';delete b.dataset.wasDisabled;}});}
    }
    function button(label,handler,disabled=false,attrs={}){const b=element('button',label,{type:'button',...attrs});b.disabled=disabled;b.addEventListener('click',()=>action(handler));return b;}
    async function refresh(){
        const certs=await api('');$('certificate-list').replaceChildren();
        for(const cert of certs){
            const card=element('fieldset',null,{'data-certificate':cert.id});card.append(element('legend',cert.domain+' · '+(cert.environment==='production'?'正式':'测试')));
            const status={pending:'正在申请',expired:'已过期',renewal_failed:'续期失败，旧证书仍保留',failed:'申请失败',staging_only:'测试签发成功（不能上线）',valid:'有效',not_issued:'尚未签发'}[cert.status];
            card.append(element('p',status+(cert.days_remaining===null?'':' · 剩余 '+cert.days_remaining+' 天')));
            card.append(element('p','到期：'+time(cert.not_after)+'　续期窗口：'+time(cert.renew_at),{class:'hint'}));
            if(cert.error)card.append(element('p',text(cert.error)+' 下次重试：'+time(cert.retry_at),{class:'hint'}));
            if(cert.latest_job)card.append(element('p','最近任务：'+cert.latest_job.state+'　'+time(cert.latest_job.finished_at||cert.latest_job.created_at),{class:'hint','data-job-state':cert.latest_job.state}));
            for(const binding of cert.bindings)card.append(element('p',binding.target+'：'+(binding.error?text(binding.error):binding.pending?'待应用':'已配置'),{class:'hint'}));
            const actions=element('div',null,{class:'actions'});
            actions.append(button('检查续期',async()=>{await api('/'+cert.id+'/renew','POST');notice('已进入续期队列。');},cert.status==='pending'));
            actions.append(button(cert.auto_renew?'暂停自动续期':'启用自动续期',async()=>{await api('/'+cert.id+'/auto-renew','PUT',{enabled:!cert.auto_renew});notice('自动续期设置已更新。');}));
            const deployable=cert.environment==='production'&&cert.revision&&cert.days_remaining>=0;
            actions.append(button('应用到面板',async()=>{if(!confirm('确认将该证书用于当前面板域名？新连接将使用新证书。'))return;await api('/'+cert.id+'/bind-panel','POST');notice('面板证书已更新。');},!deployable||!capabilities.panel_hot_reload));
            const select=element('select',null,{'aria-label':'选择绑定节点','data-bind-select':cert.id});
            select.append(element('option','选择 TLS 节点',{value:''}));
            nodes.filter(n=>n.core==='sing-box'&&['vless','trojan'].includes(n.protocol)&&n.stream_settings?.tls?.enabled&&!n.stream_settings?.tls?.reality).forEach(n=>select.append(element('option',n.remark||'节点 '+n.id,{value:String(n.id)})));
            actions.append(select);
            actions.append(button('绑定节点',async()=>{if(!select.value)throw new Error('请先选择 TLS 节点');await api('/'+cert.id+'/bind-inbound','POST',{inbound_id:Number(select.value)});notice('证书已绑定节点。未运行的核心不会被自动启动。');},!deployable));
            actions.append(button('重试应用',async()=>{const results=await api('/'+cert.id+'/apply','POST');if(results.some(r=>!r.applied))throw new Error('仍有绑定未应用，请查看卡片状态');notice('已检查所有绑定。');},!deployable||!cert.bindings.length));
            if(cert.revision)actions.append(element('a','下载公有证书链',{href:'/api/certificates/'+cert.id+'/fullchain.pem',download:'fullchain.pem'}));
            card.append(actions);$('certificate-list').append(card);
        }
        if(!certs.length)$('certificate-list').append(element('p','尚未申请证书。建议先测试签发以检查DNS与80端口。'));
    }
    $('request-certificate').addEventListener('submit',event=>{event.preventDefault();action(async()=>{
        await api('','POST',{domain:$('domain').value.trim(),email:$('email').value.trim(),environment:$('environment').value,auto_renew:$('auto-renew').checked,accept_terms:$('terms').checked});
        notice('申请已排队。测试证书不会替换正式证书。');
    });});
    $('refresh').addEventListener('click',()=>action(refresh));
    addEventListener('pagehide',()=>clearInterval(timer));
    addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
    action(async()=>{
        capabilities=await api('/capabilities');
        const response=await fetch('/api/inbounds',{credentials:'same-origin',cache:'no-store'});
        if(!response.ok)throw new Error('无法读取节点列表');nodes=await response.json();
        $('cert-workspace').hidden=false;
        notice(capabilities.worker_running?'证书服务运行中。':'证书自动处理器未启动：队列会保留，但需使用受管服务启动。',!capabilities.worker_running);
        timer=setInterval(()=>{if(!busy&&!document.hidden)refresh().catch(error=>notice(error.message,true));},5000);
    });
})();
