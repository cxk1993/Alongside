/**
 * 整合包管理页（包层六端点的用户面）。
 *
 * ★ 状态分三档，**不合并**「登记」与「装上」：
 *   已登记但没装配是**正常状态**（试装过了、还没决定装不装）。
 *   合成一个「已安装」标签会把两种截然不同的处境搅在一起。
 *   多出的「目录缺失」是半残态：登记还在、包目录被删了——
 *   它既不是「没装」也不是「装好了」，用户需要知道**得重装**。
 *
 * ★ 界面上**没有**「装包（登记）」：登记要指定本机路径，属运维/命令行动作。
 *   与插件管理页的安全线一致：安装类操作不进界面。
 *
 * ★ 试装与装配是两个按钮：
 *   试装回答「会装成什么样」，装配回答「现在就装」。
 *   合成一个，用户就永远拿不到「先看看再决定」这个选项。
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/shared/api/client";

interface PackRow {
  id: string;
  path: string | null;
  source: string;
  registered: boolean;
  installed: boolean;
  pack_dir_exists: boolean;
  name: string;
  version: string;
  description: string;
  kind: string;
  tagline: string;
}

interface MemberOutcome {
  id: string;
  status: string;
  reason: string | null;
}

interface PackReport {
  pack_id: string;
  status: string;
  ok: boolean;
  error: string | null;
  members: MemberOutcome[];
}

interface PackListResponse {
  packs: PackRow[];
  count: number;
}

interface ReportResponse {
  dry_run?: boolean;
  assembled?: boolean;
  registered?: boolean;
  pack: PackReport;
}

type PackPhase = "installed" | "registered" | "missing" | "discovered";

/** 三档半状态：目录缺失 > 已装配 > 已登记 > 可发现。 */
function phaseOf(row: PackRow): PackPhase {
  if (!row.pack_dir_exists) return "missing";
  if (row.installed) return "installed";
  if (row.registered) return "registered";
  return "discovered";
}

const PHASE_LABEL: Record<PackPhase, string> = {
  installed: "已装配",
  registered: "已登记",
  missing: "目录缺失",
  discovered: "可发现",
};

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.detail || e.title;
  return e instanceof Error ? e.message : "请求失败";
}

export default function PacksApp() {
  const qc = useQueryClient();
  const [report, setReport] = useState<{ kind: "dry" | "real"; pack: PackReport } | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["packs", "list"],
    queryFn: () => api.get<PackListResponse>("/api/v1/packs"),
  });

  const rows: PackRow[] = data?.packs ?? [];

  const settle = (kind: "dry" | "real", res: ReportResponse) => {
    setReport({ kind, pack: res.pack });
    // 装配会改成员启用态 ⇒ 列表的 installed 要重算。
    void qc.invalidateQueries({ queryKey: ["packs"] });
  };

  const dryRun = useMutation({
    mutationFn: (id: string) => api.post<ReportResponse>(`/api/v1/packs/${id}/dry-run`, {}),
    onSuccess: (res) => settle("dry", res),
    onError: (e) => alert(errText(e)),
  });

  const assemble = useMutation({
    mutationFn: (id: string) => api.post<ReportResponse>(`/api/v1/packs/${id}/assemble`, {}),
    onSuccess: (res) => settle("real", res),
    onError: (e) => alert(errText(e)),
  });

  const uninstall = useMutation({
    mutationFn: (id: string) => api.post(`/api/v1/packs/${id}/uninstall`, {}),
    onSuccess: () => {
      setReport(null);
      void qc.invalidateQueries({ queryKey: ["packs"] });
    },
    onError: (e) => alert(errText(e)),
  });

  return (
    <div className="packs-root">
      <div className="packs-hint tiny">
        登记 / 试装 / 装配 / 卸包。装新包走命令行（安装类操作不进界面）。
      </div>

      {isLoading ? (
        <div className="empty__text">加载中…</div>
      ) : error ? (
        <div className="packs-err" role="alert">
          {errText(error)}
        </div>
      ) : rows.length === 0 ? (
        <div className="empty__text">没有可发现的整合包</div>
      ) : (
        <table className="packs-table">
          <thead>
            <tr>
              <th>包</th>
              <th>来源</th>
              <th>状态</th>
              <th>动作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const phase = phaseOf(row);
              return (
                <tr key={row.id}>
                  <td>
                    <div className="packs-name">{row.name || row.id}</div>
                    <code className="packs-id">{row.id}</code>
                  </td>
                  <td>{row.source || "—"}</td>
                  <td>
                    <span className={`packs-phase packs-phase--${phase}`}>{PHASE_LABEL[phase]}</span>
                  </td>
                  <td className="packs-actions">
                    <button
                      type="button"
                      className="btn"
                      aria-label={`试装 ${row.id}`}
                      disabled={phase === "missing" || dryRun.isPending}
                      onClick={() => dryRun.mutate(row.id)}
                    >
                      试装
                    </button>
                    <button
                      type="button"
                      className="btn btn--primary"
                      aria-label={`装配 ${row.id}`}
                      disabled={phase === "missing" || assemble.isPending}
                      onClick={() => assemble.mutate(row.id)}
                    >
                      装配
                    </button>
                    <button
                      type="button"
                      className="btn"
                      aria-label={`卸包 ${row.id}`}
                      disabled={!row.registered || uninstall.isPending}
                      onClick={() => uninstall.mutate(row.id)}
                    >
                      卸包
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {report && (
        <section className="packs-report" aria-label="装配报告">
          <h3 className="packs-report__title">
            {report.kind === "dry" ? "试装结果（未改动任何插件）" : "装配结果"} ·{" "}
            <code>{report.pack.pack_id}</code> · {report.pack.status}
          </h3>
          {report.pack.error && (
            <div className="packs-err" role="alert">
              {report.pack.error}
            </div>
          )}
          {report.pack.members.length === 0 ? (
            <div className="empty__text">没有成员结论（整包在成员校验前就停了）</div>
          ) : (
            <table className="packs-table packs-table--members">
              <thead>
                <tr>
                  <th>成员</th>
                  <th>结果</th>
                  <th>原因</th>
                </tr>
              </thead>
              <tbody>
                {report.pack.members.map((m) => (
                  <tr key={`${m.id}-${m.status}`}>
                    <td>
                      <code>{m.id}</code>
                    </td>
                    <td>{m.status}</td>
                    <td>{m.reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </div>
  );
}
