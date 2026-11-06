/**
 * DeviceMonitorPage — 设备监控（深色工业大屏风格）
 * KPI 总览 + 设备实时指标卡 + 24h 振动趋势 + 设备详情弹窗（台账/三指标趋势/历史故障）
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
} from 'recharts';
import { toast } from 'sonner';
import { mesApi, type DeviceStatus, type Metric, type Equipment, type HistoryFault } from '../api/mes';
import {
  MesShell,
  MesPanel,
  MesModal,
  KpiCard,
  MetricBar,
  Sparkline,
  StatusBadge,
  MES_THRESHOLDS,
  MES_COLORS,
  mesBtnClass,
  mesBtnStyle,
} from '../components/mes/MesUi';

const VIB_THRESHOLD = MES_THRESHOLDS.vibration.max;

export function DeviceMonitorPage() {
  const [devices, setDevices] = useState<DeviceStatus[]>([]);
  const [metrics, setMetrics] = useState<Record<string, Metric[]>>({});
  const [error, setError] = useState('');
  const [detailEq, setDetailEq] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const ds = await mesApi.deviceStatus();
      setDevices(ds);
      setError('');
      const entries = await Promise.all(
        ds.map(async (d) => [
          d.equipment_id,
          await mesApi.metric(d.equipment_id, 24).catch(() => [] as Metric[]),
        ] as const),
      );
      setMetrics(Object.fromEntries(entries));
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const alertDevices = devices.filter((d) => d.over_threshold.length > 0);
  const openOrders = devices.reduce((n, d) => n + d.open_work_orders, 0);
  const trendDevice = alertDevices[0] || devices[0];
  const trendData = useMemo(
    () =>
      (metrics[trendDevice?.equipment_id ?? ''] || []).map((m) => ({
        ts: m.ts.slice(11, 16),
        vibration: m.vibration,
      })),
    [metrics, trendDevice],
  );

  return (
    <MesShell
      title="车间实时监控 · 设备总览"
      subtitle="实时指标 / 超限告警 / 工单联动 · 数据 30s 自动刷新 · 点击设备查看详情"
    >
      {error && (
        <div className="mb-5 rounded-lg px-4 py-3 text-sm text-[var(--mes-warn-text)]" style={{ background: 'var(--mes-warn-bg)', border: '1px solid var(--mes-warn-border)' }}>
          MES 后端未连接：请先运行 <code>python3 mes-mcp-server/api.py</code>
        </div>
      )}

      <div className="mb-4 grid grid-cols-2 gap-3.5 xl:grid-cols-4">
        <KpiCard label="在线设备" value={devices.length} sub={<span className="text-emerald-400">全部联网</span>} />
        <KpiCard
          label="告警设备"
          value={alertDevices.length}
          tone={alertDevices.length ? 'bad' : 'ok'}
          sub={
            alertDevices.length ? (
              <span className="text-red-400">{alertDevices.map((d) => d.equipment_id).join(' ')} 超限</span>
            ) : (
              <span className="text-emerald-400">各指标均在阈值内</span>
            )
          }
        />
        <KpiCard
          label="未闭环工单"
          value={openOrders}
          tone={openOrders ? 'warn' : 'ok'}
          sub={<span className="text-amber-400/90">{openOrders ? '可在工单中心处置' : '全部闭环'}</span>}
        />
        <KpiCard label="监控指标" value={devices.length * 3} sub={<span style={{ color: MES_COLORS.muted }}>振动 / 温度 / 压力</span>} />
      </div>

      <div className="mb-3.5 grid grid-cols-1 gap-3.5 lg:grid-cols-3">
        <MesPanel className="lg:col-span-2" title="设备实时状态" right={`阈值 ${Object.values(MES_THRESHOLDS).map((t) => t.max).join(' / ')}`}>
          {devices.map((d) => {
            const m = d.latest_metric;
            const alert = d.over_threshold.length > 0;
            const vib24 = (metrics[d.equipment_id] || []).slice(-24).map((x) => x.vibration ?? 0);
            return (
              <div
                key={d.equipment_id}
                className="mb-1.5 flex items-center gap-3.5 rounded-lg px-2.5 py-3"
                style={{
                  background: alert ? 'var(--mes-alarm-bg)' : MES_COLORS.card,
                  border: `1px solid ${alert ? 'var(--mes-alarm-border)' : MES_COLORS.borderSoft}`,
                }}
              >
                <button className="w-[140px] shrink-0 text-left" onClick={() => setDetailEq(d.equipment_id)}>
                  <b className="block text-sm hover:text-[var(--mes-accent)]">{d.equipment_id}</b>
                  <span className="text-xs" style={{ color: MES_COLORS.muted }}>{d.name}</span>
                </button>
                <MetricBar label="振动" value={m?.vibration} max={MES_THRESHOLDS.vibration.max} />
                <MetricBar label="温度" value={m?.temp} max={MES_THRESHOLDS.temp.max} />
                <MetricBar label="压力" value={m?.pressure} max={MES_THRESHOLDS.pressure.max} />
                <div className="hidden xl:block">
                  <Sparkline values={vib24} color={alert ? MES_COLORS.bad : MES_COLORS.ok} />
                </div>
                <StatusBadge status={alert ? 'alarm' : 'running'} />
              </div>
            );
          })}
          {devices.length === 0 && !error && (
            <div className="py-8 text-center text-sm" style={{ color: MES_COLORS.faint }}>加载中…</div>
          )}
        </MesPanel>

        <MesPanel title="最新工单">
          <LatestOrders />
        </MesPanel>
      </div>

      <MesPanel
        title={`24h 振动趋势 · ${trendDevice?.equipment_id ?? '--'}`}
        right={`红色虚线 = 阈值 ${VIB_THRESHOLD}`}
      >
        <div className="h-56">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={trendData}>
              <defs>
                <linearGradient id="vibDark" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={MES_COLORS.bad} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={MES_COLORS.bad} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke={MES_COLORS.border} />
              <XAxis dataKey="ts" tick={{ fontSize: 11, fill: MES_COLORS.faint }} stroke={MES_COLORS.border} />
              <YAxis tick={{ fontSize: 11, fill: MES_COLORS.faint }} stroke={MES_COLORS.border} domain={[0, 'auto']} />
              <Tooltip contentStyle={{ background: MES_COLORS.card, border: `1px solid ${MES_COLORS.border}`, borderRadius: 8, fontSize: 12, color: MES_COLORS.text }} />
              <ReferenceLine y={VIB_THRESHOLD} stroke={MES_COLORS.bad} strokeDasharray="5 4" strokeOpacity={0.6} />
              <Area type="monotone" dataKey="vibration" name="振动" stroke={MES_COLORS.bad} strokeWidth={2} fill="url(#vibDark)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </MesPanel>

      {detailEq && <DeviceDetail eqId={detailEq} onClose={() => setDetailEq(null)} onToast={toast} />}
    </MesShell>
  );
}

function LatestOrders() {
  const [orders, setOrders] = useState<Awaited<ReturnType<typeof mesApi.workOrders>>>([]);
  useEffect(() => {
    mesApi.workOrders().then(setOrders).catch(() => {});
  }, []);
  if (orders.length === 0)
    return <div className="py-8 text-center text-sm" style={{ color: MES_COLORS.faint }}>暂无工单</div>;
  return (
    <div>
      {orders.slice(0, 4).map((o) => (
        <div key={o.wo_id} className="mb-2 px-1 py-2" style={{ borderBottom: `1px solid ${MES_COLORS.border}` }}>
          <div className="mb-1.5 flex items-center gap-2">
            <span className="font-mono text-xs text-[var(--mes-accent)]">{o.wo_id}</span>
            <StatusBadge status={o.status} />
          </div>
          <div className="text-xs" style={{ color: MES_COLORS.muted }}>
            {o.equipment_id} · {o.fault_desc.slice(0, 36)}…
          </div>
        </div>
      ))}
    </div>
  );
}

function DeviceDetail({
  eqId,
  onClose,
  onToast,
}: {
  eqId: string;
  onClose: () => void;
  onToast: typeof toast;
}) {
  const [eq, setEq] = useState<Equipment | null>(null);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [faults, setFaults] = useState<HistoryFault[]>([]);
  const [inspectRunning, setInspectRunning] = useState(false);

  useEffect(() => {
    mesApi.equipment(eqId).then((r) => setEq(r[0] ?? null)).catch(() => {});
    mesApi.metric(eqId, 24).then(setMetrics).catch(() => {});
    mesApi.historyFaults(eqId).then(setFaults).catch(() => {});
  }, [eqId]);

  const runInspect = async () => {
    setInspectRunning(true);
    try {
      const r = await mesApi.inspect(eqId);
      if (r.over_threshold.length === 0) onToast.success(`${eqId} 巡检完成：各指标正常`);
      else if (r.new_work_order) onToast.error(`${eqId} 超限（${r.over_threshold.join('/')}），已诊断并创建新工单`);
      else onToast.warning(`${eqId} 超限，但已有未闭环工单，幂等跳过建单`);
    } catch (e) {
      onToast.error(String(e));
    } finally {
      setInspectRunning(false);
    }
  };

  const trendData = metrics.map((m) => ({ ts: m.ts.slice(11, 16), vibration: m.vibration, temp: m.temp, pressure: m.pressure }));
  const colors: Record<string, string> = { vibration: MES_COLORS.bad, temp: MES_COLORS.warn, pressure: MES_COLORS.accent };

  return (
    <MesModal open title={`设备详情 · ${eqId}`} onClose={onClose}>
      <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-4">
        {[
          ['类型 / 产线', eq ? `${eq.type} · ${eq.line}` : '--'],
          ['厂商 / 型号', eq ? `${eq.mfr ?? ''} ${eq.model ?? ''}` : '--'],
          ['安装日期', eq?.install_date ?? '--'],
          ['状态', eq?.status ?? '--'],
        ].map(([k, v]) => (
          <div key={k} className="rounded-lg px-3 py-2.5" style={{ background: MES_COLORS.card, border: `1px solid ${MES_COLORS.borderSoft}` }}>
            <div className="text-[11px]" style={{ color: MES_COLORS.faint }}>{k}</div>
            <div className="mt-0.5 text-[13px]">{v}</div>
          </div>
        ))}
      </div>

      <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-3">
        {Object.entries(MES_THRESHOLDS).map(([key, t]) => (
          <div key={key} className="rounded-lg p-2.5" style={{ background: MES_COLORS.card, border: `1px solid ${MES_COLORS.borderSoft}` }}>
            <div className="mb-1.5 flex justify-between text-xs" style={{ color: MES_COLORS.muted }}>
              <span>{t.label}</span>
              <span>阈值 {t.max}</span>
            </div>
            <div className="h-28">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={trendData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={MES_COLORS.border} />
                  <XAxis dataKey="ts" tick={{ fontSize: 10, fill: MES_COLORS.faint }} stroke={MES_COLORS.border} />
                  <YAxis tick={{ fontSize: 10, fill: MES_COLORS.faint }} stroke={MES_COLORS.border} domain={[0, 'auto']} width={32} />
                  <ReferenceLine y={t.max} stroke={MES_COLORS.bad} strokeDasharray="4 3" strokeOpacity={0.5} />
                  <Area type="monotone" dataKey={key} stroke={colors[key]} strokeWidth={1.5} fill="transparent" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        ))}
      </div>

      <div className="mb-2 flex items-center justify-between">
        <b className="text-[13px]" style={{ color: 'var(--mes-btn-text)' }}>历史故障（{faults.length}）</b>
        <button className={mesBtnClass} style={mesBtnStyle} onClick={runInspect} disabled={inspectRunning}>
          {inspectRunning ? '巡检中…' : '手动巡检（完整闭环）'}
        </button>
      </div>
      {faults.length > 0 ? (
        <div className="max-h-52 overflow-y-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[11px]" style={{ color: MES_COLORS.faint }}>
                <th className="py-2 pr-3 font-normal">故障号</th>
                <th className="py-2 pr-3 font-normal">类型</th>
                <th className="py-2 pr-3 font-normal">描述</th>
                <th className="py-2 pr-3 font-normal">停机</th>
                <th className="py-2 pr-3 font-normal">处置</th>
                <th className="py-2 font-normal">时间</th>
              </tr>
            </thead>
            <tbody>
              {faults.map((f) => (
                <tr key={f.fault_id} style={{ borderTop: `1px solid ${MES_COLORS.border}` }}>
                  <td className="py-2 pr-3 font-mono text-xs text-[var(--mes-accent)]">{f.fault_id}</td>
                  <td className="py-2 pr-3">{f.fault_code}</td>
                  <td className="max-w-[200px] py-2 pr-3">{f.fault_desc}</td>
                  <td className="py-2 pr-3">{f.down_minutes} min</td>
                  <td className="max-w-[180px] py-2 pr-3" style={{ color: MES_COLORS.muted }}>{f.resolution}</td>
                  <td className="py-2 text-xs" style={{ color: MES_COLORS.muted }}>{f.occurred_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="py-6 text-center text-sm" style={{ color: MES_COLORS.faint }}>无历史故障记录</div>
      )}
    </MesModal>
  );
}
