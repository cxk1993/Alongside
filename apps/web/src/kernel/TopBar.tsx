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
  onTidy: () => void;
  /** ★ T30：登录后由 Desktop 传入；未传则不渲染登出按钮（内核不强制鉴权）。 */
  onLogout?: () => void;
  /** ★ 主人④（2026-09-24）：一键最小化所有未固定窗口（固定窗除外）。 */
  onMinimizeAll?: () => void;
  /** ★ 主人（2026-09-25）：顶栏「跨插件查询」快捷入口；未传则不渲染（向后兼容）。 */
  onOpenQuery?: () => void;
}

/**
 * 顶栏：品牌 / 全局搜索入口 / 走秒时钟（U2 今日摘要面板）/ 整理桌面 / 主题切换。
 * 锁定的「登录相关」不在内核职责内（鉴权由 T03 提供），这里只留一个无副作用的占位按钮位。
 */
/**
 * ★ 2026-11-22（汐瑶 · 收壳 P1-a）：顶栏从「应用菜单」换成「此刻状态条」。
 *
 * 只上屏**真数据**：今日摘要 BFF（`/api/v1/summary/today`）里 status=ok 的 provider，
 * 每项给「名字 + 条目数」。拿不到就整块不渲染 —— 宁缺不编（判据 16/25）。
 * 这是主人打开平台第一眼该读到的句子：现在是什么，而不是版本号。
 */
function ShellNow() {
  const { data } = useSummaryToday();
  const rows = (data?.providers ?? [])
    .filter((p) => p.status === "ok")
    .map((p) => ({
      label: p.label ?? p.id,
      n: Array.isArray(p.data?.items) ? p.data!.items!.length : 0,
    }));
  if (rows.length > 0) {
    return (
      <span className="topbar__now" data-testid="topbar-now" aria-label="此刻">
        <span className="topbar__now-lead">此刻</span>
        {rows.map((r) => (
          <span key={r.label} className="topbar__now-item">
            {r.label} <b>{r.n}</b>
          </span>
        ))}
      </span>
    );
  }
  // 摘要不可用时退到「它看见的」实测：在场 / 采样时刻 / 张力数。
  // 为什么不是编一句：sight 有值才说，拿不到就整块不渲染（宁缺不编）。
  return <ShellSightLine />;
}

function ShellSightLine() {
  // ★ P0（2026-11-22）：与 collab 共享同一个 /sight/now 口径（shared/api/now）。
  //   此前顶栏自己 useQuery、collab 自己 useEffect —— 两处采样时刻可以不同。
  const { data: p } = useSightNow();
  if (!p) return null;
  const hhmm = p.head?.now_local ? String(p.head.now_local).slice(11, 16) : null;
  const tensions = p.tensions?.length ?? 0;
  if (!hhmm && tensions === 0) return null;
  return (
    <span className="topbar__now" data-testid="topbar-now" aria-label="此刻">
      <span className="topbar__now-lead">此刻</span>
      <span className="topbar__now-item">
        <b>在场</b>
      </span>
      {hhmm ? <span className="topbar__now-item">采样 {hhmm}</span> : null}
      {tensions > 0 ? (
        <span className="topbar__now-item">
          张力 <b>{tensions}</b>
        </span>
      ) : null}
    </span>
  );
}

export function TopBar({ onOpenSearch, onOpenQuery, onTidy, onLogout, onMinimizeAll }: Props) {
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
        {/* ★ 主人④：一键最小化所有未固定窗口（固定窗/已最小化/最大化窗不动） */}
        {onMinimizeAll ? (
          <button
            type="button"
            className="icon-btn"
            aria-label="最小化全部窗口"
            title="最小化全部窗口（固定窗除外）"
            onClick={onMinimizeAll}
          >
            ▁
          </button>
        ) : null}
        <button
          type="button"
          className="icon-btn"
          aria-label="整理桌面"
          title="整理桌面"
          onClick={onTidy}
        >
          ⊞
        </button>
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
