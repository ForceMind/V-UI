'use strict';
(() => {
    // A fragment is never submitted as part of a URL or copied into navigation.
    let grant = '', coreGrant = '';
    let fragment = String(location.hash || '');
    let match = /^#grant=(vui_mg_[A-Za-z0-9_-]{43})(?:&core=(vui_cg_[a-f0-9]{32}_[A-Za-z0-9_-]{43}))?$/.exec(fragment);
    if (match) {grant = match[1];coreGrant = match[2] || '';}
    if (fragment) history.replaceState(null, '', location.pathname);
    const hadFragment = !!fragment;
    fragment = '';match = null;
    const start = () => {
        const el = id => document.getElementById(id);
        const names = {diagnose:'只读诊断',nodes:'节点管理',share:'节点与订阅分享',relay:'中转配置',
            certificates:'证书管理',operate:'服务与备份',upgrade:'在线升级',destructive:'删除与恢复',core_read:'代理核心只读'};
        const actions = {status:'读取服务状态',start:'启动服务',stop:'停止服务',restart:'重启服务',backup:'私有备份',upgrade:'在线升级',restore:'停机恢复备份'};
        const phases = {queued:'排队中',running:'执行中',succeeded:'成功',success:'成功',failed:'失败',interrupted:'已中断',pending:'待执行',completed:'已完成'};
        let operationView = null;
        let token = '', admin = false, ended = false, epoch = 0, busy = false, scopes = [], issuedID = '', coreScopes = [], normalExpires = 0;
        const controllers = new Set();
        let taskTimer=null, taskSubmissionUncertain=false, watchingTask=null;
        let coreFacts=null, coreRows=[], coreWatching=null, coreTimer=null, coreUnknown=null, coreExpires=0, coreExpiryTimer=null;
        const clearCoreAuthority=message=>{
            clearTimeout(coreExpiryTimer);coreExpiryTimer=null;
            coreGrant='';coreScopes=[];coreExpires=0;
            const box=el('maintenance-core-authority');if(box)box.textContent=message;
            coreSync();
        };
        const scheduleCoreExpiry=()=>{
            clearTimeout(coreExpiryTimer);
            if(ended||!coreExpires)return;
            if(coreExpires*1000<=Date.now()){
                clearCoreAuthority('独立核心维护授权已到期，请从本机终端重新创建；仍可查询已有核心任务。');return;
            }
            coreExpiryTimer=setTimeout(scheduleCoreExpiry,Math.min(60000,coreExpires*1000-Date.now()));
        };
        const notice = value => { el('maintenance-notice').textContent = value; };
        const clearLink = () => { el('maintenance-link').value = ''; el('maintenance-issued').hidden = true; issuedID = ''; };
        const end = () => {
            clearTimeout(taskTimer);watchingTask=null;taskSubmissionUncertain=false;
            clearTimeout(coreTimer);coreWatching=null;coreUnknown=null;coreFacts=null;coreRows=[];coreExpires=0;
            clearTimeout(coreExpiryTimer);coreExpiryTimer=null;
            const work=el('maintenance-core-work');if(work)work.hidden=true;
            const retry=el('maintenance-core-retry');if(retry)retry.hidden=true;
            for(const id of ['maintenance-core-available','maintenance-core-progress','maintenance-core-pending','maintenance-core-task-list']){const node=el(id);if(node)node.textContent='';}
            for(const id of ['maintenance-core-backup-choice','maintenance-core-candidate-sing-box','maintenance-core-candidate-xray']){const node=el(id);if(node){node.textContent='';node.value='';}}
            const corePanel=el('maintenance-core-panel');if(corePanel)corePanel.hidden=true;
            const coreBox=el('maintenance-core-selection');if(coreBox)coreBox.textContent='';
            const hints=el('maintenance-core-hints');if(hints)hints.textContent='';
            const authorityBox=el('maintenance-core-authority');if(authorityBox)authorityBox.textContent='';
            epoch++; ended = true; operationView?.destroy();operationView=null;token = ''; grant = ''; coreGrant = ''; coreScopes = []; clearLink();
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
                if(coreExpires&&coreExpires*1000<=Date.now())scheduleCoreExpiry();
                if (coreGrant && !exchange && (path==='maintenance/core-authority'||path==='maintenance/core-tasks'&&method==='POST'||/^maintenance\/core-requests\/[a-f0-9]{32}$/.test(path))) headers['X-VUI-Core-Authorization'] = coreGrant;
                const response = await fetch('/api/' + path, {method, headers, credentials: token || exchange ? 'omit' : 'same-origin',
                    cache:'no-store', redirect:'error', signal:controller.signal, ...(data !== undefined ? {body:JSON.stringify(data)} : {})});
                const type=response.headers?.get('content-type')||'application/json';
                const value=type.includes('json')?await response.json():await response.text();
                if (captured !== epoch) throw new Error('请求已取消。');
                if (!response.ok) {
                    if (response.status === 401 && !exchange) { end(); notice('授权或登录已失效，请重新登录或获得维护授权。'); }
                    if(response.status===403&&headers['X-VUI-Core-Authorization'])clearCoreAuthority('独立核心维护授权已被拒绝，请从本机终端重新创建；仍可查询已有核心任务。');
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
            const coreButton=el('maintenance-core-refresh');if(coreButton)coreButton.disabled=busy||ended||!(admin||scopes.includes('core_read'));
            for(const core of ['sing-box','xray']){const button=el('maintenance-core-hints-'+core);if(button)button.disabled=busy||ended||!(admin||scopes.includes('core_read'));}
            el('maintenance-create').disabled = busy || ended;
            el('maintenance-restore').disabled = busy || ended || taskSubmissionUncertain || !!watchingTask || !(admin || scopes.includes('destructive'));
            el('maintenance-actions').querySelectorAll('button').forEach(button => { button.disabled = busy || ended || !allowed(button.dataset.task) || (taskSubmissionUncertain||!!watchingTask)&&button.dataset.task!=='status'; });
            coreSync();
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
                    watchingTask=null;sync();await tasks();notice((actions[row.action]||'维护任务')+'：'+(phases[state]||'已结束')+'。请按结果核对服务状态。');return;
                }
                notice('任务'+(phases[state]||'状态待确认')+'，正在自动读取进度；不要重复提交。');
            }catch(_){if(ended||epoch!==captured)return;notice('面板可能正在重启，正在重新读取已有任务；不会重复提交。');}
            if(attempt<59)taskTimer=setTimeout(()=>watchTask(id,attempt+1),5000);
            else{watchingTask=null;taskSubmissionUncertain=true;const ack=el('maintenance-task-ack');if(ack)ack.hidden=false;sync();notice('自动等待已结束，任务结果仍需核对。点击“刷新维护任务”，不要再次提交同一操作。');}
        };
        const submitTask=async body=>{
            if(taskSubmissionUncertain||watchingTask)return;
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
        const coreAuthority = async () => {
            const ownEpoch=epoch;
            if(ended)return;
            const box=el('maintenance-core-authority');
            coreScopes=[];
            if (!coreGrant) {if(box)box.textContent='本次仅查看核心信息。';return;}
            try {
                const value=await request('maintenance/core-authority');
                if(ended||epoch!==ownEpoch)return;
                const allowedScopes=['core_operate','core_backup','core_restore'];
                if (!value || !Array.isArray(value.scopes) || !value.scopes.length ||
                    value.scopes.some(scope=>!allowedScopes.includes(scope)) || new Set(value.scopes).size!==value.scopes.length ||
                    !Number.isSafeInteger(value.expires_at) || value.expires_at>normalExpires ||
                    value.expires_at*1000<=Date.now() || value.revoked!==false) throw new Error('核心授权响应无效。');
                coreScopes=value.scopes;
                coreExpires=value.expires_at;
                scheduleCoreExpiry();
                if(box)box.textContent='独立核心维护授权已核验 · 截止：'+time(value.expires_at);
            } catch(error) {
                if(ended||epoch!==ownEpoch)return;
                coreGrant='';coreScopes=[];
                if(box)box.textContent='核心维护授权未能确认，请从本机终端重新创建；普通维护权限保持。';
            }
        };
        const coreSelection = async () => {
            if (!(admin || scopes.includes('core_read'))) throw new Error('本次授权未开放代理核心信息。');
            const value = await request('maintenance/core-selection');
            if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('核心选择响应无效，请稍后刷新。');
            const box = el('maintenance-core-selection');
            if (!box) return;
            box.textContent = '';
            if (value.outcome === 'bundle_default') {
                text(box, '当前选择：随面板套件，未配置独立核心。');
            } else if (value.outcome === 'core_pending') {
                text(box, '有未完成的核心操作，请使用本机终端核对并恢复。');
            } else if (value.outcome === 'core_selected' && value.cores) {
                for (const name of ['sing-box', 'xray']) {
                    const row = value.cores[name];
                    if (!row || typeof row.current !== 'string' || !(row.previous === null || typeof row.previous === 'string')) {
                        throw new Error('核心选择响应无效，请稍后刷新。');
                    }
                    text(box, (name === 'xray' ? 'Xray' : 'sing-box') + ' · 当前选择：' + row.current + ' · 上一选择：' + (row.previous || '无'));
                }
            } else {
                throw new Error('核心选择响应无效，请稍后刷新。');
            }
        };
        const coreRefresh = el('maintenance-core-refresh');
        if (coreRefresh) coreRefresh.addEventListener('click', () => run(coreSelection));
        const coreHints = async core => {
            if (!(admin || scopes.includes('core_read'))) throw new Error('本次授权未开放代理核心信息。');
            const box = el('maintenance-core-hints');
            if (box) {box.textContent='';text(box,'正在读取官方候选…');}
            let value;
            try { value = await request('maintenance/core-candidates/' + core); }
            catch (error) {
                if (box) box.textContent='';
                if (error.name === 'AbortError') throw Object.assign(new Error('候选查询已超时或取消，核心选择未改变。'), {status:408});
                if (error.status === 503) throw Object.assign(new Error('暂时无法读取官方候选，请稍后重试；核心选择未改变。'), {status:503});
                throw error;
            }
            if (box) box.textContent = '';
            if (!value || value.core !== core || value.qualification !== 'unverified' ||
                !Array.isArray(value.hints) || value.hints.length > 32 || typeof value.truncated !== 'boolean') {
                throw new Error('官方候选响应无效。');
            }
            const rows = value.hints.map(row => {
                if (!row || typeof row.version !== 'string' || !Number.isSafeInteger(row.revision)) throw new Error('官方候选响应无效。');
                return (core === 'xray' ? 'Xray' : 'sing-box') + ' ' + row.version + ' · 修订 ' + row.revision + ' · 待验证';
            });
            if (!box) return;
            text(box, '仅为官方通道信息，尚未完成本机兼容验证；当前不会更新核心。');
            if (!value.hints.length) text(box, '当前官方通道暂无候选版本。');
            for (const row of rows) text(box, row);
            if (value.truncated) text(box, '最多展示32个候选，结果可能不完整。');
        };
        for (const core of ['sing-box','xray']) {
            const button = el('maintenance-core-hints-'+core);
            if (button) button.addEventListener('click', () => run(() => coreHints(core)));
        }
        // Core tasks have their own namespace, permissions and polling state.
        const coreNames={'sing-box':'sing-box',xray:'Xray'};
        const coreActions={observe:'核心检查',update:'核心更新',rollback:'回到上一版本',recover:'恢复未完成操作',backup:'核心与数据备份',restore:'核心与数据恢复'};
        const corePhases={queued:'等待执行',preflight:'核对应用与材料',planning:'检查候选与配置',changing:'备份并切换',verifying:'验证服务与流量',recovering:'恢复原状态',finished:'已结束'};
        const coreOutcomes={switched:'核心已更新并完成验证。',already_current:'当前已是所选版本。',restored_previous:'已恢复到原有版本。',resumed_confirmed:'已完成已确认操作的收尾。',restored_backup:'核心与数据已恢复，请重新登录并创建维护授权。',backup_created:'核心与数据备份已创建。',backup_recovered:'备份操作已中止，原服务状态已恢复；未重新创建备份。'};
        const exact=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
        const coreHex=(value,length)=>typeof value==='string'&&new RegExp('^[a-f0-9]{'+length+'}$').test(value);
        const coreVersion=value=>typeof value==='string'&&value.length<=64&&/^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$/.test(value);
        const coreInteger=value=>Number.isSafeInteger(value)&&value>=0;
        const coreProgress=value=>{const node=el('maintenance-core-progress');if(node)node.textContent=value;};
        const corePermit=scope=>!!coreGrant&&coreScopes.includes(scope)&&coreExpires*1000>Date.now();
        function coreSync(){
            const read=!ended&&scopes.includes('core_read');
            const locked=busy||!read||!!coreWatching||!!coreUnknown;
            for(const id of ['maintenance-core-check','maintenance-core-task-refresh']){const button=el(id);if(button)button.disabled=busy||!read||(id==='maintenance-core-check'&&(!!coreWatching||!!coreUnknown));}
            const pending=coreFacts?.pending;
            for(const core of ['sing-box','xray']){
                const candidates=coreFacts?.cores[core]?.candidates||[];
                const choice=el('maintenance-core-candidate-'+core);
                const update=el('maintenance-core-update-'+core),rollback=el('maintenance-core-rollback-'+core);
                if(choice)choice.disabled=locked||!!pending||!corePermit('core_operate');
                if(update)update.disabled=locked||!!pending||!corePermit('core_operate')||!candidates.some(row=>String(row.release_id)===choice?.value);
                if(rollback)rollback.disabled=locked||!!pending||!corePermit('core_operate')||!coreFacts?.selection_revision||!coreFacts?.cores[core]?.previous;
            }
            const backup=el('maintenance-core-backup'),restore=el('maintenance-core-restore'),recover=el('maintenance-core-recover'),choice=el('maintenance-core-backup-choice');
            if(backup)backup.disabled=locked||!!pending||!coreFacts?.selection_revision||!corePermit('core_backup');
            if(choice)choice.disabled=locked||!!pending||!corePermit('core_restore');
            if(restore)restore.disabled=locked||!!pending||!coreFacts?.selection_revision||!corePermit('core_restore')||!coreBackups().some(row=>row.id===choice?.value);
            if(recover)recover.disabled=locked||!pending||!corePermit(pending.recovery_kind==='restore'?'core_restore':pending.recovery_kind==='backup'?'core_backup':'core_operate');
            const retry=el('maintenance-core-retry');if(retry){retry.hidden=!coreUnknown?.notFound;retry.disabled=busy||!read||!coreGrant;}
        }
        const checkedFacts=value=>{
            const keys=['outcome','app_source','target','selection_revision','generation','cores','pending','backups'];
            if(value&&Object.hasOwn(value,'candidate_error'))keys.push('candidate_error');
            if(!exact(value,keys)||value.outcome!=='observed'||
                !(coreHex(value.app_source,40)||value.app_source===null&&value.pending!==null)||!['x86_64-gnu','x86_64-musl','aarch64-gnu','aarch64-musl'].includes(value.target)||
                !(value.selection_revision===null||coreHex(value.selection_revision,64))||!coreInteger(value.generation)||!exact(value.cores,['sing-box','xray'])||
                !Array.isArray(value.backups)||value.backups.length>256||('candidate_error' in value&&typeof value.candidate_error!=='string'))throw new Error('核心检查响应无效');
            for(const core of ['sing-box','xray']){
                const row=value.cores[core];
                if(!exact(row,['current','previous','candidates'])||!(coreVersion(row.current)||value.pending!==null&&row.current==='待恢复')||!(row.previous===null||coreVersion(row.previous))||!Array.isArray(row.candidates)||row.candidates.length>32||value.pending!==null&&row.candidates.length)throw new Error('核心检查响应无效');
                const ids=new Set();
                for(const candidate of row.candidates){if(!exact(candidate,['release_id','version'])||!Number.isSafeInteger(candidate.release_id)||candidate.release_id<=0||!coreVersion(candidate.version)||ids.has(candidate.release_id))throw new Error('核心候选响应无效');ids.add(candidate.release_id);}
            }
            if(value.pending!==null&&(!exact(value.pending,['recovery_kind','transaction','sequence','selection_revision'])||!['switch','restore','backup'].includes(value.pending.recovery_kind)||
                !coreHex(value.pending.transaction,32)||!coreInteger(value.pending.sequence)||!(value.pending.selection_revision===null||coreHex(value.pending.selection_revision,64))))throw new Error('核心恢复响应无效');
            const ids=new Set();for(const row of value.backups){if(!exact(row,['id','created_at'])||!coreHex(row.id,32)||!coreInteger(row.created_at)||ids.has(row.id))throw new Error('核心备份响应无效');ids.add(row.id);}
            return value;
        };
        const checkedCoreTask=value=>{
            const fields=['id','action','state','phase','created_at','updated_at'];
            if(!value||typeof value!=='object'||Array.isArray(value)||fields.some(key=>!Object.hasOwn(value,key))||Object.keys(value).some(key=>![...fields,'result','error'].includes(key))||
                !coreHex(value.id,32)||!Object.hasOwn(coreActions,value.action)||!['queued','running','succeeded','failed'].includes(value.state)||!Object.hasOwn(corePhases,value.phase)||
                !coreInteger(value.created_at)||!coreInteger(value.updated_at))throw new Error('核心任务响应无效');
            if(value.result!==undefined){
                if(value.action==='observe'){if(value.state==='succeeded')checkedFacts(value.result);else throw new Error('核心检查响应无效');}
                else{
                    const result=value.result,allowed=['outcome','core','version','previous_version','selection_revision','pending','backup_id','panel_running'];
                    if(!result||typeof result!=='object'||Array.isArray(result)||typeof result.outcome!=='string'||!Object.hasOwn(coreOutcomes,result.outcome)||
                        Object.keys(result).some(key=>!allowed.includes(key))||('core' in result&&!Object.hasOwn(coreNames,result.core))||('version' in result&&!coreVersion(result.version))||
                        ('previous_version' in result&&result.previous_version!==null&&!coreVersion(result.previous_version))||('selection_revision' in result&&!coreHex(result.selection_revision,64))||
                        ('pending' in result&&typeof result.pending!=='boolean')||('backup_id' in result&&!coreHex(result.backup_id,32))||('panel_running' in result&&typeof result.panel_running!=='boolean'))throw new Error('核心任务结果无效');
                }
            }
            if(value.error!==undefined&&(typeof value.error!=='string'||!/^[A-Z0-9_]{1,80}$/.test(value.error)))throw new Error('核心任务结果无效');
            return value;
        };
        function coreBackups(){
            const values=new Map((coreFacts?.backups||[]).map(row=>[row.id,row]));
            for(const row of coreRows)if(row.action==='backup'&&row.state==='succeeded'&&row.result?.outcome==='backup_created'&&row.result.backup_id===row.id)values.set(row.id,{id:row.id,created_at:row.created_at});
            return [...values.values()].sort((a,b)=>b.created_at-a.created_at);
        }
        const renderCoreBackups=()=>{
            const choice=el('maintenance-core-backup-choice');if(!choice)return;
            const selected=choice.value;choice.textContent='';
            const empty=document.createElement('option');empty.value='';empty.textContent='选择一个成功完成的核心与数据备份';choice.append(empty);
            const rows=coreBackups();for(const row of rows){const option=document.createElement('option');option.value=row.id;option.textContent=time(row.created_at)+' · 核心与数据备份';choice.append(option);}
            choice.value=rows.some(row=>row.id===selected)?selected:'';
        };
        const renderCoreFacts=value=>{
            coreFacts=checkedFacts(value);
            const box=el('maintenance-core-available');box.textContent='';
            for(const core of ['sing-box','xray']){
                const row=coreFacts.cores[core];text(box,coreNames[core]+' · 当前选择：'+row.current+' · 上一版本：'+(row.previous||'无'));
                const choice=el('maintenance-core-candidate-'+core);choice.textContent='';
                const empty=document.createElement('option');empty.value='';empty.textContent=row.candidates.length?'选择要更新的官方版本':'暂无适配当前应用的官方候选';choice.append(empty);
                for(const candidate of row.candidates){const option=document.createElement('option');option.value=String(candidate.release_id);option.textContent=candidate.version;choice.append(option);}
                choice.value='';
            }
            el('maintenance-core-pending').textContent=coreFacts.pending?'检测到未完成操作。请先恢复当前操作，恢复前不能发起其他核心变更。':'当前没有待恢复的核心操作。';
            if(coreFacts.candidate_error)text(box,'官方候选查询未完成，请稍后重新检查。当前空列表不表示没有可用更新。');
            renderCoreBackups();coreSync();
        };
        const renderCoreRows=()=>{
            const box=el('maintenance-core-task-list');box.textContent='';
            if(!coreRows.length)text(box,'暂无核心维护任务。');
            for(const row of coreRows){const entry=record(box);text(entry,time(row.created_at)+' · '+coreActions[row.action]+' · '+({queued:'排队中',running:'执行中',succeeded:'已完成',failed:'未完成'}[row.state])+' · '+corePhases[row.phase]);
                if(row.result?.version)text(entry,'目标版本：'+row.result.version);
                if(coreOutcomes[row.result?.outcome])text(entry,coreOutcomes[row.result.outcome]);
                if(row.result?.panel_running===false)text(entry,'面板服务保持停止。需要启动时请从本机终端明确启动。');
                if(row.result?.pending||row.state==='failed')text(entry,'请检查当前状态；有未完成操作时先恢复，不要重复发起变更。');}
            renderCoreBackups();coreSync();
        };
        const readCoreRows=async()=>{
            const value=await request('maintenance/core-tasks');
            if(!Array.isArray(value)||value.length>1024)throw new Error('核心任务列表无效');
            const rows=value.map(checkedCoreTask);if(new Set(rows.map(row=>row.id)).size!==rows.length)throw new Error('核心任务列表无效');
            coreRows=rows;renderCoreRows();return rows;
        };
        const coreCompleted=row=>{
            if(row.action==='observe'&&row.state==='succeeded'){renderCoreFacts(row.result);coreProgress(row.result.pending?'检查到未完成操作，请先恢复当前操作。':row.result.candidate_error?'检查已完成，但候选查询未完成，请稍后重试。':'检查完成。可从匹配当前应用的官方候选中选择版本。');}
            else{coreFacts=null;el('maintenance-core-available').textContent='操作已结束，请重新检查以读取当前实际选择。';coreProgress(row.state==='succeeded'?(coreOutcomes[row.result?.outcome]||'操作已完成，请重新检查核心状态。')+(row.result?.panel_running===false?'面板服务保持停止。':''):'操作未完成。请重新检查，若显示待恢复请先恢复当前操作。');}
            coreSync();
        };
        const watchCore=async(id,attempt=0)=>{
            if(ended||coreWatching!==id)return;
            const captured=epoch;
            try{
                const row=checkedCoreTask(await request('maintenance/core-tasks/'+id));
                if(row.id!==id)throw new Error('核心任务身份不符');
                if(ended||epoch!==captured||coreWatching!==id)return;
                coreRows=[row,...coreRows.filter(value=>value.id!==id)];renderCoreRows();
                if(['succeeded','failed'].includes(row.state)){coreWatching=null;coreCompleted(row);return;}
                coreProgress(coreActions[row.action]+'：'+corePhases[row.phase]+'。服务短暂停止时，本页会继续读取同一个任务。');
            }catch(_){if(ended||epoch!==captured)return;coreProgress('正在重新连接并查询原核心任务，不会重复提交。恢复数据后如授权已撤销，请重新获取维护链接。');}
            if(attempt<179)coreTimer=setTimeout(()=>watchCore(id,attempt+1),5000);
            else coreProgress('自动等待已暂停。点击“刷新核心任务与进度”继续查询原任务，不要重复提交。');
        };
        const trackCore=row=>{
            row=checkedCoreTask(row);coreRows=[row,...coreRows.filter(value=>value.id!==row.id)];renderCoreRows();
            if(['succeeded','failed'].includes(row.state)){coreWatching=null;coreCompleted(row);}
            else{coreWatching=row.id;clearTimeout(coreTimer);coreTimer=setTimeout(()=>watchCore(row.id),1000);coreProgress('核心任务已登记，正在核对并读取进度。');}
            coreSync();
        };
        const refreshCore=async()=>{
            if(coreUnknown&&coreGrant){
                const own=coreUnknown;
                const value=await request('maintenance/core-requests/'+own.nonce);
                if(ended||coreUnknown!==own)return;
                if(exact(value,['not_found'])&&value.not_found===true){own.notFound=true;coreSync();coreProgress('尚未找到原操作记录。可点击“重新确认登记原操作”，只确认同一个请求，不会创建另一项操作。');}
                else{const row=checkedCoreTask(value);coreUnknown=null;trackCore(row);}
                return;
            }
            const rows=await readCoreRows();
            if(coreWatching){clearTimeout(coreTimer);await watchCore(coreWatching);return;}
            const active=rows.find(row=>['queued','running'].includes(row.state));
            if(active){coreWatching=active.id;await watchCore(active.id);}
            // Historical observations are displayed as records, never reused
            // as fresh selection/CAS input after a later change.
        };
        const checkCore=async()=>{
            coreFacts=null;el('maintenance-core-available').textContent='';coreSync();
            coreProgress('正在检查实际应用与官方候选…');
            trackCore(await request('maintenance/core-observations','POST',{}));
        };
        const dispatchCore=async intent=>{
            coreUnknown=intent;intent.notFound=false;coreSync();
            try{const row=await request('maintenance/core-tasks','POST',{action:intent.action,parameters:intent.parameters},false,{'Idempotency-Key':intent.nonce});const checked=checkedCoreTask(row);if(checked.action!==intent.action)throw new Error('核心任务动作不符');coreUnknown=null;trackCore(checked);coreSync();}
            catch(error){if([400,403,409,422,429].includes(error.status)){coreUnknown=null;coreSync();}else coreProgress('提交结果尚未确认，已保留原请求。请刷新原任务记录，不要重复发起操作。');throw error;}
        };
        const submitCore=async(action,parameters,message)=>{
            if(coreUnknown||coreWatching||ended)return;
            const needed=action==='restore'||action==='recover'&&parameters.recovery_kind==='restore'?'core_restore':action==='backup'||action==='recover'&&parameters.recovery_kind==='backup'?'core_backup':'core_operate';
            if(!corePermit(needed)){coreProgress('本次链接没有该项独立核心维护权限，请从服务器终端重新创建。');return;}
            if(!confirm(message))return;
            const bytes=new Uint8Array(16);globalThis.crypto.getRandomValues(bytes);
            const nonce=[...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
            await dispatchCore({nonce,action,parameters});
        };
        el('maintenance-core-retry')?.addEventListener('click',()=>run(async()=>{
            if(!coreUnknown?.notFound||!confirm('确认重新核对并登记原操作？将保留同一请求和目标；若服务器已经登记，只会返回原任务，不会重复执行。'))return;
            await dispatchCore(coreUnknown);
        }));
        el('maintenance-core-check')?.addEventListener('click',()=>run(checkCore));
        el('maintenance-core-task-refresh')?.addEventListener('click',()=>run(refreshCore));
        for(const core of ['sing-box','xray']){
            el('maintenance-core-candidate-'+core)?.addEventListener('change',coreSync);
            el('maintenance-core-update-'+core)?.addEventListener('click',()=>run(async()=>{
                if(!coreFacts||coreFacts.pending)return;
                const candidate=coreFacts.cores[core].candidates.find(row=>String(row.release_id)===el('maintenance-core-candidate-'+core).value);if(!candidate)return;
                const notice=core==='xray'&&candidate.version==='26.10.21'
                    ? '\n\nXray 26.10.21 默认拒绝通过节点访问部分私网和保留地址，包括服务器回环地址。若依赖这些地址，请保留当前版本。' : '';
                await submitCore('update',{core,release_id:candidate.release_id,selection_revision:coreFacts.selection_revision},'确认将 '+coreNames[core]+' 从 '+coreFacts.cores[core].current+' 更新到 '+candidate.version+'？将先校验配置和备份，再短暂停止服务，验证失败时恢复原版本。'+notice);
            }));
            el('maintenance-core-rollback-'+core)?.addEventListener('click',()=>run(async()=>{
                if(!coreFacts||coreFacts.pending||!coreFacts.selection_revision||!coreFacts.cores[core].previous)return;
                await submitCore('rollback',{core,selection_revision:coreFacts.selection_revision},'确认将 '+coreNames[core]+' 回到上一版本 '+coreFacts.cores[core].previous+'？将先备份并短暂停止服务。');
            }));
        }
        el('maintenance-core-backup-choice')?.addEventListener('change',coreSync);
        el('maintenance-core-backup')?.addEventListener('click',()=>run(async()=>{
            if(coreFacts&&!coreFacts.pending&&coreFacts.selection_revision)await submitCore('backup',{selection_revision:coreFacts.selection_revision},'确认创建核心与数据备份？将短暂停止服务以取得一致的备份，原本停止的服务保持停止。');
        }));
        el('maintenance-core-restore')?.addEventListener('click',()=>run(async()=>{
            const selected=coreBackups().find(row=>row.id===el('maintenance-core-backup-choice').value);
            if(coreFacts&&!coreFacts.pending&&coreFacts.selection_revision&&selected)await submitCore('restore',{backup_task_id:selected.id,selection_revision:coreFacts.selection_revision},'确认恢复 '+time(selected.created_at)+' 的核心与数据备份？这会替换当前业务数据，并撤销旧登录、维护与订阅授权。原数据按恢复流程保留，完成后需要重新登录和授权。');
        }));
        el('maintenance-core-recover')?.addEventListener('click',()=>run(async()=>{
            const value=coreFacts?.pending;if(!value)return;
            await submitCore('recover',{recovery_kind:value.recovery_kind,pending_transaction:value.transaction,pending_sequence:value.sequence,selection_revision:value.selection_revision},'确认恢复当前未完成的操作？只处理已记录的这一项事务，不会猜测其他备份或继续更新到新候选。');
        }));
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
            if(action==='status'){await installation();notice('已读取实际安装版本与服务状态。');return;}
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
                token = result.token; scopes = result.scopes; normalExpires = result.expires_at;
                if (typeof token !== 'string' || !/^vui_ms_[A-Za-z0-9_-]{43}$/.test(token) || !Array.isArray(scopes)) { end(); notice('维护授权响应无效，请重新获取授权。'); return; }
                el('maintenance-remote').hidden = false;
                el('maintenance-session').textContent = '截止：' + time(result.expires_at) + ' · 权限：' + scopes.map(scope => names[scope] || '未知权限').join('、');
                notice('已建立临时维护会话，原一次性链接已失效。');
            } else {
                await request('auth/me'); admin = true; el('maintenance-admin').hidden = false; await grants(); notice('已验证管理员身份。默认只读，请按需选择维护权限。');
            }
            if(coreGrant)await coreAuthority();
            if(ended)return;
            const coreWork=el('maintenance-core-work');if(coreWork)coreWork.hidden=!scopes.includes('core_read');
            const coreOnly = !admin && scopes.includes('core_read') && scopes.every(scope => scope === 'core_read' || scope === 'diagnose');
            const corePanel=el('maintenance-core-panel');if(corePanel)corePanel.hidden=!(admin||scopes.includes('core_read'));
            if(!coreOnly&&globalThis.VUI_SIMPLE_OPERATIONS&&el('maintenance-operations')){
                el('maintenance-operations').hidden=false;
                operationView=globalThis.VUI_SIMPLE_OPERATIONS.mount(el('maintenance-operations'),{
                    request:(path,method,body,{headers={},fullResponse=false}={})=>request(path.replace(/^\/api\//,''),method,body===null?undefined:body,false,headers,fullResponse),
                    scopes:admin?null:scopes,remote:!admin,expired:()=>{end();notice('维护授权已失效，请重新获取授权。');}
                });
            }
            el('maintenance-admin-links').hidden=!admin;
            if(!admin){document.body?.classList?.add('vui-delegated');const brand=document.querySelector?.('.vui-shell-brand');if(brand)brand.href='#maintenance-remote';}
            el('maintenance-tasks-panel').hidden = coreOnly; el('maintenance-audit-panel').hidden = coreOnly || !(admin || scopes.includes('diagnose'));
            el('maintenance-logs-panel').hidden = coreOnly || !(admin || scopes.includes('diagnose'));
            if (!coreOnly && (admin || scopes.includes('diagnose'))) await audit();
        });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true}); else start();
})();
