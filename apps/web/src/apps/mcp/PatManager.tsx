import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { mcpApi, type PatCreated } from "./api";

const COMMON_SCOPES = ["calendar:read", "calendar:write", "todo:read", "todo:write"];

/**
 * PAT 管理：创建（明文只弹一次）/ 列表 / 改 scope / 吊销。
 * 红线：明文拿到手只存内存态，一旦关闭弹层就再也找不到 —— 这是有意的。
 */
export default function PatManager() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [created, setCreated] = useState<PatCreated | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["mcp", "pats"],
    queryFn: mcpApi.listPats,
    retry: 1,
  });

  const createMut = useMutation({
    mutationFn: () => mcpApi.createPat(name.trim() || "未命名 PAT", selected),
    onSuccess: (pat) => {
      setCreated(pat);
      setName("");
      setSelected([]);
      void qc.invalidateQueries({ queryKey: ["mcp", "pats"] });
    },
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) => mcpApi.revokePat(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["mcp", "pats"] }),
  });

  const patchMut = useMutation({
    mutationFn: ({ id, scopes }: { id: string; scopes: string[] }) => mcpApi.patchPat(id, scopes),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["mcp", "pats"] }),
  });

  const pats = useMemo(() => data ?? [], [data]);

  const toggleScope = (s: string) =>
    setSelected((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));

  return (
    <div className="mcp-pats">
      <div className="mcp-pats__create">
        <input
          className="mcp-input"
          placeholder="PAT 名字（如 hermes-桌面）"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={64}
        />
        <div className="mcp-pats__scopes">
          {COMMON_SCOPES.map((s) => (
            <label key={s} className="mcp-scope-chip">
              <input
                type="checkbox"
                checked={selected.includes(s)}
                onChange={() => toggleScope(s)}
              />
              <span className="mcp-mono">{s}</span>
            </label>
          ))}
        </div>
        <button
          className="mcp-btn"
          disabled={createMut.isPending}
          onClick={() => createMut.mutate()}
        >
          创建 PAT
        </button>
        {createMut.isError ? (
          <div className="mcp-note mcp-note--warn">创建失败：{String(createMut.error)}</div>
        ) : null}
      </div>

      {created ? (
        <div className="mcp-created">
          <div className="mcp-created__title">
            ★ 明文只显示这一次（{created.token_prefix}…），关闭后不可再看
          </div>
          <code className="mcp-created__token mcp-mono">{created.token}</code>
          <button className="mcp-btn mcp-btn--ghost" onClick={() => setCreated(null)}>
            我已保存，关闭
          </button>
        </div>
      ) : null}

      {isLoading ? (
        <div className="mcp-note">加载中……</div>
      ) : pats.length === 0 ? (
        <div className="mcp-note">还没有 PAT —— 创建一个给 AI 客户端用。</div>
      ) : (
        <table className="mcp-table">
          <thead>
            <tr>
              <th>名字</th>
              <th>前缀</th>
              <th>权限范围</th>
              <th>最后使用</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {pats.map((p) => (
              <tr key={p.id} style={p.revoked_at ? { opacity: 0.5 } : undefined}>
                <td>{p.name}</td>
                <td className="mcp-mono">{p.token_prefix}</td>
                <td className="mcp-mono">{p.scopes.length ? p.scopes.join(", ") : "（无）"}</td>
                <td>{p.last_used_at ? new Date(p.last_used_at).toLocaleString() : "从未"}</td>
                <td>
                  {p.revoked_at ? (
                    <span className="mcp-badge mcp-badge--delete">已吊销</span>
                  ) : (
                    <span className="mcp-badge mcp-badge--get">有效</span>
                  )}
                </td>
                <td>
                  {!p.revoked_at ? (
                    <>
                      {p.scopes.length ? (
                        <button
                          className="mcp-btn mcp-btn--ghost"
                          disabled={patchMut.isPending}
                          onClick={() => patchMut.mutate({ id: p.id, scopes: [] })}
                        >
                          收权
                        </button>
                      ) : null}
                      <button
                        className="mcp-btn mcp-btn--danger"
                        disabled={revokeMut.isPending}
                        onClick={() => revokeMut.mutate(p.id)}
                      >
                        吊销
                      </button>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
