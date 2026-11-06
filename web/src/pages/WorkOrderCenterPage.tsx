/**
 * WorkOrderCenterPage — 工单中心（深色工业大屏风格）
 * 状态/设备筛选 + 完整生命周期操作（派单→回填→闭环）+ 流转留痕弹窗
 */
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import {
  mesApi,
  type WorkOrder,
  type WorkOrderLog,
  type DeviceStatus,
} from '../api/mes';
import {
  MesShell,
  MesPanel,
  MesModal,
  StatusBadge,
  SeverityTag,
  MES_COLORS,
  mesInputClass,
  mesInputStyle,
  mesBtnClass,
  mesBtnStyle,
  mesBtnPrimaryClass,
  mesBtnPrimaryStyle,
} from '../components/mes/MesUi';

const STATUS_OPTIONS = ['open', 'assigned', 'done', 'closed'] as const;

export function WorkOrderCenterPage() {
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [devices, setDevices] = useState<DeviceStatus[]>([]);
  const [status, setStatus] = useState('');
  const [equipmentId, setEquipmentId] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [assignWo, setAssignWo] = useState<string | null>(null);
  const [doneWo, setDoneWo] = useState<string | null>(null);
  const [logsWo, setLogsWo] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setOrders(await mesApi.workOrders(status || undefined, equipmentId || undefined));
      setError('');
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [status, equipmentId]);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    mesApi.deviceStatus().then(setDevices).catch(() => {});
  }, []);

  const closeOrder = async (woId: string) => {
    try {
      await mesApi.closeWorkOrder(woId);
      toast.success(`${woId} 已沿状态机闭环归档`);
      load();
    } catch (e) {
      toast.error(String(e));
    }
  };

  const eqOptions = devices.length
    ? devices.map((d) => d.equipment_id)
    : [...new Set(orders.map((o) => o.equipment_id))];

  return (
    <MesShell title="工单中心" subtitle="open → assigned → done → closed · 所有流转经状态机校验并留痕">
      {error && (
        <div className="mb-5 rounded-lg px-4 py-3 text-sm text-amber-300" style={{ background: '#33260A', border: '1px solid #7a5b16' }}>
          MES 后端未连接：请先运行 <code>python3 mes-mcp-server/api.py</code>
        </div>
      )}

      <MesPanel>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border px-3 py-1.5 text-xs outline-none"
            style={{ background: MES_COLORS.card, borderColor: '#2A3C61', color: '#B7C6DE' }}
          >
            <option value="">全部状态</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <select
            value={equipmentId}
            onChange={(e) => setEquipmentId(e.target.value)}
            className="rounded-md border px-3 py-1.5 text-xs outline-none"
            style={{ background: MES_COLORS.card, borderColor: '#2A3C61', color: '#B7C6DE' }}
          >
            <option value="">全部设备</option>
            {eqOptions.map((e) => (
              <option key={e} value={e}>{e}</option>
            ))}
          </select>
          <button className={mesBtnClass} style={mesBtnStyle} onClick={load} disabled={loading}>
            {loading ? '加载中…' : '刷新'}
          </button>
          <span className="ml-auto text-[11px]" style={{ color: MES_COLORS.faint }}>共 {orders.length} 条</span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[11px]" style={{ color: MES_COLORS.faint }}>
                {['工单号', '设备', '故障描述 / 处置方案', '级别', '状态', '负责人', '创建 / 更新', '操作'].map((h) => (
                  <th key={h} className="whitespace-nowrap px-2.5 py-2 font-normal">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.wo_id} style={{ borderTop: `1px solid ${MES_COLORS.border}` }}>
                  <td className="px-2.5 py-2.5 font-mono text-xs text-[#4DA3FF]">{o.wo_id}</td>
                  <td className="px-2.5 py-2.5">{o.equipment_id}</td>
                  <td className="max-w-[340px] px-2.5 py-2.5">
                    <div>{o.fault_desc}</div>
                    {o.plan && (
                      <div className="mt-1 text-xs" style={{ color: MES_COLORS.muted }}>处置：{o.plan}</div>
                    )}
                  </td>
                  <td className="px-2.5 py-2.5"><SeverityTag severity={o.severity} /></td>
                  <td className="px-2.5 py-2.5"><StatusBadge status={o.status} /></td>
                  <td className="px-2.5 py-2.5">
                    {o.assignee ?? <span style={{ color: MES_COLORS.faint }}>未派单</span>}
                  </td>
                  <td className="px-2.5 py-2.5 text-xs" style={{ color: MES_COLORS.muted }}>
                    {o.created_at}
                    <br />
                    {o.updated_at !== o.created_at ? o.updated_at : ''}
                  </td>
                  <td className="whitespace-nowrap px-2.5 py-2.5">
                    <button className={mesBtnClass + ' mr-1.5'} style={mesBtnStyle} onClick={() => setLogsWo(o.wo_id)}>
                      日志
                    </button>
                    {o.status === 'open' && (
                      <button className={mesBtnClass + ' mr-1.5'} style={mesBtnStyle} onClick={() => setAssignWo(o.wo_id)}>
                        派单
                      </button>
                    )}
                    {o.status === 'assigned' && (
                      <button className={mesBtnClass + ' mr-1.5'} style={mesBtnStyle} onClick={() => setDoneWo(o.wo_id)}>
                        回填处置
                      </button>
                    )}
                    {o.status !== 'closed' && (
                      <button className={mesBtnPrimaryClass} style={mesBtnPrimaryStyle} onClick={() => closeOrder(o.wo_id)}>
                        {o.status === 'done' ? '闭环归档' : '闭环'}
                      </button>
                    )}
                    {o.status === 'closed' && <span className="text-xs" style={{ color: MES_COLORS.faint }}>已归档</span>}
                  </td>
                </tr>
              ))}
              {orders.length === 0 && !loading && (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-sm" style={{ color: MES_COLORS.faint }}>
                    当前筛选条件下无工单 · 可在智能巡检页手动触发
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </MesPanel>

      <AssignModal woId={assignWo} onClose={() => setAssignWo(null)} onDone={() => load()} />
      <DoneModal woId={doneWo} onClose={() => setDoneWo(null)} onDone={() => load()} />
      <LogsModal woId={logsWo} onClose={() => setLogsWo(null)} />
    </MesShell>
  );
}

function AssignModal({ woId, onClose, onDone }: { woId: string | null; onClose: () => void; onDone: () => void }) {
  const [operator, setOperator] = useState('');
  const [busy, setBusy] = useState(false);
  if (!woId) return null;
  const submit = async () => {
    if (!operator.trim()) return toast.error('请填写维修人');
    setBusy(true);
    try {
      await mesApi.assignWorkOrder(woId, operator.trim());
      toast.success(`已派单给 ${operator.trim()}（open → assigned）`);
      onClose();
      setOperator('');
      onDone();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <MesModal open title={`派单 · ${woId}`} onClose={onClose} width={520}>
      <label className="mb-1.5 block text-xs" style={{ color: MES_COLORS.muted }}>
        维修人 / 班组（必填，将回填 assignee 并留痕）
      </label>
      <input
        className={mesInputClass}
        style={mesInputStyle}
        value={operator}
        onChange={(e) => setOperator(e.target.value)}
        placeholder="如：王师傅 / 电工班"
        autoFocus
      />
      <div className="mt-4 flex justify-end gap-2.5">
        <button className={mesBtnClass} style={mesBtnStyle} onClick={onClose}>取消</button>
        <button className={mesBtnPrimaryClass} style={mesBtnPrimaryStyle} onClick={submit} disabled={busy}>
          {busy ? '提交中…' : '确认派单'}
        </button>
      </div>
    </MesModal>
  );
}

function DoneModal({ woId, onClose, onDone }: { woId: string | null; onClose: () => void; onDone: () => void }) {
  const [operator, setOperator] = useState('');
  const [resolution, setResolution] = useState('');
  const [busy, setBusy] = useState(false);
  if (!woId) return null;
  const submit = async () => {
    if (!resolution.trim()) return toast.error('请填写处置结果');
    setBusy(true);
    try {
      await mesApi.doneWorkOrder(woId, resolution.trim(), operator.trim() || undefined);
      toast.success('处置已回填（→ done），可继续闭环归档');
      onClose();
      setResolution('');
      setOperator('');
      onDone();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <MesModal open title={`处置回填 · ${woId}`} onClose={onClose} width={520}>
      <label className="mb-1.5 block text-xs" style={{ color: MES_COLORS.muted }}>维修人</label>
      <input
        className={mesInputClass + ' mb-3'}
        style={mesInputStyle}
        value={operator}
        onChange={(e) => setOperator(e.target.value)}
        placeholder="如：王师傅"
      />
      <label className="mb-1.5 block text-xs" style={{ color: MES_COLORS.muted }}>
        处置结果（必填，写入工单 plan 并留痕）
      </label>
      <textarea
        className={mesInputClass + ' min-h-[72px] resize-y'}
        style={mesInputStyle}
        value={resolution}
        onChange={(e) => setResolution(e.target.value)}
        placeholder="如：更换主轴轴承 6208，校动平衡后复测振动 5.8，恢复正常"
        autoFocus
      />
      <div className="mt-4 flex justify-end gap-2.5">
        <button className={mesBtnClass} style={mesBtnStyle} onClick={onClose}>取消</button>
        <button className={mesBtnPrimaryClass} style={mesBtnPrimaryStyle} onClick={submit} disabled={busy}>
          {busy ? '提交中…' : '确认回填'}
        </button>
      </div>
    </MesModal>
  );
}

function LogsModal({ woId, onClose }: { woId: string | null; onClose: () => void }) {
  const [logs, setLogs] = useState<WorkOrderLog[] | null>(null);
  useEffect(() => {
    if (woId) {
      setLogs(null);
      mesApi.workOrderLogs(woId).then(setLogs).catch(() => setLogs([]));
    }
  }, [woId]);
  if (!woId) return null;
  return (
    <MesModal open title={`流转留痕 · ${woId}`} onClose={onClose} width={640}>
      {logs === null ? (
        <div className="py-6 text-center text-sm" style={{ color: MES_COLORS.faint }}>加载中…</div>
      ) : logs.length === 0 ? (
        <div className="py-6 text-center text-sm" style={{ color: MES_COLORS.faint }}>无留痕</div>
      ) : (
        logs.map((l, i) => (
          <div key={i} className="flex gap-3 py-2 text-xs" style={{ borderBottom: `1px solid ${MES_COLORS.border}` }}>
            <span className="w-[128px] shrink-0 tabular-nums" style={{ color: MES_COLORS.faint }}>{l.ts}</span>
            <span className="w-16 shrink-0 text-emerald-400">{l.operator || '系统'}</span>
            <span>
              <span className="mr-2 rounded bg-[#0F2440] px-1.5 py-0.5 text-[11px] text-[#4DA3FF]">{l.action}</span>
              {l.detail}
            </span>
          </div>
        ))
      )}
    </MesModal>
  );
}
