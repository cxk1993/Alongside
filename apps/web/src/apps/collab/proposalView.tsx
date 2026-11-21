/**
 * 提案的两个「一块 UI」（折叠快照 + 可展开 JSON）——只导出组件，
 * 纯函数在 proposalFormat.ts（react-refresh 规则要求组件文件别混着导出常量/函数）。
 */
import { useState } from "react";

/**
 * ★ T20：变更前快照预览。
 *
 * 三条诚实规则（契约 11 §二/§三）：
 *   1. `action=create` → 没有"变更前"，**不渲染空对象**，明说「新建」；
 *   2. `before === null`（delete/update 却没有快照）→ 如实说「服务端未提供」，
 *      而不是留一个空白框让人以为快照是空的；
 *   3. 有快照 → 默认折叠（卡片上已经有一行标题，快照是签字时才要细看的东西）。
 */
export function BeforePreview({
  action,
  before,
}: {
  action?: string;
  before?: Record<string, unknown> | null;
}) {
  if (action === "create") {
    return <span className="collab-proposal__note">新建 · 无变更前快照</span>;
  }
  if (!before) {
    return <span className="collab-proposal__note">变更前快照：服务端未提供</span>;
  }
  return (
    <details className="collab-before">
      <summary>变更前快照</summary>
      <pre className="collab-before__body">{JSON.stringify(before, null, 2)}</pre>
    </details>
  );
}

/**
 * JSON 正文块。
 *
 * ★ T25 的诚实分档：`undefined`（服务端没这个字段）/ `null` / `{}` / 有内容
 * 是四种不同的事实，不能都渲染成 `{}`：
 *   - undefined → 「服务端未返回」
 *   - null      → 「null（服务端明确给了空值）」
 *   - {}        → 「无参数」（这动作本来就不需要参数）
 *   - 有内容    → 折叠 JSON
 */
export function JsonField({
  label,
  value,
  emptyText = "无参数",
}: {
  label: string;
  value: Record<string, unknown> | null | undefined;
  emptyText?: string;
}) {
  const [open, setOpen] = useState(false);
  if (value === undefined) {
    return <span className="collab-proposal__note">{label}：服务端未返回</span>;
  }
  if (value === null) {
    return <span className="collab-proposal__note">{label}：null（服务端明确给了空值）</span>;
  }
  if (Object.keys(value).length === 0) {
    return (
      <span className="collab-proposal__note">
        {label}：{emptyText}
      </span>
    );
  }
  return (
    <div className="collab-json">
      <button type="button" className="collab-json__toggle" onClick={() => setOpen((v) => !v)}>
        {label}（{Object.keys(value).length} 个字段）{open ? " · 收起" : " · 展开"}
      </button>
      {open ? <pre className="collab-before__body">{JSON.stringify(value, null, 2)}</pre> : null}
    </div>
  );
}
