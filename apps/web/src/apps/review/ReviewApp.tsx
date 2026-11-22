import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePluginEvent } from "@/shared/api/events";
import { formatSeconds, reviewApi, type DayDetail, type NamedRow, type SourceInfo } from "./api";
import ReviewCalendar from "./ReviewCalendar";
import "./review.css";

/** 取数路径：后端枚举 direct/bridge/mock → 用户可见中文。 */
const PATH_LABEL: Record<string, string> = {
  direct: "直连",
  bridge: "桥接",
  mock: "内置",
};

function sourceLine(src: SourceInfo | null | undefined): string {
  if (!src) return "数据源：Work-Review · 读取中…";
  const pathLabel = PATH_LABEL[src.path] ?? src.path;
  let sync = "尚无同步";
  if (src.last_sync_at) {
    const mins = Math.max(
      0,
      Math.floor((Date.now() - new Date(src.last_sync_at).getTime()) / 60000),
    );
    sync = mins < 1 ? "最后同步 刚刚" : `最后同步 ${mins} 分钟前`;
  }
  return `数据源：Work-Review · 模式：${pathLabel} · ${sync}`;
}

function isBridgeOffline(src: SourceInfo | null | undefined): boolean {
  return Boolean(src?.bridge && (src.bridge_online === false || src.message === "桥离线"));
}

function BarList({ rows, emptyText }: { rows: NamedRow[]; emptyText: string }) {
  if (!rows.length) return <div className="review-empty">{emptyText}</div>;
  const max = Math.max(1, ...rows.map((r) => r.seconds));
  return (
    <div className="review-bars">
      {rows.map((r) => (
        <div key={r.name} className="review-bar-row">
          <div className="review-bar-row__name" title={r.name}>
            {r.name}
          </div>
          <div className="review-bar-track">
            <div
              className="review-bar-fill"
              style={{ width: `${Math.round((r.seconds / max) * 100)}%` }}
            />
          </div>
          <div className="review-bar-row__val">{r.duration_text || formatSeconds(r.seconds)}</div>
        </div>
      ))}
    </div>
  );
}

function HourlyBars({ day }: { day?: DayDetail | null }) {
  const rows = day?.hourly ?? [];
  if (!rows.length) return <div className="review-empty">无小时数据</div>;
  const max = Math.max(1, ...rows.map((r) => r.seconds));
  return (
    <div className="review-hourly" aria-label="24小时活跃度">
      {rows.map((r) => (
        <div
          key={r.hour}
          className="review-hourly__col"
          title={`${r.hour}:00 ${r.duration_text || formatSeconds(r.seconds)}`}
          style={{ height: `${Math.max(2, Math.round((r.seconds / max) * 100))}%` }}
        />
      ))}
    </div>
  );
}

/** 复盘主界面：Day 列表 + 单日摘要 + 原文抽屉 + source 状态 + 手动 ingest。 */
export default function ReviewApp() {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [showRaw, setShowRaw] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [trendDays, setTrendDays] = useState(7);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["review"] });
  };
  usePluginEvent("review.day.ingested", invalidate);

  const sourceQ = useQuery({
    queryKey: ["review", "source"],
    queryFn: () => reviewApi.source(),
  });

  const daysQ = useQuery({
    // ★ 取全量（日历需要"有日报的全部日期"；原 size=10 只够列表）
    queryKey: ["review", "days", page],
    queryFn: () => reviewApi.days({ page, size: 200 }),
  });

  const days = daysQ.data?.items ?? [];
  const activeDate = selected ?? days[0]?.date ?? null;

  const dayQ = useQuery({
    queryKey: ["review", "day", activeDate],
    queryFn: () => reviewApi.day(activeDate as string),
    enabled: Boolean(activeDate),
  });

  const rawQ = useQuery({
    queryKey: ["review", "raw", activeDate],
    queryFn: () => reviewApi.raw(activeDate as string),
    enabled: Boolean(activeDate) && showRaw,
  });

  const trendQ = useQuery({
    queryKey: ["review", "trend", trendDays],
    queryFn: () => reviewApi.trend("total", trendDays),
  });

  const weekQ = useQuery({
    queryKey: ["review", "weekly", activeDate],
    queryFn: () => reviewApi.weekly(activeDate as string),
    enabled: Boolean(activeDate),
  });

  // ★ 同步失败原因（astrbot 下场 · 主人「点了没数据/没反应」→ 要让人看见为什么）
  const [ingestError, setIngestError] = useState<string | null>(null);
  const ingestMut = useMutation({
    mutationFn: (date?: string) => reviewApi.ingest(date),
    onSuccess: () => {
      setIngestError(null);
      invalidate();
    },
    onError: (err: unknown) => {
      // 后端把 Work-Review 的 400（如「未找到该日期的日报」）透传上来，这里如实展示。
      const msg = err instanceof Error ? err.message : String(err);
      setIngestError(msg);
    },
  });

  // ★ 全量同步历史日报（astrbot 下场 · 主人「同步理应同步历史所有日报」）
  const [ingestAllMsg, setIngestAllMsg] = useState<string | null>(null);
  const ingestAllMut = useMutation({
    mutationFn: () => reviewApi.ingestAll(),
    onSuccess: (r) => {
      setIngestAllMsg(
        `同步完成：共 ${r.total} 天，成功 ${r.ok}，失败 ${r.failed}${r.skipped ? `，跳过 ${r.skipped}` : ""}`,
      );
      invalidate();
    },
    onError: (err: unknown) => {
      setIngestAllMsg(`全量同步失败：${err instanceof Error ? err.message : String(err)}`);
    },
  });

  const noteMut = useMutation({
    mutationFn: (args: { date: string; content_md: string }) =>
      reviewApi.addNote(args.date, args.content_md),
    onSuccess: () => {
      setNoteText("");
      invalidate();
    },
  });
  // ★ 删除批注（astrbot 下场 · 主人⑤「不能在复盘窗口内增减笔记」的"减"半边）
  const deleteNoteMut = useMutation({
    mutationFn: (id: string) => reviewApi.deleteNote(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review"] });
    },
  });

  const src = sourceQ.data ?? dayQ.data?.source ?? null;
  const offline = isBridgeOffline(src);
  const day = dayQ.data;
  const trend = trendQ.data;
  const week = weekQ.data;

  const trendMax = useMemo(() => {
    const pts = trend?.points ?? [];
    return Math.max(1, ...pts.map((p) => p.total_seconds));
  }, [trend]);

  return (
    <div className="review-root">
      <div className="review-toolbar">
        <strong>复盘</strong>
        <span
          className={`review-source${offline ? " review-source--offline" : ""}`}
          data-testid="review-source"
        >
          {offline
            ? "桥离线"
            : `${sourceLine(src)}${src?.upstream_version ? ` · v${src.upstream_version}` : ""}`}
        </span>
        <button
          type="button"
          className="btn"
          onClick={() => ingestMut.mutate(activeDate ?? undefined)}
          disabled={ingestMut.isPending}
          aria-label="手动同步日报"
          // ★ 说明（astrbot 下场 · 主人「点了没数据/没反应」）：
          //   同步的是「当前选中日期」；无选中时后端默认「今天」——
          //   而 Work-Review 若当天没写日报会返回 400「未找到该日期的日报」，
          //   这正是「点了没数据」的真相（链路本身是通的）。失败原因现由下方 role=alert 如实展示。
          title={
            activeDate
              ? `同步 ${activeDate} 的日报`
              : "将同步「今天」；若今天没写日报，Work-Review 会返回「未找到该日期的日报」"
          }
        >
          {ingestMut.isPending ? "同步中…" : offline ? "同步（桥离线）" : "手动同步"}
        </button>
        {/* ★ 全量同步（astrbot 下场 · 主人「同步理应同步历史所有日报」） */}
        <button
          type="button"
          className="btn"
          onClick={() => ingestAllMut.mutate()}
          disabled={ingestAllMut.isPending}
          aria-label="同步全部历史日报"
          title="拉取 Work-Review 上有日报的全部日期（幂等，可重复跑）"
        >
          {ingestAllMut.isPending ? "全量同步中…" : "同步全部历史"}
        </button>
      </div>
      {ingestAllMsg && (
        <div className="review-sync-error" role="status">
          {ingestAllMsg}
        </div>
      )}
      {ingestError && (
        <div className="review-sync-error" role="alert">
          同步失败：{ingestError}
        </div>
      )}

      <div className="review-body">
        <aside className="review-days" aria-label="日报日期列表">
          {/* ★ 日历视图（astrbot 下场 · 主人「用日历的形式展示，想看哪天看哪天」） */}
          <ReviewCalendar
            dates={days.map((d) => d.date)}
            activeDate={activeDate}
            onSelect={(d) => setSelected(d)}
          />
          {daysQ.isLoading ? (
            <div className="review-empty" style={{ padding: 12 }}>
              加载中…
            </div>
          ) : days.length === 0 ? (
            <div className="review-empty" style={{ padding: 12 }}>
              还没有日报，点「手动同步」拉取
            </div>
          ) : (
            days.map((d) => (
              <button
                key={d.date}
                type="button"
                className={`review-day-item${d.date === activeDate ? " review-day-item--active" : ""}`}
                onClick={() => setSelected(d.date)}
              >
                <div className="review-day-item__date">{d.date}</div>
                <div className="review-day-item__meta">
                  {d.is_empty ? "当日无记录" : formatSeconds(d.total_seconds)}
                  {d.has_ai ? " · AI" : ""}
                </div>
              </button>
            ))
          )}
          <div className="review-tiny" style={{ padding: 8 }}>
            第 {daysQ.data?.page ?? page} 页 · 共 {daysQ.data?.total ?? 0} 天
            <button
              type="button"
              className="btn"
              style={{ marginLeft: 8 }}
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              上页
            </button>
            <button
              type="button"
              className="btn"
              style={{ marginLeft: 4 }}
              disabled={!daysQ.data?.has_more}
              onClick={() => setPage((p) => p + 1)}
            >
              下页
            </button>
          </div>
        </aside>

        <main className="review-main">
          {!activeDate ? (
            <div className="review-empty">选择左侧日期查看复盘</div>
          ) : dayQ.isLoading ? (
            <div className="review-empty">读取中…</div>
          ) : dayQ.isError ? (
            /* ★ 2026-11-22（汐瑶 · 三态收口）：原 `isLoading || !day` 把 error 也说成「加载中…」
               —— 永远转不完。 */
            <div className="review-empty review-source--offline">
              没读到 —— 接口异常，不是「这天没有记录」
            </div>
          ) : !day ? (
            <div className="review-empty">这一天没有记录</div>
          ) : (
            <>
              <section className="review-panel">
                <div className="review-panel__title">
                  {activeDate} · {day.is_empty ? "当日无记录" : formatSeconds(day.total_seconds)}
                </div>
                {day.is_empty ? (
                  <div className="review-empty">{day.empty_hint || "当日无记录"}</div>
                ) : (
                  <>
                    <div className="review-panel__title">时间分配</div>
                    <BarList rows={day.categories ?? []} emptyText="无类别数据" />
                    <div className="review-panel__title" style={{ marginTop: 12 }}>
                      应用
                    </div>
                    <BarList rows={(day.apps ?? []).slice(0, 10)} emptyText="无应用数据" />
                    <div className="review-panel__title" style={{ marginTop: 12 }}>
                      网站
                    </div>
                    <BarList rows={(day.domains ?? []).slice(0, 10)} emptyText="无网站数据" />
                    <div className="review-panel__title" style={{ marginTop: 12 }}>
                      活跃度（小时）
                    </div>
                    <HourlyBars day={day} />
                  </>
                )}
                <div className="review-src-line">{sourceLine(day.source ?? src)}</div>
              </section>

              <section className="review-panel">
                <div className="review-panel__title">机器 AI 分析</div>
                <div className="review-ai" data-testid="ai-analysis">
                  {day.ai_analysis_md?.trim() ? day.ai_analysis_md : "（缺 AI 分析块，独立降级）"}
                </div>
              </section>

              <section className="review-panel">
                <div className="review-panel__title">我的批注</div>
                <div className="review-note-list">
                  {(day.notes ?? []).length === 0 ? (
                    <div className="review-empty">还没有批注</div>
                  ) : (
                    (day.notes ?? []).map((n) => (
                      <div key={n.id} className="review-note-item">
                        <span className="review-note-item__text">{n.content_md}</span>
                        {/* ★ 删除批注（astrbot 下场 · 主人⑤「增减笔记」） */}
                        <button
                          type="button"
                          className="btn review-note-item__del"
                          aria-label={`删除批注 ${n.id}`}
                          disabled={deleteNoteMut.isPending}
                          onClick={() => deleteNoteMut.mutate(n.id)}
                        >
                          删除
                        </button>
                      </div>
                    ))
                  )}
                </div>
                <form
                  className="review-note-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const text = noteText.trim();
                    if (!text || !activeDate || noteMut.isPending) return;
                    noteMut.mutate({ date: activeDate, content_md: text });
                  }}
                >
                  <textarea
                    value={noteText}
                    onChange={(e) => setNoteText(e.target.value)}
                    placeholder="写一条自己的批注（与机器日报分开存）"
                    aria-label="批注内容"
                  />
                  <button type="submit" className="btn btn--primary" disabled={!noteText.trim()}>
                    保存批注
                  </button>
                </form>
              </section>

              <section className="review-panel">
                <div className="review-panel__title">
                  原始日报
                  <button
                    type="button"
                    className="btn"
                    style={{ marginLeft: 8 }}
                    onClick={() => setShowRaw(true)}
                    aria-label="查看原始日报"
                  >
                    查看原文
                  </button>
                </div>
                <div className="review-tiny">
                  {day.raw_path || `work-review:${activeDate}`}
                  {offline ? " · 已缓存数据仍可看" : ""}
                </div>
              </section>

              <section className="review-panel">
                <div className="review-panel__title">
                  趋势
                  <span style={{ marginLeft: 8, fontWeight: 400 }}>
                    <button
                      type="button"
                      className={`btn${trendDays === 7 ? " btn--primary" : ""}`}
                      onClick={() => setTrendDays(7)}
                    >
                      7日
                    </button>
                    <button
                      type="button"
                      className={`btn${trendDays === 30 ? " btn--primary" : ""}`}
                      style={{ marginLeft: 4 }}
                      onClick={() => setTrendDays(30)}
                    >
                      30日
                    </button>
                  </span>
                </div>
                {trend && trend.points.length > 0 ? (
                  <>
                    <div className="review-hourly" aria-label="趋势条">
                      {trend.points.map((p) => (
                        <div
                          key={p.date}
                          className="review-hourly__col"
                          title={`${p.date} ${formatSeconds(p.total_seconds)}`}
                          style={{
                            height: `${Math.max(2, Math.round((p.total_seconds / trendMax) * 100))}%`,
                          }}
                        />
                      ))}
                    </div>
                    <div className="review-tiny">{trend.conclusion}</div>
                    <div className="review-tiny">
                      {trend.points
                        .slice(-3)
                        .map((p) => `${p.date}=${formatSeconds(p.total_seconds)}`)
                        .join(" · ")}
                    </div>
                  </>
                ) : (
                  <div className="review-empty">区间内暂无趋势数据</div>
                )}
              </section>

              <section className="review-panel">
                <div className="review-panel__title">周报</div>
                {week?.available === false ? (
                  <div className="review-empty review-source--offline">
                    {week.offline_hint || "桥离线"}
                  </div>
                ) : week ? (
                  <>
                    <div>{week.summary}</div>
                    <div className="review-tiny">
                      {week.week_start} ~ {week.week_end} · 合计 {formatSeconds(week.total_seconds)}
                    </div>
                  </>
                ) : weekQ.isError ? (
                  /* ★ 2026-11-22（汐瑶 · 三态收口）：原来 error 也落到「加载中…」。 */
                  <div className="review-empty review-source--offline">
                    没读到 —— 接口异常，不是「本周没有记录」
                  </div>
                ) : (
                  <div className="review-empty">读取中…</div>
                )}
              </section>
            </>
          )}
        </main>
      </div>

      {showRaw && (
        <div className="review-drawer-backdrop" onClick={() => setShowRaw(false)}>
          <div
            className="review-drawer"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label="原始日报"
          >
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
              <strong>原始日报 · {activeDate}</strong>
              <button type="button" className="btn" onClick={() => setShowRaw(false)}>
                关闭
              </button>
            </div>
            {rawQ.isLoading ? (
              <div className="review-empty">加载中…</div>
            ) : rawQ.data?.found ? (
              <pre data-testid="raw-markdown">{rawQ.data.markdown}</pre>
            ) : (
              <div className="review-empty">未找到原文（可先手动同步）</div>
            )}
            <div className="review-tiny" style={{ marginTop: 12 }}>
              {rawQ.data?.raw_path || `work-review:${activeDate}`}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
