'use strict';
(() => {
    // A fragment is never submitted as part of a URL or copied into navigation.
    let grant = '';
    const fragment = String(location.hash || '');
    const match = /^#grant=(vui_mg_[A-Za-z0-9_-]{43})$/.exec(fragment);
    if (match) grant = match[1];
    if (fragment) history.replaceState(null, '', location.pathname);
    const hadFragment = !!fragment;
    const start = () => {
        const el = id => document.getElementById(id);
        const names = {diagnose:'只读诊断',nodes:'节点管理',share:'节点与订阅分享',relay:'中转配置',
            certificates:'证书管理',operate:'服务与备份',upgrade:'在线升级',destructive:'删除与恢复'};
        const actions = {status:'读取服务状态',start:'启动服务',stop:'停止服务',restart:'重启服务',backup:'私有备份',upgrade:'在线升级',restore:'停机恢复备份'};
        const phases = {queued:'排队中',running:'执行中',succeeded:'成功',success:'成功',failed:'失败',interrupted:'已中断',pending:'待执行',completed:'已完成'};
        let operationView = null;
        let token = '', admin = false, ended = false, epoch = 0, busy = false, scopes = [], issuedID = '';
        const controllers = new Set();
        let taskTimer=null, taskSubmissionUncertain=false, watchingTask=null;
        const notice = value => { el('maintenance-notice').textContent = value; };
        const clearLink = () => { el('maintenance-link').value = ''; el('maintenance-issued').hidden = true; issuedID = ''; };
        const end = () => {
            clearTimeout(taskTimer);watchingTask=null;taskSubmissionUncertain=false;
            epoch++; ended = true; operationView?.destroy();operationView=null;token = ''; grant = ''; clearLink();
            controllers.forEach(controller => controller.abort()); controllers.clear();
            ['maintenance-operations','maintenance-admin-links','maintenance-admin','maintenance-remote','maintenance-tasks-panel','maintenance-audit-panel','maintenance-logs-panel'].forEach(id => { el(id).hidden = true; });
            ['maintenance-grants','maintenance-tasks','maintenance-audit','maintenance-session','maintenance-logs'].forEach(id => { el(id).textContent = ''; });
            el('maintenance-installation').textContent = '';
            el('maintenance-backup-id').value = '';
        };
        const request = async (path, method = 'GET', data, exchange = false, extraHeaders = {}, fullResponse = false) => {
            if (ended) throw Object.assign(new Error('维护会话已结束，请重新获得授权。'), {status:401});
            const captured = epoch, controller = new AbortController(); controllers.add(controller);
            const timer = setTimeout(() => controller.abort(), 15000);
            try {
                const headers = {'X-VUI-Request':'1'};
                if (data !== undefined) headers['Content-Type'] = 'application/json';
                for(const key of ['If-Match','Idempotency-Key'])if(extraHeaders[key])headers[key]=extraHeaders[key];
                if (token && !exchange) headers.Authorization = 'Bearer ' + token;
                const response = await fetch('/api/' + path, {method, headers, credentials: token || exchange ? 'omit' : 'same-origin',
                    cache:'no-store', redirect:'error', signal:controller.signal, ...(data !== undefined ? {body:JSON.stringify(data)} : {})});
                const type=response.headers?.get('content-type')||'application/json';
                const value=type.includes('json')?await response.json():await response.text();
                if (captured !== epoch) throw new Error('请求已取消。');
                if (!response.ok) {
                    if (response.status === 401 && !exchange) { end(); notice('授权或登录已失效，请重新登录或获得维护授权。'); }
                    const labels = {401:'授权无效、已兑换、已撤销或已到期。',403:'本次授权没有这项操作权限。',409:'当前状态不允许操作，请核对配置或稍后刷新。',422:'输入无效，请核对权限与有效期。',429:'请求过于频繁，请稍后再试。',503:'系统维护组件暂不可用，请使用终端检查服务。'};
                    throw Object.assign(new Error(labels[response.status] || '请求未完成，请检查服务后重试。'), {status:response.status,detail:value?.detail});
                }
                return fullResponse?{value,nodeRevision:response.headers.get('X-VUI-Node-Revision')}:value;
            } finally { clearTimeout(timer); controllers.delete(controller); }
        };
        const time = value => Number.isFinite(value) ? new Date(value * 1000).toLocaleString('zh-CN') : '未确认';
        const text = (parent, value) => { const p = document.createElement('p'); p.textContent = value; parent.append(p); };
        const record = parent => { const row = document.createElement('div'); row.className = 'maintenance-record'; parent.append(row); return row; };
        const allowed = action => admin || scopes.includes(action === 'upgrade' ? 'upgrade' : action === 'status' ? 'diagnose' : 'operate');
        const sync = () => {
            el('maintenance-create').disabled = busy || ended;
            el('maintenance-restore').disabled = busy || ended || taskSubmissionUncertain || !(admin || scopes.includes('destructive'));
            el('maintenance-actions').querySelectorAll('button').forEach(button => { button.disabled = busy || ended || !allowed(button.dataset.task) || taskSubmissionUncertain&&button.dataset.task!=='status'; });
        };
        const run = async operation => {
            if (busy || ended) return;
            busy = true; sync(); const captured = epoch;
            try { await operation(); } catch (error) { if (!ended && captured === epoch) notice(error.name === 'AbortError' ? '请求超时，操作结果尚未确认；请刷新记录，勿重复提交。' : Number.isInteger(error.status) ? error.message : '请求未完成或响应无效，请刷新记录核对；原始错误不会在此显示。'); }
            finally { if (captured === epoch) { busy = false; sync(); } }
        };
        const grants = async () => {
            const rows = await request('maintenance/grants');
            const box = el('maintenance-grants'); box.textContent = '';
            if (!rows.length) text(box, '暂无维护授权。');
            rows.forEach(row => {
                const entry = record(box);
                text(entry, '授权 ' + row.id + ' · ' + (row.revoked ? '已撤销' : row.expired ? '已到期' : row.redeemed ? '已兑换' : '待兑换'));
                text(entry, (row.scopes || []).map(scope => names[scope] || '未知权限').join('、'));
                text(entry, '截止时间：' + time(row.expires_at));
                const button = document.createElement('button'); button.type = 'button'; button.textContent = '立即撤销'; button.disabled = row.revoked || row.expired;
                button.addEventListener('click', () => run(async () => {
                    await request('maintenance/grants/' + encodeURIComponent(row.id), 'DELETE');
                    if (issuedID === row.id) clearLink();
                    notice('授权已撤销，后续维护请求将被拒绝。'); await grants();
                })); entry.append(button);
            });
        };
        const tasks = async () => {
            const value = await request('maintenance/tasks');
            const rows = Array.isArray(value) ? value : value.tasks || [];
            const box = el('maintenance-tasks'); box.textContent = '';
            if (!rows.length) text(box, '暂无维护任务。');
            const select=el('maintenance-backup-id'),chosen=select.value;select.textContent='';
            const empty=document.createElement('option');empty.value='';empty.textContent='选择一个成功完成的私有备份';select.append(empty);
            rows.filter(row=>row.action==='backup'&&row.state==='succeeded'&&/^[a-f0-9]{32}$/.test(row.id)).forEach(row=>{const option=document.createElement('option');option.value=row.id;option.textContent=time(row.created_at)+' · 私有备份';select.append(option);});
            if(rows.some(row=>row.id===chosen&&row.action==='backup'&&row.state==='succeeded'))select.value=chosen;
            rows.forEach(row => {
                const entry = record(box);
                text(entry, (actions[row.action] || '维护任务') + ' · ' + (phases[row.state || row.status] || '状态未确认'));
                text(entry, '任务 ID：' + row.id);
                text(entry, '创建时间：' + time(row.created_at));
                if (/^v?[0-9]+\.[0-9]+\.[0-9]+$/.test(row.result?.version || '')) text(entry, '目标版本：' + row.result.version);
                if (row.error || row.code) text(entry, '结果代码：' + String(row.error || row.code).replace(/[^A-Z0-9_-]/g, '').slice(0,80));
            });
        };
        const watchTask=async(id,attempt=0)=>{
            if(ended||watchingTask!==id)return;
            const captured=epoch;
            try{
                const row=await request('maintenance/tasks/'+id);
                if(ended||epoch!==captured||watchingTask!==id)return;
                const state=row.state||row.status;
                if(['succeeded','failed','interrupted','success','completed'].includes(state)){
                    watchingTask=null;await tasks();notice((actions[row.action]||'维护任务')+'：'+(phases[state]||'已结束')+'。请按结果核对服务状态。');return;
                }
                notice('任务'+(phases[state]||'状态待确认')+'，正在自动读取进度；不要重复提交。');
            }catch(_){if(ended||epoch!==captured)return;notice('面板可能正在重启，正在重新读取已有任务；不会重复提交。');}
            if(attempt<59)taskTimer=setTimeout(()=>watchTask(id,attempt+1),5000);
            else{watchingTask=null;notice('自动等待已结束，任务结果仍需核对。点击“刷新维护任务”，不要再次提交同一操作。');}
        };
        const submitTask=async body=>{
            if(taskSubmissionUncertain)return;
            // Mark before the POST; a lost reply is never retried automatically.
            taskSubmissionUncertain=true;sync();
            try{
                const row=await request('maintenance/tasks','POST',body);
                if(!/^[a-f0-9]{32}$/.test(row.id||''))throw new Error('Invalid task response');
                taskSubmissionUncertain=false;watchingTask=row.id;notice('维护任务已登记，正在自动读取进度。');
                clearTimeout(taskTimer);taskTimer=setTimeout(()=>watchTask(row.id),1000);await tasks();
            }catch(error){
                if([400,403,409,422,429].includes(error.status))taskSubmissionUncertain=false;
                else{const ack=el('maintenance-task-ack');if(ack)ack.hidden=false;}
                throw error;
            }
        };
        const ack=el('maintenance-task-ack');if(ack)ack.addEventListener('click',()=>run(async()=>{
            await tasks();if(!confirm('请确认已核对任务记录和实际服务状态。上次请求可能已执行，允许下一次操作并不会撤销它。继续？'))return;
            taskSubmissionUncertain=false;ack.hidden=true;notice('已允许下一次维护操作。');
        }));
        const audit = async () => {
            const rows = await request('maintenance/audit');
            const box = el('maintenance-audit'); box.textContent = '';
            if (!rows.length) text(box, '暂无操作记录。');
            rows.forEach(row => { const entry = record(box); text(entry, time(row.created_at) + ' · ' + row.operation + ' · 状态 ' + row.status); });
        };
        const logs = async () => {
            const value = await request('maintenance/logs');
            const rows = Array.isArray(value) ? value : value.entries || [];
            const box = el('maintenance-logs'); box.textContent = '';
            const codes = {PERMISSION_DENIED:'权限不足',UNCLASSIFIED:'未分类服务日志',
                ADDRESS_IN_USE:'端口被占用',CONNECTION_REFUSED:'连接被拒绝',CERTIFICATE_ERROR:'证书验证或材料异常',
                TLS_ERROR:'TLS 异常',TIMEOUT:'操作超时',SERVICE_STARTED:'服务启动',SERVICE_STOPPED:'服务停止',
                TLS_VERIFY_FAILED:'TLS 证书验证失败',CODE_INTEGRITY:'启动代码完整性验证失败',MANIFEST_CHANGED:'已准备版本的清单发生变化',
                RUNTIME_MISSING:'已准备运行时缺失',PORT_IN_USE:'服务监听端口已占用',DEPENDENCY_MISSING:'服务依赖缺失',
                MEMORY_EXHAUSTED:'服务内存不足',PROCESS_EXITED:'主服务进程已退出',SERVICE_FAILED:'服务运行失败',
                RESTART_SCHEDULED:'服务已安排重新启动',HTTPS_LISTENING:'面板已监听 HTTPS'};
            if (!rows.length) text(box, '暂无可显示的近期诊断。');
            rows.forEach(row => { const entry = record(box); text(entry, time(row.timestamp) + ' · ' + (codes[row.code] || '服务事件，请结合节点与任务状态核对')); });
        };
        const installation = async () => {
            const value = await request('maintenance/status');
            const identity = /^[0-9]+\.[0-9]+\.[0-9]+-[a-f0-9]{12}$/.test(value.release_id || '') ? value.release_id : '版本未确认';
            const state = value.services?.['v-ui.service'];
            el('maintenance-installation').textContent = identity + ' · ' + (state === 'active' ? '面板服务运行中' : state === 'inactive' ? '面板服务已停止' : '服务状态未确认');
        };
        const preset=el('maintenance-permission-preset');
        if(preset)preset.addEventListener('change',()=>{
            if(preset.value==='custom')return;
            const chosen=preset.value==='full'?['diagnose','nodes','share','relay','certificates','operate','upgrade','destructive']:preset.value==='daily'?['diagnose','nodes','share','relay','certificates','operate','upgrade']:['diagnose'];
            el('maintenance-scopes').querySelectorAll('input').forEach(input=>{input.checked=chosen.includes(input.value);});
        });
        el('maintenance-grant-form').addEventListener('submit', event => { event.preventDefault(); run(async () => {
            clearLink();
            const selected = [...el('maintenance-scopes').querySelectorAll('input:checked')].map(input => input.value);
            const row = await request('maintenance/grants', 'POST', {scopes:selected, expires_seconds:Number(el('maintenance-duration').value)});
            if (typeof row.link !== 'string' || !row.link.startsWith(location.origin + '/maintenance#grant=vui_mg_')) throw new Error('维护链接来源不匹配，未显示。');
            issuedID = row.id; el('maintenance-link').value = row.link; el('maintenance-issued').hidden = false;
            notice('维护链接已生成，只展示一次；交给可信维护者后可随时撤销。'); await grants();
        }); });
        el('maintenance-copy').addEventListener('click', () => run(async () => {
            const captured = epoch, link = el('maintenance-link').value;
            if (!link) return;
            try { await navigator.clipboard.writeText(link); if (captured === epoch && link === el('maintenance-link').value) notice('维护链接已复制，请仅交给可信维护者。'); }
            catch (_) { if (captured === epoch && link === el('maintenance-link').value) { el('maintenance-link').focus(); el('maintenance-link').select(); notice('复制失败，请手动复制已选中的链接。'); } }
        }));
        el('maintenance-clear').addEventListener('click', clearLink);
        el('maintenance-end').addEventListener('click', () => { end(); notice('本页维护会话已清除。'); });
        el('maintenance-refresh-grants').addEventListener('click', () => run(grants));
        el('maintenance-refresh-tasks').addEventListener('click', () => run(tasks));
        el('maintenance-refresh-audit').addEventListener('click', () => run(audit));
        el('maintenance-refresh-logs').addEventListener('click', () => run(logs));
        el('maintenance-refresh-status').addEventListener('click', () => run(installation));
        el('maintenance-restore').addEventListener('click', () => run(async () => {
            if (!(admin || scopes.includes('destructive'))) return;
            const backupID = el('maintenance-backup-id').value;
            if (!/^[a-f0-9]{32}$/.test(backupID)) { notice('请先读取任务列表，选择成功完成的私有备份。'); return; }
            if (!confirm('确认停机恢复所选备份？这会将业务数据恢复为备份时状态。当前数据会按现有恢复流程保留，失败后不会自动启动；请核对备份任务 ID。')) return;
            await submitTask({action:'restore',backup_id:backupID});
        }));
        el('maintenance-actions').querySelectorAll('button').forEach(button => button.addEventListener('click', () => run(async () => {
            const action = button.dataset.task;
            if (!allowed(action)) return;
            if (action !== 'status' && !confirm('确认执行“' + actions[action] + '”？这可能短暂中断服务。升级将保留原配置和数据。')) return;
            await submitTask({action});
        })));
        addEventListener('pagehide', end);
        try { const channel = new BroadcastChannel('vui-admin-session'); channel.onmessage = event => { if (event.data === 'session-ended') { end(); notice('管理员会话已变化，请重新核对授权。'); } }; } catch (_) {}
        run(async () => {
            if (hadFragment) {
                if (!grant) throw new Error('维护链接格式无效，请向管理员重新获取。');
                let result;
                try { result = await request('maintenance/exchange', 'POST', {grant}, true); } finally { grant = ''; }
                token = result.token; scopes = result.scopes;
                if (typeof token !== 'string' || !/^vui_ms_[A-Za-z0-9_-]{43}$/.test(token) || !Array.isArray(scopes)) { end(); notice('维护授权响应无效，请重新获取授权。'); return; }
                el('maintenance-remote').hidden = false;
                el('maintenance-session').textContent = '截止：' + time(result.expires_at) + ' · 权限：' + scopes.map(scope => names[scope] || '未知权限').join('、');
                notice('已建立临时维护会话，原一次性链接已失效。');
            } else {
                await request('auth/me'); admin = true; el('maintenance-admin').hidden = false; await grants(); notice('已验证管理员身份。默认只读，请按需选择维护权限。');
            }
            if(globalThis.VUI_SIMPLE_OPERATIONS&&el('maintenance-operations')){
                el('maintenance-operations').hidden=false;
                operationView=globalThis.VUI_SIMPLE_OPERATIONS.mount(el('maintenance-operations'),{
                    request:(path,method,body,{headers={},fullResponse=false}={})=>request(path.replace(/^\/api\//,''),method,body===null?undefined:body,false,headers,fullResponse),
                    scopes:admin?null:scopes,remote:!admin,expired:()=>{end();notice('维护授权已失效，请重新获取授权。');}
                });
            }
            el('maintenance-admin-links').hidden=!admin;
            if(!admin){document.body?.classList?.add('vui-delegated');const brand=document.querySelector?.('.vui-shell-brand');if(brand)brand.href='#maintenance-remote';}
            el('maintenance-tasks-panel').hidden = false; el('maintenance-audit-panel').hidden = false;
            el('maintenance-logs-panel').hidden = !(admin || scopes.includes('diagnose'));
            await audit();
        });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true}); else start();
})();
