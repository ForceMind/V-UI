import { useEffect, useRef } from 'react';
import type { ComponentType } from 'react';
import { localizeApiDocument } from './localize-api-document';

// Swagger UI has no locale option. Its component plugin keeps request handling,
// authorization and schema data intact while translating the built-in controls.
const labels: Record<string, string> = {
  Authorize: '授权',
  'Available authorizations': '可用授权方式',
  Authorized: '已授权',
  Logout: '退出授权',
  Close: '关闭',
  Cancel: '取消',
  Reset: '重置',
  Clear: '清除',
  Execute: '执行',
  'Try it out': '试用接口',
  'Apply credentials': '应用凭据',
  'Copy path to clipboard': '复制接口路径',
  Parameters: '参数',
  'No parameters': '无参数',
  Name: '名称',
  Description: '说明',
  Responses: '响应',
  'Server response': '服务器响应',
  Code: '状态码',
  Details: '详情',
  Links: '链接',
  'No links': '无链接',
  'Response body': '响应正文',
  'Response headers': '响应头',
  'Request URL': '请求地址',
  'Request body': '请求正文',
  'Response content type': '响应内容类型',
  'Request duration': '请求耗时',
  Undocumented: '未定义',
  Error: '错误',
  'Fetch error': '获取失败',
  'Failed to load API definition.': '加载 API 定义失败。',
  'Possible reasons:': '可能的原因：',
  'Network Failure': '网络故障',
  'URL scheme must be "http" or "https" for CORS request.': '跨域请求必须使用 HTTP 或 HTTPS 地址。',
  'Example Value': '示例值',
  'Edit Value': '编辑值',
  'Download file': '下载文件',
  Download: '下载',
  'Controls Accept header.': '设置 Accept 请求头。',
  'Controls Content-Type header.': '设置 Content-Type 请求头。',
  'Send empty value': '发送空值',
  'Add item': '添加条目',
  required: '必填',
  deprecated: '已弃用',
  'Default value': '默认值',
  Example: '示例',
  Examples: '示例',
  Value: '值',
  Username: '用户名',
  Password: '密码',
  Location: '位置',
  In: '位置',
  Schemas: '数据结构',
  Schema: '数据结构',
  Model: '数据模型',
  Servers: '服务器',
  'Server variables': '服务器变量',
  'Computed URL:': '计算后的地址：',
  'Collapse operation': '收起操作',
  'Expand operation': '展开操作',
  'authorization button locked': '已授权，点击管理授权',
  'authorization button unlocked': '未授权，点击授权',
};

function translate(value: string): string {
  const trimmed = value.trim();
  if (Object.hasOwn(labels, trimmed)) return value.replace(trimmed, labels[trimmed]);
  if (trimmed.endsWith(':') && Object.hasOwn(labels, trimmed.slice(0, -1))) {
    return value.replace(trimmed, `${labels[trimmed.slice(0, -1)]}：`);
  }
  return value;
}

const protectedContent = [
  'code',
  'pre',
  'textarea',
  'input',
  'option',
  '[contenteditable]',
  '.parameter__name',
  '.prop-name',
  '.model-title',
  '.opblock-summary-path',
  '.highlight-code',
  '.microlight',
].join(',');

function translateVisibleControls(root: HTMLElement) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    if (node.parentElement?.closest(protectedContent)) continue;
    const translated = translate(node.data);
    if (translated !== node.data) node.data = translated;
  }
  for (const element of root.querySelectorAll('[title], [aria-label]')) {
    if (element.closest(protectedContent)) continue;
    for (const name of ['title', 'aria-label']) {
      const value = element.getAttribute(name);
      if (!value) continue;
      const translated = translate(value);
      if (translated !== value) element.setAttribute(name, translated);
    }
  }
}

function wrapChineseLayout(Original: ComponentType<Record<string, unknown>>) {
  return function ChineseLayout(props: Record<string, unknown>) {
    const rootRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
      const root = rootRef.current;
      if (!root) return;
      translateVisibleControls(root);
      // Swagger renders some controls through internal function components that its
      // component wrappers cannot inspect. Change only known UI text/attributes;
      // never insert nodes or touch examples, schema field names or input values.
      const observer = new MutationObserver(() => translateVisibleControls(root));
      observer.observe(root, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ['title', 'aria-label'],
      });
      return () => observer.disconnect();
    }, []);
    return (
      <div ref={rootRef}>
        <Original {...props} />
      </div>
    );
  };
}

export const chineseSwaggerPlugin = {
  statePlugins: {
    spec: {
      wrapActions: {
        updateJsonSpec: (original: (document: unknown) => unknown) => (document: unknown) =>
          original(localizeApiDocument(document)),
      },
    },
  },
  wrapComponents: { BaseLayout: wrapChineseLayout },
};
