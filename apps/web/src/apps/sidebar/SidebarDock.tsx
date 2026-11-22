/**
 * 侧栏槽贡献（desktop.dock-left / desktop.dock-right）—— **只读**。
 *
 * 作用：把用户自己建的侧栏条目（链接 / 友链 / 便签 / 公告）渲染到桌面侧栏。
 *
 * ★ 为什么需要它：后端 manifest 声明了 `slots: ["desktop.dock-left","desktop.dock-right"]`，
 *   但 `apps/sidebar/index.tsx` 的 `slots` 一直是 `{}` —— 声明了没人实现，
 *   于是「侧栏自定义加不进/看不到」在代码层至今没闭环（比丢 2026-11-22 的引用图谱结论）。
 *
 * ★ 布局不归这里：宿主 `kernel/shell/DesktopSidebar.tsx` 已经实现
 *   「用户项优先置顶、内置导航折叠成 <details>」，本组件只负责把数据变成节点。
 *
 * ★ 契约：GET /api/v1/sidebar/items → { items: ItemOut[], count }
 *   ItemOut = { id,type,label,icon,abbr,href,target,body,node_ref,description,group,order,enabled,pinned }
 *
 * ★ 降级：加载中 / 空 / 失败一律返回 null（安静降级，不占位、不闪、不编）。
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "@/shared/api/client";
import "./sidebarDock.css";

interface SidebarItemOut {
  id: string;
  type: string;
  label: string;
  icon: string;
  abbr: string;
  href: string;
  target: string;
  body: string;
  node_ref: string;
  description: string;
  group: string;
  order: number;
  enabled: boolean;
  pinned: boolean;
}

interface SidebarListOut {
  items: SidebarItemOut[];
  count: number;
}

/** 便签/公告的角标文字；链接类不加角标（视觉噪音）。 */
const TAG_OF: Record<string, string> = {
  note: "便签",
  announcement: "公告",
};

function isExternalLink(it: SidebarItemOut): boolean {
  return it.type === "link" || it.type === "friend-link";
}

export default function SidebarDock() {
  const { data } = useQuery({
    queryKey: ["sidebar", "items", "dock"],
    queryFn: () => api.get<SidebarListOut>("/api/v1/sidebar/items"),
    staleTime: 60_000,
    retry: false,
  });

  const enabled = (data?.items ?? []).filter((i) => i.enabled);
  if (enabled.length === 0) return null;

  // 按 group 分组（空串=默认组）；组内 pinned 优先，再按 order
  const groups = new Map<string, SidebarItemOut[]>();
  for (const it of enabled) {
    const key = (it.group || "").trim();
    const arr = groups.get(key);
    if (arr) arr.push(it);
    else groups.set(key, [it]);
  }
  for (const arr of groups.values()) {
    arr.sort((a, b) => Number(b.pinned) - Number(a.pinned) || a.order - b.order || a.label.localeCompare(b.label));
  }

  return (
    <nav className="sidebar-dock" aria-label="我添加的">
      {[...groups.entries()].map(([groupName, arr]) => (
        <div className="sidebar-dock__group" key={groupName || "__default"}>
          {groupName ? <div className="sidebar-dock__group-title">{groupName}</div> : null}
          {arr.map((it) => (
            <DockRow key={it.id} item={it} />
          ))}
        </div>
      ))}
    </nav>
  );
}

function DockRow({ item }: { item: SidebarItemOut }) {
  const abbr = item.abbr || item.label.slice(0, 1);
  const title = item.description || item.label;

  if (isExternalLink(item)) {
    return (
      <a
        className="sidebar-dock__row"
        href={item.href}
        target={item.target || "_blank"}
        rel="noopener noreferrer"
        title={title}
      >
        <span className="sidebar-dock__abbr">{abbr}</span>
        <span className="sidebar-dock__label">{item.label}</span>
      </a>
    );
  }

  const tag = TAG_OF[item.type] ?? "";
  return (
    <details className="sidebar-dock__note">
      <summary className="sidebar-dock__row" title={title}>
        <span className="sidebar-dock__abbr">{abbr}</span>
        <span className="sidebar-dock__label">{item.label}</span>
        {tag ? <span className="sidebar-dock__tag">{tag}</span> : null}
      </summary>
      {item.body ? <p className="sidebar-dock__body">{item.body}</p> : null}
    </details>
  );
}