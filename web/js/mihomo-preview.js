'use strict';
(() => {
    const $ = id => document.getElementById(id);
    const dialog = $('yaml-preview-dialog');
    if (!dialog) return;
    const LIMIT = 1048576, REQUEST_LIMIT = 2097152, REPORT_LIMIT = 131072;
    const encoder = new TextEncoder();
    const roles = ['candidate', 'baseline'];
    const reads = {candidate: 0, baseline: 0}, reading = {candidate: false, baseline: false};
    const invalidFile = {candidate: false, baseline: false};
    let active = true, epoch = 0, busy = false, controller = null;
    const notices = {
        PARSER_BUSY: '解析器繁忙，请稍后手动重试。',
        PARSE_TIMEOUT: '解析超时，未生成报告；请缩小输入后重试。',
        PARSER_UNAVAILABLE: '解析器暂不可用，未生成报告。',
        PARSER_FAILED: '无法确认解析结果，未生成报告。',
        PARSER_RESOURCE_LIMIT: '解析达到资源限制，未生成报告；请缩小输入。',
        PARSER_LIMIT_UNAVAILABLE: '解析资源限制不可用，已拒绝解析。',
        INPUT_LIMIT: '输入超过 UTF-8 字节限制，请缩小输入。',
        INVALID_UTF8: '输入不是有效 UTF-8 文本。',
        INVALID_SHAPE: '输入或区块结构不符合预览要求。',
        DUPLICATE_KEY: 'YAML 包含重复字段，请检查后重试。',
        YAML_REFERENCE: '本工具不接受 anchor / alias 引用。',
        YAML_TAG: '本工具不接受 YAML tag 或 directive。',
        YAML_MERGE: '本工具不接受 YAML merge 字段。',
        YAML_MULTIDOCUMENT: '只接受一份 YAML 文档。',
        YAML_AMBIGUOUS_SCALAR: '存在歧义裸字面，请检查布尔、数字、日期等值；字符串需明确加引号。',
        PARSE_SYNTAX: 'YAML 语法无法解析，请检查输入。',
        SECTION_LIMIT: '节点、分组或规则数量超过本工具限制。',
        CANDIDATE_LIMIT: '节点数量超过本工具限制。',
        TOKEN_LIMIT: 'YAML token 数量超过限制。',
        DEPTH_LIMIT: '输入嵌套过深。',
        STRING_LIMIT: '输入含超过限制的单个字符串。',
        INTEGER_LIMIT: '输入含超出 64 位有符号整数范围的值。',
        NONFINITE_NUMBER: '输入含不支持的非有限数值。',
        NUMBER_LIMIT: '数值超过解析限制。',
        INVALID_STRING: '输入含不支持的控制字符或字符串。',
        UNSUPPORTED_VALUE: '输入包含本工具不支持的值。',
        INVALID_INPUT: '输入不符合预览要求。'
    };
    const notice = text => { $('yaml-preview-notice').textContent = text; };
    function controls() {
        $('yaml-preview-submit').disabled = !active || busy || roles.some(role => reading[role] || invalidFile[role]);
    }
    function invalidate() {
        epoch++;
        if (controller) controller.abort();
        controller = null;
        busy = false;
        $('yaml-preview-result').textContent = '';
        $('yaml-preview-result').hidden = true;
        controls();
    }
    function clear() {
        invalidate();
        for (const role of roles) {
            reads[role]++;
            reading[role] = false;
            invalidFile[role] = false;
            $('yaml-' + role).value = '';
            $('yaml-' + role + '-file').value = '';
        }
        notice('输入只留在本次页面；点击生成后才会发送给当前面板服务器解析。');
        controls();
    }
    function changed(role) {
        reads[role]++;
        reading[role] = false;
        invalidFile[role] = false;
        $('yaml-' + role + '-file').value = '';
        invalidate();
        notice('输入已更改；旧报告已清除，请重新生成。');
    }
    function live(token) { return active && dialog.open && token === epoch; }
    async function readFile(role) {
        const file = $('yaml-' + role + '-file').files?.[0];
        if (!file || !active || !dialog.open) return;
        changed(role);
        $('yaml-' + role).value = '';
        const token = reads[role];
        reading[role] = true;
        controls();
        notice('正在本地读取所选文件；尚未发送给面板服务器。');
        const current = () => active && dialog.open && reads[role] === token;
        try {
            if (!Number.isSafeInteger(file.size) || file.size < 1 || file.size > LIMIT) throw Error();
            const bytes = await file.arrayBuffer();
            if (!current()) return;
            if (bytes.byteLength !== file.size || bytes.byteLength > LIMIT) throw Error();
            const text = new TextDecoder('utf-8', {fatal: true}).decode(bytes);
            if (!text.trim() || encoder.encode(text).length > LIMIT) throw Error();
            $('yaml-' + role).value = text;
            invalidate();
            notice('文件已读入本次输入；点击生成后才会发送给面板服务器。');
        } catch (_) {
            if (!current()) return;
            invalidFile[role] = true;
            notice('文件读取失败、为空、超过 1 MiB 或不是有效 UTF-8；请重新选择、修改文本或清空。');
        } finally {
            if (current()) {
                reading[role] = false;
                controls();
            }
        }
    }
    async function submit(event) {
        event.preventDefault();
        if (!active || !dialog.open || busy || roles.some(role => reading[role] || invalidFile[role])) return;
        invalidate();
        const candidate = $('yaml-candidate').value, baseline = $('yaml-baseline').value;
        if (!candidate.trim()) { notice('请提供候选 YAML；可选基线留空表示不比较。'); return; }
        if (encoder.encode(candidate).length > LIMIT || encoder.encode(baseline).length > LIMIT) {
            notice('每份输入最多 1 MiB UTF-8 字节；请缩小输入。'); return;
        }
        const body = JSON.stringify({candidate, baseline: baseline === '' ? null : baseline});
        if (encoder.encode(body).length > REQUEST_LIMIT) {
            notice('包含 JSON 转义和语法的总请求超过 2 MiB；请缩小输入或取消基线比较。'); return;
        }
        const token = epoch;
        controller = new AbortController();
        busy = true;
        controls();
        notice('正在由面板服务器只读解析；不会保存或应用配置。');
        try {
            const response = await fetch('/api/proxy/yaml/preview', {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: {'Content-Type': 'application/json', 'X-VUI-Request': '1'},
                body, signal: controller.signal
            });
            if (!live(token)) return;
            if (response.status === 401) {
                active = false;
                clear();
                if (dialog.open) dialog.close();
                location.replace('/login');
                return;
            }
            const result = await response.json();
            if (!live(token)) return;
            if (!response.ok) {
                const code = result?.detail?.code, source = result?.detail?.source;
                const labels = {candidate: '候选：', baseline: '基线：', pair: '整体：'};
                const label = typeof source === 'string' && Object.hasOwn(labels, source) ? labels[source] : '';
                notice(typeof code === 'string' && Object.hasOwn(notices, code) ? label + notices[code] :
                       response.status === 413 ? '请求超过 2 MiB，请缩小输入。' : '预览请求未完成，请检查输入或稍后手动重试。');
                return;
            }
            if (!result || typeof result !== 'object' || Array.isArray(result) ||
                Object.keys(result).sort().join(',') !== 'can_apply,schema_version,text' ||
                result.schema_version !== 1 || result.can_apply !== false || typeof result.text !== 'string' ||
                !result.text || encoder.encode(result.text).length > REPORT_LIMIT) throw Error();
            $('yaml-preview-result').textContent = result.text;
            $('yaml-preview-result').hidden = false;
            notice('只读报告已生成。报告不是兼容证明，也不能用于应用配置。');
        } catch (_) {
            if (live(token)) notice('无法取得有效报告；输入保留，可手动重试。');
        } finally {
            if (live(token)) { controller = null; busy = false; controls(); }
        }
    }
    $('open-yaml-preview').addEventListener('click', () => {
        if (!active || dialog.open) return;
        clear();
        dialog.showModal();
        $('yaml-candidate').focus();
    });
    $('close-yaml-preview').addEventListener('click', () => { clear(); if (dialog.open) dialog.close(); });
    $('clear-yaml-preview').addEventListener('click', clear);
    // Native close events are queued. An old close delivered after reopening
    // must not clear the new dialog session or abort its request.
    dialog.addEventListener('close', () => { if (!dialog.open) clear(); });
    dialog.addEventListener('cancel', clear);
    $('yaml-preview-form').addEventListener('submit', submit);
    for (const role of roles) {
        $('yaml-' + role).addEventListener('input', () => changed(role));
        $('yaml-' + role + '-file').addEventListener('change', () => readFile(role));
    }
    addEventListener('pagehide', () => { active = false; clear(); if (dialog.open) dialog.close(); });
    addEventListener('pageshow', () => { active = true; clear(); if (dialog.open) dialog.close(); });
    clear();
})();
