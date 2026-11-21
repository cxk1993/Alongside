/**
 * 共处工作台 · v1 灰盒（主人 2026-11-21 定调：AI 是一等用户，人坐在旁边）。
 *
 * 三个面（每区单一职责）：
 *   左 = 它看见的（Sight 处境，读 /api/v1/sight/now；未就绪就诚实空态）
 *   右 = 它想说的（提案卡流，读 /api/v1/proposals?status=pending；B1 未接线就诚实空态）
 *   底 = 它做过的（动作时间线，v1 先只读；撤销随提案层）
 * 顶 = 在场状态条。
 *
 * ★ 纪律：不造假数据、不装深度（判据 16/25）。接口没接就如实显示「未就绪」。
 * ★ 本组件同时作为桌面级背景面（desktop.widget 插槽）与普通窗口两种形态渲染。
 */
import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "@/shared/api/client";
import "./collab.css";

type ZoneStatus = "loading" | "ready" | "missing" | "error";

interface ProposalRow {
  id: string;
  title?: string;
  tool_name?: string;
  action?: string;
  status?: string;
  actor?: string;
  created_at?: string;
}

interface SightNow {
  time?: { now?: string; free_slots?: unknown[] };
  attention?: Array<{ kind?: string; text?: string; age_days?: number }>;
  tensions?: string[];
  recent?: unknown[];
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

function useProposals(): { status: ZoneStatus; rows: ProposalRow[] } {
  const [status, setStatus] = useState<ZoneStatus>("loading");
  const [rows, setRows] = useState<ProposalRow[]>([]);
  const load = useCallback(async () => {
    try {
      const d = await api.get<{ items?: ProposalRow[] }>(
        "/api/v1/proposals?status=pending",
      );
      setRows(d.items ?? []);
      setStatus("ready");
    } catch (e) {
      setStatus(e instanceof ApiError && e.status === 404 ? "missing" : "error");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  return { status, rows };
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
                    <li key={t}>{t}</li>
                  ))}
                </ul>
              ) : (
                <p className="collab-empty">暂无张力</p>
              )}
              {sight.data.attention?.length ? (
                <ul className="collab-list collab-list--attention">
                  {sight.data.attention.map((a, i) => (
                    <li key={`${a.kind ?? "item"}-${i}`}>
                      {a.text ?? a.kind}
                      {typeof a.age_days === "number" ? ` · 已挂 ${a.age_days} 天` : ""}
                    </li>
                  ))}
                </ul>
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
          {proposals.status === "ready" &&
            (proposals.rows.length ? (
              <ul className="collab-proposals">
                {proposals.rows.map((p) => (
                  <li key={p.id} className="collab-proposal" data-testid="proposal-row">
                    <span className="collab-proposal__title">
                      {p.title ?? p.tool_name ?? p.id}
                    </span>
                    <span className="collab-proposal__meta">
                      {p.action ?? "?"} · {p.actor ?? "?"}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="collab-empty">暂无待签字</p>
            ))}
        </section>

        <section className="collab-card collab-card--timeline" aria-label="它做过的">
          <h3 className="collab-card__title">它做过的</h3>
          <p className="collab-empty">
            动作时间线 v1 只读：随提案层接线后显示「做了什么 · 为什么 · [撤回]」
          </p>
        </section>
      </div>
    </section>
  );
}
