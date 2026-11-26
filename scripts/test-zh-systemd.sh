#!/usr/bin/env bash
# 仅供一次性、空白的 GitHub-hosted Ubuntu runner；每个模式使用一个全新 job。
# 明确触发示例（本脚本不创建或修改 workflow）：
# sudo --preserve-env=GITHUB_ACTIONS,RUNNER_ENVIRONMENT,RUNNER_OS,ImageOS,RUNNER_TEMP,GITHUB_RUN_ID \
#   env XUI_CI_SYSTEMD_TEST=DISPOSABLE_GITHUB_RUNNER \
#   bash scripts/test-zh-systemd.sh migrate /path/中文完整包.zip /path/官方完整包.tar.gz 2.9.2
# 模式：fresh、migrate、rollback。两个归档须事先完成来源和摘要核对。
# 私密工作目录含一次性凭据、认证快照和原始程序日志，禁止上传该目录或打印日志。
# 不修改 SSH、不调用防火墙工具、不申请公网证书，也不把测试扩展到现有机器。
set +x
set +v
set -euo pipefail
umask 077

fail() { printf '测试失败：%s\n' "$1" >&2; exit 1; }
pass() { printf '通过：%s\n' "$1"; }
[[ $# == 3 || $# == 4 ]] || fail '用法：test-zh-systemd.sh fresh|migrate|rollback 中文完整包 官方完整包 [3.11.0|2.9.2]'
mode=$1
official_version=${4:-3.11.0}
case $official_version in 3.11.0|2.9.2) ;; *) fail '官方版本必须是 3.11.0 或 2.9.2（不带 v）。' ;; esac
case $mode in fresh|migrate|rollback) ;; *) fail '模式必须是 fresh、migrate 或 rollback。' ;; esac
[[ $mode != fresh || $official_version == 3.11.0 ]] || fail 'fresh 模式固定使用 v3.11.0 官方包。'
[[ ${XUI_CI_SYSTEMD_TEST:-} == DISPOSABLE_GITHUB_RUNNER ]] || fail '缺少一次性 runner 的明确执行确认。'
[[ $EUID == 0 && ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted ]] || fail '只允许 root 在 GitHub-hosted Actions 中执行。'
[[ ${RUNNER_OS:-} == Linux && ${ImageOS:-} == ubuntu* && ${GITHUB_RUN_ID:-} =~ ^[0-9]+$ ]] || fail '只允许 GitHub Ubuntu runner。'
[[ -d /opt/hostedtoolcache && -d /run/systemd/system ]] || fail '缺少托管 runner 或运行中的 systemd 标记。'
[[ $(uname -m) == x86_64 ]] || fail '安装包集成测试仅支持 x86_64。'
source /etc/os-release
[[ $ID == ubuntu ]] || fail '实际操作系统不是 Ubuntu。'
[[ -n ${RUNNER_TEMP:-} && $RUNNER_TEMP == /* && -d $RUNNER_TEMP ]] || fail 'RUNNER_TEMP 必须是已有绝对目录。'
for program in bash python3 openssl systemctl sha256sum install cp find sort cmp runuser setpriv ss realpath cut xargs readlink; do
    command -v "$program" >/dev/null || fail 'runner 缺少测试必需的系统工具。'
done
for name in $(compgen -e); do
    [[ $name != XUI_* || $name == XUI_CI_SYSTEMD_TEST ]] || fail 'runner 已设置其他 XUI 环境变量，拒绝混入已有配置。'
done
[[ -f $2 && -f $3 ]] || fail '两个参数都必须是事先核对的本地归档文件。'
zh_archive=$(realpath -- "$2")
official_archive=$(realpath -- "$3")
case $official_version in
    3.11.0) official_sha256=0ddfa4702f595442e2962acd81addc768fdacaa4ff2672c971a0463dae744916 ;;
    2.9.2) official_sha256=ca93c552ea4ddb8d0940932f79d0c0f1ad864f93f1f49bce34b9c9a348936693 ;;
esac
[[ $(sha256sum "$official_archive" | cut -d' ' -f1) == "$official_sha256" ]] || fail '官方归档摘要与对应版本的已核对 amd64 资产不符。'
# 只接受空机；绝不清理或覆盖原数据来满足前置条件。
for path in /usr/local/x-ui /etc/x-ui /var/log/x-ui /usr/local/bin/x-ui /usr/bin/x-ui \
    /etc/default/x-ui /etc/conf.d/x-ui /etc/sysconfig/x-ui /etc/x-ui-managed-certs \
    /etc/systemd/system/x-ui.service /etc/systemd/system/x-ui.service.d \
    /etc/systemd/system/x-ui-zh-update.service /etc/systemd/system/x-ui-zh-update.path \
    /var/lib/x-ui-zh-updates /var/backups/x-ui-zh-lite /etc/x-ui-ci-fixture \
    /usr/local/lib/x-ui-ci-fixture /run/x-ui-ci-candidate-failed; do
    [[ ! -e $path && ! -L $path ]] || fail '发现已有 x-ui 数据、服务、命令或测试残留，请改用新的空白 job。'
done
[[ $(systemctl show x-ui.service --property=LoadState --value) == not-found ]] || fail '系统中已有 x-ui 服务。'
! id x-ui-zh >/dev/null 2>&1 || fail '已有中文服务账号，请改用新的空白 job。'
[[ -z $(find /usr/local -maxdepth 1 -type d -name '.x-ui-migrate.*' -print -quit) ]] || fail '存在旧迁移备份，请改用新的空白 job。'
for test_port in 20531 20771 20772 20773 62789; do
    [[ -z $(ss -H -ltn "sport = :$test_port") ]] || fail '测试端口已被占用。'
done

work=$(mktemp -d "$RUNNER_TEMP/x-ui-systemd-ci.XXXXXXXX")
chmod 0700 "$work"
stage='准备归档'
service_created=0
safe_diagnostic() {
    python3 -I - "$work" "$stage" <<'PY'
import pathlib,re,sys
root=pathlib.Path(sys.argv[1]); stage=sys.argv[2]
path=root/(stage+'.log')
if not path.exists(): sys.exit(0)
text=path.read_text(errors='replace')
allowed=set()
for name in ('fixture.py','extract.py'):
    source=root/name
    if source.exists(): allowed.update(re.findall(r"raise (?:RuntimeError|ValueError)\('([^']+)'\)",source.read_text()))
for line in reversed(text.splitlines()):
    match=re.fullmatch(r'(?:RuntimeError|ValueError): (.+)',line)
    if match and match[1] in allowed:
        print('无凭据诊断：'+match[1]); sys.exit(0)
categories=re.findall(r'(?m)^(?:[A-Za-z_]+\.)?(HTTPError|SSLCertVerificationError|ConnectionRefusedError|TimeoutError|OperationalError|IntegrityError|PermissionError|KeyError|TypeError|ValueError|RuntimeError):',text)
reason=categories[-1] if categories else '操作返回非零；原始输出未公开'
for marker,label in [('schema-check.log','候选数据库副本预演未通过'),('迁移预检失败','原安装预检未通过'),('安装包文件校验失败','候选包校验未通过'),('自动恢复未完全成功','自动恢复未完全成功，需要保留原始备份')]:
    if marker in text: reason=label
print('无凭据诊断：'+reason)
PY
}
finish() {
    local result=$?
    trap - EXIT
    if (( result )); then
        printf '未通过阶段：%s。原始日志仅保存在 runner 的 root 私密临时目录，不输出凭据或自动上传。\n' "$stage" >&2
        safe_diagnostic >&2 || true
        local state
        state=$(systemctl show x-ui.service --property=ActiveState --value 2>/dev/null) || state=unknown
        case $state in active|inactive|activating|deactivating|failed|unknown) printf '失败时服务状态：%s\n' "$state" >&2 ;; esac
    fi
    if (( service_created )); then
        systemctl stop x-ui.service >"$work/cleanup.log" 2>&1 || true
        if systemctl cat x-ui-zh-update.path >/dev/null 2>&1; then
            systemctl stop x-ui-zh-update.path >>"$work/cleanup.log" 2>&1 || true
        fi
    fi
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
private() {
    stage=$1
    shift
    "$@" >"$work/$stage.log" 2>&1
}

# 解压时拒绝路径穿越、链接、设备、重复文件及特殊权限；不执行归档内安装器。
cat >"$work/extract.py" <<'PY'
import os, pathlib, shutil, stat, sys, tarfile, zipfile
archive, target = sys.argv[1:]
root = pathlib.Path(target)
root.mkdir(mode=0o700)
seen = set()
def destination(name, directory):
    p = pathlib.PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name:
        raise ValueError('归档路径不安全')
    out = root.joinpath(*p.parts)
    if out == root:
        if not directory: raise ValueError('归档根路径不是目录')
        return out
    if out in seen and not directory: raise ValueError('归档存在重复文件')
    seen.add(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if directory: out.mkdir(exist_ok=True)
    return out
if zipfile.is_zipfile(archive):
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode): raise ValueError('拒绝归档链接')
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR): raise ValueError('拒绝归档特殊文件')
            out = destination(item.filename, item.is_dir())
            if not item.is_dir():
                with z.open(item) as src, out.open('xb') as dst: shutil.copyfileobj(src, dst)
                out.chmod((mode & 0o777) or 0o644)
else:
    with tarfile.open(archive) as t:
        for item in t:
            if not (item.isfile() or item.isdir()): raise ValueError('拒绝归档链接或特殊文件')
            out = destination(item.name, item.isdir())
            if item.isfile():
                with t.extractfile(item) as src, out.open('xb') as dst: shutil.copyfileobj(src, dst)
                out.chmod(item.mode & 0o777)
PY
private '解压中文包' python3 "$work/extract.py" "$zh_archive" "$work/zh"
private '解压官方包' python3 "$work/extract.py" "$official_archive" "$work/official"
mapfile -t packages < <(find "$work/zh" -name VERSION -type f -printf '%h\n')
[[ ${#packages[@]} == 1 ]] || fail '中文归档必须只包含一个完整安装包。'
package=${packages[0]}
[[ -f $package/install.sh && -f $package/SHA256SUMS && -f $package/scripts/zh-migrate.sh && -f $package/x-ui/x-ui ]] || fail '中文归档缺少完整安装或迁移文件。'
private '校验中文包' bash -c 'cd -- "$1"; sha256sum --check --strict --quiet SHA256SUMS' _ "$package"
version=$(<"$package/VERSION")
[[ $version =~ ^v3\.11\.0-zh\.[1-9][0-9]*$ ]] || fail '中文包版本标记不符合目标版系。'
# 使用对应官方归档中的实际 unit；不自行拼造原版服务文件。
official=$work/official/x-ui
official_unit=$official/x-ui.service.debian
[[ -f $official/x-ui && -f $official/x-ui.sh && -f $official_unit && -f $official/bin/xray-linux-amd64 ]] || fail '官方归档布局不符合对应版本的 Linux amd64 完整包。'
chmod 0755 "$official/x-ui" "$package/x-ui/x-ui"
private '读取官方版本' "$official/x-ui" -v
private '读取中文版本' "$package/x-ui/x-ui" -v
[[ $(<"$work/读取官方版本.log") == "$official_version" || $(<"$work/读取官方版本.log") == "v$official_version" ]] || fail '官方程序实际版本与指定版本不一致。'
[[ $(<"$work/读取中文版本.log") == "${version#v}" || $(<"$work/读取中文版本.log") == "$version" ]] || fail '中文程序与 VERSION 不一致。'
pass '归档校验及两个实际程序版本'
printf '%s\n' "$official_version" >"$work/official-version"

# Python 只向 root 私密目录写认证资料和诊断；不将原始响应写到 Actions 控制台。
cat >"$work/fixture.py" <<'PY'
import http.cookiejar, http.server, json, pathlib, re, socket, sqlite3, ssl, struct, subprocess, sys, threading, time, urllib.parse, urllib.request
work = pathlib.Path(sys.argv[1]); action = sys.argv[2]
authfile = work/'auth.json'; dbfile = pathlib.Path('/etc/x-ui/x-ui.db')
family = (work/'official-version').read_text().strip()
legacy = family == '2.9.2' and not pathlib.Path('/usr/local/x-ui/.zh-lite-migration').exists()
static_settings = ('webListen','webPort','webBasePath','webCertFile','webKeyFile','subEnable')
client = {'id':'cc9bf9eb-1a65-46d8-b190-76dd87e3bdb3','email':'fixture@example.invalid','enable':True,'flow':'','limitIp':0,'totalGB':1073741824,'expiryTime':4102444800000,'subId':'ci-only-subscription','reset':0,'tgId':0}
def save(name,obj): (work/name).write_text(json.dumps(obj,ensure_ascii=True,sort_keys=True))
def settings(db): return dict(db.execute('SELECT key,value FROM settings'))
def table_exists(db,name): return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None
def meta(): return json.loads((work/'fixture-meta.json').read_text())
def client_semantics(row):
    return {'id':row.get('id',row.get('uuid','')),'email':row.get('email',''),'enable':bool(row.get('enable',False)),'flow':row.get('flow',''),'limitIp':int(row.get('limitIp',row.get('limit_ip',0)) or 0),'totalGB':int(row.get('totalGB',row.get('total_gb',0)) or 0),'expiryTime':int(row.get('expiryTime',row.get('expiry_time',0)) or 0),'subId':row.get('subId',row.get('sub_id','')),'reset':int(row.get('reset',0) or 0),'tgId':int(row.get('tgId',row.get('tg_id',0)) or 0)}
def inbound_semantics(row):
    raw = row.get('settings',{}); raw = json.loads(raw) if isinstance(raw,str) else raw
    stream = row.get('streamSettings',row.get('stream_settings','{}')); stream = json.loads(stream) if isinstance(stream,str) else stream
    result = {k:row[k] for k in ('id','remark','enable','listen','port','protocol','total')}
    result['expiryTime'] = int(row.get('expiryTime',row.get('expiry_time',0)) or 0)
    result['transport'] = {'network':stream.get('network','tcp'),'security':stream.get('security','none'),'decryption':raw.get('decryption','none')}
    result['clients'] = sorted([client_semantics(c) for c in raw.get('clients',[])],key=lambda c:c['email'])
    return result

def api_session():
    auth = json.loads(authfile.read_text())
    base = ('https' if auth['tls'] else 'http')+'://127.0.0.1:'+str(auth['port'])+auth['path'].rstrip('/')+'/'
    context = ssl.create_default_context(cafile=str(work/'ca.pem')) if auth['tls'] else ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),urllib.request.HTTPSHandler(context=context))
    csrf = None
    def request(path,data=None):
        headers = {'Accept':'application/json'}
        if csrf: headers['X-CSRF-Token'] = csrf
        if data is not None:
            data = urllib.parse.urlencode(data).encode(); headers['Content-Type'] = 'application/x-www-form-urlencoded'
        with opener.open(urllib.request.Request(base+path,data=data,headers=headers),timeout=5) as response:
            obj = json.load(response)
        if not isinstance(obj,dict) or obj.get('success') is not True: raise RuntimeError('真实 API 未返回成功')
        return obj.get('obj')
    for attempt in range(45):
        try:
            if legacy:
                with opener.open(base,timeout=5) as response: response.read(1024)
            else:
                csrf = request('csrf-token')
                if not isinstance(csrf,str) or not csrf: raise RuntimeError('缺少新版 CSRF 令牌')
            break
        except Exception:
            if attempt == 44: raise RuntimeError('真实面板在等待期限内未就绪') from None
            time.sleep(1)
    request('login',{'username':auth['username'],'password':auth['password']})
    return request

if action == 'auth':
    with sqlite3.connect(dbfile) as db: s = settings(db)
    if sys.argv[3] == 'fresh':
        text = (work/'中文全新安装.log').read_text()
        u = re.search(r'^账号：([^\r\n]+)$',text,re.M); p = re.search(r'^密码：([^\r\n]+)$',text,re.M)
        if not u or not p: raise RuntimeError('无法读取一次性安装认证信息')
        username,password = u[1],p[1]
    else: username,password = 'ci-fixture-admin','CI-only-fake-password-7c4b52'
    save('auth.json',{'username':username,'password':password,'port':int(s['webPort']),'path':s['webBasePath'],'tls':False})
elif action == 'seed-static':
    with sqlite3.connect(dbfile) as db:
        # 专用环回测试模板：保留真实 API/统计服务，只允许本地客户端到本地 HTTP fixture。
        api_inbound = {'listen':'127.0.0.1','port':62789,'protocol':'dokodemo-door' if legacy else 'tunnel','settings':{'address':'127.0.0.1'} if legacy else {'rewriteAddress':'127.0.0.1'},'tag':'api'}
        template = {'log':{'loglevel':'warning'},'api':{'tag':'api','services':['HandlerService','LoggerService','StatsService']},'inbounds':[api_inbound],'outbounds':[{'tag':'direct','protocol':'freedom','settings':{'finalRules':[{'action':'allow','ip':['127.0.0.1/32']},{'action':'block'}]}},{'tag':'blocked','protocol':'blackhole','settings':{}}],'routing':{'domainStrategy':'AsIs','rules':[{'type':'field','inboundTag':['api'],'outboundTag':'api'},{'type':'field','ip':['127.0.0.0/8'],'outboundTag':'direct'},{'type':'field','network':'tcp,udp','outboundTag':'blocked'}]},'stats':{},'policy':{'levels':{'0':{'statsUserUplink':True,'statsUserDownlink':True}},'system':{'statsInboundUplink':True,'statsInboundDownlink':True}}}
        for key,value in [('subEnable','false'),('webListen','127.0.0.1'),('xrayTemplateConfig',json.dumps(template))]:
            db.execute('DELETE FROM settings WHERE key=?',(key,)); db.execute('INSERT INTO settings(key,value) VALUES (?,?)',(key,value))
        if family == '3.11.0':
            values = {'id':18732,'name':'ci-preserved-node','remark':'离线保真节点','scheme':'https','address':'192.0.2.1','port':443,'base_path':'/fixture-node/','api_token':'CI-only-fake-node-token','enable':0,'allow_private_address':0,'tls_verify_mode':'verify','inbound_sync_mode':'selected','inbound_tags':'[]','outbound_tag':'','status':'unknown'}
            columns = {r[1] for r in db.execute('PRAGMA table_info(nodes)')}
            if not {'id','name','api_token','enable'} <= columns: raise RuntimeError('3.x 缺少节点 schema')
            values = {k:v for k,v in values.items() if k in columns}
            db.execute('INSERT INTO nodes ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
elif action == 'tls':
    auth = json.loads(authfile.read_text()); auth['tls'] = True; save('auth.json',auth)
elif action == 'seed-api':
    request = api_session()
    inbound = {'remark':'ci-preserved-inbound','enable':'true','listen':'127.0.0.1','port':20771,'protocol':'vless','up':0,'down':0,'total':2147483648,'expiryTime':0,'settings':json.dumps({'clients':[client],'decryption':'none'}),'streamSettings':json.dumps({'network':'tcp','security':'none','tcpSettings':{'acceptProxyProtocol':False,'header':{'type':'none'}}}),'sniffing':json.dumps({'enabled':False,'destOverride':[]})}
    added = request('panel/api/inbounds/add',inbound)
    if not isinstance(added,dict) or not isinstance(added.get('id'),int): raise RuntimeError('原版正常 API 未创建入站')
    save('fixture-meta.json',{'inboundId':added['id']})
elif action == 'probe':
    request = api_session()
    if sys.argv[3] == 'login-only': sys.exit(0)
    rows = request('panel/api/inbounds/list')
    rows = [r for r in rows if r.get('id') == meta()['inboundId']] if isinstance(rows,list) else []
    if len(rows) != 1: raise RuntimeError('真实 API 缺少测试入站')
    inbound = inbound_semantics(rows[0])
    if inbound['clients'] != [client_semantics(client)]: raise RuntimeError('API 客户端语义不一致')
    if not legacy:
        global_client = request('panel/api/clients/get/'+urllib.parse.quote(client['email'],safe=''))
        if not isinstance(global_client,dict) or not isinstance(global_client.get('client'),dict): raise RuntimeError('新版客户端 API 未返回记录')
        record = dict(global_client['client']); record.pop('id',None)
        if client_semantics(record) != client_semantics(client): raise RuntimeError('新版客户端 API 身份或额度不保真')
        if meta()['inboundId'] not in global_client.get('inboundIds',[]): raise RuntimeError('新版客户端 API 缺少原入站关联')
    nodes = 'not-applicable-v2'
    if family == '3.11.0':
        rows = request('panel/api/nodes/list'); rows = [r for r in rows if r.get('id') == 18732] if isinstance(rows,list) else []
        if len(rows) != 1: raise RuntimeError('真实 API 未返回节点 fixture')
        keys = ('id','name','remark','scheme','address','port','basePath','enable','allowPrivateAddress','tlsVerifyMode','inboundSyncMode','inboundTags','outboundTag','hasApiToken')
        nodes = {k:rows[0][k] for k in keys if k in rows[0]}
    save(sys.argv[3],{'inbound':inbound,'node':nodes})
elif action == 'db-snapshot':
    with sqlite3.connect('file:'+str(dbfile)+'?mode=ro',uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise RuntimeError('SQLite 完整性失败')
        s = settings(db); result = {'users':[dict(r) for r in db.execute('SELECT id,username,password FROM users ORDER BY id')],'settings':{k:s.get(k) for k in static_settings}}
        inbound = db.execute('SELECT * FROM inbounds WHERE id=?',(meta()['inboundId'],)).fetchone()
        if inbound is None: raise RuntimeError('SQLite 入站丢失')
        inbound = dict(inbound); inbound['enable'] = bool(inbound['enable']); result['inbound'] = inbound_semantics(inbound)
        if table_exists(db,'clients'):
            rows = db.execute('SELECT * FROM clients WHERE email=?',(client['email'],)).fetchall()
            if len(rows) != 1: raise RuntimeError('3.x 客户端表未按旧客户端建立唯一记录')
            row = dict(rows[0]); record = dict(row); record.pop('id',None)
            if client_semantics(record) != client_semantics(client): raise RuntimeError('3.x 客户端表的身份、额度、到期或订阅字段不保真')
            links = db.execute('SELECT flow_override FROM client_inbounds WHERE client_id=? AND inbound_id=?',(row['id'],meta()['inboundId'])).fetchall()
            if len(links) != 1 or links[0][0] != client['flow']: raise RuntimeError('3.x 客户端与原入站关系未保留')
        elif not legacy: raise RuntimeError('迁移后的真实数据库缺少 3.x 客户端表')
        if result['inbound']['clients'] != [client_semantics(client)]: raise RuntimeError('SQLite 旧客户端语义改变')
        result['node'] = 'not-applicable-v2'
        if family == '3.11.0':
            row = db.execute('SELECT * FROM nodes WHERE id=18732').fetchone()
            if row is None: raise RuntimeError('SQLite 节点丢失')
            keys = ('id','name','remark','scheme','address','port','base_path','api_token','enable','allow_private_address','tls_verify_mode','inbound_sync_mode','inbound_tags','outbound_tag')
            result['node'] = {k:row[k] for k in keys if k in row.keys()}
        save(sys.argv[3],result)
        schema = [list(r) for r in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
        save(sys.argv[3]+'.schema',schema)
        traffic = db.execute('SELECT up,down FROM client_traffics WHERE email=?',(client['email'],)).fetchone()
        if traffic is None: raise RuntimeError('旧客户端流量记录丢失')
        save(sys.argv[3]+'.traffic',[int(v or 0) for v in traffic])
elif action == 'traffic-check':
    old = json.loads((work/'before-db.json.traffic').read_text()); new = json.loads((work/'after-db.json.traffic').read_text())
    if any(n < o for o,n in zip(old,new)): raise RuntimeError('原客户端流量计数发生回退')
elif action == 'proxy':
    body = b'XUI-CI-REAL-VLESS-LOOPBACK\n'
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self,*args): pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1',20773),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    configuration = {'log':{'loglevel':'warning'},'inbounds':[{'listen':'127.0.0.1','port':20772,'protocol':'socks','settings':{'auth':'noauth','udp':False}}],'outbounds':[{'protocol':'vless','settings':{'vnext':[{'address':'127.0.0.1','port':20771,'users':[{'id':client['id'],'encryption':'none'}]}]},'streamSettings':{'network':'tcp','security':'none'}}]}
    save('proxy-client.json',configuration)
    def receive(sock,count):
        data = b''
        while len(data)<count:
            part = sock.recv(count-len(data))
            if not part: raise RuntimeError('SOCKS 连接提前结束')
            data += part
        return data
    with (work/('proxy-client-'+sys.argv[3]+'.log')).open('wb') as output:
        process = subprocess.Popen(['/usr/local/x-ui/bin/xray-linux-amd64','run','-config',str(work/'proxy-client.json')],stdout=output,stderr=subprocess.STDOUT)
        try:
            for attempt in range(45):
                try:
                    if process.poll() is not None: raise RuntimeError('真实 Xray 客户端退出')
                    with socket.create_connection(('127.0.0.1',20772),timeout=3) as sock:
                        sock.settimeout(5); sock.sendall(b'\x05\x01\x00')
                        if receive(sock,2) != b'\x05\x00': raise RuntimeError('SOCKS 协商失败')
                        sock.sendall(b'\x05\x01\x00\x01'+socket.inet_aton('127.0.0.1')+struct.pack('!H',20773))
                        reply = receive(sock,4)
                        if reply[1] != 0: raise RuntimeError('真实 VLESS 代理拒绝连接')
                        if reply[3] == 1: receive(sock,4)
                        elif reply[3] == 4: receive(sock,16)
                        elif reply[3] == 3: receive(sock,receive(sock,1)[0])
                        else: raise RuntimeError('SOCKS 地址类型错误')
                        receive(sock,2); sock.sendall(b'GET /fixture HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                        response = b''
                        while len(response)<65536:
                            part = sock.recv(4096)
                            if not part: break
                            response += part
                        if b' 200 ' not in response.split(b'\r\n',1)[0] or response.split(b'\r\n\r\n',1)[-1] != body: raise RuntimeError('代理未返回本地 HTTP fixture 的实际内容')
                    break
                except Exception:
                    if attempt == 44 or process.poll() is not None: raise RuntimeError('真实环回代理验证失败') from None
                    time.sleep(1)
        finally:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            server.shutdown(); server.server_close(); thread.join(timeout=5)
else: raise RuntimeError('未知测试动作')
PY

install -d -m 0750 /etc/x-ui-ci-fixture
private '生成临时CA' openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=XUI-CI-Temporary-CA -keyout "$work/ca.key" -out "$work/ca.pem"
private '生成临时证书请求' openssl req -newkey rsa:2048 -nodes -subj /CN=localhost -keyout /etc/x-ui-ci-fixture/privkey.pem -out "$work/leaf.csr"
printf 'subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n' >"$work/leaf.ext"
private '签发临时证书' openssl x509 -req -in "$work/leaf.csr" -CA "$work/ca.pem" -CAkey "$work/ca.key" -CAcreateserial -days 1 -extfile "$work/leaf.ext" -out /etc/x-ui-ci-fixture/fullchain.pem

if [[ $mode == fresh ]]; then
    service_created=1
    private '中文全新安装' env XUI_NONINTERACTIVE=1 bash "$package/install.sh" --port 20531
    [[ $(systemctl show x-ui.service --property=User --value) == x-ui-zh ]] || fail '新装服务身份不是专用账号。'
    private '读取新装认证' python3 "$work/fixture.py" "$work" auth fresh
    private '验证初次登录' python3 "$work/fixture.py" "$work" probe login-only
    pass '全新安装的真实服务、随机账号和原始路径登录'
    systemctl stop x-ui.service
    chgrp x-ui-zh /etc/x-ui-ci-fixture /etc/x-ui-ci-fixture/privkey.pem /etc/x-ui-ci-fixture/fullchain.pem
    chmod 0640 /etc/x-ui-ci-fixture/*.pem
    private '新装绑定证书' runuser -u x-ui-zh -- env XUI_DB_FOLDER=/etc/x-ui XUI_LOG_FOLDER=/var/log/x-ui /usr/local/x-ui/x-ui cert -webCert /etc/x-ui-ci-fixture/fullchain.pem -webCertKey /etc/x-ui-ci-fixture/privkey.pem
else
    install -d -m 0755 /usr/local/x-ui
    cp -a -- "$official/." /usr/local/x-ui/
    chown -R root:root /usr/local/x-ui
    chmod 0755 /usr/local/x-ui /usr/local/x-ui/x-ui /usr/local/x-ui/bin /usr/local/x-ui/bin/*-linux-amd64
    install -d -m 0700 /etc/x-ui /var/log/x-ui
    install -m 0755 "$official/x-ui.sh" /usr/bin/x-ui
    install -m 0755 "$official/x-ui.sh" /usr/local/bin/x-ui
    install -m 0644 "$official_unit" /etc/systemd/system/x-ui.service
    install -d -m 0755 /etc/systemd/system/x-ui.service.d
    printf '[Service]\nEnvironment=XUI_CI_FIXTURE=preserve-env\nEnvironmentFile=-/etc/default/x-ui\nRestartSec=10s\n' >/etc/systemd/system/x-ui.service.d/90-ci-fixture.conf
    printf 'XUI_DB_FOLDER=/etc/x-ui\nXUI_LOG_FOLDER=/var/log/x-ui\n' >/etc/default/x-ui
    printf 'XUI_LOG_LEVEL=warning\n' >/usr/local/x-ui/.env
    private '初始化官方原账号' bash -c 'cd /usr/local/x-ui; ./x-ui setting -username ci-fixture-admin -password CI-only-fake-password-7c4b52 -port 20531 -webBasePath /ci-preserved-panel/ -listenIP 127.0.0.1'
    private '绑定官方证书' /usr/local/x-ui/x-ui cert -webCert /etc/x-ui-ci-fixture/fullchain.pem -webCertKey /etc/x-ui-ci-fixture/privkey.pem
    private '读取官方认证' python3 "$work/fixture.py" "$work" auth official
    systemctl daemon-reload
    service_created=1
fi

private '写入环回与节点配置' python3 "$work/fixture.py" "$work" seed-static
install -d -m 0750 /usr/local/x-ui/bin/ci-custom/nested
printf 'CI custom binary resource\n' >/usr/local/x-ui/bin/ci-custom/nested/resource.txt
chmod 0640 /usr/local/x-ui/bin/ci-custom/nested/resource.txt
private '启用TLS探测' python3 "$work/fixture.py" "$work" tls
systemctl start x-ui.service
private '正常API创建入站客户端' python3 "$work/fixture.py" "$work" seed-api
systemctl restart x-ui.service
private '原版真实环回代理' python3 "$work/fixture.py" "$work" proxy before
private '真实程序基线API' python3 "$work/fixture.py" "$work" probe before-api.json
private '原数据库保真快照' python3 "$work/fixture.py" "$work" db-snapshot before-db.json
pass '正常 API 创建客户端、原账号 HTTPS 登录和真实 VLESS 环回代理'
if [[ $official_version == 2.9.2 ]]; then printf '不适用：官方 v2.9.2 没有集群节点功能；该版本不声明节点迁移覆盖。\n'; fi

manifest() {
    python3 - "$work" "$1" <<'PY'
import hashlib, json, pathlib, stat, sys
work, output = pathlib.Path(sys.argv[1]), sys.argv[2]
paths = [pathlib.Path('/etc/systemd/system/x-ui.service'), pathlib.Path('/etc/x-ui-ci-fixture/fullchain.pem'), pathlib.Path('/etc/x-ui-ci-fixture/privkey.pem')]
paths += list(pathlib.Path('/etc/systemd/system/x-ui.service.d').glob('*'))
paths += [p for p in (pathlib.Path('/etc/default/x-ui'),pathlib.Path('/usr/local/x-ui/.env')) if p.exists()]
paths += list(pathlib.Path('/usr/local/x-ui/bin').rglob('*.dat'))
paths += list(pathlib.Path('/usr/local/x-ui/bin/ci-custom').rglob('*'))
rows = {}
for path in sorted(paths):
    s = path.lstat()
    if path.is_symlink(): raise RuntimeError('fixture 不应包含符号链接')
    rows[str(path)] = {'uid':s.st_uid,'gid':s.st_gid,'mode':stat.S_IMODE(s.st_mode),'sha256':hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}
(work/output).write_text(json.dumps(rows,sort_keys=True))
PY
}
private '原服务证书资源清单' manifest before-files.json
original_binary_hash=$(sha256sum /usr/local/x-ui/x-ui | cut -d' ' -f1)
original_core_hash=$(sha256sum /usr/local/x-ui/bin/xray-linux-amd64 | cut -d' ' -f1)
original_service_user=$(systemctl show x-ui.service --property=User --value)
original_service_group=$(systemctl show x-ui.service --property=Group --value)
original_cli_hash=$(sha256sum /usr/bin/x-ui 2>/dev/null | cut -d' ' -f1) || original_cli_hash=
original_local_cli_hash=$(sha256sum /usr/local/bin/x-ui | cut -d' ' -f1)

case $mode in
    fresh)
        systemctl restart x-ui.service
        private '重启后新装API' python3 "$work/fixture.py" "$work" probe after-api.json
        ;;
    migrate)
        private '接管官方原安装' env XUI_NONINTERACTIVE=1 bash "$package/install.sh" --migrate
        [[ $(sha256sum /usr/local/x-ui/x-ui | cut -d' ' -f1) == "$(sha256sum "$package/x-ui/x-ui" | cut -d' ' -f1)" ]] || fail '实际服务未使用待测中文程序。'
        [[ $(systemctl show x-ui.service --property=User --value) == "$original_service_user" && $(systemctl show x-ui.service --property=Group --value) == "$original_service_group" ]] || fail '接管改变了原服务身份。'
        [[ -f /usr/local/x-ui/.zh-lite-migration ]] || fail '缺少接管标记。'
        if [[ $official_version == 2.9.2 ]]; then
            original_core_backed_up=0
            for backup_core in /usr/local/.x-ui-migrate.*/backup/app/bin/xray-linux-amd64; do
                [[ -f $backup_core ]] || continue
                if [[ $(sha256sum "$backup_core" | cut -d' ' -f1) == "$original_core_hash" ]]; then original_core_backed_up=1; fi
            done
            (( original_core_backed_up )) || fail '跨版本升级的原 Xray 核心未完整保存在迁移备份。'
        fi
        private '首次迁移后真实环回代理' python3 "$work/fixture.py" "$work" proxy first-migrate
        private '同包重复接管' env XUI_NONINTERACTIVE=1 bash "$package/install.sh" --migrate
        private '中文程序保真API' python3 "$work/fixture.py" "$work" probe after-api.json
        ;;
    rollback)
        # 仅修改复制的候选包：真实中文 CLI 先迁移库，再污染测试记录并注入启动失败。
        # 不更改原服务、旧程序、安装器、systemctl 或原库的备份；故障只发生于候选进程。
        cp -a -- "$package" "$work/fault-package"
        fault=$work/fault-package
        install -d -m 0755 /usr/local/lib/x-ui-ci-fixture
        install -m 0755 "$package/x-ui/x-ui" /usr/local/lib/x-ui-ci-fixture/real-candidate
        cat >"$fault/x-ui/x-ui" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-run} == run ]]; then
    /usr/local/lib/x-ui-ci-fixture/real-candidate setting -show >/dev/null
    python3 - <<'PY'
import pathlib, sqlite3
with sqlite3.connect('/etc/x-ui/x-ui.db') as db:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='clients'").fetchone():
        raise RuntimeError('真实候选尚未完成客户端表迁移，不能注入回退故障')
    db.execute("UPDATE users SET username='ci-candidate-broken'")
    db.execute("UPDATE settings SET value='/ci-candidate-broken/' WHERE key='webBasePath'")
pathlib.Path('/run/x-ui-ci-candidate-failed').write_text('candidate-only\n')
PY
    exec env XUI_LOG_LEVEL=ci-invalid-log-level /usr/local/lib/x-ui-ci-fixture/real-candidate run
fi
exec /usr/local/lib/x-ui-ci-fixture/real-candidate "$@"
SH
        chmod 0755 "$fault/x-ui/x-ui"
        private '重建故障包校验' bash -c 'cd -- "$1"; find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS' _ "$fault"
        stage='候选故障回退'
        if env XUI_NONINTERACTIVE=1 bash "$fault/install.sh" --migrate >"$work/候选故障回退.log" 2>&1; then
            fail '故障候选安装意外成功，不能声明回退通过。'
        fi
        [[ -f /run/x-ui-ci-candidate-failed ]] || fail '候选故障未实际触发，不能声明回退通过。'
        [[ $(sha256sum /usr/local/x-ui/x-ui | cut -d' ' -f1) == "$original_binary_hash" ]] || fail '旧程序未恢复。'
        [[ $(sha256sum /usr/bin/x-ui | cut -d' ' -f1) == "$original_cli_hash" ]] || fail '官方命令未恢复。'
        [[ $(sha256sum /usr/local/bin/x-ui | cut -d' ' -f1) == "$original_local_cli_hash" ]] || fail '原本地命令未恢复。'
        [[ ! -e /usr/local/x-ui/.zh-lite-migration ]] || fail '回退残留接管标记。'
        private '回退后原版登录API' python3 "$work/fixture.py" "$work" probe after-api.json
        ;;
esac

stage='比较保真结果'
private '当前数据库保真快照' python3 "$work/fixture.py" "$work" db-snapshot after-db.json
private '核对原客户端流量计数' python3 "$work/fixture.py" "$work" traffic-check
private '当前服务证书资源清单' manifest after-files.json
cmp -s "$work/before-api.json" "$work/after-api.json" || fail '真实 API 的节点或入站配置与原版基线不一致。'
cmp -s "$work/before-db.json" "$work/after-db.json" || fail 'SQLite 账号、端口路径、证书或节点入站配置发生变化。'
cmp -s "$work/before-files.json" "$work/after-files.json" || fail '原服务文件、证书或 bin 资源的内容/权限发生变化。'
expected_core_hash=$original_core_hash
if [[ $official_version == 2.9.2 && $mode == migrate ]]; then
    expected_core_hash=$(sha256sum "$package/x-ui/bin/xray-linux-amd64" | cut -d' ' -f1)
fi
[[ $(sha256sum /usr/local/x-ui/bin/xray-linux-amd64 | cut -d' ' -f1) == "$expected_core_hash" ]] || fail '标准 Xray 核心未按对应版本的升级或回退契约保留。'
if [[ $mode == rollback ]]; then
    cmp -s "$work/before-db.json.schema" "$work/after-db.json.schema" || fail '回退后数据库未恢复原版 schema。'
fi
private '最终真实环回代理' python3 "$work/fixture.py" "$work" proxy after
systemctl is-active --quiet x-ui.service || fail '最终服务未运行。'
[[ $(systemctl show x-ui.service --property=SubState --value) == running ]] || fail '最终服务不处于稳定运行状态。'
pid=$(systemctl show x-ui.service --property=MainPID --value)
[[ $pid =~ ^[1-9][0-9]*$ && $(readlink "/proc/$pid/exe") == /usr/local/x-ui/x-ui ]] || fail '真实 systemd 主进程未使用目标程序。'
pass '旧账号、端口路径、客户端及关系、流量计数、证书、资源与最终代理保真'
pass "$mode 模式的真实 systemd 集成测试"
printf '验证范围：IPv4 本机 HTTPS、SQLite、真实 VLESS 环回代理；未验证外部节点连通性、PostgreSQL、公网证书或 IPv6。\n'
