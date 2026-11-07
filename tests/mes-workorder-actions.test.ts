import { describe, expect, it } from 'vitest';

import { parseCardActionValue } from '../src/feishu-card-actions.js';
import {
  buildMesWorkOrderCard,
  dispatchMesWorkOrderAction,
  nextWorkOrderAction,
  parseMesWorkOrderValue,
  type MesWorkOrderRecord,
} from '../src/mes-workorder-actions.js';

function wo(overrides: Partial<MesWorkOrderRecord> = {}): MesWorkOrderRecord {
  return {
    wo_id: 'WO-1',
    equipment_id: 'CNC-03',
    fault_desc: "巡检发现['vibration']超限，疑似主轴轴承磨损",
    severity: 'high',
    status: 'open',
    assignee: null,
    plan: null,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** 按 URL 路由的假 fetch；记录每次调用，便于断言请求体。 */
function fakeFetch(routes: {
  action?: () => Response | Promise<Response>;
  wo?: () => Response | Promise<Response>;
  equipment?: () => Response | Promise<Response>;
  calls?: Array<{ url: string; init?: RequestInit }>;
}): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    routes.calls?.push({ url, init });
    if (url.includes('/api/work_orders/') && init?.method === 'POST') {
      return routes.action ? routes.action() : jsonResponse(wo({ status: 'assigned' }));
    }
    if (url.includes('/api/equipment')) {
      return routes.equipment
        ? routes.equipment()
        : jsonResponse([{ equipment_id: 'CNC-03', name: '卧式加工中心#3' }]);
    }
    return routes.wo ? routes.wo() : jsonResponse(wo());
  }) as typeof fetch;
}

describe('parseMesWorkOrderValue', () => {
  it('reads the value MES puts on the button', () => {
    expect(
      parseMesWorkOrderValue({
        action: 'mes_work_order',
        wo_id: 'WO-9',
        op: 'assign',
      }),
    ).toEqual({ action: 'mes_work_order', wo_id: 'WO-9', op: 'assign' });
  });

  it('rejects unknown actions, ops and blank ids', () => {
    expect(parseMesWorkOrderValue({ action: 'other', wo_id: 'WO-1', op: 'assign' })).toBeNull();
    expect(
      parseMesWorkOrderValue({ action: 'mes_work_order', wo_id: 'WO-1', op: 'delete' }),
    ).toBeNull();
    expect(parseMesWorkOrderValue({ action: 'mes_work_order', wo_id: '  ', op: 'done' })).toBeNull();
    expect(parseMesWorkOrderValue('mes_work_order')).toBeNull();
  });

  it('is reachable through the shared card-action parser', () => {
    expect(
      parseCardActionValue({ action: 'mes_work_order', wo_id: 'WO-2', op: 'close' }),
    ).toEqual({ action: 'mes_work_order', wo_id: 'WO-2', op: 'close' });
    // 既有按钮不能被新分支抢走
    expect(parseCardActionValue({ action: 'interrupt_stream' })).toEqual({
      action: 'interrupt_stream',
    });
  });
});

describe('nextWorkOrderAction', () => {
  it('follows the mes state machine open → assigned → done → closed', () => {
    expect(nextWorkOrderAction('open')?.op).toBe('assign');
    expect(nextWorkOrderAction('assigned')?.op).toBe('done');
    expect(nextWorkOrderAction('done')?.op).toBe('close');
    expect(nextWorkOrderAction('closed')).toBeUndefined();
  });
});

describe('buildMesWorkOrderCard', () => {
  it('renders the live work order with one next-step button', () => {
    const card = buildMesWorkOrderCard({
      wo: wo({ status: 'assigned', assignee: '王师傅' }),
      equipmentName: '卧式加工中心#3',
    }) as Record<string, any>;

    expect(card.schema).toBe('2.0');
    expect(card.config.update_multi).toBe(true);
    expect(card.header.template).toBe('red');
    expect(card.header.title.content).toContain('WO-1');
    const elements = card.body.elements as Array<Record<string, any>>;
    const text = JSON.stringify(elements);
    expect(text).toContain('CNC-03 卧式加工中心#3');
    expect(text).toContain('王师傅');
    expect(text).toContain('已接单');

    const buttons = elements.filter((e) => e.tag === 'button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0].behaviors[0]).toEqual({
      type: 'callback',
      value: { action: 'mes_work_order', wo_id: 'WO-1', op: 'done' },
    });
  });

  it('drops the button once the order is closed', () => {
    const card = buildMesWorkOrderCard({ wo: wo({ status: 'closed' }) }) as Record<string, any>;
    const buttons = (card.body.elements as Array<Record<string, any>>).filter(
      (e) => e.tag === 'button',
    );
    expect(buttons).toHaveLength(0);
  });

  it('neutralizes accidental @-mentions and newlines from the database', () => {
    const card = buildMesWorkOrderCard({
      wo: wo({ fault_desc: '<at id=all></at>\n第二行', plan: '现场处理' }),
    }) as Record<string, any>;
    const text = JSON.stringify(card.body.elements);
    expect(text).not.toContain('<at');
    expect(text).toContain('&lt;at');
  });
});

describe('dispatchMesWorkOrderAction', () => {
  it('assigns with the clicking operator and returns the refreshed card', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const reply = await dispatchMesWorkOrderAction(
      { action: 'mes_work_order', wo_id: 'WO-1', op: 'assign' },
      '王师傅',
      {
        baseUrl: 'http://mes.test',
        fetchImpl: fakeFetch({
          calls,
          action: () => jsonResponse(wo({ status: 'assigned', assignee: '王师傅' })),
        }),
      },
    );

    const post = calls.find((c) => c.init?.method === 'POST');
    expect(post?.url).toBe('http://mes.test/api/work_orders/WO-1/assign');
    expect(JSON.parse(String(post?.init?.body))).toEqual({ operator: '王师傅' });
    expect(reply.toast).toEqual({ type: 'success', content: '已接单，工单进入处置中。' });
    expect(JSON.stringify(reply.card)).toContain('已接单');
  });

  it('fills a resolution when marking done so the state machine accepts it', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    await dispatchMesWorkOrderAction(
      { action: 'mes_work_order', wo_id: 'WO-1', op: 'done' },
      '李工',
      {
        baseUrl: 'http://mes.test',
        fetchImpl: fakeFetch({
          calls,
          action: () => jsonResponse(wo({ status: 'done', plan: '已更换轴承' })),
        }),
        now: () => new Date('2026-11-07T10:20:00Z'),
      },
    );

    const body = JSON.parse(String(calls.find((c) => c.init?.method === 'POST')?.init?.body));
    expect(body.operator).toBe('李工');
    expect(body.resolution).toContain('李工');
    expect(body.resolution).toContain('现场处置完成');
  });

  it('turns a state-machine 409 into a readable toast and still refreshes the card', async () => {
    const reply = await dispatchMesWorkOrderAction(
      { action: 'mes_work_order', wo_id: 'WO-1', op: 'assign' },
      '王师傅',
      {
        baseUrl: 'http://mes.test',
        fetchImpl: fakeFetch({
          action: () => jsonResponse({ error: '非法流转: assigned → assigned' }, 409),
          wo: () => jsonResponse(wo({ status: 'assigned', assignee: '李工' })),
        }),
      },
    );

    expect(reply.toast.type).toBe('error');
    expect(reply.toast.content).toContain('非法流转');
    // 卡片要刷新成别人改过的真实状态，而不是停在点击前那一版
    expect(JSON.stringify(reply.card)).toContain('李工');
  });

  it('reports a missing work order without inventing a card', async () => {
    const reply = await dispatchMesWorkOrderAction(
      { action: 'mes_work_order', wo_id: 'WO-gone', op: 'done' },
      '王师傅',
      {
        baseUrl: 'http://mes.test',
        fetchImpl: fakeFetch({
          action: () => jsonResponse({ error: '工单不存在: WO-gone' }, 404),
          wo: () => jsonResponse({ error: '工单不存在: WO-gone' }, 404) as never,
        }),
      },
    );

    expect(reply.toast).toEqual({
      type: 'error',
      content: '工单不存在或已被清理，请刷新看板。',
    });
    expect(reply.card).toBeUndefined();
  });

  it('survives an unreachable MES API (feishu still gets a valid response)', async () => {
    const reply = await dispatchMesWorkOrderAction(
      { action: 'mes_work_order', wo_id: 'WO-1', op: 'close' },
      '王师傅',
      {
        baseUrl: 'http://mes.test',
        fetchImpl: (async () => {
          throw new Error('connect ECONNREFUSED');
        }) as unknown as typeof fetch,
      },
    );

    expect(reply.toast.type).toBe('error');
    expect(reply.toast.content).toContain('没有响应');
    expect(reply.card).toBeUndefined();
  });

  it('falls back to a placeholder operator when the name lookup failed', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    await dispatchMesWorkOrderAction(
      { action: 'mes_work_order', wo_id: 'WO-1', op: 'assign' },
      '',
      {
        baseUrl: 'http://mes.test',
        fetchImpl: fakeFetch({ calls }),
      },
    );
    const body = JSON.parse(String(calls.find((c) => c.init?.method === 'POST')?.init?.body));
    expect(body.operator).toBe('飞书用户');
  });
});
