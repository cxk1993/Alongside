import { useCallback, useEffect, useRef, useState } from "react";

import {
  hasToken,
  setLoggedOutHandler,
  setLoginPromptHandler,
  setUnauthorizedHandler,
} from "@/shared/api/client";
import { LoginPage } from "./LoginPage";
import "./login.css";

interface Props {
  children: React.ReactNode;
}

/**
 * ★ T30 桌面壳鉴权门
 *   - 无 token（初次进站 / 登出后）→ 整页登录页，children 不挂载；
 *   - 有 token → 直接进桌面（刷新保持登录态）。
 *
 * ★ P0-3（2026-11-22）401 的两级处理：
 *   ① client.ts 先静默 refresh，成功就重放——用户什么都看不到；
 *   ② refresh 也失败 → 弹**模态登录框**（本组件注册的 loginPromptHandler）：
 *      · 不跳页、不卸载桌面 → 用户开着的窗口和位置全保住；
 *      · 并发多个 401 只弹一个（client.ts 的 promptLogin 单飞 + 本组件单实例）；
 *      · 可取消（Esc / 稍后再说 / 点遮罩）→ 关掉后留一条「未登录」提示条，
 *        绝不把界面卡在「读取中…」（配合数据层的 error 态）。
 *   ③ 登录成功 → resolve(true) → client.ts 自动重放刚才失败的请求；
 *      同时 auth.login 广播 AUTH_RESTORED，数据层立刻重拉，不等 TTL。
 *
 * 注意：401 **不再**把 authed 置 false——那是「丢上下文」的旧行为，
 * 只有显式登出（logout()）才回整页登录页。
 */
export function AuthGate({ children }: Props) {
  const [authed, setAuthed] = useState<boolean>(() => hasToken());
  /** 模态登录框是否开着（401 后弹）。 */
  const [loginOpen, setLoginOpen] = useState(false);
  /** 用户取消了登录：桌面留着，但顶部提示「未登录」并给一键重登。 */
  const [needLogin, setNeedLogin] = useState(false);
  /** 等待登录结果的 Promise resolver（client.ts 的 request 在等它）。 */
  const resolverRef = useRef<((ok: boolean) => void) | null>(null);

  const settle = useCallback((ok: boolean) => {
    const fn = resolverRef.current;
    resolverRef.current = null;
    fn?.(ok);
  }, []);

  useEffect(() => {
    // ② 401 且 refresh 失败 → 弹模态（client.ts 单飞，保证同一时刻只问一次）
    setLoginPromptHandler(() => {
      setNeedLogin(false);
      setLoginOpen(true);
      return new Promise<boolean>((resolve) => {
        resolverRef.current = resolve;
      });
    });
    // ③ 用户取消后的兜底通知：只标「未登录」，不卸载桌面
    setUnauthorizedHandler(() => setNeedLogin(true));
    // ④ 显式登出：回整页登录页（这是用户主动的，和 401 取消不是一回事）
    setLoggedOutHandler(() => {
      setLoginOpen(false);
      setNeedLogin(false);
      settle(false);
      setAuthed(false);
    });

    // 多标签页同步：别的页登出/登录，本页跟随
    const onStorage = (e: StorageEvent) => {
      if (e.key === "alongside.token") {
        setAuthed(hasToken());
        if (!hasToken()) setNeedLogin(true);
      }
    };
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("storage", onStorage);
      setLoginPromptHandler(null);
      setUnauthorizedHandler(() => {});
      setLoggedOutHandler(() => {});
      // 组件卸载时把挂着的登录等待收尾，别留一个永远不 resolve 的 Promise
      // （client.ts 的 promptLogin 单飞靠它清空，否则下一个 401 会复用一个死 Promise）
      settle(false);
    };
  }, []);

  const onSuccess = useCallback(() => {
    setAuthed(true);
    setLoginOpen(false);
    setNeedLogin(false);
    settle(true); // 让等着的请求立刻重放
  }, [settle]);

  const onCancel = useCallback(() => {
    setLoginOpen(false);
    setNeedLogin(true);
    settle(false); // 请求以 401 收尾，界面走 error 态而不是永远「读取中」
  }, [settle]);

  // 初次进站 / 显式登出：整页登录页
  if (!authed) return <LoginPage onSuccess={onSuccess} />;

  return (
    <>
      {children}
      {loginOpen ? (
        <LoginPage variant="modal" onSuccess={onSuccess} onCancel={onCancel} />
      ) : null}
      {needLogin && !loginOpen ? (
        <div className="auth-expired" role="status" data-testid="auth-expired">
          <span>登录已过期 —— 部分内容读不到，不是数据坏了。</span>
          <button
            type="button"
            className="auth-expired__btn"
            onClick={() => setLoginOpen(true)}
          >
            重新登录
          </button>
        </div>
      ) : null}
    </>
  );
}
