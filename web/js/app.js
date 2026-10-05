addEventListener('pageshow', (event) => { if (event.persisted) location.reload(); });
// Session cookies are HttpOnly. Do not persist credentials in Web Storage.
axios.defaults.headers.common['X-VUI-Request'] = '1';
axios.interceptors.response.use(response => response, error => {
    if (error.response?.status === 401) location.replace('/login');
    return Promise.reject(error);
});

const { createApp, ref, onMounted, reactive, computed } = Vue;
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
        const inbounds = ref([]);
        const showAddInbound = ref(false);
        const editingInboundId = ref(null);
        const inboundCredentials = ref({});
        const restartingCore = ref('');
        const banIpInput = ref('');
        const showQrDialog = ref(false);
        const qrLink = ref('');
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
                fetchCoreStatus();
                fetchRoutingPreview();
                fetchMihomoWarnings();
            }
        };

        const fetchSystemStatus = async () => {
            try {
                const res = await axios.get('/api/system/status');
                systemStatus.value = res.data;
            } catch (error) {
                console.error('Failed to fetch status', error);
            }
        };

        const fetchCoreStatus = async () => {
            try {
                const res = await axios.get('/api/cores/status');
                coreStatus.value = res.data;
            } catch (error) {
                console.error('Failed to fetch core status', error);
            }
        };

        const fetchInbounds = async () => {
            try {
                const res = await axios.get('/api/inbounds');
                inbounds.value = res.data;
            } catch (error) {
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
            editingInboundId.value = null;
            inboundCredentials.value = {};
            newInbound.core = 'xray';
            newInbound.protocol = 'vless';
            newInbound.remark = '';
            newInbound.port = 443;
            newInbound.enable = true;
            newInbound.settings = {};
            newInbound.stream_settings = {};
            resetInboundProfile();
            await loadManagedCertificates();
            showAddInbound.value = true;
        };

        const openEditInbound = async (row) => {
            try {
                const [state] = await Promise.all([
                    axios.get(`/api/inbounds/${row.id}/editor`),
                    loadManagedCertificates()
                ]);
                const value = state.data;
                editingInboundId.value = row.id;
                newInbound.core = value.core;
                newInbound.protocol = value.protocol;
                newInbound.remark = value.remark || '';
                newInbound.port = value.port;
                newInbound.enable = Boolean(value.enable);
                newInbound.settings = {};
                newInbound.stream_settings = {};
                resetInboundProfile();
                Object.assign(newInbound.profile, value.profile || {});
                newInbound.certificate_id = value.certificate_id || null;
                inboundCredentials.value = value.credentials || {};
                showAddInbound.value = true;
            } catch (error) {
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
            p.client_fingerprint = newInbound.protocol === 'hysteria2' ? '' : 'chrome';
            p.skip_cert_verify = false;
            p.shadowsocks_method = 'aes-128-gcm';
            p.shadowsocks_password_set = false;
            p.up_mbps = newInbound.protocol === 'hysteria2' ? null : 100;
            p.down_mbps = newInbound.protocol === 'hysteria2' ? null : 100;
            p.hysteria2_password = '';
            p.hysteria2_password_set = false;
            p.obfs_type = '';
            p.obfs_password = '';
            p.obfs_password_set = false;
            p.congestion_control = 'bbr';
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
        };

        const saveInbound = async () => {
            try {
                const profile = { ...newInbound.profile };
                const certificateId = profile.security === 'tls' ? newInbound.certificate_id || null : null;
                let res;
                if (editingInboundId.value) {
                    res = await axios.put(`/api/inbounds/${editingInboundId.value}`, {
                        remark: newInbound.remark,
                        port: newInbound.port,
                        profile,
                        certificate_id: certificateId,
                        enable: newInbound.enable
                    });
                    ElMessage.success('Inbound updated successfully');
                } else {
                    res = await axios.post('/api/inbounds', {
                        core: newInbound.core,
                        remark: newInbound.remark,
                        protocol: newInbound.protocol,
                        port: newInbound.port,
                        profile,
                        certificate_id: certificateId,
                        enable: newInbound.enable
                    });
                    ElMessage.success('Inbound added successfully');
                }
                showAddInbound.value = false;
                editingInboundId.value = null;
                await Promise.all([fetchInbounds(), fetchCoreStatus()]);
            } catch (error) {
                const detail = error.response?.data?.detail;
                if (error.response?.status === 409 && detail?.saved) {
                    ElMessage.warning('设置已保存为期望状态，但核心未应用：' + (detail.message || 'validation failed'));
                    await Promise.all([fetchInbounds(), fetchCoreStatus()]);
                    return;
                }
                ElMessage.error(
                    (editingInboundId.value ? 'Failed to update inbound: ' : 'Failed to add inbound: ') +
                    (typeof detail === 'string' ? detail : detail?.message || error.message)
                );
            }
        };

        const deleteInbound = async (row) => {
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
                await axios.delete(`/api/inbounds/${row.id}`);
                ElMessage.success('Deleted successfully');
                await Promise.all([fetchInbounds(), fetchCoreStatus()]);
            } catch (error) {
                if (error !== 'cancel') {
                    ElMessage.error('Failed to delete inbound');
                }
            }
        };

        const restartCore = async (core) => {
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
                await fetchCoreStatus();
            } catch (error) {
                ElMessage.error(
                    error.response?.data?.detail ||
                    `Failed to restart ${core}`
                );
            } finally {
                restartingCore.value = '';
            }
        };

        const updateProfile = () => { location.assign('/account'); };

        const banIp = async () => {
            if (!banIpInput.value) return;
            try {
                await axios.post(
                    `/api/security/ban_ip?ip=${encodeURIComponent(banIpInput.value)}`
                );
                ElMessage.success(`IP ${banIpInput.value} banned`);
                banIpInput.value = '';
            } catch (error) {
                ElMessage.error('Failed to ban IP');
            }
        };

        const showQrCode = async (row) => {
            try {
                const res = await axios.get(
                    `/api/subscription/link/${row.id}`,
                    { responseType: 'text' }
                );
                const link = res.data;
                if (!link) {
                    ElMessage.warning('This node does not have enough share-link data yet');
                    return;
                }
                qrLink.value = link;
                showQrDialog.value = true;
                setTimeout(() => {
                    const container = document.getElementById('qrcode');
                    container.innerHTML = '';
                    new QRCode(container, {
                        text: link,
                        width: 200,
                        height: 200
                    });
                }, 100);
            } catch (error) {
                ElMessage.error('Failed to generate share link');
            }
        };

        const copyLink = async () => {
            await navigator.clipboard.writeText(qrLink.value);
            ElMessage.success('Link copied');
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
                return; // No polling or privileged UI activity before login.
            }
            await Promise.all([
                fetchSystemStatus(),
                fetchCoreStatus(),
                fetchInbounds(),
                fetchRouting(),
                fetchMihomoWarnings()
            ]);
            setInterval(fetchSystemStatus, 3000);
            setInterval(fetchCoreStatus, 10000);
        });

        return {
            currentView,
            systemStatus,
            coreStatus,
            inbounds,
            showAddInbound,
            editingInboundId,
            inboundCredentials,
            newInbound,
            protocolOptions,
            colors,
            restartingCore,
            banIpInput,
            currentUser,
            userForm,
            mihomoWarnings,
            managedCertificates,
            showQrDialog,
            qrLink,
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
            banIp,
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
