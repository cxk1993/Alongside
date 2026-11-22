import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError } from "@/shared/api/client";
import { queryApi, type QueryResult } from "./api";
import { loadPrefs, savePrefs } from "./prefs";
import { AppErrorState } from "@/shared/ui/app-shell/AppShell";
import "./query.css";

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.detail || e.title;
  return e instanceof Error ? e.message : "查询失败";
}

/** · 跨模块预置查询（只读）。 */
export default function QueryApp() {
  const [qid, setQid] = useState<string | null>(() => loadPrefs().lastPreset);
  const [days, setDays] = useState<number>(0);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const { data: presets, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["query", "presets"],
    queryFn: () => queryApi.presets(),
  });

  const run = async (id: string, d?: number) => {
    setQid(id);
    savePrefs({ lastPreset: id });
    const scope = d ?? days;
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      setResult(await queryApi.run(id, scope || undefined));
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const items = presets?.items ?? [];
  const rows = result?.rows ?? [];

  return (
    <div className="query-root">
      <div className="query-list">
        {isLoading ? (
          <div className="empty__text">读取中…</div>
        ) : isError ? (
          /* ★ 2026-11-22（汐瑶 · 三态收口）：原来 error 落到「暂无预置查询」。 */
          <AppErrorState error={error} onRetry={() => void refetch()} title="预置查询读取失败" />
        ) : items.length === 0 ? (
          <div className="empty__text">暂无预置查询</div>
        ) : (
          items.map((p) => (
            <button
              key={p.id}
              type="button"
              className={"query-item" + (qid === p.id ? " query-item--on" : "")}
              onClick={() => run(p.id)}
              disabled={busy}
            >
              {p.title}
            </button>
          ))
        )}
      </div>
      <div className="query-range">
        <label className="tiny">
          范围
          <select
            value={days}
            onChange={(e) => {
              const n = Number(e.target.value) || 0;
              setDays(n);
              if (qid) run(qid, n);
            }}
            aria-label="查询范围天数"
          >
            <option value={0}>默认</option>
            <option value={1}>近 1 天</option>
            <option value={3}>近 3 天</option>
            <option value={7}>近 7 天</option>
            <option value={30}>近 30 天</option>
          </select>
        </label>
      </div>
      <div className="query-out">
        {err ? (
          <div role="alert" className="query-err">
            {err}
          </div>
        ) : busy ? (
          <div className="empty__text">查询中…</div>
        ) : !result ? (
          <div className="empty__text">点左侧一条预置查询</div>
        ) : result.empty ? (
          <div className="empty__text">{result.query?.empty_text || "没有结果"}</div>
        ) : (
          <>
            <div className="tiny" style={{ color: "var(--txt-faint)", display: "flex", gap: 8, alignItems: "center" }}>
              <span>
                {result.query?.id || qid} · {result.row_count} 条
                {result.partial ? " · 部分源不可用" : ""}
              </span>
              <button
                type="button"
                className="query-export"
                onClick={() => {
                  const cols: string[] = [];
                  const seen = new Set<string>();
                  for (const r of rows) {
                    for (const k of Object.keys(r)) {
                      if (!seen.has(k)) {
                        seen.add(k);
                        cols.push(k);
                      }
                    }
                  }
                  const esc = (v: unknown) => {
                    const t = v == null ? "" : String(v);
                    return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
                  };
                  const csv = [cols.map(esc).join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join("\n"))].join("\n");
                  const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
                  const a = document.createElement("a");
                  a.href = URL.createObjectURL(blob);
                  a.download = `query-${result.query?.id || qid || "result"}.csv`;
                  a.click();
                  URL.revokeObjectURL(a.href);
                }}
              >
                下载 CSV
              </button>
              <button
                type="button"
                className="query-export"
                onClick={async () => {
                  const cols: string[] = [];
                  const seen = new Set<string>();
                  for (const r of rows) {
                    for (const k of Object.keys(r)) {
                      if (!seen.has(k)) {
                        seen.add(k);
                        cols.push(k);
                      }
                    }
                  }
                  const tsv = [cols.join("\t"), ...rows.map((r) => cols.map((c) => String(r[c] ?? "")).join("\t"))].join("\n");
                  try {
                    await navigator.clipboard.writeText(tsv);
                  } catch {
                    /* 剪贴板不可用静默 */
                  }
                }}
              >
                复制
              </button>
            </div>
            {(() => {
              // 结果表格化（主人 09-25「功能上不太成功」主病灶：原 key:value 平铺可读性差）。
              // CSV 导出（MiMo 参数化后留白给的纯前端件）：列头并集 + RFC4180 引号转义。
              // 列头 = 各行键的并集（稳定序：首行序优先，新键追加），行 = rows。
              const cols: string[] = [];
              const seen = new Set<string>();
              for (const r of rows) {
                for (const k of Object.keys(r)) {
                  if (!seen.has(k)) {
                    seen.add(k);
                    cols.push(k);
                  }
                }
              }
              return (
                <table className="query-table" data-testid="query-table">
                  <thead>
                    <tr>
                      {cols.map((c) => (
                        <th key={c} scope="col">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i}>
                        {cols.map((c) => {
                          const v = r[c];
                          return (
                            <td key={c} className={v == null || v === "" ? "query-table__empty" : undefined}>
                              {v == null || v === "" ? "—" : String(v)}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              );
            })()}
          </>
        )}
      </div>
    </div>
  );
}
