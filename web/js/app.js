const { createApp, ref, onMounted, reactive } = Vue;
const { ElMessage, ElMessageBox } = ElementPlus;
const { Odometer, Connection, Setting, Lock, Refresh, ArrowDown, Document, UploadFilled } = ElementPlusIconsVue;

const app = createApp({
    setup() {
        const currentView = ref('dashboard');
        const systemStatus = ref({});
        const inbounds = ref([]);
        const showAddInbound = ref(false);
        const restarting = ref(false);
        const banIpInput = ref('');
        const showQrDialog = ref(false);
        const qrLink = ref('');
        const siteFiles = ref([]);
        
        // User State
        const currentUser = ref({ username: 'admin' });
        const userForm = reactive({
            username: '',
            password: ''
        });

        const newInbound = reactive({
            remark: '',
            protocol: 'vless',
            port: 443,
            settings: {},
            stream_settings: {}
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
            if (key === 'inbounds') {
                fetchInbounds();
            } else if (key === 'settings') {
                // Pre-fill username
                userForm.username = currentUser.value.username;
            } else if (key === 'site') {
                fetchSiteFiles();
            }
        };

        const fetchSystemStatus = async () => {
            try {
                const res = await axios.get('/api/system/status');
                systemStatus.value = res.data;
            } catch (error) {
                console.error("Failed to fetch status", error);
            }
        };

        const fetchInbounds = async () => {
            try {
                const res = await axios.get('/api/xray/inbounds');
                inbounds.value = res.data;
            } catch (error) {
                ElMessage.error('Failed to load inbounds');
            }
        };

        const addInbound = async () => {
            try {
                // Backend handles UUID generation if not provided
                const payload = {
                    remark: newInbound.remark,
                    protocol: newInbound.protocol,
                    port: newInbound.port,
                    settings: JSON.stringify(newInbound.settings),
                    stream_settings: JSON.stringify(newInbound.stream_settings),
                    enable: true
                };
                await axios.post('/api/xray/inbounds', payload);
                ElMessage.success('Inbound added successfully');
                showAddInbound.value = false;
                fetchInbounds();
                // Reset form
                newInbound.remark = '';
                newInbound.port = 443;
            } catch (error) {
                console.error(error);
                ElMessage.error('Failed to add inbound: ' + (error.response?.data?.detail || error.message));
            }
        };

        const deleteInbound = async (row) => {
            try {
                await ElMessageBox.confirm('Are you sure to delete this inbound?', 'Warning', {
                    confirmButtonText: 'OK',
                    cancelButtonText: 'Cancel',
                    type: 'warning',
                });
                
                await axios.delete(`/api/xray/inbounds/${row.id}`);
                ElMessage.success('Deleted successfully');
                fetchInbounds();
            } catch (error) {
                if (error !== 'cancel') {
                    ElMessage.error('Failed to delete inbound');
                }
            }
        };

        const updateProfile = async () => {
            try {
                const payload = {
                    username: userForm.username,
                    password: userForm.password || undefined // Only send if not empty
                };
                await axios.post('/api/auth/update_profile', payload);
                ElMessage.success('Profile updated successfully. Please login again.');
                currentUser.value.username = userForm.username;
                userForm.password = ''; // Clear password field
            } catch (error) {
                ElMessage.error('Failed to update profile');
            }
        };

        const restartXray = async () => {
            restarting.value = true;
            try {
                await axios.post('/api/xray/restart');
                ElMessage.success('Restart command sent');
            } catch (error) {
                ElMessage.error('Failed to restart');
            } finally {
                setTimeout(() => { restarting.value = false; }, 2000);
            }
        };

        const banIp = async () => {
            if(!banIpInput.value) return;
            try {
                await axios.post(`/api/security/ban_ip?ip=${banIpInput.value}`);
                ElMessage.success(`IP ${banIpInput.value} banned`);
                banIpInput.value = '';
            } catch (error) {
                ElMessage.error('Failed to ban IP');
            }
        }

        const showQrCode = (row) => {
            // Construct a simple link for demonstration. 
            // In a real app, you'd parse the settings to build a vmess:// or vless:// link
            // For now, we'll just show a placeholder or a simple JSON representation
            let link = '';
            if (row.protocol === 'vless' || row.protocol === 'vmess') {
                // Simplified link generation logic
                // uuid@ip:port?security=none&type=tcp&headerType=none#remark
                const uuid = JSON.parse(row.settings).clients?.[0]?.id || 'uuid-not-found';
                const ip = location.hostname;
                link = `${row.protocol}://${uuid}@${ip}:${row.port}?security=none&type=tcp#${encodeURIComponent(row.remark)}`;
            } else {
                link = `Protocol: ${row.protocol}, Port: ${row.port}`;
            }
            
            qrLink.value = link;
            showQrDialog.value = true;
            
            // Wait for DOM update then generate QR
            setTimeout(() => {
                const container = document.getElementById('qrcode');
                container.innerHTML = '';
                new QRCode(container, {
                    text: link,
                    width: 200,
                    height: 200
                });
            }, 100);
        };

        const copyLink = () => {
            navigator.clipboard.writeText(qrLink.value).then(() => {
                ElMessage.success('Link copied to clipboard');
            });
        };

        const fetchSiteFiles = async () => {
            try {
                const res = await axios.get('/api/files/list_files');
                siteFiles.value = res.data;
            } catch (error) {
                ElMessage.error('Failed to list files');
            }
        };

        const handleUploadSuccess = (response, file, fileList) => {
            ElMessage.success('Site deployed successfully');
            fetchSiteFiles();
        };

        const handleUploadError = (err, file, fileList) => {
            ElMessage.error('Upload failed');
        };

        // Poll system status
        onMounted(() => {
            fetchSystemStatus();
            setInterval(fetchSystemStatus, 3000);
        });

        return {
            currentView,
            systemStatus,
            inbounds,
            showAddInbound,
            newInbound,
            colors,
            restarting,
            banIpInput,
            currentUser,
            userForm,
            showQrDialog,
            qrLink,
            handleSelect,
            addInbound,
            deleteInbound,
            updateProfile,
            restartXray,
            banIp,
            showQrCode,
            copyLink,
            siteFiles,
            fetchSiteFiles,
            handleUploadSuccess,
            handleUploadError
        };
    }
});

// Register Icons
app.component('Odometer', Odometer);
app.component('Connection', Connection);
app.component('Setting', Setting);
app.component('Lock', Lock);
app.component('Refresh', Refresh);
app.component('ArrowDown', ArrowDown);
app.component('Document', Document);
app.component('UploadFilled', UploadFilled);

app.use(ElementPlus);
app.mount('#app');
