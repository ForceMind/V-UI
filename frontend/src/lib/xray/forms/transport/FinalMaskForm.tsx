import { useEffect, useId, useRef, useState } from 'react';
import {
  AutoComplete,
  Button,
  Divider,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Typography,
} from 'antd';
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { FormInstance } from 'antd/es/form';
import type { NamePath } from 'antd/es/form/interface';

import { RandomUtil } from '@/utils';
import { activateOnKey } from '@/utils/a11y';
import { OutboundProtocols, UTLS_FINGERPRINT } from '@/schemas/primitives';
import { TCP_MASK_PRESETS } from '@/lib/xray/finalmask-presets';
import { upgradeLegacyUdpHop } from '@/lib/xray/udphop-mask';
import { upgradeLegacyXdnsMasks, XDNS_LEGACY_EDNS0 } from '@/lib/xray/xdns-mask';

const UTLS_FINGERPRINT_OPTIONS = Object.values(UTLS_FINGERPRINT).map((value) => ({
  value,
  label: value,
}));

export interface FinalMaskFormProps {
  name: NamePath;
  network: string;
  protocol: string;
  form: FormInstance;
  // When true, all sections (TCP / UDP / QUIC) are shown regardless of
  // network/protocol. Used by the global sub-JSON finalmask editor where
  // the masks apply to every stream rather than one specific transport.
  showAll?: boolean;
  // udphop runs only on the dialing side (the core refuses it on a listener), so a server keeps
  // the advertised hop range in quicParams.udpHop; filter is the listener-only counterpart.
  side?: 'server' | 'client';
}

const TCP_NETWORKS = ['raw', 'tcp', 'httpupgrade', 'ws', 'grpc', 'xhttp', 'xdrive'];
const DEFAULT_GECKO_PACKET_SIZE = { min: 512, max: 1200 };
// Xray-core caps the Gecko output packet size at its internal buffer (2048)
// and needs 1 <= min <= max; mirror those bounds so the panel rejects what
// core would reject at runtime (salamander/conn.go).
const GECKO_MIN_PACKET_SIZE = 1;
const GECKO_MAX_PACKET_SIZE = 2048;

export function parseGeckoPacketSize(value: unknown): { min: number; max: number } | null {
  const str = typeof value === 'string' ? value.trim() : String(value ?? '').trim();
  const match = /^(\d+)-(\d+)$/.exec(str);
  if (!match) return null;
  const min = Number(match[1]);
  const max = Number(match[2]);
  if (
    !Number.isSafeInteger(min) ||
    !Number.isSafeInteger(max) ||
    min < GECKO_MIN_PACKET_SIZE ||
    max < min ||
    max > GECKO_MAX_PACKET_SIZE
  ) {
    return null;
  }
  return { min, max };
}

function formatGeckoPacketSize(min: number, max: number): string {
  return `${min}-${max}`;
}

function splitGeckoPacketSize(value: unknown): { min: number | null; max: number | null } {
  const str = typeof value === 'string' ? value.trim() : String(value ?? '').trim();
  const [minRaw = '', maxRaw = ''] = str.split('-', 2);
  const min = /^\d+$/.test(minRaw) ? Number(minRaw) : null;
  const max = /^\d+$/.test(maxRaw) ? Number(maxRaw) : null;
  return { min, max };
}

function validateGeckoPacketSize(_rule: unknown, value: unknown): Promise<void> {
  if (parseGeckoPacketSize(value)) return Promise.resolve();
  return Promise.reject(
    new Error(
      `请填写范围，例如 512-1200（${GECKO_MIN_PACKET_SIZE}-${GECKO_MAX_PACKET_SIZE}，最大值 ≥ 最小值）`,
    ),
  );
}

// xray-core's salamander smPSKMinLen counts bytes; a shorter password loads, then the listener
// (stopping Xray) or the dial fails, so the backend refuses it at save too.
const SALAMANDER_MIN_PSK_BYTES = 4;

function validateSalamanderPassword(_rule: unknown, value: unknown): Promise<void> {
  const bytes = new TextEncoder().encode(typeof value === 'string' ? value : '').length;
  if (bytes >= SALAMANDER_MIN_PSK_BYTES) return Promise.resolve();
  return Promise.reject(
    new Error(`密码至少需要 ${SALAMANDER_MIN_PSK_BYTES} 个字节，xray-core 不接受更短的密码`),
  );
}

function asPath(name: NamePath): (string | number)[] {
  return Array.isArray(name) ? [...name] : [name];
}

function defaultTcpMaskSettings(type: string): Record<string, unknown> {
  switch (type) {
    case 'fragment':
      // `lengths`/`delays` are per-segment range arrays (xray-core #6334);
      // a single length entry reproduces the legacy single-range behavior.
      return { packets: '1-3', lengths: ['100-200'], delays: [], maxSplit: '' };
    case 'sudoku':
      return defaultSudokuSettings();
    case 'header-custom':
      return { clients: [], servers: [] };
    case 'xmc':
      return {
        hostname: '',
        profiles: [defaultXmcProfile()],
        password: RandomUtil.randomLowerAndNum(16),
      };
    default:
      return {};
  }
}

function defaultSudokuSettings(): Record<string, unknown> {
  return {
    password: '',
    ascii: '',
    customTable: '',
    customTables: [],
    paddingMin: 0,
    paddingMax: 0,
  };
}

function defaultXmcProfile(): Record<string, unknown> {
  return { username: '', uuid: '', texturesValue: '', texturesSignature: '' };
}

// xray-core #6487 replaced the xmc mask's `usernames` string list with
// `profiles` objects carrying a Mojang-signed session profile, and dropped the
// "default to Dream" fallback so at least one complete profile is now
// mandatory. The signature can only come from Mojang's session server, so a
// legacy username cannot be upgraded automatically — carry it into a profile
// stub instead, which keeps the operator's player names visible and leaves the
// per-field validators pointing at exactly what still has to be filled in.
export function migrateXmcSettings(settings: Record<string, unknown>): {
  next: Record<string, unknown>;
  changed: boolean;
} {
  const out: Record<string, unknown> = { ...settings };
  let changed = false;
  if (!Array.isArray(out.profiles) && Array.isArray(out.usernames)) {
    out.profiles = out.usernames
      .filter((name): name is string => typeof name === 'string' && name.trim() !== '')
      .map((name) => ({ ...defaultXmcProfile(), username: name }));
    changed = true;
  }
  if ('usernames' in out) {
    delete out.usernames;
    changed = true;
  }
  if (!Array.isArray(out.profiles)) {
    out.profiles = [];
    changed = true;
  }
  return { next: out, changed };
}

// xray-core #6334 replaced a fragment mask's single `length`/`delay` ranges
// with `lengths`/`delays` arrays (the singular keys remain in core only as a
// fallback). Lift any legacy singular value into a one-element array so the
// list UI shows it, and drop the singular key so we never emit both.
function migrateFragmentSettings(settings: Record<string, unknown>): {
  next: Record<string, unknown>;
  changed: boolean;
} {
  const out: Record<string, unknown> = { ...settings };
  let changed = false;
  if (!Array.isArray(out.lengths) && typeof out.length === 'string' && out.length.trim() !== '') {
    out.lengths = [out.length];
    changed = true;
  }
  if ('length' in out) {
    delete out.length;
    changed = true;
  }
  if (!Array.isArray(out.delays) && typeof out.delay === 'string' && out.delay.trim() !== '') {
    out.delays = [out.delay];
    changed = true;
  }
  if ('delay' in out) {
    delete out.delay;
    changed = true;
  }
  return { next: out, changed };
}

function defaultUdpMaskSettings(type: string): Record<string, unknown> {
  switch (type) {
    case 'salamander':
      return { password: '' };
    case 'mkcp-legacy':
      return { header: '', value: '' };
    case 'xdns':
      return { domains: [] };
    case 'xicmp':
      return { dgram: false, ips: [] };
    case 'realm':
      return { url: '', stunServers: [] };
    case 'header-custom':
      return { client: [], server: [] };
    case 'noise':
      return { reset: 0, noise: [] };
    case 'sudoku':
      return defaultSudokuSettings();
    case 'udphop':
      return { mode: 'intervalRemote', interval: '5-10', remotePorts: '' };
    default:
      return {};
  }
}

function defaultClientServerItem(): Record<string, unknown> {
  return { delay: 0, rand: 0, randRange: '0-255', type: 'array', packet: [] };
}

function defaultUdpClientServerItem(): Record<string, unknown> {
  return { rand: 0, randRange: '0-255', type: 'array', packet: [] };
}

function defaultNoiseItem(): Record<string, unknown> {
  return {
    rand: '1-8192',
    randRange: '0-255',
    type: 'array',
    packet: [],
    delay: '10-20',
  };
}

function defaultQuicParams(): Record<string, unknown> {
  return {
    congestion: 'bbr',
    debug: false,
    maxIdleTimeout: 30,
    keepAlivePeriod: 10,
    disablePathMTUDiscovery: false,
    maxIncomingStreams: 1024,
    initStreamReceiveWindow: 8388608,
    maxStreamReceiveWindow: 8388608,
    initConnectionReceiveWindow: 20971520,
    maxConnectionReceiveWindow: 20971520,
  };
}

function defaultUdpHop(): Record<string, unknown> {
  return { ports: '20000-50000', interval: '5-10' };
}

export default function FinalMaskForm({
  name,
  network,
  protocol,
  form,
  showAll = false,
  side = 'client',
}: FinalMaskFormProps) {
  const uid = useId();
  const base = asPath(name);

  // Migrate legacy mask shapes once on mount so configs saved before #6334 (fragment
  // ranges), #6487 (xmc profiles) and #7090 (xdns names/addrs) render in the list UI.
  const migratedRef = useRef(false);
  useEffect(() => {
    if (migratedRef.current) return;
    migratedRef.current = true;
    const tcp = form.getFieldValue([...base, 'tcp']);
    if (Array.isArray(tcp)) {
      let anyChanged = false;
      const next = tcp.map((mask) => {
        if (!mask || typeof mask !== 'object') return mask;
        const m = mask as Record<string, unknown>;
        if (m.type !== 'fragment' && m.type !== 'xmc') return mask;
        if (!m.settings || typeof m.settings !== 'object') return mask;
        const settings = m.settings as Record<string, unknown>;
        const { next: migrated, changed } =
          m.type === 'fragment' ? migrateFragmentSettings(settings) : migrateXmcSettings(settings);
        if (!changed) return mask;
        anyChanged = true;
        return { ...m, settings: migrated };
      });
      if (anyChanged) form.setFieldValue([...base, 'tcp'], next);
    }
    const udp = form.getFieldValue([...base, 'udp']);
    if (Array.isArray(udp)) {
      const { next, changed } = upgradeLegacyXdnsMasks(udp);
      if (changed) form.setFieldValue([...base, 'udp'], next);
    }
    if (side === 'client') {
      const finalmask = form.getFieldValue(base) as Record<string, unknown> | undefined;
      if (finalmask && typeof finalmask === 'object') {
        const { next, changed } = upgradeLegacyUdpHop(finalmask);
        if (changed) form.setFieldValue(base, next);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const isHysteria = protocol === OutboundProtocols.Hysteria || protocol === 'hysteria';
  // Wireguard carries no user-selectable transport (always a UDP listener/
  // dialer), so only the UDP mask section applies — TCP masks would never
  // wrap anything even though the leftover network value may be 'tcp'.
  const isWireguard = protocol === 'wireguard';
  const showTcp = showAll || (!isWireguard && TCP_NETWORKS.includes(network));
  // xhttp's HTTP/3 dial runs the UDP masks too, which is where a client's udphop lives.
  const showUdp =
    showAll ||
    isHysteria ||
    isWireguard ||
    network === 'kcp' ||
    (side === 'client' && network === 'xhttp');
  const showQuic = showAll || isHysteria || network === 'xhttp';
  const quicParams = Form.useWatch([...base, 'quicParams'], { form, preserve: true });
  const hasQuicParams = quicParams != null;

  if (!showTcp && !showUdp && !showQuic) return null;

  return (
    <>
      {showTcp && (
        <TcpMasksList
          base={base}
          form={form}
          allowFilter={side === 'server'}
          allowPresets={side === 'client'}
        />
      )}
      {showUdp && (
        <UdpMasksList
          base={base}
          form={form}
          isHysteria={isHysteria}
          isWireguard={isWireguard}
          network={network}
          allowUdpHop={side === 'client'}
          allowFilter={side === 'server'}
        />
      )}
      {showQuic && (
        <>
          <Form.Item label="QUIC 参数" htmlFor={`${uid}-quic-params`}>
            <Switch
              id={`${uid}-quic-params`}
              checked={hasQuicParams}
              onChange={(v) => {
                form.setFieldValue([...base, 'quicParams'], v ? defaultQuicParams() : undefined);
              }}
            />
          </Form.Item>
          {hasQuicParams && (
            <QuicParamsForm
              base={[...base, 'quicParams']}
              form={form}
              showUdpHop={side === 'server'}
            />
          )}
        </>
      )}
    </>
  );
}

function TcpMasksList({
  base,
  form,
  allowFilter,
  allowPresets,
}: {
  base: (string | number)[];
  form: FormInstance;
  allowFilter: boolean;
  allowPresets: boolean;
}) {
  const { t } = useTranslation();
  const uid = useId();
  return (
    <Form.List name={[...base, 'tcp']}>
      {(fields, { add, remove }) => (
        <>
          <Form.Item label="TCP 混淆">
            <Space>
              <Button
                type="primary"
                size="small"
                icon={<PlusOutlined aria-hidden />}
                aria-label={t('add')}
                onClick={() =>
                  add({ type: 'fragment', settings: defaultTcpMaskSettings('fragment') })
                }
              />
              {allowPresets && (
                <Select
                  id={`${uid}-tcp-presets`}
                  size="small"
                  style={{ minWidth: 160 }}
                  aria-label={t('pages.inbounds.form.presets')}
                  placeholder={t('pages.inbounds.form.presets')}
                  value={null}
                  options={Object.keys(TCP_MASK_PRESETS).map((name) => ({
                    value: name,
                    label: name,
                  }))}
                  onChange={(name: string) =>
                    form.setFieldValue([...base, 'tcp'], structuredClone(TCP_MASK_PRESETS[name]))
                  }
                />
              )}
            </Space>
          </Form.Item>
          {fields.map((field, mIdx) => (
            <TcpMaskItem
              key={field.key}
              fieldName={field.name}
              displayIndex={mIdx + 1}
              form={form}
              listPath={[...base, 'tcp']}
              allowFilter={allowFilter}
              onRemove={() => remove(field.name)}
            />
          ))}
        </>
      )}
    </Form.List>
  );
}

function TcpMaskItem({
  fieldName,
  displayIndex,
  form,
  listPath,
  allowFilter,
  onRemove,
}: {
  fieldName: number;
  displayIndex: number;
  form: FormInstance;
  listPath: (string | number)[];
  allowFilter: boolean;
  onRemove: () => void;
}) {
  // Absolute path for setFieldValue side effects (resetting settings on
  // type change). All Form.Item `name=` use RELATIVE paths within the
  // outer Form.List context.
  const absolutePath = [...listPath, fieldName];
  const { t } = useTranslation();

  return (
    <div>
      <Divider style={{ margin: 0 }}>
        TCP 混淆 {displayIndex}
        <DeleteOutlined
          className="danger-icon"
          role="button"
          tabIndex={0}
          aria-label={t('remove')}
          onClick={onRemove}
          onKeyDown={activateOnKey(onRemove)}
        />
      </Divider>

      <Form.Item label="类型" name={[fieldName, 'type']}>
        <Select
          onChange={(v) =>
            form.setFieldValue([...absolutePath, 'settings'], defaultTcpMaskSettings(v))
          }
          options={[
            { value: 'fragment', label: '分片' },
            { value: 'header-custom', label: '自定义头部' },
            { value: 'sudoku', label: '数独混淆' },
            { value: 'xmc', label: 'XMC (Minecraft)' },
            ...(allowFilter ? [FILTER_MASK_OPTION] : []),
          ]}
        />
      </Form.Item>

      <Form.Item
        noStyle
        shouldUpdate={(prev, curr) => {
          const a = getDeep(prev, [...absolutePath, 'type']);
          const b = getDeep(curr, [...absolutePath, 'type']);
          return a !== b;
        }}
      >
        {({ getFieldValue }) => {
          const type = getFieldValue([...absolutePath, 'type']) as string | undefined;
          if (type === 'fragment') {
            return (
              <>
                <Form.Item
                  label="数据包"
                  name={[fieldName, 'settings', 'packets']}
                  rules={[{ validator: validateFragmentPackets }]}
                >
                  <AutoComplete
                    options={[
                      { value: 'tlshello', label: 'tlshello' },
                      { value: '1-3', label: '1-3' },
                      { value: '1-5', label: '1-5' },
                    ]}
                    placeholder="tlshello 或 n-m 范围，例如 1-3"
                  />
                </Form.Item>
                <FragmentRangeList
                  listName={[fieldName, 'settings', 'lengths']}
                  label="长度"
                  placeholder="例如 100-200"
                  minItems={1}
                  validator={validateFragmentLength}
                />
                <FragmentRangeList
                  listName={[fieldName, 'settings', 'delays']}
                  label="延迟"
                  placeholder="例如 10-20 或 0"
                  validator={validateFragmentDelayEntry}
                />
                <Form.Item label="最大拆分次数" name={[fieldName, 'settings', 'maxSplit']}>
                  <Input />
                </Form.Item>
              </>
            );
          }
          if (type === 'sudoku') return <SudokuSettings fieldName={fieldName} />;
          if (type === 'filter') return <FilterMaskSettings fieldName={fieldName} />;
          if (type === 'header-custom') {
            return (
              <HeaderCustomGroups
                tcpFieldName={fieldName}
                form={form}
                absoluteSettingsPath={[...absolutePath, 'settings']}
              />
            );
          }
          if (type === 'xmc') {
            return (
              <>
                <Form.Item label="主机名" name={[fieldName, 'settings', 'hostname']}>
                  <Input placeholder="握手时模拟的服务器地址" />
                </Form.Item>
                <XmcProfilesList tcpFieldName={fieldName} />
                <Form.Item label="密码" required>
                  <Space.Compact block>
                    <Form.Item
                      name={[fieldName, 'settings', 'password']}
                      noStyle
                      rules={[{ required: true, message: '请填写密码' }]}
                    >
                      <Input
                        aria-label="密码"
                        placeholder="混淆密码"
                        style={{ width: 'calc(100% - 32px)' }}
                      />
                    </Form.Item>
                    <Button
                      icon={<ReloadOutlined aria-hidden />}
                      aria-label={t('regenerate')}
                      onClick={() =>
                        form.setFieldValue(
                          [...absolutePath, 'settings', 'password'],
                          RandomUtil.randomLowerAndNum(16),
                        )
                      }
                    />
                  </Space.Compact>
                </Form.Item>
              </>
            );
          }
          return null;
        }}
      </Form.Item>
    </div>
  );
}

const FILTER_MASK_OPTION = { value: 'filter', label: '来源 IP 筛选' };
const FILTER_ALLOWED_HINT = '仅允许这些来源连接；留空则允许所有来源';
const FILTER_BLOCKED_HINT = '执行其他混淆前先丢弃这些来源的连接';

// xray-core 26.10.21 (#7206) screens a listener's source IPs; a blocked match wins over an allowed
// one, and an empty list leaves that side open. Links and JSON subscriptions drop it.
function FilterMaskSettings({ fieldName }: { fieldName: number }) {
  const uid = useId();
  return (
    <>
      <Form.Item
        label="允许的来源 IP"
        name={[fieldName, 'settings', 'allowedSourceIPs']}
        tooltip={FILTER_ALLOWED_HINT}
      >
        <Select
          aria-describedby={`${uid}-allowed-hint`}
          mode="tags"
          style={{ width: '100%' }}
          tokenSeparators={[',', ' ']}
          placeholder="10.0.0.0/8, geoip:ir"
        />
      </Form.Item>
      <span id={`${uid}-allowed-hint`} hidden>
        {FILTER_ALLOWED_HINT}
      </span>
      <Form.Item
        label="阻止的来源 IP"
        name={[fieldName, 'settings', 'blockedSourceIPs']}
        tooltip={FILTER_BLOCKED_HINT}
      >
        <Select
          aria-describedby={`${uid}-blocked-hint`}
          mode="tags"
          style={{ width: '100%' }}
          tokenSeparators={[',', ' ']}
          placeholder="203.0.113.7, geoip:cn"
        />
      </Form.Item>
      <span id={`${uid}-blocked-hint`} hidden>
        {FILTER_BLOCKED_HINT}
      </span>
    </>
  );
}

function SudokuSettings({ fieldName }: { fieldName: number }) {
  return (
    <>
      <Form.Item label="密码" name={[fieldName, 'settings', 'password']}>
        <Input />
      </Form.Item>
      <Form.Item label="ASCII" name={[fieldName, 'settings', 'ascii']}>
        <Input />
      </Form.Item>
      <Form.Item label="自定义表" name={[fieldName, 'settings', 'customTable']}>
        <Input />
      </Form.Item>
      <Form.Item label="自定义表列表" name={[fieldName, 'settings', 'customTables']}>
        <Select mode="tags" style={{ width: '100%' }} tokenSeparators={[',']} />
      </Form.Item>
      <Form.Item label="最小填充长度" name={[fieldName, 'settings', 'paddingMin']}>
        <InputNumber min={0} />
      </Form.Item>
      <Form.Item label="最大填充长度" name={[fieldName, 'settings', 'paddingMax']}>
        <InputNumber min={0} />
      </Form.Item>
    </>
  );
}

// xray's fragment `packets` accepts "tlshello" or an arbitrary packet-number
// range like "1-3" (#5075 — presets only covered the common cases).
function validateFragmentPackets(_rule: unknown, value: unknown): Promise<void> {
  const str = typeof value === 'string' ? value.trim() : String(value ?? '').trim();
  if (str.length === 0 || str === 'tlshello' || /^\d+-\d+$/.test(str)) {
    return Promise.resolve();
  }
  return Promise.reject(new Error('请填写 tlshello 或数据包范围，例如 1-3'));
}

// xray refuses only a 0 minimum in the LAST lengths entry; an earlier 0 is an
// empty segment, which PattN's tlshello-0-len preset relies on.
function validateFragmentLength(_rule: unknown, value: unknown, isLast: boolean): Promise<void> {
  const str = typeof value === 'string' ? value.trim() : String(value ?? '').trim();
  if (str.length === 0) {
    return Promise.reject(new Error('请填写长度，不需要时请删除此行'));
  }
  const min = Number(str.split('-')[0]);
  if (!Number.isFinite(min) || min < 0 || (isLast && min === 0)) {
    return Promise.reject(new Error('最小长度必须大于 0，例如 100-200'));
  }
  return Promise.resolve();
}

// A delay segment is a millisecond value or range; 0 is allowed (no delay),
// but an empty row would serialize as "" and break xray's Int32Range parse,
// so require a value and let the user remove the row instead.
function validateFragmentDelayEntry(_rule: unknown, value: unknown): Promise<void> {
  const str = typeof value === 'string' ? value.trim() : String(value ?? '').trim();
  if (str.length === 0) {
    return Promise.reject(new Error('请填写延迟，不需要延迟时请删除此行'));
  }
  if (!/^\d+(?:-\d+)?$/.test(str)) {
    return Promise.reject(new Error('请填写延迟（毫秒），例如 10 或 10-20'));
  }
  return Promise.resolve();
}

// Per-segment range list for a fragment mask's `lengths`/`delays` (xray-core
// #6334): an editable list of dash-range strings. xray applies entry N to
// fragment segment N, clamping to the last entry. `minItems` keeps at least
// one length row so the config never collapses to an empty (rejected) list.
function FragmentRangeList({
  listName,
  label,
  placeholder,
  validator,
  minItems = 0,
}: {
  listName: (string | number)[];
  label: string;
  placeholder: string;
  validator?: (rule: unknown, value: unknown, isLast: boolean) => Promise<void>;
  minItems?: number;
}) {
  const { t } = useTranslation();
  return (
    <Form.List name={listName}>
      {(fields, { add, remove }) => (
        <>
          <Form.Item label={label}>
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined aria-hidden />}
              aria-label={t('add')}
              onClick={() => add('')}
            />
          </Form.Item>
          {fields.map((field, idx) => (
            <Form.Item
              key={field.key}
              label={`#${idx + 1}`}
              name={field.name}
              rules={
                validator
                  ? [{ validator: (r, v) => validator(r, v, idx === fields.length - 1) }]
                  : undefined
              }
            >
              <Input
                placeholder={placeholder}
                suffix={
                  fields.length > minItems ? (
                    <DeleteOutlined
                      className="danger-icon"
                      role="button"
                      tabIndex={0}
                      aria-label={t('remove')}
                      onClick={() => remove(field.name)}
                      onKeyDown={activateOnKey(() => remove(field.name))}
                    />
                  ) : null
                }
              />
            </Form.Item>
          ))}
        </>
      )}
    </Form.List>
  );
}

// randRange bytes must sit in 0-255 — xray rejects the whole config with
// "invalid randRange" otherwise (reversed ranges like "200-100" are fine,
// xray reorders them).
function validateRandRange(_rule: unknown, value: unknown): Promise<void> {
  const str = typeof value === 'string' ? value.trim() : String(value ?? '').trim();
  if (str.length === 0) return Promise.resolve();
  const m = /^(\d{1,3})(?:-(\d{1,3}))?$/.exec(str);
  if (!m) return Promise.reject(new Error('请填写字节值或范围，例如 0-255'));
  const from = Number(m[1]);
  const to = m[2] !== undefined ? Number(m[2]) : from;
  if (from > 255 || to > 255) {
    return Promise.reject(new Error('随机字节范围必须在 0-255 之间'));
  }
  return Promise.resolve();
}

function getDeep(obj: unknown, path: (string | number)[]): unknown {
  let cur: unknown = obj;
  for (const key of path) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string | number, unknown>)[key];
  }
  return cur;
}

// Mojang hands the profile UUID back undashed from the session server and
// dashed from most other endpoints; xray-core parses either, so accept both
// rather than forcing the operator to reformat what they pasted.
const XMC_UUID_PATTERN =
  /^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})$/;
const XMC_USERNAME_PATTERN = /^[A-Za-z0-9_]{3,16}$/;

function validateXmcUsername(_rule: unknown, value: unknown): Promise<void> {
  if (typeof value === 'string' && XMC_USERNAME_PATTERN.test(value)) return Promise.resolve();
  return Promise.reject(new Error('长度为 3-16 个字符，仅允许字母、数字和下划线'));
}

function validateXmcUuid(_rule: unknown, value: unknown): Promise<void> {
  if (typeof value === 'string' && XMC_UUID_PATTERN.test(value.trim())) return Promise.resolve();
  return Promise.reject(new Error('请填写配置 UUID（带连字符或 32 位十六进制字符）'));
}

// Each mask needs at least one fully signed profile since xray-core #6487 —
// an empty or partial list makes the core reject the whole config, so the
// panel blocks the save here rather than letting the backend drop the mask.
function XmcProfilesList({ tcpFieldName }: { tcpFieldName: number }) {
  const { t } = useTranslation();
  return (
    <Form.List name={[tcpFieldName, 'settings', 'profiles']}>
      {(profiles, { add, remove }) => (
        <>
          <Form.Item
            label="配置列表"
            extra="使用已签名的 Minecraft 会话配置：先根据用户名解析 UUID，再以 unsigned=false 获取配置。"
          >
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined aria-hidden />}
              aria-label={t('add')}
              onClick={() => add(defaultXmcProfile())}
            />
          </Form.Item>
          {profiles.map((profile, idx) => (
            <div key={profile.key}>
              <Divider style={{ margin: 0 }}>
                配置 {idx + 1}
                <DeleteOutlined
                  className="danger-icon"
                  role="button"
                  tabIndex={0}
                  aria-label={t('remove')}
                  onClick={() => remove(profile.name)}
                  onKeyDown={activateOnKey(() => remove(profile.name))}
                />
              </Divider>
              <Form.Item
                label="用户名"
                name={[profile.name, 'username']}
                rules={[{ validator: validateXmcUsername }]}
              >
                <Input placeholder="Notch" />
              </Form.Item>
              <Form.Item
                label="UUID"
                name={[profile.name, 'uuid']}
                rules={[{ validator: validateXmcUuid }]}
              >
                <Input placeholder="069a79f4-44e9-4726-a5be-fca90e38aaf5" />
              </Form.Item>
              <Form.Item
                label="纹理值"
                name={[profile.name, 'texturesValue']}
                rules={[{ required: true, message: '请填写纹理值' }]}
              >
                <Input.TextArea
                  autoSize={{ minRows: 2, maxRows: 4 }}
                  placeholder="会话配置中的 Base64 纹理值"
                />
              </Form.Item>
              <Form.Item
                label="纹理签名"
                name={[profile.name, 'texturesSignature']}
                rules={[{ required: true, message: '请填写纹理签名' }]}
              >
                <Input.TextArea
                  autoSize={{ minRows: 2, maxRows: 4 }}
                  placeholder="会话配置中的 Base64 签名"
                />
              </Form.Item>
            </div>
          ))}
        </>
      )}
    </Form.List>
  );
}

const HEADER_CUSTOM_TCP_GROUPS = [
  { key: 'clients', label: '客户端', tooltip: undefined },
  { key: 'servers', label: '服务端', tooltip: undefined },
  {
    key: 'errors',
    label: '错误响应',
    tooltip: '客户端分组匹配失败时发送的响应，例如 HTTP 400',
  },
] as const;

const UDP_MODE_HINT = '前缀模式在每个数据包中添加头部；独立模式先通过单独的握手数据包交换头部';
const NOISE_RESET_HINT = '再次向同一目标发送噪声的间隔（秒）；0 表示只发送一次';
const NOISE_SCRIPT_HINT =
  '由 xray-core 执行并生成噪声的 Lua 文件，位于 Xray 程序所在目录；将替代噪声列表';
const DOH_TLS_HINT = 'HTTPS 地址的 TLS 配置，例如用于域名前置的 serverName';
const DOH_QUIC_HINT = '通过 HTTP/3 访问 HTTPS 地址时使用的 QUIC 参数';
const REUSE_HINT = '写入前面条目以此名称捕获的字节';
const TRANSFORM_HINT =
  '表达式树，例如 {"op": "concat", "args": [{"type": "str", "bytes": "OK "}, {"reuse": "nonce"}]}';
const CAPTURE_HINT = '以指定名称保存当前条目写入或读取的字节，供后续条目复用';
const UDP_HOP_HINT = '向客户端公布端口跳跃范围；服务器仅监听自身端口';

function HeaderCustomGroups({
  tcpFieldName,
  form,
  absoluteSettingsPath,
}: {
  tcpFieldName: number;
  form: FormInstance;
  absoluteSettingsPath: (string | number)[];
}) {
  const { t } = useTranslation();
  const uid = useId();
  return (
    <>
      {HEADER_CUSTOM_TCP_GROUPS.map(({ key: groupKey, label, tooltip }) => (
        <Form.List key={groupKey} name={[tcpFieldName, 'settings', groupKey]}>
          {(groups, { add: addGroup, remove: removeGroup }) => (
            <>
              <Form.Item label={label} tooltip={tooltip}>
                <Button
                  type="primary"
                  size="small"
                  icon={<PlusOutlined aria-hidden />}
                  aria-label={t('add')}
                  aria-describedby={`${uid}-${groupKey}-hint`}
                  onClick={() => addGroup([defaultClientServerItem()])}
                />
                <span id={`${uid}-${groupKey}-hint`} hidden>
                  {tooltip}
                </span>
              </Form.Item>
              {groups.map((group, gi) => (
                <div key={group.key}>
                  <Divider style={{ margin: 0 }}>
                    {label} 分组 {gi + 1}
                    <DeleteOutlined
                      className="danger-icon"
                      role="button"
                      tabIndex={0}
                      aria-label={t('remove')}
                      onClick={() => removeGroup(group.name)}
                      onKeyDown={activateOnKey(() => removeGroup(group.name))}
                    />
                  </Divider>
                  <Form.List name={[group.name]}>
                    {(items, { add: addItem, remove: removeItem }) => (
                      <>
                        <Form.Item label="条目">
                          <Button
                            size="small"
                            icon={<PlusOutlined aria-hidden />}
                            aria-label={t('add')}
                            onClick={() => addItem(defaultClientServerItem())}
                          />
                        </Form.Item>
                        {items.map((item) => (
                          <ItemEditor
                            key={item.key}
                            fieldName={item.name}
                            form={form}
                            absoluteItemPath={[
                              ...absoluteSettingsPath,
                              groupKey,
                              group.name,
                              item.name,
                            ]}
                            delayMode="number"
                            allowVariables
                            onRemove={() => removeItem(item.name)}
                          />
                        ))}
                      </>
                    )}
                  </Form.List>
                </div>
              ))}
            </>
          )}
        </Form.List>
      ))}
    </>
  );
}

function UdpMasksList({
  base,
  form,
  isHysteria,
  isWireguard,
  network,
  allowUdpHop,
  allowFilter,
}: {
  base: (string | number)[];
  form: FormInstance;
  isHysteria: boolean;
  isWireguard: boolean;
  network: string;
  allowUdpHop: boolean;
  allowFilter: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Form.List name={[...base, 'udp']}>
      {(fields, { add, remove }) => (
        <>
          <Form.Item label="UDP 混淆">
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined aria-hidden />}
              aria-label={t('add')}
              onClick={() => {
                const def = isHysteria || isWireguard ? 'salamander' : 'mkcp-legacy';
                add({ type: def, settings: defaultUdpMaskSettings(def) });
              }}
            />
          </Form.Item>
          {fields.map((field, mIdx) => (
            <UdpMaskItem
              key={field.key}
              fieldName={field.name}
              displayIndex={mIdx + 1}
              form={form}
              listPath={[...base, 'udp']}
              isHysteria={isHysteria}
              isWireguard={isWireguard}
              network={network}
              allowUdpHop={allowUdpHop}
              allowFilter={allowFilter}
              onRemove={() => remove(field.name)}
            />
          ))}
        </>
      )}
    </Form.List>
  );
}

function UdpMaskItem({
  fieldName,
  displayIndex,
  form,
  listPath,
  isHysteria,
  isWireguard,
  network,
  allowUdpHop,
  allowFilter,
  onRemove,
}: {
  fieldName: number;
  displayIndex: number;
  form: FormInstance;
  listPath: (string | number)[];
  isHysteria: boolean;
  isWireguard: boolean;
  network: string;
  allowUdpHop: boolean;
  allowFilter: boolean;
  onRemove: () => void;
}) {
  const absolutePath = [...listPath, fieldName];
  const { t } = useTranslation();

  const onTypeChange = (v: string) => {
    form.setFieldValue([...absolutePath, 'settings'], defaultUdpMaskSettings(v));
    if (network === 'kcp') {
      const kcpMtuPath = [...listPath.slice(0, -1), 'kcpSettings', 'mtu'];
      form.setFieldValue(kcpMtuPath, v === 'xdns' ? 900 : 1350);
    }
  };

  const udpHopOption = allowUdpHop ? [{ value: 'udphop', label: 'UDP 端口跳跃' }] : [];
  const filterOption = allowFilter ? [FILTER_MASK_OPTION] : [];
  const options = isHysteria
    ? [{ value: 'salamander', label: 'Salamander (Hysteria2)' }, ...udpHopOption, ...filterOption]
    : [
        // Salamander is the mask xray-core's own wireguard finalmask example
        // uses; it stays hysteria-only elsewhere to keep legacy parity.
        ...(isWireguard ? [{ value: 'salamander', label: 'Salamander' }] : []),
        { value: 'mkcp-legacy', label: '传统 mKCP' },
        { value: 'xdns', label: 'xDNS' },
        { value: 'xicmp', label: 'xICMP' },
        { value: 'realm', label: 'Realm' },
        { value: 'header-custom', label: '自定义头部' },
        { value: 'noise', label: '噪声' },
        { value: 'sudoku', label: '数独混淆' },
        ...udpHopOption,
        ...filterOption,
      ];

  return (
    <div>
      <Divider style={{ margin: 0 }}>
        UDP 混淆 {displayIndex}
        <DeleteOutlined
          className="danger-icon"
          role="button"
          tabIndex={0}
          aria-label={t('remove')}
          onClick={onRemove}
          onKeyDown={activateOnKey(onRemove)}
        />
      </Divider>

      <Form.Item label="类型" name={[fieldName, 'type']}>
        <Select onChange={onTypeChange} options={options} />
      </Form.Item>

      <Form.Item
        noStyle
        shouldUpdate={(prev, curr) =>
          getDeep(prev, [...absolutePath, 'type']) !== getDeep(curr, [...absolutePath, 'type'])
        }
      >
        {({ getFieldValue }) => {
          const type = getFieldValue([...absolutePath, 'type']) as string | undefined;
          if (type === 'salamander') {
            return (
              <SalamanderUdpMaskSettings
                fieldName={fieldName}
                form={form}
                absolutePath={absolutePath}
              />
            );
          }
          if (type === 'mkcp-legacy') {
            return (
              <>
                <Form.Item label="头部" name={[fieldName, 'settings', 'header']}>
                  <Select
                    options={[
                      { value: '', label: '原始格式 / AES-128-GCM' },
                      { value: 'dns', label: 'DNS' },
                      { value: 'dtls', label: 'DTLS 1.2' },
                      { value: 'srtp', label: 'SRTP' },
                      { value: 'utp', label: 'uTP' },
                      { value: 'wechat', label: '微信视频' },
                      { value: 'wireguard', label: 'WireGuard' },
                    ]}
                  />
                </Form.Item>
                <Form.Item label="值" name={[fieldName, 'settings', 'value']}>
                  <Input placeholder="密码（AES-128-GCM）或域名（DNS 头部）" />
                </Form.Item>
              </>
            );
          }
          if (type === 'xdns') {
            return <XdnsSettings udpFieldName={fieldName} />;
          }
          if (type === 'udphop') {
            return <UdpHopSettings udpFieldName={fieldName} />;
          }
          if (type === 'filter') return <FilterMaskSettings fieldName={fieldName} />;
          if (type === 'xicmp') {
            return (
              <>
                <Form.Item
                  label="数据报套接字"
                  name={[fieldName, 'settings', 'dgram']}
                  valuePropName="checked"
                >
                  <Switch />
                </Form.Item>
                <Form.Item label="IP 地址" name={[fieldName, 'settings', 'ips']}>
                  <Select mode="tags" style={{ width: '100%' }} tokenSeparators={[',']} />
                </Form.Item>
              </>
            );
          }
          if (type === 'realm') {
            return (
              <>
                <Form.Item label="URL" name={[fieldName, 'settings', 'url']}>
                  <Input placeholder="realm://token@host:port/id" />
                </Form.Item>
                <Form.Item label="STUN 服务器" name={[fieldName, 'settings', 'stunServers']}>
                  <Select
                    mode="tags"
                    style={{ width: '100%' }}
                    tokenSeparators={[',']}
                    placeholder="host:port"
                  />
                </Form.Item>
                <Form.Item label="IP 模式" name={[fieldName, 'settings', 'ipMode']}>
                  <Select
                    allowClear
                    placeholder="双栈"
                    options={[
                      { value: 'dual', label: '双栈' },
                      { value: 'v4', label: 'IPv4' },
                      { value: 'v6', label: 'IPv6' },
                    ]}
                  />
                </Form.Item>
                <Form.Item
                  label="端口映射（UPnP / NAT-PMP）"
                  name={[fieldName, 'settings', 'portMapping', 'enabled']}
                  valuePropName="checked"
                >
                  <Switch />
                </Form.Item>
                <Form.Item
                  label="映射超时（秒）"
                  name={[fieldName, 'settings', 'portMapping', 'timeout']}
                >
                  <InputNumber min={0} placeholder="默认值：10" />
                </Form.Item>
                <Form.Item
                  label="映射有效期（秒）"
                  name={[fieldName, 'settings', 'portMapping', 'lifetime']}
                >
                  <InputNumber min={0} placeholder="默认值：600" />
                </Form.Item>
                <Divider plain style={{ margin: '8px 0' }}>
                  TLS（可选）
                </Divider>
                <Form.Item
                  label="服务器名称"
                  name={[fieldName, 'settings', 'tlsConfig', 'serverName']}
                >
                  <Input placeholder="Realm 服务器的 SNI，留空则跳过 TLS" />
                </Form.Item>
                <Form.Item label="ALPN" name={[fieldName, 'settings', 'tlsConfig', 'alpn']}>
                  <Select
                    mode="multiple"
                    style={{ width: '100%' }}
                    options={[
                      { value: 'h3', label: 'h3' },
                      { value: 'h2', label: 'h2' },
                      { value: 'http/1.1', label: 'http/1.1' },
                    ]}
                  />
                </Form.Item>
                <Form.Item label="指纹" name={[fieldName, 'settings', 'tlsConfig', 'fingerprint']}>
                  <Select allowClear style={{ width: '100%' }} options={UTLS_FINGERPRINT_OPTIONS} />
                </Form.Item>
                <Form.Item
                  label="允许不安全连接"
                  name={[fieldName, 'settings', 'tlsConfig', 'allowInsecure']}
                  valuePropName="checked"
                >
                  <Switch />
                </Form.Item>
              </>
            );
          }
          if (type === 'header-custom') {
            return (
              <UdpHeaderCustom
                udpFieldName={fieldName}
                form={form}
                absoluteSettingsPath={[...absolutePath, 'settings']}
              />
            );
          }
          if (type === 'sudoku') return <SudokuSettings fieldName={fieldName} />;
          if (type === 'noise') {
            return (
              <NoiseItems
                udpFieldName={fieldName}
                form={form}
                absoluteSettingsPath={[...absolutePath, 'settings']}
              />
            );
          }
          return null;
        }}
      </Form.Item>
    </div>
  );
}

function SalamanderUdpMaskSettings({
  fieldName,
  form,
  absolutePath,
}: {
  fieldName: number;
  form: FormInstance;
  absolutePath: (string | number)[];
}) {
  const { t } = useTranslation();
  const uid = useId();
  const packetSizePath = [...absolutePath, 'settings', 'packetSize'];
  const packetSize = Form.useWatch(packetSizePath, { form, preserve: true });
  const mode = typeof packetSize === 'string' && packetSize.trim() !== '' ? 'gecko' : 'salamander';

  return (
    <>
      <Form.Item
        htmlFor={`${uid}-salamander-mode`}
        label="模式"
        extra={
          mode === 'gecko'
            ? 'Salamander 与 Gecko 组合：将每个数据包拆分为随机填充分片，大小限制在下方范围内，以抵抗包长指纹识别。保存为含 packetSize 的 Salamander 配置。'
            : '将每个数据包混淆为看似随机的字节。'
        }
      >
        <Select
          id={`${uid}-salamander-mode`}
          value={mode}
          onChange={(next) => {
            if (next === 'gecko') {
              const current = form.getFieldValue(packetSizePath);
              form.setFieldValue(
                packetSizePath,
                parseGeckoPacketSize(current)
                  ? current
                  : formatGeckoPacketSize(
                      DEFAULT_GECKO_PACKET_SIZE.min,
                      DEFAULT_GECKO_PACKET_SIZE.max,
                    ),
              );
            } else {
              form.setFieldValue(packetSizePath, undefined);
            }
          }}
          options={[
            { value: 'salamander', label: 'Salamander' },
            { value: 'gecko', label: 'Gecko（实验性）' },
          ]}
        />
      </Form.Item>

      <Form.Item label="密码">
        <Space.Compact block>
          <Form.Item
            name={[fieldName, 'settings', 'password']}
            noStyle
            rules={[{ validator: validateSalamanderPassword }]}
          >
            <Input
              aria-label="密码"
              placeholder="混淆密码"
              style={{ width: 'calc(100% - 32px)' }}
            />
          </Form.Item>
          <Button
            icon={<ReloadOutlined aria-hidden />}
            aria-label={t('regenerate')}
            onClick={() =>
              form.setFieldValue(
                [...absolutePath, 'settings', 'password'],
                RandomUtil.randomLowerAndNum(16),
              )
            }
          />
        </Space.Compact>
      </Form.Item>

      {mode === 'gecko' && (
        <Form.Item
          label="数据包大小"
          name={[fieldName, 'settings', 'packetSize']}
          rules={[{ validator: validateGeckoPacketSize }]}
          extra="保存为字符串范围，例如 512-1200。"
        >
          <GeckoPacketSizeInput />
        </Form.Item>
      )}
    </>
  );
}

function GeckoPacketSizeInput({
  id,
  value,
  onChange,
}: {
  id?: string;
  value?: string;
  onChange?: (value: string) => void;
}) {
  const { min, max } = splitGeckoPacketSize(value);

  return (
    <Space.Compact block>
      <InputNumber
        id={id}
        prefix="Min"
        min={GECKO_MIN_PACKET_SIZE}
        max={GECKO_MAX_PACKET_SIZE}
        precision={0}
        value={min}
        placeholder={String(DEFAULT_GECKO_PACKET_SIZE.min)}
        onChange={(next) => onChange?.(`${next ?? ''}-${max ?? ''}`)}
        style={{ width: '50%' }}
      />
      <InputNumber
        aria-label="数据包大小上限"
        prefix="Max"
        min={GECKO_MIN_PACKET_SIZE}
        max={GECKO_MAX_PACKET_SIZE}
        precision={0}
        value={max}
        placeholder={String(DEFAULT_GECKO_PACKET_SIZE.max)}
        onChange={(next) => onChange?.(`${min ?? ''}-${next ?? ''}`)}
        style={{ width: '50%' }}
      />
    </Space.Compact>
  );
}

function UdpHeaderCustom({
  udpFieldName,
  form,
  absoluteSettingsPath,
}: {
  udpFieldName: number;
  form: FormInstance;
  absoluteSettingsPath: (string | number)[];
}) {
  const { t } = useTranslation();
  const uid = useId();
  return (
    <>
      <Form.Item label="模式" name={[udpFieldName, 'settings', 'mode']} tooltip={UDP_MODE_HINT}>
        <Select
          aria-describedby={`${uid}-mode-hint`}
          allowClear
          placeholder="前缀"
          options={[
            { value: 'prefix', label: '前缀' },
            { value: 'standalone', label: '独立握手' },
          ]}
        />
      </Form.Item>
      <span id={`${uid}-mode-hint`} hidden>
        {UDP_MODE_HINT}
      </span>
      {(['client', 'server'] as const).map((groupKey) => (
        <Form.List key={groupKey} name={[udpFieldName, 'settings', groupKey]}>
          {(items, { add, remove }) => (
            <>
              <Form.Item label={groupKey === 'client' ? 'Client' : 'Server'}>
                <Button
                  type="primary"
                  size="small"
                  icon={<PlusOutlined aria-hidden />}
                  aria-label={t('add')}
                  onClick={() => add(defaultUdpClientServerItem())}
                />
              </Form.Item>
              {items.map((item, ci) => (
                <div key={item.key}>
                  <Divider style={{ margin: 0 }}>
                    {groupKey === 'client' ? 'Client' : 'Server'} {ci + 1}
                    <DeleteOutlined
                      className="danger-icon"
                      role="button"
                      tabIndex={0}
                      aria-label={t('remove')}
                      onClick={() => remove(item.name)}
                      onKeyDown={activateOnKey(() => remove(item.name))}
                    />
                  </Divider>
                  <ItemEditor
                    fieldName={item.name}
                    form={form}
                    absoluteItemPath={[...absoluteSettingsPath, groupKey, item.name]}
                    allowVariables
                    onRemove={() => remove(item.name)}
                  />
                </div>
              ))}
            </>
          )}
        </Form.List>
      ))}
    </>
  );
}

const XDNS_RECORD_TYPE_OPTIONS = [
  { value: 16, label: 'TXT' },
  { value: 1, label: 'A' },
  { value: 28, label: 'AAAA' },
  { value: 5, label: 'CNAME' },
];

// Every xdns key needs a registered field: the finalmask watch drops keys without one.
// Resolvers and extraPoll are read by clients only; a server keeps them for the share link.
function XdnsSettings({ udpFieldName }: { udpFieldName: number }) {
  const { t } = useTranslation();
  const uid = useId();
  return (
    <>
      <Form.List name={[udpFieldName, 'settings', 'domains']}>
        {(domains, { add, remove }) => (
          <>
            <Form.Item label="域名列表">
              <Button
                type="primary"
                size="small"
                icon={<PlusOutlined aria-hidden />}
                aria-label={t('add')}
                onClick={() => add({ names: [], edns0: XDNS_LEGACY_EDNS0 })}
              />
            </Form.Item>
            {domains.map((domain, di) => (
              <div key={domain.key}>
                <Divider style={{ margin: 0 }}>
                  域名 {di + 1}
                  <DeleteOutlined
                    className="danger-icon"
                    role="button"
                    tabIndex={0}
                    aria-label={t('remove')}
                    onClick={() => remove(domain.name)}
                    onKeyDown={activateOnKey(() => remove(domain.name))}
                  />
                </Divider>
                <Form.Item label="名称列表" name={[domain.name, 'names']}>
                  <Select
                    mode="tags"
                    style={{ width: '100%' }}
                    tokenSeparators={[',']}
                    placeholder="t.example.com"
                  />
                </Form.Item>
                <Form.Item label="记录类型" name={[domain.name, 'types']}>
                  <Select
                    mode="multiple"
                    options={XDNS_RECORD_TYPE_OPTIONS}
                    placeholder="客户端默认 TXT，服务端默认全部"
                  />
                </Form.Item>
                <Form.Item label="EDNS0" name={[domain.name, 'edns0']}>
                  <InputNumber min={512} max={4096} placeholder="关闭" />
                </Form.Item>
                <Form.Item label="长度上限" name={[domain.name, 'lenLimit']}>
                  <InputNumber min={0} max={255} placeholder="255" />
                </Form.Item>
                <Form.Item label="标签长度上限" name={[domain.name, 'labelLimit']}>
                  <InputNumber min={0} max={63} placeholder="63" />
                </Form.Item>
              </div>
            ))}
          </>
        )}
      </Form.List>
      <Form.List name={[udpFieldName, 'settings', 'resolvers']}>
        {(resolvers, { add, remove }) => (
          <>
            <Form.Item label="解析器（客户端）">
              <Button
                type="primary"
                size="small"
                icon={<PlusOutlined aria-hidden />}
                aria-label={t('add')}
                onClick={() => add({ addrs: [] })}
              />
            </Form.Item>
            {resolvers.map((resolver, ri) => (
              <div key={resolver.key}>
                <Divider style={{ margin: 0 }}>
                  解析器 {ri + 1}
                  <DeleteOutlined
                    className="danger-icon"
                    role="button"
                    tabIndex={0}
                    aria-label={t('remove')}
                    onClick={() => remove(resolver.name)}
                    onKeyDown={activateOnKey(() => remove(resolver.name))}
                  />
                </Divider>
                <Form.Item label="地址列表" name={[resolver.name, 'addrs']}>
                  <Select
                    mode="tags"
                    style={{ width: '100%' }}
                    tokenSeparators={[',', ' ']}
                    placeholder="udp://8.8.8.8:53, https://dns.google/dns-query"
                  />
                </Form.Item>
                <Form.Item label="工作线程" name={[resolver.name, 'worker']}>
                  <InputNumber min={1} placeholder="32" />
                </Form.Item>
                <Form.Item
                  label="DoH TLS"
                  name={[resolver.name, 'tlsConfig']}
                  tooltip={DOH_TLS_HINT}
                >
                  <JsonObjectInput
                    aria-describedby={`${uid}-doh-tls-hint-${ri}`}
                    placeholder='{"serverName": "dns.google", "fingerprint": "chrome"}'
                  />
                </Form.Item>
                <span id={`${uid}-doh-tls-hint-${ri}`} hidden>
                  {DOH_TLS_HINT}
                </span>
                <Form.Item
                  label="DoH QUIC"
                  name={[resolver.name, 'quicConfig']}
                  tooltip={DOH_QUIC_HINT}
                >
                  <JsonObjectInput
                    aria-describedby={`${uid}-doh-quic-hint-${ri}`}
                    placeholder='{"congestion": "bbr"}'
                  />
                </Form.Item>
                <span id={`${uid}-doh-quic-hint-${ri}`} hidden>
                  {DOH_QUIC_HINT}
                </span>
              </div>
            ))}
          </>
        )}
      </Form.List>
      <Form.Item label="额外轮询（客户端）" name={[udpFieldName, 'settings', 'extraPoll']}>
        <InputNumber min={0} max={3} placeholder="0" />
      </Form.Item>
    </>
  );
}

const UDP_HOP_MODES = [
  { value: 'intervalRemote', label: '定时轮换服务器端口' },
  { value: 'perConnRemote', label: '每个连接随机选择服务器端口' },
  { value: 'intervalLocal', label: '定时轮换本地端口' },
];

// xray-core lowercases each comma-separated mode, so a lowercase one (from an mport
// import) still maps onto its option here.
function udpHopModesFromWire(value: unknown): string[] {
  if (typeof value !== 'string') return [];
  return value
    .split(',')
    .map((mode) => mode.trim())
    .filter(Boolean)
    .map(
      (mode) =>
        UDP_HOP_MODES.find((m) => m.value.toLowerCase() === mode.toLowerCase())?.value ?? mode,
    );
}

function validateUdpHopInterval(_: unknown, value: unknown) {
  if (value == null || value === '') return Promise.resolve();
  const bounds = String(value)
    .split('-')
    .map((v) => Number(v.trim()));
  const valid = bounds.length <= 2 && bounds.every((v) => Number.isInteger(v) && v >= 5);
  return valid ? Promise.resolve() : Promise.reject(new Error('秒数必须 ≥ 5，例如 30 或 5-10'));
}

// Client-only hopping (xray-core 26.9.9); every key needs a registered field because the
// finalmask watch drops keys without one.
function UdpHopSettings({ udpFieldName }: { udpFieldName: number }) {
  return (
    <>
      <Form.Item
        label="模式"
        name={[udpFieldName, 'settings', 'mode']}
        getValueProps={(value) => ({ value: udpHopModesFromWire(value) })}
        normalize={(value: string[]) => value.join(',')}
        rules={[{ required: true, message: '请至少选择一种模式' }]}
      >
        <Select mode="multiple" options={UDP_HOP_MODES} />
      </Form.Item>
      <Form.Item
        label="间隔（秒）"
        name={[udpFieldName, 'settings', 'interval']}
        rules={[{ validator: validateUdpHopInterval }]}
      >
        <Input placeholder="30 或 5-10" />
      </Form.Item>
      <Form.Item label="远程端口" name={[udpFieldName, 'settings', 'remotePorts']}>
        <Input placeholder="20000-50000" />
      </Form.Item>
      <Form.Item label="远程 IP" name={[udpFieldName, 'settings', 'remoteIPs']}>
        <Select mode="tags" style={{ width: '100%' }} tokenSeparators={[',', ' ']} />
      </Form.Item>
    </>
  );
}

// xray-core reads reset as an Int32Range: a plain number or a "min-max" string.
function validateNoiseReset(_: unknown, value: unknown) {
  if (value == null || value === '') return Promise.resolve();
  const bounds = String(value)
    .split('-')
    .map((v) => Number(v.trim()));
  const valid =
    bounds.length <= 2 &&
    bounds.every((v) => Number.isInteger(v) && v >= 0) &&
    bounds[0] <= bounds[bounds.length - 1];
  return valid ? Promise.resolve() : Promise.reject(new Error('请填写秒数，例如 0 或 60-120'));
}

// xray-core 26.10.17 (#7124) refuses a noise script beside noise items, so a script replaces the list.
function NoiseItems({
  udpFieldName,
  form,
  absoluteSettingsPath,
}: {
  udpFieldName: number;
  form: FormInstance;
  absoluteSettingsPath: (string | number)[];
}) {
  const uid = useId();
  const script = Form.useWatch([...absoluteSettingsPath, 'script'], form) as string | undefined;
  return (
    <>
      <Form.Item
        label="重置"
        name={[udpFieldName, 'settings', 'reset']}
        tooltip={NOISE_RESET_HINT}
        rules={[{ validator: validateNoiseReset }]}
      >
        <Input placeholder="0 或 60-120" aria-describedby={`${uid}-reset-hint`} />
      </Form.Item>
      <span id={`${uid}-reset-hint`} hidden>
        {NOISE_RESET_HINT}
      </span>
      <Form.Item
        label="脚本"
        name={[udpFieldName, 'settings', 'script']}
        tooltip={NOISE_SCRIPT_HINT}
        normalize={(value: string) => value || undefined}
      >
        <Input allowClear placeholder="noise.lua" aria-describedby={`${uid}-script-hint`} />
      </Form.Item>
      <span id={`${uid}-script-hint`} hidden>
        {NOISE_SCRIPT_HINT}
      </span>
      {!script && (
        <NoiseItemList
          udpFieldName={udpFieldName}
          form={form}
          absoluteSettingsPath={absoluteSettingsPath}
        />
      )}
    </>
  );
}

function NoiseItemList({
  udpFieldName,
  form,
  absoluteSettingsPath,
}: {
  udpFieldName: number;
  form: FormInstance;
  absoluteSettingsPath: (string | number)[];
}) {
  const { t } = useTranslation();
  return (
    <>
      <Form.List name={[udpFieldName, 'settings', 'noise']}>
        {(items, { add, remove }) => (
          <>
            <Form.Item label="噪声">
              <Button
                type="primary"
                size="small"
                icon={<PlusOutlined aria-hidden />}
                aria-label={t('add')}
                onClick={() => add(defaultNoiseItem())}
              />
            </Form.Item>
            {items.map((item, ni) => (
              <div key={item.key}>
                <Divider style={{ margin: 0 }}>
                  噪声 {ni + 1}
                  <DeleteOutlined
                    className="danger-icon"
                    role="button"
                    tabIndex={0}
                    aria-label={t('remove')}
                    onClick={() => remove(item.name)}
                    onKeyDown={activateOnKey(() => remove(item.name))}
                  />
                </Divider>
                <ItemEditor
                  fieldName={item.name}
                  form={form}
                  absoluteItemPath={[...absoluteSettingsPath, 'noise', item.name]}
                  delayMode="string"
                  onRemove={() => remove(item.name)}
                />
              </div>
            ))}
          </>
        )}
      </Form.List>
    </>
  );
}

// xray-core 26.6 header-custom: an item is exactly one of raw bytes, a reused variable
// or a transform ("exactly one item kind must be set"); capture works with any of them.
type ItemKind = 'bytes' | 'reuse' | 'transform';

const ITEM_KIND_OPTIONS: { value: ItemKind; label: string }[] = [
  { value: 'bytes', label: '字节' },
  { value: 'reuse', label: '复用变量' },
  { value: 'transform', label: '转换' },
];

function itemKindOf(item: unknown): ItemKind {
  const fields = (item ?? {}) as Record<string, unknown>;
  if (typeof fields.reuse === 'string') return 'reuse';
  if (fields.transform != null) return 'transform';
  return 'bytes';
}

function validateCustomVarName(_: unknown, value: unknown) {
  if (value == null || value === '') return Promise.resolve();
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(String(value))
    ? Promise.resolve()
    : Promise.reject(new Error('仅允许字母、数字和下划线，且不能以数字开头'));
}

// Hands the form only a parsed JSON object, so half-typed text never reaches the config.
function JsonObjectInput({
  id,
  'aria-describedby': ariaDescribedBy,
  value,
  onChange,
  placeholder,
}: {
  id?: string;
  'aria-describedby'?: string;
  value?: Record<string, unknown>;
  onChange?: (next: Record<string, unknown> | undefined) => void;
  placeholder?: string;
}) {
  const [text, setText] = useState(() => (value ? JSON.stringify(value) : ''));
  const [invalid, setInvalid] = useState(false);
  return (
    <>
      <Input.TextArea
        id={id}
        aria-describedby={ariaDescribedBy}
        value={text}
        autoSize={{ minRows: 2, maxRows: 8 }}
        placeholder={placeholder}
        onChange={(e) => {
          const next = e.target.value;
          setText(next);
          if (next.trim() === '') {
            setInvalid(false);
            onChange?.(undefined);
            return;
          }
          try {
            const parsed: unknown = JSON.parse(next);
            const isObject = !!parsed && typeof parsed === 'object' && !Array.isArray(parsed);
            setInvalid(!isObject);
            if (isObject) onChange?.(parsed as Record<string, unknown>);
          } catch {
            setInvalid(true);
          }
        }}
      />
      {invalid && <Typography.Text type="danger">必须为 JSON 对象</Typography.Text>}
    </>
  );
}

function ItemEditor({
  fieldName,
  form,
  absoluteItemPath,
  delayMode,
  allowVariables = false,
  onRemove: _onRemove,
}: {
  fieldName: number;
  form: FormInstance;
  absoluteItemPath: (string | number)[];
  delayMode?: 'number' | 'string';
  allowVariables?: boolean;
  onRemove?: () => void;
}) {
  const { t } = useTranslation();
  const uid = useId();
  const [kind, setKind] = useState<ItemKind>(() =>
    allowVariables ? itemKindOf(form.getFieldValue(absoluteItemPath)) : 'bytes',
  );
  const onKindChange = (next: ItemKind) => {
    setKind(next);
    for (const key of ['type', 'packet', 'rand', 'randRange', 'reuse', 'transform']) {
      form.setFieldValue([...absoluteItemPath, key], undefined);
    }
    if (next === 'bytes') {
      form.setFieldValue([...absoluteItemPath, 'type'], 'array');
      form.setFieldValue([...absoluteItemPath, 'rand'], 0);
    }
  };
  /**
   * Switching to `array` clears the packet instead of emptying it to `[]`:
   * that branch is rand-driven, and xray-core counts even an empty array as a
   * packet, rejecting an item that carries both a packet and a rand. That
   * error fails the whole config, so one such item keeps every inbound offline.
   */
  const onTypeChange = (v: string) => {
    if (v === 'base64') {
      form.setFieldValue([...absoluteItemPath, 'packet'], RandomUtil.randomBase64());
    } else if (v === 'array') {
      form.setFieldValue([...absoluteItemPath, 'rand'], delayMode === 'string' ? '1-8192' : 0);
      form.setFieldValue([...absoluteItemPath, 'packet'], undefined);
    } else {
      form.setFieldValue([...absoluteItemPath, 'packet'], '');
    }
  };

  return (
    <>
      {allowVariables && (
        <Form.Item label="类别" htmlFor={`${uid}-kind`}>
          <Select
            id={`${uid}-kind`}
            value={kind}
            onChange={onKindChange}
            options={ITEM_KIND_OPTIONS}
          />
        </Form.Item>
      )}
      {kind === 'bytes' && (
        <Form.Item label="类型" name={[fieldName, 'type']}>
          <Select
            onChange={onTypeChange}
            options={[
              { value: 'array', label: '数组' },
              { value: 'str', label: '字符串' },
              { value: 'hex', label: '十六进制' },
              { value: 'base64', label: 'Base64' },
              // Only the noise mask parses tag expressions (xray-core 26.9.30, #6862).
              ...(delayMode === 'string' ? [{ value: 'exp', label: '表达式' }] : []),
            ]}
          />
        </Form.Item>
      )}

      {delayMode === 'number' && (
        <Form.Item label="延迟（毫秒）" name={[fieldName, 'delay']}>
          <InputNumber min={0} />
        </Form.Item>
      )}
      {delayMode === 'string' && (
        <Form.Item label="延迟" name={[fieldName, 'delay']}>
          <Input placeholder="10-20" />
        </Form.Item>
      )}

      {kind === 'reuse' && (
        <>
          <Form.Item
            label="复用"
            name={[fieldName, 'reuse']}
            tooltip={REUSE_HINT}
            rules={[
              { required: true, message: '请填写已捕获变量的名称' },
              { validator: validateCustomVarName },
            ]}
          >
            <Input placeholder="变量名称" aria-describedby={`${uid}-reuse-hint`} />
          </Form.Item>
          <span id={`${uid}-reuse-hint`} hidden>
            {REUSE_HINT}
          </span>
        </>
      )}
      {kind === 'transform' && (
        <>
          <Form.Item
            label="转换"
            name={[fieldName, 'transform']}
            tooltip={TRANSFORM_HINT}
            rules={[{ required: true, message: '请填写转换表达式' }]}
          >
            <JsonObjectInput
              aria-describedby={`${uid}-transform-hint`}
              placeholder='{"op": "concat", "args": [{"reuse": "nonce"}]}'
            />
          </Form.Item>
          <span id={`${uid}-transform-hint`} hidden>
            {TRANSFORM_HINT}
          </span>
        </>
      )}

      <Form.Item
        noStyle
        shouldUpdate={(prev, curr) =>
          getDeep(prev, [...absoluteItemPath, 'type']) !==
          getDeep(curr, [...absoluteItemPath, 'type'])
        }
      >
        {({ getFieldValue }) => {
          if (kind !== 'bytes') return null;
          const type = getFieldValue([...absoluteItemPath, 'type']) as string | undefined;
          if (type === 'array') {
            return (
              <>
                <Form.Item label="随机长度" name={[fieldName, 'rand']}>
                  {delayMode === 'string' ? (
                    <Input placeholder="0 或 1-8192" />
                  ) : (
                    <InputNumber min={0} />
                  )}
                </Form.Item>
                {/* Cleared must become undefined, not '': xray parses an
                    explicit "" as the range 0-0 (all-zero fill bytes), while
                    an omitted randRange falls back to the 0-255 default. */}
                <Form.Item
                  label="随机字节范围"
                  name={[fieldName, 'randRange']}
                  normalize={(v) => (v === '' ? undefined : v)}
                  rules={[{ validator: validateRandRange }]}
                >
                  <Input placeholder="0-255" />
                </Form.Item>
              </>
            );
          }
          if (type === 'base64') {
            return (
              <Form.Item label="数据包">
                <Space.Compact block>
                  <Form.Item name={[fieldName, 'packet']} noStyle>
                    <Input
                      aria-label="数据包"
                      placeholder="二进制数据"
                      style={{ width: 'calc(100% - 32px)' }}
                    />
                  </Form.Item>
                  <Button
                    icon={<ReloadOutlined aria-hidden />}
                    aria-label={t('regenerate')}
                    onClick={() =>
                      form.setFieldValue([...absoluteItemPath, 'packet'], RandomUtil.randomBase64())
                    }
                  />
                </Space.Compact>
              </Form.Item>
            );
          }
          return (
            <Form.Item label="数据包" name={[fieldName, 'packet']}>
              <Input placeholder={type === 'exp' ? '<b 0d0a0d0a><t><rc 20-40>' : 'binary data'} />
            </Form.Item>
          );
        }}
      </Form.Item>

      {allowVariables && (
        <>
          <Form.Item
            label="捕获"
            name={[fieldName, 'capture']}
            tooltip={CAPTURE_HINT}
            normalize={(v) => (v === '' ? undefined : v)}
            rules={[{ validator: validateCustomVarName }]}
          >
            <Input placeholder="变量名称" aria-describedby={`${uid}-capture-hint`} />
          </Form.Item>
          <span id={`${uid}-capture-hint`} hidden>
            {CAPTURE_HINT}
          </span>
        </>
      )}
    </>
  );
}

function QuicParamsForm({
  base,
  form,
  showUdpHop,
}: {
  base: (string | number)[];
  form: FormInstance;
  showUdpHop: boolean;
}) {
  const uid = useId();
  const congestion = Form.useWatch([...base, 'congestion'], form) as string | undefined;
  const udpHop = Form.useWatch([...base, 'udpHop'], { form, preserve: true }) as
    | Record<string, unknown>
    | undefined;
  const hasUdpHop = udpHop != null;

  return (
    <>
      <Form.Item label="拥塞控制" name={[...base, 'congestion']}>
        <Select
          options={[
            { value: 'reno', label: 'Reno' },
            { value: 'bbr', label: 'BBR' },
            { value: 'brutal', label: 'Brutal' },
            { value: 'force-brutal', label: '强制 Brutal' },
          ]}
        />
      </Form.Item>
      {congestion === 'bbr' && (
        <Form.Item label="BBR 策略" name={[...base, 'bbrProfile']}>
          <Select
            allowClear
            placeholder="标准"
            options={[
              { value: 'conservative', label: '保守' },
              { value: 'standard', label: '标准' },
              { value: 'aggressive', label: '激进' },
            ]}
          />
        </Form.Item>
      )}
      <Form.Item label="调试" name={[...base, 'debug']} valuePropName="checked">
        <Switch />
      </Form.Item>

      {(congestion === 'brutal' || congestion === 'force-brutal') && (
        <>
          <Form.Item label="Brutal 上行速率" name={[...base, 'brutalUp']}>
            <Input placeholder="例如 60 mbps" />
          </Form.Item>
          <Form.Item label="Brutal 下行速率" name={[...base, 'brutalDown']}>
            <Input placeholder="例如 100 mbps" />
          </Form.Item>
          <Form.Item
            label="禁用 Brutal 丢包补偿"
            name={[...base, 'brutalDisableLossCompensation']}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
        </>
      )}

      {/* Advertised to clients (link mport, JSON-subscription udphop mask); the server
          itself listens on its own port. */}
      {showUdpHop && (
        <>
          <Form.Item htmlFor={`${uid}-udp-hop`} label="UDP 端口跳跃" tooltip={UDP_HOP_HINT}>
            <Switch
              id={`${uid}-udp-hop`}
              aria-describedby={`${uid}-udp-hop-hint`}
              checked={hasUdpHop}
              onChange={(v) => {
                form.setFieldValue([...base, 'udpHop'], v ? defaultUdpHop() : undefined);
              }}
            />
            <span id={`${uid}-udp-hop-hint`} hidden>
              {UDP_HOP_HINT}
            </span>
          </Form.Item>
          {hasUdpHop && (
            <>
              <Form.Item label="跳跃端口" name={[...base, 'udpHop', 'ports']}>
                <Input placeholder="例如 20000-50000" />
              </Form.Item>
              <Form.Item label="跳跃间隔（秒）" name={[...base, 'udpHop', 'interval']}>
                <Input placeholder="例如 5-10" />
              </Form.Item>
            </>
          )}
        </>
      )}

      <Form.Item label="最大空闲超时（秒）" name={[...base, 'maxIdleTimeout']}>
        <InputNumber min={4} max={120} />
      </Form.Item>
      <Form.Item label="保活周期（秒）" name={[...base, 'keepAlivePeriod']}>
        <InputNumber min={2} max={60} />
      </Form.Item>
      <Form.Item
        label="禁用路径 MTU 探测"
        name={[...base, 'disablePathMTUDiscovery']}
        valuePropName="checked"
      >
        <Switch />
      </Form.Item>
      <Form.Item
        label="禁用 Chrome 指纹模拟"
        name={[...base, 'disableChromeParrot']}
        valuePropName="checked"
      >
        <Switch />
      </Form.Item>
      <Form.Item label="禁用 GSO" name={[...base, 'disableGSO']} valuePropName="checked">
        <Switch />
      </Form.Item>
      <Form.Item
        label="禁用无状态重置"
        name={[...base, 'disableStatelessReset']}
        valuePropName="checked"
      >
        <Switch />
      </Form.Item>

      <Form.Item label="最大入站流数量" name={[...base, 'maxIncomingStreams']}>
        <InputNumber min={8} placeholder="默认值：1024" />
      </Form.Item>
      <Form.Item label="初始流接收窗口" name={[...base, 'initStreamReceiveWindow']}>
        <InputNumber min={16384} placeholder="默认值：8388608" />
      </Form.Item>
      <Form.Item label="最大流接收窗口" name={[...base, 'maxStreamReceiveWindow']}>
        <InputNumber min={16384} placeholder="默认值：8388608" />
      </Form.Item>
      <Form.Item label="初始连接接收窗口" name={[...base, 'initConnectionReceiveWindow']}>
        <InputNumber min={16384} placeholder="默认值：20971520" />
      </Form.Item>
      <Form.Item label="最大连接接收窗口" name={[...base, 'maxConnectionReceiveWindow']}>
        <InputNumber min={16384} placeholder="默认值：20971520" />
      </Form.Item>
    </>
  );
}
