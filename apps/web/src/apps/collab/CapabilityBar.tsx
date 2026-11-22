import { useMemo, useState } from "react";

import { useDesktopStore } from "../../kernel/store";
import { buildCapabilities, filterCapabilities, quickCapabilities } from "./capabilities";

/**
 * ★ P0-c：主墙「能力」条 —— 一眼看得见的模块入口。
 *
 * 为什么不是再做一个下拉菜单：壳收掉之后模块入口只剩 Ctrl+K（键盘路径），
 * 而主人的抱怨是「不方便」—— 看得见的入口才是方便。所以这里是**常驻一行**：
 *   左：能力 N 项（真数，不是估计）
 *   中：前 8 个模块的快捷键（点了就开窗，同 Ctrl+K 的行为）
 *   右：「全部 N ▸」展开成全量网格 + 筛选框
 *
 * 展开是**行内展开（disclosure）不是弹层**：不抢焦点、不需要 z-index、
 * 不会被宿主的 `pointer-events` 白名单吃掉（桌面背景面是白名单制，新弹层漏登记就整块鼠标穿透）。
 *
 * 零模块 → 零渲染（K6：不占位、不留空条）。
 */

/** 常驻可见的芯片数。8 个在 1366 宽下也不会换行。 */
const QUICK = 8;

export function CapabilityBar({ onOpen }: { onOpen: (id: string) => void }) {
  const modules = useDesktopStore((s) => s.modules);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");

  const entries = useMemo(() => buildCapabilities(modules), [modules]);
  const quick = useMemo(() => quickCapabilities(entries, QUICK), [entries]);
  const shown = open ? filterCapabilities(entries, q) : quick;

  if (!entries.length) return null;

  const chip = (id: string, name: string, provides: string[]) => (
    <li key={id} className="collab-chip__item">
      <button
        type="button"
        className="collab-chip"
        onClick={() => onOpen(id)}
        title={provides.length ? `提供：${provides.join(" · ")}` : "该模块尚未声明能力"}
        aria-label={`打开${name}`}
        data-testid={`cap-open-${id}`}
      >
        <span className="collab-chip__name">{name}</span>
        {provides.length ? (
          <span className="collab-chip__badge" aria-hidden="true">
            {provides.length}
          </span>
        ) : null}
      </button>
    </li>
  );

  return (
    <div className="collab-caps" data-testid="collab-caps" role="region" aria-label="能力入口">
      <div className="collab-caps__row">
        <span className="collab-caps__label">
          能力<span className="collab-caps__count">{entries.length}</span>
        </span>
        {open ? (
          <input
            className="collab-caps__filter"
            type="search"
            value={q}
            autoFocus
            placeholder="筛选：名字 / 能力名，如 日历、event.read"
            aria-label="筛选能力"
            data-testid="cap-filter"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              // Esc 收起（不再抢全局 Esc：宿主也有 Esc 语义，这里只在自己有焦点时处理）
              if (e.key === "Escape") {
                e.stopPropagation();
                setOpen(false);
                setQ("");
              }
            }}
          />
        ) : (
          <ul className="collab-caps__chips">{shown.map((e) => chip(e.id, e.name, e.provides))}</ul>
        )}
        <button
          type="button"
          className="collab-caps__more"
          onClick={() => {
            setOpen((v) => !v);
            if (open) setQ("");
          }}
          aria-expanded={open}
          data-testid="cap-all"
        >
          {open ? "收起" : `全部 ${entries.length}`}
        </button>
      </div>

      {open ? (
        <div className="collab-caps__grid" data-testid="cap-grid">
          {shown.length ? (
            <ul className="collab-caps__chips collab-caps__chips--wrap">
              {shown.map((e) => chip(e.id, e.name, e.provides))}
            </ul>
          ) : (
            <p className="collab-empty" data-testid="cap-none">
              没有匹配的模块 —— 换个词，或按 Esc 收起。
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}