/**
 * i山大 插件前端入口。
 *
 * 三块内容：
 *  · 公告：公开接口，本地缓存 + 同步；
 *  · 课表：把 iSDU 的真实课表画成「周一~周日 × 第 1~12 节」的网格，可一键导入 course；
 *  · 成绩：按学期分组，带学分/绩点/加权汇总。
 *
 * 登录：CASTGC 只在登录瞬间存在，正常路径是桌面壳/登录桥自动兑换；
 * 这里保留「手动粘贴」作为兜底（凭据只进本机后端内存）。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type CSSProperties } from "react";

import { api } from "@/shared/api/client";
import "./isdu.css";

const TABS = [
  "公告",
  "课表",
  "校历",
  "成绩",
  "考试",
  "志愿",
  "自习室",
  "校车",
  "图书馆",
  "一卡通",
  "电话",
] as const;
type Tab = (typeof TABS)[number];
const DAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const ALL_TERMS = "__all__";
const COLORS = [
  "#5b8ff9",
  "#61ddaa",
  "#f6bd16",
  "#e8684a",
  "#9270ca",
  "#6dc8ec",
  "#ff9d4d",
  "#5d7092",
];

interface Notice {
  notice_id: number;
  title: string;
  content: string;
  image: string;
  url: string;
  popup: number;
}

interface Session {
  logged_in: boolean;
  has_castgc: boolean;
}

interface CourseItem {
  name: string;
  teacher?: string | null;
  location?: string | null;
  weekday: number;
  start_section?: number | null;
  end_section?: number | null;
  weeks?: string | null;
  start_time?: string | null;
}

interface TimetableResp {
  courses: CourseItem[];
  report: { rows: number; mapped: number; unknown_keys: string[] };
}

interface GradeRow {
  name: string;
  credit: number;
  grade: string;
  gradePoint: number;
  gradeRank: string;
  type: string;
  yearTerm: string;
}

type GradesResp = Record<string, GradeRow[]>;

/** 只放行 http(s) 外链：上游数据里的 javascript: / data: 一律不渲染成可点链接。 */
function safeExternalUrl(raw?: string | null): string | undefined {
  if (!raw) return undefined;
  try {
    const url = new URL(raw, window.location.origin);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : undefined;
  } catch {
    return undefined;
  }
}

function errText(e: unknown): string {
  const err = e as { detail?: string; message?: string };
  return err?.detail || err?.message || String(e);
}

function colorOf(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return COLORS[hash % COLORS.length];
}

/** "1-8,10-16" → [1..8,10..16]；空 = 每周 */
function parseWeeks(weeks?: string | null): number[] | null {
  if (!weeks || !weeks.trim()) return null;
  const out: number[] = [];
  weeks.split(",").forEach((part) => {
    const seg = part.trim();
    if (!seg) return;
    const m = /^(\d+)\s*-\s*(\d+)$/.exec(seg);
    if (m) {
      for (let w = Number(m[1]); w <= Number(m[2]); w += 1) out.push(w);
      return;
    }
    if (/^\d+$/.test(seg)) out.push(Number(seg));
  });
  return out.length ? out : null;
}

function inWeek(course: CourseItem, week: number): boolean {
  const weeks = parseWeeks(course.weeks);
  return weeks === null || weeks.includes(week);
}

/* ── 公告 + 登录 ─────────────────────────────────────────────────────── */

function NoticeList({ items }: { items: Notice[] }) {
  if (items.length === 0) {
    return <div className="isdu__muted">还没有公告。点「同步」从 i山大 拉一次。</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {items.map((n) => (
        <div key={n.notice_id} style={{ borderBottom: "1px solid var(--line)", paddingBottom: 8 }}>
          <div style={{ fontWeight: 600, marginBottom: 2 }}>
            {n.title || `公告 #${n.notice_id}`}
          </div>
          <div className="isdu__muted" style={{ whiteSpace: "pre-wrap" }}>
            {n.content}
          </div>
          {safeExternalUrl(n.image) ? (
            <a
              className="isdu__muted"
              href={safeExternalUrl(n.image)}
              target="_blank"
              rel="noreferrer"
            >
              查看配图
            </a>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function LoginPanel({ session, onDone }: { session?: Session; onDone: () => void }) {
  const [castgc, setCastgc] = useState("");
  const [open, setOpen] = useState(false);
  const desktop = typeof window !== "undefined" ? window.alongsideDesktop : undefined;

  const desktopLogin = useMutation({
    mutationFn: async () => {
      if (!desktop) throw new Error("当前不在桌面外壳里");
      const r = await desktop.isduLogin();
      if (!r.ok) throw new Error(r.error || "登录失败");
      return r;
    },
    onSuccess: () => onDone(),
  });

  const login = useMutation({
    mutationFn: () =>
      api.post<{ logged_in: boolean; token_preview: string }>("/api/v1/isdu/login/castgc", {
        castgc,
      }),
    onSuccess: () => {
      setCastgc("");
      onDone();
    },
  });
  const logout = useMutation({
    mutationFn: () => api.delete<{ logged_in: boolean }>("/api/v1/isdu/session"),
    onSuccess: () => onDone(),
  });

  return (
    <div className="isdu__card">
      <div className="isdu__row" style={{ justifyContent: "space-between" }}>
        <div>
          <b>登录态</b>
          <div className="isdu__muted">
            {session?.logged_in ? "已登录（token 只在后端进程内存里）" : "未登录"}
          </div>
        </div>
        <div className="isdu__row">
          {desktop ? (
            <button
              type="button"
              className="isdu__btn isdu__btn--primary"
              disabled={desktopLogin.isPending}
              onClick={() => desktopLogin.mutate()}
            >
              {desktopLogin.isPending ? "登录窗已打开…" : "一键登录 i山大"}
            </button>
          ) : null}
          {session?.logged_in ? (
            <button type="button" className="isdu__btn" onClick={() => logout.mutate()}>
              退出
            </button>
          ) : (
            <button type="button" className="isdu__btn" onClick={() => setOpen((v) => !v)}>
              {open ? "收起手动登录" : "手动粘贴 CASTGC"}
            </button>
          )}
        </div>
      </div>

      {desktopLogin.isError ? (
        <div className="isdu__error" style={{ marginTop: 8 }}>
          {errText(desktopLogin.error)}
        </div>
      ) : null}

      {open && !session?.logged_in ? (
        <div style={{ marginTop: 10 }}>
          <div className="isdu__muted">
            CASTGC
            只在登录瞬间存在，正常由桌面壳/登录桥自动兑换；这里仅作兜底。凭据只进本机后端内存。
          </div>
          <div className="isdu__row" style={{ marginTop: 8 }}>
            <input
              className="isdu__input"
              placeholder="CASTGC（TGT-…）"
              value={castgc}
              onChange={(e) => setCastgc(e.target.value)}
            />
            <button
              type="button"
              className="isdu__btn isdu__btn--primary"
              disabled={!castgc.trim() || login.isPending}
              onClick={() => login.mutate()}
            >
              {login.isPending ? "兑换中…" : "兑换登录"}
            </button>
          </div>
          {login.isError ? <div className="isdu__error">{errText(login.error)}</div> : null}
        </div>
      ) : null}
    </div>
  );
}

function NoticesPanel({
  session,
  onSessionChange,
}: {
  session?: Session;
  onSessionChange: () => void;
}) {
  const qc = useQueryClient();
  const notices = useQuery({
    queryKey: ["isdu", "notices"],
    queryFn: () => api.get<Notice[]>("/api/v1/isdu/notices?limit=30"),
  });
  const sync = useMutation({
    mutationFn: () => api.post<{ synced: number }>("/api/v1/isdu/sync"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["isdu", "notices"] }),
  });

  return (
    <div>
      <LoginPanel session={session} onDone={onSessionChange} />
      <div className="isdu__card">
        <div className="isdu__row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
          <b>公告</b>
          <div className="isdu__row">
            {sync.isError ? <span className="isdu__error">{errText(sync.error)}</span> : null}
            <button
              type="button"
              className="isdu__btn"
              disabled={sync.isPending}
              onClick={() => sync.mutate()}
            >
              {sync.isPending ? "同步中…" : "同步"}
            </button>
          </div>
        </div>
        {notices.isLoading ? <div className="isdu__muted">加载中…</div> : null}
        {notices.isError ? <div className="isdu__error">{errText(notices.error)}</div> : null}
        {notices.data ? <NoticeList items={notices.data} /> : null}
      </div>
      <NewsSection enabled={Boolean(session?.logged_in)} />
    </div>
  );
}

/* ── 课表 ───────────────────────────────────────────────────────────── */

function TimetablePanel({ enabled }: { enabled: boolean }) {
  const qc = useQueryClient();
  const [week, setWeek] = useState<number | "all">("all");
  const [weekTouched, setWeekTouched] = useState(false);
  const q = useQuery({
    queryKey: ["isdu", "timetable"],
    queryFn: () => api.get<TimetableResp>("/api/v1/isdu/timetable"),
    enabled,
  });
  const calendar = useQuery({
    queryKey: ["isdu", "calendar"],
    queryFn: () => api.get<CalendarResp>("/api/v1/isdu/calendar"),
    enabled,
    staleTime: 3600_000,
  });
  const currentWeek = weekOfToday(calendar.data);
  useEffect(() => {
    if (!weekTouched && currentWeek) setWeek(currentWeek);
  }, [currentWeek, weekTouched]);
  const importCourses = useMutation({
    mutationFn: () =>
      api.post<{ plan: { create: number; update: number; skip: number } }>(
        "/api/v1/isdu/import/courses",
        { apply: true },
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["course"] }),
  });

  const courses = q.data?.courses ?? [];
  const maxSection = useMemo(
    () => Math.max(10, ...courses.map((c) => c.end_section ?? c.start_section ?? 0)),
    [courses],
  );
  const visible = useMemo(
    () => (week === "all" ? courses : courses.filter((c) => inWeek(c, week))),
    [courses, week],
  );

  if (!enabled) {
    return <div className="isdu__muted">先登录 i山大（到「公告」页登录一次）。</div>;
  }
  if (q.isLoading) return <div className="isdu__muted">正在拉课表…</div>;
  if (q.isError) return <div className="isdu__error">{errText(q.error)}</div>;

  return (
    <div>
      <div className="tt__toolbar">
        <b>课表</b>
        <span className="isdu__muted">
          {q.data?.report.mapped ?? 0} 个时段 · {week === "all" ? "全部周次" : `第 ${week} 周`}
        </span>
        <select
          className="isdu__input"
          style={{ flex: "0 0 110px", minWidth: 0 }}
          value={week === "all" ? "all" : String(week)}
          onChange={(e) => {
            setWeekTouched(true);
            setWeek(e.target.value === "all" ? "all" : Number(e.target.value));
          }}
          aria-label="选择周次"
        >
          <option value="all">全部周次</option>
          {Array.from({ length: 16 }, (_, i) => i + 1).map((w) => (
            <option key={w} value={w}>
              第 {w} 周
            </option>
          ))}
        </select>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          {importCourses.isError ? (
            <span className="isdu__error">{errText(importCourses.error)}</span>
          ) : null}
          {importCourses.data ? (
            <span className="isdu__muted">
              新增 {importCourses.data.plan.create} · 更新 {importCourses.data.plan.update}
            </span>
          ) : null}
          <button
            type="button"
            className="isdu__btn isdu__btn--primary"
            disabled={importCourses.isPending}
            onClick={() => importCourses.mutate()}
          >
            {importCourses.isPending ? "导入中…" : "导入到课程表"}
          </button>
        </div>
      </div>

      {visible.length === 0 ? (
        <div className="tt__empty">这一周没有课。</div>
      ) : (
        <div
          className="tt__grid"
          style={{
            gridTemplateColumns: "30px repeat(7, minmax(0, 1fr))",
            gridTemplateRows: `auto repeat(${maxSection}, 56px)`,
          }}
        >
          <div className="tt__corner" style={{ gridColumn: 1, gridRow: 1 }} />
          {DAYS.map((d, i) => (
            <div key={d} className="tt__head" style={{ gridColumn: i + 2, gridRow: 1 }}>
              {d}
            </div>
          ))}
          {Array.from({ length: maxSection }, (_, s) => (
            <div key={`p${s}`} className="tt__period" style={{ gridColumn: 1, gridRow: s + 2 }}>
              <span>{s + 1}</span>
            </div>
          ))}
          {Array.from({ length: maxSection }, (_, s) =>
            DAYS.map((d, di) => (
              <div
                key={`c${s}-${d}`}
                className="tt__slot"
                style={{ gridColumn: di + 2, gridRow: s + 2 }}
              />
            )),
          )}
          {visible.map((c, idx) => {
            const start = Math.max(1, c.start_section ?? 1);
            const end = Math.max(start, c.end_section ?? start);
            const style = {
              gridColumn: c.weekday + 2,
              gridRow: `${start + 1} / ${end + 2}`,
              "--block-color": colorOf(c.name),
            } as CSSProperties;
            return (
              <div
                className="tt__block"
                key={`${c.name}-${c.weekday}-${start}-${idx}`}
                style={style}
                title={`${c.name} ${c.weeks ?? ""}`}
              >
                <b>{c.name}</b>
                {c.location ? <span>{c.location}</span> : null}
                {c.teacher ? <span>{c.teacher}</span> : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ── 成绩 ───────────────────────────────────────────────────────────── */

function GradesPanel({ enabled }: { enabled: boolean }) {
  const q = useQuery({
    queryKey: ["isdu", "grades"],
    queryFn: () => api.get<GradesResp>("/api/v1/isdu/grades"),
    enabled,
  });
  const terms = useMemo(
    () =>
      Object.keys(q.data ?? {})
        .sort()
        .reverse(),
    [q.data],
  );
  const [term, setTerm] = useState<string>("");
  /** 默认取「最新且至少有 3 门」的学期：最新学期常常只有一门暑期课，看着像“没成绩”。 */
  const defaultTerm = useMemo(() => {
    const withEnough = terms.find((t) => (q.data?.[t] ?? []).length >= 3);
    return withEnough ?? terms[0] ?? "";
  }, [terms, q.data]);
  const active = term || defaultTerm;
  const rows = useMemo(
    () =>
      active === ALL_TERMS ? terms.flatMap((t) => q.data?.[t] ?? []) : (q.data?.[active] ?? []),
    [active, q.data, terms],
  );

  const summary = useMemo(() => {
    let credits = 0;
    let weightedScore = 0;
    let weightedPoint = 0;
    let scored = 0;
    rows.forEach((r) => {
      const credit = Number(r.credit) || 0;
      const score = Number.parseFloat(r.grade);
      credits += credit;
      if (Number.isFinite(score)) {
        weightedScore += score * credit;
        scored += credit;
      }
      if (Number.isFinite(Number(r.gradePoint))) weightedPoint += Number(r.gradePoint) * credit;
    });
    return {
      credits,
      avg: scored ? weightedScore / scored : 0,
      gpa: credits ? weightedPoint / credits : 0,
      count: rows.length,
    };
  }, [rows]);

  if (!enabled) return <div className="isdu__muted">先登录 i山大（到「公告」页登录一次）。</div>;
  if (q.isLoading) return <div className="isdu__muted">正在拉成绩…</div>;
  if (q.isError) return <div className="isdu__error">{errText(q.error)}</div>;

  return (
    <div>
      <div className="gr__terms">
        <button
          type="button"
          className={`gr__term${active === ALL_TERMS ? " gr__term--active" : ""}`}
          onClick={() => setTerm(ALL_TERMS)}
        >
          全部学期
          <span className="gr__term-count">
            {" "}
            · {terms.reduce((n, t) => n + (q.data?.[t] ?? []).length, 0)}门
          </span>
        </button>
        {terms.map((termKey) => (
          <button
            key={termKey}
            type="button"
            className={`gr__term${termKey === active ? " gr__term--active" : ""}`}
            onClick={() => setTerm(termKey)}
          >
            {termKey}
            <span className="gr__term-count"> · {(q.data?.[termKey] ?? []).length}门</span>
          </button>
        ))}
      </div>

      <div className="gr__summary">
        <div className="gr__stat">
          <span>加权平均分</span>
          <b>{summary.avg ? summary.avg.toFixed(1) : "—"}</b>
        </div>
        <div className="gr__stat">
          <span>加权绩点</span>
          <b>{summary.gpa ? summary.gpa.toFixed(2) : "—"}</b>
        </div>
        <div className="gr__stat">
          <span>学分 / 门数</span>
          <b>
            {summary.credits.toFixed(1)} <span style={{ fontSize: 13 }}>/ {summary.count}</span>
          </b>
        </div>
      </div>

      <div className="gr__table-wrap">
        <table className="gr__table">
          <thead>
            <tr>
              <th>课程</th>
              <th>类型</th>
              <th>学分</th>
              <th>成绩</th>
              <th>绩点</th>
              <th>等级</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.name}-${i}`}>
                <td>{r.name}</td>
                <td className="gr__rank">{r.type}</td>
                <td>{Number(r.credit).toFixed(1)}</td>
                <td
                  className={`gr__score${Number.parseFloat(r.grade) >= 90 ? " gr__score--high" : Number.parseFloat(r.grade) >= 80 ? " gr__score--mid" : ""}`}
                >
                  {r.grade}
                </td>
                <td className="gr__score">
                  {Number.isFinite(Number(r.gradePoint)) ? Number(r.gradePoint).toFixed(1) : "—"}
                </td>
                <td className="gr__rank">{r.gradeRank || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length === 0 ? <div className="tt__empty">这个学期没有成绩记录。</div> : null}
    </div>
  );
}

/* ── 考试 / 志愿 / 自习室 / 一卡通 ─────────────────────────────────── */

function today(): string {
  const d = new Date();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function daysAgo(n: number): string {
  const d = new Date(Date.now() - n * 86400000);
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

interface CalendarEvent {
  day: number;
  name: string;
  week?: number | null;
  holiday?: boolean;
}

interface CalendarMonth {
  month: number;
  year: number | null;
  events: CalendarEvent[];
}

interface CalendarResp {
  start_date?: string | null;
  weeks: number;
  comments: string[];
  months: CalendarMonth[];
}

/** 今天落在教学第几周（超出范围返回 null）。 */
function weekOfToday(cal?: CalendarResp): number | null {
  if (!cal?.start_date) return null;
  const start = new Date(`${cal.start_date}T00:00:00`);
  if (Number.isNaN(start.getTime())) return null;
  const days = Math.floor((Date.now() - start.getTime()) / 86400000);
  const week = Math.floor(days / 7) + 1;
  return week >= 1 && week <= Math.max(cal.weeks || 0, 1) ? week : null;
}

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

function CalendarPanel({ enabled }: { enabled: boolean }) {
  const q = useQuery({
    queryKey: ["isdu", "calendar"],
    queryFn: () => api.get<CalendarResp>("/api/v1/isdu/calendar"),
    enabled,
    staleTime: 3600_000,
  });
  if (!enabled) return <div className="isdu__muted">先登录 i山大（到「公告」页登录一次）。</div>;
  if (q.isLoading) return <div className="isdu__muted">正在拉校历…</div>;
  if (q.isError) return <div className="isdu__error">{errText(q.error)}</div>;

  const cal = q.data;
  if (!cal) return null;
  const currentWeek = weekOfToday(cal);
  const today = new Date();
  return (
    <div>
      <div className="tt__toolbar">
        <b>校历</b>
        {cal.start_date ? <span className="isdu__muted">开学 {cal.start_date}</span> : null}
        {currentWeek ? (
          <span className="cal__badge">现在是第 {currentWeek} 周</span>
        ) : (
          <span className="isdu__muted">不在教学周内</span>
        )}
        <span className="isdu__muted">共 {cal.weeks} 周</span>
      </div>

      {cal.comments.length > 0 ? (
        <div className="isdu__card" style={{ marginTop: 10 }}>
          {cal.comments.map((c, i) => (
            <div key={i} className="isdu__muted">
              {c}
            </div>
          ))}
        </div>
      ) : null}

      <div className="cal__months">
        {cal.months.map((m) => {
          const isCurrent = m.year === today.getFullYear() && m.month === today.getMonth() + 1;
          return (
            <div
              key={`${m.year}-${m.month}`}
              className={`cal__month${isCurrent ? " cal__month--now" : ""}`}
            >
              <div className="cal__month-title">
                {m.year ?? ""} 年 {m.month} 月
                {isCurrent ? <span className="cal__now-tag">本月</span> : null}
              </div>
              {m.events.length === 0 ? (
                <div className="isdu__muted">（无记录）</div>
              ) : (
                m.events.map((ev, i) => {
                  const weekday =
                    m.year !== null ? WEEKDAYS[new Date(m.year, m.month - 1, ev.day).getDay()] : "";
                  const isNowWeek = currentWeek !== null && ev.week === currentWeek;
                  return (
                    <div key={i} className={`cal__event${isNowWeek ? " cal__event--now" : ""}`}>
                      <span className="cal__day">{ev.day}日</span>
                      {weekday ? <span className="isdu__muted">周{weekday}</span> : null}
                      <span className="cal__name">{ev.name}</span>
                      {ev.holiday ? <span className="cal__tag">假期</span> : null}
                      {typeof ev.week === "number" && ev.week > 0 ? (
                        <span className="isdu__muted">第{ev.week}周</span>
                      ) : null}
                    </div>
                  );
                })
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ExamPanel({ enabled }: { enabled: boolean }) {
  const q = useQuery({
    queryKey: ["isdu", "exams"],
    queryFn: () => api.get<Record<string, unknown>[]>("/api/v1/isdu/exams"),
    enabled,
  });
  if (!enabled) return <div className="isdu__muted">先登录 i山大（到「公告」页登录一次）。</div>;
  if (q.isLoading) return <div className="isdu__muted">正在拉考试安排…</div>;
  if (q.isError) return <div className="isdu__error">{errText(q.error)}</div>;
  const rows = q.data ?? [];
  if (rows.length === 0) return <div className="tt__empty">暂时没有考试安排。</div>;
  const nameKey =
    ["courseName", "name", "course"].find((k) => rows[0][k] !== undefined) ?? "courseName";
  const timeKey =
    ["examTime", "time", "startTime", "date"].find((k) => rows[0][k] !== undefined) ?? "examTime";
  const placeKey =
    ["examLocation", "location", "place", "room"].find((k) => rows[0][k] !== undefined) ??
    "examLocation";
  return (
    <div className="gr__table-wrap">
      <table className="gr__table">
        <thead>
          <tr>
            <th>课程</th>
            <th>时间</th>
            <th>地点</th>
            <th>座位</th>
            <th>类型</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>{String(r[nameKey] ?? "—")}</td>
              <td>{String(r[timeKey] ?? "—")}</td>
              <td>{String(r[placeKey] ?? "—")}</td>
              <td>{String(r.seatNo ?? r.seat ?? "—")}</td>
              <td className="gr__rank">{String(r.examType ?? r.type ?? "—")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VolunteerPanel({ enabled }: { enabled: boolean }) {
  const q = useQuery({
    queryKey: ["isdu", "volunteer"],
    queryFn: () =>
      api.get<{ totalHours?: number; details?: Record<string, unknown>[] }>(
        "/api/v1/isdu/volunteer",
      ),
    enabled,
  });
  if (!enabled) return <div className="isdu__muted">先登录 i山大（到「公告」页登录一次）。</div>;
  if (q.isLoading) return <div className="isdu__muted">正在拉志愿信息…</div>;
  if (q.isError) return <div className="isdu__error">{errText(q.error)}</div>;
  const details = q.data?.details ?? [];
  return (
    <div>
      <div className="gr__summary">
        <div className="gr__stat">
          <span>志愿总时长</span>
          <b>{Number(q.data?.totalHours ?? 0).toFixed(1)} 小时</b>
        </div>
        <div className="gr__stat">
          <span>记录条数</span>
          <b>{details.length}</b>
        </div>
      </div>
      <div className="gr__table-wrap">
        <table className="gr__table">
          <thead>
            <tr>
              <th>活动</th>
              <th>级别</th>
              <th>类型</th>
              <th>时长</th>
            </tr>
          </thead>
          <tbody>
            {details.map((r, i) => (
              <tr key={i}>
                <td>{String(r.name ?? "—")}</td>
                <td className="gr__rank">{String(r.level ?? "—")}</td>
                <td className="gr__rank">{String(r.type ?? "—")}</td>
                <td className="gr__score">{Number(r.duration ?? 0).toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {details.length === 0 ? <div className="tt__empty">还没有志愿记录。</div> : null}
    </div>
  );
}

interface ClassroomBuilding {
  id: string;
  name: string;
}

function campusOfBuilding(id: string): string {
  if (id.startsWith("03")) return "青岛校区";
  if (id.startsWith("02")) return "威海校区";
  return "济南校区";
}

function StudyroomPanel({ enabled }: { enabled: boolean }) {
  const [campus, setCampus] = useState("");
  const [building, setBuilding] = useState("");
  const [date, setDate] = useState(today());

  const buildings = useQuery({
    queryKey: ["isdu", "classroom-buildings"],
    queryFn: () => api.get<ClassroomBuilding[]>("/api/v1/isdu/classrooms/buildings"),
    enabled,
  });
  const occupancy = useQuery({
    queryKey: ["isdu", "classroom-occupancy", building, date],
    queryFn: () =>
      api.get<{
        date: string;
        building_id: string;
        sections: number[];
        rooms: { name: string; occupied: number[] }[];
      }>(
        `/api/v1/isdu/classrooms/occupancy?date=${encodeURIComponent(date)}&building_id=${encodeURIComponent(
          building,
        )}`,
      ),
    enabled: enabled && Boolean(building) && Boolean(date),
  });

  if (!enabled) return <div className="isdu__muted">先登录 i山大（到「公告」页登录一次）。</div>;

  const groups = new Map<string, ClassroomBuilding[]>();
  (buildings.data ?? []).forEach((b) => {
    const key = campusOfBuilding(b.id);
    groups.set(key, [...(groups.get(key) ?? []), b]);
  });

  const sections = occupancy.data?.sections ?? [];
  const rooms = occupancy.data?.rooms ?? [];
  const busyCount = rooms.reduce((n, r) => n + r.occupied.length, 0);

  return (
    <div>
      <div className="tt__toolbar">
        <b>教室占用表</b>
        <select
          className="isdu__input"
          style={{ flex: "0 0 120px" }}
          value={campus}
          onChange={(e) => {
            setCampus(e.target.value);
            setBuilding("");
          }}
        >
          <option value="">选择校区</option>
          {[...groups.keys()].map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <select
          className="isdu__input"
          style={{ flex: "0 0 200px" }}
          value={building}
          onChange={(e) => setBuilding(e.target.value)}
          disabled={!campus}
        >
          <option value="">选择教学楼</option>
          {(groups.get(campus) ?? []).map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
        <input
          className="isdu__input"
          type="date"
          style={{ flex: "0 0 140px" }}
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
        {occupancy.data ? (
          <span className="isdu__muted">
            {rooms.length} 间教室 · {busyCount} 个有课时段
          </span>
        ) : null}
        <a
          className="isdu__btn"
          style={{ marginLeft: "auto" }}
          href="https://xxbdeploy.sdu.edu.cn/free/classroom"
          target="_blank"
          rel="noreferrer"
        >
          原版查询
        </a>
      </div>

      {buildings.isError ? <div className="isdu__error">{errText(buildings.error)}</div> : null}
      {occupancy.isError ? <div className="isdu__error">{errText(occupancy.error)}</div> : null}
      {!building ? (
        <div className="tt__empty">选一个教学楼，就能看到每间教室每个时段有没有课。</div>
      ) : null}
      {occupancy.isLoading ? (
        <div className="isdu__muted" style={{ padding: 12 }}>
          加载中…
        </div>
      ) : null}

      {occupancy.isSuccess && rooms.length > 0 ? (
        <div
          className="gr__table-wrap"
          style={{ maxHeight: "calc(100vh - 300px)", overflow: "auto" }}
        >
          <table className="cls__table">
            <thead>
              <tr>
                <th>教室</th>
                {sections.map((sec) => (
                  <th key={sec}>{sec}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rooms.map((room) => (
                <tr key={room.name}>
                  <td className="cls__room">{room.name}</td>
                  {sections.map((sec) => {
                    const busy = room.occupied.includes(sec);
                    return (
                      <td
                        key={sec}
                        className={busy ? "cls__cell cls__cell--busy" : "cls__cell"}
                        title={busy ? "有课" : "空闲"}
                      >
                        {busy ? "课" : ""}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {occupancy.isSuccess && rooms.length === 0 ? (
        <div className="tt__empty">这栋楼没有教室数据。</div>
      ) : null}
      <div className="isdu__muted" style={{ marginTop: 8 }}>
        表头 1~10 是节次；「课」= 该时段有课，空白 = 空闲。数据来自学校空闲教室系统，只读。
      </div>
    </div>
  );
}

function BusPanel({ enabled }: { enabled: boolean }) {
  const [restWeek, setRestWeek] = useState(0);
  const [campus, setCampus] = useState("");
  const q = useQuery({
    queryKey: ["isdu", "bus", restWeek],
    queryFn: () => api.get<Record<string, string>[]>(`/api/v1/isdu/bus?rest_week=${restWeek}`),
    enabled,
  });
  if (!enabled) return <div className="isdu__muted">先登录 i山大（到「公告」页登录一次）。</div>;
  if (q.isLoading) return <div className="isdu__muted">正在拉校车时刻表…</div>;
  if (q.isError) return <div className="isdu__error">{errText(q.error)}</div>;
  const rows = q.data ?? [];
  const campuses = [...new Set(rows.map((r) => r.CAMPUS_DORMITORY).filter(Boolean))];
  const visible = campus ? rows.filter((r) => r.CAMPUS_DORMITORY === campus) : rows;
  return (
    <div>
      <div className="tt__toolbar">
        <b>校车时刻表</b>
        <select
          className="isdu__input"
          style={{ flex: "0 0 120px" }}
          value={String(restWeek)}
          onChange={(e) => setRestWeek(Number(e.target.value))}
        >
          <option value="0">工作日</option>
          <option value="1">双休日</option>
        </select>
        <select
          className="isdu__input"
          style={{ flex: "0 0 140px" }}
          value={campus}
          onChange={(e) => setCampus(e.target.value)}
        >
          <option value="">全部校区</option>
          {campuses.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <span className="isdu__muted">{visible.length} 班次</span>
        <a
          className="isdu__btn"
          style={{ marginLeft: "auto" }}
          href="https://scenter.sdu.edu.cn/tp_fp/view?m=fp#from=hall&serveID=f77a3fa4-d7c5-4051-8a8d-352921b79dbf&act=fp/serveapply"
          target="_blank"
          rel="noreferrer"
        >
          校车预约申请
        </a>
      </div>
      <div
        className="gr__table-wrap"
        style={{ maxHeight: "calc(100vh - 260px)", overflowY: "auto" }}
      >
        <table className="gr__table bus__table">
          <thead>
            <tr>
              <th>校区宿舍</th>
              <th>始发站</th>
              <th>发车时间</th>
              <th>途经</th>
              <th>终点站</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r, i) => (
              <tr key={i}>
                <td>{r.CAMPUS_DORMITORY ?? "—"}</td>
                <td>{r.DEPARTURE_STATION ?? "—"}</td>
                <td className="gr__score">{r.DEPARTURE_TIME ?? "—"}</td>
                <td className="gr__rank">{r.VIA_SITE ?? "—"}</td>
                <td>{r.TERMINUS ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface LibraryLevel {
  id: number;
  name: string;
  freeSeatCount?: number;
  totalSeatCount?: number;
  children?: { id: number; name: string; freeSeatCount?: number; totalSeatCount?: number }[] | null;
}

interface Seat {
  seatId: number;
  seatName: string;
  status: number;
  statusName: string;
}

function LibraryPanel({ enabled }: { enabled: boolean }) {
  const [library, setLibrary] = useState("");
  const [areaId, setAreaId] = useState<number | null>(null);

  const libraries = useQuery({
    queryKey: ["isdu", "libraries"],
    queryFn: () => api.get<{ libraries: string[] }>("/api/v1/isdu/library/libraries"),
    enabled,
  });
  // 默认选中上游返回的第一个图书馆（不写死任何校区，避免把个人校区偏好带进源码）
  useEffect(() => {
    if (!library && libraries.data?.libraries?.length) {
      setLibrary(libraries.data.libraries[0]);
    }
  }, [library, libraries.data]);
  const rooms = useQuery({
    queryKey: ["isdu", "library-rooms", library],
    queryFn: () =>
      api.get<LibraryLevel[]>(`/api/v1/isdu/library/rooms?library=${encodeURIComponent(library)}`),
    enabled: enabled && Boolean(library),
  });
  const seats = useQuery({
    queryKey: ["isdu", "library-seats", areaId],
    queryFn: () =>
      api.get<Seat[]>(
        `/api/v1/isdu/library/seat-map?room_id=${encodeURIComponent(String(areaId))}`,
      ),
    enabled: enabled && areaId !== null,
    refetchInterval: 30000,
  });
  const history = useQuery({
    queryKey: ["isdu", "library-history"],
    queryFn: () => api.get<Record<string, unknown>[]>("/api/v1/isdu/library/seats"),
    enabled,
  });

  if (!enabled) return <div className="isdu__muted">先登录 i山大（到「公告」页登录一次）。</div>;
  if (rooms.isError) return <div className="isdu__error">{errText(rooms.error)}</div>;

  const levels = rooms.data ?? [];
  const area =
    areaId === null
      ? null
      : (levels.flatMap((l) => l.children ?? []).find((a) => a.id === areaId) ?? null);
  const seatRows = seats.data ?? [];
  const freeCount = seatRows.filter((x) => x.status === 1).length;
  const historyRows = history.data ?? [];

  return (
    <div>
      <div className="tt__toolbar">
        <b>图书馆座位</b>
        <select
          className="isdu__input"
          style={{ flex: "0 0 170px" }}
          value={library}
          onChange={(e) => {
            setLibrary(e.target.value);
            setAreaId(null);
          }}
        >
          {(libraries.data?.libraries ?? []).map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        {seats.isFetching ? <span className="isdu__muted">刷新中…</span> : null}
        {seats.isSuccess && !seats.isFetching ? (
          <span className="isdu__muted">
            空闲 {freeCount} / {seatRows.length}
          </span>
        ) : null}
      </div>

      {rooms.isLoading ? (
        <div className="isdu__muted" style={{ padding: 10 }}>
          加载楼层…
        </div>
      ) : null}
      {rooms.isSuccess ? (
        <div className="lib__levels">
          {levels.map((level) => (
            <div key={level.id} className="lib__level">
              <div className="lib__level-title">
                {level.name}
                <span className="isdu__muted">
                  {level.freeSeatCount ?? 0} / {level.totalSeatCount ?? 0}
                </span>
              </div>
              <div className="lib__areas">
                {(level.children ?? []).map((child) => (
                  <button
                    key={child.id}
                    type="button"
                    className={`lib__area${areaId === child.id ? " lib__area--active" : ""}`}
                    onClick={() => setAreaId(child.id)}
                  >
                    <span>{child.name}</span>
                    <span className="lib__area-count">
                      {child.freeSeatCount ?? 0} / {child.totalSeatCount ?? 0}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {area && seats.isSuccess ? (
        <div style={{ marginTop: 12 }}>
          <div className="isdu__muted" style={{ marginBottom: 6 }}>
            {area.name} · 每 30 秒自动刷新
          </div>
          <div className="lib__seats">
            {seatRows.map((seat) => (
              <span
                key={seat.seatId}
                className={`lib__seat${seat.status === 1 ? " lib__seat--free" : seat.status === 7 ? " lib__seat--temp" : ""}`}
                title={`${seat.seatName} · ${seat.statusName}`}
              >
                {seat.seatName}
              </span>
            ))}
          </div>
          <div className="isdu__muted" style={{ marginTop: 6 }}>
            <span className="lib__legend lib__legend--free" /> 空闲
            <span className="lib__legend lib__legend--used" /> 使用中
            <span className="lib__legend lib__legend--temp" /> 临时离开
          </div>
        </div>
      ) : null}
      {seats.isError ? <div className="isdu__error">{errText(seats.error)}</div> : null}

      <div className="tt__toolbar" style={{ marginTop: 16 }}>
        <b>我的预约记录</b>
        <span className="isdu__muted">{historyRows.length} 条</span>
      </div>
      {historyRows.length === 0 ? (
        <div className="tt__empty">还没有座位预约记录。</div>
      ) : (
        <div className="gr__table-wrap">
          <table className="gr__table">
            <thead>
              <tr>
                <th>空间</th>
                <th>座位</th>
                <th>开始</th>
                <th>结束</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {historyRows.map((r, i) => (
                <tr key={i}>
                  <td>{String(r.spaceName ?? "—")}</td>
                  <td>{String(r.seatName ?? "—")}</td>
                  <td>{String(r.startTime ?? "—")}</td>
                  <td>{String(r.endTime ?? "—")}</td>
                  <td className="gr__rank">{String(r.status ?? "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <BookSection enabled={enabled} />
    </div>
  );
}

function LifePanel({ enabled }: { enabled: boolean }) {
  const [start, setStart] = useState(daysAgo(60));
  const [end, setEnd] = useState(today());
  const [areaId, setAreaId] = useState("");
  const [buildingCode, setBuildingCode] = useState("");
  const [room, setRoom] = useState("101");

  const bill = useQuery({
    queryKey: ["isdu", "card-bill", start, end],
    queryFn: () =>
      api.get<{ account?: string; lastBalance?: number; bill?: Record<string, unknown>[] }>(
        `/api/v1/isdu/card/bill?start_date=${encodeURIComponent(start)}&end_date=${encodeURIComponent(end)}`,
      ),
    enabled,
  });
  const areas = useQuery({
    queryKey: ["isdu", "elec-areas"],
    queryFn: () =>
      api.get<{ areaId: string; areaName: string }[]>("/api/v1/isdu/electricity/areas"),
    enabled,
  });
  const buildings = useQuery({
    queryKey: ["isdu", "elec-buildings", areaId],
    queryFn: () =>
      api.get<{ buildingCode: string; buildingName: string }[]>(
        `/api/v1/isdu/electricity/buildings?area_id=${encodeURIComponent(areaId)}`,
      ),
    enabled: enabled && Boolean(areaId),
  });
  const electricity = useMutation({
    mutationFn: () =>
      api.get<{
        roomName?: string;
        surplus?: number;
        recordTime?: string;
        fullMessage?: string;
      }>(
        `/api/v1/isdu/electricity?area_id=${encodeURIComponent(areaId)}&building_code=${encodeURIComponent(
          buildingCode,
        )}&room=${encodeURIComponent(room)}`,
      ),
  });

  if (!enabled) return <div className="isdu__muted">先登录 i山大（到「公告」页登录一次）。</div>;

  const rows = bill.data?.bill ?? [];
  return (
    <div>
      <div className="gr__summary">
        <div className="gr__stat">
          <span>校园卡余额</span>
          <b>
            {typeof bill.data?.lastBalance === "number"
              ? `¥ ${bill.data.lastBalance.toFixed(2)}`
              : "—"}
          </b>
        </div>
        <div className="gr__stat">
          <span>卡号</span>
          <b>{bill.data?.account ?? "—"}</b>
        </div>
        <div className="gr__stat">
          <span>流水条数</span>
          <b>{rows.length}</b>
        </div>
      </div>

      <div className="tt__toolbar">
        <b>账单</b>
        <input
          className="isdu__input"
          type="date"
          style={{ flex: "0 0 140px" }}
          value={start}
          onChange={(e) => setStart(e.target.value)}
        />
        <input
          className="isdu__input"
          type="date"
          style={{ flex: "0 0 140px" }}
          value={end}
          onChange={(e) => setEnd(e.target.value)}
        />
      </div>
      {bill.isError ? <div className="isdu__error">{errText(bill.error)}</div> : null}
      {rows.length === 0 ? (
        <div className="tt__empty">这段时间没有交易记录（余额已正常读取）。</div>
      ) : (
        <div className="gr__table-wrap">
          <table className="gr__table">
            <thead>
              <tr>
                <th>时间</th>
                <th>地点</th>
                <th>金额</th>
                <th>余额</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>{String(r.tradeTime ?? r.time ?? r.date ?? "—")}</td>
                  <td>{String(r.place ?? r.location ?? r.address ?? "—")}</td>
                  <td className="gr__score">{String(r.amount ?? r.money ?? r.cost ?? "—")}</td>
                  <td className="gr__rank">{String(r.balance ?? "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="tt__toolbar" style={{ marginTop: 14 }}>
        <b>宿舍电量</b>
        <select
          className="isdu__input"
          style={{ flex: "0 0 150px" }}
          value={areaId}
          onChange={(e) => {
            setAreaId(e.target.value);
            setBuildingCode("");
          }}
        >
          <option value="">选择校区</option>
          {(areas.data ?? []).map((a) => (
            <option key={a.areaId} value={a.areaId}>
              {a.areaName}
            </option>
          ))}
        </select>
        <select
          className="isdu__input"
          style={{ flex: "0 0 130px" }}
          value={buildingCode}
          onChange={(e) => setBuildingCode(e.target.value)}
          disabled={!areaId}
        >
          <option value="">选择楼栋</option>
          {(buildings.data ?? []).map((b) => (
            <option key={b.buildingCode} value={b.buildingCode}>
              {b.buildingName}
            </option>
          ))}
        </select>
        <input
          className="isdu__input"
          style={{ flex: "0 0 90px" }}
          value={room}
          onChange={(e) => setRoom(e.target.value)}
          placeholder="房间号"
        />
        <button
          type="button"
          className="isdu__btn isdu__btn--primary"
          disabled={!areaId || !buildingCode || !room || electricity.isPending}
          onClick={() => electricity.mutate()}
        >
          {electricity.isPending ? "查询中…" : "查电量"}
        </button>
      </div>
      {electricity.isError ? <div className="isdu__error">{errText(electricity.error)}</div> : null}
      {electricity.data ? (
        <div className="gr__summary" style={{ marginTop: 10 }}>
          <div className="gr__stat">
            <span>剩余电量</span>
            <b>
              {typeof electricity.data.surplus === "number"
                ? `${electricity.data.surplus.toFixed(2)} 度`
                : "—"}
            </b>
          </div>
          <div className="gr__stat">
            <span>宿舍</span>
            <b>{electricity.data.roomName ?? room}</b>
          </div>
          <div className="gr__stat">
            <span>更新时间</span>
            <b style={{ fontSize: 13 }}>{(electricity.data.recordTime ?? "").replace("T", " ")}</b>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* ── 校园资讯 / 馆藏检索 / 校内电话 ────────────────────────────────── */

interface NewsItem {
  id: string;
  site?: string;
  block?: string;
  title?: string;
  date?: string;
}

interface NewsDetail extends NewsItem {
  content?: string;
}

function htmlToText(html: string): string {
  if (!html) return "";
  try {
    const doc = new DOMParser().parseFromString(html, "text/html");
    return (doc.body.textContent || "").replace(/\s+/g, " ").trim();
  } catch {
    return html;
  }
}

function NewsSection({ enabled }: { enabled: boolean }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ["isdu", "news"],
    queryFn: () => api.get<NewsItem[]>("/api/v1/isdu/news/list"),
    enabled,
  });
  const detail = useQuery({
    queryKey: ["isdu", "news-detail", openId],
    queryFn: () =>
      api.get<NewsDetail>(`/api/v1/isdu/news?news_id=${encodeURIComponent(openId ?? "")}`),
    enabled: enabled && Boolean(openId),
  });

  if (!enabled) return null;
  if (openId) {
    const d = detail.data;
    return (
      <div className="isdu__card">
        <div className="isdu__row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
          <b>{d?.title ?? "加载中…"}</b>
          <button type="button" className="isdu__btn" onClick={() => setOpenId(null)}>
            返回列表
          </button>
        </div>
        <div className="isdu__muted">
          {d?.site ?? ""}
          {d?.date ? ` · ${d.date}` : ""}
        </div>
        <div style={{ marginTop: 8, fontSize: 13, lineHeight: 1.8, whiteSpace: "pre-wrap" }}>
          {htmlToText(d?.content ?? "")}
        </div>
        {detail.isError ? <div className="isdu__error">{errText(detail.error)}</div> : null}
      </div>
    );
  }
  return (
    <div className="isdu__card">
      <div className="isdu__row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
        <b>校园资讯</b>
        <span className="isdu__muted">{(list.data ?? []).length} 条</span>
      </div>
      {list.isLoading ? <div className="isdu__muted">加载中…</div> : null}
      {list.isError ? <div className="isdu__error">{errText(list.error)}</div> : null}
      {(list.data ?? []).map((n) => (
        <div
          key={n.id}
          className="news__item"
          role="button"
          tabIndex={0}
          onClick={() => setOpenId(n.id)}
          onKeyDown={(e) => {
            if (e.key === "Enter") setOpenId(n.id);
          }}
        >
          <span className="news__title">{n.title}</span>
          <span className="isdu__muted">
            {n.site ?? ""}
            {n.date ? ` · ${n.date}` : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

interface BookSummary {
  bookId: string | number;
  title?: string;
  author?: string;
  publisher?: string;
  publishYear?: string;
}

interface BookInfo extends BookSummary {
  isbn?: string;
  digest?: string;
  freeCount?: number;
  collectionCount?: number;
  collectionBooks?: { callNo?: string; location?: string; status?: string; barcode?: string }[];
}

function BookSection({ enabled }: { enabled: boolean }) {
  const [key, setKey] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [bookId, setBookId] = useState<string | number | null>(null);

  const results = useQuery({
    queryKey: ["isdu", "book-search", submitted],
    queryFn: () =>
      api.get<BookSummary[]>(
        `/api/v1/isdu/library/book-search?key=${encodeURIComponent(submitted)}`,
      ),
    enabled: enabled && Boolean(submitted),
  });
  const book = useQuery({
    queryKey: ["isdu", "book", bookId],
    queryFn: () =>
      api.get<BookInfo>(`/api/v1/isdu/library/book?book_id=${encodeURIComponent(String(bookId))}`),
    enabled: enabled && bookId !== null,
  });

  if (!enabled) return null;
  const info = book.data;
  return (
    <div style={{ marginTop: 18 }}>
      <div className="tt__toolbar">
        <b>馆藏检索</b>
        <input
          className="isdu__input"
          placeholder="书名 / 作者 / 主题，如 计算机"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && key.trim()) setSubmitted(key.trim());
          }}
        />
        <button
          type="button"
          className="isdu__btn isdu__btn--primary"
          disabled={!key.trim()}
          onClick={() => setSubmitted(key.trim())}
        >
          搜索
        </button>
        {bookId !== null ? (
          <button type="button" className="isdu__btn" onClick={() => setBookId(null)}>
            关闭详情
          </button>
        ) : null}
      </div>
      {results.isError ? <div className="isdu__error">{errText(results.error)}</div> : null}

      {bookId !== null ? (
        <div className="isdu__card" style={{ marginTop: 10 }}>
          {book.isLoading ? <div className="isdu__muted">加载中…</div> : null}
          {info ? (
            <>
              <div style={{ fontWeight: 600 }}>{info.title}</div>
              <div className="isdu__muted">
                {info.author} · {info.publisher} {info.publishYear}
                {info.isbn ? ` · ISBN ${info.isbn}` : ""}
              </div>
              <div className="isdu__muted" style={{ marginTop: 4 }}>
                馆藏 {info.collectionCount ?? "—"} 册，可借 {info.freeCount ?? "—"} 册
              </div>
              {info.digest ? <div style={{ marginTop: 8, fontSize: 13 }}>{info.digest}</div> : null}
              <div className="gr__table-wrap" style={{ marginTop: 10 }}>
                <table className="gr__table">
                  <thead>
                    <tr>
                      <th>索书号</th>
                      <th>馆藏地</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(info.collectionBooks ?? []).map((c, i) => (
                      <tr key={i}>
                        <td>{c.callNo ?? "—"}</td>
                        <td>{c.location ?? "—"}</td>
                        <td className="gr__rank">{c.status ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </div>
      ) : null}

      {bookId === null && results.isSuccess ? (
        <div style={{ marginTop: 8 }}>
          {(results.data ?? []).length === 0 ? (
            <div className="tt__empty">没有搜到相关书目。</div>
          ) : (
            (results.data ?? []).map((b) => (
              <div
                key={String(b.bookId)}
                className="news__item"
                role="button"
                tabIndex={0}
                onClick={() => setBookId(b.bookId)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") setBookId(b.bookId);
                }}
              >
                <span className="news__title">
                  {b.title}
                  <span className="isdu__muted"> · {b.author}</span>
                </span>
                <span className="isdu__muted">
                  {b.publisher} {b.publishYear}
                </span>
              </div>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}

interface PhoneBlockItem {
  name: string;
  param0: string;
  param1: string;
}

interface PhoneBlock {
  title: string;
  data: PhoneBlockItem[];
}

interface PhoneNumber {
  tel: string;
  title: string;
}

function PhonePanel({ enabled }: { enabled: boolean }) {
  const [blockIdx, setBlockIdx] = useState<number>(-1);
  const [item, setItem] = useState<PhoneBlockItem | null>(null);

  const blocks = useQuery({
    queryKey: ["isdu", "phone-blocks"],
    queryFn: () => api.get<PhoneBlock[]>("/api/v1/isdu/phone/blocks"),
    enabled,
  });
  const numbers = useQuery({
    queryKey: ["isdu", "phone", item?.param0, item?.param1],
    queryFn: () =>
      api.get<PhoneNumber[]>(
        `/api/v1/isdu/phone?param0=${encodeURIComponent(item?.param0 ?? "")}&param1=${encodeURIComponent(
          item?.param1 ?? "",
        )}`,
      ),
    enabled: enabled && Boolean(item),
  });

  if (!enabled) return <div className="isdu__muted">先登录 i山大（到「公告」页登录一次）。</div>;
  if (blocks.isError) return <div className="isdu__error">{errText(blocks.error)}</div>;

  const list = blocks.data ?? [];
  const current = blockIdx >= 0 ? list[blockIdx] : null;
  return (
    <div>
      <div className="tt__toolbar">
        <b>校内电话</b>
        <select
          className="isdu__input"
          style={{ flex: "0 0 220px" }}
          value={blockIdx >= 0 ? String(blockIdx) : ""}
          onChange={(e) => {
            setBlockIdx(Number(e.target.value));
            setItem(null);
          }}
        >
          <option value="">选择分组</option>
          {list.map((b, i) => (
            <option key={b.title} value={i}>
              {b.title}
            </option>
          ))}
        </select>
        {item ? (
          <button type="button" className="isdu__btn" onClick={() => setItem(null)}>
            返回部门列表
          </button>
        ) : null}
      </div>

      {!current ? <div className="tt__empty">选一个分组，再选部门查看电话。</div> : null}

      {current && !item ? (
        <div className="phone__grid">
          {current.data.map((it) => (
            <button
              key={it.param0 + it.param1}
              type="button"
              className="phone__item"
              onClick={() => setItem(it)}
            >
              {it.name}
            </button>
          ))}
        </div>
      ) : null}

      {item ? (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>{item.name}</div>
          {numbers.isLoading ? <div className="isdu__muted">加载中…</div> : null}
          {numbers.isError ? <div className="isdu__error">{errText(numbers.error)}</div> : null}
          {(numbers.data ?? []).map((n, i) => (
            <div key={i} className="phone__row">
              <span>{n.title}</span>
              <span className="phone__tel">
                {String(n.tel ?? "")
                  .split("\n")
                  .join(" / ")}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* ── 主组件 ─────────────────────────────────────────────────────────── */

export function IsduApp() {
  const [tab, setTab] = useState<Tab>("公告");
  const qc = useQueryClient();
  const session = useQuery({
    queryKey: ["isdu", "session"],
    queryFn: () => api.get<Session>("/api/v1/isdu/session"),
    refetchInterval: 5000,
  });
  const loggedIn = Boolean(session.data?.logged_in);

  return (
    <div className="isdu">
      <div className="isdu__tabs">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            className={`isdu__tab${tab === t ? " isdu__tab--active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
        <span
          className="isdu__muted"
          style={{ marginLeft: "auto", alignSelf: "center", paddingRight: 4 }}
        >
          {loggedIn ? "● 已登录" : "○ 未登录"}
        </span>
      </div>
      <div className="isdu__body">
        {tab === "公告" ? (
          <NoticesPanel
            session={session.data}
            onSessionChange={() => void qc.invalidateQueries({ queryKey: ["isdu", "session"] })}
          />
        ) : null}
        {tab === "课表" ? <TimetablePanel enabled={loggedIn} /> : null}
        {tab === "校历" ? <CalendarPanel enabled={loggedIn} /> : null}
        {tab === "成绩" ? <GradesPanel enabled={loggedIn} /> : null}
        {tab === "考试" ? <ExamPanel enabled={loggedIn} /> : null}
        {tab === "志愿" ? <VolunteerPanel enabled={loggedIn} /> : null}
        {tab === "自习室" ? <StudyroomPanel enabled={loggedIn} /> : null}
        {tab === "校车" ? <BusPanel enabled={loggedIn} /> : null}
        {tab === "图书馆" ? <LibraryPanel enabled={loggedIn} /> : null}
        {tab === "一卡通" ? <LifePanel enabled={loggedIn} /> : null}
        {tab === "电话" ? <PhonePanel enabled={loggedIn} /> : null}
      </div>
    </div>
  );
}

export function Component() {
  return <IsduApp />;
}

export default { manifestId: "isdu", Component, slots: {} };
