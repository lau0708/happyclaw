import { useEffect, useState } from 'react';
import { mesApi, type Knowledge } from '../api/mes';

export function FaultKnowledgePage() {
  const [items, setItems] = useState<Knowledge[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    mesApi
      .knowledge()
      .then(setItems)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="min-h-full px-4 py-5 sm:px-6 lg:px-8 lg:py-8">
      <div className="mx-auto max-w-6xl">
        <header className="mb-6">
          <h1 className="text-2xl font-bold text-foreground">故障知识库</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
            由每次工单「复盘沉淀」累积形成，后续同类故障由诊断 Agent 直接命中历史处置方案（经验回流）。
          </p>
        </header>

        {error && (
          <div className="mb-6 rounded-xl border border-border bg-warning-bg px-4 py-3 text-sm text-warning">
            后端未连接：请先运行 <code>python3 api.py</code>。
          </div>
        )}

        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无沉淀经验。</p>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {items.map((k) => (
              <div
                key={k.id}
                className="rounded-xl border border-l-4 border-l-green-500 border-border bg-card p-4 shadow-sm"
              >
                <div className="mb-2 flex items-center gap-2">
                  <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                    {k.fault_code}
                  </span>
                  <span className="text-sm font-medium text-foreground">
                    {k.symptom}
                  </span>
                </div>
                <div className="space-y-1 text-sm leading-6 text-muted-foreground">
                  <div>
                    <span className="font-medium text-foreground">根因：</span>
                    {k.root_cause}
                  </div>
                  <div>
                    <span className="font-medium text-foreground">处置：</span>
                    {k.resolution}
                  </div>
                </div>
                <div className="mt-2 text-xs text-muted-foreground">
                  来源工单：{k.source_wo_id || '-'} · {k.created_at}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
