/**
 * 共处工作台 v1.2（T10 接真 proposals / sight；T25 提案详情抽屉）。
 *
 * 三个面（每区单一职责）：
 *   左 = 它看见的（Sight：head + tensions + attention + degraded 诚实降级）
 *   右 = 它想说的（提案卡流：GET /api/v1/proposals?status=pending；签字/驳回/撤销走真 API）
 *   底 = 它做过的（时间线，撤销结果从接口 detail 回显；T29 起含已驳回）
 * 顶 = 在场状态条。
 * 侧 = ★ T25 详情抽屉（GET /proposals/{id} 全字段：执行目标/参数/执行证据/撤销回执）。
 *
 * ★ 纪律：不造假数据、不装深度（判据 16/25）。接口没接就如实显示「未就绪」。
 * ★ 本组件同时作为桌面级背景面（desktop.widget 插槽）与普通窗口两种形态渲染。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent } from "react";

import { api, ApiError } from "@/shared/api/client";
import { useSightNow } from "@/shared/api/now";
import { useDesktopStore } from "../../kernel/store";
import { ActivityStrip } from "./ActivityStrip";
import { CapabilityBar } from "./CapabilityBar";
import { ProposalDetail } from "./ProposalDetail";
import { actorLabel, hhmm, undoNote, type ProposalRow } from "./proposalFormat";

// ─────────── ★ 2026-11-22 比丢：后端枚举不进用户界面 ───────────
// 背景（24-前端灰屏·联合调研 §九）：AI 工作面里直接印着
//   `降级：finance(not_implemented) · health(not_implemented) · …`
//   `late_night · 04:26 采样`
// 用户读不出「not_implemented」是什么，也读不出 late_night 是几点。
// 这里只做**展示层映射**：改的是文案，不动数据、不动逻辑。
// 判据 5：声明与实现说同一句话 —— 后端说「没做」，界面就该说「尚未接入」，而不是把枚举裸印出来。

const PART_OF_DAY_LABEL: Record<string, string> = {
  morning: "上午",
  forenoon: "上午",
  noon: "中午",
  afternoon: "下午",
  evening: "傍晚",
  night: "夜间",
  late_night: "深夜",
};

const SOURCE_LABEL: Record<string, string> = {
  calendar: "日程",
  todo: "待办",
  habits: "习惯",
  finance: "理财",
  health: "健康",
  notes: "笔记",
  diary: "日记",
  course: "课程",
  review: "复盘",
  dashboard: "总览",
  assets: "资产",
};

const REASON_LABEL: Record<string, string> = {
  not_implemented: "尚未接入",
  timeout: "超时未响应",
  error: "读取失败",
  unavailable: "暂时不可用",
  unauthorized: "无权限",
};

/** 把 degraded[] 翻成两句话：一句给眼睛（summary），一句给悬停/展开（detail）。 */
function degradedText(items: { source?: string; reason?: string }[]): {
  summary: string;
  detail: string;
} {
  const byReason = new Map<string, string[]>();
  for (const d of items) {
    const r = d.reason ?? "unknown";
    const name = SOURCE_LABEL[d.source ?? ""] ?? d.source ?? "未知";
    const bucket = byReason.get(r);
    if (bucket) bucket.push(name);
    else byReason.set(r, [name]);
  }
  const detail = [...byReason.entries()]
    .map(([reason, names]) => `${names.join("、")}${REASON_LABEL[reason] ?? reason}`)
    .join("；");
  return { summary: `${items.length} 个数据源未就绪`, detail };
}
import { BeforePreview } from "./proposalView";
import { useProposalStream } from "./useProposalStream";
import "./collab.css";

type ZoneStatus = "loading" | "ready" | "missing" | "error";

// ★ P0（2026-11-22）：Sight 取数统一到 shared/api/now —— 与顶栏同一口径、同一采样时刻。

interface TimelineEntry {
  key: string;
  at: string;
  label: "已执行" | "已撤销" | "已驳回";
  title: string;
  /** ★ T29：时间戳的语义前缀。驳回没有服务端时间（提案表只有 status，无 rejected_at），
   *  只能拿提出时间；不加「提出于」就等于把提出时间冒充成驳回时间。 */
  atNote?: string;
  /** ★ T25：时间线也点得开——「我签过的那个，到底干成了什么」要能回查。 */
  id: string;
}

/**
 * ★ T22：「它做过的」= 已执行的（approved 且带 executed_at）+ 已撤销的，按时间倒序取最近几条。
 * ★ T29：再加「已驳回」——驳回落进这里，否则人否掉一条建议，屏幕上**一点痕迹都没有**。
 *
 * 为什么把「已撤销」也算进来：撤销是这套系统里唯一**可回看的"做完"证据**。
 * 只列 approved 的话，用户一点撤销，那条就从界面上消失了——他会怀疑「到底撤没撤」。
 * 只列 undone 的话，签字执行过的动作又看不见。两路合起来才是"做过什么"。
 *
 * 为什么驳回也进来：驳回是人的**决定**，不是「没发生」。它和执行同级留痕，
 * 但标签必须分开（已驳回 ≠ 已执行）——否则时间线会把「我否掉的」读成「它做过的」。
 */
function buildTimeline(
  approved: ProposalRow[],
  undone: ProposalRow[],
  rejected: ProposalRow[] = [],
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
  // ★ T29：驳回。服务端没记驳回时间（契约 ProposalOut 无 rejected_at）——
  //   只拿得到提出时间，所以前缀写「提出于」，不冒充「驳回于」。
  for (const p of rejected) {
    rows.push({
      key: `r-${p.id}`,
      at: p.created_at ?? "",
      atNote: "提出于",
      label: "已驳回",
      title: p.title ?? p.tool_name ?? p.id,
      id: p.id,
    });
  }
  rows.sort((x, y) => (y.at || "").localeCompare(x.at || ""));
  return rows.slice(0, limit);
}

function useProposals(): {
  status: ZoneStatus;
  pending: ProposalRow[];
  approved: ProposalRow[];
  /** ★ T22：「它做过的」——已撤销的提案（撤销是这一面唯一可回看的"做完"证据）。 */
  undone: ProposalRow[];
  /** ★ T29：已驳回的提案（人的否决也是留痕，不是「没发生」）。 */
  rejected: ProposalRow[];
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
  /** ★ T29：驳回后的提案。不拉这一路，人点了「驳回」屏幕上就没有任何痕迹。 */
  const [rejected, setRejected] = useState<ProposalRow[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  const reload = useCallback(async () => {
    try {
      const [p, a, u, r] = await Promise.all([
        api.get<ProposalRow[]>("/api/v1/proposals?status=pending"),
        api.get<ProposalRow[]>("/api/v1/proposals?status=approved"),
        // ★ T22：撤销也是「做过的」——不拉这一路，撤销在界面上就等于「凭空消失」。
        api.get<ProposalRow[]>("/api/v1/proposals?status=undone"),
        // ★ T29：驳回同理——否掉的建议也要留痕，否则「它做过的」是半份记录。
        api.get<ProposalRow[]>("/api/v1/proposals?status=rejected"),
      ]);
      setPending(Array.isArray(p) ? p : []);
      setApproved(Array.isArray(a) ? a : []);
      setUndone(Array.isArray(u) ? u : []);
      // 只收真正 rejected 的行：接口已按状态筛过，但列表替身/老数据可能不带 status，
      // 不设这道闸就会把待签卡片重复摆进时间线（同一份数据两处出现＝另一种造假）。
      setRejected(Array.isArray(r) ? r.filter((row) => row.status === "rejected") : []);
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

  return { status, pending, approved, undone, rejected, busyId, lastError, act, reload, version };
}

const STATUS_TEXT: Record<ZoneStatus, string> = {
  loading: "读取中…",
  ready: "已接入",
  missing: "未就绪",
  error: "暂不可用",
};

export default function CollabApp() {
  const sight = useSightNow();
  const proposals = useProposals();
  /** ★ P0-c：能力条点一下就开窗——与 Ctrl+K 里的「模块」命令同一个动作（store.openWindow）。 */
  const openWindow = useDesktopStore((s) => s.openWindow);
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
  const timeline = buildTimeline(proposals.approved, proposals.undone, proposals.rejected);

  return (
    <section className="collab-surface" data-testid="collab-root" aria-label="共处工作台">
      <header className="collab-status" data-testid="collab-status">
        <span className="collab-status__dot" aria-hidden="true" />
        <strong>AI 在场</strong>
        <span className="collab-status__meta">
          {sight.data?.head?.part_of_day
            ? `${PART_OF_DAY_LABEL[sight.data.head.part_of_day] ?? sight.data.head.part_of_day} · `
            : ""}
          {/* ★ T28：显示这一份视野的采样时刻——它是每次现算的，不是常量（否则「在场」只是装饰） */}
          {hhmm(sight.data?.head?.now_local) ? (
            <span data-testid="sight-sampled-at">{hhmm(sight.data?.head?.now_local)} 采样 · </span>
          ) : null}
          处境：{STATUS_TEXT[sight.status]} · 提案：{STATUS_TEXT[proposals.status]}
        </span>
        {/* ★ 2026-11-22 比丢：删「v1 灰盒 · 数据未接入时如实空态，不造假」。
            那句话是写给评审看的（解释我们为什么不编数据），不是给用户看的；
            留在界面上只会让主人以为「这是个半成品」。
            诚实空态仍然由各区的「暂无 / 未就绪」表达，不需要自证。 */}
      </header>

      {/* ★ P0-c：入口与活动都在状态条正下方 —— 一眼看得见，不用先学 Ctrl+K */}
      <CapabilityBar onOpen={openWindow} />
      <ActivityStrip />

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
                      {/* ★ T28：服务端给了动作建议就上屏；没给就不编 */}
                      {t.suggested_action ? (
                        <span className="collab-proposal__note collab-tension__action">
                          {t.suggested_action}
                        </span>
                      ) : null}
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
                /* ★ 2026-11-22 比丢：不再裸印 `finance(not_implemented)`。
                   人看到的是一句人话（N 个数据源未就绪），把明细放进 title，
                   悬停才看得到「理财、健康…尚未接入」。 */
                <p
                  className="collab-empty collab-empty--warn"
                  title={degradedText(sight.data.degraded).detail}
                  data-testid="collab-degraded"
                >
                  {degradedText(sight.data.degraded).summary}
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
                                              {hhmm(p.created_at) ? ` · 提出于 ${hhmm(p.created_at)}` : ""}
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
                            {hhmm(p.executed_at) ? ` · 执行于 ${hhmm(p.executed_at)}` : ""}
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
                    className={`collab-tag${
                      t.label === "已撤销"
                        ? " collab-tag--undo"
                        : t.label === "已驳回"
                          ? " collab-tag--reject"
                          : ""
                    }`}
                  >
                    {t.label}
                  </span>
                  <span className="collab-timeline__at">
                    {t.atNote ? `${t.atNote} ` : ""}
                    {hhmm(t.at) || "时间未知"}
                  </span>
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
            /* ★ 2026-11-22（汐瑶 · 三态收口）：此前 error/missing 一律显示「读取中…」，
               与「真的没有记录」分不开、且永远不结束。四态各说各的，空态只在 ready 才说。 */
            <p
              className={`collab-empty${proposals.status === "error" ? " collab-empty--warn" : ""}`}
              data-testid="collab-timeline-empty"
            >
              {proposals.status === "ready"
                ? "还没有签过、撤过或否过的东西"
                : proposals.status === "missing"
                  ? "数据源未接上 —— 不是「没有记录」"
                  : proposals.status === "error"
                    ? "没读到 —— 接口异常，不是「没有记录」"
                    : "读取中…"}
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
