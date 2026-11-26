import { render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

// Without a ConfigProvider locale, antd's own strings (empty states, close buttons,
// pagination) stayed English. The administrator panel always uses Simplified Chinese.
describe('AntdRootConfig locale', () => {
  afterEach(() => {
    document.cookie = 'lang=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
  });

  it.each([
    ['fa-IR', '暂无数据'],
    ['en-US', '暂无数据'],
  ])('keeps panel built-in text Chinese despite a %s cookie', async (locale, emptyText) => {
    vi.resetModules();
    document.cookie = `lang=${locale}; path=/`;
    const { readyI18n } = await import('@/i18n/react');
    const { AntdRootConfig } = await import('@/i18n/AntdRootConfig');
    const { Empty } = await import('antd');

    await readyI18n('panel');
    render(
      <AntdRootConfig>
        <Empty />
      </AntdRootConfig>,
    );

    expect(document.querySelector('.ant-empty-description')?.textContent).toBe(emptyText);
    const { z } = await import('zod');
    const invalid = z.number().min(1).safeParse(0);
    expect(invalid.error?.issues[0].message).toMatch(/[\u4e00-\u9fff]/);
  });
});
