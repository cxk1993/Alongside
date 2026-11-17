/** 复盘面板：source / 最近 ingest 状态。 */
import type { ReviewBlock } from "./api";
import { isDegraded } from "./api";

/** 取数路径：后端枚举 direct/bridge/mock → 用户可见中文。 */
const PATH_LABEL: Record<string, string> = {
  direct: "直连",
  bridge: "桥接",
  mock: "内置",
};

export default function ReviewPanel({ review }: { review: ReviewBlock }) {
  const source = review.source ?? {};
  const down = isDegraded(String(source.status ?? "ok"));
  const mode = typeof source.mode === "string" ? source.mode : "—";
  const path = typeof source.path === "string" ? source.path : "—";
  const online = source.upstream_online === true;
  const lastSync =
    typeof source.last_sync_at === "string" && source.last_sync_at ? source.last_sync_at : null;
  const message = typeof source.message === "string" ? source.message : "";

  return (
    <section className="dash-card" aria-label="复盘">
      <div className="dash-card__title">复盘</div>
      <div className="dash-card__body">
        {down ? (
          <div className="dash-degraded" role="status">
            复盘模块暂不可用
          </div>
        ) : (
          <>
            <div className="dash-stats">
              <div className="dash-stat">
                <span className="dash-stat__num" data-testid="review-mode">
                  {mode}
                </span>
                <span className="dash-stat__lbl">取数模式</span>
              </div>
              <div className="dash-stat">
                <span className="dash-stat__num" data-testid="review-online">
                  {online ? "在线" : "离线"}
                </span>
                <span className="dash-stat__lbl">上游</span>
              </div>
            </div>
            <div className="dash-muted">
              取数路径：{PATH_LABEL[path] ?? path}
              {lastSync ? ` · 最近同步 ${lastSync}` : ""}
              {message ? ` · ${message}` : ""}
            </div>
          </>
        )}
        <div className="dash-muted">来源：GET /api/v1/review/source</div>
      </div>
    </section>
  );
}
