/**
 * MultitabFrame · V6「一窗多页」容器原语（豆包预研 §2 形态的视图侧落地）。
 *
 * 需求来源：主人 ⑫（能力目录/mcp/推送/鉴权一窗标签分组）与 ⑧⑪（习惯/人格/健康
 * 并入成长罗盘多页）。一个原语三处复用：系统窗页组 / 成长罗盘 / 复盘笔记文档三合一。
 *
 * 行为契约（判据见 MultitabFrame.test.tsx，对应豆包预研 §3.3 四条）：
 * - 页签条水平展示全部一级页签；当前页高亮；
 * - 懒挂载：页面内容只在**首次被选中时**挂载（mountedKeys 集合）；
 * - keep-alive：已挂载的页切走不销毁（hidden 保挂载），切回状态不丢；
 * - 键盘可达：页签为 tab/button，aria-selected 标注当前页（对齐 U1-5 a11y 口径）；
 *   ★ 方向键 ←/→ 在页签间移动并切换（WAI-ARIA tabs 模式），Home/End 跳首尾；
 * - ★ persistKey（可选）：传入后记住最后访问页签（localStorage，模块私有键），
 *   下次打开直接落在原页——自定义化（主人反复点名的能力）。
 *
 * 二级分页：页 content 内再嵌一层 MultitabFrame 即天然支持（主人 ⑫「一级标签页
 * 内部展示二级标签页分页」），本组件不做特殊处理——组合优于配置。
 *
 * 本组件是纯视图原语：不读 manifest、不接路由、零契约面——
 * manifest `tabs` 声明（知默 schema 侧）定稿后由调用方把 entry 映射进 content 即可。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";

export interface MultitabPage {
  key: string;
  label: string;
  content: ReactNode;
}

interface Props {
  pages: MultitabPage[];
  /** 初始选中的页 key（默认取第一页；persistKey 存在时被持久化值覆盖）。 */
  initialKey?: string;
  /** 传入则在 localStorage 记住最后访问页签（键：`alongside.plugin.mtab.<persistKey>`）。 */
  persistKey?: string;
  ariaLabel?: string;
}

export const MTAB_PERSIST_PREFIX = "alongside.plugin.mtab.";

/**
 * 外部深链接通道（纯加法，既有 8 处调用零影响）。
 *
 * 为什么需要：入口层（主墙区块）要能「点一条子入口 → 窗里直接落在那一页」。
 * 但 `persistKey` 只在**挂载时**读一次，同文档 `localStorage.setItem` 又不会触发
 * storage 事件 —— 窗已经开着时改持久化值没有任何反应。所以补一条自定义事件：
 *   ① 窗还没开 → 先写持久化，新实例挂载即落在目标页；
 *   ② 窗已经开着 → 广播事件，已挂载实例当场切页（不销毁页面状态，keep-alive 不受影响）。
 */
export const MTAB_SELECT_EVENT = "alongside:mtab-select";

/** 记住某窗最后访问的页（覆盖「窗还没开」这一半）。 */
export function rememberMtabPage(persistKey: string, page: string): void {
  try {
    localStorage.setItem(MTAB_PERSIST_PREFIX + persistKey, page);
  } catch {
    /* 隐私模式等 localStorage 不可用 → 静默 */
  }
}

/** 深链接请求：写持久化 + 广播。两半缺一，就有一种「点了没反应」。 */
export function requestMtabPage(persistKey: string, page: string): void {
  rememberMtabPage(persistKey, page);
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(MTAB_SELECT_EVENT, { detail: { persistKey, page } }));
}

function readPersisted(persistKey?: string, pages?: MultitabPage[]): string | null {
  if (!persistKey) return null;
  try {
    const saved = localStorage.getItem(MTAB_PERSIST_PREFIX + persistKey);
    if (saved && pages?.some((p) => p.key === saved)) return saved;
  } catch {
    /* localStorage 不可用（隐私模式等）→ 静默退回默认页 */
  }
  return null;
}

export default function MultitabFrame({ pages, initialKey, persistKey, ariaLabel }: Props) {
  const [activeKey, setActiveKey] = useState(
    () => readPersisted(persistKey, pages) ?? initialKey ?? pages[0]?.key ?? "",
  );
  // 懒挂载 + keep-alive：只有被选中过的页才挂载，挂载后永不移除。
  const [mountedKeys, setMountedKeys] = useState<Set<string>>(
    () => new Set([readPersisted(persistKey, pages) ?? initialKey ?? pages[0]?.key ?? ""]),
  );
  const tabbarRef = useRef<HTMLDivElement>(null);

  const select = useCallback(
    (key: string) => {
      setActiveKey(key);
      setMountedKeys((prev) => {
        if (prev.has(key)) return prev;
        const next = new Set(prev);
        next.add(key);
        return next;
      });
      if (persistKey) {
        try {
          localStorage.setItem(MTAB_PERSIST_PREFIX + persistKey, key);
        } catch {
          /* 同上：隐私模式静默 */
        }
      }
    },
    [persistKey],
  );

  // WAI-ARIA tabs 模式：←/→ 在页签间循环移动并切换，Home/End 跳首尾。
  const onTabbarKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (pages.length === 0) return;
      const idx = pages.findIndex((p) => p.key === activeKey);
      let next = -1;
      if (e.key === "ArrowRight") next = (idx + 1) % pages.length;
      else if (e.key === "ArrowLeft") next = (idx - 1 + pages.length) % pages.length;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = pages.length - 1;
      if (next >= 0) {
        e.preventDefault();
        select(pages[next].key);
        const buttons = tabbarRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
        buttons?.[next]?.focus();
      }
    },
    [pages, activeKey, select],
  );

  // 深链接：入口层广播「把 persistKey 这扇窗切到 page」。
  // 只在 persistKey 匹配时响应 —— 同一时刻可能有多扇 MultitabFrame 挂载。
  useEffect(() => {
    if (!persistKey) return;
    const onSelect = (e: Event) => {
      const d = (e as CustomEvent<{ persistKey?: string; page?: string }>).detail;
      if (!d || d.persistKey !== persistKey || !d.page) return;
      if (pages.some((p) => p.key === d.page)) select(d.page);
    };
    window.addEventListener(MTAB_SELECT_EVENT, onSelect);
    return () => window.removeEventListener(MTAB_SELECT_EVENT, onSelect);
  }, [persistKey, pages, select]);

  // persistKey 变化（组件复用/热更）时同步一次持久化值。
  useEffect(() => {
    const saved = readPersisted(persistKey, pages);
    if (saved && saved !== activeKey) select(saved);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅在 persistKey 变化时对齐
  }, [persistKey]);

  if (pages.length === 0) return null;

  return (
    <div className="mtab" data-testid="multitab-frame">
      <div
        className="mtab__tabbar"
        role="tablist"
        aria-label={ariaLabel}
        ref={tabbarRef}
        onKeyDown={onTabbarKeyDown}
      >
        {pages.map((page) => {
          const active = page.key === activeKey;
          return (
            <button
              key={page.key}
              type="button"
              role="tab"
              aria-selected={active}
              tabIndex={active ? 0 : -1}
              data-testid={`mtab-tab-${page.key}`}
              className={`mtab__tab${active ? " mtab__tab--active" : ""}`}
              onClick={() => select(page.key)}
            >
              {page.label}
            </button>
          );
        })}
      </div>
      <div className="mtab__body">
        {pages.map((page) =>
          mountedKeys.has(page.key) ? (
            <div
              key={page.key}
              role="tabpanel"
              data-testid={`mtab-panel-${page.key}`}
              hidden={page.key !== activeKey}
              className="mtab__panel"
            >
              {page.content}
            </div>
          ) : null,
        )}
      </div>
    </div>
  );
}
