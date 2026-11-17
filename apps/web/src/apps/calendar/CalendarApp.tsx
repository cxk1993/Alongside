/**
 * 日程表主应用（插件入口组件）。
 * 组合：工具栏（视图切换 / 缩放 / 回到现在 / 新建）+ 时间网格（日·周）或月历 + 右侧 Inspector
 * + 全局 Toast。
 *
 * 数据一律来自 useCalendarEvents（TanStack Query 拉取 + SSE 增量合并 + 乐观更新/回滚）。
 * 所有写请求经 api.ts 自带 Idempotency-Key，对接真实后端，不使用任何 mock。
 */

import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { EventCreate, EventPatch } from "./api";
import { useCalendarEvents } from "./hooks/useCalendarEvents";
import "./calendar.css";
import { Inspector } from "./inspector/Inspector";
import FullCalView from "./fullcal/FullCalView"; // V8 原型：FullCalendar 6.1.21 渲染层（自研网格保留可回退）
import { useCalendarUI, SCALE_PRESETS, useToast, type ViewMode } from "./state";
import { DAY_MS, formatSH, shMonday, shWallClock, shWallParts } from "./lib/time";

/** 测量元素宽度（用于让网格列宽自适应容器）。 */
function useElementWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setW(el.clientWidth);
    measure();
    // jsdom 等环境无 ResizeObserver：仅测量一次即可（不影响真实浏览器行为）。
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

const DEFAULT_COLOR = "var(--accent)";

/** 星期中文名（wd：0=周日…6=周六，与 time.ts 的 shWallParts 口径一致）。 */
const WEEKDAY_CN = ["日", "一", "二", "三", "四", "五", "六"];

/** 工具栏日期标题：随视图与 anchor 变化，让主人一眼知道当前在哪个时间窗。 */
function anchorLabel(view: ViewMode, anchor: Date): string {
  const p = shWallParts(anchor);
  if (view === "day") {
    return `${p.y}年${p.mo}月${p.d}日 周${WEEKDAY_CN[p.wd]}`;
  }
  if (view === "month") {
    return `${p.y}年${p.mo}月`;
  }
  const mon = shMonday(anchor);
  const mp = shWallParts(mon);
  const sun = new Date(mon.getTime() + 6 * DAY_MS);
  const sp = shWallParts(sun);
  if (mp.y === sp.y && mp.mo === sp.mo) {
    return `${mp.y}年${mp.mo}月${mp.d}日 – ${sp.d}日`;
  }
  return `${mp.y}年${mp.mo}月${mp.d}日 – ${sp.y}年${sp.mo}月${sp.d}日`;
}

export function CalendarApp() {
  const {
    view,
    hourHeight,
    selectedId,
    setView,
    setHourHeight,
    select,
    requestScrollToNow,
  } = useCalendarUI();
  const [anchor, setAnchor] = useState<Date>(() => new Date());
  const [areaRef] = useElementWidth<HTMLDivElement>();

  // 可见范围：随视图变化（一律以东八区墙钟为基准，from/to 带 +08:00）。
  const { range } = useMemo(() => {
    if (view === "day") {
      const p = shWallParts(anchor);
      const ws = shWallClock(p.y, p.mo, p.d, 0, 0, 0, 0);
      return {
        weekStart: ws,
        weekDays: 1,
        range: { from: formatSH(ws), to: formatSH(new Date(ws.getTime() + DAY_MS)) },
      };
    }
    if (view === "month") {
      const p = shWallParts(anchor);
      const first = shWallClock(p.y, p.mo, 1, 0, 0, 0, 0);
      const fp = shWallParts(first);
      const gridStart = new Date(first.getTime() - ((fp.wd + 6) % 7) * DAY_MS);
      return {
        weekStart: gridStart,
        weekDays: 7,
        range: {
          from: formatSH(gridStart),
          to: formatSH(new Date(gridStart.getTime() + 42 * DAY_MS)),
        },
      };
    }
    const monday = shMonday(anchor);
    return {
      weekStart: monday,
      weekDays: 7,
      range: {
        from: formatSH(monday),
        to: formatSH(new Date(monday.getTime() + 7 * DAY_MS)),
      },
    };
  }, [view, anchor]);

  const data = useCalendarEvents(range);
  const events = data.events;


  const selected = useMemo(
    () => events.find((e) => e.id === selectedId) ?? null,
    [events, selectedId],
  );
  const parentOfSelected = useMemo(() => {
    if (!selected?.parent_id) return null;
    return events.find((e) => e.id === selected.parent_id) ?? null;
  }, [selected, events]);

  // —— 各类写操作 → 对应 mutation ——
  const handleMove = useCallback(
    (id: string, start: string, end: string, spanDays: number) =>
      data.updateEvent.mutate({ id, patch: { start_at: start, end_at: end, span_days: spanDays } }),
    [data],
  );
  const handleResize = useCallback(
    (id: string, _mode: "bottom" | "right", start: string, end: string, spanDays: number) =>
      data.updateEvent.mutate({ id, patch: { start_at: start, end_at: end, span_days: spanDays } }),
    [data],
  );
  const handlePatch = useCallback(
    (id: string, patch: EventPatch) => data.updateEvent.mutate({ id, patch }),
    [data],
  );
  const handlePatchChild = useCallback(
    (parentId: string, childId: string, patch: EventPatch) =>
      data.updateChild.mutate({ parentId, childId, patch }),
    [data],
  );
  const handleCreate = useCallback(
    (startISO: string, endISO: string) => {
      const input: EventCreate = {
        title: "新事项",
        start_at: startISO,
        end_at: endISO,
        color: DEFAULT_COLOR,
      };
      data.createEvent.mutate(input);
    },
    [data],
  );
  const handleCreateAt = useCallback(
    (startISO: string) => {
      const s = new Date(startISO);
      const e = new Date(s.getTime() + 60 * 60 * 1000);
      data.createEvent.mutate({
        title: "新事项",
        start_at: s.toISOString(),
        end_at: e.toISOString(),
        color: DEFAULT_COLOR,
      });
    },
    [data],
  );
  const handleAddChild = useCallback(
    (parentId: string, input: { title: string; start_at: string; end_at: string }) =>
      data.addChild.mutate({ parentId, input: { ...input, color: DEFAULT_COLOR } }),
    [data],
  );
  const handleDelete = useCallback(
    (id: string) => {
      if (parentOfSelected && selected?.id === id) {
        data.deleteChild.mutate({ parentId: parentOfSelected.id, childId: id });
      } else {
        data.deleteEvent.mutate(id);
      }
      select(null);
    },
    [data, parentOfSelected, selected, select],
  );
  const handleDeleteChild = useCallback(
    (parentId: string, childId: string) => data.deleteChild.mutate({ parentId, childId }),
    [data],
  );

  const toasts = useToast((s) => s.toasts);

  return (
    <div className="cal-app">
      <div className="cal-toolbar">
        <div className="cal-toolbar-group">
          <button className={view === "day" ? "active" : ""} onClick={() => setView("day")}>
            日
          </button>
          <button className={view === "week" ? "active" : ""} onClick={() => setView("week")}>
            周
          </button>
          <button className={view === "month" ? "active" : ""} onClick={() => setView("month")}>
            月
          </button>
        </div>

        <div className="cal-toolbar-group cal-toolbar-title" data-testid="cal-title">
          {anchorLabel(view, anchor)}
        </div>

        <div className="cal-toolbar-group">
          <span className="cal-toolbar-label">缩放</span>
          {SCALE_PRESETS.map((p) => (
            <button
              key={p.hourHeight}
              className={hourHeight === p.hourHeight ? "active" : ""}
              onClick={() => setHourHeight(p.hourHeight)}
            >
              {p.label}
            </button>
          ))}
        </div>

        <div className="cal-toolbar-group">
          <button onClick={requestScrollToNow}>回到现在</button>
          <button
            onClick={() => {
              const s = new Date();
              s.setMinutes(0, 0, 0);
              const e = new Date(s.getTime() + 60 * 60 * 1000);
              handleCreate(s.toISOString(), e.toISOString());
            }}
          >
            + 新建
          </button>
          <button
            onClick={() =>
              setAnchor((a) =>
                view === "month"
                  ? new Date(a.getFullYear(), a.getMonth() - 1, 1)
                  : new Date(a.getTime() + (view === "week" ? -7 : -1) * DAY_MS),
              )
            }
          >
            ‹
          </button>
          <button
            onClick={() =>
              setAnchor((a) =>
                view === "month"
                  ? new Date(a.getFullYear(), a.getMonth() + 1, 1)
                  : new Date(a.getTime() + (view === "week" ? 7 : 1) * DAY_MS),
              )
            }
          >
            ›
          </button>
          <button onClick={() => setAnchor(new Date())}>今天</button>
        </div>

        <div className="cal-toolbar-group cal-toolbar-status">
          {data.isLoading ? <span>加载中…</span> : null}
          {data.error ? <span className="cal-err">加载失败</span> : null}
        </div>
      </div>

      <div className="cal-main">
        <div className="cal-grid-area" ref={areaRef}>
          <FullCalView
            events={events}
            view={view}
            anchor={anchor}
            onMove={handleMove}
            onResize={(id, start, end, spanDays) => handleResize(id, "right", start, end, spanDays)}
            onCreateAt={handleCreateAt}
            onSelect={select}
          />
        </div>

        <Inspector
          selected={selected}
          parentOfSelected={parentOfSelected}
          onPatch={handlePatch}
          onPatchChild={handlePatchChild}
          onAddChild={handleAddChild}
          onDelete={handleDelete}
          onDeleteChild={handleDeleteChild}
          onSelect={select}
        />
      </div>

      <div className="cal-toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`cal-toast ${t.type}`}>
            {t.msg}
          </div>
        ))}
      </div>
    </div>
  );
}
