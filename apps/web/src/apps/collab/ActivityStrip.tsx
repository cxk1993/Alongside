import { useMemo, useState } from "react";

import { useDesktopStore } from "../../kernel/store";
import { collectTopics } from "./capabilities";
import { useEventStream, type ActivityEvent } from "./useEventStream";

/**
 * ★ P0-c：活动条「它正在做什么」。
 *
 * 为什么不并进「它做过的」：那张卡是**提案的终态**（已执行/已撤销/已驳回）。
 * 这里是**事件流**：AI 刚碰了日历、刚写了日记、刚读了待办——很多动作根本不产生提案。
 * 两件事混在一张卡里，人会分不清「发生过」和「我签过」。
 *
 * 展示纪律：
 *   - 未连上就写「实时未连接」，不写「暂无活动」（后者会被读成「它什么都没做」）；
 *   - 没有事件且有连接 → 「还没有事件」；
 *   - topic 原样显示（`proposals.proposal.created` 这类），不做中文翻译编造，
 *     但把 `topic` 的最后两段作为可读标签（`created` 之类）也不能乱猜语义 → 只显示 topic + source + 摘要。
 */

/** 折叠态显示最近几条（一行放得下 1 条完整 + 计数）。 */
const COLLAPSED = 1;

function timeOf(at: string): string {
  // 服务端 at 是 ISO；只截 HH:MM，解析不了就不显示（不编时间）。
  const m = /T(\d{2}:\d{2})/.exec(at);
  return m ? m[1] : "";
}

function Row({ e }: { e: ActivityEvent }) {
  return (
    <li className="collab-act__row" data-testid="activity-row">
      <span className="collab-act__at">{timeOf(e.at) || "时间未知"}</span>
      <span className="collab-act__topic" title={e.topic}>
        {e.topic}
      </span>
      {e.summary ? <span className="collab-act__sum">{e.summary}</span> : null}
      {e.source ? <span className="collab-act__src">{e.source}</span> : null}
    </li>
  );
}

export function ActivityStrip() {
  const modules = useDesktopStore((s) => s.modules);
  const topics = useMemo(() => collectTopics(modules), [modules]);
  const { events, status } = useEventStream(topics);
  const [open, setOpen] = useState(false);

  const shown = open ? events : events.slice(0, COLLAPSED);

  const statusText =
    status === "live" ? "实时" : status === "offline" ? "实时未连接" : "连接中";

  return (
    <div
      className={`collab-act${open ? " is-open" : ""}`}
      data-testid="collab-activity"
      data-status={status}
      role="region"
      aria-label="它正在做什么"
    >
      <div className="collab-act__head">
        <span className="collab-act__title">它正在做什么</span>
        <span
          className={`collab-act__dot collab-act__dot--${status}`}
          aria-hidden="true"
          data-testid="activity-status"
          title={statusText}
        />
        <span className="collab-act__state">{statusText}</span>
        {events.length ? (
          <span className="collab-act__count">{events.length} 条</span>
        ) : (
          <span className="collab-act__count">
            {status === "offline" ? "事件流不可用（不影响其他区域）" : "还没有事件"}
          </span>
        )}
        {events.length > COLLAPSED ? (
          <button
            type="button"
            className="collab-act__toggle"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            data-testid="activity-toggle"
          >
            {open ? "收起" : "展开"}
          </button>
        ) : null}
      </div>
      {shown.length ? (
        <ul className="collab-act__list">
          {shown.map((e) => (
            <Row key={e.eid} e={e} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}