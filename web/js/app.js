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
    location.replace('/login');
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
        const currentView = ref(location.hash === '#inbounds' ? 'inbounds' : 'dashboard');
        const systemStatus = ref({});
        const coreStatus = ref({});
        // Pending polls retain the last confirmed display; rejected reads hide
        // only that endpoint. Missing/malformed fields never imply false or zero.
        const systemStatusKnown = ref(false);
        const coreStatusKnown = ref(false);
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
        let qrGeneration = 0, qrController = null, qrRenderTimer = null;
        const clearQrCode = () => {
            qrGeneration++;
            if (qrController) qrController.abort();
            qrController = null;
            if (qrRenderTimer !== null) clearTimeout(qrRenderTimer);
            qrRenderTimer = null;
            qrLink.value = '';
            qrBusy.value = false;
            qrNotice.value = '';
            const container = document.getElementById('qrcode');
            if (container) { container.textContent = ''; container.title = ''; }
            const output = document.getElementById('share-link');
            if (output) output.value = '';
        };
        const closeQrDialog = () => {
            clearQrCode();
            showQrDialog.value = false;
        };
        // Element Plus emits v-model=false only after its leave transition.
        // Invalidate before Escape/header/backdrop dismissal starts, not after
        // a late response can repopulate the closing dialog.
        const beforeQrDialogClose = (done) => {
            closeQrDialog();
            done();
        };
        // Keep the synchronous watcher for direct model changes too.
        watch(showQrDialog, (open) => { if (!open) clearQrCode(); }, { flush: 'sync' });
        watch(currentView, closeQrDialog, { flush: 'sync' });
        clearAdminSecrets.push(closeQrDialog);
        addEventListener('pagehide', closeQrDialog);
        addEventListener('popstate', closeQrDialog);
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
            upstreamExport.inboundId = null;
            upstreamExport.name = '';
            upstreamExport.server = '';
            upstreamExport.acknowledged = false;
        };
        const openUpstreamExport = (row) => {
            closeUpstreamExport();
            upstreamExport.inboundId = row.id;
            upstreamExport.name = row.remark || `${row.protocol}:${row.port}`;
            upstreamExport.format = 'uri';
            upstreamExport.open = true;
        };
        watch(() => [upstreamExport.server, upstreamExport.format, upstreamExport.inboundId], () => {
            clearUpstreamExport();
            upstreamExport.acknowledged = false;
        }, { flush: 'sync' });
        watch(() => upstreamExport.acknowledged, clearUpstreamExport, { flush: 'sync' });
        watch(() => upstreamExport.open, (open) => { if (!open) closeUpstreamExport(); }, { flush: 'sync' });
        watch(currentView, closeUpstreamExport, { flush: 'sync' });
        clearAdminSecrets.push(closeUpstreamExport);
        addEventListener('pagehide', closeUpstreamExport);
        addEventListener('popstate', closeUpstreamExport);

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
        const closeInboundEditor = () => {
            inboundEditorGeneration++;
            showAddInbound.value = false;
            editingInboundId.value = null;
            editingInboundRevision = null;
            inboundCredentials.value = {};
            newInbound.routing_mode = 'inherit';
            resetInboundProfile();
        };
        watch(currentView, closeInboundEditor, { flush: 'sync' });
        clearAdminSecrets.push(() => { closeInboundEditor(); inboundListGeneration++; inbounds.value = []; });
        addEventListener('pagehide', closeInboundEditor);
        addEventListener('popstate', closeInboundEditor);

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

        const handleSelect = (key) => {
            closeQrDialog();
            if (key === 'proxy') { location.assign('/proxy'); return; }
            if (key === 'certificates') { location.assign('/certificates'); return; }
            if (['routing', 'subscriptions'].includes(key)) { location.assign('/workspace'); return; }
            if (key === 'settings') { location.assign('/account'); return; }
            if (key === 'site') {
                ElMessage.warning('站点托管已暂时隔离，现有文件仍保留在服务器。');
                return;
            }
            currentView.value = key;
            if (key === 'inbounds') fetchInbounds();
            if (key === 'routing') fetchRouting();
            if (key === 'settings') userForm.username = currentUser.value.username;
            if (key === 'site') fetchSiteFiles();
            if (key === 'subscriptions') {
                fetchCoreStatus.fresh();
                fetchRoutingPreview();
                fetchMihomoWarnings();
            }
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

        const fetchInbounds = async () => {
            closeQrDialog();
            closeUpstreamExport();
            const generation = ++inboundListGeneration;
            try {
                const res = await axios.get('/api/inbounds');
                if (generation !== inboundListGeneration || adminSessionEnded) return;
                inbounds.value = res.data;
            } catch (error) {
                if (generation !== inboundListGeneration || adminSessionEnded || error.response?.status === 401) return;
                // A failed refresh cannot confirm previously shown applied state.
                inbounds.value = inbounds.value.map(row => ({...row, routing_status: null}));
                ElMessage.error('Failed to load inbounds');
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

        const loadManagedCertificates = async () => {
            try {
                managedCertificates.value = (await axios.get('/api/certificates')).data
                    .filter(c => c.environment === 'production' && c.revision && c.days_remaining >= 0);
            } catch {
                managedCertificates.value = [];
            }
        };

        const openAddInbound = async () => {
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
            await loadManagedCertificates();
            if (generation !== inboundEditorGeneration || adminSessionEnded) return;
            showAddInbound.value = true;
        };

        const openEditInbound = async (row) => {
            if (savingInbound.value || adminSessionEnded) return;
            const generation = ++inboundEditorGeneration;
            // Capture the displayed instance before any asynchronous read. A
            // refreshed row (or recycled integer ID) cannot redefine this intent.
            const editingId = row.id, revision = row.mutation_revision;
            if (!validInboundRevision(revision)) {
                ElMessage.error('Failed to load inbound editor: ' + inboundInstanceUnknown);
                return;
            }
            try {
                const [state] = await Promise.all([
                    axios.get(`/api/inbounds/${editingId}/editor`),
                    loadManagedCertificates()
                ]);
                if (generation !== inboundEditorGeneration || adminSessionEnded) return;
                const value = state.data;
                if (!validInboundRevision(value?.mutation_revision)) {
                    ElMessage.error('Failed to load inbound editor: ' + inboundInstanceUnknown);
                    return;
                }
                if (value.id !== editingId || value.mutation_revision !== revision) {
                    ElMessage.error('Failed to load inbound editor: ' + inboundInstanceChanged);
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
                inboundCredentials.value = value.credentials || {};
                showAddInbound.value = true;
            } catch (error) {
                if (generation !== inboundEditorGeneration || adminSessionEnded || error.response?.status === 401) return;
                ElMessage.error(error.response?.data?.detail || 'Failed to load inbound editor');
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

        const onCoreChanged = () => {
            newInbound.protocol = 'vless';
            newInbound.settings = {};
            newInbound.stream_settings = {};
            resetInboundProfile();
        };

        const onProtocolChanged = () => {
            newInbound.settings = {};
            newInbound.stream_settings = {};
            resetInboundProfile();
        };

        const clearManagedCertificate = () => {
            if (!editingInboundId.value) return;
            newInbound.profile.certificate_path = '';
            newInbound.profile.key_path = '';
        };

        const onSecurityChanged = () => {
            if (newInbound.profile.security !== 'tls' && newInbound.certificate_id) {
                newInbound.certificate_id = null;
                newInbound.profile.certificate_path = '';
                newInbound.profile.key_path = '';
            }
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
        };

        const saveInbound = async () => {
            if (savingInbound.value || !showAddInbound.value || adminSessionEnded) return;
            savingInbound.value = true;
            const generation = inboundEditorGeneration, editingId = editingInboundId.value;
            const revision = editingInboundRevision;
            const routing = knownRoutingMode(newInbound.routing_mode) ? {routing_mode: newInbound.routing_mode} : {};
            try {
                if (editingId && !validInboundRevision(revision)) {
                    ElMessage.error('Failed to update inbound: ' + inboundInstanceUnknown);
                    return;
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
                    if (generation === inboundEditorGeneration && !adminSessionEnded) ElMessage.success('Inbound updated successfully');
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
                    if (generation === inboundEditorGeneration && !adminSessionEnded) ElMessage.success('Inbound added successfully');
                }
                if (generation !== inboundEditorGeneration || adminSessionEnded) return;
                closeInboundEditor();
                await Promise.all([fetchInbounds(), fetchCoreStatus.fresh()]);
            } catch (error) {
                if (generation !== inboundEditorGeneration || adminSessionEnded || error.response?.status === 401) return;
                const detail = error.response?.data?.detail;
                if (error.response?.status === 409 && detail?.saved === true) {
                    closeInboundEditor();
                    ElMessage.warning('设置已保存为期望状态，但核心未应用：' + (detail.message || 'validation failed'));
                    await Promise.all([fetchInbounds(), fetchCoreStatus.fresh()]);
                    return;
                }
                ElMessage.error(
                    (editingId ? 'Failed to update inbound: ' : 'Failed to add inbound: ') +
                    (typeof detail === 'string' ? detail : detail?.message || error.message)
                );
            } finally { savingInbound.value = false; }
        };

        const deleteInbound = async (row) => {
            if (deletingInbound || adminSessionEnded) return;
            const deletingId = row.id, revision = row.mutation_revision;
            if (!validInboundRevision(revision)) {
                ElMessage.error('Failed to delete inbound: ' + inboundInstanceUnknown);
                return;
            }
            deletingInbound = true;
            try {
                await ElMessageBox.confirm(
                    `Delete ${row.remark || row.protocol}:${row.port}?`,
                    'Warning',
                    {
                        confirmButtonText: 'OK',
                        cancelButtonText: 'Cancel',
                        type: 'warning'
                    }
                );
                if (adminSessionEnded) return;
                // The guarded action deliberately has no legacy DELETE fallback:
                // a downlevel backend must reject, not ignore the condition.
                await axios.post(`/api/inbounds/${deletingId}/delete`, {expected_instance_revision: revision});
                if (adminSessionEnded) return;
                ElMessage.success('Deleted successfully');
                await Promise.all([fetchInbounds(), fetchCoreStatus.fresh()]);
            } catch (error) {
                if (error === 'cancel' || error === 'close' || adminSessionEnded || error.response?.status === 401) return;
                const detail = error.response?.data?.detail;
                if (error.response?.status === 409 && detail?.saved === true) {
                    ElMessage.warning('节点删除已保存，但运行配置未更新，旧入口可能仍可用：' + (detail.message || 'validation failed'));
                    // The database and runtime have separate commit points. Read
                    // authoritative rows/status; never remove a row locally or
                    // carry its previous applied badge forward as confirmation.
                    await Promise.all([fetchInbounds(), fetchCoreStatus.fresh()]);
                    return;
                }
                ElMessage.error('Failed to delete inbound: ' +
                    (typeof detail === 'string' ? detail : detail?.message || error.message));
            } finally { deletingInbound = false; }
        };

        const restartCore = async (core) => {
            if (restartingCore.value) return;
            restartingCore.value = core;
            try {
                const res = await axios.post(
                    `/api/cores/${encodeURIComponent(core)}/restart`
                );
                if (res.data.valid === false) {
                    ElMessage.error(
                        res.data.validation_output ||
                        `${core} config validation failed`
                    );
                } else {
                    ElMessage.success(`${core} restarted`);
                }
                await fetchCoreStatus.fresh();
            } catch (error) {
                ElMessage.error(
                    error.response?.data?.detail ||
                    `Failed to restart ${core}`
                );
            } finally {
                restartingCore.value = '';
            }
        };

        const updateProfile = () => { closeQrDialog(); location.assign('/account'); };

        const showQrCode = async (row) => {
            if (adminSessionEnded) return;
            clearQrCode();
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
            } catch (_) {
                if (current()) qrNotice.value = '获取分享链接失败，请重试。';
            } finally {
                if (current()) { qrBusy.value = false; qrController = null; }
            }
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
            ElMessage.success('Subscription URL copied');
        };

        const fetchSiteFiles = async () => {
            try {
                const res = await axios.get('/api/files/list_files');
                siteFiles.value = res.data;
            } catch (error) {
                ElMessage.error('Failed to list files');
            }
        };

        const handleUploadSuccess = () => {
            ElMessage.success('Site deployed successfully');
            fetchSiteFiles();
        };

        const handleUploadError = () => {
            ElMessage.error('Upload failed');
        };

        onMounted(async () => {
            try {
                currentUser.value = (await axios.get('/api/auth/me')).data;
            } catch (error) {
                if (adminSessionEnded) return;
                const verified = await checkAdminSession();
                if (!verified) return; // Never poll without current server authentication.
                currentUser.value = verified;
            }
            if (adminSessionEnded) return;
            await Promise.all([
                fetchSystemStatus.fresh(),
                fetchCoreStatus.fresh(),
                fetchInbounds(),
                fetchRouting(),
                fetchMihomoWarnings()
            ]);
            if (adminSessionEnded) return;
            statusPollers.push(visibleStatusPoller(fetchSystemStatus, 3000));
            statusPollers.push(visibleStatusPoller(fetchCoreStatus, 10000));
        });

        return {
            currentView,
            systemStatus,
            coreStatus,
            systemOsKnown,
            systemMetricKnown,
            coreFieldKnown,
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
            newInbound,
            protocolOptions,
            colors,
            restartingCore,
            currentUser,
            userForm,
            mihomoWarnings,
            managedCertificates,
            showQrDialog,
            qrLink,
            qrBusy,
            qrNotice,
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
