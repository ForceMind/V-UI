import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import SwaggerUI from 'swagger-ui-react';

import { chineseSwaggerPlugin } from '@/pages/api-docs/chinese-swagger';

describe('Chinese Swagger controls', () => {
  it('keeps the interactive API request editor and original schema fields', async () => {
    render(
      <SwaggerUI
        spec={{
          openapi: '3.0.3',
          info: { title: 'API', version: '1' },
          components: {
            securitySchemes: { AdminToken: { type: 'http', scheme: 'bearer' } },
          },
          security: [{ AdminToken: [] }],
          paths: {
            '/test': {
              get: {
                responses: { '200': { description: 'OK' } },
                parameters: [{ name: 'Name', in: 'query', schema: { type: 'string' } }],
              },
            },
          },
        }}
        plugins={[chineseSwaggerPlugin]}
        docExpansion="full"
        {...{ validatorUrl: null }}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '试用接口' }));
    expect(await screen.findByRole('button', { name: '执行' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '取消' })).toBeTruthy();
    expect(await screen.findByRole('heading', { name: '参数' })).toBeTruthy();
    expect(await screen.findByRole('heading', { name: '响应' })).toBeTruthy();
    expect(document.querySelector('.parameter__name')?.textContent).toBe('Name');

    fireEvent.click(screen.getByRole('button', { name: '授权' }));
    expect(await screen.findByText('可用授权方式')).toBeTruthy();
    expect(await screen.findByRole('button', { name: '关闭' })).toBeTruthy();
  });
});
