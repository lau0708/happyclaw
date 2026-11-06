/**
 * MesUi.tsx — 设备业务页共享的深色工业风组件
 * 视觉基调与 mes-dashboard（深色大屏）一致：#0B1220 底、#0E1729 面板、蓝色主强调。
 */
import { type ReactNode, useEffect } from 'react';

export const MES_THRESHOLDS = {
  vibration: { label: '振动 mm/s', max: 8.0 },
  temp: { label: '温度 ℃', max: 75.0 },
  pressure: { label: '压力 MPa', max: 5.5 },
} as const;

export type MesMetricKey = keyof typeof MES_THRESHOLDS;

export const MES_COLORS = {
  bg: '#0B1220',
  panel: '#0E1729',
  card: '#101B31',
  border: '#1C2A44',
  borderSoft: '#1A2740',
  text: '#E6EDF7',
  muted: '#8FA3C0',
  faint: '#51638A',
  accent: '#4DA3FF',
  ok: '#34D399',
  warn: '#FBBF24',
  bad: '#F87171',
};

export function MesShell({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      className="min-h-full"
      style={{ background: MES_COLORS.bg, color: MES_COLORS.text }}
    >
      <div className="px-4 py-5 sm:px-6 lg:px-8 lg:py-6">
        <div className="mx-auto max-w-7xl">
          <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
            <div>
              <h1 className="text-lg font-semibold tracking-wide">{title}</h1>
              {subtitle && (
                <p
                  className="mt-1 text-xs"
                  style={{ color: MES_COLORS.muted }}
                >
                  {subtitle}
                </p>
              )}
            </div>
            {actions}
          </header>
          {children}
        </div>
      </div>
    </div>
  );
}

export function MesPanel({
  title,
  right,
  children,
  className = '',
}: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl p-4 ${className}`}
      style={{
        background: MES_COLORS.panel,
        border: `1px solid ${MES_COLORS.border}`,
      }}
    >
      {title && (
        <div
          className="mb-3 flex flex-wrap items-center justify-between gap-2 text-[13px] font-medium"
          style={{ color: '#B7C6DE' }}
        >
          <span>{title}</span>
          {right && (
            <span className="text-[11px] font-normal" style={{ color: MES_COLORS.faint }}>
              {right}
            </span>
          )}
        </div>
      )}
      {children}
    </div>
  );
}

export function KpiCard({
  label,
  value,
  suffix,
  sub,
  tone = 'text',
}: {
  label: string;
  value: ReactNode;
  suffix?: string;
  sub?: ReactNode;
  tone?: 'text' | 'ok' | 'warn' | 'bad' | 'accent';
}) {
  const toneClass = { text: '', ok: 'text-emerald-400', warn: 'text-amber-400', bad: 'text-red-400', accent: 'text-[#4DA3FF]' }[tone];
  return (
    <div
      className="rounded-[10px] p-4"
      style={{ background: MES_COLORS.panel, border: `1px solid ${MES_COLORS.border}` }}
    >
      <div className="mb-1.5 text-xs" style={{ color: MES_COLORS.muted }}>
        {label}
      </div>
      <div className={`text-[26px] font-semibold tabular-nums ${toneClass}`}>
        {value}
        {suffix && <span className="text-sm font-normal" style={{ color: MES_COLORS.muted }}> {suffix}</span>}
      </div>
      {sub && <div className="mt-1 text-xs">{sub}</div>}
    </div>
  );
}

export function MetricBar({
  label,
  value,
  max,
  unit,
}: {
  label: string;
  value: number | null | undefined;
  max: number;
  unit?: string;
}) {
  const v = value ?? 0;
  const pct = Math.min(100, Math.round((v / max) * 100));
  const color = v > max ? MES_COLORS.bad : pct > 80 ? MES_COLORS.warn : MES_COLORS.ok;
  return (
    <div className="min-w-0 flex-1">
      <div className="mb-1 flex justify-between text-[11px]" style={{ color: MES_COLORS.muted }}>
        <span>
          {label}
          {unit ? '' : ` / ${max}`}
        </span>
        <span className={v > max ? 'text-red-400' : ''}>{value ?? '--'}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full" style={{ background: MES_COLORS.borderSoft }}>
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
    </div>
  );
}

export function Sparkline({
  values,
  color,
  width = 200,
  height = 22,
}: {
  values: number[];
  color: string;
  width?: number;
  height?: number;
}) {
  if (!values || values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values
    .map((v, i) => `${((i / (values.length - 1)) * width).toFixed(1)},${(height - 2 - ((v - min) / span) * (height - 4)).toFixed(1)}`)
    .join(' ');
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="mt-1.5 block">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { bg: string; fg: string; label: string }> = {
    open: { bg: '#33260A', fg: MES_COLORS.warn, label: '待处理' },
    assigned: { bg: '#0F2440', fg: MES_COLORS.accent, label: '已派单' },
    done: { bg: '#06281E', fg: MES_COLORS.ok, label: '已回填' },
    closed: { bg: '#1A2338', fg: MES_COLORS.muted, label: '已闭环' },
    running: { bg: '#06281E', fg: MES_COLORS.ok, label: '运行' },
    alarm: { bg: '#3B0D0D', fg: MES_COLORS.bad, label: '告警' },
  };
  const s = map[status] || { bg: '#1A2338', fg: MES_COLORS.muted, label: status };
  return (
    <span className="inline-block rounded px-2 py-0.5 text-[11px]" style={{ background: s.bg, color: s.fg }}>
      {s.label}
    </span>
  );
}

export function SeverityTag({ severity }: { severity: string }) {
  const map: Record<string, { bg: string; fg: string; label: string }> = {
    high: { bg: '#3B0D0D', fg: MES_COLORS.bad, label: '紧急' },
    medium: { bg: '#33260A', fg: MES_COLORS.warn, label: '一般' },
    low: { bg: '#06281E', fg: MES_COLORS.ok, label: '低优' },
  };
  const s = map[severity] || { bg: '#1A2338', fg: MES_COLORS.muted, label: severity };
  return (
    <span className="inline-block rounded px-2 py-0.5 text-[11px]" style={{ background: s.bg, color: s.fg }}>
      {s.label}
    </span>
  );
}

export function MesModal({
  open,
  title,
  onClose,
  children,
  width = 860,
}: {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-6 md:p-10"
      style={{ background: 'rgba(4,8,18,.72)' }}
      onClick={onClose}
    >
      <div
        className="w-full rounded-xl p-5"
        style={{ maxWidth: width, background: MES_COLORS.panel, border: `1px solid #2A3C61` }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <b className="text-[15px]">{title}</b>
          <button onClick={onClose} className="text-lg leading-none" style={{ color: MES_COLORS.muted }}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export const mesInputClass =
  'w-full rounded-md border px-3 py-2 text-[13px] outline-none focus:border-[#4DA3FF]';

export const mesInputStyle = {
  background: MES_COLORS.card,
  borderColor: '#2A3C61',
  color: MES_COLORS.text,
};

export const mesBtnClass =
  'rounded-md border px-3 py-1.5 text-xs transition-colors';

export const mesBtnStyle = {
  background: '#16233D',
  borderColor: '#2A3C61',
  color: '#B7C6DE',
};

export const mesBtnPrimaryClass = mesBtnClass + ' hover:opacity-90';

export const mesBtnPrimaryStyle = {
  background: '#1D4ED8',
  borderColor: '#1D4ED8',
  color: '#fff',
};
