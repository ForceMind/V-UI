const { createApp, ref, onMounted, reactive } = Vue;
const { ElMessage } = ElementPlus;
const { Odometer, Connection, Setting, Lock, Refresh, ArrowDown } = ElementPlusIconsVue;

const app = createApp({
    setup() {
        const currentView = ref('dashboard');
        const systemStatus = ref({});
        const inbounds = ref([]);
        const showAddInbound = ref(false);
        const restarting = ref(false);
        const banIpInput = ref('');
        
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
                // Basic mock payload
                const payload = {
                    id: Date.now(), // Mock ID
                    ...newInbound,
                    enable: true
                };
                await axios.post('/api/xray/inbounds', payload);
                ElMessage.success('Inbound added successfully');
                showAddInbound.value = false;
                fetchInbounds();
            } catch (error) {
                ElMessage.error('Failed to add inbound');
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
            handleSelect,
            addInbound,
            restartXray,
            banIp
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

app.use(ElementPlus);
app.mount('#app');
