import { describe, expect, it } from 'vitest';

import { localizeApiDocument } from '@/pages/api-docs/localize-api-document';

describe('API document localization', () => {
  it('translates schema descriptions without changing fields or executable examples', () => {
    const schema = {
      type: 'object',
      properties: {
        description: { type: 'string', description: 'Client comment' },
        email: { type: 'string', description: 'Client email identifier' },
      },
      example: { description: 'Client comment', email: 'user@example.com' },
      examples: { original: { value: { description: 'Client comment' } } },
      default: { description: 'Client comment' },
    };

    const result = localizeApiDocument(schema);
    expect(result.properties.description.description).toBe('客户端备注。');
    expect(result.properties.email.description).toBe('客户端 email 标识。');
    expect(result.example).toBe(schema.example);
    expect(result.examples).toBe(schema.examples);
    expect(result.default).toBe(schema.default);
    expect(schema.properties.description.description).toBe('Client comment');
  });
});
