import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePluginEvent } from "@/shared/api/events";
import { agentsApi } from "./api";
import AgentRow from "./AgentRow";
import "./agents.css";

/** AI 编排主界面：agent 列表 + 创建 + 启用停用 + 删除。 */
export default function AgentsApp() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const invalidate = () => qc.invalidateQueries({ queryKey: ["agents"] });
  usePluginEvent("agents.agent.created", invalidate);
  usePluginEvent("agents.agent.updated", invalidate);
  usePluginEvent("agents.agent.deleted", invalidate);

  const { data: agents, isLoading } = useQuery({
    queryKey: ["agents", "list"],
    queryFn: () => agentsApi.list(),
  });

  const createMut = useMutation({
    mutationFn: () =>
      agentsApi.create({
        name: name.trim(),
        description: description.trim(),
      }),
    onSuccess: () => {
      setName("");
      setDescription("");
      invalidate();
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || createMut.isPending) return;
    createMut.mutate();
  };

  const list = agents ?? [];
  const enabledCount = list.filter((a) => a.enabled).length;

  return (
    <div className="agents-root">
      <form className="agents-add" onSubmit={submit}>
        <input
          className="agents-add__input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="智能体名称，如：研究员 / 写作助手"
          aria-label="智能体名称"
        />
        <input
          className="agents-add__input agents-add__input--desc"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="描述（可选）"
          aria-label="智能体描述"
        />
        <button
          className="btn btn--primary"
          type="submit"
          disabled={createMut.isPending || !name.trim()}
        >
          添加
        </button>
      </form>

      <div className="tiny" style={{ color: "var(--txt-faint)" }}>
        {list.length > 0 ? `启用 ${enabledCount}/${list.length}` : ""}
      </div>

      <div className="agents-list">
        {isLoading ? (
          <div className="empty">
            <div className="empty__text">加载中…</div>
          </div>
        ) : list.length === 0 ? (
          <div className="empty">
            <div className="empty__icon" aria-hidden>
              ◎
            </div>
            <div className="empty__text">还没有智能体</div>
            <div className="empty__hint">在上方注册一个智能体，任务块才能派发出去</div>
          </div>
        ) : (
          list.map((a) => <AgentRow key={a.id} agent={a} />)
        )}
      </div>
    </div>
  );
}
