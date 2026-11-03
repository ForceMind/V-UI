'use strict';
// Deliberately limited DOM mock: verifies shell state/events, not CSS/layout/browser behavior.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/js/shell.js', 'utf8');
const ids = ['dashboard', 'inbounds', 'routing', 'subscriptions', 'proxy', 'certificates', 'cores', 'diagnostics', 'firewall', 'security', 'account', 'legacy'];
function scenario(page, authenticated = true, realNavigation = false) {
    const documentEvents = {}, windowEvents = {};
    let small = true, observer, document;
    class Element {
        constructor(dataset = {}) { this.dataset = dataset; this.attrs = {}; this.listeners = {}; this.hidden = false; this.inert = false; this.textContent = ''; }
        setAttribute(key, value) { this.attrs[key] = value; }
        getAttribute(key) { return this.attrs[key]; }
        removeAttribute(key) { delete this.attrs[key]; }
        addEventListener(key, fn) { this.listeners[key] = fn; }
        focus() { document.activeElement = this; }
    }
    const toggle = new Element(), close = new Element(), shade = new Element(), crumb = new Element(), skip = new Element(), main = new Element();
    const links = ids.map(id => new Element({ navId: id }));
    const sidebar = new Element();
    sidebar.querySelectorAll = () => [links[0], close, ...links.slice(1)];
    const selectors = { '.vui-menu-toggle': toggle, '.vui-drawer-close': close, '.vui-drawer-shade': shade, '.vui-sidebar': sidebar, '.vui-crumb-current': crumb, '.vui-skip': skip };
    const chrome = new Element();
    chrome.querySelector = key => selectors[key];
    chrome.querySelectorAll = () => links;
    const classes = new Set();
    const body = { dataset: { vuiPage: page }, firstChild: null,
        classList: { toggle(key, value) { value ? classes.add(key) : classes.delete(key); } },
        insertBefore(element) { assert.equal(element, chrome); } };
    const panel = page === 'account' ? new Element() : null;
    if (panel) panel.hidden = !authenticated;
    document = { body, activeElement: main, createElement: () => chrome,
        addEventListener: (key, fn) => { documentEvents[key] = fn; },
        getElementById: key => key === 'account-panel' ? panel : key === 'vui-main' ? main : null };
    const window = { matchMedia: () => ({ matches: small }), addEventListener: (key, fn) => { windowEvents[key] = fn; },
        VUI_NAV: { route: () => ({ id: page }) } };
    const context = { document, window, location: { pathname: '/ui/', hash: '#' + page, search: '', origin: 'https://panel.example.test', assign() {} },
        MutationObserver: class { constructor(fn) { observer = fn; } observe() {} } };
    if (realNavigation) {
        context.URL = URL; context.URLSearchParams = URLSearchParams; context.Event = class { constructor(type) { this.type = type; } };
        context.addEventListener = window.addEventListener;
        context.dispatchEvent = event => { windowEvents[event.type]?.(event); };
        const setURL = value => { const u = new URL(value, context.location.origin); Object.assign(context.location, { pathname: u.pathname, search: u.search, hash: u.hash }); };
        context.history = { state: null, replaceState(state, unused, value) { this.state = state; setURL(value); }, pushState(state, unused, value) { this.replaceState(state, unused, value); }, back() {}, go() {} };
        vm.runInNewContext(fs.readFileSync('web/js/navigation.js', 'utf8'), context);
        window.VUI_NAV = context.VUI_NAV;
    }
    vm.runInNewContext(source, context);
    return { nav: window.VUI_NAV, document, documentEvents, windowEvents, body, classes, chrome, toggle, close, shade, sidebar, crumb, skip, main, links, panel, observer, setSmall(value) { small = value; } };
}
for (const id of ids) {
    const state = scenario(id);
    assert.equal(state.links.filter(link => link.attrs['aria-current'] === 'page').length, 1);
    assert.equal(state.links.find(link => link.attrs['aria-current'] === 'page').dataset.navId, id);
    assert.equal(state.classes.has('vui-shell-active'), true);
    assert.equal(state.sidebar.inert, true);
    state.toggle.listeners.click();
    assert.equal(state.toggle.attrs['aria-expanded'], 'true');
    assert.equal(state.shade.hidden, false);
    assert.equal(state.sidebar.inert, false);
    assert.equal(state.document.activeElement, state.close);
    state.documentEvents.keydown({ key: 'Escape', preventDefault() {} });
    assert.equal(state.toggle.attrs['aria-expanded'], 'false');
    assert.equal(state.shade.hidden, true);
    assert.equal(state.document.activeElement, state.main);
    state.toggle.listeners.click(); state.shade.listeners.click();
    assert.equal(state.toggle.attrs['aria-expanded'], 'false');
    state.setSmall(false); state.windowEvents.resize();
    assert.equal(state.sidebar.inert, false);
    let prevented = false;
    state.skip.listeners.click({ preventDefault() { prevented = true; } });
    assert.equal(prevented, true); assert.equal(state.document.activeElement, state.main);
}
const login = scenario('account', false);
assert.equal(login.chrome.hidden, true);
assert.equal(login.classes.has('vui-shell-active'), false);
login.panel.hidden = false; login.observer();
assert.equal(login.chrome.hidden, false);
assert.equal(login.classes.has('vui-shell-active'), true);
login.toggle.listeners.click(); login.panel.hidden = true; login.observer();
assert.equal(login.chrome.hidden, true);
assert.equal(login.toggle.attrs['aria-expanded'], 'false');
console.log('UI shell state/event mock passed (12 routes, drawer, skip link, authenticated account visibility).');

const integrated = scenario('dashboard', true, true);
assert.equal(integrated.nav.navigate('inbounds'), true);
assert.equal(integrated.links.find(link => link.attrs['aria-current'] === 'page').dataset.navId, 'inbounds');
assert.equal(integrated.document.title, 'V-UI · ' + integrated.crumb.textContent);
assert.notEqual(integrated.crumb.textContent, '总览');
assert.equal(integrated.nav.navigate('cores'), true);
assert.equal(integrated.links.find(link => link.attrs['aria-current'] === 'page').dataset.navId, 'cores');
console.log('Real navigation pushState + shell subscription integration passed without synthetic hashchange.');

const focusFlow = scenario('dashboard', true, true);
focusFlow.toggle.listeners.click();
assert.equal(focusFlow.nav.navigate('inbounds'), true);
focusFlow.links[1].listeners.click();
assert.equal(focusFlow.document.activeElement, focusFlow.main);
const rejected = scenario('dashboard', true, true);
rejected.nav.guard(() => false);
rejected.toggle.listeners.click();
assert.equal(rejected.nav.navigate('inbounds'), false);
rejected.links[1].listeners.click();
assert.equal(rejected.document.activeElement, rejected.toggle);
console.log('Mobile navigation/guard refusal returns focus out of hidden drawer.');
