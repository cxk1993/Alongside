import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { healthApi, type HealthKind, type HealthRecord } from "./api";
import { AppErrorState } from "@/shared/ui/app-shell/AppShell";
import "./health.css";

const KIND_LABEL: Record<HealthKind, string> = {
  symptom: "症状",
  medication: "用药",
  appointment: "复诊",
  lab: "体检",
};

function nowLocalInput(): string {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export default function HealthApp() {
  const qc = useQueryClient();
  const [kind, setKind] = useState<HealthKind>("symptom");
  const [title, setTitle] = useState("");
  const [occurredAt, setOccurredAt] = useState(nowLocalInput());
  const [severity, setSeverity] = useState<string>("");
  const [followup, setFollowup] = useState(false);
  const [followupDue, setFollowupDue] = useState("");
  const [note, setNote] = useState("");
  const [selected, setSelected] = useState<HealthRecord | null>(null);
  const [detailNote, setDetailNote] = useState("");

  const invalidate = () => qc.invalidateQueries({ queryKey: ["health"] });

  const { data: records, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["health", "list"],
    queryFn: () => healthApi.list(),
  });

  const createMut = useMutation({
    mutationFn: () =>
      healthApi.create({
        kind,
        title: title.trim(),
        occurred_at: new Date(occurredAt).toISOString(),
        severity: severity ? Number(severity) : null,
        note: note.trim() || null,
        followup_needed: followup,
        followup_due: followup && followupDue ? followupDue : null,
      }),
    onSuccess: () => {
      setTitle("");
      setNote("");
      setSeverity("");
      setFollowup(false);
      setFollowupDue("");
      invalidate();
    },
  });

  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof healthApi.update>[1] }) =>
      healthApi.update(id, body),
    onSuccess: () => {
      setSelected(null);
      invalidate();
    },
  });

  const delMut = useMutation({
    mutationFn: (id: string) => healthApi.remove(id),
    onSuccess: () => {
      setSelected(null);
      invalidate();
    },
  });

  const fuMut = useMutation({
    mutationFn: (id: string) => healthApi.requestFollowup(id),
    onSuccess: () => invalidate(),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || createMut.isPending) return;
    createMut.mutate();
  };

  const list = records ?? [];

  return (
    <div className="health-root">
      <form className="health-add" onSubmit={submit}>
        <div className="health-add__row">
          <select
            aria-label="类型"
            value={kind}
            onChange={(e) => setKind(e.target.value as HealthKind)}
          >
            {(Object.keys(KIND_LABEL) as HealthKind[]).map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
          <input
            className="health-add__title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="标题，如：头痛 / 布洛芬 / 心内科复诊"
            aria-label="标题"
          />
          <input
            type="datetime-local"
            value={occurredAt}
            onChange={(e) => setOccurredAt(e.target.value)}
            aria-label="发生时间"
          />
          <select
            aria-label="严重程度"
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
          >
            <option value="">程度—</option>
            {["1", "2", "3", "4", "5"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div className="health-add__row">
          <input
            className="health-add__note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="备注（可选）"
            aria-label="备注"
          />
          <label className="health-check">
            <input
              type="checkbox"
              checked={followup}
              onChange={(e) => setFollowup(e.target.checked)}
            />
            需跟进
          </label>
          {followup ? (
            <input
              type="date"
              value={followupDue}
              onChange={(e) => setFollowupDue(e.target.value)}
              aria-label="建议跟进日"
            />
          ) : null}
          <button
            className="btn btn--primary"
            type="submit"
            disabled={createMut.isPending || !title.trim()}
          >
            记一笔
          </button>
        </div>
      </form>

      <div className="health-list">
        {isLoading ? (
          <div className="empty">
            <div className="empty__text">读取中…</div>
          </div>
        ) : isError ? (
          /* ★ 2026-11-22（汐瑶 · 三态收口）：原来 error 落到「还没有健康记录」。 */
          <AppErrorState error={error} onRetry={() => void refetch()} title="健康记录读取失败" />
        ) : list.length === 0 ? (
          <div className="empty">
            <div className="empty__icon" aria-hidden>
              ○
            </div>
            <div className="empty__text">还没有健康记录</div>
            <div className="empty__hint">症状 / 用药 / 复诊 / 体检，记下第一笔</div>
          </div>
        ) : (
          list.map((r) => (
            <button
              key={r.id}
              type="button"
              className="health-row"
              onClick={() => {
                setSelected(r);
                setDetailNote(r.note ?? "");
              }}
            >
              <span className={`health-row__kind health-row__kind--${r.kind}`}>
                {KIND_LABEL[r.kind]}
              </span>
              <span className="health-row__title">{r.title}</span>
              <span className="health-row__meta">
                {new Date(r.occurred_at).toLocaleString()}
                {r.severity ? ` · 程度 ${r.severity}` : ""}
                {r.followup_needed ? " · 需跟进" : ""}
              </span>
            </button>
          ))
        )}
      </div>

      {selected ? (
        <div className="health-drawer" role="dialog" aria-label="记录详情">
          <div className="health-drawer__head">
            <strong>{selected.title}</strong>
            <button type="button" className="btn" onClick={() => setSelected(null)}>
              关闭
            </button>
          </div>
          <div className="health-drawer__body">
            <div>
              类型：{KIND_LABEL[selected.kind]} · {new Date(selected.occurred_at).toLocaleString()}
              {selected.severity ? ` · 程度 ${selected.severity}` : ""}
            </div>
            <label>
              备注
              <textarea
                value={detailNote}
                onChange={(e) => setDetailNote(e.target.value)}
                rows={4}
              />
            </label>
            <label className="health-check">
              <input
                type="checkbox"
                checked={selected.followup_needed}
                onChange={(e) =>
                  updateMut.mutate({
                    id: selected.id,
                    body: { followup_needed: e.target.checked },
                  })
                }
              />
              需跟进（会请求待办模块生成跟进项）
            </label>
            <div className="health-drawer__actions">
              <button
                type="button"
                className="btn"
                onClick={() => updateMut.mutate({ id: selected.id, body: { note: detailNote } })}
              >
                保存备注
              </button>
              <button type="button" className="btn" onClick={() => fuMut.mutate(selected.id)}>
                再次请求跟进
              </button>
              <button type="button" className="btn" onClick={() => delMut.mutate(selected.id)}>
                删除
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
