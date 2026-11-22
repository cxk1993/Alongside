import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/shared/api/client";
import { loadPrefs, savePrefs, type SidebarPrefs } from "./prefs";
import { AppErrorState } from "@/shared/ui/app-shell/AppShell";

interface Item {
  id: string;
  type: string;
  label: string;
  abbr: string;
  href: string;
  node_ref: string;
  group: string;
  order: number;
  enabled: boolean;
  pinned?: boolean;
}

const TYPES = [
  { id: "link", label: "链接" },
  { id: "friend-link", label: "友链" },
  { id: "note", label: "便签" },
  { id: "announcement", label: "公告" },
] as const;

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.detail || e.title;
  return e instanceof Error ? e.message : "请求失败";
}

/** · 侧栏自定义容器（高度自定义：类型/分组/排序/显隐）。 */
export default function SidebarApp() {
  const qc = useQueryClient();
  const [prefs, setPrefs] = useState<SidebarPrefs>(() => loadPrefs());
  const [label, setLabel] = useState("");
  const [href, setHref] = useState("");
  const [group, setGroup] = useState("");
  const [type, setType] = useState<string>("link");
  const [nodeRef, setNodeRef] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const patchPrefs = (p: Partial<SidebarPrefs>) => {
    const next = { ...prefs, ...p };
    setPrefs(next);
    savePrefs(next);
  };

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["sidebar", "items"],
    queryFn: () => api.get<{ items: Item[]; count: number }>("/api/v1/sidebar/items"),
  });

  const createMut = useMutation({
    mutationFn: () =>
      api.post<Item>("/api/v1/sidebar/items", {
        type,
        label: label.trim(),
        href: type === "note" || type === "announcement" ? "" : href.trim(),
        node_ref: nodeRef.trim(),
        group: group.trim(),
      }),
    onSuccess: () => {
      setLabel("");
      setHref("");
      setNodeRef("");
      setErr(null);
      qc.invalidateQueries({ queryKey: ["sidebar"] });
    },
    onError: (e) => setErr(errText(e)),
  });

  const delMut = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/sidebar/items/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sidebar"] }),
    onError: (e) => setErr(errText(e)),
  });

  const patchMut = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Item> }) =>
      api.patch(`/api/v1/sidebar/items/${id}`, body),
    onSuccess: () => {
      setEditingId(null);
      setErr(null);
      qc.invalidateQueries({ queryKey: ["sidebar"] });
    },
    onError: (e) => setErr(errText(e)),
  });

  const all = data?.items ?? [];
  const groups = useMemo(
    () => Array.from(new Set(all.map((i) => i.group).filter(Boolean))).sort(),
    [all],
  );
  const items = useMemo(() => {
    let list = all;
    if (prefs.groupFilter) list = list.filter((i) => i.group === prefs.groupFilter);
    if (prefs.hideDisabled) list = list.filter((i) => i.enabled);
    return list;
  }, [all, prefs.groupFilter, prefs.hideDisabled]);

  const move = (id: string, dir: -1 | 1) => {
    const idx = items.findIndex((i) => i.id === id);
    const j = idx + dir;
    if (idx < 0 || j < 0 || j >= items.length) return;
    const ids = items.map((i) => i.id);
    const a = ids[idx];
    ids[idx] = ids[j];
    ids[j] = a;
    api.post("/api/v1/sidebar/items/reorder", { ids }).then(
      () => qc.invalidateQueries({ queryKey: ["sidebar"] }),
      (e) => setErr(errText(e)),
    );
  };

  const linkish = type === "link" || type === "friend-link";

  return (
    <div className="sidebar-root">
      <form
        className="sidebar-add"
        onSubmit={(e) => {
          e.preventDefault();
          if (!label.trim() || createMut.isPending) return;
          if (linkish && !href.trim()) return;
          createMut.mutate();
        }}
      >
        <select
          value={type}
          onChange={(e) => setType(e.target.value)}
          aria-label="类型"
          className="sidebar-type"
        >
          {TYPES.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
            </option>
          ))}
        </select>
        <input placeholder="标题" value={label} onChange={(e) => setLabel(e.target.value)} aria-label="标题" />
        {linkish ? (
          <input placeholder="https://…" value={href} onChange={(e) => setHref(e.target.value)} aria-label="链接" />
        ) : (
          <input
            placeholder="笔记路径 notes/便签/…"
            value={nodeRef}
            onChange={(e) => setNodeRef(e.target.value)}
            aria-label="笔记引用"
          />
        )}
        <input placeholder="分组" value={group} onChange={(e) => setGroup(e.target.value)} aria-label="分组" />
        <button type="submit" className="btn btn--primary" disabled={createMut.isPending}>
          添加
        </button>
      </form>

      <div className="sidebar-toolbar">
        <select
          value={prefs.groupFilter}
          onChange={(e) => patchPrefs({ groupFilter: e.target.value })}
          aria-label="按分组筛选"
        >
          <option value="">全部分组</option>
          {groups.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
        <label className="tiny">
          <input
            type="checkbox"
            checked={prefs.hideDisabled}
            onChange={(e) => patchPrefs({ hideDisabled: e.target.checked })}
            aria-label="隐藏已禁用"
          />{" "}
          隐藏已禁用
        </label>
        <span className="tiny">{items.length} 条</span>
        <button
          type="button"
          className="btn"
          aria-label="导出条目 JSON"
          onClick={() => {
            api.get<{ items: Item[] }>("/api/v1/sidebar/export").then(
              (data) => {
                const blob = new Blob([JSON.stringify(data, null, 2)], {
                  type: "application/json",
                });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = "sidebar-items.json";
                a.click();
                URL.revokeObjectURL(url);
              },
              (e) => setErr(errText(e)),
            );
          }}
        >
          导出
        </button>
      </div>

      {err ? (
        <div role="alert" className="sidebar-err">
          {err}
        </div>
      ) : null}

      <div className="sidebar-list">
        {isLoading ? (
          <div>读取中…</div>
        ) : isError ? (
          /* ★ 2026-11-22（汐瑶 · 三态收口）：原来 error 落到「还没有自定义条目」。 */
          <AppErrorState error={error} onRetry={() => void refetch()} title="条目读取失败" />
        ) : items.length === 0 ? (
          <div className="sidebar-empty">
            还没有自定义条目——加一个书签 / 友链 / 便签吧
            <div className="tiny">便签正文走笔记窗（node_ref），这里只放卡片</div>
          </div>
        ) : (
          items.map((it, idx) => (
            <div key={it.id} className={"sidebar-row" + (it.enabled ? "" : " sidebar-row--off")}>
              <span className="sidebar-abbr">{it.abbr || it.label.slice(0, 2)}</span>
              <div className="sidebar-main">
                {editingId === it.id ? (
                  <input
                    value={editLabel}
                    onChange={(e) => setEditLabel(e.target.value)}
                    aria-label="改名"
                    onKeyDown={(e) => {
                      if (e.key === "Enter")
                        patchMut.mutate({ id: it.id, body: { label: editLabel.trim() } });
                      if (e.key === "Escape") setEditingId(null);
                    }}
                  />
                ) : (
                  <span className="sidebar-label" title={it.href || it.node_ref}>
                    {it.label}
                  </span>
                )}
                <span className="tiny">
                  {it.group || "未分组"} · {TYPES.find((t) => t.id === it.type)?.label ?? it.type}
                </span>
              </div>
              <button type="button" className="btn" aria-label={`上移 ${it.label}`} disabled={idx === 0} onClick={() => move(it.id, -1)}>
                ↑
              </button>
              <button
                type="button"
                className="btn"
                aria-label={`下移 ${it.label}`}
                disabled={idx === items.length - 1}
                onClick={() => move(it.id, 1)}
              >
                ↓
              </button>
              <button
                type="button"
                className="btn"
                aria-label={`改名 ${it.label}`}
                onClick={() => {
                  setEditingId(it.id);
                  setEditLabel(it.label);
                }}
              >
                名
              </button>
              <button
                type="button"
                className="btn"
                aria-label={it.enabled ? `禁用 ${it.label}` : `启用 ${it.label}`}
                onClick={() => patchMut.mutate({ id: it.id, body: { enabled: !it.enabled } })}
              >
                {it.enabled ? "停" : "启"}
              </button>
              {it.href ? (
                <a href={it.href} target="_blank" rel="noreferrer">
                  打开
                </a>
              ) : (
                <span className="tiny">笔记</span>
              )}
              <button type="button" className="btn" aria-label={`删除 ${it.label}`} onClick={() => delMut.mutate(it.id)}>
                删
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
