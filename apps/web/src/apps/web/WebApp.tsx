/**
 * 网页工作台主界面。
 *
 * 左侧是入口栏（只列 enabled 的条目），右侧是内嵌窗口。
 * 顶部两个页签：浏览 / 管理入口。
 *
 * ★ 本组件不硬编码任何站点 —— 全部来自 GET /api/v1/web/entries。
 * ★ 订阅内核 SSE（web.entry.*）做增量刷新，与 todo 同一套写法。
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { Skeleton } from "@/shared/components/Skeleton";
import { Tabs } from "@/shared/components/Tabs";
import { ApiError } from "@/shared/api/client";
import { usePluginEvent } from "@/shared/api/events";
import { webApi, webKeys, WEB_KIND_LABEL, type WebEntry } from "./api";
import WebFrame from "./WebFrame";
import WebEntriesPanel from "./WebEntriesPanel";

type View = "browse" | "manage";

const TABS = [
  { id: "browse", label: "浏览" },
  { id: "manage", label: "管理入口" },
];

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail || e.message || `请求失败（${e.status}）`;
  return e instanceof Error ? e.message : "未知错误";
}

export default function WebApp() {
  const qc = useQueryClient();
  const [view, setView] = useState<View>("browse");
  const [slug, setSlug] = useState<string | null>(null);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: webKeys.entries(),
    queryFn: () => webApi.list(),
  });

  // 后端写入会推 web.entry.* 事件，收到即失效本地缓存（SSE 自动重连）。
  const invalidate = () => void qc.invalidateQueries({ queryKey: webKeys.all });
  usePluginEvent("web.entry.created", invalidate);
  usePluginEvent("web.entry.updated", invalidate);
  usePluginEvent("web.entry.deleted", invalidate);

  const all = useMemo<WebEntry[]>(() => data?.items ?? [], [data]);

  /** 可浏览的：已启用，按 order 升序（后端已排序，这里再兜一次以防本地改动）。 */
  const usable = useMemo(
    () =>
      all
        .filter((e) => e.enabled)
        .slice()
        .sort((a, b) => a.order - b.order),
    [all],
  );

  // 选中项：默认第一个可用的；被禁用/删除后自动回退
  useEffect(() => {
    if (usable.length === 0) {
      if (slug !== null) setSlug(null);
      return;
    }
    if (!slug || !usable.some((e) => e.slug === slug)) {
      setSlug(usable[0].slug);
    }
  }, [usable, slug]);

  const current = usable.find((e) => e.slug === slug) ?? null;

  const openEntry = async (e: WebEntry) => {
    setSlug(e.slug);
    try {
      await webApi.touch(e.id); // 记一次打开（失败不影响使用）
    } catch {
      /* 非关键路径，静默 */
    }
  };

  return (
    <div className="web-root">
      <div className="web-root__tabs">
        <Tabs tabs={TABS} active={view} onChange={(id) => setView(id as View)} />
      </div>

      {isLoading && (
        <div className="web-root__loading">
          <Skeleton height={14} />
          <Skeleton height={14} />
          <Skeleton height={14} />
        </div>
      )}

      {!isLoading && error && (
        <div className="web-root__error">
          <EmptyState text="加载入口列表失败" hint={errMsg(error)} />
          <div className="web-root__error-actions">
            <Button variant="primary" onClick={() => void refetch()}>
              重试
            </Button>
          </div>
        </div>
      )}

      {!isLoading && !error && view === "manage" && <WebEntriesPanel entries={all} />}

      {!isLoading && !error && view === "browse" && (
        <div className="web-root__browse">
          <aside className="web-rail">
            {usable.length === 0 ? (
              <div className="web-rail__empty">还没有启用的入口</div>
            ) : (
              usable.map((e) => (
                <button
                  key={e.id}
                  type="button"
                  className={`web-rail__item${e.slug === slug ? " is-active" : ""}`}
                  onClick={() => void openEntry(e)}
                  title={e.title}
                >
                  <span className="web-rail__name">{e.title}</span>
                  <span className="web-rail__kind">{WEB_KIND_LABEL[e.kind] ?? e.kind}</span>
                </button>
              ))
            )}
            <button
              type="button"
              className="web-rail__add"
              onClick={() => setView("manage")}
              title="去管理入口"
            >
              ＋ 管理入口
            </button>
          </aside>

          <section className="web-stage">
            {current ? (
              <WebFrame entry={current} />
            ) : (
              <EmptyState
                text="没有可显示的网页"
                hint="到「管理入口」新建一个，填上 URL 即可内嵌；也可先关掉再打开某个入口的开关。"
              />
            )}
          </section>
        </div>
      )}
    </div>
  );
}
