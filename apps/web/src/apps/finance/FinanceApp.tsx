import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePluginEvent } from "@/shared/api/events";
import { financeApi, formatCents, type Direction } from "./api";
import BeeCountSyncPanel from "./BeeCountSyncPanel";
import { AppErrorState } from "@/shared/ui/app-shell/AppShell";
import "./finance.css";

/** 理财主界面：BeeCount 同步面板 + summary 卡 + 快速记一笔 + 流水列表。 */
export default function FinanceApp() {
  const qc = useQueryClient();
  const [direction, setDirection] = useState<Direction>("expense");
  const [amount, setAmount] = useState("");
  const [category, setCategory] = useState("");
  const [account, setAccount] = useState("");
  const [note, setNote] = useState("");
  const [filterDirection, setFilterDirection] = useState<"" | Direction>("");
  const [filterCategory, setFilterCategory] = useState("");

  const invalidate = () => qc.invalidateQueries({ queryKey: ["finance"] });
  usePluginEvent("finance.entry.created", invalidate);
  usePluginEvent("finance.entry.updated", invalidate);
  usePluginEvent("finance.entry.deleted", invalidate);
  usePluginEvent("finance.snapshot.updated", invalidate);

  const { data: summary } = useQuery({
    queryKey: ["finance", "summary"],
    queryFn: () => financeApi.summary(),
  });

  const listParams = useMemo(
    () => ({
      direction: filterDirection || undefined,
      category: filterCategory || undefined,
      limit: 50,
    }),
    [filterDirection, filterCategory],
  );

  const { data: list, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["finance", "list", listParams],
    queryFn: () => financeApi.list(listParams),
  });

  const createMut = useMutation({
    mutationFn: () => {
      const cents = Math.round(parseFloat(amount) * 100);
      return financeApi.create({
        direction,
        amount_cents: cents,
        category: category.trim(),
        account: account.trim(),
        occurred_at: new Date().toISOString(),
        note: note.trim() || null,
      });
    },
    onSuccess: () => {
      setAmount("");
      setCategory("");
      setAccount("");
      setNote("");
      invalidate();
    },
  });

  const removeMut = useMutation({
    mutationFn: (id: string) => financeApi.remove(id),
    onSuccess: () => invalidate(),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const n = parseFloat(amount);
    if (!Number.isFinite(n) || n <= 0 || createMut.isPending) return;
    createMut.mutate();
  };

  const items = list?.items ?? [];
  const categories = useMemo(() => {
    const s = new Set<string>();
    for (const it of items) if (it.category) s.add(it.category);
    if (filterCategory) s.add(filterCategory);
    return [...s].sort();
  }, [items, filterCategory]);

  return (
    <div className="finance-root">
      <BeeCountSyncPanel />

      <section className="finance-summary" aria-label="收支汇总">
        <div className="finance-summary__item">
          <span className="finance-summary__label">支出</span>
          <span className="finance-summary__value" data-testid="sum-expense">
            {formatCents(summary?.expense_cents ?? 0)}
          </span>
        </div>
        <div className="finance-summary__item">
          <span className="finance-summary__label">收入</span>
          <span className="finance-summary__value" data-testid="sum-income">
            {formatCents(summary?.income_cents ?? 0)}
          </span>
        </div>
        <div className="finance-summary__item">
          <span className="finance-summary__label">净额</span>
          <span className="finance-summary__value" data-testid="sum-net">
            {formatCents(summary?.net_cents ?? 0)}
          </span>
        </div>
      </section>

      <form className="finance-add" onSubmit={submit} aria-label="快速记一笔">
        <div className="finance-add__field">
          <label htmlFor="fin-dir">方向</label>
          <select
            id="fin-dir"
            aria-label="方向"
            value={direction}
            onChange={(e) => setDirection(e.target.value as Direction)}
          >
            <option value="expense">支出</option>
            <option value="income">收入</option>
          </select>
        </div>
        <div className="finance-add__field">
          <label htmlFor="fin-amount">金额（元）</label>
          <input
            id="fin-amount"
            className="finance-add__input"
            aria-label="金额"
            inputMode="decimal"
            placeholder="12.34"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </div>
        <div className="finance-add__field">
          <label htmlFor="fin-cat">分类</label>
          <input
            id="fin-cat"
            className="finance-add__input"
            aria-label="分类"
            placeholder="餐饮"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          />
        </div>
        <div className="finance-add__field">
          <label htmlFor="fin-acc">账户</label>
          <input
            id="fin-acc"
            className="finance-add__input"
            aria-label="账户"
            placeholder="现金"
            value={account}
            onChange={(e) => setAccount(e.target.value)}
          />
        </div>
        <button
          className="btn btn--primary"
          type="submit"
          disabled={createMut.isPending || !(parseFloat(amount) > 0)}
        >
          记一笔
        </button>
      </form>

      <div className="finance-filters">
        <select
          aria-label="按方向筛选"
          value={filterDirection}
          onChange={(e) => setFilterDirection(e.target.value as "" | Direction)}
        >
          <option value="">全部方向</option>
          <option value="expense">支出</option>
          <option value="income">收入</option>
        </select>
        <select
          aria-label="按分类筛选"
          value={filterCategory}
          onChange={(e) => setFilterCategory(e.target.value)}
        >
          <option value="">全部分类</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>

      <div className="finance-list">
        {isLoading ? (
          <div className="empty">
            <div className="empty__text">读取中…</div>
          </div>
        ) : isError ? (
          /* ★ 2026-11-22（汐瑶 · 三态收口）：原来 error 落到「还没有流水」= 把坏说成空。 */
          <AppErrorState error={error} onRetry={() => void refetch()} title="流水读取失败" />
        ) : items.length === 0 ? (
          <div className="empty">
            <div className="empty__icon" aria-hidden>
              ○
            </div>
            <div className="empty__text">还没有流水</div>
            <div className="empty__hint">在上方快速记一笔收入或支出</div>
          </div>
        ) : (
          items.map((it) => (
            <div className="finance-row" key={it.id}>
              <div className="finance-row__main">
                <div className="finance-row__title">
                  <span className="finance-row__cat">{it.category || "未分类"}</span>
                  <span className="finance-row__meta">
                    {it.direction === "income" ? "收入" : "支出"}
                    {it.account ? ` · ${it.account}` : ""}
                  </span>
                </div>
                <div className="finance-row__meta">{it.note || it.occurred_at}</div>
              </div>
              <span className="finance-row__amount" data-testid="row-amount">
                {formatCents(it.amount_cents, it.direction)}
              </span>
              <button
                type="button"
                className="finance-row__del"
                aria-label="删除流水"
                onClick={() => removeMut.mutate(it.id)}
              >
                删除
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
