import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/shared/api/client";
import { requestPluginListRefresh } from "@/kernel/plugins/pluginListSignal";

interface PluginRow {
  id: string;
  name?: string;
  enabled?: boolean;
  valid?: boolean;
  kind?: string;
  version?: string;
}

/** 插件类型枚举（后端 discover.py: core | builtin | third-party | container）→ 中文。 */
const KIND_LABEL: Record<string, string> = {
  core: "内核",
  builtin: "内置",
  "third-party": "第三方",
  container: "容器",
};

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.detail || e.title;
  return e instanceof Error ? e.message : "请求失败";
}

/**
 * 插件管理（主人⑫：并入「系统」窗多页）。
 * 列表 / 启停——安装卸载不在此页（危险操作走命令行）。
 */
export default function PluginsApp() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["plugins", "list"],
    queryFn: () =>
      api.get<PluginRow[] | { items?: PluginRow[]; plugins?: PluginRow[] }>("/api/v1/plugins"),
  });

  const rows: PluginRow[] = Array.isArray(data)
    ? data
    : ((data as { items?: PluginRow[] })?.items ??
      (data as { plugins?: PluginRow[] })?.plugins ??
      []);

  const toggle = useMutation({
    mutationFn: ({ id, enable }: { id: string; enable: boolean }) =>
      api.post(`/api/v1/plugins/${id}/${enable ? "enable" : "disable"}`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["plugins"] });
      // ★ r5：启停也改桌面清单（Dock 出现/置灰）—— poke 内核，不刷新页面即生效。
      requestPluginListRefresh();
    },
    onError: (e) => alert(errText(e)),
  });

  /** 手动刷新：覆盖「在别处（CLI/上传端点）装好、本页要立刻看见」的场景。 */
  const refreshAll = () => {
    void qc.invalidateQueries({ queryKey: ["plugins"] });
    requestPluginListRefresh();
  };

  return (
    <div className="plugins-root">
      <div className="plugins-hint tiny">
        启用 / 停用即时生效（不刷新页面即同步到桌面坞）。安装卸载不在此页（安全线）。
      </div>
      <div className="plugins-actions">
        <button type="button" className="btn" onClick={refreshAll}>
          刷新清单
        </button>
      </div>
      {isLoading ? (
        <div className="empty__text">加载中…</div>
      ) : error ? (
        <div className="plugins-err" role="alert">
          {errText(error)}
        </div>
      ) : rows.length === 0 ? (
        <div className="empty__text">没有插件清单</div>
      ) : (
        <table className="plugins-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>ID</th>
              <th>类型</th>
              <th>版本</th>
              <th>状态</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id}>
                <td>{p.name || p.id}</td>
                <td>
                  <code>{p.id}</code>
                </td>
                <td>{p.kind ? (KIND_LABEL[p.kind] ?? p.kind) : "—"}</td>
                <td>{p.version || "—"}</td>
                <td>
                  <span className={p.enabled === false ? "plugins-off" : "plugins-on"}>
                    {p.enabled === false ? "停用" : "启用"}
                  </span>
                </td>
                <td>
                  <button
                    type="button"
                    className="btn"
                    aria-label={`${p.enabled === false ? "启用" : "停用"} ${p.id}`}
                    onClick={() => toggle.mutate({ id: p.id, enable: p.enabled === false })}
                  >
                    {p.enabled === false ? "启用" : "停用"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
