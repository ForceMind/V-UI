import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import { LanguageManager, type LanguageScope } from '@/utils';
import { loadAntdLocale } from './AntdRootConfig';
import messages from '../../../internal/web/translation/zh-CN.json';

const language = 'zh-CN';

const subscriptionLocales = import.meta.glob([
  '../../../internal/web/translation/*.json',
  '!../../../internal/web/translation/zh-CN.json',
]);

export async function readyI18n(scope: LanguageScope = 'panel') {
  const requested = scope === 'subscription' ? LanguageManager.getLanguage(scope) : language;
  const key = `../../../internal/web/translation/${requested}.json`;
  const load = Object.hasOwn(subscriptionLocales, key) ? subscriptionLocales[key] : undefined;
  const active = load ? requested : language;
  await i18next.use(initReactI18next).init({
    lng: active,
    fallbackLng: language,
    resources: { [language]: { translation: messages } },
    interpolation: { escapeValue: false, prefix: '{', suffix: '}' },
    returnNull: false,
  });
  if (load) {
    const { default: subscriptionMessages } = (await load()) as {
      default: Record<string, unknown>;
    };
    i18next.addResourceBundle(active, 'translation', subscriptionMessages, true, true);
    await i18next.changeLanguage(active);
  }
  document.documentElement.lang = active;
  await loadAntdLocale(active);
  return i18next;
}

export { i18next as i18n };
