/**
 * 网页入口管理面板：增删改 + 独立开关 + ★ 能力声明。
 *
 * ★ 这里就是「能力条目」的**输入端** —— 填完之后，右侧会当场显示
 *   这条目进入能力目录（T20）时的 JSON 形状，让"输入端"的产出可见。
 * ★ 校验以后端为准（url schema / auth_ref 形状 / kind 枚举），
 *   前端只做第一道（非空 + 显式提示怎么填才对）。
 */
import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { Modal } from "@/shared/components/Modal";
import { toast } from "@/shared/components/Toast";
import { ApiError } from "@/shared/api/client";
import {
  WEB_KINDS,
  WEB_KIND_LABEL,
  webApi,
  webKeys,
  type WebEntry,
  type WebEntryInput,
  type WebKind,
} from "./api";

interface FormState {
  slug: string;
  title: string;
  url: string;
  icon: string;
  order: string;
  enabled: boolean;
  kind: WebKind;
  endpoint: string;
  authRef: string;
  capabilities: string;
  note: string;
}

const EMPTY: FormState = {
  slug: "",
  title: "",
  url: "",
  icon: "",
  order: "0",
  enabled: true,
  kind: "web",
  endpoint: "",
  authRef: "",
  capabilities: "",
  note: "",
};

function toForm(e: WebEntry): FormState {
  return {
    slug: e.slug,
    title: e.title,
    url: e.url,
    icon: e.icon ?? "",
    order: String(e.order),
    enabled: e.enabled,
    kind: e.kind,
    endpoint: e.endpoint ?? "",
    authRef: e.auth_ref ?? "",
    capabilities: e.capabilities.join(", "),
    note: e.note ?? "",
  };
}

function toPayload(f: FormState): WebEntryInput {
  const caps = f.capabilities
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return {
    slug: f.slug.trim(),
    title: f.title.trim(),
    url: f.url.trim(),
    icon: f.icon.trim() || null,
    order: Number(f.order) || 0,
    enabled: f.enabled,
    kind: f.kind,
    endpoint: f.endpoint.trim() || null,
    auth_ref: f.authRef.trim() || null,
    capabilities: caps,
    note: f.note.trim() || null,
  };
}

/** 后端返回的 RFC7807 → 给人看的一句话。 */
function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message || `请求失败（${e.status}）`;
  return e instanceof Error ? e.message : "未知错误";
}

export default function WebEntriesPanel({ entries }: { entries: WebEntry[] }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<WebEntry | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY);

  useEffect(() => {
    setForm(editing ? toForm(editing) : EMPTY);
  }, [editing, open]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: webKeys.all });
  };

  const save = useMutation({
    mutationFn: (f: FormState) =>
      editing ? webApi.update(editing.id, toPayload(f)) : webApi.create(toPayload(f)),
    onSuccess: () => {
      toast("ok", editing ? "已保存" : "已新建入口");
      setOpen(false);
      setEditing(null);
      invalidate();
    },
    onError: (e) => toast("danger", errMsg(e)),
  });

  const remove = useMutation({
    mutationFn: (id: string) => webApi.remove(id),
    onSuccess: () => {
      toast("ok", "已删除");
      invalidate();
    },
    onError: (e) => toast("danger", errMsg(e)),
  });

  const toggle = useMutation({
    mutationFn: (e: WebEntry) => webApi.update(e.id, { enabled: !e.enabled }),
    onSuccess: () => invalidate(),
    onError: (e) => toast("danger", errMsg(e)),
  });

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  return (
    <div className="web-panel">
      <div className="web-panel__head">
        <div>
          <div className="web-panel__title">网页入口</div>
          <div className="web-panel__sub">
            加一个网页 → 顺手声明它的 API/MCP → 任何 agent 都能用
          </div>
        </div>
        <Button
          variant="primary"
          onClick={() => {
            setEditing(null);
            setOpen(true);
          }}
        >
          ＋ 新建入口
        </Button>
      </div>

      {entries.length === 0 ? (
        <EmptyState text="还没有网页入口" hint="点右上角「新建入口」，填一个 URL 就能内嵌。" />
      ) : (
        <div className="web-panel__list">
          {entries.map((e) => (
            <div key={e.id} className="web-panel__row">
              <label className="web-panel__toggle" title={e.enabled ? "已启用" : "已停用"}>
                <input
                  type="checkbox"
                  checked={e.enabled}
                  onChange={() => toggle.mutate(e)}
                  aria-label={`启用 ${e.title}`}
                />
              </label>
              <div className="web-panel__meta">
                <div className="web-panel__name">
                  {e.title} <span className="web-panel__slug">{e.slug}</span>
                </div>
                <div className="web-panel__url" title={e.url}>
                  {e.url}
                </div>
              </div>
              <span className={`web-panel__kind web-panel__kind--${e.kind.replace("+", "-")}`}>
                {WEB_KIND_LABEL[e.kind] ?? e.kind}
              </span>
              <Button
                onClick={() => {
                  setEditing(e);
                  setOpen(true);
                }}
              >
                编辑
              </Button>
              <Button
                variant="danger"
                onClick={() => {
                  if (window.confirm(`删除入口「${e.title}」？`)) remove.mutate(e.id);
                }}
              >
                删除
              </Button>
            </div>
          ))}
        </div>
      )}

      <Modal
        open={open}
        title={editing ? `编辑入口 · ${editing.title}` : "新建入口"}
        onClose={() => {
          setOpen(false);
          setEditing(null);
        }}
        footer={
          <>
            <Button
              onClick={() => {
                setOpen(false);
                setEditing(null);
              }}
            >
              取消
            </Button>
            <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate(form)}>
              {save.isPending ? "保存中…" : "保存"}
            </Button>
          </>
        }
      >
        <div className="web-form">
          <label className="web-form__field">
            <span>名称</span>
            <input
              value={form.title}
              onChange={(e) => set("title", e.target.value)}
              placeholder="例：某个内部工具"
            />
          </label>

          <label className="web-form__field">
            <span>标识 slug</span>
            <input
              value={form.slug}
              disabled={!!editing}
              onChange={(e) => set("slug", e.target.value)}
              placeholder="小写字母/数字/连字符，例：my-portal"
            />
          </label>

          <label className="web-form__field web-form__field--wide">
            <span>地址（只支持 http / https）</span>
            <input
              value={form.url}
              onChange={(e) => set("url", e.target.value)}
              placeholder="https://…"
            />
          </label>

          <label className="web-form__field">
            <span>图标名（可选）</span>
            <input
              value={form.icon}
              onChange={(e) => set("icon", e.target.value)}
              placeholder="globe"
            />
          </label>

          <label className="web-form__field">
            <span>排序（小的在前）</span>
            <input
              value={form.order}
              onChange={(e) => set("order", e.target.value)}
              inputMode="numeric"
            />
          </label>

          <label className="web-form__field">
            <span>类型 kind</span>
            <select value={form.kind} onChange={(e) => set("kind", e.target.value as WebKind)}>
              {WEB_KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>

          <label className="web-form__field">
            <span>启用</span>
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => set("enabled", e.target.checked)}
            />
          </label>

          <label className="web-form__field web-form__field--wide">
            <span>能力端点 endpoint（kind 非 web 时必填）</span>
            <input
              value={form.endpoint}
              onChange={(e) => set("endpoint", e.target.value)}
              placeholder="http://127.0.0.1:9999/api/v1/mcp"
            />
          </label>

          <label className="web-form__field web-form__field--wide">
            <span>
              ★ 凭据引用 <code>auth_ref</code>（只写引用，不写明文）
            </span>
            <input
              value={form.authRef}
              onChange={(e) => set("authRef", e.target.value)}
              placeholder="none 或 pat:env:YOUR_TOKEN_VAR"
            />
            <small className="web-form__hint">
              凭据本体放后端 <code>.env</code>（已 gitignore）；这里只写「方式:env:变量名」。
              写成明文会被后端拒绝。
            </small>
          </label>

          <label className="web-form__field web-form__field--wide">
            <span>能力清单（逗号分隔，可留空让 agent 自探索）</span>
            <input
              value={form.capabilities}
              onChange={(e) => set("capabilities", e.target.value)}
              placeholder="ledger.read, transaction.write"
            />
          </label>

          <label className="web-form__field web-form__field--wide">
            <span>备注（给 agent 的提示）</span>
            <input value={form.note} onChange={(e) => set("note", e.target.value)} />
          </label>

          {/* ★ 输入端 → 输出端 的当场可见：这就是它会以什么形状进能力目录 */}
          <div className="web-form__preview">
            <div className="web-form__preview-title">它将这样进入能力目录</div>
            <pre className="web-form__preview-json">
              {JSON.stringify(
                {
                  id: form.slug || "…",
                  name: form.title || "…",
                  kind: form.kind,
                  url: form.url || null,
                  endpoint: form.endpoint || null,
                  auth_ref: form.authRef || null,
                  capabilities: form.capabilities
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                  enabled: form.enabled,
                  note: form.note || null,
                  source: "web_entry",
                },
                null,
                2,
              )}
            </pre>
          </div>
        </div>
      </Modal>
    </div>
  );
}
