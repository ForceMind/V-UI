'use strict';
(() => {
    const $ = id => document.getElementById(id);
    const MAX_INPUT_BYTES = 1048576;
    const REVISION = /^[0-9a-f]{64}$/;
    const modes = {'00':'普通模式', '10':'仅本机显式代理', '01':'仅设备接入中转', '11':'本机代理与接入中转'};
    const errors = {
        DIAGNOSTIC_BUSY:'诊断并发已满，请等待当前任务完成。',
        DIAGNOSTIC_RATE_LIMITED:'诊断启动间隔至少 30 秒，请稍后再试。',
        DIAGNOSTIC_NOT_FOUND:'诊断任务不存在或已过期。',
        INVALID_DIAGNOSTIC_REQUEST:'诊断只接受已保存上游与准确修订。',
        DIAGNOSTIC_UNAVAILABLE:'服务器线路诊断暂不可用。',
        PROXY_REVISION_CONFLICT:'服务器草稿或应用事务已变化，请核对最新状态。',
        REVISION_REQUIRED:'缺少草稿修订号，请刷新后重试。', INVALID_REVISION:'草稿修订号无效，请刷新后重试。',
        IGNORED_SECTIONS_ACKNOWLEDGEMENT_REQUIRED:'请逐项确认全部未导入区块。',
        UPSTREAM_OR_SETTINGS_INVALID:'输入不符合支持范围，请检查格式、所选节点与独立鉴权。',
        PROXY_STORAGE_UNAVAILABLE:'服务器私有存储不可用，未确认保存或应用成功。',
        PROXY_CAPACITY_EXHAUSTED:'代理历史容量已满。本次操作未完成，原运行配置保持不变；请保留记录并联系管理员处理。',
        INVALID_STRICT_EXPORT_REQUEST:'严格导出需要准确的活动修订、正整数节点 ID 和公网 IP。',
        STRICT_EXPORT_PUBLIC_IP_REQUIRED:'严格导出仅接受公网 IP 字面量，不接受域名、URL 或私网地址；不会自动解析域名。',
        STRICT_EXPORT_RELAY_NOT_ACTIVE:'设备中转尚未提交生效，无法下载严格中转配置。',
        STRICT_EXPORT_UNQUALIFIED_INGRESS:'存在未获中转资格的启用节点，请先核对全部入口。',
        STRICT_EXPORT_INGRESS_NOT_FOUND:'指定节点不是当前已启用的合格接入节点。',
        STRICT_EXPORT_STATE_MISMATCH:'活动配置、入口或运行状态已变化，严格导出已阻止；请重新核对并应用。',
        STRICT_EXPORT_RUNTIME_UNAVAILABLE:'无法确认已提交中转仍在运行，严格导出已阻止。',
        PROXY_APPLY_UNAVAILABLE:'此服务器尚未开放应用能力。', PROXY_CHECK_UNAVAILABLE:'此服务器尚未开放检查能力。',
        PROXY_APPLY_FAILED:'应用未完成，请核对服务器状态；不要把草稿视为已生效。',
        PROXY_CHECK_FAILED:'草稿检查未通过；未证明线路可用。',
        PROXY_POLICY_REJECTED:'策略检查未通过，请查看全部启用入口的资格。未应用，也不回退直出。',
        PROXY_RUNTIME_UNAVAILABLE:'核心检查或运行条件未满足，未确认应用成功。',
        PROXY_TRANSACTION_NOT_ACTIVE:'该历史事务现在不是已提交活动事务。请核对当前状态。',
        PROXY_LOCAL_PORT_OCCUPIED:'本机端口被占用，无法应用。请更换草稿端口后重新检查。',
        PROXY_RECOVERY_REQUIRED:'服务器需要完成恢复，当前状态未确认。请保留现场并检查受控服务日志。',
        INVALID_IDEMPOTENCY_KEY:'应用事务标识无效，请重新读取服务器状态。'
    };
    let snapshot = null, capabilities = {}, busy = false, conflict = false, attempt = null, applyReview = null;
    let diagnostic = null, diagnosticBusy = false, diagnosticTimer = null, diagnosticExpiryTimer = null, diagnosticEpoch = 0, diagnosticDeadline = 0;
    let importEpoch = 0, importBusy = false, importSaving = false, importController = null, preview = null;
    let exportEpoch = 0, exportController = null;
    const downloadUrls = new Set();
    function invalidateReview() {
        applyReview=null; $('apply-review').hidden=true; $('review-coverage').replaceChildren();
        $('confirm-apply').disabled=true;
        $('review-disable-warning').hidden=true;
    }
    const element = (tag, content, attrs={}) => {
        const node = document.createElement(tag);
        if (content !== null) node.textContent = content;
        Object.entries(attrs).forEach(([key,value]) => node.setAttribute(key,value));
        return node;
    };
    const mode = settings => {
        const key = (settings.local_proxy_enabled ? '1' : '0') + (settings.relay_enabled ? '1' : '0');
        return key + ' · ' + modes[key];
    };
    const timestamp = value => typeof value==='number'&&Number.isFinite(value)?new Date(value*1000).toLocaleString():'时间未确认';
    const notice = (message, error=false) => { $('notice').textContent=message; $('notice').className=error?'error':''; };
    const importNotice = message => { $('import-notice').textContent=message; };
    function clearCredentials() { $('local-username').value=''; $('local-password').value=''; }
    function resetPreview() {
        preview=null;
        $('import-preview').hidden=true;
        $('candidates').replaceChildren(); $('ignored-sections').replaceChildren();
        $('import-notices').textContent=''; $('import-name').value=''; $('save-import').disabled=true;
    }
    function clearImport() {
        importEpoch++;
        if (importController) importController.abort();
        importController=null; importBusy=false;
        $('import-content').value=''; $('import-file').value=''; resetPreview();
        $('import-fields').disabled=false;
    }
    function closeImport() {
        clearImport();
        if ($('import-dialog').open) $('import-dialog').close();
    }
    class ApiError extends Error {
        constructor(message,status,code='',coverage=[]) { super(message); this.status=status; this.code=code; this.coverage=coverage; }
    }
    async function api(path, method='GET', data, extra={}) {
        const response=await fetch('/api/proxy'+path, {
            method, credentials:'same-origin', cache:'no-store',
            headers:{'X-VUI-Request':'1',...(data===undefined?{}:{'Content-Type':'application/json'}),...(extra.headers||{})},
            body:data===undefined?undefined:JSON.stringify(data), signal:extra.signal
        });
        if (response.status===401) {
            closeImport(); clearCredentials(); location.replace('/login');
            throw new ApiError('登录已过期，请重新登录。',401);
        }
        let body;
        try { body=await response.json(); } catch (_) { throw new ApiError('服务器响应无法确认，请刷新状态。',response.status); }
        if (!response.ok) {
            const code=typeof body.detail==='string'?body.detail:(body.detail?.code||'');
            const coverage=code==='PROXY_POLICY_REJECTED'&&Array.isArray(body.detail.coverage)?body.detail.coverage:[];
            throw new ApiError(response.status===413?'输入超过服务端大小限制。':(errors[code]||'请求未完成，请检查输入或刷新服务器状态。'),response.status,code,coverage);
        }
        return body;
    }
    function formValues() {
        return {schema_version:1,local_proxy_enabled:$('local-enabled').checked,
            relay_enabled:$('relay-enabled').checked,selected_upstream_id:$('upstream').value||null,
            local_port:Number($('local-port').value),
            local_username:$('local-username').value || (snapshot?snapshot.settings.local_username:''),
            dns_policy_version:1,deny_policy_version:1};
    }
    function isDirty() {
        if (!snapshot) return false;
        const values=formValues();
        return Object.keys(values).some(key=>values[key]!==snapshot.settings[key]) || $('local-password').value!=='';
    }
    function updateControls() {
        if (!snapshot) return;
        const values=formValues(), dirty=isDirty(), pending=!!snapshot.pending;
        $('draft-mode').textContent=mode(values);
        $('local-settings').hidden=!values.local_proxy_enabled;
        $('upstream-area').hidden=!(values.local_proxy_enabled||values.relay_enabled);
        $('draft-fields').disabled=busy||pending;
        $('refresh').disabled=busy;
        $('save').disabled=busy||pending||conflict;
        const runtime=capabilities.apply_available===true;
        const canCheck=capabilities.check_available===true||runtime;
        $('check').disabled=busy||pending||conflict||dirty||!canCheck;
        const uncertain=attempt&&attempt.state==='uncertain';
        $('apply').disabled=busy||pending||conflict||dirty||!runtime||!!uncertain||!globalThis.crypto?.getRandomValues;
        $('apply').textContent=attempt?'检查并核对新的应用事务':'检查并核对应用';
        $('retry-apply').hidden=!uncertain;
        $('retry-apply').disabled=busy||dirty||conflict||!runtime;
        $('retry-apply').textContent=uncertain?'重试已确认修订 '+attempt.revision.slice(0,12)+' 的同一次应用':'重试同一次应用';
        $('confirm-apply').disabled=busy||pending||conflict||dirty||!runtime||!!uncertain||!applyReview||applyReview.revision!==snapshot.revision;
        $('cancel-apply-review').disabled=busy;
        const diagnosticsAvailable=capabilities.diagnostics_available===true;
        $('diagnostics').disabled=busy||diagnosticBusy||diagnostic?.state==='running'||conflict||dirty||!diagnosticsAvailable||!snapshot.settings.selected_upstream_id||!$('diagnostic-consent').checked;
        $('cancel-diagnostics').hidden=diagnostic?.state!=='running';
        $('cancel-diagnostics').disabled=diagnosticBusy;
        const activeRelay=!!snapshot.active&&REVISION.test(snapshot.active.revision)&&snapshot.active.settings?.relay_enabled===true;
        const exportsAvailable=capabilities.strict_exports_available===true;
        $('export-mihomo').disabled=$('export-singbox').disabled=busy||pending||!activeRelay||!exportsAvailable;
        $('export-inbound').disabled=$('export-server').disabled=busy;
        $('export-state').textContent=!exportsAvailable?'此服务器尚未开放严格中转导出。':!activeRelay?
            '尚无已提交的设备中转活动配置，不能导出草稿。':pending?'存在未完成事务，暂时阻止导出。':
            '仅使用活动修订 '+snapshot.active.revision+'；页面编辑及较新的草稿不会进入下载。服务器仍会重新核验运行状态。';
        if (diagnostic) renderDiagnostic(diagnostic);
        $('rebase').disabled=busy||pending; $('discard').disabled=busy||pending;
        $('conflict').hidden=!conflict;
        $('save-state').textContent=conflict?'有冲突：页面编辑尚未保存。':dirty?'有未保存编辑；不会改变活动配置。':
            snapshot.active&&snapshot.active.revision===snapshot.revision?'草稿与已提交活动修订一致；这不是线路检测结果。':'草稿已保存，尚未与活动修订一致。';
        $('apply-gate').textContent=!runtime?'当前服务仅允许草稿；应用能力尚未开放。':pending?
            '服务器存在未结束事务。草稿写入与新的应用已阻止，请刷新状态或重试同一次事务。':
            dirty?'先保存页面编辑，才能检查或应用准确的草稿修订。':
            uncertain?'上次应用结果不确定。重试会沿用相同事务标识，不创建重复应用。':'应用只处理已保存修订；成功后还需重新读取活动快照确认。';
    }
    function renderSnapshot(next, preserve=false) {
        if (!next||!REVISION.test(next.revision)||!next.settings||!Array.isArray(next.upstreams)) {
            throw new Error('服务器快照格式无法确认。');
        }
        const selected=preserve?$('upstream').value:next.settings.selected_upstream_id;
        invalidateReview();
        snapshot=next;
        $('upstream').replaceChildren(element('option','请选择已保存上游',{value:''}));
        next.upstreams.forEach(profile=>$('upstream').append(element('option',
            profile.name+' · '+profile.server+':'+profile.server_port+' · '+(profile.tls?.mode||''),{value:profile.id})));
        if (selected&&!next.upstreams.some(item=>item.id===selected)) {
            $('upstream').append(element('option','此前选择的上游已失效，请重新选择',{value:selected}));
        }
        $('upstream').value=selected||'';
        if (!preserve) {
            $('local-enabled').checked=next.settings.local_proxy_enabled;
            $('relay-enabled').checked=next.settings.relay_enabled;
            $('local-port').value=next.settings.local_port;
            clearCredentials();
        }
        $('draft-state').textContent=mode(next.settings);
        $('draft-revision').textContent='修订 '+next.revision;
        $('active-state').textContent=next.active?mode(next.active.settings):'尚无已提交活动快照；当前运行状态未验证。';
        $('active-revision').textContent=next.active?'修订 '+next.active.revision:'';
        $('active-address').textContent=next.active?.pinned_address?'已提交固定地址：'+next.active.pinned_address+(next.active.resolved_at?'；解析时间：'+timestamp(next.active.resolved_at):'')+'。地址变化需明确重新应用。':'';
        $('pending-state').textContent=next.pending?'存在未结束应用事务；不表示已生效。':'没有未结束应用事务。';
        $('credential-state').textContent='用户名'+(next.settings.local_username?'已设置':'未设置')+'；密码'+(next.settings.local_password_set?'已设置':'未设置')+'。输入不回填，留空保留已保存值。';
        updateControls();
    }
    async function reloadSnapshot(preserve=isDirty()) {
        const previous=snapshot?.revision;
        const next=await api('/settings');
        if (preserve&&previous!==next.revision) conflict=true;
        renderSnapshot(next,preserve);
        return next;
    }
    async function handleConflict() {
        conflict=true;
        try { await reloadSnapshot(true); }
        catch (_) { notice('发生修订冲突，最新状态暂时无法读取。页面编辑已保留，请刷新后再核对。',true); return; }
        notice('发生修订冲突。已读取服务器最新状态，页面编辑已保留；核对前不会再次保存或应用。',true);
    }
    async function action(fn) {
        if (busy) return;
        busy=true; updateControls();
        try { await fn(); }
        catch(error) {
            if (error.code==='PROXY_REVISION_CONFLICT') await handleConflict();
            else if (error.code==='PROXY_POLICY_REJECTED') { renderCheck({coverage:error.coverage},snapshot.revision); notice(error.message,true); }
            else if (error.status!==401) notice(error instanceof ApiError?error.message:'连接或响应异常，结果未确认。请刷新服务器状态。',true);
        } finally { busy=false; updateControls(); }
    }
    function edited() {
        invalidateReview(); $('check-result').hidden=true; $('coverage').replaceChildren(); updateControls();
    }
    $('settings-form').addEventListener('input',edited);
    $('settings-form').addEventListener('change',edited);
    $('settings-form').addEventListener('submit',event=>{
        event.preventDefault();
        if (conflict||snapshot?.pending) return;
        action(async()=>{
            const values=formValues();
            if ((values.local_proxy_enabled||values.relay_enabled)&&!snapshot.upstreams.some(item=>item.id===values.selected_upstream_id)) {
                throw new ApiError('请明确选择一个有效的已保存上游。',422);
            }
            if (values.local_proxy_enabled&&(!values.local_username||(!$('local-password').value&&!snapshot.settings.local_password_set))) {
                throw new ApiError('首次启用本机代理需要独立用户名和密码。',422);
            }
            if ($('local-password').value) values.local_password=$('local-password').value;
            const result=await api('/settings','PUT',values,{headers:{'If-Match':'"'+snapshot.revision+'"'}});
            renderSnapshot(result); notice('草稿已保存；活动配置未由保存操作改变。');
        });
    });
    $('refresh').addEventListener('click',()=>action(async()=>{
        await reloadSnapshot();
        notice(conflict?'服务器状态已刷新；请先处理修订冲突。':'已重新读取服务器草稿与活动快照。',conflict);
    }));
    $('rebase').addEventListener('click',()=>{ conflict=false; edited(); notice('已保留页面编辑。请核对后明确保存，新保存仍需服务端修订检查。'); });
    $('discard').addEventListener('click',()=>{ conflict=false; renderSnapshot(snapshot); $('check-result').hidden=true; notice('页面编辑已放弃，已载入最新服务器草稿。'); });
    function ipLiteral(value) {
        if (/^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$/.test(value)) {
            return value.split('.').every(part=>String(Number(part))===part&&Number(part)<=255);
        }
        if (!value.includes(':')||!/^[0-9a-fA-F:.]+$/.test(value)) return false;
        try { return new URL('http://['+value+']/').hostname.startsWith('['); } catch (_) { return false; }
    }
    async function downloadExport(kind) {
        if (busy||capabilities.strict_exports_available!==true||snapshot?.pending||snapshot?.active?.settings?.relay_enabled!==true) return;
        const identifier=$('export-inbound').value, server=$('export-server').value;
        if (!/^[1-9][0-9]*$/.test(identifier)||!Number.isSafeInteger(Number(identifier))||!ipLiteral(server)) {
            $('export-notice').textContent='请明确填写正整数节点 ID 和公网 IP 字面量。此严格配置不接受域名、URL 或端口；公网地址由服务器再次检查。';
            return;
        }
        await action(async()=>{
            const revision=snapshot.active.revision, epoch=++exportEpoch;
            exportController=new AbortController();
            $('export-notice').textContent='正在核验活动配置与运行状态；下载内容不会预览或保存到网页存储。';
            try {
                const response=await fetch('/api/proxy/exports/'+kind, {method:'POST',credentials:'same-origin',cache:'no-store',
                    headers:{'X-VUI-Request':'1','Content-Type':'application/json'},signal:exportController.signal,
                    body:JSON.stringify({base_revision:revision,inbound_id:Number(identifier),server})});
                if (response.status===401) {
                    closeImport(); clearCredentials(); location.replace('/login');
                    throw new ApiError('登录已过期，请重新登录。',401);
                }
                if (!response.ok) {
                    let body; try { body=await response.json(); } catch (_) { body={}; }
                    const code=typeof body.detail==='string'?body.detail:'';
                    throw new ApiError(errors[code]||'严格导出未完成；请刷新并核对活动状态。',response.status,code);
                }
                if (response.headers.get('ETag')!=='"'+revision+'"') throw new Error('Unconfirmed export revision');
                const blob=await response.blob();
                if (blob.size>65536||blob.size===0) throw new Error('Unconfirmed export size');
                if (epoch!==exportEpoch) return;
                const latest=await reloadSnapshot();
                if (epoch!==exportEpoch) return;
                if (latest.pending||latest.active?.revision!==revision||latest.active?.settings?.relay_enabled!==true) {
                    throw new ApiError('下载前活动配置已变化，未交付旧配置；请核对后重新下载。',409);
                }
                const url=URL.createObjectURL(blob), anchor=element('a',null);
                downloadUrls.add(url); anchor.href=url;
                anchor.download=kind==='mihomo.yaml'?'vui-strict-relay.yaml':'vui-strict-relay.json';
                document.body.append(anchor); anchor.click(); anchor.remove();
                setTimeout(()=>{ URL.revokeObjectURL(url); downloadUrls.delete(url); },1000);
                $('export-notice').textContent='配置已交给浏览器下载，包含国内接入凭据，请妥善保管；未执行设备端到端验证。';
            } catch(error) {
                if (epoch!==exportEpoch) return;
                $('export-notice').textContent=error instanceof ApiError?error.message:'严格导出响应未确认，未交付配置；请刷新状态后重试。';
                if (error.status!==401) {
                    try { await reloadSnapshot(); } catch (_) { /* Keep the last confirmed snapshot. */ }
                }
            } finally { if (epoch===exportEpoch) exportController=null; }
        });
    }
    $('export-mihomo').addEventListener('click',()=>downloadExport('mihomo.yaml'));
    $('export-singbox').addEventListener('click',()=>downloadExport('sing-box.json'));
    function renderCheck(result, revision) {
        $('check-result').hidden=false;
        $('check-summary').textContent=result.valid===true&&result.phase==='checked'?
            '修订 '+revision.slice(0,12)+' 的入口、地址与核心配置检查通过；未进行 TLS 或业务连接测试。':
            '该草稿尚未取得完整检查结果；没有线路通过证明。';
        $('pinned-address').textContent=result.pinned_address?
            '本次检查固定地址：'+result.pinned_address+(result.resolved_at?'；解析时间：'+timestamp(result.resolved_at):'')+'。重新应用时将重新核对。':'';
        $('coverage').replaceChildren();
        const coverage=Array.isArray(result.coverage)?result.coverage:[];
        coverage.forEach(item=>$('coverage').append(element('li',
            '入口 '+String(item.inbound_id??item.id??'未知')+' · '+String(item.core??'')+' / '+String(item.protocol??'')+' / '+String(item.transport??'')+' · '+
            (item.qualified===true?'符合本期接入资格':('未获资格'+(item.reason?'：'+String(item.reason):''))))));
    }
    $('check').addEventListener('click',()=>action(async()=>{
        if (isDirty()||conflict||snapshot.pending) return;
        invalidateReview();
        const revision=snapshot.revision;
        const result=await api('/check','POST',{base_revision:revision});
        renderCheck(result,revision);
        notice('检查结果仅属于该保存修订，未证明业务线路或设备端到端可用。');
    }));
    function newKey() {
        return Array.from(crypto.getRandomValues(new Uint8Array(32)),value=>value.toString(16).padStart(2,'0')).join('');
    }
    async function apply(retry) {
        if (isDirty()||conflict||capabilities.apply_available!==true) return;
        if (retry) {
            if (!attempt||attempt.state!=='uncertain') return;
        } else {
            if (snapshot.pending||attempt?.state==='uncertain'||!applyReview||applyReview.revision!==snapshot.revision) return;
            attempt={key:newKey(),revision:snapshot.revision,state:'sending'};
        }
        invalidateReview();
        const current=attempt;
        current.state='sending'; updateControls();
        try {
            const result=await api('/apply','POST',{base_revision:current.revision},{headers:{'Idempotency-Key':current.key}});
            // A POST success is insufficient. Only a fresh GET can describe the current active state.
            const latest=await reloadSnapshot(false);
            const committed=result.applied===true&&result.phase==='committed'&&
                result.snapshot?.active?.revision===current.revision&&latest.active?.revision===current.revision&&!latest.pending;
            current.state='finished';
            notice(committed?'应用事务已提交，并已重新读取活动修订确认。线路与外部设备端到端仍须独立检测。':
                '本次请求未确认当前活动修订为该候选。请核对状态；历史事务完成不代表它现在仍生效。',!committed);
        } catch(error) {
            if (error.status===409) {
                current.state='finished';
                if (error.code!=='PROXY_REVISION_CONFLICT') {
                    try { await reloadSnapshot(false); } catch (_) { /* No new success claim without a confirmed snapshot. */ }
                }
                throw error;
            }
            // Transport/server failures may occur after commit. Retain the exact key and revision.
            current.state=error.status>=400&&error.status<500?'finished':'uncertain';
            if (current.state==='uncertain') {
                try { await reloadSnapshot(false); } catch (_) { /* Keep last verified snapshot, never invent success. */ }
                notice('应用结果不确定。请刷新状态或重试同一次应用；重试沿用同一事务标识与候选修订。',true);
            } else { throw error; }
        }
    }
    $('apply').addEventListener('click',()=>action(async()=>{
        if (isDirty()||conflict||snapshot.pending||attempt?.state==='uncertain'||capabilities.apply_available!==true) return;
        invalidateReview();
        const revision=snapshot.revision;
        const result=await api('/check','POST',{base_revision:revision});
        if (result.valid!==true||result.phase!=='checked'||result.applied!==false||result.revision!==revision||
            result.snapshot?.revision!==revision||!Array.isArray(result.coverage)||snapshot.revision!==revision) {
            throw new ApiError('无法确认同一修订的完整预检结果，未开放应用确认。请刷新后重新检查。',409);
        }
        renderSnapshot(result.snapshot,true);
        renderCheck(result,revision);
        if (snapshot.pending||isDirty()) return;
        applyReview={revision};
        $('review-revision').textContent='待确认修订 '+revision;
        $('review-mode').textContent='待应用组合：'+mode(snapshot.settings);
        const selected=snapshot.upstreams.find(value=>value.id===snapshot.settings.selected_upstream_id);
        $('review-upstream').textContent=selected?'所选上游：'+selected.name+' · '+selected.server+':'+selected.server_port+' · '+(selected.tls?.mode||''):'没有使用中的上游。';
        $('review-listener').textContent=snapshot.settings.local_proxy_enabled?
            '本机入口：127.0.0.1:'+snapshot.settings.local_port+'，使用独立鉴权；不改变系统代理。':'本机显式代理入口关闭，不监听本机代理端口。';
        $('review-disable-warning').hidden=!(snapshot.active?.settings?.relay_enabled===true&&snapshot.settings.relay_enabled===false);
        result.coverage.forEach(item=>$('review-coverage').append(element('li',
            '入口 '+String(item.inbound_id)+' · '+String(item.core)+' / '+String(item.protocol)+' / '+String(item.transport)+' · '+
            (item.qualified===true?'符合本期中转资格':'未获本期中转资格')+(snapshot.settings.relay_enabled?'；将应用中转策略':'；保留普通出口行为'))));
        if (!result.coverage.length) $('review-coverage').append(element('li','没有启用的设备接入节点。'));
        $('apply-review').hidden=false;
        notice('同一保存修订的配置预检已返回。请核对影响，再点击独立确认按钮；尚未应用。');
    }));
    $('confirm-apply').addEventListener('click',()=>action(()=>apply(false)));
    $('cancel-apply-review').addEventListener('click',()=>{ invalidateReview(); updateControls(); notice('应用核对已取消，未发送新的应用事务。'); });
    $('retry-apply').addEventListener('click',()=>action(()=>apply(true)));
    function inputWithinLimit() {
        return new TextEncoder().encode($('import-content').value).byteLength<=MAX_INPUT_BYTES;
    }
    function selectionReady() {
        const candidate=$('candidates').querySelector('input:checked');
        const acknowledgements=Array.from($('ignored-sections').querySelectorAll('input'));
        $('save-import').disabled=importBusy||!preview||!candidate||!acknowledgements.every(input=>input.checked);
    }
    $('open-import').addEventListener('click',()=>{
        invalidateReview(); clearImport(); $('import-format').value='uri'; importNotice(''); $('import-dialog').showModal();
    });
    function cancelImport() {
        if (importSaving) notice('本地输入已清空；正在发送的保存可能已到达服务器，请刷新上游列表核对。',true);
        importSaving=false; closeImport();
    }
    $('close-import').addEventListener('click',cancelImport);
    $('import-dialog').addEventListener('cancel',event=>{ event.preventDefault(); cancelImport(); });
    $('import-dialog').addEventListener('close',()=>{ if (!$('import-dialog').open) clearImport(); });
    $('import-content').addEventListener('input',()=>{
        importEpoch++; $('import-file').value=''; resetPreview();
        if (!inputWithinLimit()) { $('import-content').value=''; $('import-file').value=''; importNotice('输入超过 1 MiB，已清空；没有发送到服务器。'); }
    });
    $('import-format').addEventListener('change',()=>{ resetPreview(); importNotice('格式已变更，请重新预览。'); });
    $('import-file').addEventListener('change',async()=>{
        resetPreview(); $('import-content').value='';
        const file=$('import-file').files[0], epoch=++importEpoch;
        if (!file) return;
        if (file.size>MAX_INPUT_BYTES) { $('import-file').value=''; importNotice('文件超过 1 MiB，没有读取或发送到服务器。'); return; }
        try {
            const content=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());
            if (epoch!==importEpoch||!$('import-dialog').open) return;
            $('import-content').value=content;
            if (!inputWithinLimit()) throw new Error();
            importNotice('文件已临时读取。请选择正确格式后预览。');
        } catch (_) {
            if (epoch!==importEpoch) return;
            $('import-content').value=''; $('import-file').value=''; importNotice('文件必须是有效 UTF-8，且不超过 1 MiB。');
        }
    });
    function showPreview(value) {
        preview=value; $('import-preview').hidden=false;
        value.candidates.forEach(candidate=>{
            const label=element('label',null,{class:'candidate'+(candidate.supported?'':' unsupported')});
            const radio=element('input',null,{type:'radio',name:'candidate',value:String(candidate.index)});
            radio.disabled=candidate.supported!==true;
            radio.addEventListener('change',selectionReady);
            const description=element('span',candidate.name||'未命名候选');
            description.append(element('small',(candidate.protocol||'')+' / '+(candidate.transport||'')+' / '+(candidate.tls_mode||'')+' · '+(candidate.server||'')+':'+(candidate.server_port??'')));
            if (!candidate.supported) description.append(element('small','不支持：'+(candidate.reason||candidate.reason_code||'超出本期范围')));
            label.append(radio,description); $('candidates').append(label);
        });
        if (!value.candidates.length) $('candidates').append(element('p','没有可选择的候选节点。'));
        if (value.ignored_sections.length) $('ignored-sections').append(element('h3','逐项确认不导入的区块'));
        value.ignored_sections.forEach(section=>{
            const label=element('label',null,{class:'check'}), input=element('input',null,{type:'checkbox',value:section});
            input.addEventListener('change',selectionReady); label.append(input,element('span','确认不导入 '+section)); $('ignored-sections').append(label);
        });
        $('import-notices').textContent=value.notice_codes?.length?'完整客户端配置不会迁移；远程 provider 不会读取。':'';
        selectionReady();
    }
    $('preview-import').addEventListener('click',async()=>{
        if (importBusy) return;
        if (!$('import-content').value||!inputWithinLimit()) { importNotice('请输入不超过 1 MiB 的配置。'); return; }
        resetPreview();
        const epoch=++importEpoch;
        importBusy=true; importController=new AbortController(); $('import-fields').disabled=true;
        try {
            const value=await api('/import/preview','POST',{format:$('import-format').value,content:$('import-content').value},{signal:importController.signal});
            if (epoch!==importEpoch||!$('import-dialog').open) return;
            showPreview(value); importNotice('预览完成。必须明确选择候选；没有自动选中，也未保存或连接。');
        } catch(error) {
            if (epoch!==importEpoch) return;
            $('import-content').value=''; $('import-file').value='';
            importNotice(error instanceof ApiError?error.message:'预览未完成，原文已清空。');
        } finally {
            if (epoch===importEpoch) { importBusy=false; importController=null; $('import-fields').disabled=false; selectionReady(); }
        }
    });
    $('import-form').addEventListener('submit',async event=>{
        event.preventDefault();
        if (importBusy||busy||conflict||snapshot.pending||!preview||$('save-import').disabled) return;
        const selected=$('candidates').querySelector('input:checked');
        if (!selected||!inputWithinLimit()) return;
        const epoch=++importEpoch;
        importBusy=true; importController=new AbortController(); $('import-fields').disabled=true;
        importSaving=true;
        const payload={format:$('import-format').value,content:$('import-content').value,candidate_index:Number(selected.value),
            acknowledged_ignored_sections:Array.from($('ignored-sections').querySelectorAll('input:checked'),input=>input.value)};
        if ($('import-name').value) payload.name=$('import-name').value;
        try {
            const result=await api('/profiles','POST',payload,{headers:{'If-Match':'"'+snapshot.revision+'"'},signal:importController.signal});
            if (epoch!==importEpoch) return;
            renderSnapshot(result.snapshot,true); closeImport();
            notice('上游已保存，原文已清空。请明确选择上游并保存草稿；尚未应用。');
        } catch(error) {
            if (epoch!==importEpoch) return;
            closeImport();
            if (error.code==='PROXY_REVISION_CONFLICT') await handleConflict();
            else if (error.code==='PROXY_POLICY_REJECTED') { renderCheck({coverage:error.coverage},snapshot.revision); notice(error.message,true); }
            else if (error.status!==401) notice(error instanceof ApiError?error.message:'保存结果未确认，原文已清空。请刷新上游列表后再决定是否重新导入。',true);
        } finally { importSaving=false; payload.content=''; }
    });
    const stageNames={configuration:'配置检查',tcp:'上游 TCP',tls_or_reality:'TLS / REALITY',proxy_forwarding:'代理转发',https_exit:'HTTPS 与出口 IP（同一次请求）',dns:'业务 DNS',external_client:'外部设备端到端'};
    const diagnosticStates={running:'运行中',completed:'已结束',failed:'失败',cancelled:'已取消',expired:'证据已过期'};
    const stageStates={passed:'通过',failed:'失败',not_tested:'未测',insufficient_evidence:'证据不足',expired:'证据已过期'};
    function renderDiagnostic(job) {
        $('diagnostic-result').hidden=false;
        const expired=job.state==='expired'||(typeof job.expires_at==='number'&&Date.now()>=job.expires_at*1000);
        const stale=job.stale===true||job.revision!==snapshot?.revision;
        clearTimeout(diagnosticExpiryTimer);
        if (!expired&&typeof job.expires_at==='number') {
            diagnosticExpiryTimer=setTimeout(updateControls,Math.min(2147483647,Math.max(1,job.expires_at*1000-Date.now()+1)));
        }
        $('diagnostic-summary').textContent='修订 '+String(job.revision).slice(0,12)+' · '+(expired?'证据已过期':diagnosticStates[job.state]||'状态未确认')+
            (stale?' · 与当前草稿不一致，不能沿用结果':isDirty()?' · 未保存编辑不属于本次检测':'');
        $('diagnostic-endpoint').textContent=(job.pinned_address?'本次固定地址：'+job.pinned_address+'；':'')+
            (job.resolved_at?'解析时间：'+timestamp(job.resolved_at)+'；':'')+(job.exit_ip?'ipify 观察到的出口 IP：'+job.exit_ip+'。':'尚无有效出口 IP 结果。');
        $('diagnostic-stages').replaceChildren();
        Object.entries(stageNames).forEach(([id,name])=>{
            const stage=(Array.isArray(job.stages)?job.stages:[]).find(item=>item.stage===id);
            // External end-to-end requires an independent observer, absent from this API.
            let status=id==='external_client'?'not_tested':stage?.status||'not_tested';
            if ((stale||expired)&&status!=='not_tested') status='expired';
            // DNS_PATH_BOUND_TO_HTTPS is not an independent DNS probe.
            if (id==='dns'&&stage?.code==='DNS_PATH_BOUND_TO_HTTPS') status=stale||expired?'expired':'insufficient_evidence';
            const line=element('li',name+'：'+(stageStates[status]||'未测'),{'data-stage':id,'data-status':status});
            if (id==='dns') line.append(element('small','经 HTTPS 转发的间接证据不能代替独立 DNS 检测。'));
            $('diagnostic-stages').append(line);
        });
        $('diagnostic-state').textContent='服务器线路诊断：'+(diagnosticStates[job.state]||'状态未确认')+'，请分别查看各层证据';
        $('external-state').textContent='外部设备端到端：未测，需要独立外部观察者';
    }
    function scheduleDiagnostic(epoch) {
        clearTimeout(diagnosticTimer);
        if (diagnostic?.state!=='running') return;
        if (Date.now()>diagnosticDeadline) {
            $('diagnostic-notice').textContent='已超过本次任务的预期运行期限，结果仍未确认。请取消或刷新服务器状态；不能视为通过。';
            return;
        }
        diagnosticTimer=setTimeout(async()=>{
            if (epoch!==diagnosticEpoch) return;
            try {
                const job=await api('/diagnostics/'+encodeURIComponent(diagnostic.job_id));
                if (epoch!==diagnosticEpoch) return;
                diagnostic=job; updateControls(); scheduleDiagnostic(epoch);
            } catch(error) {
                if (epoch!==diagnosticEpoch) return;
                $('diagnostic-notice').textContent='诊断状态暂时无法读取；未确认完成。可取消或刷新后重新核对。';
                // The server task remains bounded; do not turn a failed poll into a pass.
                if (error.status!==401&&error.status!==404) scheduleDiagnostic(epoch);
            }
        },1200);
    }
    $('diagnostic-consent').addEventListener('change',updateControls);
    $('diagnostics').addEventListener('click',async()=>{
        if ($('diagnostics').disabled) return;
        diagnosticBusy=true; diagnosticDeadline=Date.now()+35000; const epoch=++diagnosticEpoch; updateControls();
        try {
            const job=await api('/diagnostics','POST',{upstream_id:snapshot.settings.selected_upstream_id,base_revision:snapshot.revision});
            if (epoch!==diagnosticEpoch) return;
            diagnostic=job;
            $('diagnostic-notice').textContent='检测仅属于所选上游与修订；代理转发和 HTTPS 出口来自同一次 ipify 请求。';
            scheduleDiagnostic(epoch);
        } catch(error) {
            if (epoch!==diagnosticEpoch) return;
            if (error.code==='PROXY_REVISION_CONFLICT') await handleConflict();
            $('diagnostic-notice').textContent=error instanceof ApiError?error.message:'诊断启动结果不确定，请等待服务器任务期限后再检查。';
        } finally { if (epoch===diagnosticEpoch) { diagnosticBusy=false; updateControls(); } }
    });
    $('cancel-diagnostics').addEventListener('click',async()=>{
        if (diagnosticBusy||diagnostic?.state!=='running') return;
        diagnosticBusy=true; const epoch=++diagnosticEpoch; clearTimeout(diagnosticTimer); updateControls();
        try {
            const job=await api('/diagnostics/'+encodeURIComponent(diagnostic.job_id),'DELETE');
            if (epoch!==diagnosticEpoch) return;
            diagnostic=job; $('diagnostic-notice').textContent='取消请求已返回，请以任务状态为准。';
            scheduleDiagnostic(epoch);
        } catch(error) {
            if (epoch!==diagnosticEpoch) return;
            $('diagnostic-notice').textContent='取消结果未确认；任务仍受服务器时限约束。'; scheduleDiagnostic(epoch);
        } finally { if (epoch===diagnosticEpoch) { diagnosticBusy=false; updateControls(); } }
    });
    function leave() {
        invalidateReview();
        closeImport(); clearCredentials(); clearTimeout(diagnosticTimer); clearTimeout(diagnosticExpiryTimer); diagnosticEpoch++;
        exportEpoch++; if (exportController) exportController.abort(); exportController=null;
        downloadUrls.forEach(url=>URL.revokeObjectURL(url)); downloadUrls.clear();
        if (diagnostic?.state==='running') {
            fetch('/api/proxy/diagnostics/'+encodeURIComponent(diagnostic.job_id),{method:'DELETE',credentials:'same-origin',cache:'no-store',keepalive:true,headers:{'X-VUI-Request':'1'}}).catch(()=>{});
        }
    }
    addEventListener('pagehide',leave);
    addEventListener('popstate',leave);
    addEventListener('pageshow',event=>{ if (event.persisted) { leave(); location.reload(); } });
    action(async()=>{
        closeImport(); clearCredentials();
        capabilities=await api('/capabilities');
        renderSnapshot(await api('/settings'));
        $('diagnostic-state').textContent=capabilities.diagnostics_available===true?'服务器线路诊断：尚未执行':'服务器线路诊断：未开放，未测';
        $('diagnostic-notice').textContent=capabilities.diagnostics_available===true?'每次诊断绑定一个已保存上游与修订，最长 30 秒；每管理员一次，启动间隔至少 30 秒。':'线路诊断暂不可用。';
        $('stage').textContent=capabilities.apply_available===true?'草稿与事务应用':'仅草稿';
        $('proxy-workspace').hidden=false;
        notice('已读取草稿与活动快照；保存不等于应用，配置检查不等于线路通过。');
    });
})();
