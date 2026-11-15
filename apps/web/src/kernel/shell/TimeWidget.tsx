import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "@/shared/api/client";
import { useDesktopStore } from "../store";

/**
 * U2 · 聚合小日历 · 视图侧（拍案 1 号：U2 视图层 → Doubao）。
 *
 * 右上角时间区 → 点击展开「今日摘要」面板，跨插件聚合：
 *   calendar 日程 / todo 待办 / diary 日记 / review 复盘
 *
 * 数据契约（workbuddy《todaySummary 数据源规范 v1》· 前端并发调各插件自供端点）：
 *   GET /api/v1/<id>/today-summary
 *   → { title?, items:[{text, state?, count?}], link? }   items ≤5
 * 三态语义（★ 铁律：「没装」≠「没数据」必须界面可区分）：
 *   404           → 「未安装 · 去安装」（占位，不消失）
 *   200 空 items  → 「今日暂无 X」（空态）
 *   200 有        → 列表
 *   5xx           → 「暂时不可用」（≠ 没数据）
 * 铁律：逐分区独立 try/catch（某插件坏了不拖累其余）；不轮询。
 */

interface TodaySummaryItem {
  text: string;
  state?: "info" | "due" | "done" | "alert";
  count?: number;
}
/** 内核 BFF /api/v1/summary/today（裁决令61 丙案：前端只调这一个，杜绝双份）。 */
interface SummaryProvider {
  id: string;
  status: "ok" | "not-implemented" | "unavailable";
  data?: { title?: string; items?: unknown[]; link?: string };
}
interface SummaryTodayResp {
  providers: SummaryProvider[];
}

const PROV_SOURCES = [
  { id: "calendar", moduleId: "calendar", label: "日程" }, // purity-ok: U2 规范四源清单（BFF provider id 数据源声明，裁决令61丙案只调 BFF），非业务逻辑
  { id: "todo", moduleId: "todo", label: "待办" },
  { id: "diary", moduleId: "diary", label: "日记" },
  { id: "review", moduleId: "review", label: "复盘" },
] as const;

function useClock(): string {
  const [now, setNow] = useState(() => formatNow());
  useEffect(() => {
    const t = setInterval(() => setNow(formatNow()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function formatNow(): string {
  try {
    return new Date().toLocaleTimeString("zh-CN", { hour12: false });
  } catch {
    return "";
  }
}

const STATE_CLASS: Record<string, string> = {
  info: "today-sum__item--info",
  due: "today-sum__item--due",
  done: "today-sum__item--done",
  alert: "today-sum__item--alert",
};

/** 形状归一（兜底）：四源 today-summary 可能返回 string[] 或 object[]（规范 v1 或各源自定）。
 * BFF 透传不解析（R-1），视图层必须容忍两种 -> 统一成 {text,state}。 */
function normalizeItems(items: unknown): TodaySummaryItem[] {
  if (!Array.isArray(items)) return [];
  return items
    .map((it): TodaySummaryItem | null => {
      if (typeof it === "string") return { text: it };
      if (it && typeof it === "object") {
        const o = it as Record<string, unknown>;
        const text = String(o.text ?? o.title ?? "");
        if (!text) return null;
        const state = ["info", "due", "done", "alert"].includes(String(o.state))
          ? (o.state as TodaySummaryItem["state"])
          : "info";
        return { text, state, count: typeof o.count === "number" ? o.count : undefined };
      }
      return null;
    })
    .filter((it): it is TodaySummaryItem => it !== null);
}

/** 单个插件分区：三态渲染 + 独立降级（U2 判据 J3/J4/J6）。
 * 裁决令61 丙案：前端只调 BFF /api/v1/summary/today（单请求，四分区共享）；
 * provider.status 三态 = ok / not-implemented（未安装）/ unavailable（暂时不可用）。 */
function ProvSection({ id, moduleId, label }: { id: string; moduleId: string; label: string }) {
  const openWindow = useDesktopStore((s) => s.openWindow);
  const { data, isError, isLoading, error } = useQuery({
    queryKey: ["summary-today"],
    queryFn: (): Promise<SummaryTodayResp> => api.get<SummaryTodayResp>("/api/v1/summary/today"),
    retry: 1,
    refetchOnWindowFocus: false,
  });

  const prov = data?.providers?.find((p) => p.id === id);
  const body = prov?.data;
  const items = normalizeItems(body?.items);
  const go = () => openWindow(moduleId);

  return (
    <section className="today-sum__sec" data-testid={`today-sum-${id}`}>
      <button type="button" className="today-sum__head" onClick={go}>
        <span>{label}</span>
        {body?.title ? <span className="today-sum__sub">{body.title}</span> : null}
        {items.length > 0 ? <span className="today-sum__count">{items.length}</span> : null}
      </button>
      {isLoading ? (
        <div className="today-sum__empty">…</div>
      ) : isError ? (
        // V4-01：BFF 端点 401（未登录/会话过期）→ 「未登录」子态，不再混进「暂时不可用」
        error instanceof ApiError && error.status === 401 ? (
          <div className="today-sum__empty" data-testid={`today-sum-${id}-auth`}>
            未登录 · 请重新登录
          </div>
        ) : (
          // BFF 端点本身 5xx/404 → 全面板降级（单点，非「没数据」）
          <div className="today-sum__empty" data-testid={`today-sum-${id}-err`}>
            暂时不可用
          </div>
        )
      ) : prov?.status === "not-implemented" ? (
        // 该源未实现：占位 + 引导（★ 铁律：不消失，可操作）
        <div className="today-sum__na" data-testid={`today-sum-${id}-na`}>
          未安装 · 去安装
        </div>
      ) : prov?.status === "unavailable" ? (
        // 该源 5xx/超时：独立降级（缺一不塌）
        <div className="today-sum__empty" data-testid={`today-sum-${id}-err`}>
          暂时不可用
        </div>
      ) : items.length === 0 ? (
        <div className="today-sum__empty">今日暂无{label}</div>
      ) : (
        <ul className="today-sum__list">
          {items.map((it, i) => (
            <li key={i} className={`today-sum__item ${STATE_CLASS[it.state ?? "info"] ?? ""}`}>
              <span className="today-sum__dot" aria-hidden="true" />
              <span className="today-sum__txt">{it.text}</span>
              {typeof it.count === "number" ? (
                <span className="today-sum__cnt">{it.count}</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* —— U2 v2：月历 + 按日聚合（MiMo day-dots / day-peek 契约） —— */

interface DayDotsResp {
  [date: string]: string[];
}
interface DayPeekList {
  id?: string;
  title?: string;
  text?: string;
  start?: string;
  done?: boolean;
}
interface DayPeekResp {
  date: string;
  marks: Record<string, number>;
  dots: Record<string, boolean>;
  list: Record<string, DayPeekList[]>;
}

/** 生成月历网格（周一起始，42 格，含前后月补位）。 */
function monthGrid(year: number, month: number): (string | null)[] {
  const first = new Date(year, month, 1);
  const startDow = (first.getDay() + 6) % 7; // 周一=0
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells: (string | null)[] = [];
  const fmt = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  for (let i = 0; i < startDow; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(fmt(new Date(year, month, d)));
  while (cells.length % 7 !== 0) cells.push(null);
  return cells;
}

const DOT_KIND = PROV_SOURCES.map((p) => p.id);

/** 月历：day-dots 打点 + 选中日切换（U2 v2）。 */
function MonthGrid({
  selected,
  onSelect,
}: {
  selected: string;
  onSelect: (date: string) => void;
}) {
  const today = new Date();
  const [view, setView] = useState(() => ({
    y: today.getFullYear(),
    m: today.getMonth(),
  }));
  const y = view.y;
  const m = view.m;
  const monthKey = `${y}-${String(m + 1).padStart(2, "0")}`;
  const from = `${monthKey}-01`;
  const lastDay = new Date(y, m + 1, 0).getDate();
  const to = `${monthKey}-${String(lastDay).padStart(2, "0")}`;
  const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;

  const { data: dots } = useQuery({
    queryKey: ["day-dots", from, to],
    queryFn: async (): Promise<DayDotsResp> =>
      api.get<DayDotsResp>(`/api/v1/dashboard/day-dots?from=${from}&to=${to}`),
    retry: 1,
    refetchOnWindowFocus: false,
  });

  const shift = (delta: number) => {
    const d = new Date(y, m + delta, 1);
    setView({ y: d.getFullYear(), m: d.getMonth() });
  };
  const gotoToday = () => {
    setView({ y: today.getFullYear(), m: today.getMonth() });
    onSelect(todayStr);
  };

  const cells = monthGrid(y, m);
  const weekLabels = ["一", "二", "三", "四", "五", "六", "日"];

  return (
    <div className="today-sum__cal" data-testid="month-grid">
      <div className="today-sum__cal-head">
        <button type="button" aria-label="上个月" onClick={() => shift(-1)}>
          ‹
        </button>
        <span className="today-sum__cal-title">
          {y}年{m + 1}月
        </span>
        <button type="button" aria-label="下个月" onClick={() => shift(1)}>
          ›
        </button>
        <button type="button" className="today-sum__cal-today" onClick={gotoToday}>
          今
        </button>
      </div>
      <div className="today-sum__cal-week">
        {weekLabels.map((w) => (
          <span key={w}>{w}</span>
        ))}
      </div>
      <div className="today-sum__cal-grid">
        {cells.map((d, i) =>
          d === null ? (
            <span key={i} className="today-sum__cal-cell is-void" />
          ) : (
            <button
              key={i}
              type="button"
              className={`today-sum__cal-cell${d === selected ? " is-selected" : ""}${d === todayStr ? " is-today" : ""}`}
              data-testid={`cal-day-${d}`}
              onClick={() => onSelect(d)}
            >
              {Number(d.slice(8, 10))}
              {(dots?.[d]?.length ?? 0) > 0 ? (
                <span className="today-sum__cal-dots" aria-hidden="true">
                  {DOT_KIND.map((k) => (
                    <i
                      key={k}
                      className={`today-sum__cal-dot today-sum__cal-dot--${k}${dots?.[d]?.includes(k) ? " is-on" : ""}`}
                    />
                  ))}
                </span>
              ) : null}
            </button>
          ),
        )}
      </div>
    </div>
  );
}

const PEEK_LABELS: Record<string, string> = Object.fromEntries(
  PROV_SOURCES.map((p) => [p.id, p.label]),
);

/** 选中日聚合：day-peek 四源（历史/未来日；软失败空态）。 */
function DayPeekPanel({ date }: { date: string }) {
  const openWindow = useDesktopStore((s) => s.openWindow);
  const { data, isError, isLoading, error } = useQuery({
    queryKey: ["day-peek", date],
    queryFn: async (): Promise<DayPeekResp> =>
      api.get<DayPeekResp>(`/api/v1/dashboard/day-peek?date=${date}`),
    retry: 1,
    refetchOnWindowFocus: false,
  });

  if (isLoading) return <div className="today-sum__empty">…</div>;
  if (isError)
    return (
      <div className="today-sum__empty" data-testid={error instanceof ApiError && error.status === 401 ? "day-peek-auth" : "day-peek-err"}>
        {error instanceof ApiError && error.status === 401 ? "未登录 · 请重新登录" : "暂时不可用"}
      </div>
    );

  return (
    <div className="today-sum__peek" data-testid="day-peek-panel">
      {Object.keys(PEEK_LABELS).map((k) => {
        const items = data?.list?.[k] ?? [];
        return (
          <section key={k} className="today-sum__sec" data-testid={`day-peek-${k}`}>
            <button type="button" className="today-sum__head" onClick={() => openWindow(k)}>
              <span>{PEEK_LABELS[k]}</span>
              {items.length > 0 ? <span className="today-sum__count">{items.length}</span> : null}
            </button>
            {items.length === 0 ? (
              <div className="today-sum__empty">当日暂无{PEEK_LABELS[k]}</div>
            ) : (
              <ul className="today-sum__list">
                {items.map((it, i) => (
                  <li key={i} className="today-sum__item">
                    <span className="today-sum__dot" aria-hidden="true" />
                    <span className="today-sum__txt">
                      {it.title ?? it.text ?? ""}
                      {it.start ? ` · ${String(it.start).slice(11, 16)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

/** U2 视图侧 · 时间 + 今日摘要面板（Popover）。 */
export function TimeWidget() {
  const clock = useClock();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const [selectedDate, setSelectedDate] = useState<string>(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  });
  const todayStr = (() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  })();
  const isToday = selectedDate === todayStr;

  // 点击外部 / Esc 关闭（判据 J1）
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="time-widget" ref={rootRef}>
      <button
        type="button"
        className="topbar__clock"
        aria-label="当前时间，点击展开日历"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {clock}
      </button>
      {open && (
        <div
          className="today-sum"
          data-testid="today-summary-panel"
          role="dialog"
          aria-label="日历与今日摘要"
        >
          <div className="today-sum__title">日历</div>
          <MonthGrid selected={selectedDate} onSelect={setSelectedDate} />
          <div className="today-sum__divider" />
          {isToday ? (
            <>
              <div className="today-sum__title">今日摘要</div>
              {PROV_SOURCES.map((p) => (
                <ProvSection key={p.id} id={p.id} moduleId={p.moduleId} label={p.label} />
              ))}
            </>
          ) : (
            <>
              <div className="today-sum__title">{selectedDate} 摘要</div>
              <DayPeekPanel date={selectedDate} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
