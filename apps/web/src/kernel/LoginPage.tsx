import { useState, type FormEvent } from "react";
import { humanizeAuthError, login } from "@/shared/api/auth";

interface Props {
  onSuccess: () => void;
}

/**
 * ★ T30 登录页 —— 系统门面：密码 + TOTP（可选）、人话报错、加载态。
 * 视觉只用设计令牌（克制、干净）；不引入任何新依赖（X01）。
 */
export function LoginPage({ onSuccess }: Props) {
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [showTotp, setShowTotp] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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
    <div className="login">
      <form className="login__card" onSubmit={submit} aria-label="登录 Alongside">
        <div className="login__brand">Alongside</div>
        <div className="login__hint">人生管理系统 · 单用户私人实例</div>

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
      </form>
    </div>
  );
}
