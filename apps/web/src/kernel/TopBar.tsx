import { useTheme } from "@/shared/styles/theme";
import { useDesktopStore } from "./store";
import { TimeWidget } from "./shell/TimeWidget";
import { useSightNow } from "@/shared/api/now";
import { useSummaryToday } from "./shell/summaryToday";

/** ★ 版本号 / 构建时刻 —— 由 vite.config.ts 的 define 注入（见该文件注释）。 */
declare const __APP_VERSION__: string;
declare const __BUILD_AT__: string;

interface Props {
  onOpenSearch: () => void;
  /** ★ T30：登录后由 Desktop 传入；未传则不渲染登出按钮（内核不强制鉴权）。 */
  onLogout?: () => void;
  /** ★ 主人（2026-09-25）：顶栏「跨插件查询」快捷入口；未传则不渲染（向后兼容）。 */
  onOpenQuery?: () => void;
}

/**
 * 顶栏：品牌 / 全局搜索 / 此刻状态 / 时钟 / 工作区页签 / 主题。
 *
 * ★ 顶栏语义化（2026-11-22 汐瑶 · 云昔 13:5x 派活）：第一行该读到「现在是什么」，不是版本号 ——
 *   ● AI 在场 · 下一件 19:00 · 今天 2 · 欠 0 · 张力 1
 *
 * ★ P0 去桌面化（2026-11-22 17:xx 凝析 · 25-前端路线重构 P0）：
 *   撤除「▁ 最小化全部窗口」「⊞ 整理桌面」两个 icon-btn —— 它们是 Windows 桌面隐喻的余党。
 *   顶栏右上角只留 主题 / 登出（时钟 `TimeWidget` 保留，它是今日摘要面板的唯一入口）。
 *   store 动作（`minimizeAllUnpinned` / `tidyDesktop`）与命令面板条目**本轮不动**，
 *   待区块模型（RGL）落地后随浮窗模型一并退役。
 *   回归钉子见 `TopBar.test.tsx`（此前两个按钮零测试覆盖，删了会长回来）。
 *
 * 取数口径唯一：`useSightNow()`（shared/api/now.ts 单例，与 collab 同一份采样时刻）。
 * 为什么不再自己拉一次：两处采样时刻可以不同，界面会自己跟自己打架。
 * 「今天 N」来自今日摘要 BFF；摘要 unavailable 时**只省这一段**，其余照常。
 * 诚实边界：sight 不是 ready 就整块不渲染（missing = 还没做，error = 坏了，都不许编）。
 */
function ShellNow() {
  const { status, data } = useSightNow();
  const { data: summary } = useSummaryToday();
  if (status !== "ready" || !data) return null;

  const t = data.time;
  const hhmm = (iso?: string) => (iso ? String(iso).slice(11, 16) : null);

  // 「正在做」：忙到几点 > 下一件几点 > 空闲（都取服务端 time 块，不推测）
  const doing = t?.busy_until
    ? `忙到 ${hhmm(t.busy_until) ?? "—"}`
    : t?.next_event?.start_at
      ? `下一件 ${hhmm(t.next_event.start_at) ?? "—"}`
      : "空闲";

  const okProviders = (summary?.providers ?? []).filter((p) => p.status === "ok");
  const todayN = okProviders.reduce(
    (acc, p) => acc + (Array.isArray(p.data?.items) ? p.data!.items!.length : 0),
    0,
  );
  const owed = data.attention?.length ?? 0;
  const tensions = data.tensions?.length ?? 0;

  return (
    <span className="topbar__now" data-testid="topbar-now" aria-label="此刻">
      <span className="topbar__now-item topbar__now-live">
        <i className="topbar__dot" aria-hidden="true" />
        AI 在场
      </span>
      <span className="topbar__now-item">{doing}</span>
      {okProviders.length > 0 ? (
        <span className="topbar__now-item">
          今天 <b>{todayN}</b>
        </span>
      ) : null}
      <span className="topbar__now-item">
        欠 <b>{owed}</b>
      </span>
      <span className="topbar__now-item">
        冲突 <b>{tensions}</b>
      </span>
    </span>
  );
}

export function TopBar({ onOpenSearch, onOpenQuery, onLogout }: Props) {
  const theme = useTheme((s) => s.theme);
  const toggle = useTheme((s) => s.toggle);
  // ★ T23 工作区切换器
  const workspaces = useDesktopStore((s) => s.workspaces);
  // ★ 收放统一走「罗盘」（Desktop 内的 .desktop__compass-btn）——
  //   TopBar 不再持有收放按钮与 store 订阅（原 ⌃/⌄ 已撤）。
  const activeWorkspaceId = useDesktopStore((s) => s.activeWorkspaceId);
  const switchWorkspace = useDesktopStore((s) => s.switchWorkspace);
  const createWorkspace = useDesktopStore((s) => s.createWorkspace);

  return (
    <div className="topbar">
      <span className="topbar__brand">Alongside</span>
      <ShellNow />
      {/* ★ 2026-09-27（主人「正式版 1.0.0」）：版本号 + 构建时刻。
          悬停可看构建时间 —— 一眼分清「跑的是新版还是 PWA 缓存的旧版」。 */}
      <span
        className="topbar__version"
        title={`构建于 ${__BUILD_AT__}（UTC）· 若与部署时间不符请强制刷新 Ctrl+Shift+R`}
        data-testid="app-version"
      >
        v{__APP_VERSION__}
      </span>
      <button
        type="button"
        className="topbar__search"
        onClick={onOpenSearch}
        aria-label="打开全局搜索"
      >
        搜索…
        <kbd>Ctrl K</kbd>
      </button>

      {/* ★ 主人（2026-09-25）：搜索框旁「跨插件查询」快捷入口——尺寸随搜索框，窄屏同隐。 */}
      {onOpenQuery ? (
        <button
          type="button"
          className="topbar__query"
          onClick={onOpenQuery}
          aria-label="打开跨插件查询"
        >
          跨插件查询
        </button>
      ) : null}

      {/* ★ T23：工作区页签 + 新建。名字一律「工作区 N」，内核零业务。 */}
      <div className="topbar__ws" role="tablist" aria-label="工作区">
        {workspaces.map((w) => (
          <button
            key={w.id}
            type="button"
            role="tab"
            aria-selected={w.id === activeWorkspaceId}
            className={`topbar__ws-tab${w.id === activeWorkspaceId ? " is-active" : ""}`}
            title={w.name}
            onClick={() => switchWorkspace(w.id)}
          >
            {w.name}
          </button>
        ))}
        <button
          type="button"
          className="topbar__ws-add"
          aria-label="新建工作区"
          title="新建工作区"
          onClick={() => createWorkspace()}
        >
          ＋
        </button>
      </div>

      <span className="topbar__spacer" />
      {/* U2：右上角时间 → 点击展开今日摘要面板（跨插件聚合） */}
      <TimeWidget />
      <div className="topbar__actions">
        {/* ★ + 主人反馈（2026-09-25 16:0x）：原「收起顶栏 ⌃ / 收起底栏 ⌄」
            两按钮**已由罗盘取代**（罗盘按钮=统一收放入口），此处撤除，避免双入口并存。 */}
        {/* ★ P0 去桌面化（2026-11-22 凝析）：原「▁ 最小化全部」「⊞ 整理桌面」已撤除 ——
            这两个是 Windows 桌面隐喻的余党（浮窗层叠/平铺管理），区块模型里不存在。
            store 动作与命令面板条目本轮保留（比丢 lane），RGL 落地后一并退役。
            回归钉子：TopBar.test.tsx。 */}
        <button
          type="button"
          className="icon-btn"
          aria-label={theme === "dark" ? "切换到亮色" : "切换到暗色"}
          title="切换主题"
          onClick={toggle}
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>
        {onLogout ? (
          <button
            type="button"
            className="icon-btn"
            aria-label="登出"
            title="登出"
            onClick={onLogout}
          >
            ⏻
          </button>
        ) : null}
      </div>
    </div>
  );
}
