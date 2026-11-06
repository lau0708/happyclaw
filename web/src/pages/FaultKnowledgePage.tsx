/**
 * FaultKnowledgePage — 故障知识库（深色工业大屏风格）
 * 关键词检索（search_knowledge）+ 手动沉淀处置经验（save_knowledge）+ 溯源展示
 */
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { mesApi, type Knowledge } from '../api/mes';
import {
  MesShell,
  MesPanel,
  MesModal,
  KpiCard,
  MES_COLORS,
  mesInputClass,
  mesInputStyle,
  mesBtnClass,
  mesBtnStyle,
  mesBtnPrimaryClass,
  mesBtnPrimaryStyle,
} from '../components/mes/MesUi';

function KnCard({ k }: { k: Knowledge }) {
  return (
    <div className="mb-2.5 rounded-lg p-3.5" style={{ background: MES_COLORS.card, border: `1px solid ${MES_COLORS.borderSoft}` }}>
      <div className="mb-1.5 flex flex-wrap justify-between gap-2 font-mono text-xs text-[#4DA3FF]">
        <span>{k.id} · {k.fault_code}</span>
        <span style={{ color: MES_COLORS.faint }}>{k.created_at}</span>
      </div>
      {[
        ['症状', k.symptom],
        ['根因', k.root_cause],
        ['处置', k.resolution],
      ].map(([kk, v]) => (
        <div key={kk} className="my-0.5 text-[13px] leading-relaxed">
          <span className="mr-1 inline-block w-14 shrink-0" style={{ color: MES_COLORS.muted }}>{kk}</span>
          {v}
        </div>
      ))}
      <div className="text-[13px]">
        <span className="mr-1 inline-block w-14 shrink-0" style={{ color: MES_COLORS.muted }}>溯源</span>
        {k.source_wo_id ? (
          <span className="font-mono text-xs text-[#4DA3FF]">{k.source_wo_id}</span>
        ) : (
          <span style={{ color: MES_COLORS.faint }}>预置种子</span>
        )}
      </div>
    </div>
  );
}

export function FaultKnowledgePage() {
  const [items, setItems] = useState<Knowledge[]>([]);
  const [error, setError] = useState('');
  const [keyword, setKeyword] = useState('');
  const [searching, setSearching] = useState(false);
  const [addOpen, setAddOpen] = useState(false);

  const load = useCallback(async (q?: string) => {
    setSearching(true);
    try {
      setItems(await mesApi.knowledge(q || undefined));
      setError('');
    } catch (e) {
      setError(String(e));
    } finally {
      setSearching(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <MesShell
      title="故障知识库"
      subtitle="处置经验沉淀与检索 · 关单复盘自动写入（source_wo_id 溯源）· 诊断 Agent 首轮命中"
      actions={
        <button className={mesBtnPrimaryClass} style={mesBtnPrimaryStyle} onClick={() => setAddOpen(true)}>
          + 沉淀新经验
        </button>
      }
    >
      {error && (
        <div className="mb-5 rounded-lg px-4 py-3 text-sm text-amber-300" style={{ background: '#33260A', border: '1px solid #7a5b16' }}>
          MES 后端未连接：请先运行 <code>python3 mes-mcp-server/api.py</code>
        </div>
      )}

      <div className="mb-4 grid grid-cols-3 gap-3.5">
        <KpiCard label="知识条目" value={items.length} tone="accent" sub={<span style={{ color: MES_COLORS.muted }}>当前检索结果</span>} />
        <KpiCard label="溯源沉淀" value={items.filter((k) => k.source_wo_id).length} tone="ok" sub={<span style={{ color: MES_COLORS.muted }}>带 source_wo_id</span>} />
        <KpiCard label="检索方式" value="关键词" sub={<span style={{ color: MES_COLORS.muted }}>向量 RAG 为升级项</span>} />
      </div>

      <MesPanel>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <input
            className={mesInputClass}
            style={{ ...mesInputStyle, width: 280 }}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && load(keyword.trim())}
            placeholder="按症状关键词检索，如：振动 / 温度 / 泄漏"
          />
          <button className={mesBtnClass} style={mesBtnStyle} onClick={() => load(keyword.trim())} disabled={searching}>
            {searching ? '检索中…' : '检索'}
          </button>
          {keyword && (
            <button className={mesBtnClass} style={mesBtnStyle} onClick={() => { setKeyword(''); load(''); }}>
              清空
            </button>
          )}
        </div>
        {items.length > 0 ? (
          items.map((k) => <KnCard key={k.id} k={k} />)
        ) : (
          <div className="py-8 text-center text-sm" style={{ color: MES_COLORS.faint }}>
            {searching ? '检索中…' : '无匹配结果'}
          </div>
        )}
      </MesPanel>

      <AddKnowledgeModal open={addOpen} onClose={() => setAddOpen(false)} onSaved={() => load()} />
    </MesShell>
  );
}

function AddKnowledgeModal({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({ fault_code: '', symptom: '', root_cause: '', resolution: '', source_wo_id: '' });
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async () => {
    if (!form.fault_code.trim() || !form.symptom.trim() || !form.root_cause.trim() || !form.resolution.trim())
      return toast.error('请填写全部必填字段');
    setBusy(true);
    try {
      const r = await mesApi.createKnowledge({
        fault_code: form.fault_code.trim(),
        symptom: form.symptom.trim(),
        root_cause: form.root_cause.trim(),
        resolution: form.resolution.trim(),
        source_wo_id: form.source_wo_id.trim() || null,
      });
      toast.success(`经验已沉淀（${r.id}），下轮诊断可命中`);
      onClose();
      setForm({ fault_code: '', symptom: '', root_cause: '', resolution: '', source_wo_id: '' });
      onSaved();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <MesModal open={open} title="沉淀处置经验" onClose={onClose} width={560}>
      <div className="grid grid-cols-2 gap-x-3.5">
        <div>
          <label className="mb-1.5 block text-xs" style={{ color: MES_COLORS.muted }}>故障代码（必填）</label>
          <input className={mesInputClass} style={mesInputStyle} value={form.fault_code} onChange={set('fault_code')} placeholder="如 SPINDLE-BEARING" />
        </div>
        <div>
          <label className="mb-1.5 block text-xs" style={{ color: MES_COLORS.muted }}>溯源工单号（可选）</label>
          <input className={mesInputClass} style={mesInputStyle} value={form.source_wo_id} onChange={set('source_wo_id')} placeholder="如 WO-2026…" />
        </div>
      </div>
      <label className="mb-1.5 mt-3 block text-xs" style={{ color: MES_COLORS.muted }}>症状关键词（必填，供检索命中）</label>
      <input className={mesInputClass} style={mesInputStyle} value={form.symptom} onChange={set('symptom')} placeholder="如 主轴异响 振动升高" />
      <label className="mb-1.5 mt-3 block text-xs" style={{ color: MES_COLORS.muted }}>根因（必填）</label>
      <input className={mesInputClass} style={mesInputStyle} value={form.root_cause} onChange={set('root_cause')} placeholder="如 轴承游隙超差" />
      <label className="mb-1.5 mt-3 block text-xs" style={{ color: MES_COLORS.muted }}>处置方案（必填）</label>
      <textarea className={mesInputClass + ' min-h-[70px] resize-y'} style={mesInputStyle} value={form.resolution} onChange={set('resolution')} placeholder="如 停机→换轴承→校动平衡→复测振动" />
      <div className="mt-4 flex justify-end gap-2.5">
        <button className={mesBtnClass} style={mesBtnStyle} onClick={onClose}>取消</button>
        <button className={mesBtnPrimaryClass} style={mesBtnPrimaryStyle} onClick={submit} disabled={busy}>
          {busy ? '保存中…' : '保存到知识库'}
        </button>
      </div>
    </MesModal>
  );
}
