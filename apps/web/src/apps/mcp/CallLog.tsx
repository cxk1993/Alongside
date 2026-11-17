import { useQuery } from "@tanstack/react-query";
import { mcpApi } from "./api";

/**
 * 调用日志：经 MCP 的操作流水（audit_log 中 actor 以 mcp: 开头的行）。
 * action=denied 表示被 scope 拦下的越权尝试 —— 标红让人一眼看到。
 */
export default function CallLog() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["mcp", "audit"],
    queryFn: () => mcpApi.auditLogs(150),
    retry: 1,
  });

  if (isLoading) return <div className="mcp-note">加载中……</div>;
  if (error) return <div className="mcp-note mcp-note--warn">流水加载失败</div>;

  const rows = data ?? [];
  if (rows.length === 0) {
    return <div className="mcp-note">还没有 MCP 调用记录 —— AI 第一次动手后这里会出现流水。</div>;
  }

  return (
    <table className="mcp-table">
      <thead>
        <tr>
          <th>时间</th>
          <th>调用方（PAT）</th>
          <th>动作</th>
          <th>目标</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td>{new Date(r.at).toLocaleString()}</td>
            <td className="mcp-mono">{r.actor}</td>
            <td>
              {r.action === "denied" ? (
                <span className="mcp-badge mcp-badge--delete">已拒绝</span>
              ) : (
                <span className="mcp-mono">{r.action}</span>
              )}
            </td>
            <td className="mcp-mono mcp-path">{r.target}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
