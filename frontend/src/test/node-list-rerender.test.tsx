import type { ReactNode } from 'react';
import { render } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import { ThemeProvider } from '@/hooks/useTheme';
import NodeList from '@/pages/nodes/NodeList';
import type { NodeRecord } from '@/schemas/node';

import { makeTestQueryClient } from './test-utils';

// Every heartbeat push re-rendered all rows, unchanged ones too: the columns and
// table props were rebuilt on each render, so every cell re-ran its renderer.
describe('NodeList re-render', () => {
  it('leaves the rows alone when its parent re-renders with the same nodes', () => {
    const queryClient = makeTestQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>{children}</ThemeProvider>
      </QueryClientProvider>
    );
    const unavailableMetrics: string[] = [];
    const metricsChecks = vi.spyOn(unavailableMetrics, 'includes');
    const nodes: NodeRecord[] = [1, 2, 3].map((id) => ({
      id,
      name: `node-${id}`,
      guid: `g${id}`,
      transitive: false,
      enable: true,
      status: 'online',
      panelVersion: '3.0.0',
      unavailableMetrics,
    }));
    const noop = () => {};
    const props = {
      nodes,
      isMobile: false,
      onAdd: noop,
      onMtls: noop,
      onEdit: noop,
      onDelete: noop,
      onProbe: noop,
      onToggleEnable: noop,
    };
    const view = render(<NodeList {...props} />, { wrapper });
    expect(metricsChecks).toHaveBeenCalled();

    metricsChecks.mockClear();
    view.rerender(<NodeList {...props} />);

    expect(metricsChecks).not.toHaveBeenCalled();
  });
});
