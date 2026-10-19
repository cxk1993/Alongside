/**
 * BeeCount 同步面板（T08B）。
 *
 * ★ 只调 Alongside 后端 `/api/v1/finance/*`，**前端绝不直连 BeeCount**，
 *   也不接触 PAT / BEECOUNT_* 凭据（那些只在后端 .env）。
 * ★ 本轮只读：按钮触发 `POST /snapshots/sync`（MCP 读工具 → finance_snapshot）。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { financeApi, formatCents } from "./api";

function formatTs(iso: string | null | undefined): string {
  if (!iso) return "从未同步";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}

export default function BeeCountSyncPanel() {
  const qc = useQueryClient();

  const { data: source, isLoading: sourceLoading } = useQuery({
    queryKey: ["finance", "beecount-source"],
    queryFn: () => financeApi.beeCountSource(),
  });

  const { data: snaps } = useQuery({
    queryKey: ["finance", "snapshots"],
    queryFn: () => financeApi.snapshots({ limit: 5 }),
  });

  const syncMut = useMutation({
    mutationFn: () => financeApi.syncSnapshots(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["finance"] });
    },
  });

  const latest = snaps?.items?.[0];
  const upstream = source?.upstream ?? "…";
  const configured = source?.configured ?? false;

  return (
    <section className="beecount-panel" aria-label="BeeCount 同步">
      <header className="beecount-panel__head">
        <h3 className="beecount-panel__title">BeeCount 同步</h3>
        <span className="beecount-panel__badge" data-testid="bc-upstream">
          {upstream}
        </span>
      </header>

      <div className="beecount-panel__grid">
        <div className="beecount-panel__item">
          <span className="beecount-panel__label">状态</span>
          <span className="beecount-panel__value" data-testid="bc-status">
            {sourceLoading ? "加载中…" : configured ? "已就绪" : "未配置凭据（请检查后端 .env）"}
          </span>
        </div>
        <div className="beecount-panel__item">
          <span className="beecount-panel__label">最近同步</span>
          <span className="beecount-panel__value" data-testid="bc-last-sync">
            {formatTs(source?.last_sync)}
          </span>
        </div>
        <div className="beecount-panel__item">
          <span className="beecount-panel__label">最近快照总资产</span>
          <span className="beecount-panel__value" data-testid="bc-total-asset">
            {latest ? formatCents(latest.total_asset) : "—"}
          </span>
        </div>
        <div className="beecount-panel__item">
          <span className="beecount-panel__label">快照日</span>
          <span className="beecount-panel__value" data-testid="bc-snap-date">
            {source?.last_snapshot_date ?? latest?.date ?? "—"}
          </span>
        </div>
      </div>

      <div className="beecount-panel__actions">
        <button
          type="button"
          className="btn btn--primary"
          data-testid="bc-sync-btn"
          disabled={syncMut.isPending || sourceLoading}
          onClick={() => syncMut.mutate()}
        >
          {syncMut.isPending ? "同步中…" : "手动同步"}
        </button>
        <span className="beecount-panel__hint">只读快照 · 同日覆盖不翻倍 · 不写 BeeCount</span>
      </div>

      {syncMut.isError ? (
        <p className="beecount-panel__error" data-testid="bc-sync-error" role="alert">
          同步失败：{(syncMut.error as Error)?.message || "上游不可达或未配置"}
        </p>
      ) : null}
      {syncMut.isSuccess && syncMut.data?.snapshot ? (
        <p className="beecount-panel__ok" data-testid="bc-sync-ok">
          已同步 {syncMut.data.snapshot.date}（upstream={syncMut.data.upstream}）
        </p>
      ) : null}
    </section>
  );
}
