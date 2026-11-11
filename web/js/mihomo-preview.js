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
    let mergeReview = null, mergeSelects = [];
    let planOptions = null, planSelects = [], planDns = null, planRevision = null;
    let writing = false;
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
        INVALID_INPUT: '输入不符合预览要求。',
        MERGE_CHOICES_REQUIRED: '两份 YAML 有冲突，请点击“检查合并冲突”，逐项选择保留哪份内容。',
        MERGE_REVIEW_STALE: '输入或冲突顺序已变化，请重新检查合并冲突。',
        MERGE_REVIEW_LIMIT: '冲突超过 256 项，请先缩小输入。',
        MERGE_INVALID_CHOICES: '冲突选择不完整或无效，请重新检查。',
        PLAN_UNSUPPORTED_SECTION: '配置含尚不能运行的区块。可以合并下载，请先移除这些区块再保存运行草稿。',
        PLAN_UNSUPPORTED_NODE: '存在尚不支持的运行节点，请核对节点协议和 TLS 设置。',
        PLAN_UNSUPPORTED_GROUP: '运行草稿只支持静态、非嵌套的选择分组。',
        PLAN_UNSUPPORTED_RULE: '存在尚不支持的规则，请使用域名、字面 CIDR no-resolve 和末尾 MATCH。',
        PLAN_FINAL_REQUIRED: '运行规则必须以唯一的 MATCH 结束。',
        PLAN_RULE_MODE_REQUIRED: '运行草稿需要显式设置 mode: rule。',
        PROXY_REVISION_CONFLICT: '服务器草稿已变化，请重新点击“选择运行分组”。',
        PROXY_STORAGE_UNAVAILABLE: '私有存储暂不可用，请核对状态后重试。',
        PROXY_CAPACITY_EXHAUSTED: '私有历史容量已满，请保留原文件并联系管理员。',
        MERGE_INVALID_REFERENCES: '合并结果包含悬空引用、分组循环或无效规则顺序。',
        MERGE_NAME_COLLISION: '合并结果包含重复或保留的节点、分组名称。',
        MERGE_RESULT_LIMIT: '合并结果超出限制，请缩小输入。',
        MERGE_SERIALIZATION_REJECTED: '合并结果无法安全重新导入，请检查格式与大小。'
    };
    const notice = text => { $('yaml-preview-notice').textContent = text; };
    function controls() {
        const blocked = !active || busy || roles.some(role => reading[role] || invalidFile[role]);
        $('yaml-preview-submit').disabled = blocked;
        const both = roles.every(role => $('yaml-' + role).value.trim());
        $('yaml-merge-review').disabled = blocked || !both;
        $('yaml-merge-download').disabled = blocked || !both || mergeSelects.some(select => !select.value);
        const resolved = !!mergeReview && !mergeSelects.some(select => !select.value);
        $('yaml-plan-options').disabled = blocked || !both || !resolved;
        $('yaml-plan-save').disabled = blocked || !resolved || !planOptions || !planRevision ||
            planSelects.some(row => !row.select.value) || !planDns?.value;
        for (const select of mergeSelects) select.disabled = blocked;
        for (const row of planSelects) row.select.disabled = blocked;
        if (planDns) planDns.disabled = blocked;
        for (const role of roles) {
            $('yaml-' + role).disabled = writing;
            $('yaml-' + role + '-file').disabled = writing;
        }
        $('close-yaml-preview').disabled = writing;
        $('clear-yaml-preview').disabled = writing;
    }
    function clearPlan() {
        planOptions = null;
        planSelects = [];
        planDns = null;
        planRevision = null;
        $('yaml-plan-choices').replaceChildren();
        $('yaml-plan-choices').hidden = true;
    }
    function clearReview() {
        clearPlan();
        mergeReview = null;
        mergeSelects = [];
        $('yaml-merge-conflicts').replaceChildren();
        $('yaml-merge-conflicts').hidden = true;
    }
    function showConflicts(result, baseline, candidate) {
        const sections = {proxies: '节点', 'proxy-groups': '分组', rules: '规则顺序', mode: '运行模式', other: '其他字段'};
        if (!result || typeof result !== 'object' || Array.isArray(result) ||
            Object.keys(result).sort().join(',') !== 'conflicts,fingerprint' ||
            typeof result.fingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(result.fingerprint) ||
            !Array.isArray(result.conflicts) || result.conflicts.length > 256) throw Error();
        const lineLimits = {baseline: baseline.split(/\r\n|\r|\n/).length + 1,
                            candidate: candidate.split(/\r\n|\r|\n/).length + 1};
        for (const [index, row] of result.conflicts.entries()) {
            if (!row || typeof row !== 'object' || Array.isArray(row) ||
                Object.keys(row).sort().join(',') !== 'baseline,candidate,index,section' ||
                row.index !== index || typeof row.section !== 'string' || !Object.hasOwn(sections, row.section)) throw Error();
            for (const source of roles) {
                const point = row[source];
                if (!Array.isArray(point) || point.length !== 2 ||
                    point.some(n => !Number.isSafeInteger(n) || n < 1) ||
                    point[0] > lineLimits[source] || point[1] > LIMIT) throw Error();
            }
        }
        clearReview();
        mergeReview = result;
        const panel = $('yaml-merge-conflicts');
        const hint = document.createElement('p');
        hint.textContent = '请对照原文选择。行列从 1 开始，列号按 Unicode 字符计数。';
        panel.append(hint);
        for (const row of result.conflicts) {
            const label = document.createElement('label');
            label.textContent = '冲突 ' + (row.index + 1) + ' · ' + sections[row.section] +
                ' · 基线第 ' + row.baseline[0] + ' 行第 ' + row.baseline[1] + ' 列' +
                ' / 候选第 ' + row.candidate[0] + ' 行第 ' + row.candidate[1] + ' 列';
            const select = document.createElement('select');
            for (const [value, text] of [['', '请选择要保留的内容'], ['baseline', '保留基线内容'], ['candidate', '使用候选内容']]) {
                const option = document.createElement('option');
                option.value = value;
                option.textContent = text;
                select.append(option);
            }
            select.addEventListener('change', () => { clearPlan(); invalidate(true); });
            label.append(select);
            panel.append(label);
            mergeSelects.push(select);
        }
        panel.hidden = result.conflicts.length === 0;
    }
    function showPlanOptions(result, revision) {
        const validName = name => typeof name === 'string' && Array.from(name).length <= 128 &&
            name.trim().length > 0 && encoder.encode(name).length <= 512;
        if (!result || typeof result !== 'object' || Array.isArray(result) ||
            Object.keys(result).sort().join(',') !== 'groups,upstreams' ||
            !Array.isArray(result.upstreams) || result.upstreams.length > 128 ||
            result.upstreams.some(name => !validName(name)) ||
            new Set(result.upstreams).size !== result.upstreams.length ||
            !Array.isArray(result.groups) || result.groups.length > 128) throw Error();
        const names = new Set([...result.upstreams, 'DIRECT', 'REJECT']);
        const groupNames = new Set();
        for (const row of result.groups) {
            if (!row || typeof row !== 'object' || Array.isArray(row) ||
                Object.keys(row).sort().join(',') !== 'members,name' ||
                !validName(row.name) || groupNames.has(row.name) || names.has(row.name) ||
                !Array.isArray(row.members) || !row.members.length || row.members.length > names.size ||
                row.members.some(name => !names.has(name)) || new Set(row.members).size !== row.members.length) throw Error();
            groupNames.add(row.name);
        }
        clearPlan();
        planOptions = result;
        planRevision = revision;
        const panel = $('yaml-plan-choices');
        const hint = document.createElement('p');
        hint.textContent = '保存仅更新运行草稿。当前版本的 YAML 运行应用仍在开发中。';
        panel.append(hint);
        function selector(labelText, values) {
            const label = document.createElement('label');
            label.textContent = labelText;
            const select = document.createElement('select');
            for (const [value, text] of [['', '请选择'], ...values.map(value => [value, value])]) {
                const option = document.createElement('option');
                option.value = value;
                option.textContent = text;
                select.append(option);
            }
            if (values.length === 1) select.value = values[0];
            select.addEventListener('change', controls);
            label.append(select);
            panel.append(label);
            return select;
        }
        for (const row of result.groups) {
            planSelects.push({name: row.name, select: selector('分组 ' + row.name + ' 使用', row.members)});
        }
        planDns = selector('保护解析使用的上游', result.upstreams.length ? result.upstreams : ['无上游']);
        panel.hidden = false;
    }
    function invalidate(keepReview = false) {
        epoch++;
        if (controller) controller.abort();
        controller = null;
        busy = false;
        if (!keepReview) clearReview();
        $('yaml-preview-result').textContent = '';
        $('yaml-preview-result').hidden = true;
        controls();
    }
    function clear(force = false) {
        if (writing && force !== true) return;
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
        if (writing) return;
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
        if (!file || !active || !dialog.open || writing) return;
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
        const download = event.submitter?.id === 'yaml-merge-download';
        const review = event.submitter?.id === 'yaml-merge-review';
        const options = event.submitter?.id === 'yaml-plan-options';
        const save = event.submitter?.id === 'yaml-plan-save';
        if ((options || save) && (!mergeReview || mergeSelects.some(select => !select.value))) {
            notice('请先检查合并冲突并逐项完成选择。'); return;
        }
        if (save && (!planOptions || !planRevision || planSelects.some(row => !row.select.value) || !planDns?.value)) {
            notice('请先选择运行分组与保护解析上游。'); return;
        }
        if (download && mergeReview && mergeSelects.some(select => !select.value)) {
            notice('请先为每项冲突选择基线或候选内容。'); return;
        }
        const selectedReview = download || options || save ? mergeReview : null;
        const selections = mergeSelects.map(select => select.value);
        const groups = save ? Object.fromEntries(planSelects.map(row => [row.name, row.select.value])) : null;
        const dns = save && planOptions.upstreams.length ? planDns.value : null;
        const revision = save ? planRevision : null;
        if (options) clearPlan();
        invalidate(download || options || save);
        const candidate = $('yaml-candidate').value, baseline = $('yaml-baseline').value;
        if ((download || review || options || save) && !baseline.trim()) { notice('合并下载需要提供基线 YAML。'); return; }
        if (!candidate.trim()) { notice('请提供候选 YAML；可选基线留空表示不比较。'); return; }
        if (encoder.encode(candidate).length > LIMIT || encoder.encode(baseline).length > LIMIT) {
            notice('每份输入最多 1 MiB UTF-8 字节；请缩小输入。'); return;
        }
        const payload = {candidate, baseline: baseline === '' ? null : baseline};
        if (selectedReview) {
            payload.fingerprint = selectedReview.fingerprint;
            payload.selections = selections;
        }
        if (save) {
            payload.group_members = groups;
            payload.dns_upstream = dns;
        }
        const body = JSON.stringify(payload);
        if (encoder.encode(body).length > REQUEST_LIMIT) {
            notice('包含 JSON 转义和语法的总请求超过 2 MiB；请缩小输入或取消基线比较。'); return;
        }
        const token = epoch;
        controller = new AbortController();
        busy = true;
        writing = save;
        let saveConfirmed = false, saveRejected = false, saveConflict = false;
        controls();
        notice(save ? '正在检查并保存运行草稿。' : options ? '正在读取可选运行分组。' : review ? '正在检查合并冲突。' : download ? '正在合并两份 YAML，完成后会下载附件。' :
               '正在由面板服务器只读解析；不会保存或应用配置。');
        try {
            const endpoint = save ? '/api/proxy/yaml/draft' : options ? '/api/proxy/yaml/plan/options' :
                review ? '/api/proxy/yaml/merge/review' :
                download ? '/api/proxy/yaml/merge/download' : '/api/proxy/yaml/preview';
            const response = await fetch(endpoint, {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: {'Content-Type': 'application/json', 'X-VUI-Request': '1', ...(save ? {'If-Match': revision} : {})},
                ...(download || review || options || save ? {redirect: 'error'} : {}),
                body, signal: controller.signal
            });
            if (!live(token)) return;
            if (save && response.status >= 400 && response.status < 500) {
                saveRejected = true;
                saveConflict = response.status === 409;
            }
            if (response.status === 401) {
                active = false;
                clear(true);
                if (dialog.open) dialog.close();
                globalThis.VUI_NAV ? VUI_NAV.replaceLogin() : location.replace('/login');
                return;
            }
            if (download && response.ok) {
                const length = response.headers.get('content-length');
                if (response.status !== 200 || typeof length !== 'string' || !/^[1-9]\d{0,6}$/.test(length) || Number(length) > LIMIT ||
                    response.headers.get('content-type') !== 'application/yaml' ||
                    response.headers.get('content-disposition') !== 'attachment; filename="vui-merged.yaml"') throw Error();
                const blob = await response.blob();
                if (!live(token)) return;
                if (blob.size !== Number(length)) throw Error();
                const link = document.createElement('a');
                const url = URL.createObjectURL(blob);
                try {
                    link.href = url;
                    link.download = 'vui-merged.yaml';
                    document.body.append(link);
                    link.click();
                } finally {
                    link.remove();
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                }
                notice('合并 YAML 已交给浏览器下载。');
                return;
            }
            const result = await response.json();
            if (!live(token)) return;
            if (!response.ok) {
                const code = typeof result?.detail === 'string' ? result.detail : result?.detail?.code, source = result?.detail?.source;
                if (code === 'MERGE_REVIEW_STALE') clearReview();
                if (response.status === 409) clearPlan();
                if (save && response.status >= 500) {
                    notice('保存结果未确认。请重新读取服务器草稿后再保存。');
                    return;
                }
                const labels = {candidate: '候选：', baseline: '基线：', pair: '整体：'};
                const label = typeof source === 'string' && Object.hasOwn(labels, source) ? labels[source] : '';
                notice(typeof code === 'string' && Object.hasOwn(notices, code) ? label + notices[code] :
                       response.status === 413 ? '请求超过 2 MiB，请缩小输入。' : (save || options ? '运行草稿请求未完成，请核对输入与服务器状态。' : download ? '合并请求未完成，请检查输入或稍后手动重试。' : '预览请求未完成，请检查输入或稍后手动重试。'));
                return;
            }
            if (options) {
                const snapshotResponse = await fetch('/api/proxy/settings', {
                    method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
                    signal: controller.signal
                });
                if (!live(token)) return;
                if (snapshotResponse.status === 401) {
                    active = false; clear(true); if (dialog.open) dialog.close();
                    globalThis.VUI_NAV ? VUI_NAV.replaceLogin() : location.replace('/login');
                    return;
                }
                const snapshot = await snapshotResponse.json();
                if (!live(token)) return;
                if (!snapshotResponse.ok || typeof snapshot?.revision !== 'string' ||
                    !/^[a-f0-9]{64}$/.test(snapshot.revision) || snapshot.pending !== null ||
                    snapshotResponse.headers.get('etag') !== '"' + snapshot.revision + '"') throw Error();
                showPlanOptions(result, snapshot.revision);
                notice('请选择分组与保护解析上游，再保存运行草稿。');
                return;
            }
            if (save) {
                if (response.status !== 200 || !result || typeof result.revision !== 'string' ||
                    !/^[a-f0-9]{64}$/.test(result.revision) || result.execution_mode !== 'yaml' ||
                    typeof result.yaml_plan?.revision !== 'string' || !/^[a-f0-9]{64}$/.test(result.yaml_plan.revision) ||
                    response.headers.get('etag') !== '"' + result.revision + '"') throw Error();
                planRevision = null;
                saveConfirmed = true;
                notice('YAML 运行草稿已保存。');
                return;
            }
            if (review) {
                showConflicts(result, baseline, candidate);
                notice(result.conflicts.length ? '请逐项选择，再点击“合并并下载 YAML”。' : '未发现合并冲突，可以下载。');
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
            if (live(token)) notice(save ? '保存结果未确认。请重新选择运行分组并核对服务器草稿后再保存。' : options ?
                '无法确认运行分组选项，请重新读取。' : download ? '无法取得有效附件；输入保留，可手动重试。' :
                                    '无法取得有效报告；输入保留，可手动重试。');
        } finally {
            if (save) {
                writing = false;
                planRevision = null;
                if (saveConflict) dispatchEvent(new Event('vui-yaml-draft-conflict'));
                else if (!saveRejected) dispatchEvent(new Event(saveConfirmed ? 'vui-yaml-draft-saved' : 'vui-yaml-draft-uncertain'));
            }
            if (live(token)) { controller = null; busy = false; controls(); }
            else if (save) controls();
        }
    }
    $('open-yaml-preview').addEventListener('click', () => {
        if (!active || dialog.open) return;
        clear();
        dialog.showModal();
        globalThis.VUI_NAV?.modal.open('yaml-preview', () => {
            if (writing) { notice('正在保存，请等待结果。'); return; }
            clear(); if (dialog.open) dialog.close();
        }, () => !writing);
        $('yaml-candidate').focus();
    });
    $('close-yaml-preview').addEventListener('click', () => { if (writing) return; clear(); if (dialog.open) dialog.close(); });
    $('clear-yaml-preview').addEventListener('click', clear);
    // Native close events are queued. An old close delivered after reopening
    // must not clear the new dialog session or abort its request.
    dialog.addEventListener('close', () => { if (!dialog.open) { clear(); globalThis.VUI_NAV?.modal.close('yaml-preview'); } });
    dialog.addEventListener('cancel', event => {
        if (writing) { event.preventDefault(); notice('正在保存，请等待结果。'); return; }
        clear();
    });
    $('yaml-preview-form').addEventListener('submit', submit);
    for (const role of roles) {
        $('yaml-' + role).addEventListener('input', () => changed(role));
        $('yaml-' + role + '-file').addEventListener('change', () => readFile(role));
    }
    addEventListener('pagehide', () => { active = false; clear(true); if (dialog.open) dialog.close(); });
    addEventListener('pageshow', () => { active = true; clear(); if (dialog.open) dialog.close(); });
    clear();
})();
