import { useQuery } from "@tanstack/react-query";
import { mcpApi, type ToolInfo } from "./api";

const METHOD_BADGE: Record<string, string> = {
  GET: "mcp-badge--get",
  POST: "mcp-badge--post",
  PUT: "mcp-badge--put",
  DELETE: "mcp-badge--delete",
};

/**
 * 工具浏览器：AI 现在能干什么，一眼看完。
 * 数据来自 GET /tools（= MCP tools/list 的纯 JSON 版）。
 */
export default function ToolExplorer() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["mcp", "tools"],
    queryFn: mcpApi.tools,
    retry: 1,
  });

  if (isLoading) return <div className="mcp-note">加载中……</div>;
  if (error) return <div className="mcp-note mcp-note--warn">工具列表加载失败（后端未启用？）</div>;

  const tools: ToolInfo[] = data ?? [];
  const byPlugin = new Map<string, ToolInfo[]>();
  for (const t of tools) {
    const list = byPlugin.get(t.plugin_id) ?? [];
    list.push(t);
    byPlugin.set(t.plugin_id, list);
  }

  if (tools.length === 0) {
    return (
      <div className="mcp-note">
        当前没有任何插件声明 provides —— 在某个插件的 manifest 里加一行 provides，
        这里就会自动多出一个工具。
      </div>
    );
  }

  return (
    <div className="mcp-tools">
      <div className="mcp-note">
        共 {tools.length} 个工具，来自 {byPlugin.size} 个插件。加新工具 = 插件 manifest 多一行
        <code>provides</code>，无需改 mcp 插件。
      </div>
      {[...byPlugin.entries()].map(([pluginId, list]) => (
        <div key={pluginId} className="mcp-plugin-group">
          <div className="mcp-plugin-group__title">{pluginId}</div>
          <table className="mcp-table">
            <thead>
              <tr>
                <th>工具名</th>
                <th>调用</th>
                <th>权限范围</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {list.map((t) => (
                <tr key={t.name}>
                  <td className="mcp-mono">{t.name}</td>
                  <td>
                    <span className={`mcp-badge ${METHOD_BADGE[t.method] ?? ""}`}>{t.method}</span>{" "}
                    <span className="mcp-mono mcp-path">{t.path}</span>
                  </td>
                  <td className="mcp-mono">{t.scope}</td>
                  <td className="mcp-desc">{t.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
