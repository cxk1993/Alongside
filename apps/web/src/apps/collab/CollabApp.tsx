/**
 * 共处工作台 v1.2（T10 接真 proposals / sight；T25 提案详情抽屉）。
 *
 * 三个面（每区单一职责）：
 *   左 = 它看见的（Sight：head + tensions + attention + degraded 诚实降级）
 *   右 = 它想说的（提案卡流：GET /api/v1/proposals?status=pending；签字/驳回/撤销走真 API）
 *   底 = 它做过的（时间线，撤销结果从接口 detail 回显）
 * 顶 = 在场状态条。
 * 侧 = ★ T25 详情抽屉（GET /proposals/{id} 全字段：执行目标/参数/执行证据/撤销回执）。
 *
 * ★ 纪律：不造假数据、不装深度（判据 16/25）。接口没接就如实显示「未就绪」。
 * ★ 本组件同时作为桌面级背景面（desktop.widget 插槽）与普通窗口两种形态渲染。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent } from "react";

import { api, ApiError } from "@/shared/api/client";
import { ProposalDetail } from "./ProposalDetail";
import { actorLabel, hhmm, undoNote, type ProposalRow } from "./proposalFormat";
import { BeforePreview } from "./proposalView";
import { useProposalStream } from "./useProposalStream";
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

interface TimelineEntry {
  key: string;
  at: string;
  label: "已执行" | "已撤销";
  title: string;
  /** ★ T25：时间线也点得开——「我签过的那个，到底干成了什么」要能回查。 */
  id: string;
}

/**
 * ★ T22：「它做过的」= 已执行的（approved 且带 executed_at）+ 已撤销的，按时间倒序取最近几条。
 *
 * 为什么把「已撤销」也算进来：撤销是这套系统里唯一**可回看的"做完"证据**。
 * 只列 approved 的话，用户一点撤销，那条就从界面上消失了——他会怀疑「到底撤没撤」。
 * 只列 undone 的话，签字执行过的动作又看不见。两路合起来才是"做过什么"。
 */
function buildTimeline(
  approved: ProposalRow[],
  undone: ProposalRow[],
  limit = 6,
): TimelineEntry[] {
  const rows: TimelineEntry[] = [];
  for (const p of approved) {
    rows.push({
      key: `a-${p.id}`,
      at: p.executed_at ?? p.created_at ?? "",
      label: "已执行",
      title: p.title ?? p.tool_name ?? p.id,
      id: p.id,
    });
  }
  for (const p of undone) {
    rows.push({
      key: `u-${p.id}`,
      at: p.undone_at ?? p.executed_at ?? p.created_at ?? "",
      label: "已撤销",
      title: p.title ?? p.tool_name ?? p.id,
      id: p.id,
    });
  }
  rows.sort((x, y) => (y.at || "").localeCompare(x.at || ""));
  return rows.slice(0, limit);
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
  /** ★ T22：「它做过的」——已撤销的提案（撤销是这一面唯一可回看的"做完"证据）。 */
  undone: ProposalRow[];
  busyId: string | null;
  lastError: string | null;
  /** ★ T27：返回是否成功——签字成功要把「已签」区自动展开（否则卡片像凭空消失）。 */
  act: (id: string, op: "approve" | "reject" | "undo") => Promise<boolean>;
  /** ★ T21：给 SSE 推送用（事件到了就重拉一次，不做本地增量拼接——列表是唯一真相）。 */
  reload: () => Promise<void>;
  /** ★ T25：每次成功重拉 +1 —— 详情抽屉拿它当「该刷新了」的信号，避免抽屉比卡片旧。 */
  version: number;
} {
  const [status, setStatus] = useState<ZoneStatus>("loading");
  const [pending, setPending] = useState<ProposalRow[]>([]);
  const [approved, setApproved] = useState<ProposalRow[]>([]);
  const [undone, setUndone] = useState<ProposalRow[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  const reload = useCallback(async () => {
    try {
      const [p, a, u] = await Promise.all([
        api.get<ProposalRow[]>("/api/v1/proposals?status=pending"),
        api.get<ProposalRow[]>("/api/v1/proposals?status=approved"),
        // ★ T22：撤销也是「做过的」——不拉这一路，撤销在界面上就等于「凭空消失」。
        api.get<ProposalRow[]>("/api/v1/proposals?status=undone"),
      ]);
      setPending(Array.isArray(p) ? p : []);
      setApproved(Array.isArray(a) ? a : []);
      setUndone(Array.isArray(u) ? u : []);
      setStatus("ready");
      setVersion((v) => v + 1);
    } catch (e) {
      setStatus(e instanceof ApiError && e.status === 404 ? "missing" : "error");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const act = useCallback(
    async (id: string, op: "approve" | "reject" | "undo"): Promise<boolean> => {
      setBusyId(id);
      setLastError(null);
      let ok = false;
      try {
        await api.post(`/api/v1/proposals/${id}/${op}`, {});
        ok = true;
      } catch (e) {
        setLastError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyId(null);
        await reload();
      }
      return ok;
    },
    [reload],
  );

  return { status, pending, approved, undone, busyId, lastError, act, reload, version };
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
  // ★ T25：详情抽屉当前的提案 id（null = 关闭）。抽屉自己发 GET，不复用列表快照。
  const [detailId, setDetailId] = useState<string | null>(null);
  /** ★ T27：「已签（可撤销）」默认收起。
   *  真栈里这一组长到 5+ 条（今晚验收探针全留在这里），演示第一屏会被历史淹没；
   *  收起只藏「展示」不藏「数据」——计数与「展开」按钮都在，点开就是全量。
   *  签字成功后自动展开：否则刚签的卡片移到收起区，人会觉得「它消失了」。 */
  const [approvedOpen, setApprovedOpen] = useState(false);
  /** ★ T27：抽屉关闭后焦点要回到打开它的按钮（键盘/读屏用户不至于掉到页首）。 */
  const openerRef = useRef<HTMLButtonElement | null>(null);
  const openDetail = (id: string) => (e: MouseEvent<HTMLButtonElement>) => {
    openerRef.current = e.currentTarget;
    setDetailId(id);
  };
  const closeDetail = useCallback(() => {
    setDetailId(null);
    const el = openerRef.current;
    openerRef.current = null;
    // 等抽屉真的从 DOM 卸掉再还焦点（同一帧里 focus 会被卸载覆盖）
    queueMicrotask(() => {
      if (el && document.contains(el)) el.focus();
    });
  }, []);
  // ★ T21：提案的四种状态变更是 SSE 推来的 → 收到就重拉（挂载时那次拉取仍然保留，
  //   它是「断线/无 EventSource 时」的兜底）。
  useProposalStream(proposals.reload);
  // ★ T22：时间线在渲染期推导（纯函数），不额外存一份状态——省掉「状态与列表不同步」这一类 bug。
  const timeline = buildTimeline(proposals.approved, proposals.undone);

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
                          onClick={() => {
                            void proposals.act(p.id, "approve").then((ok) => {
                              // ★ T27：签完立刻展开已签区——刚签的卡片进了收起组，看起来就是「没了」
                              if (ok) setApprovedOpen(true);
                            });
                          }}
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
                        {/* ★ T25：签字前核对「打到哪个端点、带什么参数」→ 打开详情抽屉 */}
                        <button
                          type="button"
                          className="collab-btn"
                          onClick={openDetail(p.id)}
                          data-testid="detail-btn"
                        >
                          详情
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
                  <h4 className="collab-subtitle collab-subtitle--row">
                    <span>
                      已签（可撤销）<span className="collab-count">{proposals.approved.length}</span>
                    </span>
                    {/* ★ T27：默认收起，但「有几条」永远看得见——收起的是展示，不是事实 */}
                    <button
                      type="button"
                      className="collab-btn"
                      onClick={() => setApprovedOpen((v) => !v)}
                      aria-expanded={approvedOpen}
                      data-testid="approved-toggle"
                    >
                      {approvedOpen ? "收起" : `展开 ${proposals.approved.length} 条`}
                    </button>
                  </h4>
                  {approvedOpen ? (
                    <ul className="collab-proposals">
                      {proposals.approved.map((p) => (
                        <li key={p.id} className="collab-proposal" data-testid="proposal-row-approved">
                          <span className="collab-proposal__title">{p.title ?? p.tool_name ?? p.id}</span>
                          <span className="collab-proposal__meta">
                            已签 · {p.action ?? "?"} · {actorLabel(p.actor)}
                          </span>
                          <span className="collab-proposal__ops">
                            {/* ★ T20 修正（契约 8ccef3b）：三种动作**都可撤销**——原契约
                                「create 不可撤销 409」已被实现 5cff08a 推翻并改口。
                                但语义不同，按钮必须说清撤掉的是什么，否则只是另一种假按钮。 */}
                            <button
                              type="button"
                              className="collab-btn collab-btn--primary"
                              disabled={proposals.busyId === p.id}
                              onClick={() => void proposals.act(p.id, "undo")}
                              data-testid="undo-btn"
                            >
                              {p.action === "create" ? "撤销（删掉刚建的那条）" : "撤销"}
                            </button>
                            {/* ★ T25：执行证据（executed_status / executed_id）在详情抽屉里，
                                摊在卡片上只是噪声。 */}
                            <button
                              type="button"
                              className="collab-btn"
                              onClick={openDetail(p.id)}
                              data-testid="detail-btn"
                            >
                              详情
                            </button>
                            <span className="collab-proposal__note">{undoNote(p.action)}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="collab-empty" data-testid="approved-collapsed">
                      已收起 —— 点「展开」回看已签与撤销（撤错了想复原，就在里面）。
                    </p>
                  )}
                </>
              ) : null}
            </div>
          )}
        </section>

        <section className="collab-card collab-card--timeline" aria-label="它做过的">
          <h3 className="collab-card__title">它做过的</h3>
          {/* ★ T22：真数据——已执行 + 已撤销，按时间倒序。撤销在列表里留痕，}
              人才不用怀疑「到底撤没撤」。 */}
          {timeline.length ? (
            <ul className="collab-list collab-timeline" data-testid="collab-timeline">
              {timeline.map((t) => (
                <li key={t.key} className="collab-timeline__row">
                  <span
                    className={`collab-tag${t.label === "已撤销" ? " collab-tag--undo" : ""}`}
                  >
                    {t.label}
                  </span>
                  <span className="collab-timeline__at">{hhmm(t.at) || "时间未知"}</span>
                  <span className="collab-timeline__title">{t.title}</span>
                  {/* ★ T25：时间线点得开——「这条当时到底打通没有」要能回查 */}
                  <button
                    type="button"
                    className="collab-btn collab-timeline__open"
                    onClick={openDetail(t.id)}
                    data-testid="detail-btn"
                  >
                    详情
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="collab-empty">
              {proposals.status === "ready" ? "还没有签过或撤过的东西" : "读取中…"}
            </p>
          )}
        </section>
      </div>

      {/* ★ T25：抽屉做成 surface 内的绝对定位侧栏（不是 modal）——核对证据时
          签字/撤销仍可点，不用关窗再开窗。 */}
      {detailId ? (
        <ProposalDetail
          proposalId={detailId}
          onClose={closeDetail}
          refreshKey={proposals.version}
        />
      ) : null}
    </section>
  );
}
