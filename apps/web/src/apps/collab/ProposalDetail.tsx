/**
 * ★ T25：提案详情抽屉（GET /api/v1/proposals/{id} 全字段）。
 *
 * 为什么要有它：卡片上只有标题/动作/来源/快照这类「签字时够用」的摘要；
 * 而「它到底做了什么」的证据（method+path+payload、executed_id、executed_status、
 * undo_result）在列表里根本看不见 —— 签字之后想核对，此前只能自己去敲 curl。
 *
 * 三条纪律：
 *   1. **以服务端当次返回为准**：抽屉自己发 GET，不拿列表快照硬凑（列表可能是几秒前的）；
 *   2. **缺失 ≠ 空**：字段没有 → 「服务端未返回」；null → 「null」；{} → 「无参数」；
 *   3. **失败如实说**：404 → 「服务端没有这条提案」；其它错误 → 错误行 + 重试按钮。
 *      不把错误渲染成空白，让人以为「这提案啥也没干」。
 */
import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { api, ApiError } from "@/shared/api/client";
import { actorLabel, undoNote, ymdhm } from "./proposalFormat";
import { BeforePreview, JsonField } from "./proposalView";

/** ProposalOut 全字段（services/api/modules/proposals/schema.py）。 */
export interface ProposalDetailData {
  id: string;
  title?: string;
  tool_name?: string;
  action?: string;
  method?: string;
  path?: string;
  payload?: Record<string, unknown> | null;
  before?: Record<string, unknown> | null;
  actor?: string;
  status?: string;
  created_at?: string;
  executed_at?: string | null;
  executed_id?: string | null;
  executed_status?: number | null;
  undone_at?: string | null;
  undo_result?: string | null;
}

type DetailState =
  | { kind: "loading" }
  | { kind: "ready"; data: ProposalDetailData }
  | { kind: "missing" }
  | { kind: "error"; message: string };

const STATUS_TEXT: Record<string, string> = {
  pending: "待签",
  approved: "已签（已执行）",
  undone: "已撤销",
  rejected: "已作废",
};

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="collab-detail__row">
      <span className="collab-detail__k">{k}</span>
      <span className="collab-detail__v">{children}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="collab-detail__section">
      <h4 className="collab-detail__h">{title}</h4>
      {children}
    </section>
  );
}

export function ProposalDetail({
  proposalId,
  onClose,
  refreshKey = 0,
}: {
  proposalId: string;
  onClose: () => void;
  /**
   * ★ 外部「提案列表已变」的信号（SSE 推送 / 签字 / 撤销后 +1）。
   * 不带它的话，抽屉会停在打开那一刻的快照：你刚在卡片上签了字，
   * 抽屉里还写着「尚未执行」——同一屏两块证据自相矛盾。
   */
  refreshKey?: number;
}) {
  const [state, setState] = useState<DetailState>({ kind: "loading" });
  const [nonce, setNonce] = useState(0);

  const load = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    setState({ kind: "loading" });
    api
      .get<ProposalDetailData>(`/api/v1/proposals/${encodeURIComponent(proposalId)}`)
      .then((d) => {
        if (alive) setState({ kind: "ready", data: d });
      })
      .catch((e: unknown) => {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) {
          setState({ kind: "missing" });
        } else {
          setState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
        }
      });
    return () => {
      alive = false;
    };
  }, [proposalId, nonce, refreshKey]);

  return (
    <aside className="collab-detail" data-testid="proposal-detail" aria-label="提案详情">
      <header className="collab-detail__head">
        <strong>提案详情</strong>
        <span className="collab-detail__id">{proposalId}</span>
        <button type="button" className="collab-btn" onClick={onClose} data-testid="detail-close">
          关闭
        </button>
      </header>

      <div className="collab-detail__body">
        {state.kind === "loading" && <p className="collab-empty">读取中…</p>}

        {state.kind === "missing" && (
          <p className="collab-empty collab-empty--warn" data-testid="detail-missing">
            服务端没有这条提案（404）——可能已被清理，或 id 不对。
          </p>
        )}

        {state.kind === "error" && (
          <div data-testid="detail-error">
            <p className="collab-empty collab-empty--warn">读取详情失败：{state.message}</p>
            <button type="button" className="collab-btn" onClick={load}>
              重试
            </button>
          </div>
        )}

        {state.kind === "ready" && <DetailBody d={state.data} />}
      </div>
    </aside>
  );
}

function DetailBody({ d }: { d: ProposalDetailData }) {
  const executed = Boolean(d.executed_at) || typeof d.executed_status === "number";
  return (
    <div className="collab-detail__grid">
      <Section title="这是什么">
        <Row k="标题">{d.title ?? "（服务端未返回标题）"}</Row>
        <Row k="状态" >
          <span data-testid="detail-status">{STATUS_TEXT[d.status ?? ""] ?? d.status ?? "未知"}</span>
        </Row>
        <Row k="动作">{d.action ?? "未知"}</Row>
        <Row k="来源">{actorLabel(d.actor)}</Row>
        <Row k="工具">
          <code>{d.tool_name ?? "未返回"}</code>
        </Row>
        <Row k="签发时间">{ymdhm(d.created_at) || "未返回"}</Row>
      </Section>

      <Section title="它要做的（执行目标）">
        {d.method && d.path ? (
          <Row k="请求">
            <code data-testid="detail-target">
              {d.method} {d.path}
            </code>
          </Row>
        ) : (
          <p className="collab-empty collab-empty--warn" data-testid="detail-target-missing">
            服务端未记录 method/path —— 这条无法执行（签字会 422「提案缺少 method/path」）
          </p>
        )}
        <div className="collab-detail__field" data-testid="detail-payload">
          <JsonField label="执行参数" value={d.payload} />
        </div>
        <div className="collab-detail__field" data-testid="detail-before">
          <BeforePreview action={d.action} before={d.before} />
        </div>
      </Section>

      <Section title="执行结果">
        {executed ? (
          <>
            <Row k="执行时间">{ymdhm(d.executed_at) || "未返回"}</Row>
            <Row k="返回状态">
              {typeof d.executed_status === "number" ? `HTTP ${d.executed_status}` : "未返回"}
            </Row>
            <Row k="产物 id">
              {d.executed_id ? (
                <code data-testid="detail-executed-id">{d.executed_id}</code>
              ) : (
                // 服务端 _extract_id 取不到就是 null——那不是「空字符串」，是「这次执行没产出 id」
                <span className="collab-proposal__note">无（执行结果里没有 id）</span>
              )}
            </Row>
          </>
        ) : (
          <p className="collab-empty" data-testid="detail-not-executed">
            尚未执行 —— 签字之后这里才会出现执行证据。
          </p>
        )}
      </Section>

      <Section title="撤销">
        {d.status === "undone" ? (
          <>
            <Row k="撤销时间">{ymdhm(d.undone_at) || "未返回"}</Row>
            <Row k="撤销语义">{undoNote(d.action) || "未知动作"}</Row>
            <Row k="撤销回执">
              {/* ★ 真栈实测（T25）：撤销 create 走 DELETE → 204 无正文 → 服务端把回执记成
                  空字符串 `""`。这不是「没记录」，是「记了，但回执本来就没有正文」——
                  两者混成一句「未记录」就是失真。分三档如实说。 */}
              {typeof d.undo_result === "string" && d.undo_result.length > 0 ? (
                <pre className="collab-before__body" data-testid="detail-undo-result">
                  {d.undo_result}
                </pre>
              ) : d.undo_result === "" ? (
                <span className="collab-proposal__note" data-testid="detail-undo-empty">
                  空回执 —— 服务端那次撤销返回无正文（如 HTTP 204）
                </span>
              ) : d.undo_result === null ? (
                <span className="collab-proposal__note">服务端未记录回执</span>
              ) : (
                <span className="collab-proposal__note">服务端未返回该字段</span>
              )}
            </Row>
          </>
        ) : (
          <p className="collab-empty" data-testid="detail-not-undone">
            {d.status === "approved" ? "未撤销。" : "还没到可撤销的阶段。"}
            {d.action ? ` ${undoNote(d.action)}` : ""}
          </p>
        )}
      </Section>
    </div>
  );
}