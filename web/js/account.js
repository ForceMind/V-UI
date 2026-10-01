'use strict';
(() => {
    addEventListener('pageshow', (event) => { if (event.persisted) location.reload(); });
    const el = (id) => document.getElementById(id);
    const message = (text) => { el('message').textContent = text; };
    const api = async (path, data) => {
        const res = await fetch('/api/auth/' + path, {
            method: data === undefined ? 'GET' : 'POST',
            credentials: 'same-origin', cache: 'no-store',
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
    };
    const showLogin = () => {
        el('login-form').hidden = false;
        el('account-panel').hidden = true;
        el('login-password').value = '';
        el('current-password').value = '';
        el('new-password').value = '';
    };
    const busy = (value) => document.querySelectorAll('button').forEach((b) => { b.disabled = value; });
    el('login-form').addEventListener('submit', async (event) => {
        event.preventDefault(); busy(true); message('正在登录…');
        try {
            await api('login', { username: el('login-name').value, password: el('login-password').value });
            el('login-password').value = '';
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
            showLogin(); message('账户已更新，所有旧会话已退出，请重新登录。');
        } catch (error) {
            if (error.status === 401 && error.message !== 'Current password is incorrect') showLogin();
            message(error.message);
        }
        finally { el('current-password').value = ''; el('new-password').value = ''; busy(false); }
    });
    el('logout').addEventListener('click', async () => {
        busy(true);
        try { await api('logout', {}); showLogin(); message('已退出登录。'); }
        catch (error) { if (error.status === 401) showLogin(); message(error.message); }
        finally { busy(false); }
    });
    api('me').then((user) => {
        el('current-name').textContent = user.username;
        el('profile-name').value = user.username;
        el('account-panel').hidden = false;
        message('');
    }).catch((error) => {
        showLogin(); message(error.status === 401 ? '' : error.message);
    });
})();
