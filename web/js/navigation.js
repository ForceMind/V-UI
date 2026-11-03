'use strict';
// Only enumerated UI destinations may cross the login boundary. No drafts or
// credentials are written to URLs, storage, or browser history.
(() => {
    const views = ['dashboard', 'inbounds', 'cores', 'diagnostics', 'firewall', 'security', 'legacy'];
    const targets = Object.freeze(Object.fromEntries([
        ...views.map(view => [view, '/ui/#' + view]),
        ['routing', '/workspace#routing'], ['subscriptions', '/workspace#subscriptions'],
        ['proxy', '/proxy'], ['certificates', '/certificates'], ['account', '/account']
    ]));
    const appView = hash => {
        const value = String(hash || '').replace(/^#/, '');
        return views.includes(value) ? value : value === 'site' ? 'legacy' : 'dashboard';
    };
    const route = (pathname = location.pathname, hash = location.hash) => {
        let id = 'dashboard';
        if (pathname === '/ui' || pathname === '/ui/' || pathname === '/ui/index.html') {
            const old = String(hash || '').replace(/^#/, '');
            id = old === 'settings' ? 'account' : ['routing', 'subscriptions'].includes(old) ? old : appView(hash);
        } else if (pathname === '/workspace') id = hash === '#subscriptions' ? 'subscriptions' : 'routing';
        else if (pathname === '/proxy') id = 'proxy';
        else if (pathname === '/certificates') id = 'certificates';
        else if (pathname === '/account') id = 'account';
        return { id, url: targets[id], view: views.includes(id) ? id : null };
    };
    const loginURL = (pathname = location.pathname, hash = location.hash) => '/login?return=' + route(pathname, hash).id;
    const resumeTarget = (search = location.search) => {
        const id = new URLSearchParams(search).get('return');
        return Object.hasOwn(targets, id) ? targets[id] : targets.dashboard;
    };
    let modal = null, sequence = 0, rollback = false, pageLeaving = false, closeRequest = 0, closingPosition = null, forcedExit = false, approvedExit = false;
    const listeners = new Set(), guards = new Set();
    const positionOf = state => state?.vuiNavigation === 1 && Number.isSafeInteger(state.position) && state.position >= 0 ? state.position : null;
    const marker = () => ({ vuiNavigation: 1, position: sequence });
    let activeRouteID = route().id;
    const notify = reason => { const target = route(); activeRouteID = target.id; listeners.forEach(listener => listener(target, reason)); };
    const pageAllowed = () => [...guards].every(guard => guard() !== false);
    const allowed = () => (!modal || modal.canClose() !== false) && pageAllowed();
    const currentURL = () => location.pathname + location.search + location.hash;
    // Replacing the current entry never enables a Back jump from a cold link.
    if (typeof history !== 'undefined') {
        sequence = positionOf(history.state) ?? 0;
        history.replaceState(marker(), '', currentURL());
    }
    const modalHistory = {
        open(id, close, canClose = () => true) {
            if (!/^[a-z-]+$/.test(id) || pageLeaving) return;
            closeRequest++;
            if (modal?.id === id) return;
            if (modal) { const previous = modal; modal = null; previous.close(); }
            modal = { id, close, canClose };
            const state = { ...marker(), modal: id };
            // Replace the existing modal layer when changing modal workflows.
            if (history.state?.modal) history.replaceState(state, '', currentURL());
            else { sequence++; history.pushState({ ...marker(), modal: id }, '', currentURL()); }
        },
        close(id) {
            if (modal?.id !== id) return;
            modal = null;
            const request = ++closeRequest;
            // A synchronous workflow replacement reuses this modal layer.
            Promise.resolve().then(() => {
                if (request !== closeRequest || modal || pageLeaving || history.state?.modal !== id) return;
                closingPosition = sequence - 1;
                history.back();
            });
        },
        forget() { modal = null; }
    };
    const navigate = id => {
        if (!Object.hasOwn(targets, id)) return false;
        const url = targets[id];
        if (currentURL() === url) return true;
        if (!allowed()) return false;
        const parsed = new URL(url, location.origin);
        // Invalidate secrets and pending reads before either kind of navigation.
        // Clear ownership first so the close callback cannot traverse old history.
        if (modal) { const old = modal; modal = null; old.close(); }
        if (parsed.pathname !== location.pathname) { approvedExit = true; location.assign(url); return true; }
        if (history.state?.modal) history.replaceState(marker(), '', url);
        else { sequence++; history.pushState(marker(), '', url); }
        notify();
        dispatchEvent(new Event('vui:navigation'));
        return true;
    };
    addEventListener('popstate', event => {
        const next = event.state, nextPosition = positionOf(next);
        if (closingPosition !== null && nextPosition === closingPosition) {
            closingPosition = null; sequence = nextPosition; notify('modal-close'); return;
        }
        if (rollback) { rollback = false; return; }
        const routeChanged = route().id !== activeRouteID;
        const canPop = modal ? modal.canClose() !== false && (!routeChanged || pageAllowed()) : pageAllowed();
        if (!canPop) {
            const delta = nextPosition !== null ? sequence - nextPosition : 1;
            rollback = true; history.go(delta || 1); return;
        }
        if (modal) { const old = modal; modal = null; old.close(); }
        sequence = nextPosition ?? sequence;
        // A Forward traversal cannot resurrect secrets or an unverified editor.
        if (next?.modal) history.replaceState(marker(), '', currentURL());
        notify('popstate');
    });
    addEventListener('hashchange', () => {
        if (rollback) return;
        // Address-bar/hash navigation creates its entry before this event. It
        // must use the same guards and cursor accounting as sidebar clicks.
        if (route().id !== activeRouteID) {
            history.replaceState({vuiNavigation: 1, position: sequence + 1}, '', currentURL());
            if (!allowed()) { rollback = true; history.back(); return; }
            sequence++;
            if (modal) { const old = modal; modal = null; old.close(); }
        }
        notify('hashchange');
    });
    addEventListener('pagehide', () => { pageLeaving = true; modal = null; });
    addEventListener('beforeunload', event => {
        if (forcedExit || approvedExit) { approvedExit = false; return; }
        if (!allowed()) { event.preventDefault(); event.returnValue = ''; }
    });
    if (document.addEventListener) document.addEventListener('click', event => {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const anchor = event.target.closest?.('a[href]');
        if (!anchor || anchor.target || anchor.hasAttribute('download')) return;
        let url;
        try { url = new URL(anchor.href, location.origin); } catch (_) { return; }
        if (url.origin !== location.origin || url.search) return;
        const destination = route(url.pathname, url.hash);
        if (!Object.values(targets).includes(url.pathname + url.hash)) return;
        event.preventDefault(); navigate(destination.id);
    }, true);
    globalThis.VUI_NAV = Object.freeze({ targets, route, appView, loginURL, resumeTarget, navigate,
        replaceLogin(pathname, hash) { forcedExit = true; pageLeaving = true; closeRequest++; modal = null; location.replace(loginURL(pathname, hash)); },
        resume() { forcedExit = true; pageLeaving = true; closeRequest++; modal = null; location.replace(resumeTarget()); },
        subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
        guard(guard) { guards.add(guard); return () => guards.delete(guard); }, modal: modalHistory });
})();
