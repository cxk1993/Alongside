/** 模块健康列表：底部小灯，一眼看出哪个模块坏了。 */
import type { CardHint, SystemEntry } from "./api";

/** 后端健康状态枚举 → 用户可见中文。 */
const STATUS_LABEL: Record<string, string> = {
  ok: "正常",
  timeout: "超时",
  error: "异常",
};

export default function SystemHealth({
  system,
  cards,
  cardsHint,
}: {
  system: SystemEntry[];
  cards: CardHint[];
  cardsHint: string;
}) {
  return (
    <section className="dash-card" aria-label="模块健康">
      <div className="dash-card__title">模块健康</div>
      <div className="dash-card__body">
        <div className="dash-health" role="list">
          {system.map((s) => (
            <span
              key={s.id}
              className="dash-health__item"
              data-status={s.status}
              data-testid={`health-${s.id}`}
              role="listitem"
              title={s.detail || s.path || s.status}
            >
              <span className="dash-health__dot" aria-hidden="true" />
              {s.name}
              <span className="dash-muted">{STATUS_LABEL[s.status] ?? s.status}</span>
            </span>
          ))}
        </div>
        {cards.length > 0 && (
          <div className="dash-muted" data-testid="card-hints">
            已声明 dashboard.card：{cards.map((c) => c.name).join(" / ")}
          </div>
        )}
        <div className="dash-cards-hint">{cardsHint}</div>
      </div>
    </section>
  );
}
