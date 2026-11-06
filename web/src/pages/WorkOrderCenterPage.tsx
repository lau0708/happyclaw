import { useEffect, useState } from 'react';
import { mesApi, type WorkOrder } from '../api/mes';

const statusLabel: Record<string, string> = {
  open: '待处理',
  assigned: '已分派',
  in_progress: '处理中',
  done: '已完成',
  closed: '已闭环',
};

const sevStyle: Record<string, string> = {
  low: 'bg-gray-100 text-gray-700',
  medium: 'bg-blue-100 text-blue-700',
  high: 'bg-amber-100 text-amber-700',
  critical: 'bg-red-100 text-red-700',
};

export function WorkOrderCenterPage() {
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);
  const [closing, setClosing] = useState('');
  const [error, setError] = useState('');

  const load = async (s = status) => {
    setLoading(true);
    try {
      setOrders(await mesApi.workOrders(s || undefined));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const closeOrder = async (woId: string) => {
    setClosing(woId);
    try {
      await mesApi.closeWorkOrder(woId);
      await load();
    } finally {
      setClosing('');
    }
  };

  return (
    <div className="min-h-full px-4 py-5 sm:px-6 lg:px-8 lg:py-8">
      <div className="mx-auto max-w-7xl">
        <header className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="text-2xl font-bold text-foreground">工单中心</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
              设备异常工单全生命周期：由巡检/报警自动生成，人工处置后闭环并沉淀经验。
            </p>
          </div>
        </header>

        {error && (
          <div className="mb-6 rounded-xl border border-border bg-warning-bg px-4 py-3 text-sm text-warning">
            后端未连接：请先运行 <code>python3 api.py</code>。
          </div>
        )}

        <div className="mb-4 flex items-center gap-2">
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              load(e.target.value);
            }}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-sm"
          >
            <option value="">全部状态</option>
            {Object.entries(statusLabel).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <button
            onClick={() => load()}
            disabled={loading}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-sm hover:bg-muted"
          >
            {loading ? '加载中…' : '刷新'}
          </button>
        </div>

        <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="px-4 py-2.5 font-medium">工单号</th>
                <th className="px-4 py-2.5 font-medium">设备</th>
                <th className="px-4 py-2.5 font-medium">故障描述</th>
                <th className="px-4 py-2.5 font-medium">级别</th>
                <th className="px-4 py-2.5 font-medium">状态</th>
                <th className="px-4 py-2.5 font-medium">创建时间</th>
                <th className="px-4 py-2.5 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.wo_id} className="border-b border-border last:border-0">
                  <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                    {o.wo_id}
                  </td>
                  <td className="px-4 py-3">{o.equipment_id}</td>
                  <td className="max-w-xs px-4 py-3 text-muted-foreground">
                    {o.fault_desc}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs ${sevStyle[o.severity] || 'bg-gray-100 text-gray-700'}`}
                    >
                      {o.severity}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
                      {statusLabel[o.status] || o.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                    {o.created_at}
                  </td>
                  <td className="px-4 py-3">
                    {o.status !== 'closed' ? (
                      <button
                        onClick={() => closeOrder(o.wo_id)}
                        disabled={closing === o.wo_id}
                        className="rounded-md border border-border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
                      >
                        闭环
                      </button>
                    ) : (
                      <span className="text-green-600">✔</span>
                    )}
                  </td>
                </tr>
              ))}
              {orders.length === 0 && !loading && (
                <tr>
                  <td
                    colSpan={7}
                    className="px-4 py-8 text-center text-sm text-muted-foreground"
                  >
                    暂无工单。可运行 <code>python3 scheduler.py --once</code> 触发一条巡检告警。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
