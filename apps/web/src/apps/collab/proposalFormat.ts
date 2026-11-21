/**
 * 提案字段的「人话化」（纯函数，无 JSX）——卡片、详情抽屉、将来的列表都共用这一套。
 *
 * ★ T25：从 CollabApp 抽出来（原先内联在组件文件里）。两处各写一份的结果是
 *   「卡片说服务端未提供、抽屉说 {}」——同一份数据两套口径，比不显示更糟。
 *
 * 纪律（判据 16/25）：不造假数据、不装深度。
 *   - 缺值一律说「未提供/未执行」，绝不渲染空对象冒充「空快照」；
 *   - 三种撤销语义文案照契约原文，不自己编。
 */

/** 提案行（列表/详情的公共子集）。 */
export interface ProposalRow {
  id: string;
  title?: string;
  tool_name?: string;
  action?: string;
  status?: string;
  actor?: string;
  created_at?: string;
  /** ★ T20：变更前快照。契约口径：create 必为 null；null 就是**没有快照**，不是空对象。 */
  before?: Record<string, unknown> | null;
  executed_at?: string | null;
  undone_at?: string | null;
}

/**
 * ★ T20：actor 要一眼分得开（契约 11 §二）。
 * 原样显示 `mcp:nx-t10-ui` 这类内部标识对人没有意义，但也不能丢掉「是谁」——
 * 折中：保留前缀语义，人读得懂的部分放前面。
 */
export function actorLabel(actor?: string): string {
  if (!actor) return "来源不明";
  if (actor === "human") return "你签发";
  if (actor.startsWith("mcp:")) return `AI（${actor.slice(4) || "未命名 PAT"}）`;
  return actor;
}

/**
 * ★ T26：审计字段（谁签的字 `executed_by` / 谁撤销的 `undone_by`，73a0519 落库）。
 * 和「谁提的议」不是一回事，不能共用 actorLabel —— 把签字人写成「你签发」会读成提案来源。
 * 空值返回 ""，由调用方决定说「未记录」还是整行不显示。
 */
export function signerLabel(who?: string | null): string {
  if (!who) return "";
  if (who === "human") return "你（human）";
  if (who.startsWith("mcp:")) return `AI（${who.slice(4) || "未命名 PAT"}）`;
  return who;
}

/**
 * ★ T20 修正（契约 8ccef3b）：三种撤销语义不同，文案必须说清「撤掉的是什么」。
 *   契约原文：delete → 用 before 重建（新 id 同内容）｜update → 用 before 回写原 id
 *   ｜create → 删除 executed_id 指向的那条。
 */
export function undoNote(action?: string): string {
  if (action === "create") return "撤销 = 删除刚建的那条";
  if (action === "delete") return "撤销 = 用快照重建（会得到新 id，不是同一行）";
  if (action === "update") return "撤销 = 用快照回写原行";
  return "";
}

/** ★ T22：时间只到分钟（ISO 的 T 之后 5 个字符）——给人看的列表不该出现毫秒。 */
export function hhmm(iso?: string | null): string {
  const m = /T(\d{2}:\d{2})/.exec(iso ?? "");
  return m ? m[1] : "";
}

/** 完整一点的时间：`2026-11-22 03:35`（详情抽屉里要看日期，列表只要时分）。 */
export function ymdhm(iso?: string | null): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(iso ?? "");
  return m ? `${m[1]} ${m[2]}` : "";
}
