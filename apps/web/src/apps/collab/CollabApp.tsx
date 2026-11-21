/**
 * 共处工作台 v1.1（T10：接真 proposals / sight 端点）。
 *
 * 三个面（每区单一职责）：
 *   左 = 它看见的（Sight：head + tensions + attention + degraded 诚实降级）
 *   右 = 它想说的（提案卡流：GET /api/v1/proposals?status=pending；签字/驳回/撤销走真 API）
 *   底 = 它做过的（时间线 v1 只读，撤销结果从接口 detail 回显）
 * 顶 = 在场状态条。
 *
 * ★ 纪律：不造假数据、不装深度（判据 16/25）。接口没接就如实显示「未就绪」。
 * ★ 本组件同时作为桌面级背景面（desktop.widget 插槽）与普通窗口两种形态渲染。
 */
import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "@/shared/api/client";
import "./collab.css";

type ZoneStatus = "loading" | "ready" | "missing" | "error";

interface AttentionItem {
  kind?: string;
  title?: string;
}

interface TensionItem {
  id?: string;
  kind?: string;
  summary?: string;
  severity?: string;
}

interface DegradedItem {
  source?: string;
  reason?: string;
}

interface SightNow {
  head?: { now_local?: string; weekday?: string; part_of_day?: string };
  attention?: AttentionItem[];
  tensions?: TensionItem[];
  degraded?: DegradedItem[];
}

interface ProposalRow {
  id: string;
  title?: string;
  tool_name?: string;
  action?: string;
  status?: string;
  actor?: string;
  created_at?: string;
}

function useSight(): { status: ZoneStatus; data: SightNow | null } {
  const [status, setStatus] = useState<ZoneStatus>("loading");
  const [data, setData] = useState<SightNow | null>(null);
  const load = useCallback(async () => {
    try {
      const d = await api.get<SightNow>("/api/v1/sight/now");
      setData(d);
      setStatus("ready");
    } catch (e) {
      setStatus(e instanceof ApiError && e.status === 404 ? "missing" : "error");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  return { status, data };
}

function useProposals(): {
  status: ZoneStatus;
  pending: ProposalRow[];
  approved: ProposalRow[];
  busyId: string | null;
  lastError: string | null;
  act: (id: string, op: "approve" | "reject" | "undo") => Promise<void>;
} {
  const [status, setStatus] = useState<ZoneStatus>("loading");
  const [pending, setPending] = useState<ProposalRow[]>([]);
  const [approved, setApproved] = useState<ProposalRow[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [p, a] = await Promise.all([
        api.get<ProposalRow[]>("/api/v1/proposals?status=pending"),
        api.get<ProposalRow[]>("/api/v1/proposals?status=approved"),
      ]);
      setPending(Array.isArray(p) ? p : []);
      setApproved(Array.isArray(a) ? a : []);
      setStatus("ready");
    } catch (e) {
      setStatus(e instanceof ApiError && e.status === 404 ? "missing" : "error");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const act = useCallback(
    async (id: string, op: "approve" | "reject" | "undo") => {
      setBusyId(id);
      setLastError(null);
      try {
        await api.post(`/api/v1/proposals/${id}/${op}`, {});
      } catch (e) {
        setLastError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyId(null);
        await reload();
      }
    },
    [reload],
  );

  return { status, pending, approved, busyId, lastError, act };
}

const STATUS_TEXT: Record<ZoneStatus, string> = {
  loading: "读取中…",
  ready: "已接入",
  missing: "未就绪",
  error: "暂不可用",
};

export default function CollabApp() {
  const sight = useSight();
  const proposals = useProposals();

  return (
    <section className="collab-surface" data-testid="collab-root" aria-label="共处工作台">
      <header className="collab-status" data-testid="collab-status">
        <span className="collab-status__dot" aria-hidden="true" />
        <strong>AI 在场</strong>
        <span className="collab-status__meta">
          {sight.data?.head?.part_of_day ? `${sight.data.head.part_of_day} · ` : ""}
          处境：{STATUS_TEXT[sight.status]} · 提案：{STATUS_TEXT[proposals.status]}
        </span>
        <span className="collab-status__hint">v1 灰盒 · 数据未接入时如实空态，不造假</span>
      </header>

      <div className="collab-grid">
        <section className="collab-card" aria-label="它看见的">
          <h3 className="collab-card__title">它看见的</h3>
          {sight.status === "missing" && (
            <p className="collab-empty">视野数据未就绪（等待 Sight 层 /sight/now）</p>
          )}
          {sight.status === "error" && (
            <p className="collab-empty collab-empty--warn">视野暂不可用（接口异常）</p>
          )}
          {sight.status === "loading" && <p className="collab-empty">读取中…</p>}
          {sight.status === "ready" && sight.data && (
            <div className="collab-sight">
              {sight.data.tensions?.length ? (
                <ul className="collab-list">
                  {sight.data.tensions.map((t) => (
                    <li key={t.id ?? t.summary ?? t.kind}>
                      {t.summary ?? t.kind}
                      {t.severity ? ` · ${t.severity}` : ""}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="collab-empty">暂无张力</p>
              )}
              {sight.data.attention?.length ? (
                <ul className="collab-list collab-list--attention">
                  {sight.data.attention.map((a, i) => (
                    <li key={`${a.kind ?? "item"}-${i}`}>{a.title ?? a.kind}</li>
                  ))}
                </ul>
              ) : null}
              {sight.data.degraded?.length ? (
                <p className="collab-empty collab-empty--warn">
                  降级：
                  {sight.data.degraded.map((d) => `${d.source ?? "?"}(${d.reason ?? "?"})`).join(" · ")}
                </p>
              ) : null}
            </div>
          )}
        </section>

        <section className="collab-card" aria-label="它想说的">
          <h3 className="collab-card__title">它想说的</h3>
          {proposals.status === "missing" && (
            <p className="collab-empty">提案层未接线（B1 施工中）</p>
          )}
          {proposals.status === "error" && (
            <p className="collab-empty collab-empty--warn">提案列表暂不可用</p>
          )}
          {proposals.status === "loading" && <p className="collab-empty">读取中…</p>}
          {proposals.lastError && (
            <p className="collab-empty collab-empty--warn" data-testid="proposal-error">
              操作失败：{proposals.lastError}
            </p>
          )}
          {proposals.status === "ready" && (
            <div className="collab-groups">
              {proposals.pending.length ? (
                <ul className="collab-proposals">
                  {proposals.pending.map((p) => (
                    <li key={p.id} className="collab-proposal" data-testid="proposal-row">
                      <span className="collab-proposal__title">{p.title ?? p.tool_name ?? p.id}</span>
                      <span className="collab-proposal__meta">
                        待签 · {p.action ?? "?"} · {p.actor ?? "?"}
                      </span>
                      <span className="collab-proposal__ops">
                        <button
                          type="button"
                          className="collab-btn collab-btn--primary"
                          disabled={proposals.busyId === p.id}
                          onClick={() => void proposals.act(p.id, "approve")}
                        >
                          签字
                        </button>
                        <button
                          type="button"
                          className="collab-btn"
                          disabled={proposals.busyId === p.id}
                          onClick={() => void proposals.act(p.id, "reject")}
                        >
                          驳回
                        </button>
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="collab-empty">暂无待签字</p>
              )}
              {proposals.approved.length ? (
                <>
                  <h4 className="collab-subtitle">已签（可撤销）</h4>
                  <ul className="collab-proposals">
                    {proposals.approved.map((p) => (
                      <li key={p.id} className="collab-proposal" data-testid="proposal-row-approved">
                        <span className="collab-proposal__title">{p.title ?? p.tool_name ?? p.id}</span>
                        <span className="collab-proposal__meta">
                          已签 · {p.action ?? "?"} · {p.actor ?? "?"}
                        </span>
                        <span className="collab-proposal__ops">
                          <button
                            type="button"
                            className="collab-btn collab-btn--primary"
                            disabled={proposals.busyId === p.id}
                            onClick={() => void proposals.act(p.id, "undo")}
                          >
                            撤销
                          </button>
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
            </div>
          )}
        </section>

        <section className="collab-card collab-card--timeline" aria-label="它做过的">
          <h3 className="collab-card__title">它做过的</h3>
          <p className="collab-empty">
            动作时间线 v1 只读：签字/撤销结果即时回显（SSE 推送接 v2，见 T10 备注）
          </p>
        </section>
      </div>
    </section>
  );
}
