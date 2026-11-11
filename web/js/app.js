addEventListener('pageshow', (event) => { if (event.persisted) location.reload(); });
// Session cookies are HttpOnly. Do not persist credentials in Web Storage.
// One request per status endpoint, shared by timer and manual refresh callers.
function singleStatusRequest(task) {
    let pending = null, queued = null;
    function refresh(...args) {
        if (pending) return pending;
        refresh.pending = true;
        pending = Promise.resolve().then(() => task(...args)).finally(() => {
            pending = null;
            refresh.pending = false;
        });
        return pending;
    }
    refresh.pending = false;
    refresh.fresh = (...args) => {
        if (!pending) return refresh(...args);
        if (!queued) queued = pending.catch(() => {}).then(() => {
            queued = null;
            return refresh(...args);
        });
        return queued;
    };
    return refresh;
}

function visibleStatusPoller(task, interval, environment = {}) {
    const page = environment.document || document;
    const later = environment.setTimeout || setTimeout;
    const cancel = environment.clearTimeout || clearTimeout;
    const failed = environment.onError || ((error) => console.error('Status polling failed', error));
    let timer = null, running = false, stopped = false, refreshAfterFlight = false;
    const clear = () => { if (timer !== null) { cancel(timer); timer = null; } };
    const schedule = () => {
        clear();
        if (!stopped && !page.hidden) timer = later(run, interval);
    };
    async function run() {
        clear();
        if (stopped || page.hidden) return;
        if (running) { refreshAfterFlight = true; return; }
        // A manual refresh may already own this endpoint. Await it, then fetch
        // once more for the newly visible page rather than displaying old data.
        refreshAfterFlight = refreshAfterFlight || task.pending === true;
        running = true;
        try { await task(); }
        catch (error) { failed(error); }
        finally {
            running = false;
            if (stopped || page.hidden) refreshAfterFlight = false;
            else if (refreshAfterFlight) { refreshAfterFlight = false; void run(); }
            else schedule();
        }
    }
    const changed = () => {
        clear();
        if (page.hidden) refreshAfterFlight = false;
        else void run();
    };
    page.addEventListener('visibilitychange', changed);
    schedule(); // Initial page data has already been fetched by onMounted.
    return { stop() {
        stopped = true; refreshAfterFlight = false; clear();
        page.removeEventListener('visibilitychange', changed);
    } };
}

const statusPollers = [];
const clearAdminSecrets = [];
let adminSessionEnded = false;
let adminSessionChannel = null;
try {
    if (typeof BroadcastChannel === 'function') adminSessionChannel = new BroadcastChannel('vui-admin-session');
} catch (_) { /* Older browsers still revalidate immediately when shown. */ }
function endAdminSession(announce = false) {
    if (adminSessionEnded) return;
    adminSessionEnded = true;
    clearAdminSecrets.forEach(clear => clear());
    statusPollers.forEach(poller => poller.stop());
    try { if (announce && adminSessionChannel) adminSessionChannel.postMessage('session-ended'); } catch (_) {}
    try { if (adminSessionChannel) adminSessionChannel.close(); } catch (_) {}
    globalThis.VUI_NAV ? VUI_NAV.replaceLogin() : location.replace('/login');
}
if (adminSessionChannel) adminSessionChannel.onmessage = (event) => {
    if (event.data === 'session-ended') void checkAdminSession(false, true);
};

let adminSessionCheck = null, announceConfirmedSessionEnd = false, adminCheckGeneration = 0;
function checkAdminSession(announce = false, newEvidence = false) {
    if (adminSessionEnded) return Promise.resolve(null);
    announceConfirmedSessionEnd = announceConfirmedSessionEnd || announce;
    if (newEvidence) adminCheckGeneration++;
    if (adminSessionCheck) return adminSessionCheck;
    let checkedGeneration = -1;
    // Messages received during an older check invalidate its result. Keep one
    // request in flight, then verify the latest cookie instead of losing logout.
    adminSessionCheck = Promise.resolve().then(async () => {
        while (!adminSessionEnded) {
            const generation = adminCheckGeneration;
            let response = null;
            try {
                response = await axios.get('/api/auth/me', {
                    timeout: 5000, validateStatus: status => status === 200 || status === 401
                });
            } catch (_) { /* A network failure is not evidence of invalid credentials. */ }
            if (generation !== adminCheckGeneration) continue;
            checkedGeneration = generation;
            if (response?.status === 401) endAdminSession(announceConfirmedSessionEnd);
            return response?.status === 200 ? response.data : null;
        }
        return null;
    }).finally(() => {
        adminSessionCheck = null;
        // Cover a notification queued between the final result and this cleanup.
        if (!adminSessionEnded && checkedGeneration !== adminCheckGeneration) void checkAdminSession();
        else announceConfirmedSessionEnd = false;
    });
    return adminSessionCheck;
}
axios.defaults.headers.common['X-VUI-Request'] = '1';
axios.interceptors.response.use(response => response, error => {
    if (error.response?.status === 401) {
        clearAdminSecrets.forEach(clear => clear());
        return checkAdminSession(true, true).then(() => Promise.reject(error));
    }
    return Promise.reject(error);
});

const { createApp, ref, onMounted, reactive, computed, watch, nextTick } = Vue;
const { ElMessage, ElMessageBox } = ElementPlus;
const {
    Odometer,
    Connection,
    Setting,
    Lock,
    Refresh,
    ArrowDown,
    Document,
    UploadFilled,
    Link
} = ElementPlusIconsVue;

const app = createApp({
    setup() {
        const currentView = ref(globalThis.VUI_NAV ? VUI_NAV.appView(location.hash) : (location.hash === '#inbounds' ? 'inbounds' : 'dashboard'));
        const viewScroll = new Map();
        const systemStatus = ref({});
        const coreStatus = ref({});
        // Pending polls retain the last confirmed display; rejected reads hide
        // only that endpoint. Missing/malformed fields never imply false or zero.
        const systemStatusKnown = ref(false);
        const coreStatusKnown = ref(false);
        const statusRefreshReady = ref(false);
        const refreshingStatus = ref(false);
        clearAdminSecrets.push(() => {
            // A stale 401 alone does not end a currently valid session.
            if (adminSessionEnded) statusRefreshReady.value = false;
        });
        const statusObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
        const systemOsKnown = computed(() => systemStatusKnown.value &&
            typeof systemStatus.value.os === 'string' && systemStatus.value.os.trim().length > 0);
        const systemMetricKnown = name => {
            const percent = systemStatus.value[name]?.percent;
            return systemStatusKnown.value && Number.isFinite(percent) && percent >= 0 && percent <= 100;
        };
        const coreFieldKnown = (core, field) => coreStatusKnown.value &&
            typeof coreStatus.value[core]?.[field] === 'boolean';
        const inbounds = ref([]);
        const inboundSearch = ref('');
        const filteredInbounds = computed(() => {
            const query = inboundSearch.value.trim().toLowerCase();
            if (!query) return inbounds.value;
            // Search only displayed metadata, keeping the original row objects.
            return inbounds.value.filter(row => ['remark', 'id', 'port', 'core', 'protocol'].some(field => {
                const value = row[field];
                return (typeof value === 'string' || typeof value === 'number') &&
                    String(value).toLowerCase().includes(query);
            }));
        });
        const showAddInbound = ref(false);
        const editingInboundId = ref(null);
        const inboundCredentials = ref({});
        const savingInbound = ref(false);
        let inboundEditorGeneration = 0, inboundListGeneration = 0, deletingInbound = false;
        let editingInboundRevision = null;
        const validInboundRevision = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
        const inboundInstanceChanged = 'Inbound instance changed; refresh nodes and reopen or confirm again';
        const inboundInstanceUnknown = 'Cannot verify inbound instance; refresh nodes or reload the page';
        const routingModes = {inherit: '跟随全局', direct: '指定直连', relay: '指定中转'};
        const knownRoutingMode = value => Object.hasOwn(routingModes, value);
        const inboundSavedRouting = row => knownRoutingMode(row.routing_mode) ? routingModes[row.routing_mode] : '策略未确认';
        const inboundAppliedRouting = row => {
            const state = row.routing_status;
            if (state?.state === 'stopped') return '核心已停止；出口未运行';
            if (state?.state === 'pending') return '待应用；当前出口未确认';
            if (state?.state !== 'applied' || !/^[0-9a-f]{64}$/.test(state.policy_identity || '') ||
                !['direct', 'relay'].includes(state.applied_effective_mode)) return '已应用出口未确认';
            return state.applied_effective_mode === 'relay' ? '已应用中转（仅 TCP；UDP 拒绝）' : '已应用普通直连';
        };
        const restartingCore = ref('');
        const showQrDialog = ref(false);
        const qrLink = ref('');
        const qrBusy = ref(false);
        const qrNotice = ref('');
        let qrGeneration = 0, qrController = null, qrRenderTimer = null, qrRetryRow = null;
        const clearQrCode = () => {
            qrGeneration++;
            if (qrController) qrController.abort();
            qrController = null;
            if (qrRenderTimer !== null) clearTimeout(qrRenderTimer);
            qrRenderTimer = null;
            qrLink.value = '';
            qrBusy.value = false;
            qrNotice.value = '';
            qrRetryRow = null;
            const container = document.getElementById('qrcode');
            if (container) { container.textContent = ''; container.title = ''; }
            const output = document.getElementById('share-link');
            if (output) output.value = '';
        };
        const closeQrDialog = () => {
            clearQrCode();
            showQrDialog.value = false;
            globalThis.VUI_NAV?.modal.close('share');
        };
        // Element Plus emits v-model=false only after its leave transition.
        // Invalidate before Escape/header/backdrop dismissal starts, not after
        // a late response can repopulate the closing dialog.
        const beforeQrDialogClose = (done) => {
            closeQrDialog();
            done();
        };
        // Keep the synchronous watcher for direct model changes too.
        watch(showQrDialog, (open) => {
            if (open) globalThis.VUI_NAV?.modal.open('share', closeQrDialog);
            else { clearQrCode(); globalThis.VUI_NAV?.modal.close('share'); }
        }, { flush: 'sync' });
        watch(currentView, closeQrDialog, { flush: 'sync' });
        clearAdminSecrets.push(closeQrDialog);
        addEventListener('pagehide', closeQrDialog);
        if (!globalThis.VUI_NAV) addEventListener('popstate', closeQrDialog);
        // Read only the literal hostname; never reuse a panel URL as a node address.
        const suggestedNodeHost = () => (location.hostname || '').replace(/^\[|\]$/g, '');
        const upstreamExport = reactive({
            open: false, inboundId: null, name: '', server: '', format: 'uri',
            acknowledged: false, busy: false, content: '', error: '', notice: ''
        });
        let upstreamExportGeneration = 0, upstreamExportController = null, upstreamExportURL = null;
        const clearUpstreamExport = () => {
            upstreamExportGeneration++;
            if (upstreamExportController) upstreamExportController.abort();
            upstreamExportController = null;
            if (upstreamExportURL) URL.revokeObjectURL(upstreamExportURL);
            upstreamExportURL = null;
            upstreamExport.content = '';
            upstreamExport.error = '';
            upstreamExport.notice = '';
            upstreamExport.busy = false;
        };
        const closeUpstreamExport = () => {
            clearUpstreamExport();
            upstreamExport.open = false;
            globalThis.VUI_NAV?.modal.close('upstream-export');
            upstreamExport.inboundId = null;
            upstreamExport.name = '';
            upstreamExport.server = '';
            upstreamExport.acknowledged = false;
        };
        const openUpstreamExport = (row, suggestedServer = '') => {
            closeUpstreamExport();
            upstreamExport.inboundId = row.id;
            upstreamExport.name = row.remark || `${row.protocol}:${row.port}`;
            upstreamExport.format = 'uri';
            // Suggest a hostname only, never the management URL or its port/path.
            upstreamExport.server = suggestedServer || suggestedNodeHost();
            upstreamExport.open = true;
        };
        watch(() => [upstreamExport.server, upstreamExport.format, upstreamExport.inboundId], () => {
            clearUpstreamExport();
            upstreamExport.acknowledged = false;
        }, { flush: 'sync' });
        watch(() => upstreamExport.acknowledged, clearUpstreamExport, { flush: 'sync' });
        watch(() => upstreamExport.open, (open) => {
            if (open) globalThis.VUI_NAV?.modal.open('upstream-export', closeUpstreamExport);
            else closeUpstreamExport();
        }, { flush: 'sync' });
        watch(currentView, closeUpstreamExport, { flush: 'sync' });
        clearAdminSecrets.push(closeUpstreamExport);
        addEventListener('pagehide', closeUpstreamExport);
        if (!globalThis.VUI_NAV) addEventListener('popstate', closeUpstreamExport);

        const requestUpstreamExport = async () => {
            if (upstreamExport.busy || !upstreamExport.open || adminSessionEnded ||
                !upstreamExport.inboundId || !upstreamExport.server || !upstreamExport.acknowledged) return;
            clearUpstreamExport();
            const generation = upstreamExportGeneration;
            upstreamExportController = new AbortController();
            upstreamExport.busy = true;
            try {
                const response = await axios.post(`/api/subscription/upstream/${upstreamExport.inboundId}`, {
                    format: upstreamExport.format, server: upstreamExport.server, acknowledge_tcp_only: true
                }, { responseType: 'text', transformResponse: [value => value],
                    signal: upstreamExportController.signal, timeout: 15000 });
                if (generation !== upstreamExportGeneration || !upstreamExport.open || adminSessionEnded) return;
                if (response.headers['x-vui-export-profile'] !== 'tcp-upstream-v1' ||
                    typeof response.data !== 'string' || !response.data.length) throw new Error('Invalid export response');
                upstreamExport.content = response.data;
            } catch (error) {
                if (generation !== upstreamExportGeneration || !upstreamExport.open || adminSessionEnded) return;
                clearUpstreamExport();
                const status = error.response?.status;
                upstreamExport.error = status === 403 ? '无权导出：请使用管理员会话并从当前面板重试。' :
                    status === 404 ? '节点不存在，请刷新节点列表。' :
                    status === 409 ? '此节点不具备 TCP 中转上游导出资格：仅支持已启用且未设置定时过期的单用户 sing-box VLESS TCP TLS / REALITY。' :
                    status === 422 ? '请填写有效的字面主机或 IP，并确认导出格式和 TCP-only 用途。' :
                    '导出失败，旧内容已清除，请重试。';
            } finally {
                if (generation === upstreamExportGeneration) {
                    upstreamExport.busy = false;
                    upstreamExportController = null;
                }
            }
        };
        const copyUpstreamExport = async () => {
            if (!upstreamExport.open || !upstreamExport.content || adminSessionEnded) return;
            const generation = upstreamExportGeneration;
            try {
                await navigator.clipboard.writeText(upstreamExport.content);
                if (generation === upstreamExportGeneration) upstreamExport.notice = '已复制含连接凭据的内容。';
            } catch (_) {
                if (generation !== upstreamExportGeneration) return;
                upstreamExport.notice = '复制失败。请在下方文本框中手动选择并复制；内容含连接凭据。';
                const output = document.getElementById('upstream-export-content');
                if (output) { output.focus(); output.select(); }
            }
        };
        const downloadUpstreamExport = () => {
            if (!upstreamExport.open || !upstreamExport.content || adminSessionEnded) return;
            if (upstreamExportURL) URL.revokeObjectURL(upstreamExportURL);
            const yaml = upstreamExport.format === 'clash-yaml';
            upstreamExportURL = URL.createObjectURL(new Blob([upstreamExport.content], {
                type: yaml ? 'application/yaml;charset=utf-8' : 'text/plain;charset=utf-8'
            }));
            const url = upstreamExportURL;
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = yaml ? 'upstream-tcp.yaml' : 'upstream-tcp.txt';
            try { anchor.click(); }
            finally {
                setTimeout(() => {
                    URL.revokeObjectURL(url);
                    if (upstreamExportURL === url) upstreamExportURL = null;
                }, 0);
            }
        };
        const siteFiles = ref([]);
        const currentUser = ref({ username: '' });
        const mihomoWarnings = ref([]);
        const managedCertificates = ref([]);
        const managedCertificateState = ref('idle');
        const certificateMode = ref('managed');
        let certificateReadGeneration = 0, automaticCertificateId = null, tlsCertificateDraft = null;
        const certificateDomain = value => typeof value === 'string' ? value.toLowerCase().replace(/\.$/, '') : '';
        const managedCertificateEligible = computed(() => newInbound.profile.security === 'tls' && (
            (newInbound.core === 'sing-box' && ['vless','vmess','trojan','hysteria2','tuic'].includes(newInbound.protocol)) ||
            (newInbound.core === 'xray' && newInbound.protocol === 'vless' &&
                ['raw','tcp'].includes(newInbound.profile.transport) && !newInbound.profile.flow)));
        const certificateMatchesSNI = cert => !newInbound.profile.server_name ||
            certificateDomain(cert.domain) === certificateDomain(newInbound.profile.server_name);
        const managedCertificateHint = computed(() => {
            if (managedCertificateState.value === 'loading') return '正在读取已有证书…';
            if (managedCertificateState.value === 'error') return '证书列表读取失败，请重试；已有选择和配置已保留。';
            const selected = managedCertificates.value.find(c => c.id === newInbound.certificate_id);
            if (newInbound.certificate_id && !selected) return '当前绑定的证书不在可用列表中，请重试或明确选择其他证书；原绑定已保留。';
            if (selected && !certificateMatchesSNI(selected)) return 'SNI 与所选证书域名不匹配，请修改 SNI 或重新选择证书。';
            if (selected) return '已选择托管证书；服务器自动填写材料路径，续期后自动更新节点。';
            if (!managedCertificates.value.length) return '暂无可用的正式证书，请先到证书页签发，或选择手工证书路径。';
            if (managedCertificates.value.length > 1) return '有多张可用证书，请选择一张；SNI 必须与证书域名匹配。';
            return 'SNI 与唯一可用证书域名不匹配，请修改 SNI 或选择手工证书路径。';
        });
        const inboundEditorError = (error, action) => {
            const status = error.response?.status;
            const detail = error.response?.data?.detail;
            const text = typeof detail === 'string' ? detail : detail?.message || '';
            const safeMessages = ['请选择可用的正式证书，或填写 TLS 证书路径', '请填写 TLS 私钥路径'];
            if (safeMessages.includes(text)) return text;
            if (status === 401) return '登录状态已失效，请重新登录。';
            if (status === 403) return '没有操作该节点的权限，请重新登录后重试。';
            if (status === 404) return '节点或证书已不存在，请刷新列表后重试。';
            if (/instance.*(changed|revision)|expected_instance_revision/i.test(text)) return '节点已发生变化，请刷新节点列表并重新打开编辑。';
            if (/certificate|certificat|\bTLS\b|\bSNI\b/i.test(text)) return '证书配置不可用：请核对证书是否有效、SNI 是否匹配，以及当前核心、协议和传输是否支持该证书。';
            if (status === 422 || status === 400) return '节点参数不符合要求，请核对端口、协议和安全设置后重试。';
            if (status === 409) return '节点配置发生冲突或无法应用，请刷新后核对设置并重试。';
            return action + '失败，请稍后重试。';
        };

        const routingCatalog = ref({ categories: [], presets: [] });
        const routingPreview = ref({ sections: [], dns: {}, warnings: [] });
        const savingRouting = ref(false);
        const routingForm = reactive({
            mode: 'standard',
            directDomains: '',
            proxyDomains: '',
            presets: {},
            bypassCgnat: false,
            intranet: []
        });

        const userForm = reactive({
            username: '',
            password: ''
        });

        const newInbound = reactive({
            core: 'xray',
            remark: '',
            protocol: 'vless',
            port: 443,
            settings: {},
            stream_settings: {},
            certificate_id: null,
            enable: true,
            routing_mode: 'inherit',
            profile: {
                security: 'none',
                transport: 'raw',
                flow: '',
                server_name: '',
                certificate_path: '',
                key_path: '',
                path: '/',
                host: '',
                service_name: '',
                xhttp_mode: 'auto',
                xhttp_alpn_h2: false,
                reality_target: '',
                reality_server_name: '',
                reality_short_id: '',
                client_fingerprint: 'chrome',
                skip_cert_verify: false,
                shadowsocks_method: 'aes-128-gcm',
                shadowsocks_password_set: false,
                up_mbps: 100,
                down_mbps: 100,
                obfs_type: '',
                obfs_password: '',
                congestion_control: 'bbr',
                udp_relay_mode: 'native',
                zero_rtt_handshake: false
            }
        });

        const inboundRoutingHint = computed(() => {
            if (!knownRoutingMode(newInbound.routing_mode)) return '服务器未返回可确认的已保存策略；保持不改，或明确选择策略后保存。';
            if (newInbound.routing_mode === 'inherit') return '只有“跟随全局”受默认入口中转开关影响。主机代理开关不改变此节点策略。';
            const p = newInbound.profile;
            if (newInbound.core !== 'sing-box' || newInbound.protocol !== 'vless' ||
                p.transport !== 'direct' || p.security !== 'tls' || p.flow || p.skip_cert_verify) {
                return '本期显式策略仅支持 sing-box / VLESS / 直接 TCP / 验证 TLS / 空 flow。当前配置可能不获资格，保存时由服务器严格核验，不会忽略策略。';
            }
            return newInbound.routing_mode === 'relay' ?
                '指定中转共用唯一已选上游，不受默认开关影响；有效中转仅 TCP，服务器拒绝应用 UDP。先在代理与中转页选择并应用上游；禁用节点可保存为草稿。' :
                '指定直连无需海外上游，排除此中转策略，保留普通入口既有路由、拒绝规则与能力。首次尚无活动配置时，可在代理与中转页保持两个开关关闭、不选上游，检查并应用建立活动配置。';
        });
        let inboundEditorBaseline = '';
        const canLeaveInboundEditor = () => !savingInbound.value && (!showAddInbound.value ||
            inboundEditorBaseline === JSON.stringify(newInbound) || confirm('放弃本次未保存的节点编辑？'));
        const beforeInboundEditorClose = (done) => {
            if (!canLeaveInboundEditor()) return;
            closeInboundEditor();
            if (typeof done === 'function') done();
        };
        const closeInboundEditor = () => {
            inboundEditorGeneration++;
            certificateReadGeneration++;
            managedCertificateState.value = 'idle';
            showAddInbound.value = false;
            globalThis.VUI_NAV?.modal.close('inbound-editor');
            editingInboundId.value = null;
            editingInboundRevision = null;
            inboundCredentials.value = {};
            inboundEditorBaseline = '';
            newInbound.routing_mode = 'inherit';
            resetInboundProfile();
        };
        watch(showAddInbound, (open) => {
            if (open) {
                inboundEditorBaseline = JSON.stringify(newInbound);
                globalThis.VUI_NAV?.modal.open('inbound-editor', closeInboundEditor, canLeaveInboundEditor);
            }
            else globalThis.VUI_NAV?.modal.close('inbound-editor');
        }, { flush: 'sync' });
        globalThis.VUI_NAV?.guard(() => !savingInbound.value);
        watch(currentView, closeInboundEditor, { flush: 'sync' });
        clearAdminSecrets.push(() => { closeInboundEditor(); inboundListGeneration++; inbounds.value = []; });
        addEventListener('pagehide', closeInboundEditor);
        if (!globalThis.VUI_NAV) addEventListener('popstate', closeInboundEditor);

        const protocolOptions = computed(() => {
            if (newInbound.core === 'sing-box') {
                return [
                    ['VLESS', 'vless'],
                    ['VMess', 'vmess'],
                    ['Trojan', 'trojan'],
                    ['Shadowsocks', 'shadowsocks'],
                    ['Hysteria2', 'hysteria2'],
                    ['TUIC', 'tuic']
                ];
            }
            return [
                ['VLESS', 'vless'],
                ['VMess', 'vmess'],
                ['Trojan', 'trojan'],
                ['Shadowsocks', 'shadowsocks']
            ];
        });

        const presetGroups = computed(() => {
            return routingCatalog.value.categories.map(category => ({
                ...category,
                presets: routingCatalog.value.presets.filter(
                    preset => preset.category === category.id
                )
            }));
        });

        const enabledPresetCount = computed(() => {
            return Object.values(routingForm.presets)
                .filter(Boolean)
                .length;
        });

        const routingRuleCount = computed(() => {
            return (routingPreview.value.sections || [])
                .reduce((sum, section) => sum + (section.rules?.length || 0), 0);
        });

        const colors = [
            { color: '#f56c6c', percentage: 100 },
            { color: '#e6a23c', percentage: 80 },
            { color: '#5cb87a', percentage: 60 },
            { color: '#1989fa', percentage: 40 },
            { color: '#6f7ad3', percentage: 20 }
        ];

        const splitLines = (value) => {
            return String(value || '')
                .split(/\r?\n/)
                .map(item => item.trim())
                .filter(Boolean);
        };

        const applyRoute = () => {
            if (!globalThis.VUI_NAV) return;
            const target = VUI_NAV.route();
            if (!target.view) { location.replace(target.url); return; }
            const previous = currentView.value;
            if (previous === target.view) return;
            viewScroll.set(previous, globalThis.scrollY || 0);
            currentView.value = target.view;
            if (target.view === 'inbounds') fetchInbounds();
            nextTick(() => globalThis.scrollTo?.(0, viewScroll.get(target.view) || 0));
        };
        if (globalThis.VUI_NAV) VUI_NAV.subscribe((_route, reason) => {
            if (reason === 'popstate') { closeQrDialog(); closeUpstreamExport(); closeInboundEditor(); }
            applyRoute();
        });
        const handleSelect = (key) => {
            const id = key === 'settings' ? 'account' : key === 'site' ? 'legacy' : key;
            if (globalThis.VUI_NAV) { VUI_NAV.navigate(id); return; }
            const external = {proxy:'/proxy', certificates:'/certificates', routing:'/workspace#routing', subscriptions:'/workspace#subscriptions', account:'/account'};
            if (external[id]) {
                if (!canLeaveInboundEditor()) return;
                closeQrDialog(); closeUpstreamExport(); closeInboundEditor();
                location.assign(external[id]); return;
            }
            currentView.value = ['dashboard', 'inbounds', 'cores', 'diagnostics', 'firewall', 'security', 'legacy'].includes(id) ? id : 'dashboard';
            if (id === 'inbounds') fetchInbounds();
        };

        const fetchSystemStatus = singleStatusRequest(async () => {
            if (adminSessionEnded) return;
            try {
                const res = await axios.get('/api/system/status', { timeout: 5000 });
                systemStatusKnown.value = statusObject(res.data);
                systemStatus.value = systemStatusKnown.value ? res.data : {};
            } catch (error) {
                systemStatusKnown.value = false;
                console.error('Failed to fetch status', error);
            }
        });

        const fetchCoreStatus = singleStatusRequest(async () => {
            if (adminSessionEnded) return;
            try {
                const res = await axios.get('/api/cores/status', { timeout: 5000 });
                coreStatusKnown.value = statusObject(res.data);
                coreStatus.value = coreStatusKnown.value ? res.data : {};
            } catch (error) {
                coreStatusKnown.value = false;
                console.error('Failed to fetch core status', error);
            }
        });

        const refreshStatus = async () => {
            if (!statusRefreshReady.value || adminSessionEnded || refreshingStatus.value) return;
            refreshingStatus.value = true;
            try {
                // Own both new reads, including any wait behind existing polls.
                // Each reader keeps its independent Unknown/error semantics.
                await Promise.all([fetchSystemStatus.fresh(), fetchCoreStatus.fresh()]);
            } finally {
                refreshingStatus.value = false;
            }
        };

        const fetchInbounds = async () => {
            closeQrDialog();
            closeUpstreamExport();
            const generation = ++inboundListGeneration;
            try {
                const res = await axios.get('/api/inbounds', {timeout:15000});
                if (generation !== inboundListGeneration || adminSessionEnded) return;
                inbounds.value = res.data;
            } catch (error) {
                if (generation !== inboundListGeneration || adminSessionEnded || error.response?.status === 401) return;
                // A failed refresh cannot confirm previously shown applied state.
                inbounds.value = inbounds.value.map(row => ({...row, routing_status: null}));
                ElMessage.error('节点列表读取失败，请检查连接后重试；运行状态未能确认。');
            }
        };

        const fetchMihomoWarnings = async () => {
            try {
                const res = await axios.get('/api/subscription/mihomo-warnings');
                mihomoWarnings.value = res.data;
            } catch (error) {
                console.error('Failed to load Mihomo compatibility warnings', error);
            }
        };

        const fetchRoutingPreview = async () => {
            try {
                const res = await axios.get('/api/routing/mihomo/preview');
                routingPreview.value = res.data;
            } catch (error) {
                console.error('Failed to preview routing', error);
            }
        };

        const fetchRouting = async () => {
            try {
                const [catalogRes, settingsRes] = await Promise.all([
                    axios.get('/api/routing/mihomo/catalog'),
                    axios.get('/api/routing/mihomo')
                ]);
                routingCatalog.value = catalogRes.data;
                const settings = settingsRes.data;
                routingForm.mode = settings.mode || 'standard';
                routingForm.directDomains = (settings.direct_domains || []).join('\n');
                routingForm.proxyDomains = (settings.proxy_domains || []).join('\n');
                routingForm.presets = { ...(settings.presets || {}) };
                routingForm.bypassCgnat = Boolean(settings.bypass_cgnat);
                routingForm.intranet = (settings.intranet || []).map(zone => ({
                    suffix: zone.suffix,
                    nameserversText: (zone.nameservers || []).join(', ')
                }));
                await fetchRoutingPreview();
            } catch (error) {
                ElMessage.error('Failed to load Mihomo routing settings');
            }
        };

        const saveRouting = async () => {
            savingRouting.value = true;
            try {
                const payload = {
                    mode: routingForm.mode,
                    direct_domains: splitLines(routingForm.directDomains),
                    proxy_domains: splitLines(routingForm.proxyDomains),
                    presets: { ...routingForm.presets },
                    bypass_cgnat: routingForm.bypassCgnat,
                    intranet: routingForm.intranet
                        .filter(zone => zone.suffix?.trim())
                        .map(zone => ({
                            suffix: zone.suffix.trim(),
                            nameservers: String(zone.nameserversText || '')
                                .split(/[\s,]+/)
                                .map(item => item.trim())
                                .filter(Boolean)
                        }))
                };
                const res = await axios.put('/api/routing/mihomo', payload);
                const settings = res.data;
                routingForm.directDomains = (settings.direct_domains || []).join('\n');
                routingForm.proxyDomains = (settings.proxy_domains || []).join('\n');
                routingForm.presets = { ...(settings.presets || {}) };
                routingForm.intranet = (settings.intranet || []).map(zone => ({
                    suffix: zone.suffix,
                    nameserversText: (zone.nameservers || []).join(', ')
                }));
                await fetchRoutingPreview();
                ElMessage.success('Mihomo 分流设置已保存，订阅立即生效');
            } catch (error) {
                ElMessage.error(
                    error.response?.data?.detail ||
                    'Failed to save Mihomo routing settings'
                );
            } finally {
                savingRouting.value = false;
            }
        };

        const resetRoutingDefaults = () => {
            routingForm.mode = 'standard';
            routingForm.directDomains = '';
            routingForm.proxyDomains = '';
            routingForm.bypassCgnat = false;
            routingForm.intranet = [];
            routingForm.presets = Object.fromEntries(
                routingCatalog.value.presets.map(preset => [
                    preset.id,
                    Boolean(preset.default_enabled)
                ])
            );
        };

        const setCategoryEnabled = (categoryId, enabled) => {
            routingCatalog.value.presets
                .filter(preset => preset.category === categoryId)
                .forEach(preset => {
                    routingForm.presets[preset.id] = enabled;
                });
        };

        const addIntranetZone = () => {
            routingForm.intranet.push({
                suffix: '',
                nameserversText: ''
            });
        };

        const removeIntranetZone = (index) => {
            routingForm.intranet.splice(index, 1);
        };

        const reconcileManagedCertificate = () => {
            if (savingInbound.value || adminSessionEnded || editingInboundId.value ||
                certificateMode.value !== 'managed' || !managedCertificateEligible.value ||
                managedCertificateState.value !== 'ready') return;
            if (automaticCertificateId && newInbound.certificate_id === automaticCertificateId) {
                const selected = managedCertificates.value.find(c => c.id === automaticCertificateId);
                if (!selected || !certificateMatchesSNI(selected)) newInbound.certificate_id = null;
            }
            if (newInbound.certificate_id) return;
            automaticCertificateId = null;
            // Multiple available certificates always require a deliberate choice,
            // even if an entered SNI happens to match only one of them.
            if (managedCertificates.value.length !== 1) return;
            const cert = managedCertificates.value[0];
            if (!certificateMatchesSNI(cert)) return;
            newInbound.certificate_id = cert.id;
            automaticCertificateId = cert.id;
            if (!newInbound.profile.server_name) newInbound.profile.server_name = cert.domain;
        };
        const loadManagedCertificates = async (editorGeneration = inboundEditorGeneration, selectDefault = true) => {
            if (savingInbound.value || adminSessionEnded) return;
            const readGeneration = ++certificateReadGeneration;
            const current = () => readGeneration === certificateReadGeneration &&
                editorGeneration === inboundEditorGeneration && !adminSessionEnded && !savingInbound.value;
            managedCertificateState.value = 'loading';
            try {
                const {data} = await axios.get('/api/certificates');
                if (!current()) return;
                if (!Array.isArray(data)) throw new Error('Invalid certificate list');
                managedCertificates.value = data.filter(c => c && c.environment === 'production' &&
                    typeof c.id === 'string' && c.id && typeof c.domain === 'string' && c.domain &&
                    typeof c.revision === 'string' && c.revision &&
                    typeof c.days_remaining === 'number' && Number.isFinite(c.days_remaining) && c.days_remaining >= 0 &&
                    (c.not_after == null || (typeof c.not_after === 'number' && c.not_after > Date.now() / 1000)));
                managedCertificateState.value = 'ready';
                if (selectDefault) reconcileManagedCertificate();
            } catch {
                if (!current()) return;
                managedCertificateState.value = 'error';
            }
        };
        const retryManagedCertificates = () => {
            if (!showAddInbound.value || savingInbound.value || adminSessionEnded) return;
            return loadManagedCertificates();
        };
        const onManagedCertificateChanged = () => {
            if (savingInbound.value || adminSessionEnded) return;
            automaticCertificateId = null;
            const selected = managedCertificates.value.find(c => c.id === newInbound.certificate_id);
            if (selected && !newInbound.profile.server_name) newInbound.profile.server_name = selected.domain;
        };
        const onCertificateModeChanged = () => {
            if (savingInbound.value || adminSessionEnded) return;
            if (certificateMode.value === 'manual') newInbound.certificate_id = null;
            reconcileManagedCertificate();
        };

        const openAdvancedInbound = async () => {
            if (savingInbound.value || adminSessionEnded) return;
            const generation = ++inboundEditorGeneration;
            editingInboundId.value = null;
            editingInboundRevision = null;
            inboundCredentials.value = {};
            newInbound.core = 'xray';
            newInbound.protocol = 'vless';
            newInbound.remark = '';
            newInbound.port = 443;
            newInbound.enable = true;
            newInbound.routing_mode = 'inherit';
            newInbound.settings = {};
            newInbound.stream_settings = {};
            resetInboundProfile();
            certificateMode.value = 'managed';
            automaticCertificateId = null;
            tlsCertificateDraft = null;
            managedCertificates.value = [];
            await loadManagedCertificates(generation);
            if (generation !== inboundEditorGeneration || adminSessionEnded || savingInbound.value) return;
            showAddInbound.value = true;
        };

        // The simple flow owns a fresh draft. Existing nodes only use the
        // independent advanced editor, preserving their original profile.
        const nodeWizard = reactive({open:false, step:1, purpose:'device', remark:'', port:10443,
            certificateId:null, certificates:[], certificateState:'idle', busy:false,
            error:'', outcome:'draft', row:null, serverName:''});
        let wizardGeneration = 0, wizardCertificateGeneration = 0, wizardController = null, wizardCreatedInstance = null;
        const wizardNameHint = computed(() => {
            const name = nodeWizard.remark;
            if (typeof name !== 'string' || Array.from(name).length > 128 || /[\u0000-\u001f\u007f]/.test(name))
                return '名称最多 128 个字符，不能包含换行或控制字符；也可以留空。';
            return '';
        });
        const wizardPortHint = computed(() => {
            if (!Number.isInteger(nodeWizard.port) || nodeWizard.port < 1024 || nodeWizard.port > 65535)
                return '请填写 1024–65535 之间的整数端口；推荐流程使用无需特权的端口。';
            if (inbounds.value.some(row => Number(row.port) === nodeWizard.port))
                return '已有节点使用这个端口，请换一个端口。';
            return '';
        });
        const wizardReady = computed(() => nodeWizard.outcome === 'draft' && !nodeWizard.busy &&
            !wizardNameHint.value && !wizardPortHint.value && nodeWizard.certificateState === 'ready' &&
            nodeWizard.certificates.some(cert => cert.id === nodeWizard.certificateId));
        const closeNodeWizard = () => {
            wizardGeneration++; wizardCertificateGeneration++;
            if (wizardController) wizardController.abort();
            wizardController = null; wizardCreatedInstance = null;
            nodeWizard.open = false; nodeWizard.busy = false; nodeWizard.row = null;
            nodeWizard.remark = ''; nodeWizard.serverName = ''; nodeWizard.error = '';
            nodeWizard.certificates = []; nodeWizard.certificateId = null;
            globalThis.VUI_NAV?.modal.close('node-wizard');
        };
        const canLeaveNodeWizard = () => !nodeWizard.busy;
        const beforeNodeWizardClose = done => {
            if (!canLeaveNodeWizard()) return;
            closeNodeWizard();
            if (typeof done === 'function') done();
        };
        const loadWizardCertificates = async () => {
            if (!nodeWizard.open || nodeWizard.busy || adminSessionEnded) return;
            const generation = wizardGeneration, read = ++wizardCertificateGeneration;
            nodeWizard.certificateState = 'loading';
            try {
                const response = await axios.get('/api/certificates', {timeout:15000});
                if (generation !== wizardGeneration || read !== wizardCertificateGeneration || adminSessionEnded) return;
                if (!Array.isArray(response.data)) throw new Error('Invalid certificate list');
                nodeWizard.certificates = response.data.filter(cert => cert && cert.environment === 'production' &&
                    typeof cert.id === 'string' && cert.id && typeof cert.domain === 'string' && cert.domain &&
                    typeof cert.revision === 'string' && cert.revision &&
                    Number.isFinite(cert.days_remaining) && cert.days_remaining >= 0 &&
                    (cert.not_after == null || (typeof cert.not_after === 'number' && cert.not_after > Date.now()/1000)));
                // Never replace a deliberate selection, even if it disappeared.
                if (!nodeWizard.certificateId && nodeWizard.certificates.length === 1)
                    nodeWizard.certificateId = nodeWizard.certificates[0].id;
                nodeWizard.certificateState = 'ready';
            } catch (error) {
                if (generation !== wizardGeneration || read !== wizardCertificateGeneration || adminSessionEnded) return;
                nodeWizard.certificateState = 'error';
            }
        };
        const openAddInbound = () => {
            if (nodeWizard.busy || savingInbound.value || adminSessionEnded) return;
            closeInboundEditor(); closeNodeWizard(); closeQrDialog(); closeUpstreamExport();
            Object.assign(nodeWizard, {open:true, step:1, purpose:'device', remark:'', port:10443,
                certificateId:null, certificates:[], certificateState:'idle', busy:false,
                error:'', outcome:'draft', row:null, serverName:''});
            globalThis.VUI_NAV?.modal.open('node-wizard', closeNodeWizard, canLeaveNodeWizard);
            return loadWizardCertificates();
        };
        const wizardAdvanced = async () => {
            if (nodeWizard.busy || nodeWizard.outcome !== 'draft') return;
            const remark = nodeWizard.remark, port = nodeWizard.port;
            closeNodeWizard();
            const opening = openAdvancedInbound(), generation = inboundEditorGeneration;
            await opening;
            if (generation === inboundEditorGeneration && showAddInbound.value && !editingInboundId.value) {
                newInbound.remark = remark; newInbound.port = port;
            }
        };
        const syncWizardRow = () => {
            if (!wizardCreatedInstance) { nodeWizard.row = null; return; }
            const created = wizardCreatedInstance;
            nodeWizard.row = inbounds.value.find(row => row.id === created.id &&
                row.mutation_revision === created.mutation_revision) || null;
        };
        const refreshWizardResult = async () => {
            if (!nodeWizard.open || nodeWizard.busy || adminSessionEnded) return;
            const generation = wizardGeneration;
            nodeWizard.busy = true;
            try {
                await Promise.all([fetchInbounds(), fetchCoreStatus.fresh()]);
                if (generation !== wizardGeneration || adminSessionEnded) return;
                // Bind only to the exact created instance, never a recycled ID.
                syncWizardRow();
            } finally { if (generation === wizardGeneration) nodeWizard.busy = false; }
        };
        const createSimpleNode = async () => {
            if (!nodeWizard.open || nodeWizard.step !== 2 || !wizardReady.value || adminSessionEnded) return;
            const cert = nodeWizard.certificates.find(value => value.id === nodeWizard.certificateId);
            const generation = wizardGeneration;
            nodeWizard.busy = true; nodeWizard.error = '';
            wizardCertificateGeneration++;
            wizardController = new AbortController();
            try {
                const response = await axios.post('/api/inbounds', {
                    core:'sing-box', protocol:'vless', remark:nodeWizard.remark,
                    port:nodeWizard.port, enable:true, routing_mode:'direct',
                    profile:{security:'tls', transport:'direct'}, certificate_id:cert.id
                }, {signal:wizardController.signal, timeout:20000});
                if (generation !== wizardGeneration || adminSessionEnded) return;
                const row = response.data?.inbound;
                nodeWizard.outcome = row && validInboundRevision(row.mutation_revision) &&
                    Number.isInteger(row.id) ? 'created' : 'uncertain';
                nodeWizard.row = nodeWizard.outcome === 'created' ? row : null;
                wizardCreatedInstance = nodeWizard.row ? {id:row.id, mutation_revision:row.mutation_revision} : null;
                nodeWizard.serverName = cert.domain;
                nodeWizard.step = 3;
                if (!nodeWizard.row) nodeWizard.error = '服务器返回未能确认新节点。请刷新节点列表核对；不要重复创建。';
                await Promise.all([fetchInbounds(), fetchCoreStatus.fresh()]);
                if (generation === wizardGeneration && !adminSessionEnded) syncWizardRow();
            } catch (error) {
                if (generation !== wizardGeneration || adminSessionEnded || error.response?.status === 401) return;
                const status = error.response?.status, detail = error.response?.data?.detail;
                if (detail?.saved === true) {
                    nodeWizard.outcome = 'saved'; nodeWizard.step = 3;
                    nodeWizard.error = '节点已保存，但核心未应用。请刷新列表检查配置与运行状态，不要重复创建。';
                    await Promise.all([fetchInbounds(), fetchCoreStatus.fresh()]);
                } else if (!status || status >= 500 || [404, 408, 409].includes(status)) {
                    nodeWizard.outcome = 'uncertain'; nodeWizard.step = 3;
                    nodeWizard.error = inboundEditorError(error, '创建节点') + ' 创建结果未能确认，节点可能已保存。请刷新列表核对，不要重复创建。';
                } else nodeWizard.error = inboundEditorError(error, '创建节点');
            } finally {
                if (generation === wizardGeneration) { nodeWizard.busy = false; wizardController = null; }
            }
        };
        const wizardDeliver = () => {
            if (nodeWizard.busy || !nodeWizard.row || nodeWizard.outcome !== 'created' || adminSessionEnded) return;
            const row = nodeWizard.row, server = nodeWizard.serverName, purpose = nodeWizard.purpose;
            if (purpose === 'device' && row.share_eligible !== true) return;
            if (purpose === 'server' && row.upstream_export_eligible !== true) return;
            closeNodeWizard();
            if (purpose === 'server') openUpstreamExport(row, server);
            else void showQrCode(row);
        };
        watch(currentView, closeNodeWizard, {flush:'sync'});
        clearAdminSecrets.push(closeNodeWizard);
        addEventListener('pagehide', closeNodeWizard);
        if (!globalThis.VUI_NAV) addEventListener('popstate', closeNodeWizard);
        globalThis.VUI_NAV?.guard(canLeaveNodeWizard);
        const openSimpleOperation = task => {
            if (savingInbound.value || nodeWizard.busy || adminSessionEnded) return;
            if (!globalThis.VUI_SIMPLE_OPERATIONS) { ElMessage.error('操作界面尚未加载，请刷新重试。'); return; }
            void globalThis.VUI_SIMPLE_OPERATIONS.open(task).catch(()=>ElMessage.error('操作界面未能打开，请重试。'));
        };
        const openSimpleEdit = row => {
            if (!validInboundRevision(row.mutation_revision)) { ElMessage.error('请刷新节点后再选择。'); return; }
            void globalThis.VUI_SIMPLE_OPERATIONS?.open('edit',{id:row.id,revision:row.mutation_revision});
        };
        const openSimpleShare = row => {
            if (!validInboundRevision(row.mutation_revision)||!validInboundRevision(row.content_revision)) { ElMessage.error('请刷新节点后再分享。'); return; }
            void globalThis.VUI_SIMPLE_OPERATIONS?.open('share',{id:row.id,revision:row.mutation_revision,contentRevision:row.content_revision});
        };
        addEventListener('vui:operations-changed', () => { if (!adminSessionEnded) { void fetchInbounds(); void fetchCoreStatus.fresh(); } });
        addEventListener('vui:advanced-node', event => {
            if (adminSessionEnded || savingInbound.value) return;
            if (!event.detail) { void openAdvancedInbound(); return; }
            const row=inbounds.value.find(value=>value.id===event.detail.id && value.mutation_revision===event.detail.revision);
            if (!row) { ElMessage.error('节点已变化，请刷新后重新选择。'); return; }
            void openEditInbound(row);
        });
        const togglingInbound = ref(null);
        const toggleInbound = async row => {
            if (togglingInbound.value !== null || savingInbound.value || adminSessionEnded) return;
            if (!validInboundRevision(row.mutation_revision)) {
                ElMessage.error('无法确认节点版本，请刷新节点列表。'); return;
            }
            togglingInbound.value = row.id;
            try {
                await axios.put(`/api/inbounds/${row.id}`, {
                    expected_instance_revision:row.mutation_revision, enable:!row.enable
                }, {timeout:20000});
                if (!adminSessionEnded) ElMessage.success('节点开关已保存，请查看运行状态。');
            } catch (error) {
                if (adminSessionEnded || error.response?.status === 401) return;
                ElMessage.error(error.response?.data?.detail?.saved === true ?
                    '开关已保存，但核心未应用，请检查运行状态。' : inboundEditorError(error, '切换节点'));
            } finally {
                if (!adminSessionEnded) await Promise.all([fetchInbounds(), fetchCoreStatus.fresh()]);
                togglingInbound.value = null;
            }
        };

        const openEditInbound = async (row) => {
            if (savingInbound.value || adminSessionEnded) return;
            const generation = ++inboundEditorGeneration;
            // Capture the displayed instance before any asynchronous read. A
            // refreshed row (or recycled integer ID) cannot redefine this intent.
            const editingId = row.id, revision = row.mutation_revision;
            if (!validInboundRevision(revision)) {
                ElMessage.error('无法确认节点版本，请刷新节点列表或重新加载页面。');
                return;
            }
            try {
                const [state] = await Promise.all([
                    axios.get(`/api/inbounds/${editingId}/editor`),
                    loadManagedCertificates(generation, false)
                ]);
                if (generation !== inboundEditorGeneration || adminSessionEnded || savingInbound.value) return;
                const value = state.data;
                if (value?.certificate_binding_unavailable) {
                    ElMessage.error('无法读取节点原证书绑定，请稍后重试；尚未修改节点配置。');
                    return;
                }
                if (!validInboundRevision(value?.mutation_revision)) {
                    ElMessage.error('无法确认节点版本，请刷新节点列表或重新加载页面。');
                    return;
                }
                if (value.id !== editingId || value.mutation_revision !== revision) {
                    ElMessage.error('节点已发生变化，请刷新节点列表并重新打开编辑。');
                    return;
                }
                editingInboundId.value = editingId;
                // Bound only to this verified editor snapshot, never list refresh.
                editingInboundRevision = value.mutation_revision;
                newInbound.core = value.core;
                newInbound.protocol = value.protocol;
                newInbound.remark = value.remark || '';
                newInbound.port = value.port;
                newInbound.enable = Boolean(value.enable);
                // Absence in an older response must not turn an update into a reset.
                const savedMode = value.routing_mode;
                newInbound.routing_mode = knownRoutingMode(savedMode) ? savedMode : null;
                newInbound.settings = {};
                newInbound.stream_settings = {};
                resetInboundProfile();
                Object.assign(newInbound.profile, value.profile || {});
                newInbound.certificate_id = value.certificate_id || null;
                certificateMode.value = newInbound.certificate_id ? 'managed' : 'manual';
                automaticCertificateId = null;
                tlsCertificateDraft = null;
                inboundCredentials.value = value.credentials || {};
                showAddInbound.value = true;
            } catch (error) {
                if (generation !== inboundEditorGeneration || adminSessionEnded || error.response?.status === 401) return;
                ElMessage.error(inboundEditorError(error, '读取节点配置'));
            }
        };

        const resetInboundProfile = () => {
            newInbound.certificate_id = null;
            const p = newInbound.profile;
            p.security = (
                ['trojan', 'hysteria2', 'tuic'].includes(newInbound.protocol)
                ? 'tls'
                : 'none'
            );
            p.transport = (
                newInbound.core === 'xray'
                ? 'raw'
                : (
                    ['hysteria2', 'tuic'].includes(newInbound.protocol)
                    ? 'quic'
                    : 'direct'
                )
            );
            p.flow = '';
            p.server_name = '';
            p.certificate_path = '';
            p.key_path = '';
            p.path = '/';
            p.host = '';
            p.service_name = '';
            p.xhttp_mode = 'auto';
            p.xhttp_alpn_h2 = false;
            p.reality_target = '';
            p.reality_server_name = '';
            p.reality_short_id = '';
            p.reality_uuid = '';
            p.reality_uuid_set = false;
            p.reality_short_id_set = false;
            p.reality_private_key_set = false;
            p.client_fingerprint = ['hysteria2', 'tuic'].includes(newInbound.protocol) ? '' : 'chrome';
            p.skip_cert_verify = false;
            p.shadowsocks_method = 'aes-128-gcm';
            p.shadowsocks_password_set = false;
            p.up_mbps = ['hysteria2', 'tuic'].includes(newInbound.protocol) ? null : 100;
            p.down_mbps = ['hysteria2', 'tuic'].includes(newInbound.protocol) ? null : 100;
            p.hysteria2_password = '';
            p.hysteria2_password_set = false;
            p.obfs_type = '';
            p.obfs_password = '';
            p.obfs_password_set = false;
            p.congestion_control = newInbound.protocol === 'tuic' ? 'cubic' : 'bbr';
            p.tuic_uuid = ''; p.tuic_password = '';
            p.tuic_uuid_set = false; p.tuic_password_set = false;
            p.udp_relay_mode = 'native';
            p.zero_rtt_handshake = false;
        };

        const rememberTLSCertificate = () => {
            if (newInbound.profile.security === 'tls') tlsCertificateDraft = {
                certificate_id: newInbound.certificate_id, mode: certificateMode.value,
                server_name: newInbound.profile.server_name,
                certificate_path: newInbound.profile.certificate_path, key_path: newInbound.profile.key_path
            };
        };
        const restoreTLSCertificate = () => {
            if (newInbound.profile.security !== 'tls' || !tlsCertificateDraft) return;
            const {certificate_id, mode, ...profile} = tlsCertificateDraft;
            Object.assign(newInbound.profile, profile);
            newInbound.certificate_id = certificate_id;
            certificateMode.value = mode;
        };
        const resetProtocolWithCommonFields = () => {
            rememberTLSCertificate();
            const p = newInbound.profile;
            const common = {server_name:p.server_name, certificate_path:p.certificate_path, key_path:p.key_path};
            resetInboundProfile();
            Object.assign(p, common);
            restoreTLSCertificate();
            reconcileManagedCertificate();
        };
        const onCoreChanged = () => {
            if (savingInbound.value || adminSessionEnded || editingInboundId.value) return;
            newInbound.protocol = 'vless';
            newInbound.settings = {};
            newInbound.stream_settings = {};
            resetProtocolWithCommonFields();
        };

        const onProtocolChanged = () => {
            if (savingInbound.value || adminSessionEnded || editingInboundId.value) return;
            newInbound.settings = {};
            newInbound.stream_settings = {};
            resetProtocolWithCommonFields();
        };

        const clearManagedCertificate = () => {
            automaticCertificateId = null;
        };

        const onSecurityChanged = () => {
            if (savingInbound.value || adminSessionEnded) return;
            if (newInbound.profile.security !== 'tls' && (newInbound.certificate_id ||
                newInbound.profile.certificate_path || newInbound.profile.key_path)) {
                tlsCertificateDraft = {
                    certificate_id:newInbound.certificate_id, mode:certificateMode.value,
                    server_name:newInbound.profile.server_name,
                    certificate_path:newInbound.profile.certificate_path, key_path:newInbound.profile.key_path
                };
                newInbound.certificate_id = null;
                newInbound.profile.certificate_path = '';
                newInbound.profile.key_path = '';
            }
            restoreTLSCertificate();
            if (newInbound.core === 'sing-box' && newInbound.protocol === 'vless') {
                if (newInbound.profile.security === 'reality') {
                    newInbound.profile.transport = 'direct';
                    newInbound.profile.flow = 'xtls-rprx-vision';
                    newInbound.profile.client_fingerprint = 'chrome';
                    newInbound.profile.skip_cert_verify = false;
                } else if (newInbound.profile.flow === 'xtls-rprx-vision') {
                    newInbound.profile.flow = '';
                }
            }
            reconcileManagedCertificate();
        };

        const saveInbound = async () => {
            if (savingInbound.value || !showAddInbound.value || adminSessionEnded) return;
            certificateReadGeneration++;
            if (managedCertificateState.value === 'loading') managedCertificateState.value = 'error';
            savingInbound.value = true;
            const generation = inboundEditorGeneration, editingId = editingInboundId.value;
            const revision = editingInboundRevision;
            const routing = knownRoutingMode(newInbound.routing_mode) ? {routing_mode: newInbound.routing_mode} : {};
            try {
                if (editingId && !validInboundRevision(revision)) {
                    ElMessage.error('无法确认节点版本，请刷新节点列表并重新打开编辑。');
                    return;
                }
                if (newInbound.profile.security === 'tls' && certificateMode.value === 'managed') {
                    if (!managedCertificateEligible.value) {
                        ElMessage.error('当前核心、协议或传输不支持托管证书；Xray 仅支持 VLESS / RAW TCP / TLS / 空 flow。');
                        return;
                    }
                    if (managedCertificateState.value !== 'ready') {
                        ElMessage.error('证书列表尚未成功读取，请重试后保存。');
                        return;
                    }
                    const selected = managedCertificates.value.find(c => c.id === newInbound.certificate_id);
                    if (!selected || !certificateMatchesSNI(selected)) {
                        ElMessage.error(selected ? 'SNI 与所选证书域名不匹配，请核对后保存。' : '请选择一张可用的正式证书后保存。');
                        return;
                    }
                }
                let profile = { ...newInbound.profile };
                if (newInbound.core === 'sing-box' && newInbound.protocol === 'vless' && profile.security === 'reality' && profile.transport === 'direct') {
                    // Send only this dedicated profile's visible fields. The
                    // server validates persisted state before any edit so an
                    // unsupported import cannot be silently repaired here.
                    const fields = ['security', 'transport', 'flow', 'reality_target', 'reality_server_name',
                        'reality_uuid', 'reality_short_id', 'client_fingerprint', 'skip_cert_verify'];
                    profile = Object.fromEntries(fields.map(key => [key, profile[key]]));
                } else {
                    for (const key of ['reality_uuid', 'reality_uuid_set', 'reality_short_id_set', 'reality_private_key_set']) {
                        delete profile[key];
                    }
                }
                const certificateId = profile.security === 'tls' ? newInbound.certificate_id || null : null;
                let res;
                if (editingId) {
                    res = await axios.put(`/api/inbounds/${editingId}`, {
                        expected_instance_revision: revision,
                        ...routing,
                        remark: newInbound.remark,
                        port: newInbound.port,
                        profile,
                        certificate_id: certificateId,
                        enable: newInbound.enable
                    });
                    if (generation === inboundEditorGeneration && !adminSessionEnded) ElMessage.success('节点已更新');
                } else {
                    res = await axios.post('/api/inbounds', {
                        ...routing,
                        core: newInbound.core,
                        remark: newInbound.remark,
                        protocol: newInbound.protocol,
                        port: newInbound.port,
                        profile,
                        certificate_id: certificateId,
                        enable: newInbound.enable
                    });
                    if (generation === inboundEditorGeneration && !adminSessionEnded) ElMessage.success('节点已添加');
                }
                if (generation !== inboundEditorGeneration || adminSessionEnded) return;
                closeInboundEditor();
                await Promise.all([fetchInbounds(), fetchCoreStatus.fresh()]);
            } catch (error) {
                if (generation !== inboundEditorGeneration || adminSessionEnded || error.response?.status === 401) return;
                const detail = error.response?.data?.detail;
                if (error.response?.status === 409 && detail?.saved === true) {
                    closeInboundEditor();
                    ElMessage.warning('设置已保存为期望状态，但核心未应用。请检查节点配置与核心状态后重试。');
                    await Promise.all([fetchInbounds(), fetchCoreStatus.fresh()]);
                    return;
                }
                ElMessage.error(inboundEditorError(error, editingId ? '更新节点' : '添加节点'));
            } finally { savingInbound.value = false; }
        };

        const deleteInbound = async (row) => {
            if (deletingInbound || adminSessionEnded) return;
            const deletingId = row.id, revision = row.mutation_revision;
            if (!validInboundRevision(revision)) {
                ElMessage.error('删除节点未完成：' + inboundInstanceUnknown);
                return;
            }
            deletingInbound = true;
            try {
                await ElMessageBox.confirm(
                    `确认永久删除“${row.remark || row.protocol}”（端口${row.port}）？配置和此节点的连接将删除。`,
                    '删除节点',
                    {
                        confirmButtonText: '确认删除',
                        cancelButtonText: '取消',
                        type: 'warning'
                    }
                );
                if (adminSessionEnded) return;
                // The guarded action deliberately has no legacy DELETE fallback:
                // a downlevel backend must reject, not ignore the condition.
                await axios.post(`/api/inbounds/${deletingId}/delete`, {expected_instance_revision: revision});
                if (adminSessionEnded) return;
                ElMessage.success('节点已删除');
                await Promise.all([fetchInbounds(), fetchCoreStatus.fresh()]);
            } catch (error) {
                if (error === 'cancel' || error === 'close' || adminSessionEnded || error.response?.status === 401) return;
                const detail = error.response?.data?.detail;
                if (error.response?.status === 409 && detail?.saved === true) {
                    ElMessage.warning('节点删除已保存，但运行配置未更新，旧入口可能仍可用：' + (detail.message || '运行验证未通过'));
                    // The database and runtime have separate commit points. Read
                    // authoritative rows/status; never remove a row locally or
                    // carry its previous applied badge forward as confirmation.
                    await Promise.all([fetchInbounds(), fetchCoreStatus.fresh()]);
                    return;
                }
                ElMessage.error((error.response?.status===409&&typeof detail==='string'&&detail===inboundInstanceChanged)?'节点已发生变化，请刷新后重新选择。':'删除节点未完成，请刷新节点和运行状态后核对。');
            } finally { deletingInbound = false; }
        };

        const restartCore = async (core) => {
            if (restartingCore.value) return;
            if(!confirm('重启'+core+'节点组会短暂中断其中全部节点连接。确认重启？'))return;
            restartingCore.value = core;
            try {
                const res = await axios.post(
                    `/api/cores/${encodeURIComponent(core)}/restart`
                );
                if (res.data.valid === false) {
                    ElMessage.error(
                        `${core}配置检查未通过，运行配置保持，请查看节点设置。`
                    );
                } else {
                    ElMessage.success(`${core}节点组已重启，请查看实际运行状态`);
                }
                await fetchCoreStatus.fresh();
            } catch (error) {
                ElMessage.error(
                    `${core}重启未完成，请刷新运行状态后核对。`
                );
            } finally {
                restartingCore.value = '';
            }
        };

        const updateProfile = () => { handleSelect('account'); };

        const showQrCode = async (row) => {
            if (adminSessionEnded) return;
            clearQrCode();
            qrRetryRow = {id: row.id};
            const generation = qrGeneration;
            qrController = new AbortController();
            showQrDialog.value = true;
            qrBusy.value = true;
            const current = () => generation === qrGeneration && showQrDialog.value && !adminSessionEnded;
            try {
                const res = await axios.get(
                    `/api/subscription/link/${row.id}`,
                    { responseType: 'text', signal: qrController.signal, timeout: 15000 }
                );
                if (!current()) return;
                const link = res.data;
                if (typeof link !== 'string' || !link.length) {
                    qrNotice.value = '此节点暂时没有可分享的链接。';
                    return;
                }
                qrLink.value = link;
                await nextTick(); // The dialog is lazily mounted by Vue.
                if (!current()) return;
                qrRenderTimer = setTimeout(() => {
                    if (!current()) return;
                    qrRenderTimer = null;
                    const container = document.getElementById('qrcode');
                    if (!container) return;
                    container.textContent = '';
                    container.title = '';
                    try {
                        new QRCode(container, { text: link, width: 200, height: 200 });
                    } catch (_) {
                        container.textContent = '';
                        container.title = '';
                        qrNotice.value = '二维码生成失败，请复制下方链接。';
                    }
                }, 0);
            } catch (error) {
                if (current()) {
                    const status = error.response?.status;
                    let detail = error.response?.data?.detail;
                    if (typeof error.response?.data === 'string' && error.response.data.length < 2048) {
                        try { detail = JSON.parse(error.response.data).detail; } catch (_) {}
                    }
                    // Only known server messages may reach the dialog. Unknown
                    // bodies/errors may contain credentials or internal paths.
                    const messages = {
                        SHARE_NODE_INELIGIBLE: '此节点已停用或设置了到期时间，暂不符合当前分享格式要求，请编辑节点后重试。',
                        SHARE_PROFILE_UNSUPPORTED: '此节点的协议、传输或 TLS 配置暂不符合已支持的分享格式。Xray 目前支持单用户 VLESS RAW/TCP TLS、空 flow 和有效 SNI。'
                    };
                    qrNotice.value = messages[detail?.code] || (status === 404 ? '节点已不存在，请刷新节点列表。' :
                        status === 403 ? '没有分享此节点的权限，请核对授权。' :
                        status === 409 ? '节点配置不符合当前分享格式，请检查协议、传输和 TLS 设置。' :
                        '获取分享链接失败，请检查网络后重试；原节点未更改。');
                }
            } finally {
                if (current()) { qrBusy.value = false; qrController = null; }
            }
        };

        const retryShare = () => {
            if (qrRetryRow && !qrBusy.value && !adminSessionEnded) return showQrCode(qrRetryRow);
        };

        const copyLink = async () => {
            if (!showQrDialog.value || !qrLink.value || qrBusy.value || adminSessionEnded) return;
            const generation = qrGeneration;
            const current = () => generation === qrGeneration && showQrDialog.value && !adminSessionEnded;
            try {
                // A write already handed to the browser cannot be recalled.
                // Closing prevents new writes and invalidates later UI feedback.
                await navigator.clipboard.writeText(qrLink.value);
                if (current()) qrNotice.value = '已复制含连接凭据的链接。';
            } catch (_) {
                if (!current()) return;
                qrNotice.value = '复制失败，请手动选择下方链接；内容含连接凭据。';
                const output = document.getElementById('share-link');
                if (output) { output.focus(); output.select(); }
            }
        };

        const subscriptionUrl = (format) => {
            const paths = {
                raw: '/api/subscription/raw',
                mihomo: '/api/subscription/mihomo.yaml',
                singbox: '/api/subscription/sing-box.json'
            };
            return `${location.origin}${paths[format]}`;
        };

        const copySubscription = async (format) => {
            await navigator.clipboard.writeText(subscriptionUrl(format));
            ElMessage.success('已复制配置下载地址；需要持续订阅请到订阅分享生成专用地址。');
        };

        const fetchSiteFiles = async () => {
            try {
                const res = await axios.get('/api/files/list_files');
                siteFiles.value = res.data;
            } catch (error) {
                ElMessage.error('站点文件读取失败，请稍后重试。');
            }
        };

        const handleUploadSuccess = () => {
            ElMessage.success('站点文件已上传。');
            fetchSiteFiles();
        };

        const handleUploadError = () => {
            ElMessage.error('上传未完成，请检查文件后重试。');
        };

        onMounted(async () => {
            applyRoute();
            try {
                currentUser.value = (await axios.get('/api/auth/me')).data;
            } catch (error) {
                if (adminSessionEnded) return;
                const verified = await checkAdminSession();
                if (!verified) return; // Never poll without current server authentication.
                currentUser.value = verified;
            }
            if (adminSessionEnded) return;
            statusRefreshReady.value = true;
            await Promise.all([
                Promise.all([fetchSystemStatus.fresh(), fetchCoreStatus.fresh()]).then(() => {
                    if (adminSessionEnded) return;
                    statusPollers.push(visibleStatusPoller(fetchSystemStatus, 3000));
                    statusPollers.push(visibleStatusPoller(fetchCoreStatus, 10000));
                }),
                fetchInbounds(),
                fetchRouting(),
                fetchMihomoWarnings()
            ]);
        });

        return {
            currentView,
            systemStatus,
            coreStatus,
            systemOsKnown,
            systemMetricKnown,
            coreFieldKnown,
            statusRefreshReady,
            refreshingStatus,
            refreshStatus,
            inbounds,
            inboundSearch,
            filteredInbounds,
            showAddInbound,
            editingInboundId,
            inboundCredentials,
            savingInbound,
            inboundSavedRouting,
            inboundAppliedRouting,
            inboundRoutingHint,
            closeInboundEditor,
            beforeInboundEditorClose,
            newInbound,
            protocolOptions,
            colors,
            restartingCore,
            currentUser,
            userForm,
            mihomoWarnings,
            managedCertificates,
            managedCertificateState,
            managedCertificateEligible,
            managedCertificateHint,
            certificateMode,
            retryManagedCertificates,
            reconcileManagedCertificate,
            onManagedCertificateChanged,
            onCertificateModeChanged,
            showQrDialog,
            qrLink,
            qrBusy,
            qrNotice,
            retryShare,
            closeQrDialog,
            beforeQrDialogClose,
            upstreamExport,
            openUpstreamExport,
            closeUpstreamExport,
            requestUpstreamExport,
            copyUpstreamExport,
            downloadUpstreamExport,
            routingCatalog,
            routingPreview,
            routingForm,
            presetGroups,
            enabledPresetCount,
            routingRuleCount,
            savingRouting,
            handleSelect,
            fetchInbounds,
            fetchRouting,
            saveRouting,
            resetRoutingDefaults,
            setCategoryEnabled,
            addIntranetZone,
            removeIntranetZone,
            openAddInbound,
            openSimpleOperation,
            openSimpleEdit,
            openSimpleShare,
            openAdvancedInbound,
            nodeWizard,
            wizardNameHint,
            wizardPortHint,
            wizardReady,
            loadWizardCertificates,
            closeNodeWizard,
            beforeNodeWizardClose,
            wizardAdvanced,
            createSimpleNode,
            wizardDeliver,
            refreshWizardResult,
            toggleInbound,
            togglingInbound,
            openEditInbound,
            onCoreChanged,
            onProtocolChanged,
            clearManagedCertificate,
            onSecurityChanged,
            resetInboundProfile,
            saveInbound,
            deleteInbound,
            updateProfile,
            restartCore,
            showQrCode,
            copyLink,
            subscriptionUrl,
            copySubscription,
            siteFiles,
            fetchSiteFiles,
            handleUploadSuccess,
            handleUploadError
        };
    }
});

app.component('Odometer', Odometer);
app.component('Connection', Connection);
app.component('Setting', Setting);
app.component('Lock', Lock);
app.component('Refresh', Refresh);
app.component('ArrowDown', ArrowDown);
app.component('Document', Document);
app.component('UploadFilled', UploadFilled);
app.component('Link', Link);

app.use(ElementPlus);
app.mount('#app');
