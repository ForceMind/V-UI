'use strict';
(() => {
    // Shared navigation owns no business data, form fields, credentials, or API calls.
    const body = document.body;
    if (!body || !body.dataset.vuiPage) return;
    const groups = [
        ['业务配置', [
            ['dashboard', '总览', '/ui/#dashboard'],
            ['inbounds', '节点管理', '/ui/#inbounds'],
            ['routing', '分流配置', '/workspace#routing'],
            ['subscriptions', '订阅授权', '/workspace#subscriptions'],
            ['proxy', '连接其他服务器', '/proxy'],
            ['certificates', '证书管理', '/certificates']
        ]],
        ['运行维护', [
            ['maintenance', '远程维护与升级', '/maintenance'],
            ['cores', '核心与服务', '/ui/#cores'],
            ['diagnostics', '日志与诊断', '/ui/#diagnostics'],
            ['firewall', '防火墙', '/ui/#firewall']
        ]],
        ['管理', [
            ['security', '安全中心', '/ui/#security'],
            ['account', '账户设置', '/account']
        ]],
        ['高级 / 历史', [['legacy', '站点托管', '/ui/#legacy']]]
    ];
    const entries = groups.flatMap(([, links]) => links);
    const chrome = document.createElement('div');
    chrome.className = 'vui-shell';
    chrome.innerHTML = `
        <a class="vui-skip" href="#vui-main">跳到主要内容</a>
        <aside id="vui-sidebar" class="vui-sidebar" aria-label="主导航">
            <a class="vui-shell-brand" href="/ui/#dashboard"><img class="vui-brand-image" src="/ui/brand/vui-icon.svg" width="32" height="32" alt="V-UI"><span aria-hidden="true">V-UI<small>管理控制台</small></span></a>
            <button class="vui-drawer-close" type="button" aria-label="关闭导航">关闭导航 ×</button>
            <nav>${groups.map(([label, links]) => `<div class="vui-nav-group">${label}</div>${links.map(([id, title, href]) => `<a class="vui-nav-link" data-nav-id="${id}" href="${href}">${title}</a>`).join('')}`).join('')}</nav>
            <div class="vui-shell-foot">配置保存与实际线路验收分开<br>敏感内容仅在授权后展示</div>
        </aside>
        <div class="vui-drawer-shade" hidden></div>
        <header class="vui-topbar">
            <button class="vui-menu-toggle" type="button" aria-label="打开导航" aria-controls="vui-sidebar" aria-expanded="false">☰</button>
            <nav class="vui-breadcrumb" aria-label="面包屑"><a href="/ui/#dashboard">总览</a><span aria-hidden="true"> / </span><span class="vui-crumb-current" aria-current="page"></span></nav>
            <a class="vui-account-link" href="/account">账户设置</a>
        </header>`;
    body.insertBefore(chrome, body.firstChild);
    chrome.querySelector('.vui-skip').addEventListener('click', (event) => {
        event.preventDefault();
        const main = document.getElementById('vui-main');
        if (main) { main.setAttribute('tabindex', '-1'); main.focus(); }
    });
    const toggle = chrome.querySelector('.vui-menu-toggle');
    const close = chrome.querySelector('.vui-drawer-close');
    const shade = chrome.querySelector('.vui-drawer-shade');
    const sidebar = chrome.querySelector('.vui-sidebar');
    const narrow = () => window.matchMedia('(max-width: 800px)').matches;
    let previousFocus = null;
    const setDrawer = (open, restoreFocus = true) => {
        const expanded = open && narrow();
        if (expanded) previousFocus = document.activeElement;
        body.classList.toggle('vui-drawer-open', expanded);
        toggle.setAttribute('aria-expanded', String(expanded));
        shade.hidden = !expanded;
        sidebar.inert = narrow() && !expanded;
        if (expanded) close.focus();
        else if (restoreFocus && previousFocus && typeof previousFocus.focus === 'function') {
            previousFocus.focus(); previousFocus = null;
        }
    };
    toggle.addEventListener('click', () => setDrawer(toggle.getAttribute('aria-expanded') !== 'true'));
    close.addEventListener('click', () => setDrawer(false));
    shade.addEventListener('click', () => setDrawer(false));
    document.addEventListener('keydown', (event) => {
        if (toggle.getAttribute('aria-expanded') !== 'true') return;
        if (event.key === 'Escape') { event.preventDefault(); setDrawer(false); }
        if (event.key === 'Tab') {
            const items = [...sidebar.querySelectorAll('a[href], button:not([disabled])')];
            const first = items[0], last = items[items.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    });
    chrome.querySelectorAll('.vui-nav-link, .vui-shell-brand').forEach((link) => {
        link.addEventListener('click', () => {
            if (toggle.getAttribute('aria-expanded') === 'true') { setDrawer(false, false); toggle.focus(); }
        });
    });
    const update = () => {
        const wasOpen = toggle.getAttribute('aria-expanded') === 'true';
        const route = window.VUI_NAV ? window.VUI_NAV.route(location.pathname, location.hash) : null;
        const id = route ? route.id : body.dataset.vuiPage;
        const entry = entries.find(([key]) => key === id) || entries[0];
        chrome.querySelectorAll('[data-nav-id]').forEach((link) => {
            if (link.dataset.navId === entry[0]) link.setAttribute('aria-current', 'page');
            else link.removeAttribute('aria-current');
        });
        chrome.querySelector('.vui-crumb-current').textContent = entry[1];
        document.title = `V-UI · ${entry[1]}`;
        setDrawer(false, false);
        if (wasOpen) { const main = document.getElementById('vui-main'); if (main) { main.setAttribute('tabindex', '-1'); main.focus(); } }
    };
    if (window.VUI_NAV?.subscribe) window.VUI_NAV.subscribe(update);
    window.addEventListener('hashchange', update);
    window.addEventListener('resize', () => setDrawer(false, false));
    const panel = document.getElementById('account-panel');
    const accountVisibility = () => {
        const visible = !panel || !panel.hidden;
        chrome.hidden = !visible;
        body.classList.toggle('vui-shell-active', visible);
        if (!visible) setDrawer(false, false);
    };
    if (panel) new MutationObserver(accountVisibility).observe(panel, { attributes: true, attributeFilter: ['hidden'] });
    accountVisibility();
    update();
})();
