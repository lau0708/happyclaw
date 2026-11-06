import { useEffect, useState } from 'react';
import { Radar } from 'lucide-react';
import { mesApi, type DeviceStatus, type WorkOrder } from '../api/mes';

const overLabel: Record<string, string> = {
  vibration: '振动',
  temp: '温度',
  pressure: '压力',
};

export function SmartInspectionPage() {
  const [devices, setDevices] = useState<DeviceStatus[]>([]);
  const [openOrders, setOpenOrders] = useState<WorkOrder[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([mesApi.deviceStatus(), mesApi.workOrders()])
      .then(([ds, wo]) => {
        setDevices(ds);
        setOpenOrders(wo.filter((o) => o.status !== 'closed'));
      })
      .catch((e) => setError(String(e)));
  }, []);

  const abnormal = devices.filter((d) => d.over_threshold.length > 0);

  return (
    <div className="min-h-full px-4 py-5 sm:px-6 lg:px-8 lg:py-8">
      <div className="mx-auto max-w-6xl">
        <header className="mb-6">
          <h1 className="text-2xl font-bold text-foreground">智能巡检</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
            定时巡检 Agent 周期性扫描设备指标，超阈值自动触发「诊断 → 建单 → 推送」，并做幂等去重。
          </p>
        </header>

        {error && (
          <div className="mb-6 rounded-xl border border-border bg-warning-bg px-4 py-3 text-sm text-warning">
            后端未连接：请先运行 <code>python3 api.py</code>。
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
            <div className="mb-3 flex items-center gap-2">
              <Radar className="size-4 text-primary" />
              <h2 className="text-sm font-semibold text-foreground">
                异常设备（超阈值）
              </h2>
            </div>
            {abnormal.length === 0 ? (
              <p className="text-sm text-muted-foreground">当前无异常设备。</p>
            ) : (
              <ul className="space-y-2">
                {abnormal.map((d) => (
                  <li
                    key={d.equipment_id}
                    className="flex items-center justify-between rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm"
                  >
                    <span className="font-medium text-foreground">
                      {d.name} ({d.equipment_id})
                    </span>
                    <span className="text-red-600">
                      {d.over_threshold.map((o) => overLabel[o] || o).join(' / ')} 超限
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
            <h2 className="mb-3 text-sm font-semibold text-foreground">
              未闭环工单（{openOrders.length}）
            </h2>
            {openOrders.length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无待处理工单。</p>
            ) : (
              <ul className="space-y-2">
                {openOrders.map((o) => (
                  <li
                    key={o.wo_id}
                    className="rounded-lg border border-border px-3 py-2 text-sm"
                  >
                    <div className="font-mono text-xs text-muted-foreground">
                      {o.wo_id} · {o.equipment_id}
                    </div>
                    <div className="text-foreground">{o.fault_desc}</div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="mt-4 rounded-xl border border-border bg-card p-5 text-sm leading-6 text-muted-foreground shadow-sm">
          <p className="font-medium text-foreground">运行方式</p>
          <p className="mt-1">
            在 mes-mcp-server 目录执行{' '}
            <code className="rounded bg-muted px-1.5 py-0.5">
              python3 scheduler.py --once
            </code>{' '}
            触发单次巡检；或{' '}
            <code className="rounded bg-muted px-1.5 py-0.5">
              python3 scheduler.py --interval 30
            </code>{' '}
            每 30 秒巡检。异常设备会自动生成工单并推送到工单中心。
          </p>
        </div>
      </div>
    </div>
  );
}
