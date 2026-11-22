import { useEffect, useMemo, useState } from "react";

import { useSightNow } from "@/shared/api/now";

import { useDesktopStore } from "../../kernel/store";
import { addFav, loadFavs, moveFav, moveFavTo, removeFav, saveFavs, FAVS_MAX } from "./homeFavs";
import "./home.css";

/**
 * ★ 「我的今天」= 工作区 1（人的主屏）。
 *
 * 裁决来源：团队 `23-前端重排·两屏分工.md`（主人 2026-11-22 13:48）——
 *   「AI 的『它看见了/它想说/它做过的』可以单独切一个界面，锁定成一个单独的工作区。
 *    但这个始终应该是副屏，因为这个界面并没有多少有用的功能。」
 *
 * 所以本组件的主角是**人**：常用（一键开窗）+ 今天（下一件事 / 空档）+ 欠的 + 该注意的。
 * AI 的三块不在这里——它们在**第二个工作区**（原有 collab 原样搬过去，一个字没改）。
 *
 * 数据源：`/api/v1/sight/now`（P0 单一口径，与顶栏共用一份缓存与采样时刻）。
 *   `time.next_event` / `time.free_slots` / `attention[]` / `tensions[]` 正好是四块，**不需要任何后端改动**。
 *
 * 三条不许破的纪律：
 *   1. **不造假**：没数据就写没数据（未接线 / 读取失败 / 空），不写「今日无事」蒙人；
 *   2. **不装深度**：降级源如实收进一行折叠，不假装 finance/health/notes 已经接上；
 *   3. **首屏不许是空态**：默认预置 5 个常用（空框子等于没做），今天至少给出采样时刻。
 */

const WEEKDAY: Record<string, string> = {
  mon: "周一",
  tue: "周二",
  wed: "周三",
  thu: "周四",
  fri: "周五",
  sat: "周六",
  sun: "周日",
};

const PART: Record<string, string> = {
  early_morning: "清晨",
  morning: "上午",
  noon: "中午",
  afternoon: "下午",
  evening: "傍晚",
  late_night: "深夜",
  night: "夜里",
};

function hhmm(iso: string | undefined | null): string {
  if (!iso) return "";
  const m = /T(\d{2}:\d{2})/.exec(iso);
  return m ? m[1] : "";
}

function minutesText(min: number | undefined | null): string {
  if (typeof min !== "number" || !Number.isFinite(min)) return "";
  if (min <= 0) return "现在";
  if (min < 60) return `${min} 分钟后`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h} 小时 ${m} 分后` : `${h} 小时后`;
}

export interface HomeModule {
  id: string;
  name: string;
}

export function HomeMyToday() {
  const sight = useSightNow();
  const modules = useDesktopStore((s) => s.modules);
  const openWindow = useDesktopStore((s) => s.openWindow);
  const workspaces = useDesktopStore((s) => s.workspaces);
  const switchWorkspace = useDesktopStore((s) => s.switchWorkspace);
  const createWorkspace = useDesktopStore((s) => s.createWorkspace);

  const [favs, setFavs] = useState<string[]>(() => loadFavs());
  const [picking, setPicking] = useState(false);
  const [dragId, setDragId] = useState<string | null>(null);
  useEffect(() => saveFavs(favs), [favs]);

  /** 全部已启用模块（「我的常用」只能从里面挑）。顺序 = 注册顺序。 */
  const all: HomeModule[] = useMemo(() => {
    const out: HomeModule[] = [];
    for (const reg of Object.values(modules)) {
      if (!reg?.enabled) continue;
      const m = reg.manifest;
      if (!m?.id) continue;
      out.push({ id: m.id, name: m.name || m.id });
    }
    return out;
  }, [modules]);

  const nameOf = (id: string) => all.find((m) => m.id === id)?.name ?? id;
  /** 常用里可能留着已被停用/卸载的模块 id：不删数据，但从显示里剔除（点不开的按钮比没有更糟）。 */
  const shown = favs.filter((id) => all.some((m) => m.id === id));

  const head = sight.data?.head;
  const time = sight.data?.time;
  const next = time?.next_event;
  const slots = time?.free_slots ?? [];
  const attention = sight.data?.attention ?? [];
  const tensions = sight.data?.tensions ?? [];
  const degraded = sight.data?.degraded ?? [];

  const aiWs = workspaces[1];
  const goAi = () => {
    if (aiWs) switchWorkspace(aiWs.id);
    else createWorkspace();
  };

  const statusLine =
    sight.status === "ready"
      ? `${head?.weekday ? (WEEKDAY[head.weekday] ?? head.weekday) : ""}${
          head?.part_of_day ? ` · ${PART[head.part_of_day] ?? head.part_of_day}` : ""
        }${hhmm(head?.now_local) ? ` · ${hhmm(head?.now_local)} 采样` : ""}`
      : sight.status === "loading"
        ? "读取中…"
        : sight.status === "missing"
          ? "日程层未接线"
          : "读取失败";

  return (
    <section className="home" data-testid="home-root" aria-label="我的今天">
      <header className="home__head">
        <h1 className="home__title">今天</h1>
        <span className="home__when" data-testid="home-when">
          {statusLine}
        </span>
      </header>

      {/* ── ① 我的常用（主人说的那个小框子）────────────────────────── */}
      <nav className="home__favs" aria-label="我的常用" data-testid="home-favs">
        <span className="home__favs-label">常用</span>
        <ul className="home__fav-list">
          {shown.map((id, i) => (
            <li key={id} className="home__fav-item">
              <button
                type="button"
                className="home__fav"
                draggable
                onDragStart={() => setDragId(id)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => {
                  if (dragId) setFavs((list) => moveFavTo(list, dragId, id));
                  setDragId(null);
                }}
                onKeyDown={(e) => {
                  // 键盘等价物：Alt+←/→ 挪一格（不能只有鼠标能排序）
                  if (!e.altKey) return;
                  if (e.key === "ArrowLeft") {
                    e.preventDefault();
                    setFavs((list) => moveFav(list, id, -1));
                  } else if (e.key === "ArrowRight") {
                    e.preventDefault();
                    setFavs((list) => moveFav(list, id, 1));
                  }
                }}
                onClick={() => openWindow(id)}
                title={`打开${nameOf(id)}（Alt+←/→ 调整顺序）`}
                data-testid={`fav-${id}`}
                data-index={i}
              >
                <span className="home__fav-name">{nameOf(id)}</span>
                <span
                  role="button"
                  tabIndex={-1}
                  aria-label={`从常用移除${nameOf(id)}`}
                  className="home__fav-x"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    setFavs((list) => removeFav(list, id));
                  }}
                  data-testid={`fav-remove-${id}`}
                >
                  ×
                </span>
              </button>
            </li>
          ))}
          {shown.length === 0 ? (
            <li className="home__favs-empty">还没有常用 —— 点「＋」挑几个，一键就在这儿了</li>
          ) : null}
        </ul>
        <button
          type="button"
          className="home__fav-add"
          aria-expanded={picking}
          onClick={() => setPicking((v) => !v)}
          title={picking ? "收起模块清单" : "加常用"}
          data-testid="fav-add"
        >
          ＋
        </button>
        {picking ? (
          <div className="home__pick" data-testid="fav-picker" role="group" aria-label="选择常用模块">
            <p className="home__pick-hint">
              勾选即加入（最多 {FAVS_MAX} 个，现 {favs.length} 个）
            </p>
            <ul className="home__pick-list">
              {all.map((m) => {
                const on = favs.includes(m.id);
                const full = !on && favs.length >= FAVS_MAX;
                return (
                  <li key={m.id}>
                    <label className={`home__pick-item${full ? " is-disabled" : ""}`}>
                      <input
                        type="checkbox"
                        checked={on}
                        disabled={full}
                        onChange={() =>
                          setFavs((list) => (on ? removeFav(list, m.id) : addFav(list, m.id)))
                        }
                        data-testid={`pick-${m.id}`}
                      />
                      <span>{m.name}</span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}
      </nav>

      {/* ── ②③④ 今天 / 欠的 / 该注意的 ──────────────────────────── */}
      <div className="home__grid">
        <section className="home-card" aria-label="今天" data-testid="home-today">
          <h2 className="home-card__title">今天</h2>
          {sight.status === "loading" ? <p className="home-empty">读取中…</p> : null}
          {sight.status === "missing" ? (
            <p className="home-empty">日程层未接线（/sight/now 404）</p>
          ) : null}
          {sight.status === "error" ? (
            <p className="home-empty home-empty--warn">处境读取失败 —— 不是「今天没事」，是没读到</p>
          ) : null}
          {sight.status === "ready" ? (
            next ? (
              <div className="home-next">
                <span className="home-next__label">下一件</span>
                <span className="home-next__title" data-testid="home-next-title">
                  {next.title ?? "（未命名事项）"}
                </span>
                <span className="home-next__meta">
                  {hhmm(next.start_at) || "时间未知"}
                  {minutesText(next.minutes_until) ? ` · ${minutesText(next.minutes_until)}` : ""}
                  {next.location ? ` · ${next.location}` : ""}
                </span>
              </div>
            ) : (
              <p className="home-empty">今天后面没有排定的事</p>
            )
          ) : null}
          {sight.status === "ready" && slots.length ? (
            <ul className="home-slots" data-testid="home-slots">
              {slots.slice(0, 3).map((s, i) => (
                <li key={`${s.start}-${i}`} className="home-slots__row">
                  空档 {hhmm(s.start)}–{hhmm(s.end)}
                  <span className="home-slots__min">
                    {typeof s.minutes === "number" ? ` ${s.minutes} 分钟` : ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </section>

        <section className="home-card" aria-label="欠的" data-testid="home-attention">
          <h2 className="home-card__title">欠的</h2>
          {attention.length ? (
            <ul className="home-list">
              {attention.map((a, i) => (
                <li key={`${a.kind ?? "x"}-${i}`}>{a.title ?? a.kind}</li>
              ))}
            </ul>
          ) : sight.status === "loading" ? (
            <p className="home-empty">读取中…</p>
          ) : sight.status === "missing" ? (
            <p className="home-empty">暂未提供 —— 日程层还没接上</p>
          ) : sight.status === "error" ? (
            <p className="home-empty home-empty--warn">
              没读到 —— 接口异常，不是「没有欠的」
            </p>
          ) : (
            <p className="home-empty">没有欠的</p>
          )}
        </section>

        <section className="home-card" aria-label="该注意的" data-testid="home-conflicts">
          <h2 className="home-card__title">该注意的</h2>
          {tensions.length ? (
            <ul className="home-list home-list--warn">
              {tensions.map((t, i) => (
                <li key={t.id ?? `${t.kind ?? "t"}-${i}`}>
                  <span className="home-tension__sum">{t.summary ?? t.kind}</span>
                  {t.severity ? <span className="home-tension__sev">{t.severity}</span> : null}
                  {/* 服务端给了「建议怎么办」就上屏；没给不编 */}
                  {t.suggested_action ? (
                    <span className="home-tension__act">{t.suggested_action}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : sight.status === "loading" ? (
            <p className="home-empty">读取中…</p>
          ) : sight.status === "missing" ? (
            <p className="home-empty">暂未提供 —— 日程层还没接上</p>
          ) : sight.status === "error" ? (
            <p className="home-empty home-empty--warn">
              没读到 —— 接口异常，不是「没有冲突」
            </p>
          ) : (
            <p className="home-empty">没有冲突</p>
          )}
        </section>
      </div>

      <footer className="home__foot">
        {/* 降级源如实收进一行折叠：不假装这些源已经接上 */}
        {degraded.length ? (
          <details className="home__sources">
            <summary>{degraded.length} 个数据源未接入</summary>
            <p>
              {degraded
                .map((d) => `${d.source ?? "?"}（${d.reason ?? "?"}）`)
                .join(" · ")}
            </p>
          </details>
        ) : (
          <span />
        )}
        <button type="button" className="home__ai" onClick={goAi} data-testid="home-to-ai">
          AI 工作面 →
        </button>
      </footer>
    </section>
  );
}