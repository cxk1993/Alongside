import { useCallback, useEffect, useState } from "react";

import { hasToken, setUnauthorizedHandler } from "@/shared/api/client";
import { LoginPage } from "./LoginPage";
import "./login.css";

interface Props {
  children: React.ReactNode;
}

/**
 * ★ T30 桌面壳鉴权门：
 *   - 有 token → 直接进桌面（刷新页面保持登录态）；
 *   - 无 token → 只渲染登录页；
 *   - 任何业务请求 401 且 refresh 失败 → client.ts notifyUnauthorized() → 切回登录页；
 *   - 登录成功 → 进桌面。children 原样包装，App.tsx 不用改。
 */
export function AuthGate({ children }: Props) {
  const [authed, setAuthed] = useState<boolean>(() => hasToken());

  useEffect(() => {
    // 401 且 refresh 失败 → 回登录页（token 已被 client.ts 清掉）
    setUnauthorizedHandler(() => setAuthed(false));
    // 多标签页同步：别的页登出/登录，本页跟随
    const onStorage = (e: StorageEvent) => {
      if (e.key === "alongside.token") setAuthed(hasToken());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const onSuccess = useCallback(() => setAuthed(true), []);

  if (!authed) return <LoginPage onSuccess={onSuccess} />;
  return <>{children}</>;
}
