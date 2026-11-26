import descriptions from './schema-descriptions.zh-CN.json';

const chineseDescriptions: Record<string, string> = descriptions;
const exampleKeys = new Set(['example', 'examples', 'default', 'enum', 'const']);

// Only presentation metadata is localized. Property names, routes, schemas,
// example payloads and values used to execute/copy a request are preserved.
export function localizeApiDocument<T>(document: T): T {
  function visit(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(visit);
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => {
        if (exampleKeys.has(key)) return [key, item];
        if (['description', 'summary', 'title'].includes(key) && typeof item === 'string') {
          return [key, chineseDescriptions[item] ?? item];
        }
        return [key, visit(item)];
      }),
    );
  }
  return visit(document) as T;
}
