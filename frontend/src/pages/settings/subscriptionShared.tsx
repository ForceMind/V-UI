import { Tag } from 'antd';

export const isRemoteRoutingSource = (value: string) => /^https:\/\/\S+$/i.test(value.trim());

export const remoteSourceBadge = (value: string) =>
  isRemoteRoutingSource(value) ? <Tag color="blue">HTTPS 地址</Tag> : undefined;
