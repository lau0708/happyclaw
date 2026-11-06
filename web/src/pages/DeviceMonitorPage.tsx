import { useEffect, useMemo, useState } from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts';
import { mesApi, type DeviceStatus, type Metric } from '../api/mes';

const overLabel: Record<string, string> = {
  vibration: '振动',
  temp: '温度',
  pressure: '压力',
};

export function DeviceMonitorPage() {
  const [devices, setDevices] = useState<DeviceStatus[]>([]);
  const [trend, setTrend] = useState<Metric[]>([]);
  const [error, setError] = useState('');
  const [trendDevice, setTrendDevice] = useState('CNC-03');

  useEffect(() => {
    mesApi
      .deviceStatus()
      .then(setDevices)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    mesApi
      .metric(trendDevice, 24)
      .then(setTrend)
      .catch(() => setTrend([]));
  }, [trendDevice]);

  const trendData = useMemo(
    () => trend.map((m) => ({ ts: m.ts.slice(5, 16), vibration: m.vibration })),
    [trend],
  );

  return (
    <div className="min-h-full px-4 py-5 sm:px-6 lg:px-8 lg:py-8">
      <div className="mx-auto max-w-7xl">
        <header className="mb-6">
          <h1 className="text-2xl font-bold text-foreground">设备监控</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
            实时设备运行状态与超限告警，数据来自模拟 MES（mcp-server-mes）。
          </p>
        </header>

        {error && (
          <div className="mb-6 rounded-xl border border-border bg-warning-bg px-4 py-3 text-sm text-warning">
            后端未连接：请先运行 <code>python3 api.py</code>（mes-mcp-server）。
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {devices.map((d) => (
            <div
              key={d.equipment_id}
              className={`rounded-xl border bg-card p-4 shadow-sm ${
                d.over_threshold.length > 0
                  ? 'border-red-300'
                  : 'border-border'
              }`}
            >
              <div className="mb-2 flex items-start justify-between">
                <div>
                  <div className="text-sm font-semibold text-foreground">
                    {d.name}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {d.equipment_id}
                  </div>
                </div>
                <button
                  onClick={() => setTrendDevice(d.equipment_id)}
                  className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
                >
                  趋势
                </button>
              </div>
              <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span>振动 {d.latest_metric?.vibration ?? '-'} mm/s</span>
                <span>温度 {d.latest_metric?.temp ?? '-'}°C</span>
                <span>压力 {d.latest_metric?.pressure ?? '-'} MPa</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {d.over_threshold.length > 0 ? (
                  <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
                    ⚠ {d.over_threshold.map((o) => overLabel[o] || o).join(' / ')} 超限
                  </span>
                ) : (
                  <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                    正常
                  </span>
                )}
                {d.open_work_orders > 0 && (
                  <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">
                    未闭环工单 ×{d.open_work_orders}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-6 rounded-xl border border-border bg-card p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-foreground">
              指标趋势 · {trendDevice}（振动 mm/s，最近 24h）
            </h2>
            <span className="text-xs text-muted-foreground">阈值 8.0</span>
          </div>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trendData}>
                <defs>
                  <linearGradient id="vib" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#ef4444" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="ts" tick={{ fontSize: 11 }} stroke="#94a3b8" />
                <YAxis tick={{ fontSize: 11 }} stroke="#94a3b8" />
                <Tooltip
                  contentStyle={{ fontSize: 12, borderRadius: 8 }}
                />
                <Area
                  type="monotone"
                  dataKey="vibration"
                  stroke="#ef4444"
                  strokeWidth={2}
                  fill="url(#vib)"
                  name="振动"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
}
