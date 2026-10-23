"""Delay an actual certificate-list response, never synthesize API success."""


def hold_next_certificate_read(page):
    page.evaluate('''() => {
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
                setTimeout(() => { audit.delivered = true; }, 0);
                return body;
            };
            audit.ready = true;
            return new Promise(resolve => {
                audit.release = () => { audit.released = true; resolve(response); };
            });
        };
        audit.restore = () => {
            window.fetch = original; audit.observer?.disconnect(); if (audit.release) audit.release();
        };
    }''')


def wait_certificate_read(page):
    page.wait_for_function('certificateReadAudit.ready', timeout=10000)
    return page.evaluate('''() => ({status:certificateReadAudit.status,
        body:certificateReadAudit.body, intercepted:certificateReadAudit.intercepted})''')


def release_certificate_read(page):
    page.evaluate('certificateReadAudit.release()')
    page.wait_for_function('certificateReadAudit.delivered', timeout=10000)


def restore_certificate_reads(page):
    page.evaluate('certificateReadAudit.restore()')
