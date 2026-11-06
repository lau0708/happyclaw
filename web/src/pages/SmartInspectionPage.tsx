/**
 * SmartInspectionPage — 智能巡检（深色工业大屏风格）
 * 逐设备手动巡检（走完整闭环）+ 阈值看板 + 闭环流程说明
 */
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { mesApi, type DeviceStatus } from '../api/mes';
import {
  MesShell,
  MesPanel,
  KpiCard,
  StatusBadge,
  MES_THRESHOLDS,
  MES_COLORS,
  mesBtnPrimaryClass,
  mesBtnPrimaryStyle,
} from '../components/mes/MesUi';

const FLOW_STEPS = [
  '定时巡检取数',
  '阈值判异',
  '幂等拦截',
  'LLM 诊断（可降级）',
  '创建工单',
  '飞书报警推送',
  '人工处置回填',
  '复盘沉淀知识库',
];

export function SmartInspectionPage() {
  const [devices, setDevices] = useState<DeviceStatus[]>([]);
  const [error, setError] = useState('');
  const [inspecting, setInspecting] = useState('');

  const load = useCallback(async () => {
    try {
      setDevices(await mesApi.deviceStatus());
      setError('');
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

  const runInspect = async (eqId: string) => {
    setInspecting(eqId);
    try {
      const r = await mesApi.inspect(eqId);
      if (r.over_threshold.length === 0) toast.success(`${eqId} 巡检完成：各指标正常`);
      else if (r.new_work_order) toast.error(`${eqId} 超限（${r.over_threshold.join('/')}），已诊断并创建新工单`);
      else toast.warning(`${eqId} 超限，但已有未闭环工单，幂等跳过建单`);
      await load();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setInspecting('');
    }
  };

  return (
    <MesShell
      title="智能巡检"
      subtitle="手动触发走完整闭环：判异 → 幂等 → LLM 诊断 → 建单 → 飞书推送 · 定时巡检用 scheduler.py"
    >
      {error && (
        <div className="mb-5 rounded-lg px-4 py-3 text-sm text-amber-300" style={{ background: '#33260A', border: '1px solid #7a5b16' }}>
          MES 后端未连接：请先运行 <code>python3 mes-mcp-server/api.py</code>
        </div>
      )}

      <div className="mb-4 grid grid-cols-2 gap-3.5 xl:grid-cols-4">
        <KpiCard label="巡检对象" value={devices.length} sub={<span style={{ color: MES_COLORS.muted }}>全部在线设备</span>} />
        <KpiCard
          label="本轮异常"
          value={alertDevices.length}
          tone={alertDevices.length ? 'bad' : 'ok'}
          sub={
            alertDevices.length ? (
              <span className="text-red-400">
                {alertDevices.map((d) => `${d.equipment_id} ${d.over_threshold.join('/')}`).join('；')}
              </span>
            ) : (
              <span className="text-emerald-400">无超限项</span>
            )
          }
        />
        <KpiCard label="诊断引擎" value="LLM" tone="accent" sub={<span className="text-emerald-400">失败自动降级知识库</span>} />
        <KpiCard label="定时巡检" value={60} suffix="s" sub={<span style={{ color: MES_COLORS.muted }}>scheduler.py --interval 60</span>} />
      </div>

      <MesPanel title="逐设备巡检" right="超限时经幂等校验后自动建单并推送飞书">
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[11px]" style={{ color: MES_COLORS.faint }}>
                {['设备', '最新指标', '超限项', '开放工单', '操作'].map((h) => (
                  <th key={h} className="whitespace-nowrap px-2.5 py-2 font-normal">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {devices.map((d) => {
                const m = d.latest_metric;
                const alert = d.over_threshold.length > 0;
                return (
                  <tr key={d.equipment_id} style={{ borderTop: `1px solid ${MES_COLORS.border}` }}>
                    <td className="whitespace-nowrap px-2.5 py-2.5">
                      <b className="text-[#4DA3FF]">{d.equipment_id}</b>
                      <span className="ml-1.5" style={{ color: MES_COLORS.muted }}>{d.name}</span>
                    </td>
                    <td className="px-2.5 py-2.5 text-xs">
                      振 {m?.vibration ?? '--'} · 温 {m?.temp ?? '--'} · 压 {m?.pressure ?? '--'}
                      <span className="ml-1.5" style={{ color: MES_COLORS.faint }}>({m?.ts.slice(11, 16) ?? '--'})</span>
                    </td>
                    <td className="px-2.5 py-2.5">
                      {alert ? <StatusBadge status="alarm" /> : <span className="text-emerald-400">无</span>}
                      {alert && <span className="ml-1.5 text-xs text-red-400">{d.over_threshold.join(' / ')}</span>}
                    </td>
                    <td className={'px-2.5 py-2.5 ' + (d.open_work_orders ? 'text-amber-400' : '')} style={!d.open_work_orders ? { color: MES_COLORS.faint } : {}}>
                      {d.open_work_orders || 0}
                    </td>
                    <td className="px-2.5 py-2.5">
                      <button
                        className={mesBtnPrimaryClass}
                        style={mesBtnPrimaryStyle}
                        onClick={() => runInspect(d.equipment_id)}
                        disabled={inspecting === d.equipment_id}
                      >
                        {inspecting === d.equipment_id ? '巡检中…' : '手动巡检'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </MesPanel>

      <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-2">
        <MesPanel title="判异阈值（严格大于才触发）">
          <div className="grid grid-cols-3 gap-2.5">
            {Object.entries(MES_THRESHOLDS).map(([k, t]) => (
              <div key={k} className="rounded-lg px-3 py-3" style={{ background: MES_COLORS.card, border: `1px solid ${MES_COLORS.borderSoft}` }}>
                <div className="text-xs" style={{ color: MES_COLORS.muted }}>{t.label}</div>
                <div className="mt-0.5 text-xl font-semibold tabular-nums text-[#4DA3FF]">{t.max}</div>
              </div>
            ))}
          </div>
        </MesPanel>
        <MesPanel title="闭环流程">
          <div className="flex flex-wrap gap-2 text-xs">
            {FLOW_STEPS.map((s, i) => (
              <span key={s} className="rounded-md px-3 py-2" style={{ background: MES_COLORS.card, border: `1px solid ${MES_COLORS.borderSoft}` }}>
                <span className="mr-1.5 text-[#4DA3FF]">{i + 1}</span>
                {s}
              </span>
            ))}
          </div>
        </MesPanel>
      </div>
    </MesShell>
  );
}
