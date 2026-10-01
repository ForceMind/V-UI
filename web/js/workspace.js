'use strict';
(() => {
    const $ = id => document.getElementById(id);
    let savedRevision = '', savedForm = '', editNumber = 0, catalog = null, issued = null, busy = false;
    const notice = (text, error = false) => { $('notice').textContent = text; $('notice').className = error ? 'error' : ''; };
    const element = (tag, text, attrs = {}) => {
        const node = document.createElement(tag);
        if (text !== null) node.textContent = text;
        Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
        return node;
    };
    const request = async (url, options = {}) => {
        const response = await fetch(url, {credentials:'same-origin', cache:'no-store', ...options,
            headers:{'X-VUI-Request':'1', ...(options.body ? {'Content-Type':'application/json'} : {}), ...(options.headers || {})}});
        if (response.status === 401) { hideIssued(); location.replace('/login'); throw new Error('登录已过期'); }
        if (!response.ok) {
            let text = '请求未完成';
            try { const value = await response.json(); text = typeof value.detail === 'string' ? value.detail : '输入格式不正确，未保存'; } catch {}
            const error = new Error(text); error.status = response.status; throw error;
        }
        return response;
    };
    const json = async (url, options) => (await request(url, options)).json();
    const lines = value => value.split(/\r?\n/).map(v => v.trim()).filter(Boolean);
    function collect() {
        const presets = Object.fromEntries([...document.querySelectorAll('[data-preset]')].map(n => [n.dataset.preset, n.checked]));
        const intranet = [...document.querySelectorAll('.zone')].map(n => ({suffix:n.querySelector('[data-suffix]').value.trim(),
            nameservers:n.querySelector('[data-dns]').value.split(/[\s,]+/).filter(Boolean)}));
        return {mode:$('mode').value, direct_domains:lines($('direct').value), proxy_domains:lines($('proxy').value),
            presets, bypass_cgnat:$('cgnat').checked, intranet};
    }
    const fingerprint = value => JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item) ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
    const dirty = () => savedForm !== fingerprint(collect());
    function updateState() {
        $('save-state').textContent = dirty() ? '有未保存草稿 · 订阅仍使用旧设置' : '已与保存版本一致 · 客户端需刷新订阅';
        $('preset-count').textContent = `${Object.values(collect().presets).filter(Boolean).length} / 40`;
        $('save').disabled = busy || !savedRevision || !dirty();
    }
    function edited() {
        editNumber++; updateState();
        $('preview-label').textContent = '表单已修改，预览需重新生成';
    }
    function addZone(zone = {suffix:'', nameservers:[]}) {
        const div = element('div', null, {class:'zone'});
        const suffix = element('label', '域名后缀');
        const suffixInput = element('input', null, {'data-suffix':'', placeholder:'corp.example', maxlength:'253'});
        suffixInput.value = zone.suffix; suffix.append(suffixInput);
        const dns = element('label', 'DNS 地址（逗号分隔）');
        const dnsInput = element('input', null, {'data-dns':'', placeholder:'192.0.2.53 或 system'});
        dnsInput.value = zone.nameservers.join(', '); dns.append(dnsInput);
        const remove = element('button', '移除区域', {type:'button'});
        remove.addEventListener('click', () => { div.remove(); edited(); });
        div.append(suffix, dns, remove); $('zones').append(div);
    }
    function fill(settings) {
        $('mode').value = settings.mode; $('direct').value = settings.direct_domains.join('\n');
        $('proxy').value = settings.proxy_domains.join('\n'); $('cgnat').checked = settings.bypass_cgnat;
        document.querySelectorAll('[data-preset]').forEach(n => { n.checked = Boolean(settings.presets[n.dataset.preset]); });
        $('zones').replaceChildren(); settings.intranet.forEach(addZone);
    }
    function showPlan(plan, name) {
        $('preview-label').textContent = name;
        $('rule-count').textContent = plan.sections.reduce((n, s) => n + s.rules.length, 0) + ' 条规则';
        $('rule-preview').textContent = plan.sections.map(s => '# ' + s.comment + '\n' + s.rules.join('\n')).join('\n\n');
        const labels = {LOCAL_OVERRIDE:'本地保护优先', INTRANET_OVERRIDE:'内网规则优先', DIRECT_OVERRIDE:'始终直连规则优先'};
        $('warnings').textContent = plan.warnings.map(w => `${labels[w.code] || w.code}：${w.count} 项覆盖`).join('；');
    }
    async function reload() {
        if (savedRevision && dirty() && !confirm('放弃未保存草稿，重新载入已保存版本？')) return;
        const value = await json('/api/routing/mihomo/snapshot');
        savedRevision = value.revision; fill(value.settings); savedForm = fingerprint(collect()); editNumber++;
        const current = editNumber;
        const plan = await json('/api/routing/mihomo/preview');
        if (current === editNumber) showPlan(plan, '已保存规则预览');
        updateState(); notice('已读取保存版本。编辑表单不会立即改变订阅。');
    }
    function hideIssued() { issued = null; $('issued-urls').value = ''; $('issued').hidden = true; }
    function showIssued(value) {
        issued = value; $('issued-urls').value = Object.values(value.paths).map(p => location.origin + p).join('\n');
        $('issued').hidden = false; $('download').disabled = !value.paths['mihomo.yaml'];
    }
    async function refreshGrants() {
        const values = await json('/api/subscriptions'); $('grants').replaceChildren();
        values.forEach(value => {
            const row = element('tr', null); const name = element('td', value.label);
            name.append(element('small', value.server));
            const status = value.revoked ? '已撤销' : value.expired ? '已过期' : value.invalidated ? '密码变更后失效' : '有效';
            const actions = element('td', null);
            const rotate = element('button', '轮换', {type:'button', 'data-rotate':String(value.id)});
            rotate.addEventListener('click', () => operation(async () => {
                if (!confirm('轮换后旧地址失效，需更新客户端。继续？')) return;
                const days = Number($('days').value); if (!Number.isInteger(days) || days < 1 || days > 3650) throw new Error('有效天数须为 1–3650');
                hideIssued(); showIssued(await json(`/api/subscriptions/${value.id}/rotate`, {method:'POST', body:JSON.stringify({expires_days:days})}));
                await refreshGrants(); notice('已轮换，旧地址失效。新地址仅本次显示。');
            }));
            const revoke = element('button', '撤销', {type:'button', 'data-revoke':String(value.id)}); revoke.disabled = value.revoked;
            revoke.addEventListener('click', () => operation(async () => {
                if (!confirm('撤销该订阅？客户端将不能继续拉取配置。')) return;
                await json(`/api/subscriptions/${value.id}`, {method:'DELETE'});
                if (issued?.id === value.id) hideIssued(); await refreshGrants(); notice('订阅已撤销。');
            }));
            actions.append(rotate, revoke);
            row.append(name, element('td', `${value.inbound_ids.length} 节点 / ${value.formats.join(', ')}`), element('td', status),
                element('td', new Date(value.expires_at * 1000).toLocaleString()), actions);
            $('grants').append(row);
        });
        if (!values.length) { const row = element('tr', null); row.append(element('td', '尚未创建订阅', {colspan:'5'})); $('grants').append(row); }
    }
    async function operation(fn) {
        if (busy) return; busy = true;
        document.querySelectorAll('button').forEach(n => { n.dataset.disabled = String(n.disabled); n.disabled = true; });
        try { await fn(); }
        catch (error) { notice(error.status === 409 ? '保存版本冲突或配置尚未就绪：' + error.message : error.message, true); }
        finally {
            busy = false;
            document.querySelectorAll('button').forEach(n => { if (n.dataset.disabled !== undefined) { n.disabled = n.dataset.disabled === 'true'; delete n.dataset.disabled; } });
            if (savedRevision) updateState();
            $('download').disabled = !issued?.paths['mihomo.yaml'];
        }
    }
    $('routing-form').addEventListener('submit', event => event.preventDefault());
    $('routing-form').addEventListener('input', edited);
    $('routing-form').addEventListener('change', edited);
    $('add-zone').addEventListener('click', () => { addZone(); edited(); });
    $('reload').addEventListener('click', () => operation(reload));
    $('preview').addEventListener('click', () => operation(async () => {
        const number = editNumber;
        const plan = await json('/api/routing/mihomo/preview', {method:'POST', body:JSON.stringify(collect())});
        if (number === editNumber) { showPlan(plan, '草稿预览 · 尚未保存'); notice('仅预览，没有修改订阅。'); }
    }));
    $('save').addEventListener('click', () => operation(async () => {
        const number = editNumber;
        const response = await request('/api/routing/mihomo', {method:'PUT', headers:{'If-Match':'"' + savedRevision + '"'}, body:JSON.stringify(collect())});
        const settings = await response.json(); savedRevision = response.headers.get('ETag').replaceAll('"', '');
        if (number === editNumber) { fill(settings); savedForm = fingerprint(collect()); }
        else { savedForm = fingerprint(settings); } // Preserve edits made while the save was in flight.
        const plan = await json('/api/routing/mihomo/preview');
        if (number === editNumber) showPlan(plan, '已保存规则预览');
        notice('设置已保存。请在客户端刷新原订阅地址；无需重新创建令牌。');
    }));
    $('refresh-grants').addEventListener('click', () => operation(refreshGrants));
    $('grant-form').addEventListener('submit', event => { event.preventDefault(); operation(async () => {
        const ids = [...document.querySelectorAll('[data-node]:checked')].map(n => Number(n.dataset.node));
        const formats = [...document.querySelectorAll('input[name="format"]:checked')].map(n => n.value);
        if (!ids.length || !formats.length) throw new Error('请选择至少一个已验证节点和输出格式');
        hideIssued();
        showIssued(await json('/api/subscriptions', {method:'POST', body:JSON.stringify({label:$('label').value.trim(), server:$('server').value.trim(),
            inbound_ids:ids, formats, expires_days:Number($('days').value)})}));
        await refreshGrants(); notice('订阅已创建。请保存新地址，不要分享给他人。');
    }); });
    $('forget').addEventListener('click', hideIssued);
    $('copy').addEventListener('click', () => operation(async () => {
        if (!issued) return;
        try { await navigator.clipboard.writeText($('issued-urls').value); notice('地址已复制。'); }
        catch { $('issued-urls').focus(); $('issued-urls').select(); notice('无法自动复制，请复制已选中的地址。'); }
    }));
    $('download').addEventListener('click', () => operation(async () => {
        if (!issued?.paths['mihomo.yaml']) return;
        const response = await request(issued.paths['mihomo.yaml']); const objectUrl = URL.createObjectURL(await response.blob());
        const a = element('a', null, {href:objectUrl, download:'vui-mihomo.yaml'}); document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    }));
    addEventListener('beforeunload', event => { if (savedRevision && dirty()) { event.preventDefault(); event.returnValue = ''; } });
    addEventListener('pagehide', hideIssued);
    addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
    operation(async () => {
        await json('/api/auth/me'); catalog = await json('/api/routing/mihomo/catalog');
        catalog.categories.forEach(category => {
            const group = element('fieldset', null); group.append(element('legend', category.name_zh));
            catalog.presets.filter(p => p.category === category.id).forEach(preset => {
                const label = element('label', null, {class:'check'}); label.append(element('input', null, {type:'checkbox', 'data-preset':preset.id}), document.createTextNode(preset.name_zh)); group.append(label);
            }); $('presets').append(group);
        });
        await reload();
        const [nodes, warnings] = await Promise.all([json('/api/inbounds'), json('/api/subscription/mihomo-warnings')]);
        const invalid = new Set(warnings.map(w => w.inbound_id));
        nodes.forEach(node => {
            const input = element('input', null, {type:'checkbox', 'data-node':String(node.id)});
            input.disabled = !node.enable || invalid.has(node.id);
            const label = element('label', null, {class:'check'});
            label.append(input, document.createTextNode(`${node.remark || '节点 ' + node.id} · ${node.core}/${node.protocol}` + (input.disabled ? '（未验证或已禁用）' : ''))); $('nodes').append(label);
        });
        if (!nodes.length) $('nodes').textContent = '暂无节点，请先到节点管理创建。';
        await refreshGrants(); $('workspace').hidden = false;
    });
})();
