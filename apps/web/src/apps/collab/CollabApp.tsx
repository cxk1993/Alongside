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
  /** ★ T20：变更前快照。契约口径：create 必为 null；null 就是**没有快照**，不是空对象。 */
  before?: Record<string, unknown> | null;
  executed_at?: string | null;
  undone_at?: string | null;
}

/**
 * ★ T20：actor 要一眼分得开（契约 11 §二）。
 * 原样显示 `mcp:nx-t10-ui` 这类内部标识对人没有意义，但也不能丢掉「是谁」——
 * 折中：保留前缀语义，人读得懂的部分放前面。
 */
function actorLabel(actor?: string): string {
  if (!actor) return "来源不明";
  if (actor === "human") return "你签发";
  if (actor.startsWith("mcp:")) return `AI（${actor.slice(4) || "未命名 PAT"}）`;
  return actor;
}

/**
 * ★ T20：变更前快照预览。
 *
 * 三条诚实规则（契约 11 §二/§三）：
 *   1. `action=create` → 没有"变更前"，**不渲染空对象**，明说「新建」；
 *   2. `before === null`（delete/update 却没有快照）→ 如实说「服务端未提供」，
 *      而不是留一个空白框让人以为快照是空的；
 *   3. 有快照 → 默认折叠（卡片上已经有一行标题，快照是签字时才要细看的东西）。
 */
function BeforePreview({ action, before }: { action?: string; before?: Record<string, unknown> | null }) {
  if (action === "create") {
    return <span className="collab-proposal__note">新建 · 无变更前快照</span>;
  }
  if (!before) {
    return <span className="collab-proposal__note">变更前快照：服务端未提供</span>;
  }
  return (
    <details className="collab-before">
      <summary>变更前快照</summary>
      <pre className="collab-before__body">{JSON.stringify(before, null, 2)}</pre>
    </details>
  );
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
                                              待签 · {p.action ?? "?"} · {actorLabel(p.actor)}
                                            </span>
                                            <BeforePreview action={p.action} before={p.before} />
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
                                                  已签 · {p.action ?? "?"} · {actorLabel(p.actor)}
                                                </span>
                                                <span className="collab-proposal__ops">
                                                  {p.action === "create" ? (
                                                    // ★ T20：契约 11 §三——create 没有 before 可回放，服务端会 409。
                                                    //   所以这里**置灰 + 明说**，而不是做出一个按下去必然报错的按钮。
                                                    <>
                                                      <button
                                                        type="button"
                                                        className="collab-btn"
                                                        disabled
                                                        title="此操作不可撤销"
                                                        data-testid="undo-disabled"
                                                      >
                                                        撤销
                                                      </button>
                                                      <span className="collab-proposal__note">
                                                        新建的提案不可撤销（没有可回放的快照）
                                                      </span>
                                                    </>
                                                  ) : (
                                                    <button
                                                      type="button"
                                                      className="collab-btn collab-btn--primary"
                                                      disabled={proposals.busyId === p.id}
                                                      onClick={() => void proposals.act(p.id, "undo")}
                                                    >
                                                      撤销
                                                    </button>
                                                  )}
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
