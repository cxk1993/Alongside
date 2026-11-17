import { useQuery } from "@tanstack/react-query";
import { agentsApi } from "../api";

/** 概览页卡片：agent 启用数 / 任务块进度。空数据也要稳。 */
export default function DashboardCard() {
  const { data } = useQuery({
    queryKey: ["agents", "summary"],
    queryFn: () => agentsApi.summary(),
    retry: 1,
  });

  const agentsEnabled = data?.agents_enabled ?? 0;
  const agentsTotal = data?.agents_total ?? 0;
  const tasksTotal = data?.tasks_total ?? 0;
  const done = data?.done ?? 0;
  const pending = (data?.draft ?? 0) + (data?.queued ?? 0) + (data?.running ?? 0);

  return (
    <div className="card">
      <div className="card__title">AI 编排</div>
      <div className="card__body">
        <div className="agents-dash__stats">
          <div className="agents-dash__stat">
            <span className="agents-dash__num">
              {agentsTotal ? `${agentsEnabled}/${agentsTotal}` : "—"}
            </span>
            <span className="agents-dash__lbl">启用智能体</span>
          </div>
          <div className="agents-dash__stat">
            <span className="agents-dash__num">{tasksTotal ? `${done}/${tasksTotal}` : "—"}</span>
            <span className="agents-dash__lbl">任务完成</span>
          </div>
          <div className="agents-dash__stat">
            <span className="agents-dash__num">{pending}</span>
            <span className="agents-dash__lbl">进行中</span>
          </div>
        </div>
        {agentsTotal === 0 && tasksTotal === 0 ? (
          <div className="tiny" style={{ color: "var(--txt-faint)" }}>
            还没有智能体 / 任务
          </div>
        ) : null}
      </div>
    </div>
  );
}
