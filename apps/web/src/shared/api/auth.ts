/**
 * ★ T30：鉴权 API —— 登录 / 登出的唯一前端入口。
 *
 * 契约（后端 modules/auth，零改动，只读参考）：
 *   POST /api/v1/auth/login   { password, totp?, username? } → { access_token, expires_in, user }
 *                             （refresh token 由后端写进 httpOnly Cookie，前端绝不接触）
 *   POST /api/v1/auth/logout  → { ok: true }（清 refresh Cookie）
 *   POST /api/v1/auth/refresh → { access_token }（client.ts 的 401 拦截自动走）
 *
 * token 只进 localStorage（key: alongside.token，与 client.ts / 过渡页 login.html 一致），
 * 不进 URL、不进 git。
 */
import { api, setToken, notifyUnauthorized, ApiError } from "./client";

export interface LoginResult {
  access_token: string;
  expires_in: number;
  user: { sub: string };
}

/** 把后端错误翻译成人话（RFC7807 detail 优先，兜底按状态码）。 */
export function humanizeAuthError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) {
      // 后端 detail 已是人话（"密码错误" / "TOTP 验证失败"等），优先用
      return e.detail || "密码或验证码不对，请再试一次";
    }
    if (e.status === 0) return "连不上服务器，请检查网络后再试";
    if (e.status === 429) return "尝试太频繁了，稍等一会儿再试";
    if (e.status >= 500) return "服务器开小差了，稍后再试";
    return e.detail || e.title || "登录失败，请再试一次";
  }
  return "登录失败，请再试一次";
}

/** 登录：成功则 access token 落 localStorage 并返回用户信息；失败抛 ApiError（调方用 humanizeAuthError）。 */
export async function login(password: string, totp = ""): Promise<LoginResult> {
  const body: Record<string, string> = { password };
  if (totp.trim()) body.totp = totp.trim();
  const res = await api.post<LoginResult>("/api/v1/auth/login", body);
  setToken(res.access_token);
  return res;
}

/**
 * 登出：清本地 token + 通知鉴权门切回登录页。
 * 后端 logout 尽力而为（清 refresh Cookie）——后端不可达也要把本地退干净。
 */
export async function logout(): Promise<void> {
  try {
    await api.post("/api/v1/auth/logout");
  } catch {
    /* 后端不可达也继续本地登出 */
  }
  setToken(null);
  notifyUnauthorized();
}
