import { describe, expect, it } from 'vitest';

import { buildHappPresetDeeplink, toBase64Utf8 } from '@/pages/settings/happPresets';

describe('Happ presets and helpers', () => {
  it('generates valid happ://routing/off for off preset', () => {
    const link = buildHappPresetDeeplink('off');
    expect(link).toBe('happ://routing/off');
  });

  it('generates valid base64 payload for iran-bypass preset', () => {
    const link = buildHappPresetDeeplink('iran-bypass');
    expect(link.startsWith('happ://routing/onadd/')).toBe(true);

    const b64 = link.replace('happ://routing/onadd/', '');
    const jsonStr = new TextDecoder().decode(
      Uint8Array.from(atob(b64), (char) => char.charCodeAt(0)),
    );
    const parsed = JSON.parse(jsonStr);

    expect(parsed.Name).toBe('伊朗分流');
    expect(parsed.GlobalProxy).toBe('true');
    expect(parsed.DirectSites).toContain('domain:ir');
    expect(parsed.DirectIp).toContain('geoip:ir');
    expect(parsed.BlockSites).toEqual([]);
  });

  it('generates valid base64 payload for china-direct preset', () => {
    const link = buildHappPresetDeeplink('china-direct');
    expect(link.startsWith('happ://routing/onadd/')).toBe(true);

    const b64 = link.replace('happ://routing/onadd/', '');
    const jsonStr = new TextDecoder().decode(
      Uint8Array.from(atob(b64), (char) => char.charCodeAt(0)),
    );
    const parsed = JSON.parse(jsonStr);

    expect(parsed).toEqual({
      Name: 'Bypass-CN',
      GlobalProxy: 'true',
      RouteOrder: 'block-proxy-direct',
      RemoteDNSType: 'DoH',
      RemoteDNSDomain: 'https://cloudflare-dns.com/dns-query',
      RemoteDNSIP: '1.1.1.1',
      DomesticDNSType: 'DoH',
      DomesticDNSDomain: 'https://dns.alidns.com/dns-query',
      DomesticDNSIP: '223.5.5.5',
      DnsHosts: {
        'cloudflare-dns.com': '1.1.1.1',
        'dns.alidns.com': '223.5.5.5',
      },
      DirectSites: ['geosite:private', 'geosite:cn', 'geosite:geolocation-cn'],
      DirectIp: [
        'geoip:cn',
        'geoip:private',
        '127.0.0.0/8',
        '10.0.0.0/8',
        '172.16.0.0/12',
        '192.168.0.0/16',
        '169.254.0.0/16',
        '224.0.0.0/4',
        '255.255.255.255',
      ],
      ProxySites: [],
      ProxyIp: [],
      BlockSites: [],
      BlockIp: [],
      DomainStrategy: 'IPIfNonMatch',
      FakeDNS: 'false',
      UseChunkFiles: 'true',
    });
  });

  it.each(['iran-bypass', 'china-direct', 'global', 'lan-bypass'])(
    'adds ad blocking only when opted in for %s without changing its routing',
    (preset) => {
      const decode = (link: string) => JSON.parse(atob(link.replace('happ://routing/onadd/', '')));
      const base = decode(buildHappPresetDeeplink(preset));
      const withAds = decode(buildHappPresetDeeplink(preset, true));
      const withoutAds = decode(buildHappPresetDeeplink(preset, false));

      expect(base.BlockSites).toEqual([]);
      expect(withAds.BlockSites).toEqual(['geosite:category-ads-all']);
      expect({ ...withAds, BlockSites: [] }).toEqual(base);
      expect(withoutAds).toEqual(base);
    },
  );

  it('keeps routing disabled even when ad blocking is selected', () => {
    expect(buildHappPresetDeeplink('off', true)).toBe('happ://routing/off');
  });

  it.each(['adblock', 'unknown'])(
    'does not generate a profile for unsupported preset %s',
    (preset) => {
      expect(buildHappPresetDeeplink(preset)).toBe('');
    },
  );

  it('keeps the global preset without direct exceptions', () => {
    const link = buildHappPresetDeeplink('global');
    const b64 = link.replace('happ://routing/onadd/', '');
    const jsonStr = new TextDecoder().decode(
      Uint8Array.from(atob(b64), (char) => char.charCodeAt(0)),
    );
    const parsed = JSON.parse(jsonStr);

    expect(parsed.Name).toBe('全局代理');
    expect(parsed.GlobalProxy).toBe('true');
    expect(parsed.DirectSites).toEqual([]);
    expect(parsed.DirectIp).toEqual([]);
    expect(parsed.DomainStrategy).toBe('AsIs');
  });

  it('generates a separate LAN bypass preset with proxy as the default', () => {
    const link = buildHappPresetDeeplink('lan-bypass');
    const prefix = 'happ://routing/onadd/';
    expect(link.startsWith(prefix)).toBe(true);
    const parsed = JSON.parse(
      new TextDecoder().decode(
        Uint8Array.from(atob(link.slice(prefix.length)), (char) => char.charCodeAt(0)),
      ),
    );

    expect(parsed.Name).toBe('全局代理（绕过本地网络）');
    expect(parsed.GlobalProxy).toBe('true');
    expect(parsed.DirectSites).toEqual(['geosite:private']);
    expect(parsed.DirectIp).toEqual(['geoip:private']);
    expect(parsed.BlockSites).toEqual([]);
    expect(parsed.DomainStrategy).toBe('AsIs');
  });

  it('encodes unicode properly via toBase64Utf8', () => {
    const text = 'فیلترشکن و روتینگ';
    const b64 = toBase64Utf8(text);
    const decoded = decodeURIComponent(
      Array.prototype.map
        .call(atob(b64), (c: string) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join(''),
    );
    expect(decoded).toBe(text);
  });
});
