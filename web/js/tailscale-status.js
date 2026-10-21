/* Optional read-only observation; never a management action or path assertion. */
function createTailscaleCard(environment) {
    const {document: page, fetch: request, now, later, cancel, listen} = environment;
    const card = page.getElementById('tailscale-card');
    const state = page.getElementById('tailscale-state');
    const stamp = page.getElementById('tailscale-time');
    const button = page.getElementById('tailscale-refresh');
    const labels = {control_connected:'控制平面在线', control_disconnected:'控制平面离线',
        stopped:'已停止', not_ready:'未知：尚未就绪', unavailable:'未知：读取不可用',
        permission_denied:'未知：当前权限无法读取', timeout:'未知：读取超时',
        output_limit:'未知：输出超出限制', invalid_data:'未知：无法识别状态数据',
        unsupported_version:'未知：尚未支持此版本', busy:'未知：正在采样',
        cleanup_failed:'未知：采样已停止，请人工检查', expired:'未知：观察已过期'};
    let pending = false, active = true, enabled = false, generation = 0, timer = null, controller = null;
    function clear() { if (timer !== null) cancel(timer); timer = null; }
    function unknown() { clear(); state.textContent = '未知：请手动刷新'; stamp.textContent = ''; }
    function suspend() {
        active = false; generation++; unknown(); card.hidden = true;
        if (controller) controller.abort();
    }
    function resume() {
        active = true; generation++; unknown(); card.hidden = !enabled;
    }
    async function refresh() {
        if (pending || !active || page.hidden) return;
        pending = true; button.disabled = true;
        const current = generation, started = now();
        controller = new AbortController();
        unknown();
        const timeout = later(() => controller?.abort(), 4000);
        try {
            const response = await request('/api/management/tailscale', {
                credentials:'same-origin', cache:'no-store', signal:controller.signal});
            if (current !== generation || !active || page.hidden) return;
            if (response.status === 401 || response.status === 403) { enabled = false; card.hidden = true; return; }
            if (!response.ok) throw new Error('unavailable');
            const data = await response.json();
            if (current !== generation || !active || page.hidden) return;
            if (data.enabled !== true) { enabled = false; card.hidden = true; return; }
            enabled = true; card.hidden = false;
            const remaining = Number.isInteger(data.valid_for_seconds) && data.valid_for_seconds <= 30
                ? data.valid_for_seconds * 1000 - (now() - started) : 0;
            const expected = data.reason === 'control_connected' ? 'online'
                : ['control_disconnected','stopped'].includes(data.reason) ? 'offline' : 'unknown';
            if (data.state !== expected || !Object.prototype.hasOwnProperty.call(labels, data.reason)) return;
            // Fixed unknown reasons remain useful without a fresh observation,
            // especially a cleanup fuse that manual refresh cannot clear.
            if (expected === 'unknown') state.textContent = labels[data.reason];
            if (data.stale !== false || remaining <= 0) return;
            state.textContent = labels[data.reason];
            if (Number.isSafeInteger(data.observed_at) && data.observed_at >= 0) {
                const date = new Date(data.observed_at * 1000);
                if (Number.isFinite(date.getTime())) stamp.textContent = '观察时间：' + date.toISOString();
            }
            timer = later(unknown, remaining);
        } catch (_) {
            // Keep only fixed text. Neither server errors nor raw status are rendered.
            if (current === generation && active) unknown();
        } finally {
            cancel(timeout);
            pending = false; controller = null; button.disabled = false;
        }
    }
    button.addEventListener('click', refresh);
    page.addEventListener('visibilitychange', () => {
        if (page.hidden) { generation++; unknown(); if (controller) controller.abort(); }
    });
    listen('pagehide', suspend);
    listen('popstate', () => { suspend(); resume(); });
    listen('pageshow', event => { if (event.persisted) { suspend(); resume(); } });
    return {refresh};
}
if (typeof module !== 'undefined') module.exports = {createTailscaleCard};
if (typeof document !== 'undefined') {
    createTailscaleCard({document, fetch:(...args) => fetch(...args), now:() => performance.now(),
        later:setTimeout, cancel:clearTimeout, listen:addEventListener}).refresh();
}
