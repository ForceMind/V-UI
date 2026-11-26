import { afterEach, describe, expect, it, vi } from 'vitest';

describe('readyI18n page language', () => {
  afterEach(() => {
    document.cookie = 'lang=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
    document.cookie = 'subLang=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
    document.documentElement.lang = 'en';
  });

  it.each([
    ['panel', 'lang=fa-IR', 'zh-CN'],
    ['subscription', 'subLang=zh-TW', 'zh-TW'],
  ] as const)('sets <html lang> to the active %s locale', async (scope, cookie, locale) => {
    vi.resetModules();
    document.cookie = `${cookie}; path=/`;
    const { readyI18n } = await import('@/i18n/react');

    await readyI18n(scope);

    expect(document.documentElement.lang).toBe(locale);
  });

  it('falls back to Simplified Chinese when the subscription cookie names an unknown locale', async () => {
    vi.resetModules();
    document.cookie = 'subLang=xx-XX; path=/';
    const { readyI18n } = await import('@/i18n/react');

    await readyI18n('subscription');

    expect(document.documentElement.lang).toBe('zh-CN');
  });
});
