// Keep the wire response and diagnostics intact for API clients. The panel shows
// translations for messages produced by the server's own operation handlers.
const messages: Record<string, string> = {
  success: '成功',
  'invalid data': '数据无效',
  'Invalid id': '编号无效',
  'invalid node id': '节点编号无效',
  'node not found': '找不到节点',
  'node is disabled': '节点已停用',
  'node is offline': '节点离线',
  'no nodes selected': '尚未选择节点',
  'runtime manager unavailable': '运行管理服务不可用',
  'invalid bucket': '时间分组无效',
  'unsupported bucket': '不支持此时间分组',
  'invalid metric': '指标无效',
  'unknown metric': '未知指标',
  'invalid tag': '标签无效',
  'unknown observatory tag': '找不到探测标签',
  'unknown action': '未知操作',
  'invalid filename': '文件名无效',
  'invalid filename: contains unsafe characters or path traversal patterns':
    '文件名含有不安全字符或路径',
  'get ech certificate': '获取 ECH 证书',
  'get cert hash': '获取证书摘要',
  'get remote cert hash': '获取远端证书摘要',
  'Failed to generate UUID': '生成 UUID 失败',
  'Failed to generate mlkem768 keys': '生成 ML-KEM-768 密钥失败',
  'Client IPs merged': '客户端 IP 地址已合并',
  'Failed to list outbound subscriptions': '读取出站订阅失败',
  'Failed to create outbound subscription': '创建出站订阅失败',
  'Failed to update outbound subscription': '更新出站订阅失败',
  'Failed to delete outbound subscription': '删除出站订阅失败',
  'Failed to reorder outbound subscription': '调整出站订阅顺序失败',
  'Refresh failed': '刷新失败',
  'url is required': '请填写地址',
  'missing url': '缺少地址',
  'Failed to preview subscription': '预览订阅失败',
  'Failed to fetch/parse subscription': '获取或解析订阅失败',
  'email service not available': '邮件服务不可用',
  'telegram bot disabled': 'Telegram 机器人已停用',
  'bot not started': '机器人尚未启动',
  'discord service not available': 'Discord 服务不可用',
  'discord bot disabled': 'Discord 机器人已停用',
  'record not found': '找不到记录',
  'context deadline exceeded': '操作超时',
};

export function panelMessage(text: string): string {
  const translate = (value: string) => (Object.hasOwn(messages, value) ? messages[value] : value);
  const parts = /^(.*?) \((.*)\)$/s.exec(text);
  if (!parts) return translate(text);
  const [, action, error] = parts;
  return action ? `${translate(action)}（${translate(error)}）` : translate(error);
}
