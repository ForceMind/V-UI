export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD';
export type ParamLocation =
  | 'path'
  | 'query'
  | 'header'
  | 'body'
  | 'body (form)'
  | 'body (json)'
  | 'body (multipart)';
export type ParamType =
  | 'string'
  | 'integer'
  | 'integer[]'
  | 'string[]'
  | 'number'
  | 'boolean'
  | 'object'
  | 'object[]'
  | 'array'
  | 'file';

export interface EndpointParam {
  name: string;
  in: ParamLocation;
  type: ParamType;
  desc?: string;
  optional?: boolean;
  defaultValue?: string | number | boolean;
  minLength?: number;
  pattern?: string;
  enum?: readonly (string | number | boolean)[];
}

export interface Endpoint {
  method: HttpMethod;
  path: string;
  summary: string;
  description?: string;
  deprecated?: boolean;
  params?: EndpointParam[];
  body?: string;
  response?: string;
  errorResponse?: string;
  errorStatus?: number;
  requestSchema?: Record<string, unknown>;
  bodyRequiredOneOf?: string[];
  responseSchema?: string;
  responseSchemaArray?: boolean;
  responseSchemaArrayNullable?: boolean;
  responseObjectSchema?: Record<string, unknown>;
  responses?: Record<string, Record<string, unknown>>;
  security?: readonly Record<string, readonly string[]>[];
}

export interface SubscriptionHeader {
  name: string;
  desc: string;
}

export interface Section {
  id: string;
  title: string;
  description?: string;
  subHeader?: SubscriptionHeader[];
  endpoints: Endpoint[];
}

// /inbounds/update replaces the whole row, so it takes the same payload as /add.
const inboundBody =
  '{\n  "enable": true,\n  "remark": "VLESS-443",\n  "listen": "",\n  "port": 443,\n  "protocol": "vless",\n  "expiryTime": 0,\n  "total": 0,\n  "settings": {\n    "clients": [{ "id": "...", "email": "user1" }],\n    "decryption": "none",\n    "fallbacks": []\n  },\n  "streamSettings": {\n    "network": "tcp",\n    "security": "reality",\n    "realitySettings": { "show": false, "dest": "..." }\n  },\n  "sniffing": {\n    "enabled": true,\n    "destOverride": ["http", "tls"]\n  }\n}';

const outboundSubscriptionBodyParams: EndpointParam[] = [
  {
    name: 'remark',
    in: 'body (form)',
    type: 'string',
    desc: '可选的显示名称。',
    optional: true,
  },
  {
    name: 'url',
    in: 'body (form)',
    type: 'string',
    desc: '订阅地址（必填），必须为公开 HTTP(S) 地址。默认禁止私有、内部和回环地址，除非 allowPrivate 为 true。',
  },
  {
    name: 'tagPrefix',
    in: 'body (form)',
    type: 'string',
    desc: '生成出站标签时使用的前缀。默认选择最小的未占用 sub<N>- 前缀。',
    optional: true,
  },
  {
    name: 'userAgent',
    in: 'body (form)',
    type: 'string',
    desc: '获取订阅时发送的自定义 User-Agent，默认为 3x-ui-outbound-sub/1.0。',
    optional: true,
    defaultValue: '3x-ui-outbound-sub/1.0',
  },
  {
    name: 'updateInterval',
    in: 'body (form)',
    type: 'integer',
    desc: '自动刷新间隔，单位为秒，默认为 600。',
    optional: true,
    defaultValue: 600,
  },
  {
    name: 'enabled',
    in: 'body (form)',
    type: 'boolean',
    desc: '是否启用订阅，默认为 true。',
    optional: true,
    defaultValue: true,
  },
  {
    name: 'allowPrivate',
    in: 'body (form)',
    type: 'boolean',
    desc: '允许订阅指向私有、内部或回环地址，默认为 false。',
    optional: true,
    defaultValue: false,
  },
  {
    name: 'allowInsecure',
    in: 'body (form)',
    type: 'boolean',
    desc: '获取订阅时跳过 TLS 证书验证，默认为 false。',
    optional: true,
    defaultValue: false,
  },
  {
    name: 'prepend',
    in: 'body (form)',
    type: 'boolean',
    desc: '将此订阅的出站放在手动模板出站之前，默认为 false。',
    optional: true,
    defaultValue: false,
  },
];

const subBalancerBodyParams: EndpointParam[] = [
  {
    name: 'remark',
    in: 'body (form)',
    type: 'string',
    desc: '显示名称，用作配置备注（必填）。',
  },
  {
    name: 'strategy',
    in: 'body (form)',
    type: 'string',
    desc: '负载均衡策略：leastLoad、leastPing、roundRobin 或 random，默认为 random。',
    optional: true,
    defaultValue: 'random',
  },
  {
    name: 'inboundIds',
    in: 'body (form)',
    type: 'integer[]',
    desc: '通过重复的表单键选择成员入站，至少提供一个。',
  },
  {
    name: 'memberWeights',
    in: 'body (form)',
    type: 'object',
    desc: '仅用于 leastLoad：将入站编号映射到大于 0 的静态权重，例如 {"3":0.2}。权重越小，选择越频繁；未设置时权重为 1。其他策略不接受此字段，未选中入站的权重会被丢弃。',
    optional: true,
  },
  {
    name: 'sortOrder',
    in: 'body (form)',
    type: 'integer',
    desc: '订阅列表中的位置，从 1 开始，默认为 1。',
    optional: true,
    defaultValue: 1,
  },
  {
    name: 'enabled',
    in: 'body (form)',
    type: 'boolean',
    desc: '是否输出此负载均衡配置。创建时默认为 true，更新时省略则保留原值。',
    optional: true,
  },
];

const subscriptionHeadResponses = {
  '200': { description: '订阅可用。响应头与 GET 相同，无响应正文。' },
  '404': { description: '没有与此订阅标识匹配的已启用客户端。' },
  '500': { description: '生成订阅失败。' },
};

const hwidStatusErrorResponses = {
  '404': { description: '没有与此订阅标识匹配的已启用客户端，响应正文为空。' },
  '500': { description: '数据库查询失败，响应正文为空。' },
};

export const sections: readonly Section[] = [
  {
    id: 'authentication',
    title: '身份验证',
    description:
      '支持两种身份验证方式：浏览器会话使用登录接口设置的 Cookie；机器人、脚本和远程面板使用“设置 → 安全 → API 令牌”中的 Bearer 令牌。两种方式均适用于 /panel/api/* 下的所有接口。',
    endpoints: [
      {
        method: 'POST',
        path: '/login',
        summary: '使用用户名和密码登录并获取会话 Cookie。使用 Cookie 调用接口前必须先登录。',
        params: [
          { name: 'username', in: 'body', type: 'string', desc: '面板管理员用户名。' },
          { name: 'password', in: 'body', type: 'string', desc: '面板管理员密码。' },
          {
            name: 'twoFactorCode',
            in: 'body',
            type: 'string',
            desc: '启用双重验证时填写一次性验证码，否则省略。',
            optional: true,
          },
        ],
        body: '{\n  "username": "admin",\n  "password": "admin",\n  "twoFactorCode": "123456"\n}',
        response: '{\n  "success": true,\n  "msg": "Logged in successfully"\n}',
        errorResponse: '{\n  "success": false,\n  "msg": "Wrong username or password"\n}',
      },
      {
        method: 'POST',
        path: '/logout',
        summary: '清除会话 Cookie。浏览器会话需携带 CSRF 请求头。',
        response: '{\n  "success": true\n}',
      },
      {
        method: 'GET',
        path: '/csrf-token',
        summary:
          '为当前会话生成 CSRF 令牌，前端在修改请求的 X-CSRF-Token 请求头中发送。使用 Bearer 令牌验证的 API 调用不需要此令牌。',
        response: '{\n  "success": true,\n  "obj": "csrf-token-string"\n}',
      },
      {
        method: 'POST',
        path: '/getTwoFactorEnable',
        summary: '返回面板是否启用双重验证，登录页据此决定是否显示一次性验证码输入框。',
        response: '{\n  "success": true,\n  "obj": false\n}',
      },
    ],
  },

  {
    id: 'inbounds',
    title: '入站管理',
    description:
      '管理入站及其客户端。接口位于 /panel/api/inbounds，需要已登录会话或 Bearer 令牌。生成链接时，仅信任来自已配置可信代理的转发请求头。',
    endpoints: [
      {
        method: 'GET',
        path: '/panel/api/inbounds/list',
        summary:
          '列出当前用户的所有入站及 clientStats 流量计数。settings、streamSettings 和 sniffing 返回嵌套 JSON 对象；写入时仍兼容旧版 JSON 编码字符串。',
        responseSchema: 'Inbound',
        responseSchemaArray: true,
      },
      {
        method: 'GET',
        path: '/panel/api/inbounds/list/slim',
        summary:
          '与 /list 结构相同，但 settings.clients[] 仅保留 {email, enable, comment}，ClientStats 不包含 UUID/SubId。适合列表页；需要 uuid、password、flow 等完整信息时请求 /get/:id。',
        response:
          '{\n  "success": true,\n  "obj": [\n    {\n      "id": 1,\n      "remark": "VLESS-443",\n      "settings": {\n        "clients": [\n          { "email": "alice", "enable": true }\n        ],\n        "decryption": "none"\n      },\n      "clientStats": []\n    }\n  ]\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/inbounds/options',
        summary:
          '返回轻量入站选项：id、remark、tag、protocol、port、tlsFlowCapable 和 ssMethod。tlsFlowCapable 表示 TCP 上启用 TLS/Reality 的 VLESS，或 XHTTP 上启用 VLESS 加密的 VLESS；ssMethod 为 Shadowsocks 加密方式，供生成 2022 PSK 使用。省略 settings、streamSettings 和 clientStats，适合大量客户端场景中的选择器。',
        responseSchema: 'InboundOption',
        responseSchemaArray: true,
      },
      {
        method: 'GET',
        path: '/panel/api/inbounds/amneziawgKernelReadiness',
        responseSchema: 'Readiness',
        summary:
          '检查主机能否通过内核模块运行 AmneziaWG 入站，返回 ready、root/tools/module/nft 阻塞代码及修复命令。保存入站时还会拒绝内核后端不支持的 IPv6、端口转发、routeThroughXray 和固定监听地址。',
      },
      {
        method: 'GET',
        path: '/panel/api/inbounds/allLinks',
        responseObjectSchema: { type: 'array', nullable: true, items: { type: 'string' } },
        summary:
          '导出所有入站和客户端的协议链接（vless://、vmess://、trojan://、ss://、hysteria://、mtproto）。通过订阅引擎应用每个客户端的备注模板，与客户端详情和二维码中的链接一致。不具备链接格式的 socks、http、mixed、wireguard、dokodemo、tunnel 不输出。',
        response:
          '{\n  "success": true,\n  "obj": [\n    "vless://uuid@host:443?security=reality&...#Germany-alice",\n    "vmess://eyJ2IjoyLC..."\n  ]\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/inbounds/get/:id',
        summary: '按数字编号获取单个入站。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '入站编号。' }],
      },
      {
        method: 'POST',
        path: '/panel/api/inbounds/add',
        summary:
          '创建入站，提交完整配置：protocol、port、settings、streamSettings、sniffing、remark、expiryTime、total、enable。配置对象建议直接嵌套，也兼容旧版 JSON 编码字符串。',
        body: inboundBody,
        errorResponse: '{\n  "success": false,\n  "msg": "Port 443 is already in use"\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/inbounds/del/:id',
        summary: '按编号删除入站，同时删除关联客户端的流量统计记录。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '入站编号。' }],
      },
      {
        method: 'POST',
        path: '/panel/api/inbounds/bulkDel',
        summary: '批量删除入站，按列表顺序处理。逐项报告失败，其余项继续执行，最多重启一次 Xray。',
        body: '{\n  "ids": [1, 2, 3]\n}',
        response:
          '{\n  "success": true,\n  "obj": {\n    "deleted": 2,\n    "skipped": [\n      { "id": 3, "reason": "..." }\n    ]\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/inbounds/update/:id',
        summary:
          '替换入站配置，请求结构与 /add 相同，但保留已存储的客户端列表和启用状态，忽略请求中的 settings.clients 与 enable。客户端通过 /panel/api/clients 管理，启用状态通过 /setEnable 修改。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '入站编号。' }],
        body: inboundBody,
      },
      {
        method: 'POST',
        path: '/panel/api/inbounds/setEnable/:id',
        summary: '仅修改 enable 标志，无需序列化整个 settings。推荐用于大入站的界面开关。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '入站编号。' }],
        body: '{\n  "enable": false\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/inbounds/:id/subSortIndex',
        summary: '仅修改订阅排序。读取已保存的入站，避免排序请求用旧客户端列表覆盖并发编辑。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '入站编号。' }],
        body: '{\n  "subSortIndex": 2\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/inbounds/:id/resetTraffic',
        summary: '清零单个入站的上传和下载计数，不修改客户端计数。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '入站编号。' }],
      },
      {
        method: 'POST',
        path: '/panel/api/inbounds/:id/delAllClients',
        summary:
          '删除某个入站中的所有客户端，保留入站本身。从 settings.clients[] 收集标识后批量移除运行时用户、清理流量记录并同步入站。此操作不可撤销。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '入站编号。' }],
        response: '{\n  "success": true,\n  "obj": {\n    "deleted": 12\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/inbounds/resetAllTraffics',
        summary: '清零所有入站的上传和下载计数，历史计量数据将丢失。',
      },
      {
        method: 'POST',
        path: '/panel/api/inbounds/import',
        summary: '从 JSON 导入入站，例如界面导出的配置。使用表单编码，通过单个 data 字段提交。',
        params: [
          {
            name: 'data',
            in: 'body (form)',
            type: 'string',
            desc: '经过 JSON 编码的入站配置。',
          },
        ],
      },
      {
        method: 'POST',
        path: '/panel/api/inbounds/pushClientTraffics',
        summary:
          '接收主面板汇总的客户端用量，以主面板 GUID 区分来源。保存在独立表中，仅用于界面叠加显示和本地额度控制，不混入供主面板轮询的本地计数，避免破坏增量计量。由节点流量同步任务调用。',
        params: [
          {
            name: 'masterGuid',
            in: 'body (json)',
            type: 'string',
            desc: '推送数据的主面板稳定 GUID。',
          },
          {
            name: 'traffics',
            in: 'body (json)',
            type: 'object[]',
            desc: '客户端流量记录，仅读取 email、up、down。',
          },
        ],
        body: '{\n  "masterGuid": "9f6c2d-…",\n  "traffics": [\n    { "email": "alice", "up": 1048576, "down": 2097152 }\n  ]\n}',
        response: '{\n  "success": true\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/inbounds/:id/fallbacks',
        summary:
          '列出 VLESS/Trojan TCP-TLS 主入站的回落规则。每项关联一个子入站及可选 SNI/ALPN/path/dest/xver 条件。dest 为空时使用子入站的监听地址和端口。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '主入站编号。' }],
        response:
          '{\n  "success": true,\n  "obj": [\n    {\n      "id": 1,\n      "masterId": 10,\n      "childId": 11,\n      "name": "",\n      "alpn": "",\n      "path": "/vlws",\n      "dest": "",\n      "xver": 2,\n      "sortOrder": 0\n    }\n  ]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/inbounds/:id/fallbacks',
        summary: '替换主入站的全部回落规则，使用 JSON 请求正文，并触发 Xray 重启。',
        params: [
          { name: 'id', in: 'path', type: 'number', desc: '主入站编号。' },
          {
            name: 'fallbacks',
            in: 'body (json)',
            type: 'object[]',
            desc: '由 {childId, name, alpn, path, dest, xver, sortOrder} 组成的数组。dest 留空时使用子入站监听地址和端口，也可指定 8443、127.0.0.1:8443 或 /dev/shm/x.sock 等值覆盖。',
          },
        ],
        body: '{\n  "fallbacks": [\n    { "childId": 11, "path": "/vlws", "xver": 2 },\n    { "childId": 12, "alpn": "h2", "dest": "8443" }\n  ]\n}',
        response: '{\n  "success": true,\n  "msg": "Inbound updated"\n}',
      },
    ],
  },

  {
    id: 'server',
    title: '服务器',
    description:
      '系统状态、日志、证书生成、Xray 程序管理和备份恢复，接口均位于 /panel/api/server。',
    endpoints: [
      {
        method: 'GET',
        path: '/panel/api/openapi.json',
        summary:
          '以 OpenAPI 3 格式返回当前接口文档，也是面板 API 文档页的数据来源。与其他 /panel/api 接口一样需要会话或 Bearer 令牌，可用于生成客户端或导入接口工具。',
      },
      {
        method: 'GET',
        path: '/panel/api/server/status',
        summary:
          '实时主机快照：CPU、内存、交换空间、磁盘、网络读写、负载、连接数和 Xray 状态。后台每两秒更新缓存。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "cpu": 12.5,\n    "mem": { "current": 2147483648, "total": 8589934592 },\n    "swap": { "current": 0, "total": 4294967296 },\n    "disk": { "current": 53687091200, "total": 268435456000 },\n    "netIO": { "up": 1073741824, "down": 2147483648 },\n    "xray": { "state": "running", "version": "v25.10.31" },\n    "tcpCount": 42,\n    "load": { "load1": 0.5, "load5": 0.3, "load15": 0.2 }\n  }\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/server/fail2banStatus',
        summary:
          '检查主机能否实施客户端 IP 限制，面板据此控制“IP 限制”字段是否可用。此功能依赖 Fail2ban。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "enabled": true,\n    "installed": true,\n    "usable": true,\n    "windows": false\n  }\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/server/cpuHistory/:bucket',
        summary: '旧版 CPU 汇总历史。请改用 /history/cpu/:bucket，数据相同，统一采用 {t, v} 结构。',
        params: [
          {
            name: 'bucket',
            in: 'path',
            type: 'number',
            desc: '统计分桶大小，单位为秒，允许 2、30、60、120、180、300。',
          },
        ],
      },
      {
        method: 'GET',
        path: '/panel/api/server/history/:metric/:bucket',
        summary: '返回某项指标最近约六小时的聚合时序数据，样本结构为 {t, v}。',
        params: [
          {
            name: 'metric',
            in: 'path',
            type: 'string',
            desc: '指标：cpu、mem、netUp、netDown、online、load1、load5、load15。',
          },
          {
            name: 'bucket',
            in: 'path',
            type: 'number',
            desc: '统计分桶大小，单位为秒，允许 2、30、60、120、180、300。',
          },
        ],
        response:
          '{\n  "success": true,\n  "obj": [\n    { "t": 1700000000, "v": 12.5 },\n    { "t": 1700000002, "v": 13.1 }\n  ]\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/server/xrayMetricsState',
        summary:
          '返回 Xray 运行指标状态，包括是否配置 metrics、已产生数据的 expvar 键及当前快照。未配置指标时返回空状态。',
      },
      {
        method: 'GET',
        path: '/panel/api/server/xrayMetricsHistory/:metric/:bucket',
        summary:
          '返回某项 Xray 运行指标最近约六小时的历史，结构与 /history/:metric/:bucket 相同，为 {t, v}。',
        params: [
          {
            name: 'metric',
            in: 'path',
            type: 'string',
            desc: '指标：xrAlloc、xrSys、xrHeapObjects、xrNumGC、xrPauseNs。',
          },
          {
            name: 'bucket',
            in: 'path',
            type: 'number',
            desc: '统计分桶大小，单位为秒，允许 2、30、60、120、180、300。',
          },
        ],
      },
      {
        method: 'GET',
        path: '/panel/api/server/xrayObservatory',
        summary:
          '返回 Xray 连接观测的最新快照，包括各出站的延迟、健康状态和最后探测时间。仅在配置了连接观测时有数据。',
      },
      {
        method: 'GET',
        path: '/panel/api/server/xrayObservatoryHistory/:tag/:bucket',
        summary: '返回某个出站标签的连接观测历史，与其他历史接口一样采用 {t, v} 结构。',
        params: [
          {
            name: 'tag',
            in: 'path',
            type: 'string',
            desc: '连接观测配置中的出站标签。',
          },
          {
            name: 'bucket',
            in: 'path',
            type: 'number',
            desc: '统计分桶大小，单位为秒，允许 2、30、60、120、180、300。',
          },
        ],
      },
      {
        method: 'GET',
        path: '/panel/api/server/getXrayVersion',
        summary: '列出此主机可安装的 Xray 版本。',
        response: '{\n  "success": true,\n  "obj": ["v25.10.31", "v25.9.15", "v25.8.1"]\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/server/getPanelUpdateInfo',
        summary: '检查 GitHub 上是否有更新的 3x-ui 版本。',
      },
      {
        method: 'GET',
        path: '/panel/api/server/getUpdateStatus',
        summary:
          '返回最近一次面板自更新的结果。将 runId 与 POST updatePanel 返回值对比，以区分当前运行和旧结果。',
        responseSchema: 'PanelUpdateStatus',
      },
      {
        method: 'GET',
        path: '/panel/api/server/getConfigJson',
        summary: '返回此主机当前运行的完整 Xray 配置。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "log": { "loglevel": "warning" },\n    "inbounds": [...],\n    "outbounds": [...],\n    "routing": { "rules": [...] }\n  }\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/server/getDb',
        summary:
          '下载完整数据库备份。SQLite 面板返回 .db 文件，PostgreSQL 面板返回 pg_dump 自定义格式的 .dump 文件，可用作手动备份。',
      },
      {
        method: 'GET',
        path: '/panel/api/server/getMigration',
        summary:
          '下载跨数据库引擎迁移文件。SQLite 返回 SQL 文本 .dump，PostgreSQL 返回基于实时数据生成的 SQLite .db 文件。',
      },
      {
        method: 'GET',
        path: '/panel/api/server/getNewUUID',
        summary: '生成 UUID v4，可用于客户端标识。',
        responseSchema: 'NewUUIDResponse',
      },
      {
        method: 'GET',
        path: '/panel/api/server/getWebCertFiles',
        summary:
          '返回当前面板 Web TLS 证书和密钥的文件路径。主面板通过节点 API 令牌调用，使分配到节点的入站能够使用节点本机证书路径。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "webCertFile": "/root/cert/example.com/fullchain.pem",\n    "webKeyFile": "/root/cert/example.com/privkey.pem"\n  }\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/server/descendants',
        summary:
          '只读返回当前面板管理的节点摘要：guid、parentGuid、name、address、status 和版本。上级面板通过节点 API 令牌读取，用于展示链式拓扑中的间接节点。节点数量由上级面板计算。',
        response:
          '{\n  "success": true,\n  "obj": [\n    {\n      "guid": "c3d4-...",\n      "parentGuid": "a1b2-...",\n      "name": "Node3",\n      "address": "10.0.0.3",\n      "status": "online"\n    }\n  ]\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/server/getNewX25519Cert',
        summary: '生成 Reality 使用的 X25519 密钥对。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "privateKey": "uN9qLfV3zH8w...",\n    "publicKey": "5v8xPqR2sM7k..."\n  }\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/server/getNewmldsa65',
        summary: '生成 ML-DSA-65 密钥对，返回 {seed, verify}。',
        responseSchema: 'MLDSA65Response',
      },
      {
        method: 'GET',
        path: '/panel/api/server/getNewmlkem768',
        summary: '生成 ML-KEM-768 密钥对，返回 {seed, client}。',
        responseSchema: 'MLKEM768Response',
      },
      {
        method: 'GET',
        path: '/panel/api/server/getNewVlessEnc',
        summary:
          '生成 VLESS 加密认证选项，返回 auths 数组，每项包含 id、label、encryption、decryption。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "auths": [\n      { "id": 0, "label": "Auth #0", "encryption": "aes-256-gcm", "decryption": "" }\n    ]\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/server/stopXrayService',
        summary: '停止 Xray 程序，所有代理立即离线。',
        errorResponse: '{\n  "success": false,\n  "msg": "Xray is not running"\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/server/restartXrayService',
        summary: '按当前配置重新加载 Xray，通常用于入站结构或路由变更后。',
        errorResponse: '{\n  "success": false,\n  "msg": "Xray config is invalid: ..."\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/server/installXray/:version',
        summary: '下载并安装指定 Xray 版本，latest 表示最新版本。',
        params: [
          {
            name: 'version',
            in: 'path',
            type: 'string',
            desc: 'Xray 版本标签，例如 v25.10.31；latest 表示最新版本。',
          },
        ],
      },
      {
        method: 'POST',
        path: '/panel/api/server/updatePanel',
        summary:
          '将面板更新至最新版本，成功后重启服务器进程。Bearer 令牌调用者需要 admin 或 node-admin 权限。',
        params: [
          {
            name: 'dev',
            in: 'body (form)',
            type: 'boolean',
            desc: '覆盖本次更新渠道，省略则使用面板配置的渠道。',
            optional: true,
          },
        ],
        response: '{\n  "success": true,\n  "obj": {\n    "runId": "1735689600123456789"\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/server/setUpdateChannel',
        summary: '在稳定版和随提交滚动发布的开发版之间切换更新渠道，仅对开发版构建生效。',
        params: [
          {
            name: 'dev',
            in: 'body (form)',
            type: 'boolean',
            desc: 'true 为开发渠道，false 为稳定渠道。',
          },
        ],
        body: 'dev=true',
      },
      {
        method: 'POST',
        path: '/panel/api/server/updateGeofile',
        summary: '更新默认 GeoIP/GeoSite 数据文件；更新单个文件请使用 /:fileName 接口。',
      },
      {
        method: 'POST',
        path: '/panel/api/server/updateGeofile/:fileName',
        summary: '按文件名更新单个地理数据文件，例如 geoip.dat、geosite.dat。',
        params: [
          {
            name: 'fileName',
            in: 'path',
            type: 'string',
            desc: '需要更新的数据文件名。',
          },
        ],
      },
      {
        method: 'POST',
        path: '/panel/api/server/logs/:count',
        summary: '返回面板日志末尾的 N 行。',
        params: [
          { name: 'count', in: 'path', type: 'number', desc: '返回末尾的日志行数。' },
          {
            name: 'level',
            in: 'body (form)',
            type: 'string',
            desc: '最低日志级别筛选条件。',
            optional: true,
          },
          {
            name: 'syslog',
            in: 'body (form)',
            type: 'boolean',
            desc: '读取系统日志而非面板日志。',
            optional: true,
          },
        ],
        body: 'level=info&syslog=false',
        responseObjectSchema: { type: 'array', nullable: true, items: { type: 'string' } },
        response:
          '{\n  "success": true,\n  "obj": [\n    "2025/01/01 12:00:00 [INFO] Server started",\n    "2025/01/01 12:00:01 [INFO] Xray is running"\n  ]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/server/xraylogs/:count',
        summary: '返回 Xray 进程日志末尾的 N 行。',
        params: [
          { name: 'count', in: 'path', type: 'number', desc: '返回末尾的日志行数。' },
          {
            name: 'filter',
            in: 'body (form)',
            type: 'string',
            desc: '关键字筛选，仅返回包含此字符串的行。',
            optional: true,
          },
          {
            name: 'showDirect',
            in: 'body (form)',
            type: 'string',
            desc: 'true 表示包含直连（freedom）流量日志。',
            optional: true,
          },
          {
            name: 'showBlocked',
            in: 'body (form)',
            type: 'string',
            desc: 'true 表示包含拦截（blackhole）流量日志。',
            optional: true,
          },
          {
            name: 'showProxy',
            in: 'body (form)',
            type: 'string',
            desc: 'true 表示包含代理流量日志。',
            optional: true,
          },
        ],
        body: 'filter=error&showDirect=false&showBlocked=true&showProxy=true',
        responseSchema: 'LogEntry',
        responseSchemaArray: true,
        responseSchemaArrayNullable: true,
      },
      {
        method: 'POST',
        path: '/panel/api/server/amneziawglogs/:count',
        summary:
          '返回 AmneziaWG 对等节点的实时握手、端点和流量，以及面板自身的 AmneziaWG 事件日志。',
        params: [
          {
            name: 'count',
            in: 'path',
            type: 'number',
            desc: '最多返回的对等节点和事件日志条数。',
          },
          {
            name: 'filter',
            in: 'body (form)',
            type: 'string',
            desc: '关键字筛选，仅返回包含此字符串的记录或日志行。',
            optional: true,
          },
        ],
        body: 'filter=awg1',
        responseSchema: 'AmneziaWGLogs',
      },
      {
        method: 'POST',
        path: '/panel/api/server/importDB',
        summary:
          '从上传的备份恢复数据库，使用多部分表单，字段名为 db。SQLite 面板接受 SQLite .db 或迁移 .dump；PostgreSQL 面板接受 pg_dump .dump、SQLite .db 或 SQLite 迁移文件。恢复后面板重启，此操作将覆盖现有数据。',
        params: [
          {
            name: 'db',
            in: 'body (multipart)',
            type: 'file',
            desc: '需要上传的数据库备份或迁移文件。',
          },
          {
            name: 'keepHostSettings',
            in: 'body (multipart)',
            type: 'boolean',
            desc: '保留本机地址、证书和节点身份，默认为 true。',
            optional: true,
            defaultValue: true,
          },
        ],
      },
      {
        method: 'POST',
        path: '/panel/api/server/getNewEchCert',
        summary: '为指定 SNI 生成 ECH（加密客户端问候）密钥对和配置列表。',
        params: [
          {
            name: 'sni',
            in: 'body (form)',
            type: 'string',
            desc: '需要生成 ECH 配置的服务器名称指示（SNI）。',
          },
        ],
        body: 'sni=example.com',
        response:
          '{\n  "success": true,\n  "obj": {\n    "echKeySet": "...",\n    "echServerKeys": [...],\n    "echConfigList": "..."\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/server/getCertHash',
        summary:
          '计算 DER 证书的十六进制 SHA-256，供 pinnedPeerCertSha256 固定证书使用。支持服务端文件路径或直接提供 PEM/DER 内容。',
        bodyRequiredOneOf: ['certFile', 'certContent'],
        params: [
          {
            name: 'certFile',
            in: 'body (form)',
            type: 'string',
            desc: '服务器上的证书文件路径，优先于 certContent。',
            optional: true,
            pattern: '.*\\S.*',
          },
          {
            name: 'certContent',
            in: 'body (form)',
            type: 'string',
            desc: '直接提供 PEM 或 DER 证书内容，certFile 为空时使用。',
            optional: true,
            pattern: '.*\\S.*',
          },
        ],
        body: 'certFile=/root/cert.crt',
        response: '{\n  "success": true,\n  "obj": [\n    "e8e2d3..."\n  ]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/server/getRemoteCertHash',
        summary:
          '对远程服务器执行 xray tls ping，返回当前叶证书的 SHA-256 列表，供 pinnedPeerCertSha256 使用。',
        params: [
          {
            name: 'server',
            in: 'body (form)',
            type: 'string',
            desc: '远程域名或域名:端口，默认端口为 443，例如 cloudflare-dns.com。',
          },
          {
            name: 'allowPrivate',
            in: 'body (form)',
            type: 'boolean',
            optional: true,
            desc: '允许探测私有、内部或回环服务器，例如局域网或 Docker 服务名。默认为 false；请求防护拦截此类目标时，错误响应会设置 obj.privateTarget=true。',
          },
        ],
        body: 'server=cloudflare-dns.com',
        response: '{\n  "success": true,\n  "obj": [\n    "e8e2d3..."\n  ]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/server/scanRealityTarget',
        summary:
          '对候选 REALITY 目标进行 TLS 1.3 实时探测，根据 TLS 1.3、h2、X25519 和可信证书判断可用性，并返回证书 SAN 域名。私有或回环地址标记 privateTarget=true，仅在 allowPrivate 开启时探测。',
        params: [
          {
            name: 'target',
            in: 'body (form)',
            type: 'string',
            desc: '候选主机或主机:端口，默认端口为 443，例如 www.cloudflare.com:443。',
          },
          {
            name: 'sni',
            in: 'body (form)',
            type: 'string',
            optional: true,
            desc: '握手发送并用于证书验证的 SNI，对应入站 serverNames。默认使用目标主机名，前置代理通常以其默认证书响应。',
          },
          {
            name: 'xver',
            in: 'body (form)',
            type: 'number',
            optional: true,
            desc: '目标所需的 PROXY 协议版本，对应入站 xver，0 表示不使用。',
          },
          {
            name: 'allowPrivate',
            in: 'body (form)',
            type: 'boolean',
            optional: true,
            desc: '允许探测私有、内部或回环目标，例如局域网或 Docker 服务名。默认为 false；请求防护拦截时返回 privateTarget=true。',
          },
        ],
        body: 'target=www.cloudflare.com:443',
        responseSchema: 'RealityScanResult',
      },
      {
        method: 'POST',
        path: '/panel/api/server/scanRealityTargets',
        summary:
          '探测或发现 REALITY 目标，按可用性和延迟排序。逗号分隔项可以是域名（携带 SNI 验证）、IP 或 CIDR（不携带 SNI，从证书发现域名）。留空时使用 realityScanCandidates 设置；该设置也为空时使用内置候选列表。',
        params: [
          {
            name: 'targets',
            in: 'body (form)',
            type: 'string',
            optional: true,
            desc: '可选的逗号分隔目标：域名[:端口]、IP[:端口] 或 CIDR，例如 104.16.0.0/24。省略时使用 realityScanCandidates 设置，设置为空则使用内置候选列表。',
          },
        ],
        body: 'targets=104.16.0.0/24,www.apple.com:443',
        responseSchema: 'RealityScanResult',
        responseSchemaArray: true,
      },
      {
        method: 'GET',
        path: '/panel/api/server/clientIps',
        summary: '获取完整聚合的 inbound_client_ips 数据表，供节点同步集群近期活动 IP。',
        responseSchema: 'InboundClientIps',
        responseSchemaArray: true,
      },
      {
        method: 'POST',
        path: '/panel/api/server/clientIps',
        summary: '提交近期活动 IP 及时间戳，面板合并到数据库，以保持全局 IP 限制视图一致。',
        requestSchema: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              clientEmail: { type: 'string' },
              ips: {
                type: 'array',
                nullable: true,
                items: {
                  type: 'object',
                  properties: { ip: { type: 'string' }, timestamp: { type: 'integer' } },
                  required: ['ip', 'timestamp'],
                },
              },
            },
            required: ['clientEmail', 'ips'],
          },
        },
      },
    ],
  },

  {
    id: 'clients',
    title: '客户端',
    description:
      '将客户端作为独立实体管理，可关联一个或多个入站。同一客户端记录用于生成所属各入站的 settings.clients 项。接口位于 /panel/api/clients。',
    endpoints: [
      {
        method: 'GET',
        path: '/panel/api/clients/list',
        summary:
          '列出所有客户端、关联入站编号及流量记录。reverse 返回嵌套 JSON 对象，写入时仍兼容旧版 JSON 编码字符串。',
        response:
          '{\n  "success": true,\n  "obj": [\n    {\n      "id": 1,\n      "email": "alice@example.com",\n      "subId": "abcd1234",\n      "uuid": "...",\n      "totalGB": 53687091200,\n      "expiryTime": 1735689600000,\n      "enable": true,\n      "reverse": null,\n      "inboundIds": [3, 5],\n      "traffic": { "up": 1024, "down": 4096, "enable": true }\n    }\n  ]\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/clients/list/paged',
        summary:
          '在服务端筛选、排序和分页客户端。每行省略 uuid/password/auth/flow/security/reverse/tgId，减少列表载荷。响应摘要按完整数据库计算，计数字段精确，关联标识数组最多 200 项。每页最多 200 行；编辑或详情页通过 /get/:email 获取完整数据。',
        params: [
          {
            name: 'page',
            in: 'query',
            type: 'number',
            desc: '页码从 1 开始，默认为 1。',
            optional: true,
            defaultValue: 1,
          },
          {
            name: 'pageSize',
            in: 'query',
            type: 'number',
            desc: '每页行数，默认 25，最多 200。',
            optional: true,
            defaultValue: 25,
          },
          {
            name: 'search',
            in: 'query',
            type: 'string',
            desc: '忽略大小写，在 email、subId、comment、UUID、password、auth 或 Telegram 标识中匹配子字符串。',
            optional: true,
          },
          {
            name: 'filter',
            in: 'query',
            type: 'string',
            desc: '逗号分隔的状态：online、active、deactive、depleted、expiring，多个值取并集。',
            optional: true,
          },
          {
            name: 'protocol',
            in: 'query',
            type: 'string',
            desc: '逗号分隔的入站协议：vmess、vless、trojan、shadowsocks、wireguard、hysteria、http、mixed、tunnel、tun、mtproto、amneziawg，多个值取并集。',
            optional: true,
          },
          {
            name: 'inbound',
            in: 'query',
            type: 'string',
            desc: '逗号分隔的正整数入站编号，多个值取并集，忽略无效或非正数编号。',
            optional: true,
          },
          {
            name: 'sort',
            in: 'query',
            type: 'string',
            desc: '排序字段，省略或不识别时按客户端编号升序。',
            optional: true,
            enum: [
              'enable',
              'email',
              'inboundIds',
              'traffic',
              'remaining',
              'expiryTime',
              'createdAt',
              'updatedAt',
              'lastOnline',
            ],
          },
          {
            name: 'order',
            in: 'query',
            type: 'string',
            desc: '排序方向，仅 descend 表示降序，其他值均为升序。',
            optional: true,
            enum: ['ascend', 'descend'],
          },
          {
            name: 'expiryFrom',
            in: 'query',
            type: 'number',
            desc: '最小到期时间，包含边界，单位为 Unix 毫秒。0 或负数表示不设置。',
            optional: true,
          },
          {
            name: 'expiryTo',
            in: 'query',
            type: 'number',
            desc: '最大到期时间，包含边界，单位为 Unix 毫秒。0 或负数表示无上限。',
            optional: true,
          },
          {
            name: 'usageFrom',
            in: 'query',
            type: 'number',
            desc: '最小上传和下载合计用量，单位为字节，包含边界，0 表示不设置。',
            optional: true,
          },
          {
            name: 'usageTo',
            in: 'query',
            type: 'number',
            desc: '最大上传和下载合计用量，单位为字节，包含边界，0 表示无上限。',
            optional: true,
          },
          {
            name: 'autoRenew',
            in: 'query',
            type: 'string',
            desc: 'on 筛选已设置定期间隔或日历重置的客户端，off 筛选未设置的客户端。',
            optional: true,
            enum: ['on', 'off'],
          },
          {
            name: 'hasTgId',
            in: 'query',
            type: 'string',
            desc: 'yes 筛选 Telegram 标识非零的客户端，no 筛选未设置的客户端。',
            optional: true,
            enum: ['yes', 'no'],
          },
          {
            name: 'hasComment',
            in: 'query',
            type: 'string',
            desc: 'yes 筛选备注非空的客户端，no 筛选备注为空的客户端。',
            optional: true,
            enum: ['yes', 'no'],
          },
          {
            name: 'group',
            in: 'query',
            type: 'string',
            desc: '逗号分隔的分组名称，去除首尾空格后忽略大小写匹配，多个值取并集。',
            optional: true,
          },
        ],
        responseSchema: 'ClientPageResponse',
      },
      {
        method: 'GET',
        path: '/panel/api/clients/get/:email',
        summary: '按 email 获取客户端，包含已关联的入站编号和外部配置编号。',
        params: [
          { name: 'email', in: 'path', type: 'string', desc: '客户端 email，作为唯一标识。' },
        ],
        response:
          '{\n  "success": true,\n  "obj": {\n    "client": { "id": 1, "email": "alice@example.com", ... },\n    "inboundIds": [3, 5],\n    "externalLinks": [\n      { "id": 11, "kind": "link", "value": "vless://...", "remark": "DE", "enable": true, "expiryTime": 0 },\n      { "id": 12, "kind": "subscription", "value": "https://provider.example/sub/abc", "remark": "Provider", "enable": false, "expiryTime": 1767225600000, "namePrefix": "[zjh] ", "lastFetchAt": 1767220000000, "lastFetchError": "" }\n    ]\n  }\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/clients/get/subId/:subId',
        summary: '按订阅标识获取客户端。',
        description:
          '精确匹配订阅标识并按客户端编号排序，返回与 GET /clients/get/:email 相同结构的数组。同一订阅可关联多个客户端，包括禁用、过期和未关联入站的客户端。标识不存在时返回 HTTP 200、success:false、obj:null 和记录不存在提示。需要管理员令牌或面板登录会话。',
        params: [
          {
            name: 'subId',
            in: 'path',
            type: 'string',
            desc: '精确订阅标识，需进行 URL 编码。',
          },
        ],
        responseObjectSchema: {
          type: 'array',
          nullable: true,
          items: {
            type: 'object',
            properties: {
              client: { $ref: '#/components/schemas/ClientRecord' },
              inboundIds: { type: 'array', items: { type: 'integer' } },
              externalLinks: { type: 'array', items: { type: 'object' } },
              usedTraffic: { type: 'integer', format: 'int64' },
              tunnelAllowedIPs: { type: 'object', additionalProperties: { type: 'string' } },
            },
          },
        },
      },
      {
        method: 'GET',
        path: '/panel/api/clients/get/tgId/:tgId',
        summary: '按 Telegram 用户编号获取客户端。同一编号可关联多个客户端，因此返回数组。',
        params: [{ name: 'tgId', in: 'path', type: 'integer', desc: 'Telegram 数字用户编号。' }],
        response:
          '{\n  "success": true,\n  "obj": [\n    {\n      "client": { "id": 1, "email": "alice@example.com", ... },\n      "inboundIds": [3, 5],\n      "externalLinks": [],\n      "usedTraffic": 1048576\n    }\n  ]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/add',
        summary:
          '使用 JSON 一次创建客户端并关联一个或多个入站。未提供协议密钥时由服务端生成，因此调用方可以只提交通用字段。',
        description:
          '未提供的密钥由服务端补全，调用方提供的有效值不会被覆盖。重新添加已存在的 email 并提供其已存储的 subId 时，会复用 id、password、auth 和 secret，保持各入站身份一致。\n\n- VLESS/VMess：生成 UUID 作为 id。\n- Trojan：生成 password。\n- Shadowsocks：生成 password。对 2022-blake3-*，如果提供的密码无法通过 Base64 解码为对应加密方式要求的 16 或 32 字节，会替换为生成密钥，接口仍成功；如未交由服务端选择，应回读客户端确认。传统加密方式保留所有非空密码。\n- Hysteria：生成 auth。\n- mtproto：按入站前置域名生成 FakeTLS secret，未设置域名时使用 www.cloudflare.com。\n- WireGuard：privateKey/publicKey 均空时生成密钥对；仅有 privateKey 时补公钥。allowedIPs 从现有对等节点所在 /24 分配空闲 /32；无现有节点时使用 10.0.0.0/24。\n\npreSharedKey、keepAlive（WireGuard）和 adTag（mtproto）可提交，但不会自动生成。\n\nWireGuard 地址分配失败时会先扩大到所在 /16 搜索，然后返回无空闲地址错误。显式 allowedIPs 只验证不分配；与同一或其他 WireGuard/AmneziaWG 入站中其他客户端的地址或前缀重叠时拒绝。按地址范围比较，因此 10.0.0.9/24 与 10.0.0.5/32 冲突；0.0.0.0/0 和 ::/0 默认路由不占用地址。自动分配也跳过已占用前缀，关联客户端接口同样执行此验证。\n\n任何 inboundIds 不存在时整次调用在写入前拒绝。此后各入站独立并发执行，某项失败不阻止其他入站，因此 success:false 仍可能部分创建成功。错误带有入站编号，多项错误分行报告。仅所有入站成功时应用 limitHwid，修复失败后可重新调用。',
        params: [
          {
            name: 'client',
            in: 'body (json)',
            type: 'object',
            desc: '客户端字段：email、subId、id（UUID）、password、auth、flow、totalGB、expiryTime、limitIp、limitHwid、tgId（Telegram 数字用户编号，0 表示未设置）、comment、enable。协议专用字段：secret/adTag（mtproto），privateKey/publicKey/preSharedKey/allowedIPs/keepAlive（WireGuard）。',
          },
          {
            name: 'inboundIds',
            in: 'body (json)',
            type: 'integer[]',
            desc: '需要关联的入站编号，至少一个。',
          },
        ],
        body: '{\n  "client": {\n    "email": "alice@example.com",\n    "totalGB": 53687091200,\n    "expiryTime": 1735689600000,\n    "tgId": 0,\n    "limitIp": 0,\n    "limitHwid": 0,\n    "enable": true\n  },\n  "inboundIds": [3, 5]\n}',
        response: '{\n  "success": true,\n  "msg": "Client added"\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/renewalPreview',
        summary: '预览客户端自动续期日期，不保存或重置任何数据。',
        description:
          '使用面板时区及自动续期相同的日历和补齐算法。resetWeekday 为 1（周一）至 7（周日），0 禁用，不能与正数 reset 或 resetDay 同时使用。resetDay 优先于 reset。expiryTime=0 时仅建议首次截止日期，不启用续期；负数表示等待首次使用。resetMax/resetCount 模拟现有周期次数限制。预览仅供参考，不预留次数或保证节点可用。',
        params: [
          {
            name: 'expiryTime',
            in: 'body (json)',
            type: 'integer',
            desc: '当前截止时间，单位为 Unix 毫秒。0 表示无限期，负数表示首次使用后的时长。',
          },
          {
            name: 'reset',
            in: 'body (json)',
            type: 'integer',
            desc: '固定间隔，单位为天，0 表示禁用。',
          },
          {
            name: 'resetDay',
            in: 'body (json)',
            type: 'integer',
            desc: '每月日历日期 1-31，0 表示禁用。',
          },
          {
            name: 'resetWeekday',
            in: 'body (json)',
            type: 'integer',
            desc: '每周日期 1-7，分别为周一至周日，0 表示禁用。',
          },
          {
            name: 'resetMax',
            in: 'body (json)',
            type: 'integer',
            desc: '最大续期次数，0 表示不限。',
          },
          {
            name: 'resetCount',
            in: 'body (json)',
            type: 'integer',
            desc: '已消耗的续期次数，默认为 0。',
          },
        ],
        responseSchema: 'ClientRenewalPreview',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/update/:email',
        summary:
          '按 email 更新客户端并同步到所有关联入站。使用 JSON 提交需要保留的完整字段，服务器替换整条记录，不进行局部合并。',
        description:
          '各入站独立并发更新，某项失败不阻止其他项。错误带入站编号并逐行报告，因此 success:false 可能已更新其他入站。客户端记录在入站之后写入；记录写入失败时错误不带入站前缀，已完成的入站修改保留。',
        params: [
          {
            name: 'email',
            in: 'path',
            type: 'string',
            desc: '当前客户端 email，作为唯一标识。',
          },
        ],
        body: '{\n  "email": "alice@example.com",\n  "totalGB": 107374182400,\n  "expiryTime": 1767225600000,\n  "limitHwid": 2,\n  "tgId": 123456789,\n  "enable": true\n}',
        response: '{\n  "success": true,\n  "msg": "Client updated"\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/del/:email',
        summary:
          '按 email 删除客户端，从所有关联入站移除并删除流量记录；keepTraffic=1 可保留流量记录。',
        description:
          '各入站独立并发移除，某项失败不阻止其他项。错误带入站编号并逐行报告，失败时客户端记录保留，重新调用仅重试剩余项。客户端及流量记录在入站之后删除，记录删除失败时错误不带入站前缀，已完成的入站移除保留。',
        params: [
          { name: 'email', in: 'path', type: 'string', desc: '客户端 email，作为唯一标识。' },
          {
            name: 'keepTraffic',
            in: 'query',
            type: 'integer',
            desc: '设为 1 可在删除后保留 xray_client_traffic 记录。',
          },
        ],
        response: '{\n  "success": true,\n  "msg": "Client deleted"\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/:email/attach',
        summary: '将已有客户端关联到更多入站，使用 JSON 请求正文。',
        description:
          'WireGuard 客户端将已有 allowedIPs 带入新入站，不重新分配。若与目标入站其他客户端地址或前缀重叠则失败，需先释放冲突地址，完整规则见 POST /panel/api/clients/add。各入站独立处理，因此 success:false 仍可能部分关联成功。',
        params: [
          { name: 'email', in: 'path', type: 'string', desc: '客户端 email，作为唯一标识。' },
          {
            name: 'inboundIds',
            in: 'body (json)',
            type: 'integer[]',
            desc: '需要关联的入站编号。',
          },
        ],
        body: '{\n  "inboundIds": [7, 9]\n}',
        response: '{\n  "success": true\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/:email/detach',
        summary: '将客户端从指定入站解除关联，保留客户端本身。',
        description:
          '各入站独立并发解除关联，某项失败不阻止其他项。每项错误带入站编号并逐行报告，因此 success:false 仍可能已解除其他关联。本操作只写入站数据，所有错误均带入站前缀。',
        params: [
          { name: 'email', in: 'path', type: 'string', desc: '客户端 email，作为唯一标识。' },
          {
            name: 'inboundIds',
            in: 'body (json)',
            type: 'integer[]',
            desc: '需要解除关联的入站编号。',
          },
        ],
        body: '{\n  "inboundIds": [5]\n}',
        response: '{\n  "success": true\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/:email/externalLinks',
        summary:
          '完整替换客户端的外部链接和外部订阅。禁用项保留以供编辑，但不生成到订阅中；所属客户端禁用或过期也会停止后续输出。已被应用导入的凭据需由外部服务撤销后才会失效。',
        params: [
          { name: 'email', in: 'path', type: 'string', desc: '客户端 email，作为唯一标识。' },
          {
            name: 'externalLinks',
            in: 'body',
            type: 'object[]',
            desc: '完整替换列表，每项支持 {kind, value, remark, enable, expiryTime, namePrefix}。kind=link 时 value 必须为支持的 vless://、vmess://、trojan://、ss://、hysteria2:// 或 wireguard:// 等分享链接，remark 可覆盖节点名。kind=subscription 时 value 为 HTTP(S) 订阅地址，namePrefix 加在获取到的节点名前。enable 默认 true；禁用或过期项保留存储但不输出。expiryTime 为非负 Unix 毫秒时间戳，0 表示此链接无独立到期时间；所属客户端启用状态和期限仍适用。保存时按 kind+value 匹配，写入忽略 id。lastFetchAt/lastFetchError 为 GET 返回的只读状态。',
          },
        ],
        body: '{\n  "externalLinks": [\n    { "kind": "link", "value": "vless://uuid@host:443?...#srv", "remark": "DE", "enable": true, "expiryTime": 0 },\n    { "kind": "subscription", "value": "https://provider.example/sub/abc", "remark": "Provider", "enable": false, "expiryTime": 1767225600000, "namePrefix": "[zjh] " }\n  ]\n}',
        response: '{\n  "success": true\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/resetAllTraffics',
        summary: '全局清零所有客户端上传和下载计数，不修改额度和期限。确有计数变化时重启 Xray。',
        response: '{\n  "success": true\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/delDepleted',
        summary:
          '删除额度耗尽（未开启重置时 used >= total）或已过期的客户端，返回删除数量；涉及运行中入站时重启 Xray。',
        response: '{\n  "success": true,\n  "obj": {\n    "deleted": 0\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/delOrphans',
        summary:
          '删除未关联任何入站的客户端及其流量记录、IP 日志、HWID 设备和外部链接。适合清理入站删除后遗留的客户端，返回删除数量，不可撤销。',
        response: '{\n  "success": true,\n  "obj": {\n    "deleted": 0\n  }\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/clients/export',
        summary:
          '以 {client, inboundIds, traffic} 数组导出所有客户端，可直接回导 /import。traffic 包含 up、down、resetCount、lastOnline、lastSubFetch，无流量记录时省略；额度仍在 client.totalGB。未关联入站的客户端也会导出，inboundIds 为空。界面可复制或下载，API 在 obj 中返回数组。',
        response:
          '{\n  "success": true,\n  "obj": [\n    {\n      "client": {\n        "email": "alice@example.com",\n        "id": "...",\n        "totalGB": 53687091200,\n        "expiryTime": 0,\n        "limitHwid": 2,\n        "enable": true,\n        "subId": "..."\n      },\n      "inboundIds": [7, 9],\n      "traffic": {\n        "up": 1048576,\n        "down": 2097152,\n        "resetCount": 0,\n        "lastOnline": 1735680000000\n      }\n    }\n  ]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/import',
        summary:
          '通过 JSON 正文 {"data":"<json>"} 导入 /export 生成的 [{client,inboundIds,traffic}] 字符串数组。有 inboundIds 时创建并关联，空列表时恢复为独立客户端。可选 traffic 仅为新建客户端恢复计数。已有 email 不覆盖，列入 skipped，现有计数保留。涉及运行中入站时最后统一重启 Xray；计数恢复失败仍返回 success=false，已创建的客户端保留。',
        body: '{\n  "data": "[{\\"client\\":{\\"email\\":\\"alice@example.com\\",\\"enable\\":true},\\"inboundIds\\":[7]}]"\n}',
        response:
          '{\n  "success": true,\n  "obj": {\n    "created": 2,\n    "skipped": [\n      { "email": "alice@example.com", "reason": "email already in use: alice@example.com" }\n    ]\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/bulkAdjust',
        summary:
          '批量调整期限或流量额度，addDays/addBytes 可为负数。无限期或无限流量的对应字段跳过，绝不转为有限值。仅因耗尽自动禁用的客户端在调整后恢复可用时会在本地及节点重新启用；手动禁用或仍耗尽的客户端不变。flow 可省略或留空保留，none 清除，支持的入站可设 xtls-rprx-vision 或 xtls-rprx-vision-udp443。limitHwid 设置设备上限，0 为无限；adTag 设置 MTProto 赞助频道，none 清除。返回调整数量及逐客户端跳过原因。',
        body: '{\n  "emails": ["alice", "bob"],\n  "addDays": 30,\n  "addBytes": 53687091200,\n  "flow": "xtls-rprx-vision",\n  "limitHwid": 2,\n  "adTag": "0123456789abcdef0123456789abcdef"\n}',
        response:
          '{\n  "success": true,\n  "obj": {\n    "adjusted": 2,\n    "skipped": [\n      { "email": "carol", "reason": "unlimited expiry" }\n    ]\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/bulkEnable',
        summary:
          '批量启用客户端。按入站分组，每个入站一次读改写，并向本地或节点 Xray 添加用户。已过期或额度耗尽时仅翻转开关，下次流量检查会再次禁用。返回修改数量及逐客户端跳过原因。',
        body: '{\n  "emails": ["alice", "bob"]\n}',
        response:
          '{\n  "success": true,\n  "obj": {\n    "changed": 2,\n    "skipped": [\n      { "email": "carol", "reason": "client not found" }\n    ]\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/bulkDisable',
        summary:
          '批量禁用客户端。按入站分组，每个入站一次读改写，并从本地或节点 Xray 移除用户。返回修改数量及逐客户端跳过原因。',
        body: '{\n  "emails": ["alice", "bob"]\n}',
        response:
          '{\n  "success": true,\n  "obj": {\n    "changed": 2,\n    "skipped": [\n      { "email": "carol", "reason": "client not found" }\n    ]\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/bulkDel',
        summary:
          '批量删除客户端，服务器按顺序处理，使每次删除看到前一次已提交状态，避免并发删除竞争。keepTraffic=true 可保留 xray_client_traffic 记录。',
        body: '{\n  "emails": ["alice", "bob"],\n  "keepTraffic": false\n}',
        response:
          '{\n  "success": true,\n  "obj": {\n    "deleted": 2,\n    "skipped": [\n      { "email": "carol", "reason": "client not found" }\n    ]\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/bulkCreate',
        summary:
          '批量创建客户端，JSON 正文为 /add 同形的 {client,inboundIds} 数组。顺序处理，逐客户端返回失败或跳过原因（如标识重复）。涉及运行中入站时最后统一重启一次 Xray。',
        body: '[\n  {\n    "client": {\n      "email": "alice@example.com",\n      "totalGB": 53687091200,\n      "expiryTime": 0,\n      "limitHwid": 2,\n      "enable": true\n    },\n    "inboundIds": [7]\n  },\n  {\n    "client": {\n      "email": "bob@example.com",\n      "totalGB": 53687091200,\n      "expiryTime": 0,\n      "limitHwid": 0,\n      "enable": true\n    },\n    "inboundIds": [7, 9]\n  }\n]',
        response:
          '{\n  "success": true,\n  "obj": {\n    "created": 2,\n    "skipped": [\n      { "email": "alice@example.com", "reason": "email already in use" }\n    ]\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/groups/bulkAdd',
        summary:
          '批量将客户端加入分组，在同一事务中更新 clients.group_name 和各所属入站配置中的对应项。不存在的分组自动持久创建。清除分组请使用 /groups/bulkRemove。',
        body: '{\n  "emails": ["alice", "bob"],\n  "group": "customer-a"\n}',
        response: '{\n  "success": true,\n  "obj": {\n    "affected": 2\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/groups/bulkRemove',
        summary:
          '批量清除客户端分组，与 /groups/bulkAdd 相反。客户端保留，仅清除 clients.group_name 及各所属入站配置中的分组标签。所有成员移出后分组变为空组。',
        body: '{\n  "emails": ["alice", "bob"]\n}',
        response: '{\n  "success": true,\n  "obj": {\n    "affected": 2\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/bulkAttach',
        summary:
          '批量将已有客户端关联到多个入站，保留 email/UUID/password/subId 身份及共享流量记录。每个目标入站一次 AddInboundClient 添加全部用户，已存在者列入 skipped。逐客户端返回 attached/skipped/errors，涉及运行中入站时统一重启一次 Xray。',
        params: [
          {
            name: 'emails',
            in: 'body (json)',
            type: 'string[]',
            desc: '需要关联的已有客户端 email 列表。',
          },
          {
            name: 'inboundIds',
            in: 'body (json)',
            type: 'integer[]',
            desc: '所有客户端需要关联的目标入站编号。',
          },
        ],
        body: '{\n  "emails": ["alice", "bob"],\n  "inboundIds": [7, 9]\n}',
        response:
          '{\n  "success": true,\n  "obj": {\n    "attached": ["alice", "bob"],\n    "skipped": ["bob"],\n    "errors": []\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/bulkDetach',
        summary:
          '批量解除多个客户端与多个入站的关联。仅处理当前关联与请求入站的交集，不存在的关联不做操作。没有匹配关联的客户端列入 skipped，解除后无入站的客户端记录仍保留，完整删除请使用 bulkDel。逐客户端返回 detached/skipped/errors，涉及运行中入站时统一重启一次 Xray。',
        params: [
          {
            name: 'emails',
            in: 'body (json)',
            type: 'string[]',
            desc: '需要解除关联的已有客户端 email 列表。',
          },
          {
            name: 'inboundIds',
            in: 'body (json)',
            type: 'integer[]',
            desc: '需要与客户端解除关联的入站编号。',
          },
        ],
        body: '{\n  "emails": ["alice", "bob"],\n  "inboundIds": [7, 9]\n}',
        response:
          '{\n  "success": true,\n  "obj": {\n    "detached": ["alice", "bob"],\n    "skipped": [],\n    "errors": []\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/bulkResetTraffic',
        summary:
          '批量清零客户端上传和下载计数。逐个执行重置，在关联入站重新启用客户端并同步至 Xray 或远程节点，返回成功重置数量。',
        body: '{\n  "emails": ["alice", "bob"]\n}',
        response: '{\n  "success": true,\n  "obj": {\n    "affected": 2\n  }\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/clients/groups',
        summary:
          '列出客户端分组及成员数。合并 client_groups 中的持久分组（包括空组）和客户端当前 group_name，忽略大小写按名称排序。',
        response:
          '{\n  "success": true,\n  "obj": [\n    { "name": "customer-a", "clientCount": 5 },\n    { "name": "internal", "clientCount": 0 }\n  ]\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/clients/groups/:name/emails',
        summary:
          '仅返回指定分组的客户端 email 列表，便于批量操作整个分组，无需获取完整客户端记录。',
        params: [{ name: 'name', in: 'path', type: 'string', desc: '分组名称，需进行 URL 编码。' }],
        response: '{\n  "success": true,\n  "obj": ["alice", "bob", "carol"]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/groups/create',
        summary: '创建空分组，创建后即可在客户端表单和筛选中选择。名称已存在时返回错误。',
        body: '{\n  "name": "customer-a"\n}',
        response: '{\n  "success": true,\n  "obj": {\n    "name": "customer-a"\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/groups/rename',
        summary:
          '在同一事务中重命名分组，更新 client_groups、所有匹配客户端的 clients.group_name 及所属入站配置，返回更新的客户端数量。',
        body: '{\n  "oldName": "customer-a",\n  "newName": "tier-1"\n}',
        response: '{\n  "success": true,\n  "obj": {\n    "affected": 5\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/groups/delete',
        summary:
          '删除分组并清除所有匹配客户端的分组标签，更新客户端表及入站配置。保留客户端本身，如需删除客户端应按分组筛选后调用 /bulkDel。返回清除标签的客户端数量。',
        body: '{\n  "name": "customer-a"\n}',
        response: '{\n  "success": true,\n  "obj": {\n    "affected": 5\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/groups/resetTraffic',
        summary:
          '仅重置分组页显示的分组流量，以成员当前上传下载总和为基线归零，不修改各客户端计数和额度，不重启 Xray。仅存在派生标签时会创建 client_groups 记录。',
        body: '{\n  "name": "customer-a"\n}',
        response: '{\n  "success": true,\n  "obj": {\n    "name": "customer-a"\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/resetTraffic/:email',
        summary:
          '清零单个客户端的上传下载计数，在所有关联入站重新启用并同步至 Xray 或远程节点，使耗尽用户立即恢复连接。',
        params: [{ name: 'email', in: 'path', type: 'string', desc: '客户端 email。' }],
      },
      {
        method: 'POST',
        path: '/panel/api/clients/updateTraffic/:email',
        summary: '手动调整客户端上传和下载计数，适合从外部计量系统迁移。',
        params: [{ name: 'email', in: 'path', type: 'string', desc: '客户端 email。' }],
        body: '{\n  "upload": 1073741824,\n  "download": 5368709120\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/ips/:email',
        summary: '列出使用此客户端凭据连接过的来源 IP，返回 ip（时间戳）字符串数组。',
        params: [{ name: 'email', in: 'path', type: 'string', desc: '客户端 email。' }],
      },
      {
        method: 'POST',
        path: '/panel/api/clients/clearIps/:email',
        summary: '清空客户端已记录的 IP 列表。',
        params: [{ name: 'email', in: 'path', type: 'string', desc: '客户端 email。' }],
      },
      {
        method: 'POST',
        path: '/panel/api/clients/hwids/:email',
        summary: '列出客户端已注册的 HWID 设备及短指纹，不公开完整散列值。',
        params: [{ name: 'email', in: 'path', type: 'string', desc: '客户端 email。' }],
        response:
          '{\n  "success": true,\n  "obj": [\n    {\n      "id": 1,\n      "firstSeen": 1735000000000,\n      "lastSeen": 1735100000000,\n      "userAgent": "Happ/1.0",\n      "deviceOs": "android",\n      "osVersion": "15",\n      "deviceModel": "Pixel 9",\n      "fingerprint": "6ad17c93e821"\n    }\n  ]\n}',
      },
      {
        method: 'DELETE',
        path: '/panel/api/clients/hwids/:email',
        summary: '清除客户端所有已注册 HWID 设备，允许新设备重新注册。',
        params: [{ name: 'email', in: 'path', type: 'string', desc: '客户端 email。' }],
      },
      {
        method: 'DELETE',
        path: '/panel/api/clients/hwids/:email/:id',
        summary: '按编号删除单个已注册 HWID 设备，释放一个设备名额。',
        params: [
          { name: 'email', in: 'path', type: 'string', desc: '客户端 email。' },
          { name: 'id', in: 'path', type: 'number', desc: '设备编号，由列表接口返回。' },
        ],
      },
      {
        method: 'POST',
        path: '/panel/api/clients/onlines',
        summary: '返回心跳窗口内活动客户端的 email 列表，跨所有节点去重。',
        response: '{\n  "success": true,\n  "obj": ["user1", "user2"]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/onlinesByGuid',
        summary:
          '按实际承载客户端的节点 panelGuid 返回在线客户端。各层节点和本地面板均使用自己的 GUID，使入站页将状态归属实际节点而非中间同步节点。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "a1b2-...": ["user1"],\n    "c3d4-...": ["user1", "user2"]\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/clientIpsByGuid',
        summary:
          '按观测到来源 IP 的节点 panelGuid 分组，主面板据此使用节点看到的真实访客 IP 统计和实施客户端 IP 限制，避免使用中间面板地址。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "a1b2-...": {\n      "user1": [\n        { "ip": "1.2.3.4", "timestamp": 1700000000 }\n      ]\n    }\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/activeInbounds',
        summary:
          '按承载节点 panelGuid 返回心跳窗口内有流量的入站标签。配合 onlinesByGuid，只将多入站客户端实际使用的入站标为在线。未上报逐入站活动的节点不包含在结果中。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "a1b2-...": ["in-443-tcp", "in-8443-tcp"]\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/lastOnline',
        summary: '客户端 email 到最后在线 Unix 时间戳的映射。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "user1": 1700000000,\n    "user2": 1699999000\n  }\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/clients/traffic/:email',
        summary: '按客户端 email 获取流量计数。',
        params: [
          {
            name: 'email',
            in: 'path',
            type: 'string',
            desc: '客户端 email，在面板内唯一。',
          },
        ],
        responseSchema: 'ClientTraffic',
      },
      {
        method: 'GET',
        path: '/panel/api/clients/subLinks/:subId',
        summary:
          '返回匹配订阅标识的客户端协议链接（vless://、vmess://、trojan://、ss://、hysteria://、hy2://）。与 subPath 接口结果相同，但返回 JSON 数组而非 Base64。有 externalProxy 时每个外部代理输出一个链接，无已启用客户端时返回空数组。',
        params: [
          {
            name: 'subId',
            in: 'path',
            type: 'string',
            desc: '客户端 subId 字段中的订阅标识。',
          },
        ],
        response:
          '{\n  "success": true,\n  "obj": [\n    "vless://uuid@host:443?security=reality&...#user1",\n    "vmess://eyJ2IjoyLC..."\n  ]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/clients/happLink/:id',
        summary:
          '启用 Happ 链接生成时，使用当前客户端订阅地址在本地生成全新 crypt5 链接。面板限制来源地址为 8192 个 UTF-8 字节，这是面板资源限制，不是 Happ 客户端上限。超长返回 success:false、msg:happ_source_too_long、obj:null。地址不会发往外部生成服务，结果也不会存储或复用。',
        params: [{ name: 'id', in: 'path', type: 'integer', desc: '客户端记录的稳定编号。' }],
        responseSchema: 'HappLinkResult',
      },
      {
        method: 'GET',
        path: '/panel/api/clients/links/:email',
        summary:
          '返回客户端在所有关联入站的链接，每个公布端点一个。端点优先级为入站托管主机、externalProxy、入站自身地址。支持 vmess、vless、trojan、shadowsocks、hysteria、mtproto；socks、http、mixed、wireguard、dokodemo、tunnel 等无链接格式的协议不输出。',
        params: [
          { name: 'email', in: 'path', type: 'string', desc: '客户端 email，作为唯一标识。' },
        ],
        response:
          '{\n  "success": true,\n  "obj": [\n    "vless://uuid@host:443?...#user1"\n  ]\n}',
      },
    ],
  },

  {
    id: 'nodes',
    title: '节点管理',
    description: '管理作为主面板节点的远程 3x-ui 面板，接口均位于 /panel/api/nodes。',
    endpoints: [
      {
        method: 'GET',
        path: '/panel/api/nodes/list',
        summary: '列出所有已配置节点，包括连接信息、健康状态及最近心跳更新。',
        responseSchema: 'NodeView',
        responseSchemaArray: true,
      },
      {
        method: 'POST',
        path: '/panel/api/nodes/mtls/ca',
        summary:
          '返回面板的节点认证 CA 公共 PEM 证书，供粘贴到节点的双向 TLS 信任设置。首次调用生成 CA 和主面板客户端证书，节点需设置 tlsVerifyMode=mtls。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "caCert": "-----BEGIN CERTIFICATE-----\\n...\\n-----END CERTIFICATE-----\\n"\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/nodes/mtls/trustCA',
        summary:
          '设置当前面板作为节点时信任的入站 API 客户端证书 CA，内容来自管理面板 nodes/mtls/ca。空 caCert 表示禁用，非空值必须为 PEM 证书，下次面板重启后生效。',
        body: '{\n  "caCert": "-----BEGIN CERTIFICATE-----\\n...\\n-----END CERTIFICATE-----\\n"\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/nodes/mtls/reloadClient',
        summary:
          '验证已存储的主面板双向 TLS 客户端凭据并失效缓存连接。各传输关闭旧空闲连接池，在下次请求前使用轮换后的证书重建。',
      },
      {
        method: 'GET',
        path: '/panel/api/nodes/get/:id',
        summary: '按编号获取单个节点。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '节点编号。' }],
        responseSchema: 'NodeView',
      },
      {
        method: 'GET',
        path: '/panel/api/nodes/webCert/:id',
        summary:
          '通过代理读取节点自身 Web TLS 证书和密钥文件路径。用于入站表单“从面板设置证书”，确保节点入站使用节点本机路径。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '节点编号。' }],
        response:
          '{\n  "success": true,\n  "obj": {\n    "webCertFile": "/root/cert/example.com/fullchain.pem",\n    "webKeyFile": "/root/cert/example.com/privkey.pem"\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/nodes/add',
        summary:
          '注册远程节点，提交地址、只写 apiToken 及可选 remark/allowPrivateAddress。响应仅通过 hasApiToken 表示是否设置令牌，不返回令牌内容。',
        body: '{\n  "name": "de-fra-1",\n  "remark": "",\n  "scheme": "https",\n  "address": "node1.example.com",\n  "port": 2053,\n  "basePath": "/",\n  "apiToken": "abcdef...",\n  "clearApiToken": false,\n  "enable": true,\n  "allowPrivateAddress": false\n}',
        responseSchema: 'NodeView',
      },
      {
        method: 'POST',
        path: '/panel/api/nodes/update/:id',
        summary:
          '替换节点连接信息。apiToken 只写，省略或空字符串时保留原令牌，clearApiToken=true 时清除。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '节点编号。' }],
        body: '{\n  "name": "de-fra-1",\n  "remark": "",\n  "scheme": "https",\n  "address": "node1.example.com",\n  "port": 2053,\n  "basePath": "/",\n  "apiToken": "",\n  "clearApiToken": false,\n  "enable": true,\n  "allowPrivateAddress": false\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/nodes/del/:id',
        summary: '删除节点，不自动迁移关联入站。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '节点编号。' }],
      },
      {
        method: 'POST',
        path: '/panel/api/nodes/setEnable/:id',
        summary: '暂停或恢复与此节点的流量同步。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '节点编号。' }],
        body: '{\n  "enable": true\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/nodes/test',
        summary: '使用请求正文中的连接信息测试节点，不保存，返回与已注册节点相同的心跳快照。',
        body: '{\n  "scheme": "https",\n  "address": "node1.example.com",\n  "port": 2053,\n  "basePath": "/",\n  "apiToken": "abcdef..."\n}',
        responseSchema: 'ProbeResultUI',
      },
      {
        method: 'POST',
        path: '/panel/api/nodes/certFingerprint',
        summary:
          '通过 HTTPS 连接节点但不验证证书，返回叶证书的 Base64 SHA-256，供节点新增或编辑时固定自签名证书。请求正文与 /test 相同。',
        body: '{\n  "scheme": "https",\n  "address": "node1.example.com",\n  "port": 2053,\n  "basePath": "/"\n}',
        response: '{\n  "success": true,\n  "obj": "k3b1...base64-sha256...="\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/nodes/inbounds',
        summary: '使用尚未保存的节点连接信息，列出可选择导入的远程入站。',
        body: '{\n  "name": "de-fra-1",\n  "scheme": "https",\n  "address": "node1.example.com",\n  "port": 2053,\n  "basePath": "/",\n  "apiToken": "abcdef..."\n}',
        response:
          '{\n  "success": true,\n  "obj": [\n    { "tag": "inbound-443", "remark": "VLESS", "protocol": "vless", "port": 443 }\n  ]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/nodes/probe/:id',
        summary: '探测已存在的节点并更新健康状态缓存。',
        params: [{ name: 'id', in: 'path', type: 'number', desc: '节点编号。' }],
      },
      {
        method: 'POST',
        path: '/panel/api/nodes/updatePanel',
        summary:
          '对指定节点调用官方面板自更新程序，下载最新版本并重启。仅处理已启用且在线节点，其余列为跳过。dev:true 使用随提交滚动发布的开发渠道，否则使用最新稳定版。返回逐节点结果。',
        body: '{\n  "ids": [1, 2, 3],\n  "dev": false\n}',
        response:
          '{\n  "success": true,\n  "obj": [\n    { "id": 1, "name": "de-1", "ok": true },\n    { "id": 2, "name": "fr-1", "ok": false, "error": "node is offline" }\n  ]\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/nodes/history/:id/:metric/:bucket',
        summary: '返回单个节点的聚合指标历史，结构与 /server/history 相同。',
        params: [
          { name: 'id', in: 'path', type: 'number', desc: '节点编号。' },
          { name: 'metric', in: 'path', type: 'string', desc: '指标：cpu 或 mem。' },
          {
            name: 'bucket',
            in: 'path',
            type: 'number',
            desc: '统计分桶大小，单位为秒，允许 2、30、60、120、180、300。',
          },
        ],
      },
    ],
  },

  {
    id: 'hosts',
    title: '主机管理',
    description:
      '管理入站主机覆盖配置。每个已启用主机使用自身地址、端口和 TLS 生成额外订阅链接，替代旧 externalProxy 数组。接口位于 /panel/api/hosts。',
    endpoints: [
      {
        method: 'GET',
        path: '/panel/api/hosts/list',
        summary: '列出所有入站的主机，先按入站分组，再按排序值排列。',
        responseSchema: 'HostGroup',
        responseSchemaArray: true,
      },
      {
        method: 'GET',
        path: '/panel/api/hosts/raw',
        summary: '列出所有主机记录及数字编号，用于按编号选择，例如逐客户端主机规则覆盖。',
        responseSchema: 'Host',
        responseSchemaArray: true,
      },
      {
        method: 'GET',
        path: '/panel/api/hosts/get/:groupId',
        summary: '按分组编号获取主机组。',
        params: [{ name: 'groupId', in: 'path', type: 'string', desc: '主机分组编号。' }],
        responseSchema: 'HostGroup',
      },
      {
        method: 'GET',
        path: '/panel/api/hosts/byInbound/:inboundId',
        summary: '获取单个入站的主机，按主机组分组。',
        params: [{ name: 'inboundId', in: 'path', type: 'number', desc: '入站编号。' }],
        responseSchema: 'HostGroup',
        responseSchemaArray: true,
      },
      {
        method: 'GET',
        path: '/panel/api/hosts/tags',
        summary: '返回所有主机标签，去重并排序。',
        response: '{\n  "success": true,\n  "obj": ["CDN", "EU", "FAST"]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/hosts/add',
        summary: '在入站上创建主机组。',
        body: '{\n  "inboundIds": [1],\n  "remark": "cdn-front",\n  "hosts": ["cdn.example.com"],\n  "port": 8443,\n  "security": "same",\n  "tags": ["CDN"]\n}',
        responseSchema: 'Host',
        responseSchemaArray: true,
      },
      {
        method: 'POST',
        path: '/panel/api/hosts/update/:groupId',
        summary: '替换主机组内容。',
        params: [{ name: 'groupId', in: 'path', type: 'string', desc: '主机分组编号。' }],
        body: '{\n  "inboundIds": [1],\n  "remark": "cdn-front",\n  "hosts": ["cdn.example.com"],\n  "port": 8443,\n  "security": "same",\n  "tags": ["CDN"]\n}',
        responseSchema: 'Host',
        responseSchemaArray: true,
      },
      {
        method: 'POST',
        path: '/panel/api/hosts/del/:groupId',
        summary: '删除主机组。',
        params: [{ name: 'groupId', in: 'path', type: 'string', desc: '主机分组编号。' }],
      },
      {
        method: 'POST',
        path: '/panel/api/hosts/setEnable/:groupId',
        summary: '启用或禁用主机组。',
        params: [{ name: 'groupId', in: 'path', type: 'string', desc: '主机分组编号。' }],
        body: '{\n  "enable": true\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/hosts/reorder',
        summary:
          '按数组中的 groupId 位置设置主机组顺序。编号必须唯一且对应已存在分组，省略的分组按原顺序排在后面。',
        body: '{\n  "ids": ["abc-123", "def-456"]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/hosts/bulk/add',
        summary: '将主机组添加到入站，与 /add 相同。',
        body: '{\n  "inboundIds": [1, 2],\n  "hosts": ["cdn.example.com", "cdn2.example.com:443"],\n  "remark": "Cloudflare CDN",\n  "port": 0,\n  "security": "same",\n  "isDisabled": false\n}',
        responseSchema: 'Host',
        responseSchemaArray: true,
      },
      {
        method: 'POST',
        path: '/panel/api/hosts/bulk/setEnable',
        summary: '批量启用或禁用主机组。',
        body: '{\n  "ids": ["abc-123", "def-456"],\n  "enable": false\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/hosts/bulk/del',
        summary: '批量删除主机组。',
        body: '{\n  "ids": ["abc-123", "def-456"]\n}',
      },
    ],
  },

  {
    id: 'backup',
    title: '备份',
    description: '与已配置 Telegram 机器人交互的操作。',
    endpoints: [
      {
        method: 'POST',
        path: '/panel/api/backuptotgbot',
        summary: '向所有已配置为管理员接收方的 Telegram 会话发送最新数据库备份，无请求正文或参数。',
      },
    ],
  },

  {
    id: 'settings',
    title: '面板设置',
    description: '面板配置及管理员凭据，接口位于 /panel/api/setting，需要登录会话或 Bearer 令牌。',
    endpoints: [
      {
        method: 'POST',
        path: '/panel/api/setting/all',
        summary:
          '返回全部面板设置，包括 Web 服务、Telegram、订阅、安全和 LDAP，即设置页编辑的完整 JSON。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "webPort": 2053,\n    "webCertFile": "",\n    "webKeyFile": "",\n    "webBasePath": "/",\n    "subPort": 10882,\n    "subPath": "/sub/",\n    "subClashAutoDetect": false,\n    "subClashUserAgentRegex": "",\n    "subJsonEnable": false,\n    "subJsonAutoDetect": false,\n    "subJsonAlwaysArray": false,\n    "subJsonUserAgentRegex": "",\n    "subJsonPath": "/json/",\n    "subJsonURI": "https://sub.example.com/json/",\n    "subClashEnable": true,\n    "subClashPath": "/clash/",\n    "subClashURI": "https://sub.example.com/clash/",\n    "tgBotEnable": false,\n    "tgBotToken": "",\n    ...\n  }\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/defaultSettings',
        summary: '按当前请求主机计算默认设置，可用于预览全新安装的默认值。',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/factoryDefaults',
        summary:
          '返回浏览器可访问设置的出厂默认值，供区分已存储值和回退值。不包含安装专属材料（secret、panelGuid、双向 TLS 密钥）或凭据字段。',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/update',
        summary:
          '一次保存全部设置，结构与 /all 返回值相同。无效端口、缺少证书配对等问题在写入前拒绝。',
        body: '{\n  "webPort": 2053,\n  "webBasePath": "/",\n  "subPort": 10882,\n  "subPath": "/sub/",\n  "subClashAutoDetect": false,\n  "subClashUserAgentRegex": "",\n  "subJsonEnable": false,\n  "subJsonAutoDetect": false,\n  "subJsonAlwaysArray": false,\n  "subJsonUserAgentRegex": "",\n  "subJsonPath": "/json/",\n  "subJsonURI": "https://sub.example.com/json/",\n  "subClashEnable": true,\n  "subClashPath": "/clash/",\n  "subClashURI": "https://sub.example.com/clash/",\n  "tgBotEnable": false,\n  ...\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/validateRegex',
        summary: '使用服务端 Go RE2 编译器验证正则表达式，不保存。',
        body: '{\n  "regex": "(?m)^general-purpose$"\n}',
        response: '{\n  "success": true,\n  "msg": ""\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/updateUser',
        summary: '修改面板管理员用户名和密码，需用当前凭据验证；成功后以新凭据刷新会话。',
        params: [
          { name: 'oldUsername', in: 'body', type: 'string', desc: '当前管理员用户名。' },
          { name: 'oldPassword', in: 'body', type: 'string', desc: '当前管理员密码。' },
          { name: 'newUsername', in: 'body', type: 'string', desc: '新的用户名。' },
          { name: 'newPassword', in: 'body', type: 'string', desc: '新的密码。' },
        ],
        body: '{\n  "oldUsername": "admin",\n  "oldPassword": "admin",\n  "newUsername": "newadmin",\n  "newPassword": "newpass"\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/restartPanel',
        summary: '等待三秒后重启整个 3x-ui 进程。连接立即断开，面板通常在约 5-10 秒后恢复。',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/testSmtp',
        summary: '分阶段测试 SMTP 连接，包括连接、认证和发送，返回包含阶段及消息的结构化结果。',
        response:
          '{\n  "success": true,\n  "stage": "send",\n  "msg": "Test email sent successfully"\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/testTgBot',
        summary: '向已配置的会话发送测试消息，以测试 Telegram 机器人连接。',
        response: '{\n  "success": true,\n  "msg": "Test message sent to Telegram"\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/tgBotCapabilities',
        summary: '返回机器人能力。内联模式由 BotFather 控制，未开启时面板会显示提示。',
        responseSchema: 'TgBotCapabilities',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/testDiscord',
        summary: '向已配置频道发送测试嵌入消息，以测试 Discord 机器人连接。',
        response: '{\n  "success": true,\n  "msg": "Test notification sent successfully"\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/setting/getDefaultJsonConfig',
        summary: '返回当前面板版本自带的默认 Xray JSON 配置模板。',
      },
    ],
  },

  {
    id: 'api-tokens',
    title: 'API 令牌',
    description:
      '管理程序调用使用的 Bearer 权限令牌。admin 可操作全部面板 API；monitor 仅读取状态和指标；node-sync 管理节点配置但不能更新面板；node-admin 额外允许面板自更新。令牌可设置到期时间，以 SHA-256 存储，明文仅创建时返回一次。',
    endpoints: [
      {
        method: 'GET',
        path: '/panel/api/setting/apiTokens',
        summary: '列出所有 API 令牌（包括禁用项），仅返回元数据，不返回令牌值。',
        response:
          '{\n  "success": true,\n  "obj": [\n    {\n      "id": 1,\n      "name": "central-panel-a",\n      "enabled": true,\n      "createdAt": 1736000000,\n      "scope": "node-admin",\n      "expiresAt": 0\n    }\n  ]\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/apiTokens/create',
        summary: '生成指定权限的 API 令牌。服务端生成的明文仅返回一次，存储为散列值。',
        params: [
          {
            name: 'name',
            in: 'body',
            type: 'string',
            desc: '便于识别的名称，例如 central-panel-a。',
          },
          {
            name: 'scope',
            in: 'body',
            type: 'string',
            desc: '权限：admin（默认）、monitor、node-sync 或 node-admin。node-admin 包含 node-sync 权限，并允许启动面板更新。',
            enum: ['admin', 'monitor', 'node-sync', 'node-admin'],
            optional: true,
          },
          {
            name: 'expiresAt',
            in: 'body',
            type: 'number',
            desc: '未来的 Unix 毫秒时间戳，0 表示永不过期。',
            optional: true,
          },
        ],
        body: '{\n  "name": "central-panel-a",\n  "scope": "node-admin",\n  "expiresAt": 1798761600000\n}',
        responseSchema: 'ApiTokenView',
        errorResponse:
          '{\n  "success": false,\n  "msg": "a token with that name already exists"\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/apiTokens/delete/:id',
        summary: '永久删除令牌，使用该令牌的调用方立即无法通过身份验证。',
        params: [
          { name: 'id', in: 'path', type: 'number', desc: '令牌记录编号。' },
          {
            name: 'expectedScope',
            in: 'body',
            type: 'string',
            desc: '操作员预期的已存储权限范围。',
          },
        ],
        body: '{\n  "expectedScope": "node-sync"\n}',
        response: '{\n  "success": true\n}',
      },
      {
        method: 'POST',
        path: '/panel/api/setting/apiTokens/setEnabled/:id',
        summary: '切换令牌启用状态，保留记录。禁用后，下一次请求即被 checkAPIAuth 拒绝。',
        params: [
          { name: 'id', in: 'path', type: 'number', desc: '令牌记录编号。' },
          { name: 'enabled', in: 'body', type: 'boolean', desc: '新的启用状态。' },
          {
            name: 'expectedScope',
            in: 'body',
            type: 'string',
            desc: '操作员预期的已存储权限范围。',
          },
        ],
        body: '{\n  "enabled": false,\n  "expectedScope": "node-sync"\n}',
        response: '{\n  "success": true\n}',
      },
    ],
  },

  {
    id: 'xray-settings',
    title: 'Xray 设置',
    description:
      'Xray 配置模板、出站管理、Warp/Nord/PIA 集成及配置测试，接口位于 /panel/api/xray。',
    endpoints: [
      {
        method: 'POST',
        path: '/panel/api/xray/',
        summary:
          '一次返回 Xray 配置模板（JSON 字符串）、可用入站标签、客户端反向代理标签及出站测试地址。',
        response:
          '{\n  "success": true,\n  "obj": {\n    "xraySetting": "{...raw xray config...}",\n    "inboundTags": "[\\"in-443-tcp\\"]",\n    "clientReverseTags": "[]",\n    "outboundTestUrl": "https://www.google.com/generate_204"\n  }\n}',
      },
      {
        method: 'GET',
        path: '/panel/api/xray/getDefaultJsonConfig',
        summary: '返回面板自带的默认 Xray 配置，与 /panel/api/setting/getDefaultJsonConfig 相同。',
      },
      {
        method: 'GET',
        path: '/panel/api/xray/getOutboundsTraffic',
        summary: '返回所有出站的流量统计，每项包含 up/down/total 计数。',
      },
      {
        method: 'GET',
        path: '/panel/api/xray/getXrayResult',
        summary: '返回最近的 Xray 标准输出和错误输出，用于检查启动错误或运行警告。',
      },
      {
        method: 'POST',
        path: '/panel/api/xray/update',
        summary: '保存 Xray JSON 配置模板，可同时保存出站测试地址，两者均通过表单字段发送。',
        params: [
          {
            name: 'xraySetting',
            in: 'body (form)',
            type: 'string',
            desc: '完整 Xray JSON 配置模板。',
          },
          {
            name: 'outboundTestUrl',
            in: 'body (form)',
            type: 'string',
            desc: '出站连通性测试地址，默认为 https://www.google.com/generate_204。',
            optional: true,
          },
        ],
      },
      {
        method: 'POST',
        path: '/panel/api/xray/warp/:action',
        summary: '管理 Cloudflare WARP 集成，通过 action 参数选择操作。',
        params: [
          {
            name: 'action',
            in: 'path',
            type: 'string',
            desc: 'data：返回 WARP 统计；del：删除数据；config：返回当前配置；reg：注册并提交密钥；regMasque：注册独立 MASQUE 设备，返回 WARP 密钥对、地址和端点；changeIp：轮换端点；license：设置 WARP+ 密钥；interval：设置自动轮换间隔（小时）。',
          },
          {
            name: 'privateKey',
            in: 'body (form)',
            type: 'string',
            desc: 'action=reg 时必填。',
            optional: true,
          },
          {
            name: 'publicKey',
            in: 'body (form)',
            type: 'string',
            desc: 'action=reg 时必填。',
            optional: true,
          },
          {
            name: 'license',
            in: 'body (form)',
            type: 'string',
            desc: 'action=license 时必填。',
            optional: true,
          },
          {
            name: 'interval',
            in: 'body (form)',
            type: 'integer',
            desc: '自动轮换间隔，单位为小时，必须非负。action=interval 时必填，0 表示禁用。',
            optional: true,
          },
        ],
      },
      {
        method: 'POST',
        path: '/panel/api/xray/nord/:action',
        summary: '管理 NordVPN 集成，通过 action 参数选择操作。',
        params: [
          {
            name: 'action',
            in: 'path',
            type: 'string',
            desc: 'countries：列出国家；servers：按 countryId 列出国家内服务器；reg：提交 token 获取 NordVPN 凭据；setKey：提交 key 保存 API 密钥；data：返回当前连接数据；del：删除数据。',
          },
          {
            name: 'countryId',
            in: 'body (form)',
            type: 'string',
            desc: 'action=servers 时必填。',
            optional: true,
          },
          {
            name: 'token',
            in: 'body (form)',
            type: 'string',
            desc: 'action=reg 时必填。',
            optional: true,
          },
          {
            name: 'key',
            in: 'body (form)',
            type: 'string',
            desc: 'action=setKey 时必填。',
            optional: true,
          },
        ],
      },
      {
        method: 'POST',
        path: '/panel/api/xray/pia/:action',
        summary: '管理 PIA WireGuard 集成，通过 action 参数选择操作。',
        params: [
          {
            name: 'action',
            in: 'path',
            type: 'string',
            desc: 'countries：读取已签名 PIA 服务器列表中的国家；servers：按 countryCode 列出区域和 WireGuard 服务器；reg：提交 username/password 登录；data：返回已登录账户提示；del：删除已保存凭据；addKey：向 hostname 指定服务器注册 WireGuard 密钥并返回出站配置字段。',
          },
          {
            name: 'username',
            in: 'body (form)',
            type: 'string',
            desc: 'action=reg 时必填。',
            optional: true,
          },
          {
            name: 'password',
            in: 'body (form)',
            type: 'string',
            desc: 'action=reg 时必填。',
            optional: true,
          },
          {
            name: 'countryCode',
            in: 'body (form)',
            type: 'string',
            desc: 'action=servers 时必填。',
            optional: true,
          },
          {
            name: 'hostname',
            in: 'body (form)',
            type: 'string',
            desc: 'action=addKey 时必填。',
            optional: true,
          },
        ],
      },
      {
        method: 'POST',
        path: '/panel/api/xray/resetOutboundsTraffic',
        summary: '按标签重置指定出站的流量计数。',
        params: [
          {
            name: 'tag',
            in: 'body (form)',
            type: 'string',
            desc: '需要重置的出站标签，例如 proxy 或 direct。',
          },
        ],
        body: 'tag=proxy',
      },
      {
        method: 'POST',
        path: '/panel/api/xray/testOutbound',
        summary:
          '测试出站配置。必须提供出站 JSON，可选提供全部出站以解析 sockopt.dialerProxy 依赖，并通过模式字段选择探测方式。',
        params: [
          {
            name: 'outbound',
            in: 'body (form)',
            type: 'string',
            desc: 'JSON 编码的单个待测出站，必填。',
          },
          {
            name: 'allOutbounds',
            in: 'body (form)',
            type: 'string',
            desc: '全部出站的 JSON 数组，用于解析 dialerProxy 链。',
            optional: true,
          },
          {
            name: 'mode',
            in: 'body (form)',
            type: 'string',
            desc: 'tcp 仅探测拨号，速度快且可并发；real 测量含隧道建立的完整请求延迟。默认或留空使用完整 HTTP 探测，报告连接预热后的单次往返延迟。两种 HTTP 模式均通过临时 Xray 实例执行。',
            optional: true,
          },
        ],
        body: 'outbound={"protocol":"freedom","settings":{}}&mode=tcp',
      },
      {
        method: 'POST',
        path: '/panel/api/xray/testOutbounds',
        summary:
          '通过共享临时 Xray 实例批量测试出站，最多 50 项。按输入顺序返回标签、延迟、HTTP 状态及连接/TLS/首字节耗时分解。',
        params: [
          {
            name: 'outbounds',
            in: 'body (form)',
            type: 'string',
            desc: '待测出站配置的 JSON 数组，必填。',
          },
          {
            name: 'allOutbounds',
            in: 'body (form)',
            type: 'string',
            desc: '全部出站的 JSON 数组，用于解析 dialerProxy 链。',
            optional: true,
          },
          {
            name: 'mode',
            in: 'body (form)',
            type: 'string',
            desc: 'tcp 为快速拨号探测，UDP 传输出站仍使用 HTTP；real 测量含隧道建立的完整请求延迟；默认或留空通过各出站发起 HTTP 请求，报告预热后的单次往返延迟。',
            optional: true,
          },
        ],
        body: 'outbounds=[{"tag":"direct","protocol":"freedom","settings":{}}]&mode=http',
      },
      {
        method: 'POST',
        path: '/panel/api/xray/balancerStatus',
        summary:
          '读取运行中内核的路由负载均衡实时状态（RoutingService.GetBalancerInfo），包括当前覆盖设置和策略首选目标，返回以负载均衡标签为键的映射。',
        params: [
          {
            name: 'tags',
            in: 'body (form)',
            type: 'string',
            desc: '逗号分隔的负载均衡标签，例如 b1,b2。',
          },
        ],
        body: 'tags=b1,b2',
      },
      {
        method: 'POST',
        path: '/panel/api/xray/balancerOverride',
        summary:
          '强制运行中负载均衡始终选择指定出站（RoutingService.OverrideBalancerTarget）。实时生效，无需重启，保存配置后保留；Xray 重启或目标出站移除后清除。',
        params: [
          { name: 'tag', in: 'body (form)', type: 'string', desc: '负载均衡标签，必填。' },
          {
            name: 'target',
            in: 'body (form)',
            type: 'string',
            desc: '强制使用的出站标签，必须存在于当前运行配置。留空清除覆盖，恢复策略控制。',
            optional: true,
          },
        ],
        body: 'tag=b1&target=proxy',
      },
      {
        method: 'POST',
        path: '/panel/api/xray/routeTest',
        summary:
          '让运行中内核判断模拟连接会选择哪个出站（RoutingService.TestRoute），不发送实际流量。',
        bodyRequiredOneOf: ['domain', 'ip'],
        params: [
          {
            name: 'domain',
            in: 'body (form)',
            type: 'string',
            desc: '目标域名，domain 和 ip 至少提供一个。',
            optional: true,
            minLength: 1,
          },
          {
            name: 'ip',
            in: 'body (form)',
            type: 'string',
            desc: '目标 IP，domain 和 ip 至少提供一个。',
            optional: true,
            minLength: 1,
          },
          {
            name: 'port',
            in: 'body (form)',
            type: 'number',
            desc: '目标端口，可选。',
            optional: true,
          },
          {
            name: 'network',
            in: 'body (form)',
            type: 'string',
            desc: '传输类型 tcp（默认）或 udp。',
            optional: true,
          },
          {
            name: 'inboundTag',
            in: 'body (form)',
            type: 'string',
            desc: '模拟从此入站进入，可选。',
            optional: true,
          },
          {
            name: 'protocol',
            in: 'body (form)',
            type: 'string',
            desc: '嗅探协议，例如 http、tls、bittorrent，可选。',
            optional: true,
          },
          {
            name: 'email',
            in: 'body (form)',
            type: 'string',
            desc: '用于用户规则的用户归属，可选。',
            optional: true,
          },
        ],
        body: 'domain=example.com&port=443&network=tcp',
      },
      {
        method: 'GET',
        path: '/panel/api/xray/geodata/files',
        summary:
          '列出 Xray 资源目录中的 .dat 地理数据库，按内容识别结构，并返回大小、修改时间和分类数。解析失败的文件仍列出，原因位于 error。',
      },
      {
        method: 'GET',
        path: '/panel/api/xray/geodata/categories',
        summary: '分页返回数据库分类，每项包含条目数及域名属性，例如 ads、cn。',
        params: [
          {
            name: 'file',
            in: 'query',
            type: 'string',
            desc: '资源目录内的数据库文件名，例如 geosite.dat，必填。',
          },
          {
            name: 'q',
            in: 'query',
            type: 'string',
            optional: true,
            desc: '忽略大小写，以分类代码子字符串筛选。',
          },
          {
            name: 'offset',
            in: 'query',
            type: 'integer',
            optional: true,
            desc: '跳过的行数，默认为 0。',
          },
          {
            name: 'limit',
            in: 'query',
            type: 'integer',
            optional: true,
            desc: '返回行数，最多 500。省略时返回全部分类；索引较小，由前端筛选。',
          },
        ],
      },
      {
        method: 'GET',
        path: '/panel/api/xray/geodata/entries',
        summary:
          '分页返回分类中的规则。geosite 域名规则类型为 domain/full/keyword/regexp，geoip 返回 CIDR。',
        params: [
          {
            name: 'file',
            in: 'query',
            type: 'string',
            desc: '资源目录内的数据库文件名，必填。',
          },
          {
            name: 'code',
            in: 'query',
            type: 'string',
            desc: '分类代码，忽略大小写，例如 google，必填。',
          },
          {
            name: 'q',
            in: 'query',
            type: 'string',
            optional: true,
            desc: '忽略大小写，以规则值的子字符串筛选。',
          },
          {
            name: 'offset',
            in: 'query',
            type: 'integer',
            optional: true,
            desc: '跳过的行数，默认为 0。',
          },
          {
            name: 'limit',
            in: 'query',
            type: 'integer',
            optional: true,
            desc: '返回行数，最多 500，默认使用上限。',
          },
        ],
      },
      {
        method: 'POST',
        path: '/panel/api/xray/geodata/validate',
        summary:
          '对照磁盘数据库检查路由标记，仅返回无法解析的项，忽略普通域名和 CIDR。原因代码包括 syntax、fileMissing、categoryMissing。',
        params: [
          {
            name: 'tokens',
            in: 'body (form)',
            type: 'string',
            desc: '逗号分隔的路由标记，例如 geosite:google,geosite:blabla，每次最多 500 项。',
          },
          {
            name: 'kind',
            in: 'body (form)',
            type: 'string',
            desc: 'ip 表示按 IP 规则解析（geoip:、ext-ip:、前导 !），其他值按域名规则解析（geosite:、ext-site:）。',
            optional: true,
          },
        ],
        body: 'kind=domain&tokens=geosite:google,geosite:blabla',
      },
      {
        method: 'GET',
        path: '/panel/api/xray/outbound-subs',
        summary: '列出提供附加出站的远程订阅，按创建时间倒序。',
      },
      {
        method: 'POST',
        path: '/panel/api/xray/outbound-subs',
        summary:
          '创建出站订阅，获取地址内容并解析成稳定标签出站，以追加方式合并到运行中的 Xray 配置。',
        params: outboundSubscriptionBodyParams,
      },
      {
        method: 'POST',
        path: '/panel/api/xray/outbound-subs/:id',
        summary: '按编号更新出站订阅，表单字段与创建相同。',
        params: [
          { name: 'id', in: 'path', type: 'integer', desc: '订阅编号。' },
          ...outboundSubscriptionBodyParams,
        ],
      },
      {
        method: 'DELETE',
        path: '/panel/api/xray/outbound-subs/:id',
        summary: '按编号删除出站订阅。',
        params: [{ name: 'id', in: 'path', type: 'integer', desc: '订阅编号。' }],
      },
      {
        method: 'POST',
        path: '/panel/api/xray/outbound-subs/:id/del',
        summary: '按编号删除出站订阅，为不支持 DELETE 的客户端提供 POST 等价接口。',
        params: [{ name: 'id', in: 'path', type: 'integer', desc: '订阅编号。' }],
      },
      {
        method: 'POST',
        path: '/panel/api/xray/outbound-subs/:id/refresh',
        summary: '立即重新获取订阅并返回解析后的出站，同时通知 Xray 重新加载。',
        params: [{ name: 'id', in: 'path', type: 'integer', desc: '订阅编号。' }],
      },
      {
        method: 'POST',
        path: '/panel/api/xray/outbound-subs/:id/move',
        summary: '将订阅优先级上移或下移一位，控制它在合并出站中的位置。',
        params: [
          { name: 'id', in: 'path', type: 'integer', desc: '订阅编号。' },
          {
            name: 'dir',
            in: 'body (form)',
            type: 'string',
            desc: 'up 表示提高优先级，其他值表示降低。',
            optional: true,
          },
        ],
      },
      {
        method: 'POST',
        path: '/panel/api/xray/outbound-subs/parse',
        summary: '预览订阅地址，获取并解析出站，不保存任何数据。',
        params: [
          {
            name: 'url',
            in: 'body (form)',
            type: 'string',
            desc: '需要预览的订阅地址，必填。',
          },
          {
            name: 'userAgent',
            in: 'body (form)',
            type: 'string',
            desc: '获取预览时发送的自定义 User-Agent。',
            optional: true,
          },
          {
            name: 'allowPrivate',
            in: 'body (form)',
            type: 'boolean',
            desc: '允许私有、内部或回环地址，默认为 false。',
            optional: true,
          },
          {
            name: 'allowInsecure',
            in: 'body (form)',
            type: 'boolean',
            desc: '跳过 TLS 证书验证，默认为 false。',
            optional: true,
          },
        ],
      },
    ],
  },

  {
    id: 'sub-balancers',
    title: '订阅负载均衡',
    description:
      'JSON 订阅中的客户端负载均衡。每个启用项生成一份额外配置，成员为选中入站的代理出站，使用 routing.balancers 和 burstObservatory。在“设置 → 订阅负载均衡”中管理。',
    endpoints: [
      {
        method: 'GET',
        path: '/panel/api/sub-balancers',
        summary: '按 sort_order 升序、id 升序列出全部订阅负载均衡配置。',
        responseSchema: 'SubBalancer',
        responseSchemaArray: true,
      },
      {
        method: 'POST',
        path: '/panel/api/sub-balancers',
        summary: '创建订阅负载均衡配置。与任一所选入站关联的客户端，其 JSON 订阅中均包含此配置。',
        params: subBalancerBodyParams,
        responseSchema: 'SubBalancer',
      },
      {
        method: 'POST',
        path: '/panel/api/sub-balancers/:id',
        summary:
          '按编号替换完整负载均衡记录，字段与创建相同。省略 memberWeights 会清除已存权重，省略 enabled 则保留当前状态。',
        params: [
          { name: 'id', in: 'path', type: 'integer', desc: '负载均衡编号。' },
          ...subBalancerBodyParams,
        ],
        responseSchema: 'SubBalancer',
      },
      {
        method: 'DELETE',
        path: '/panel/api/sub-balancers/:id',
        summary: '按编号删除负载均衡配置。',
        params: [{ name: 'id', in: 'path', type: 'integer', desc: '负载均衡编号。' }],
        responseSchema: 'SubBalancer',
      },
      {
        method: 'POST',
        path: '/panel/api/sub-balancers/:id/del',
        summary: '按编号删除负载均衡配置，为不支持 DELETE 的客户端提供 POST 等价接口。',
        params: [{ name: 'id', in: 'path', type: 'integer', desc: '负载均衡编号。' }],
        responseSchema: 'SubBalancer',
      },
    ],
  },

  {
    id: 'subscription',
    title: '订阅服务器',
    description:
      '向客户端提供标准、JSON 和 Clash 订阅的独立 HTTP/HTTPS 服务。使用独立端口，默认 2096，在“设置 → 订阅”配置。新安装为各格式生成随机路径前缀，仍可自行修改。各订阅接口通过响应头提供流量和期限信息。',
    subHeader: [
      {
        name: 'Subscription-Userinfo',
        desc: '流量和期限：<code>upload=N; download=N; total=N; expire=TS</code>。',
      },
      { name: 'Profile-Title', desc: '经过 Base64 编码的订阅显示名称。' },
      { name: 'Profile-Web-Page-Url', desc: '订阅详情页链接。' },
      { name: 'Support-Url', desc: '设置中配置的支持联系地址。' },
      {
        name: 'Profile-Update-Interval',
        desc: '建议轮询间隔，单位为分钟，例如 <code>10</code>。',
      },
      { name: 'Announce', desc: '经过 Base64 编码的公告文本。' },
      {
        name: 'Routing-Enable',
        desc: '<code>true</code> 或 <code>false</code>，表示是否包含路由规则。',
      },
      {
        name: 'Routing',
        desc: '供支持此功能的客户端（如 Happ）使用的全局路由规则。',
      },
    ],
    endpoints: [
      {
        method: 'GET',
        path: '/{subPath}:subid',
        summary:
          '返回匹配订阅标识的所有已启用客户端链接，使用 Base64 编码。Accept:text/html 或 ?html=1 时显示详情页；?format=info 返回流量、期限和在线状态的 JSON 视图模型，不含链接，供实时轮询。路径前缀由 subPath 配置。',
        params: [
          { name: 'subid', in: 'path', type: 'string', desc: '客户端订阅标识。' },
          {
            name: 'format',
            in: 'query',
            type: 'string',
            optional: true,
            desc: '设为 info 时返回订阅状态的 JSON 视图模型，而非链接。',
          },
        ],
      },
      {
        method: 'HEAD',
        path: '/{subPath}:subid',
        summary: '返回与 GET 相同的状态和订阅元数据响应头，无响应正文。',
        params: [{ name: 'subid', in: 'path', type: 'string', desc: '客户端订阅标识。' }],
        responses: subscriptionHeadResponses,
      },
      {
        method: 'GET',
        path: '/{subPath}:subid/hwid-status',
        summary:
          '返回订阅的 HWID 设备名额汇总：限制是否启用、名额上限、已注册数量和剩余数量。此接口只读，不注册设备或占用名额，仅返回计数，不包含 HWID、email 或设备信息。路径前缀由 subPath 配置。',
        description:
          '与其他订阅服务接口一样，直接返回 HwidSlotStatus 对象，不使用面板 <code>{success,msg,obj}</code> 包装。未配置 HWID 限制时，active 为 false，所有计数为 0。',
        params: [{ name: 'subid', in: 'path', type: 'string', desc: '客户端订阅标识。' }],
        responses: {
          '200': {
            description: '订阅的设备名额计数。',
            content: {
              'application/json': { schema: { $ref: '#/components/schemas/HwidSlotStatus' } },
            },
          },
          ...hwidStatusErrorResponses,
        },
      },
      {
        method: 'HEAD',
        path: '/{subPath}:subid/hwid-status',
        summary: '返回与 GET 相同的 HWID 设备名额状态码和响应头，无响应正文。',
        params: [{ name: 'subid', in: 'path', type: 'string', desc: '客户端订阅标识。' }],
        responses: {
          '200': { description: '响应头与 GET 相同，无响应正文。' },
          ...hwidStatusErrorResponses,
        },
      },
      {
        method: 'GET',
        path: '/{jsonPath}:subid',
        summary:
          '以 JSON 数组返回代理配置，每个启用客户端一份。仅在设置中启用 JSON 订阅时提供，路径前缀由 subJsonPath 配置。',
        params: [{ name: 'subid', in: 'path', type: 'string', desc: '客户端订阅标识。' }],
      },
      {
        method: 'HEAD',
        path: '/{jsonPath}:subid',
        summary: '返回 JSON 订阅状态和元数据响应头，无正文。仅在启用 JSON 订阅时注册此路由。',
        params: [{ name: 'subid', in: 'path', type: 'string', desc: '客户端订阅标识。' }],
        responses: subscriptionHeadResponses,
      },
      {
        method: 'GET',
        path: '/{clashPath}:subid',
        summary:
          '返回兼容 Clash/Mihomo 的 YAML 配置，包括已配置的全局 Clash 路由规则。仅在启用 Clash 订阅时提供，路径前缀由 subClashPath 配置。',
        params: [{ name: 'subid', in: 'path', type: 'string', desc: '客户端订阅标识。' }],
      },
      {
        method: 'HEAD',
        path: '/{clashPath}:subid',
        summary: '返回 Clash 订阅状态和元数据响应头，无正文。仅在启用 Clash 订阅时注册此路由。',
        params: [{ name: 'subid', in: 'path', type: 'string', desc: '客户端订阅标识。' }],
        responses: subscriptionHeadResponses,
      },
    ],
  },

  {
    id: 'websocket',
    title: 'WebSocket 实时事件',
    description:
      '通过 WebSocket 实时更新状态。连接 <code>ws://<panel>/ws</code> 后持续接收 JSON 消息，无需轮询。需要已验证的会话 Cookie，不支持 Bearer 令牌。各消息以 type 字段标识载荷结构。',
    endpoints: [
      {
        method: 'GET',
        path: '/ws',
        summary:
          '将 HTTP 连接升级为 WebSocket，需要已验证的会话 Cookie，不支持 Bearer 令牌。成功返回 101 状态码，随后服务器推送下述 JSON 消息。',
        responses: {
          '101': { description: '协议切换成功，WebSocket 消息使用 WebSocketEnvelope。' },
          '401': { description: '未提供已验证的面板会话 Cookie。' },
        },
        security: [{ cookieAuth: [] }],
      },
    ],
  },
];
