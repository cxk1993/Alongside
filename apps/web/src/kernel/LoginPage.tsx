import { useEffect, useState, type FormEvent } from "react";
import { humanizeAuthError, login } from "@/shared/api/auth";

interface Props {
  onSuccess: () => void;
  /**
   * ★ P0-3：
   *   "page"（默认）= 整页门面（初次进站、登出后）；
   *   "modal"      = 401 后弹在桌面之上——不跳页、不丢窗口，用户关掉也能继续看。
   */
  variant?: "page" | "modal";
  /** modal 下「稍后再说」/ Esc / 点遮罩的出口（page 下不用）。 */
  onCancel?: () => void;
}

/**
 * ★ T30 登录页 —— 系统门面：密码 + TOTP（可选）、人话报错、加载态。
 * 视觉只用设计令牌（克制、干净）；不引入任何新依赖（X01）。
 * ★ P0-3：同一份组件同时充当「整页」与「模态」——不复制第二份登录逻辑。
 */
export function LoginPage({ onSuccess, variant = "page", onCancel }: Props) {
  const isModal = variant === "modal";
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [showTotp, setShowTotp] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // modal：Esc 可取消（可取消是硬要求，不能只能点按钮）
  useEffect(() => {
    if (!isModal || !onCancel) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isModal, onCancel]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setError("");
    if (!password) {
      setError("请输入密码");
      return;
    }
    setBusy(true);
    try {
      await login(password, totp);
      onSuccess();
    } catch (err) {
      setError(humanizeAuthError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={isModal ? "login login--modal" : "login"}
      role={isModal ? "dialog" : undefined}
      aria-modal={isModal || undefined}
      aria-label={isModal ? "登录已过期" : undefined}
      onMouseDown={(e) => {
        // 只有点在遮罩本身（不是卡片内部）才取消
        if (isModal && onCancel && e.target === e.currentTarget) onCancel();
      }}
    >
      <form className="login__card" onSubmit={submit} aria-label="登录 Alongside">
        <div className="login__brand">Alongside</div>
        <div className="login__hint">
          {isModal ? "登录已过期 · 登完回到原来的位置" : "人生管理系统 · 单用户私人实例"}
        </div>

        <label className="login__label" htmlFor="login-password">
          密码
        </label>
        <input
          id="login-password"
          className="login__input"
          type="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            setError("");
          }}
          disabled={busy}
          placeholder="管理员密码"
        />

        {showTotp ? (
          <>
            <label className="login__label" htmlFor="login-totp">
              两步验证码
            </label>
            <input
              id="login-totp"
              className="login__input login__input--totp"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={totp}
              onChange={(e) => {
                // 只留数字（TOTP 是 6 位数字；粘贴带空格也能容）
                setTotp(e.target.value.replace(/\D/g, "").slice(0, 6));
                setError("");
              }}
              disabled={busy}
              placeholder="6 位数字"
            />
          </>
        ) : (
          <button
            type="button"
            className="login__totp-toggle"
            onClick={() => setShowTotp(true)}
            disabled={busy}
          >
            使用两步验证码（可选）
          </button>
        )}

        {error ? (
          <div className="login__error" role="alert">
            {error}
          </div>
        ) : null}

        <button className="login__submit" type="submit" disabled={busy || !password}>
          {busy ? "正在进入…" : "进入"}
        </button>

        {isModal ? (
          <button type="button" className="login__cancel" onClick={onCancel} disabled={busy}>
            稍后再说
          </button>
        ) : null}
      </form>
    </div>
  );
}
