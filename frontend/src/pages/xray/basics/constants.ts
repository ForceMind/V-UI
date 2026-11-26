export const ROUTING_DOMAIN_STRATEGIES = ['AsIs', 'IPIfNonMatch', 'IPOnDemand'];
export const LOG_LEVELS = ['none', 'debug', 'info', 'warning', 'error'];
export const ACCESS_LOG = ['none', './access.log'];
export const ERROR_LOG = ['none', './error.log'];
export const MASK_ADDRESS = ['quarter', 'half', 'full'];
export const BITTORRENT_PROTOCOLS = ['bittorrent'];

export const IPS_OPTIONS = [
  { label: '私有 IP', value: 'geoip:private' },
  { label: '🇮🇷 伊朗', value: 'ext:geoip_IR.dat:ir' },
  { label: '🇨🇳 中国', value: 'geoip:cn' },
  { label: '🇷🇺 俄罗斯', value: 'ext:geoip_RU.dat:ru' },
  { label: '🇻🇳 越南', value: 'geoip:vn' },
  { label: '🇪🇸 西班牙', value: 'geoip:es' },
  { label: '🇮🇩 印度尼西亚', value: 'geoip:id' },
  { label: '🇺🇦 乌克兰', value: 'geoip:ua' },
  { label: '🇹🇷 土耳其', value: 'geoip:tr' },
  { label: '🇧🇷 巴西', value: 'geoip:br' },
];
export const DOMAINS_OPTIONS = [
  { label: '🇮🇷 伊朗', value: 'ext:geosite_IR.dat:ir' },
  { label: '🇮🇷 .ir', value: 'regexp:.*\\.ir$' },
  { label: '🇮🇷 .ایران', value: 'regexp:.*\\.xn--mgba3a4f16a$' },
  { label: '🇨🇳 中国', value: 'geosite:cn' },
  { label: '🇨🇳 .cn', value: 'regexp:.*\\.cn$' },
  { label: '🇷🇺 俄罗斯', value: 'ext:geosite_RU.dat:ru-available-only-inside' },
  { label: '🇷🇺 .ru', value: 'regexp:.*\\.ru$' },
  { label: '🇷🇺 .su', value: 'regexp:.*\\.su$' },
  { label: '🇷🇺 .рф', value: 'regexp:.*\\.xn--p1ai$' },
  { label: '🇻🇳 .vn', value: 'regexp:.*\\.vn$' },
];
export const BLOCK_DOMAINS_OPTIONS = [
  { label: '全部广告', value: 'geosite:category-ads-all' },
  { label: '伊朗广告 🇮🇷', value: 'ext:geosite_IR.dat:category-ads-all' },
  { label: '俄罗斯广告 🇷🇺', value: 'ext:geosite_RU.dat:category-ads-all' },
  { label: '恶意软件 🇮🇷', value: 'ext:geosite_IR.dat:malware' },
  { label: '钓鱼网站 🇮🇷', value: 'ext:geosite_IR.dat:phishing' },
  { label: '加密货币挖矿 🇮🇷', value: 'ext:geosite_IR.dat:cryptominers' },
  { label: '成人内容（18+）', value: 'geosite:category-porn' },
  { label: '🇮🇷 伊朗', value: 'ext:geosite_IR.dat:ir' },
  { label: '🇮🇷 .ir', value: 'regexp:.*\\.ir$' },
  { label: '🇮🇷 .ایران', value: 'regexp:.*\\.xn--mgba3a4f16a$' },
  { label: '🇨🇳 中国', value: 'geosite:cn' },
  { label: '🇨🇳 .cn', value: 'regexp:.*\\.cn$' },
  { label: '🇷🇺 俄罗斯', value: 'ext:geosite_RU.dat:ru-available-only-inside' },
  { label: '🇷🇺 .ru', value: 'regexp:.*\\.ru$' },
  { label: '🇷🇺 .su', value: 'regexp:.*\\.su$' },
  { label: '🇷🇺 .рф', value: 'regexp:.*\\.xn--p1ai$' },
  { label: '🇻🇳 .vn', value: 'regexp:.*\\.vn$' },
];
export const SERVICES_OPTIONS = [
  { label: 'Apple', value: 'geosite:apple' },
  { label: 'Meta', value: 'geosite:meta' },
  { label: 'Google', value: 'geosite:google' },
  { label: 'OpenAI', value: 'geosite:openai' },
  { label: 'Spotify', value: 'geosite:spotify' },
  { label: 'Netflix', value: 'geosite:netflix' },
  { label: 'Reddit', value: 'geosite:reddit' },
  { label: 'Speedtest', value: 'geosite:speedtest' },
];

export const directSettings = { tag: 'direct', protocol: 'freedom' };
export const blockedSettings = { tag: 'blocked', protocol: 'blackhole', settings: {} };
// The strategy rides on sockopt: freedom resolves through the socket layer, and
// the settings-level alias makes the core warn on every config load.
export const ipv4Settings = {
  tag: 'IPv4',
  protocol: 'freedom',
  streamSettings: { sockopt: { domainStrategy: 'UseIPv4' } },
};
