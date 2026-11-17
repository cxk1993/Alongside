import { useMutation, useQueryClient } from "@tanstack/react-query";
import { agentsApi, type Agent } from "./api";

interface Props {
  agent: Agent;
}

/** 一行 agent：名称/描述/能力 + 启用停用 + 删除。 */
export default function AgentRow({ agent }: Props) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["agents"] });

  const toggleMut = useMutation({
    mutationFn: () => agentsApi.update(agent.id, { enabled: !agent.enabled }),
    onSuccess: invalidate,
  });

  const delMut = useMutation({
    mutationFn: () => agentsApi.remove(agent.id),
    onSuccess: invalidate,
  });

  return (
    <div className={"agent-row" + (agent.enabled ? "" : " agent-row--off")}>
      <div className="agent-row__main">
        <div className="agent-row__name" title={agent.name}>
          {agent.name}
        </div>
        <div className="agent-row__meta">
          {agent.description ? <span>{agent.description}</span> : null}
          {agent.capabilities.length > 0 ? (
            <span>{agent.capabilities.map((c) => `#${c}`).join(" ")}</span>
          ) : null}
          <span className="agent-row__badge">{agent.enabled ? "启用" : "停用"}</span>
        </div>
      </div>
      <button
        type="button"
        className="btn"
        aria-label={`${agent.enabled ? "停用" : "启用"} ${agent.name}`}
        disabled={toggleMut.isPending}
        onClick={() => toggleMut.mutate()}
      >
        {agent.enabled ? "停用" : "启用"}
      </button>
      <button
        type="button"
        className="btn btn--danger"
        aria-label={`删除 ${agent.name}`}
        onClick={() => {
          if (window.confirm(`删除智能体「${agent.name}」？`)) {
            delMut.mutate();
          }
        }}
      >
        删除
      </button>
    </div>
  );
}
