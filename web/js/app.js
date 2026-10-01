const { createApp, ref, onMounted, reactive, computed } = Vue;
const { ElMessage, ElMessageBox } = ElementPlus;
const { Odometer, Connection, Setting, Lock, Refresh, ArrowDown, Document, UploadFilled, Link } = ElementPlusIconsVue;

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

        const colors = [
            { color: '#f56c6c', percentage: 100 },
            { color: '#e6a23c', percentage: 80 },
            { color: '#5cb87a', percentage: 60 },
            { color: '#1989fa', percentage: 40 },
            { color: '#6f7ad3', percentage: 20 }
        ];

        const handleSelect = (key) => {
            currentView.value = key;
            if (key === 'inbounds') fetchInbounds();
            if (key === 'settings') userForm.username = currentUser.value.username;
            if (key === 'site') fetchSiteFiles();
            if (key === 'subscriptions') fetchCoreStatus();
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
                fetchInbounds()
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
            handleSelect,
            fetchInbounds,
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
