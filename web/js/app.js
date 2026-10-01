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
        const currentView = ref('dashboard');
        const systemStatus = ref({});
        const coreStatus = ref({});
        const inbounds = ref([]);
        const showAddInbound = ref(false);
        const restartingCore = ref('');
        const banIpInput = ref('');
        const showQrDialog = ref(false);
        const qrLink = ref('');
        const siteFiles = ref([]);
        const currentUser = ref({ username: 'admin' });

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
            stream_settings: {}
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
            currentView.value = key;
            if (key === 'inbounds') fetchInbounds();
            if (key === 'routing') fetchRouting();
            if (key === 'settings') userForm.username = currentUser.value.username;
            if (key === 'site') fetchSiteFiles();
            if (key === 'subscriptions') {
                fetchCoreStatus();
                fetchRoutingPreview();
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

        const openAddInbound = () => {
            showAddInbound.value = true;
        };

        const onCoreChanged = () => {
            newInbound.protocol = 'vless';
            newInbound.settings = {};
            newInbound.stream_settings = {};
        };

        const addInbound = async () => {
            try {
                const payload = {
                    core: newInbound.core,
                    remark: newInbound.remark,
                    protocol: newInbound.protocol,
                    port: newInbound.port,
                    settings: newInbound.settings,
                    stream_settings: newInbound.stream_settings,
                    enable: true
                };
                const res = await axios.post('/api/inbounds', payload);
                if (res.data.core && res.data.core.valid === false) {
                    ElMessage.warning(
                        'Node saved, but the core is not ready: ' +
                        (res.data.core.validation_output || 'validation failed')
                    );
                } else {
                    ElMessage.success('Inbound added successfully');
                }
                showAddInbound.value = false;
                await Promise.all([fetchInbounds(), fetchCoreStatus()]);
                newInbound.remark = '';
                newInbound.port = 443;
                newInbound.settings = {};
                newInbound.stream_settings = {};
            } catch (error) {
                ElMessage.error(
                    'Failed to add inbound: ' +
                    (error.response?.data?.detail || error.message)
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

        const updateProfile = async () => {
            try {
                const payload = {
                    username: userForm.username,
                    password: userForm.password || undefined
                };
                await axios.post('/api/auth/update_profile', payload);
                ElMessage.success('Profile updated successfully. Please login again.');
                currentUser.value.username = userForm.username;
                userForm.password = '';
            } catch (error) {
                ElMessage.error('Failed to update profile');
            }
        };

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
            await Promise.all([
                fetchSystemStatus(),
                fetchCoreStatus(),
                fetchInbounds(),
                fetchRouting()
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
            newInbound,
            protocolOptions,
            colors,
            restartingCore,
            banIpInput,
            currentUser,
            userForm,
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
            onCoreChanged,
            addInbound,
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
