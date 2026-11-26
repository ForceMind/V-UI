const languageNames: Record<string, string> = {
  'ar-EG': '阿拉伯语',
  'en-US': '英语',
  'fa-IR': '波斯语',
  'zh-CN': '简体中文',
  'zh-TW': '繁体中文',
  'ja-JP': '日语',
  'ru-RU': '俄语',
  'vi-VN': '越南语',
  'es-ES': '西班牙语',
  'id-ID': '印度尼西亚语',
  'uk-UA': '乌克兰语',
  'tr-TR': '土耳其语',
  'pt-BR': '葡萄牙语',
  'sk-SK': '斯洛伐克语',
};

// Administrator labels only; subscription language names and choices are unchanged.
export function panelLanguageLabel(language: string): string {
  return languageNames[language] ?? '未知语言';
}
