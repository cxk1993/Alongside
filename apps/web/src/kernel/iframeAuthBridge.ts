/**
 * ★ B2（T19#3）：iframe 跨源登录态握手桥（内核桌面侧）。
 *
 * 背景（B2 前置勘查结论，2026-09-20）：
 *   - 情形 A：同源 iframe 天然共享父页 localStorage，**不走此桥**；
 *   - 情形 B：跨源内嵌 Alongside 自家页面（独立端口/域）读不到父页 token，
 *     此前必须二次登录——本桥即缺口本体；
 *   - 情形 C：内嵌外部任意站点**明确排除**（WebFrame 现状正确，永不共享凭据）。
 *
 * 协议（被嵌页面侧配合一段握手代码，归该页面所属卡）：
 *   子页启动时 → `parent.postMessage({ type: "alongside:request-token" }, "<父页源>")`
 *   父页（本桥）校验通过 → `event.source.postMessage(
 *       { type: "alongside:token", token }, { targetOrigin: event.origin })`
 *
 * 安全设计（采纳方案 1 的落地口径）：
 *   1. **origin 白名单，空列表起步**：白名单为空 = 拒绝一切跨源请求（fail closed）；
 *      情形 B 的自家页面接入时，把其完整源（协议+域+端口）登记进 ALLOWED_IFRAME_ORIGINS。
 *   2. 只认 `alongside:request-token` 协议消息，其余 message 一律忽略；
 *   3. 回信 targetOrigin **锁定为请求来源**，凭据绝不广播；
 *   4. token 只进 postMessage（内存通道），不进 URL、不进日志（T30 契约）。
 */
/** 读 token 的口径与 shared/api/client.ts:41 同 key（T30 契约：alongside.token）。 */
const TOKEN_KEY = "alongside.token";

const REQUEST_TYPE = "alongside:request-token";
const RESPONSE_TYPE = "alongside:token";

/**
 * ★ 跨源内嵌白名单（origin = 协议 + 域 + 端口，完全匹配才放行）。
 *
 * 空列表起步（口径）：当前没有已登记的跨源自家页面 = 拒绝一切。
 * 示例（情形 B 页面接入时登记）：
 *   "https://app.example.local"   // 独立域部署的子应用
 *   "http://localhost:18091"      // 本机开发中的子应用
 */
export const ALLOWED_IFRAME_ORIGINS: readonly string[] = [];

export interface IframeAuthBridgeOptions {
  /** 读 token 的函数（默认读 localStorage[TOKEN_KEY]；测试可注入）。 */
  getToken?: () => string | null;
  /** 白名单（默认 ALLOWED_IFRAME_ORIGINS；测试可注入）。 */
  allowedOrigins?: readonly string[];
  /** 监听目标（默认 window；测试可注入）。 */
  target?: Pick<Window, "addEventListener" | "removeEventListener">;
}

export interface IframeAuthBridgeHandle {
  /** 摘除监听（Desktop 卸载时用；应用生命周期内通常不调）。 */
  dispose: () => void;
}

/** 单条 message 的处理（拆出来便于测试：监听挂载/卸载与协议逻辑分开验证）。 */
export function handleMessage(
  event: MessageEvent,
  allowedOrigins: readonly string[],
  getToken: () => string | null,
): boolean {
  // 1) origin 白名单：完全匹配才放行；空列表 = 拒绝一切（fail closed）
  if (!allowedOrigins.includes(event.origin)) return false;
  // 2) 只认本协议的请求消息
  const data = event.data as { type?: string } | null;
  if (!data || data.type !== REQUEST_TYPE) return false;
  // 3) source 必须在（跨文档 postMessage 才有）；回信 targetOrigin 锁定来源
  if (!event.source) return false;
  const token = getToken();
  if (!token) return false; // 未登录：不回（子页自行落到自己的登录态）
  const source = event.source as Window;
  source.postMessage({ type: RESPONSE_TYPE, token }, { targetOrigin: event.origin });
  return true;
}

/** 安装握手桥：返回句柄带 dispose。重复安装请自行管理（桌面壳只装一次）。 */
export function installIframeAuthBridge(options: IframeAuthBridgeOptions = {}): IframeAuthBridgeHandle {
  const allowedOrigins = options.allowedOrigins ?? ALLOWED_IFRAME_ORIGINS;
  const getToken = options.getToken ?? (() => window.localStorage.getItem(TOKEN_KEY));
  const target: Pick<Window, "addEventListener" | "removeEventListener"> =
    options.target ?? window;

  const listener = (event: MessageEvent): void => {
    handleMessage(event, allowedOrigins, getToken);
  };

  target.addEventListener("message", listener as EventListener);
  return {
    dispose: () => {
      target.removeEventListener("message", listener as EventListener);
    },
  };
}
