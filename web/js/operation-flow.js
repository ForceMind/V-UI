'use strict';
// Three user intents over the existing guarded APIs. No transport credentials,
// URL state, browser storage, root commands or implicit DIRECT fallback here.
((root, factory) => {
    const api = factory(root.crypto || (typeof require === 'function' ? require('node:crypto').webcrypto : null));
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.VUI_OPERATION_FLOW = api;
})(globalThis, crypto => {
    const REV = /^[0-9a-f]{64}$/;
    const copy = value => JSON.parse(JSON.stringify(value));
    const stable = value => JSON.stringify(value, (_, item) => item && !Array.isArray(item) && typeof item === 'object' ? Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])) : item);
    const same = (a,b) => stable(a) === stable(b);
    class FlowError extends Error {
        constructor(code, message, data = null) { super(message); this.name = 'FlowError'; this.code = code; this.data = data; }
    }
    const fail = (code, message, data) => { throw new FlowError(code, message, data); };
    const hex = size => {
        if (!crypto?.getRandomValues) fail('CRYPTO_UNAVAILABLE', '当前页面无法安全生成操作标识，请使用HTTPS重新打开。');
        return Array.from(crypto.getRandomValues(new Uint8Array(size)), value => value.toString(16).padStart(2, '0')).join('');
    };
    const settings = source => Object.fromEntries(Object.entries(source).filter(([key]) => key !== 'local_password_set'));
    const nodeState = row => row?.routing_status?.state === 'applied';
    const usableCertificates = (rows, now = Date.now() / 1000) => rows.filter(row => row && row.environment === 'production' &&
        typeof row.id === 'string' && row.id && typeof row.domain === 'string' && row.domain &&
        typeof row.revision === 'string' && row.revision && Number.isFinite(row.days_remaining) && row.days_remaining >= 0 &&
        (row.not_after == null || typeof row.not_after === 'number' && row.not_after > now));
    const suggestedPort = (rows, start = 10443) => {
        const occupied = new Set(rows.map(row => row.port));
        for (let port = start; port <= 65535; port++) if (!occupied.has(port)) return port;
        return null;
    };
    const errors = {
        NODE_CONTENT_CHANGED:'节点设置已被其他操作修改，请刷新后重新选择，原配置保持。',
        SHARE_NODE_CHANGED:'节点已变化，请重新读取后分享。',
        PROXY_REVISION_CONFLICT:'连接设置已被其他会话修改，请重新读取后确认。',
        PROXY_RECOVERY_REQUIRED:'服务器有待恢复操作，请先处理，当前运行设置不会被覆盖。',
        PROXY_RUNTIME_UNAVAILABLE:'服务器当前运行条件不满足，请重新检查节点组状态。',
        PROXY_POLICY_REJECTED:'当前节点组合不支持这条路线，请查看需要处理的节点。',
        PROXY_LOCAL_PORT_OCCUPIED:'服务器本地代理端口被占用，请在更多设置中换一个端口。',
        UPSTREAM_OR_SETTINGS_INVALID:'连接配置不受支持，请使用对端导出的TCP出口链接。',
        IGNORED_SECTIONS_ACKNOWLEDGEMENT_REQUIRED:'连接包含额外配置，请逐项确认后再继续。',
        LOCAL_PROXY_NOT_ACTIVE:'服务器程序代理尚未开启，先开启后再获取连接信息。',
        LOCAL_PROXY_STATE_MISMATCH:'代理运行状态已变化，请刷新后重新获取连接信息。'
    };
    function message(error) {
        if (error instanceof FlowError) return error.message;
        if (error.status === 401) return '登录或维护授权已失效，请重新获取授权。';
        if (error.status === 403) return '当前授权没有这项操作权限，请联系管理员调整授权。';
        const detail = error.detail, code = typeof detail === 'string' ? detail : detail?.code;
        return errors[code] || '操作未完成，已保存的数据保持。请读取状态后继续，不要重复创建。';
    }
    class Flow {
        constructor(request, {scopes = null, changed = () => {}, expired = () => {}} = {}) {
            this.request = request; this.scopes = scopes; this.changed = changed; this.expired = expired;
            this.generation = 0; this.busy = false; this.snapshot = null; this.nodes = [];
            this.cores = {}; this.certificates = []; this.attempt = null; this.intent = null;
            this.ownedRevision = null; this.baselineRevision = null; this.generatedAuth = null;
        }
        has(...scopes) { return this.scopes === null || scopes.every(scope => this.scopes.includes(scope)); }
        require(...scopes) { if (!this.has(...scopes)) fail('PERMISSION', '这项操作需要：' + scopes.map(scope => ({nodes:'节点管理',relay:'出口配置',certificates:'证书管理',share:'分享连接',destructive:'删除与恢复'}[scope] || '对应操作权限')).join('、') + '。请联系管理员调整维护授权。'); }
        async api(path, method = 'GET', body = null, headers = {}, fullResponse = false) {
            const generation = this.generation;
            try {
                const value = await this.request(path, method, body, {headers,fullResponse});
                if (generation !== this.generation) fail('ENDED', '页面已离开，本次后续操作已停止。');
                return value;
            } catch (error) {
                if (error.status === 401) { this.destroy(); this.expired(); }
                throw error;
            }
        }
        destroy() {
            this.generation++; this.generatedAuth = null; this.intent = null; this.attempt = null;
            this.ownedRevision = null; this.baselineRevision = null;
        }
        stage(text) { this.changed({stage:text}); }
        async load() {
            const [snapshot, nodes, cores, certs] = await Promise.all([
                this.api('/api/proxy/settings'), this.api('/api/inbounds'),
                this.api('/api/cores/status'), this.api('/api/certificates')]);
            if (!snapshot || !REV.test(snapshot.revision) || !snapshot.settings || !Array.isArray(snapshot.upstreams) ||
                !Array.isArray(nodes) || !Array.isArray(certs) || !cores) fail('INVALID_STATE', '服务器状态读取不完整，请重试。');
            this.snapshot = snapshot; this.nodes = nodes; this.cores = cores; this.certificates = usableCertificates(certs);
            this.changed({loaded:true}); return snapshot;
        }
        clean() {
            const s = this.snapshot;
            if (s.pending) fail('PENDING', '服务器有尚未结束的操作，请先核对结果。');
            if (s.active && s.active.revision !== s.revision && !(this.ownedRevision === s.revision && s.active.revision === this.baselineRevision))
                fail('UNKNOWN_DRAFT', '发现上次尚未启用的更改。请先查看，选择继续处理或恢复当前运行设置。');
            if (!s.active) {
                const v = s.settings;
                if (v.local_proxy_enabled || v.relay_enabled || v.selected_upstream_id !== null || s.upstreams.length ||
                    v.local_port !== 7891 || v.local_username !== '' || v.local_password_set)
                    fail('UNKNOWN_DRAFT', '有尚未启用的连接设置，请先处理，不能顺便应用到本次操作。');
            }
        }
        relayAllowed() {
            const xray = this.cores.xray;
            if (!xray || typeof xray.enabled !== 'boolean' || typeof xray.running !== 'boolean')
                fail('CORE_UNKNOWN', '未能确认旧节点组状态，请重新读取。');
            if (xray.enabled || xray.running) fail('XRAY_CONFLICT', '现有Xray节点组处于启用或运行状态，本版本不能同时开启中转。旧节点保持不变。',
                this.nodes.filter(row => row.core === 'xray' && row.enable));
        }
        affected() {
            const globalRelay = this.snapshot.settings.relay_enabled;
            return this.nodes.filter(row => row.enable && (row.routing_mode === 'relay' || row.routing_mode === 'inherit' && globalRelay));
        }
        impactStamp() {
            return JSON.stringify([this.snapshot.revision, this.snapshot.settings.local_proxy_enabled,
                this.affected().map(row => [row.id, row.mutation_revision, row.content_revision||null]).sort((a,b) => a[0]-b[0])]);
        }
        needsImpact(choice) {
            const selected=!!choice.link||this.snapshot.upstreams.some(row=>row.id===choice.upstreamId);
            return selected && !!(choice.link || choice.upstreamId !== this.snapshot.settings.selected_upstream_id) &&
                (this.snapshot.settings.local_proxy_enabled || this.affected().length > 0);
        }
        async checkApply(snapshot = this.snapshot) {
            this.require('relay'); this.stage('正在检查连接设置');
            const checked = await this.api('/api/proxy/check', 'POST', {base_revision:snapshot.revision});
            if (!checked || checked.valid !== true || checked.applied !== false || checked.phase !== 'checked' ||
                checked.revision !== snapshot.revision || checked.snapshot?.revision !== snapshot.revision || !REV.test(checked.source_fingerprint) || !REV.test(checked.runtime_fingerprint))
                fail('CHECK_UNCONFIRMED', '检查结果未能确认，请重新读取。尚未启动新操作。');
            if(this.intent?.choice?.startStopped){
                const current=await this.api('/api/inbounds'), ownID=this.intent.node?.id;
                const others=current.filter(row=>row.id!==ownID&&row.core==='sing-box'&&row.enable).map(row=>[row.id,row.mutation_revision,row.content_revision||null]).sort((a,b)=>a[0]-b[0]);
                if(!same(others,this.intent.startRows))fail('START_SCOPE_CHANGED','将恢复的节点发生变化，请重新确认，未启动新操作。');
            }
            this.attempt = {key:hex(32), revision:snapshot.revision, fingerprint:checked.source_fingerprint, runtimeFingerprint:checked.runtime_fingerprint, state:'sending'};
            return this.sendAttempt();
        }
        async sendAttempt() {
            const a = this.attempt; this.stage('正在启用连接设置');
            try {
                const result = await this.api('/api/proxy/apply', 'POST', {base_revision:a.revision, source_fingerprint:a.fingerprint, runtime_fingerprint:a.runtimeFingerprint}, {'Idempotency-Key':a.key});
                const latest = await this.api('/api/proxy/settings');
                if (result?.applied !== true || result.phase !== 'committed' || result.snapshot?.active?.revision !== a.revision ||
                    latest?.active?.revision !== a.revision || latest.pending) fail('APPLY_UNCONFIRMED', '请求已返回，但当前运行结果未能确认，请核对。');
                const cores=await this.api('/api/cores/status'), core=cores['sing-box'];
                const needsListener=latest.active.settings.local_proxy_enabled||this.nodes.some(row=>row.core==='sing-box'&&row.enable)||!!this.intent?.node?.enable;
                if(!core||core.dirty||core.applied_revision!==latest.active.core_revision||needsListener&&(!core.running||!core.enabled)){
                    a.state='finished';this.snapshot=latest;this.cores=cores;
                    fail('RUNTIME_CHANGED','设置曾经提交，但运行状态后来已停止或变化。请重新确认，不会自动恢复旧操作。');
                }
                a.state = 'finished'; this.snapshot = latest; this.cores=cores; this.ownedRevision = null;
                this.baselineRevision = latest.revision; this.changed({applied:true}); return latest;
            } catch (error) {
                if (error instanceof FlowError && ['ENDED','RUNTIME_CHANGED'].includes(error.code)) throw error;
                a.state = !Number.isInteger(error.status) || error.status < 400 || error.status >= 500 ? 'uncertain' : 'finished';
                if (a.state === 'uncertain') fail('APPLY_UNCERTAIN', '操作已提交，结果暂未确认。点击“核对并继续”，不会创建新的应用任务。');
                throw error;
            }
        }
        async ensureBaseline() {
            if (this.snapshot.active) return;
            this.stage('正在准备首次节点设置'); await this.checkApply();
        }
        async inspectLink(choice) {
            if (!choice.link) return null;
            if (new TextEncoder().encode(choice.link).byteLength > 1048576) fail('INPUT_LIMIT', '连接内容太长，请使用单个TCP出口链接。');
            const preview = await this.api('/api/proxy/import/preview', 'POST', {format:choice.format || 'uri', content:choice.link});
            if (!Array.isArray(preview?.candidates) || !Array.isArray(preview.ignored_sections)) fail('INVALID_PREVIEW', '连接预览不完整，请重新粘贴。');
            const supported = preview.candidates.filter(item => item.supported === true);
            if (!supported.length) fail('UNSUPPORTED_LINK', '没有受支持的TCP出口。请在对端节点选择“给中转服务器使用（仅TCP）”。', preview);
            if (choice.candidateIndex == null && supported.length === 1) choice.candidateIndex = supported[0].index;
            if (!supported.some(item => item.index === choice.candidateIndex)) fail('CHOOSE_CANDIDATE', '连接包含多个节点，请选择要使用的出口。', preview);
            const acknowledgements = choice.ignored || [];
            if (preview.ignored_sections.some(value => !acknowledgements.includes(value)) || acknowledgements.length !== preview.ignored_sections.length)
                fail('ACK_SECTIONS', '这份配置还包含其他内容，请确认哪些不导入。', preview);
            return preview;
        }
        async upstream(choice) {
            if (!choice.link) {
                if (!this.snapshot.upstreams.some(row => row.id === choice.upstreamId)) fail('CHOOSE_UPSTREAM', '请先选择出口服务器，或粘贴对端连接链接。');
                return choice.upstreamId;
            }
            if (this.intent.importUncertain) fail('IMPORT_UNCERTAIN','出口保存结果待确认，请先核对，不会重复导入。');
            this.stage('正在保存所选出口');
            this.intent.importBefore = copy(this.snapshot);
            this.intent.importPreview = await this.inspectLink(choice);
            this.intent.importUncertain = true;
            let value;
            try { value = await this.api('/api/proxy/profiles', 'POST', {format:choice.format || 'uri', content:choice.link,
                candidate_index:choice.candidateIndex, acknowledged_ignored_sections:choice.ignored || []}, {'If-Match':'"'+this.snapshot.revision+'"'}); } catch(error) { if(this.intent&&error.status>=400&&error.status<500)this.intent.importUncertain=false;throw error; }
            if (!value?.snapshot || !value.upstream_id || !REV.test(value.snapshot.revision)) fail('IMPORT_UNCERTAIN', '出口保存结果未确认，请读取服务器状态，不要重复导入。');
            this.snapshot = value.snapshot; this.ownedRevision = value.snapshot.revision; this.intent.importUncertain=false;
            choice.upstreamId = value.upstream_id; choice.link = ''; this.changed({imported:true}); return value.upstream_id;
        }
        async saveSettings(values, password = null) {
            if (same(values, settings(this.snapshot.settings)) && password === null) return;
            const body = {...values}; if (password !== null) body.local_password = password;
            if (this.intent) this.intent.saveUncertain=true;
            let next;
            try { next = await this.api('/api/proxy/settings', 'PUT', body, {'If-Match':'"'+this.snapshot.revision+'"'}); }
            catch(error) { if(this.intent&&error.status>=400&&error.status<500)this.intent.saveUncertain=false;throw error; }
            if (!REV.test(next?.revision)) fail('SETTINGS_UNCERTAIN', '设置保存结果未确认，请重新读取，不要重复提交。');
            if(this.intent)this.intent.saveUncertain=false;
            this.snapshot = next; this.ownedRevision = next.revision;
        }
        stoppedStamp() {
            return JSON.stringify(this.nodes.filter(row => row.core === 'sing-box' && row.enable).map(row => [row.id,row.mutation_revision,row.content_revision||null]).sort((a,b)=>a[0]-b[0]));
        }
        validateStartup(choice) {
            const core=this.cores['sing-box'];
            if (!core || typeof core.running!=='boolean' || typeof core.enabled!=='boolean')fail('CORE_UNKNOWN','节点组状态未确认，请重新读取。');
            const stopped=!core.running&&!core.enabled&&core.applied_revision&&this.nodes.some(row=>row.core==='sing-box'&&row.enable);
            if(stopped){
                if(!choice.startStopped||choice.startStamp!==this.stoppedStamp())fail('CORE_STOPPED','这个节点组被手动停止，启动会恢复其中已启用的节点，请核对后确认。',this.nodes.filter(row=>row.core==='sing-box'&&row.enable));
                this.require('nodes');
            }
        }
        validateNode(choice) {
            if (typeof choice.name !== 'string' || Array.from(choice.name).length > 128 || /[\u0000-\u001f\u007f]/.test(choice.name))
                fail('NAME_INVALID', '名称最多128个字符，不支持换行或控制字符。');
            if (!Number.isInteger(choice.port) || choice.port < 1024 || choice.port > 65535) fail('PORT_INVALID', '请填写1024–65535之间的整数端口。');
            if (this.nodes.some(row => row.port === choice.port)) fail('PORT_USED', '这个端口已有节点使用，请使用建议端口或另选一个。');
            const cert = this.certificates.find(row => row.id === choice.certificateId);
            if (!cert) fail('CERTIFICATE_REQUIRED', '请选择一个可用的正式证书域名。');
            const core = this.cores['sing-box'];
            const stopped = core && !core.running && !core.enabled && core.applied_revision && this.nodes.some(row => row.core === 'sing-box' && row.enable);
            if (stopped && (!choice.startStopped || choice.startStamp !== this.stoppedStamp())) fail('CORE_STOPPED', '这个节点组被手动停止。启动会同时恢复其中已启用的节点，请明确确认。',
                this.nodes.filter(row => row.core === 'sing-box' && row.enable));
            return cert;
        }
        async run(task, supplied) {
            if (this.busy) fail('BUSY', '操作正在进行，请等待结果。');
            if (this.attempt?.state === 'uncertain') fail('APPLY_UNCERTAIN', '先核对上次操作结果，不能发起新的应用。');
            if (this.intent?.importUncertain || this.intent?.saveUncertain) fail('SETTINGS_UNCERTAIN','先核对上次设置保存结果，不能重复提交。');
            if (this.intent?.nodeSent) fail('NODE_UNCERTAIN', '创建请求已经提交，请核对结果，不能重复创建。');
            this.busy = true;
            if (!['direct','relay','host','exit'].includes(task) || !REV.test(supplied?.revision || '')) { this.busy=false; fail('INVALID_INTENT','请先读取当前设置，再选择要做的操作。'); }
            const choice = copy(supplied), expected = supplied.revision;
            try {
                this.require('relay'); if (task === 'direct' || task === 'relay') this.require('nodes', 'certificates');
                this.stage('正在读取实际状态'); await this.load();
                if (expected && this.snapshot.revision !== expected) fail('CHANGED', '服务器设置已变化，请重新确认本次选择。');
                this.clean(); this.baselineRevision = this.snapshot.active?.revision || this.snapshot.revision;
                if (task === 'relay' || this.snapshot.settings.relay_enabled || this.affected().length) this.relayAllowed();
                this.validateStartup(choice);
                const cert = task === 'direct' || task === 'relay' ? this.validateNode(choice) : null;
                if (task !== 'direct') {
                    if (this.needsImpact(choice) && choice.impactStamp !== this.impactStamp())
                        fail('CONFIRM_IMPACT', '更换统一出口会影响现有中转节点或本机代理，请先核对影响并确认。');
                    await this.inspectLink(choice);
                }
                this.intent = {task, choice, nodeSent:false, node:null, startRows:this.nodes.filter(row=>row.core==='sing-box'&&row.enable).map(row=>[row.id,row.mutation_revision,row.content_revision||null]).sort((a,b)=>a[0]-b[0])};
                await this.ensureBaseline();
                if (task !== 'direct') {
                    const id = await this.upstream(choice), values = settings(this.snapshot.settings);
                    values.selected_upstream_id = id;
                    let password = null;
                    if (task === 'host') {
                        values.local_proxy_enabled = true;
                        if (!this.snapshot.settings.local_password_set) {
                            this.generatedAuth = {username:'vui_'+hex(4), password:hex(24)};
                            values.local_username = this.generatedAuth.username; password = this.generatedAuth.password;
                        }
                        if (choice.localPort != null) values.local_port = choice.localPort;
                    }
                    await this.saveSettings(values, password);
                    if (this.snapshot.active?.revision !== this.snapshot.revision || task==='host'&&!this.cores['sing-box'].running) await this.checkApply();
                }
                if (task === 'exit') { this.stage('出口设置已完成');return {kind:'exit',upstreamId:this.snapshot.settings.selected_upstream_id}; }
                if (task === 'host') { const live=await this.api('/api/cores/status');if(!live['sing-box']?.running||live['sing-box'].dirty)fail('HOST_NOT_RUNNING','设置已保存，但本机代理尚未确认启动，请核对，不要重复提交。');this.cores=live; this.stage('服务器程序代理已开启'); this.generatedAuth = null; return {kind:'host', revision:this.snapshot.active.revision}; }
                const payload = {core:'sing-box', protocol:'vless', remark:choice.name, port:choice.port, enable:true,
                    routing_mode:task === 'relay' ? 'relay' : 'direct', tag:'vui-'+hex(16),
                    profile:{security:'tls', transport:'direct'}, certificate_id:cert.id, expected_proxy_revision:this.snapshot.revision};
                // Existing tag is an explicit public correlation identifier,
                // not a hidden purpose field. It resolves a lost create reply.
                this.intent.expectedProxyRevision=this.snapshot.revision;this.intent.payload = payload; this.intent.server = cert.domain; this.intent.nodeSent = true;
                this.stage('正在创建节点并应用');
                try {
                    const result = await this.api('/api/inbounds', 'POST', payload);
                    const row = result?.inbound;
                    if (!row || !Number.isInteger(row.id) || !REV.test(row.mutation_revision)) fail('NODE_UNCERTAIN', '服务器创建结果不完整，请核对，不要再次创建。');
                    this.intent.node = row;this.changed({nodes:true});
                } catch (error) {
                    if (!this.intent || error.status === 401 || error.code === 'ENDED') throw error;
                    this.changed({nodes:true});
                    if(error.detail?.saved===false){this.intent.nodeSent=false;throw error;}
                    if (error.detail?.saved === true || !Number.isInteger(error.status) || error.status < 400 || error.status >= 500 || error.status === 409)
                        fail('NODE_UNCERTAIN', '节点可能已经保存。点击“核对并完成”，不会重复创建。');
                    this.intent.nodeSent = false; throw error;
                }
                return await this.finishNode();
            } finally { this.busy = false; this.changed({idle:true}); }
        }
        owned(row) {
            const p = this.intent?.payload;
            return !!p && row.tag === p.tag && ['core','protocol','port','remark','enable','routing_mode'].every(key => row[key] === p[key]);
        }
        async finishNode() {
            this.stage('正在确认节点和证书');
            const active=await this.api('/api/proxy/settings');
            if(active.pending||active.revision!==this.intent.expectedProxyRevision||active.active?.revision!==this.intent.expectedProxyRevision)fail('NODE_SETTINGS_CHANGED','节点可能已存在，但出口设置发生变化，请查看现有节点，不要重复创建。');
            const rows = await this.api('/api/inbounds'), matches = rows.filter(row => this.owned(row));
            if (matches.length !== 1 || !REV.test(matches[0].mutation_revision)) fail('NODE_UNCERTAIN', '尚未唯一确认刚才创建的节点，请继续核对，不要重复创建。');
            let row = matches[0];
            if (this.intent.node && (row.id !== this.intent.node.id || row.mutation_revision !== this.intent.node.mutation_revision))
                fail('NODE_CHANGED', '节点已被其他操作修改，请回列表核对，当前设置保持不变。');
            this.intent.node = row;
            const editor = await this.api('/api/inbounds/'+row.id+'/editor');
            if (editor.mutation_revision && editor.mutation_revision !== row.mutation_revision) fail('NODE_CHANGED', '节点已变化，请重新选择。');
            if (editor.certificate_id !== this.intent.payload.certificate_id) {
                if (editor.certificate_id) fail('NODE_CHANGED', '节点证书已被其他操作更改，请重新核对。');
                if(!REV.test(editor.content_revision||''))fail('NODE_CHANGED','节点内容条件未确认，请刷新后查看设置。');
                if(this.intent.bindingConsent!==editor.content_revision){
                    this.intent.bindingProposal={id:row.id,content:editor.content_revision};
                    fail('BIND_CONFIRM_REQUIRED','这个已创建节点尚未完成自动续期绑定。请确认使用刚才选择的域名证书，其他字段保持。', {domain:this.intent.server});
                }
                await this.api('/api/inbounds/'+row.id, 'PUT', {certificate_id:this.intent.payload.certificate_id,
                    expected_instance_revision:row.mutation_revision, expected_content_revision:editor.content_revision,
                    expected_proxy_revision:this.intent.expectedProxyRevision});
                const latest = await this.api('/api/inbounds'); row = latest.find(value => value.id === row.id && this.owned(value));
                if (!row) fail('NODE_CHANGED', '节点已变化，请回列表核对。'); this.intent.node = row;
            }
            let cores = await this.api('/api/cores/status');
            if (!cores['sing-box']?.running && this.intent.choice.startStopped) {
                const others=rows.filter(value=>value.id!==row.id&&value.core==='sing-box'&&value.enable).map(value=>[value.id,value.mutation_revision,value.content_revision||null]).sort((a,b)=>a[0]-b[0]);
                if (JSON.stringify(others)!==JSON.stringify(this.intent.startRows)) fail('START_SCOPE_CHANGED','需要恢复的节点已变化，请重新核对后再启动。');
                this.require('nodes');this.snapshot=active;await this.checkApply();
                cores = await this.api('/api/cores/status');
            }
            if (!cores['sing-box']?.running || cores['sing-box'].dirty || !nodeState(row))
                fail('NODE_SAVED', '节点已经保存，但尚未确认启动。请处理运行状态，不要重新创建。', row);
            this.nodes = await this.api('/api/inbounds'); this.cores = cores;
            const result = {kind:'node', node:row, server:this.intent.server};
            this.stage('节点已创建并启动'); this.changed({nodes:true}); return result;
        }
        async resolve() {
            if (this.busy) fail('BUSY', '正在核对，请稍候。');
            this.busy = true;
            try {
                if (this.attempt?.state === 'uncertain') {
                    await this.sendAttempt();
                    // No create was sent before a preparation failure. Resume
                    // only its exact prepared choice, never re-import a link.
                    const intent = this.intent;
                    if (intent && !intent.nodeSent && ['direct','relay','host','exit'].includes(intent.task)) {
                        const choice = {...intent.choice, revision:this.snapshot.revision};
                        this.busy = false; return await this.run(intent.task, choice);
                    }
                    if(this.intent?.nodeSent)return await this.finishNode();
                    return {kind:this.intent?.task==='host-off'?'host-off':'settings', revision:this.snapshot.revision};
                }
                if (this.intent?.importUncertain) {
                    const i=this.intent, latest=await this.api('/api/proxy/settings'), before=i.importBefore;
                    const oldIDs=new Set(before.upstreams.map(row=>row.id));
                    const added=latest.upstreams.filter(row=>!oldIDs.has(row.id));
                    if (latest.pending || latest.active?.revision!==before.active?.revision || !same(latest.settings,before.settings) ||
                        added.length!==1 || added[0].source_fingerprint!==i.importPreview.source_fingerprint ||
                        latest.upstreams.filter(row=>oldIDs.has(row.id)).some(row=>!same(row,before.upstreams.find(old=>old.id===row.id))))
                        fail('IMPORT_UNCERTAIN','尚不能确认出口保存结果，请查看服务器设置，不会重复导入。');
                    this.snapshot=latest;this.ownedRevision=latest.revision;i.importUncertain=false;i.choice.upstreamId=added[0].id;i.choice.link='';
                    this.busy=false;return await this.run(i.task,{...i.choice,revision:latest.revision});
                }
                if (this.intent?.saveUncertain) { await this.load();fail('UNKNOWN_DRAFT','设置可能已保存，请查看并明确处理这份未启用更改；不会重复提交。'); }
                if (this.intent?.nodeSent) return await this.finishNode();
                fail('NO_PENDING', '没有本页可以继续的操作，请读取最新状态。');
            } finally { this.busy = false; this.changed({idle:true}); }
        }
        async disableHost() {
            this.require('relay'); if (this.busy) fail('BUSY', '请等待当前操作。'); this.busy = true;
            try {
                await this.load(); this.clean(); this.baselineRevision = this.snapshot.active?.revision;
                if(this.attempt?.state==='uncertain')fail('APPLY_UNCERTAIN','请先核对上次操作。');
                this.intent={task:'host-off',nodeSent:false};
                const values = settings(this.snapshot.settings); values.local_proxy_enabled = false;
                await this.saveSettings(values); if (this.snapshot.active?.revision !== this.snapshot.revision) await this.checkApply();
                return {kind:'host-off'};
            } finally { this.busy = false; this.changed({idle:true}); }
        }
        async discardDraft() {
            this.require('relay'); if (this.busy) fail('BUSY', '请等待当前操作。');
            if (!this.snapshot?.active || this.snapshot.pending) fail('CANNOT_DISCARD', '没有可安全恢复的运行设置，请先核对未完成操作。');
            this.busy = true;
            try {
                await this.api('/api/proxy/discard-draft', 'POST', {base_revision:this.snapshot.revision, active_revision:this.snapshot.active.revision});
                this.ownedRevision = null; this.intent = null; this.attempt = null; this.generatedAuth = null;
                return await this.load();
            } finally { this.busy = false; }
        }
        async applyDraft() {
            this.require('relay'); if (this.busy) fail('BUSY', '请等待当前操作。'); this.busy = true;
            try { this.intent={task:'review-draft',nodeSent:false}; if (this.snapshot.pending) fail('PENDING', '请先核对未结束的事务。'); return await this.checkApply(); }
            finally { this.busy = false; this.changed({idle:true}); }
        }
        async confirmBinding() {
            if(!this.intent?.bindingProposal)fail('NO_PENDING','没有待确认的证书绑定。');
            this.intent.bindingConsent=this.intent.bindingProposal.content;return await this.resolve();
        }
        async exactNode(id, expected, expectedContent=null) {
            const rows = await this.api('/api/inbounds'), row = rows.find(value => value.id === id);
            if (!row || !REV.test(expected) || row.mutation_revision !== expected) fail('NODE_CHANGED', '节点已变化，请刷新列表后重新选择。');
            if(expectedContent!==null&&(!REV.test(expectedContent)||row.content_revision!==expectedContent))fail('NODE_CHANGED','节点内容已变化，请刷新后重新选择。');
            return row;
        }
        async edit(id, expected, changes, proxyRevision = null, expectedContent=null) {
            this.require('nodes'); if (this.busy) fail('BUSY', '请等待当前操作。');
            const allowed = ['remark','port','enable','routing_mode','certificate_id'];
            if (Object.keys(changes).some(key => !allowed.includes(key))) fail('EDIT_FIELDS', '此处只修改基本设置，其他配置请进入高级编辑。');
            if ('routing_mode' in changes) {this.require('relay');if(!REV.test(proxyRevision||''))fail('SETTINGS_CHANGED','更改出口前请重新读取连接设置。');}
            if ('certificate_id' in changes) this.require('certificates');
            this.busy = true;
            try {
                if(!REV.test(expectedContent||''))fail('NODE_CHANGED','当前节点缺少安全编辑条件，请刷新或进入高级编辑。');
                await this.exactNode(id, expected, expectedContent);
                await this.api('/api/inbounds/'+id, 'PUT', {...changes, expected_instance_revision:expected,expected_content_revision:expectedContent,...(proxyRevision?{expected_proxy_revision:proxyRevision}:{})});
                this.nodes = await this.api('/api/inbounds'); this.cores = await this.api('/api/cores/status');
                this.changed({nodes:true}); return this.nodes.find(row => row.id === id);
            } finally { this.busy = false; }
        }
        async share(id, expected, server, upstream = false, format = 'uri', expectedContent = null) {
            this.require('share'); const row = await this.exactNode(id, expected, expectedContent);
            if(!REV.test(row.content_revision||''))fail('NODE_CHANGED','节点安全条件未确认，请刷新后重新分享。');
            const conditions={expected_instance_revision:expected,expected_content_revision:row.content_revision};
            if (upstream ? row.upstream_export_eligible !== true : row.share_eligible !== true)
                fail('SHARE_UNAVAILABLE', upstream ? '该旧节点不能作为TCP出口。可以另建兼容出口节点，原配置保持。' : '这个节点当前不具备分享资格，请先查看运行和配置状态。');
            if (upstream) return await this.api('/api/subscription/upstream/'+id, 'POST', {format, server, acknowledge_tcp_only:true,...conditions});
            return await this.api('/api/subscription/link/'+id+'?host='+encodeURIComponent(server)+'&expected_instance_revision='+expected+'&expected_content_revision='+row.content_revision);
        }
        async localConnection() {
            this.require('relay','share');
            const snapshot = await this.api('/api/proxy/settings');
            if (!snapshot.active?.settings?.local_proxy_enabled || snapshot.pending) fail('HOST_NOT_ACTIVE', '服务器程序代理尚未确认开启，请先处理运行状态。');
            const info=await this.api('/api/proxy/local-connection', 'POST', {base_revision:snapshot.active.revision});
            if(info?.host!=='127.0.0.1'||!Number.isInteger(info.port)||info.port<1024||info.port>65535||info.revision!==snapshot.active.revision||
                typeof info.username!=='string'||! /^[A-Za-z0-9._-]{1,64}$/.test(info.username)||typeof info.password!=='string'||info.password.length<12||info.password.length>255||/[^!-~]/.test(info.password))fail('CONNECTION_UNCONFIRMED','连接信息未能确认，请刷新后重试。');
            return info;
        }
    }
    return {Flow, FlowError, message, usableCertificates, suggestedPort, REV};
});
