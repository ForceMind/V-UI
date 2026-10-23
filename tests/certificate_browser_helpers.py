"""Delay an actual certificate-list response, never synthesize API success."""


def hold_next_certificate_read(page):
    page.evaluate('''() => {
        const root = document.documentElement;
        root.removeAttribute('data-test-certificate-read-ready');
        root.removeAttribute('data-test-certificate-read-delivered');
        const original = window.fetch;
        const audit = window.certificateReadAudit = {
            ready:false, released:false, delivered:false, intercepted:0
        };
        window.fetch = async (...args) => {
            const target = new URL(args[0], location.href);
            const match = target.origin === location.origin && target.pathname === '/api/certificates'
                && (!args[1]?.method || args[1].method === 'GET') && audit.intercepted === 0;
            if (match) audit.intercepted++;
            const response = await original(...args);
            if (!match) return response;
            audit.status = response.status;
            audit.body = await response.clone().json();
            const json = response.json.bind(response);
            response.json = async () => {
                const body = await json();
                // The consuming api()/refresh() continuations settle before
                // this task, so assertions cannot run before the stale read.
                setTimeout(() => {
                    audit.delivered = true;
                    root.setAttribute('data-test-certificate-read-delivered', '1');
                }, 0);
                return body;
            };
            audit.ready = true;
            root.setAttribute('data-test-certificate-read-ready', '1');
            return new Promise(resolve => {
                audit.release = () => { audit.released = true; resolve(response); };
            });
        };
        audit.restore = () => {
            window.fetch = original; audit.observer?.disconnect(); if (audit.release) audit.release();
            root.removeAttribute('data-test-certificate-read-ready');
            root.removeAttribute('data-test-certificate-read-delivered');
        };
    }''')


def wait_certificate_read(page):
    from playwright.sync_api import expect
    # wait_for_function uses page-side eval even for a function expression,
    # which this page's existing script-src 'self' correctly refuses.
    expect(page.locator('html')).to_have_attribute('data-test-certificate-read-ready', '1', timeout=10000)
    return page.evaluate('''() => ({status:certificateReadAudit.status,
        body:certificateReadAudit.body, intercepted:certificateReadAudit.intercepted})''')


def release_certificate_read(page):
    from playwright.sync_api import expect
    page.evaluate('certificateReadAudit.release()')
    expect(page.locator('html')).to_have_attribute('data-test-certificate-read-delivered', '1', timeout=10000)


def restore_certificate_reads(page):
    page.evaluate('certificateReadAudit.restore()')
