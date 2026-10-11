'use strict';
(() => {
    addEventListener('pageshow', (event) => { if (event.persisted) location.reload(); });
    const el = (id) => document.getElementById(id);
    const message = (text) => { el('message').textContent = text; };
    let accountGeneration = 0;
    const api = async (path, data) => {
        const controller = data === undefined && typeof AbortController === 'function' ? new AbortController() : null;
        const deadline = controller ? setTimeout(() => controller.abort(), 5000) : null;
        try {
            const res = await fetch('/api/auth/' + path, {
                method: data === undefined ? 'GET' : 'POST',
                credentials: 'same-origin', cache: 'no-store', signal: controller?.signal,
                headers: data === undefined ? {} : {
                    'Content-Type': 'application/json', 'X-VUI-Request': '1'
                },
                body: data === undefined ? undefined : JSON.stringify(data)
            });
            const payload = await res.json();
            if (!res.ok) {
                const error = new Error(typeof payload.detail === 'string' ? payload.detail : '请求失败');
                error.status = res.status;
                throw error;
            }
            return payload;
        } finally { if (deadline !== null) clearTimeout(deadline); }
    };
    const showLogin = () => {
        accountGeneration++;
        el('login-form').hidden = false;
        el('account-panel').hidden = true;
        el('login-password').value = '';
        el('current-password').value = '';
        el('new-password').value = '';
    };
    let sessionChannel = null;
    try {
        if (typeof BroadcastChannel === 'function') sessionChannel = new BroadcastChannel('vui-admin-session');
    } catch (_) { /* No credential or persistent-storage fallback. */ }
    const notifySessionEnded = () => {
        try { if (sessionChannel) sessionChannel.postMessage('session-ended'); } catch (_) {}
    };
    const showAccount = (user, generation) => {
        if (generation !== accountGeneration) return;
        el('current-name').textContent = user.username;
        if (el('account-panel').hidden) el('profile-name').value = user.username;
        el('login-form').hidden = true;
        el('account-panel').hidden = false;
        message('');
    };
    let sessionCheck = null, checkAgain = false;
    const checkCurrentSession = () => {
        accountGeneration++; // Ignore an older initial /me response immediately.
        if (sessionCheck) { checkAgain = true; return sessionCheck; }
        sessionCheck = (async () => {
            do {
                checkAgain = false;
                const generation = accountGeneration;
                try { showAccount(await api('me'), generation); }
                catch (error) {
                    if (generation === accountGeneration && error.status === 401) {
                        showLogin(); message('会话已失效，请重新登录。');
                    } else if (generation === accountGeneration) {
                        if (el('account-panel').hidden && el('login-form').hidden) showLogin();
                        message('暂时无法验证会话，请重试登录或刷新页面。');
                    }
                }
            } while (checkAgain);
        })().finally(() => { sessionCheck = null; });
        return sessionCheck;
    };
    if (sessionChannel) sessionChannel.onmessage = (event) => {
        if (event.data === 'session-ended') return checkCurrentSession();
    };
    const busy = (value) => document.querySelectorAll('button').forEach((b) => { b.disabled = value; });
    el('login-form').addEventListener('submit', async (event) => {
        event.preventDefault(); busy(true); message('正在登录…');
        try {
            await api('login', { username: el('login-name').value, password: el('login-password').value });
            el('login-password').value = '';
            accountGeneration++;
            // Fixed destination, never trust a next/redirect query parameter.
            location.replace('/ui/');
        } catch (error) {
            message(error.status === 401 ? '用户名或密码不正确。' : error.message);
            el('login-password').value = '';
        } finally { busy(false); }
    });
    el('profile-form').addEventListener('submit', async (event) => {
        event.preventDefault(); busy(true);
        try {
            const payload = { username: el('profile-name').value,
                current_password: el('current-password').value };
            if (el('new-password').value) payload.password = el('new-password').value;
            await api('update_profile', payload);
            notifySessionEnded();
            showLogin(); message('账户已更新，所有旧会话已退出，请重新登录。');
        } catch (error) {
            if (error.status === 401 && error.message !== 'Current password is incorrect') { notifySessionEnded(); showLogin(); }
            message(error.message);
        }
        finally { el('current-password').value = ''; el('new-password').value = ''; busy(false); }
    });
    el('logout').addEventListener('click', async () => {
        busy(true);
        try { await api('logout', {}); notifySessionEnded(); showLogin(); message('已退出登录。'); }
        catch (error) { if (error.status === 401) { notifySessionEnded(); showLogin(); } message(error.message); }
        finally { busy(false); }
    });
    const initialGeneration = accountGeneration;
    api('me').then((user) => showAccount(user, initialGeneration)).catch((error) => {
        if (initialGeneration !== accountGeneration) return;
        showLogin(); message(error.status === 401 ? '' : error.message);
    });
})();
