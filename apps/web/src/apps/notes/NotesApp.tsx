import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { notesApi, type NoteBrief, type TreeNode } from "./api";
import MdView from "@/shared/components/MdView";
import { ApiError } from "@/shared/api/client";
import { AppErrorState } from "@/shared/ui/app-shell/AppShell";

/**
 * 笔记窗口：搜索索引 + 点开看全文（全文经本机桥按需拉取）。
 * 桥离线时详情 content 为空，列表仍可用。
 */
export default function NotesApp() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<NoteBrief | null>(null);
  // ★ 2026-09-25（astrbot 下场 · 主人⑤）：
  //   树视图（左栏文件夹结构）+ 可写（新建/编辑/保存）
  const [activeLib, setActiveLib] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  // ★ 新建（astrbot 下场 · 主人⑤「无法新建」）
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newPath, setNewPath] = useState("");
  // ★ 自动保存（astrbot 下场 · 主人⑤「无法自动保存」）——draft 变化 1.2s 后自动 PUT
  const autosaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const search = useQuery({
    queryKey: ["notes", "search", q],
    queryFn: () => notesApi.search(q || undefined),
  });

  const detail = useQuery({
    queryKey: ["notes", "detail", selected?.id],
    queryFn: () => notesApi.get(selected!.id),
    enabled: !!selected,
  });

  const libs = useQuery({ queryKey: ["notes", "libs"], queryFn: () => notesApi.libs() });

  const tree = useQuery({
    queryKey: ["notes", "tree", activeLib],
    queryFn: () => notesApi.tree(activeLib!),
    enabled: !!activeLib,
  });

  // ★ 默认选中第一个库（astrbot 下场 · 主人「文件夹树要再点击'主仓库'才有用，默认展出的页面还是杂乱无章」）：
  //   此前 activeLib 默认 null → 树不加载 → 首屏是平铺列表（观感"杂乱"）。
  //   现在：库列表到位后自动选中第一个（通常是 main 主仓库），树即刻可见。
  useEffect(() => {
    if (activeLib) return;
    const first = libs.data?.[0]?.id;
    if (first) setActiveLib(first);
  }, [libs.data, activeLib]);

  // ★ 可写：保存（新建/编辑统一走 update 或 create）
  const saveMut = useMutation({
    mutationFn: async () => {
      if (!selected) return null;
      return notesApi.updateNote(selected.id, { content: draft });
    },
    onSuccess: () => {
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["notes"] });
    },
  });

  const createMut = useMutation({
    mutationFn: async () => {
      if (!activeLib) return null;
      return notesApi.createNote({
        lib_id: activeLib,
        title: newTitle,
        rel_path: newPath,
        content: "",
      });
    },
    onSuccess: (d) => {
      setCreating(false);
      setNewTitle("");
      setNewPath("");
      qc.invalidateQueries({ queryKey: ["notes"] });
      if (d) setSelected(d as NoteBrief);
    },
  });

  // ★ 自动保存：编辑态下 draft 变化 → 1.2s 防抖 → 自动 PUT
  useEffect(() => {
    if (!editing || !selected) return;
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(() => {
      notesApi.updateNote(selected.id, { content: draft }).then(() => {
        qc.invalidateQueries({ queryKey: ["notes", "detail", selected.id] });
      }).catch(() => {
        /* 自动保存失败静默；手动「保存」按钮仍是可靠路径 */
      });
    }, 1200);
    return () => {
      if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, editing, selected?.id]);

  const syncMut = useMutation({
    mutationFn: async () => {
      const libs = await notesApi.libs();
      const enabled = libs.filter((l) => l.enabled);
      return Promise.all(enabled.map((l) => notesApi.syncLib(l.id)));
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notes"] });
    },
  });

  return (
    <div className="notes-root">
      {/* ★ 库选择 + 文件夹树（astrbot 下场 · 主人⑤「像 obsidian 同步文件夹结构树」） */}
      <div className="notes-libs" role="tablist" aria-label="笔记库">
        {libs.data?.map((l) => (
          <button
            key={l.id}
            type="button"
            role="tab"
            aria-selected={activeLib === l.id}
            className={activeLib === l.id ? "btn btn--active" : "btn"}
            onClick={() => setActiveLib(activeLib === l.id ? null : l.id)}
            title={`${l.name}（${l.md_count} 篇）`}
          >
            {l.name || l.key}
          </button>
        ))}
      </div>
      {activeLib && tree.data && (
        <div className="notes-tree" aria-label="文件夹结构树">
          <TreeBranch node={tree.data.root} onPick={(n) => {
            setSelected({ id: n.id, lib_id: activeLib, title: n.title, rel_path: n.rel_path } as NoteBrief);
          }} />
        </div>
      )}
      <div className="notes-toolbar">
        <input
          className="notes-search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索标题或摘要…"
          aria-label="搜索笔记"
        />
        <button
          className="btn"
          type="button"
          disabled={syncMut.isPending}
          onClick={() => syncMut.mutate()}
        >
          {syncMut.isPending ? "同步中…" : "同步"}
        </button>
        {/* ★ 新建（astrbot 下场 · 主人⑤「无法新建」）——需先选库（树/笔记归属） */}
        <button
          className="btn"
          type="button"
          disabled={!activeLib}
          title={activeLib ? "在当前库新建笔记" : "请先在上方选择一个库"}
          onClick={() => setCreating(!creating)}
        >
          新建
        </button>
      </div>
      {creating && (
        <div className="notes-create" role="form" aria-label="新建笔记">
          <input
            className="notes-create__title"
            placeholder="标题"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            aria-label="新笔记标题"
          />
          <input
            className="notes-create__path"
            placeholder="相对路径，如 学习/热力学.md"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            aria-label="新笔记路径"
          />
          <button
            className="btn"
            type="button"
            disabled={!newPath || createMut.isPending}
            onClick={() => createMut.mutate()}
          >
            {createMut.isPending ? "创建中…" : "创建"}
          </button>
          <button className="btn" type="button" onClick={() => setCreating(false)}>
            取消
          </button>
        </div>
      )}
      <div className="notes-body">
        <ul className="notes-list" aria-label="笔记列表">
          {search.isLoading && <li className="empty">读取中…</li>}
          {/* ★ 2026-11-22（汐瑶 · 三态收口）：原来 error 与「真的没有索引」长得一样；
              data 为 undefined 时既不 loading 也不 empty → 整块白板，一句话都没有。
              404 单独说「未接线」（notes 模块 phase=PENDING 时就是这样），不当成「坏」。 */}
          {search.isError &&
            (search.error instanceof ApiError && search.error.status === 404 ? (
              <li className="empty">笔记层未接线（/api/v1/notes/search → 404）—— 不是「没有笔记」</li>
            ) : (
              <li>
                <AppErrorState
                  error={search.error}
                  onRetry={() => void search.refetch()}
                  title="笔记列表读取失败"
                />
              </li>
            ))}
          {search.isSuccess && search.data?.items.length === 0 && (
            <li className="empty">还没有索引，点右上角「同步」从本机桥拉取</li>
          )}
          {search.data?.items.map((n) => (
            <li key={n.id}>
              <button
                type="button"
                className={selected?.id === n.id ? "notes-item notes-item--active" : "notes-item"}
                onClick={() => setSelected(n)}
              >
                <span className="notes-item__title">{n.title || n.rel_path}</span>
                {n.excerpt && <span className="notes-item__excerpt">{n.excerpt}</span>}
              </button>
            </li>
          ))}
        </ul>
        <div className="notes-detail" aria-label="笔记详情">
          {!selected && <div className="empty">选一篇看看</div>}
          {selected && detail.isLoading && <div className="empty">读取中…</div>}
          {selected && detail.isError && (
            <div className="empty">
              {detail.error instanceof ApiError && detail.error.status === 404
                ? "这篇在索引里找不到（404）—— 可能已被移走，点「同步」再试"
                : "正文没读到 —— 不是一篇空笔记"}
            </div>
          )}
          {selected && detail.data && (
            <>
              <h3 className="notes-detail__title">{detail.data.title}</h3>
              <div className="notes-detail__path">{detail.data.rel_path}</div>
              {/* ★ 可写（astrbot 下场 · 主人⑤「无法去写去记笔记，无法新建，也无法自动保存」） */}
              <div className="notes-detail__actions">
                {!editing ? (
                  <button
                    type="button"
                    className="btn"
                    onClick={() => {
                      setDraft(detail.data!.content || "");
                      setEditing(true);
                    }}
                  >
                    编辑
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      className="btn"
                      disabled={saveMut.isPending}
                      onClick={() => saveMut.mutate()}
                    >
                      {saveMut.isPending ? "保存中…" : "保存"}
                    </button>
                    <button type="button" className="btn" onClick={() => setEditing(false)}>
                      取消
                    </button>
                  </>
                )}
              </div>
              {!editing ? (
                <MdView
                  className="notes-detail__content"
                  content={detail.data.content || "（全文暂不可用——本机桥未连接）"}
                  // ★ 附件映射（astrbot 下场 · 主人⑤「ob 附件、图片插入要能正常展示」）：
                  //   相对路径图片 → 走同源附件端点（lib=attach = obsidian 附件库），
                  //   由后端经桥取回原始字节；绝对 URL / 已带 /api 的不重写。
                  rewriteImageSrc={(src) => {
                    if (/^(https?:)?\/\//.test(src) || src.startsWith("/api/")) return src;
                    return `/api/v1/notes/attachment?lib=attach&path=${encodeURIComponent(src)}`;
                  }}
                />
              ) : (
                <textarea
                  className="notes-detail__editor"
                  aria-label="编辑笔记内容"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** ★ 递归渲染文件夹结构树（astrbot 下场 · 主人⑤）。 */
function TreeBranch({ node, onPick }: { node: TreeNode; onPick: (n: TreeNode) => void }) {
  const [open, setOpen] = useState(true);
  if (!node.is_dir) {
    return (
      <button type="button" className="notes-tree__leaf" onClick={() => onPick(node)}>
        {node.title || node.rel_path}
      </button>
    );
  }
  return (
    <div className="notes-tree__dir">
      <button type="button" className="notes-tree__toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "▾" : "▸"} {node.title || node.rel_path}
      </button>
      {open && node.children.map((c) => (
        <div key={c.id} className="notes-tree__children">
          <TreeBranch node={c} onPick={onPick} />
        </div>
      ))}
    </div>
  );
}
