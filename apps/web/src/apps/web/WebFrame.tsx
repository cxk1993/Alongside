/**
 * 内嵌窗口：把一个网页装进 Alongside。
 *
 * ★ 不许硬编码任何具体站点 —— 本组件只认 entry.url。
 *
 * ── 关于"能不能内嵌"的诚实说明（重要） ─────────────────────────────
 *  浏览器**无法**用 JS 判断一个跨域 iframe 是否被 X-Frame-Options / CSP 拒绝：
 *  被拒时 onLoad 照样会触发，而 contentDocument 因跨域读不到，拿不到任何信号。
 *  可选的"服务端预探测"我们**故意不做** —— 那等于让后端去 fetch 用户填的任意 URL，
 *  是一个 SSRF 口子，与"任意网页"的白名单需求冲突，得不偿失。
 *  所以采用两条**兜底**，保证任何情况下都不是死路：
 *    1) 加载超时（6s）→ 显示"无法内嵌"提示 + 新窗口按钮；
 *    2) 底部常驻一条极简提示 + 工具条里的「在新窗口打开」始终可用。
 *  代价：被拒的站点若加载很快，用户会先看到空白（但有常驻提示可点），不会被困住。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/shared/components/Button";
import { useDesktopStore } from "@/kernel/store";
import { useWindowInstance } from "@/kernel/windowInstance";
import { webApi, type WebEntry } from "./api";

/** 加载超时（毫秒）——超过即认为"内嵌不成功"。 */
export const FRAME_TIMEOUT_MS = 6000;

type Phase = "loading" | "ok" | "timeout";

interface Props {
  entry: WebEntry;
}

export default function WebFrame({ entry }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [nonce, setNonce] = useState(0);
  const timerRef = useRef<number | null>(null);
  // ★ 真正塞进 iframe 的地址：默认 = entry.url；条目带 auth_ref 时由后端注入凭据后替换。
  const [srcUrl, setSrcUrl] = useState(entry.url);
  const [credError, setCredError] = useState<string | null>(null);
  // 记得本次渲染用的是哪份 URL（下拉选另一个条目时用它比对，避免闪烁）
  const [resolvedFor, setResolvedFor] = useState(`${entry.id}:${nonce}`);

  // ★ T22：这两按钮要操作"本窗" —— 从内核上下文拿 instanceId（同模块可能开多窗，不能靠 moduleId 反查）
  const instanceId = useWindowInstance();
  const pinned = useDesktopStore((s) =>
    instanceId ? (s.windows.find((w) => w.instanceId === instanceId)?.pinned ?? false) : false,
  );
  const fixedGeometry = useDesktopStore((s) =>
    instanceId
      ? (s.windows.find((w) => w.instanceId === instanceId)?.fixedGeometry ?? false)
      : false,
  );
  const setPinned = useDesktopStore((s) => s.setPinned);
  const setFixedGeometry = useDesktopStore((s) => s.setFixedGeometry);

  // ★ 解析内嵌地址：没配 auth_ref 的条目一步到位（与旧行为完全一致，零额外请求）。
  //   配了 auth_ref 的（如 token:env:PI_WEB_TOKEN）才去后端换带凭据的 URL ——
  //   凭据本体永远不进前端代码/条目表，前端只拿到"这一次可用"的地址。
  useEffect(() => {
    let cancelled = false;
    const key = `${entry.id}:${nonce}`;
    setCredError(null);

    if (!entry.auth_ref || entry.auth_ref.toLowerCase() === "none") {
      setSrcUrl(entry.url);
      setResolvedFor(key);
      return;
    }

    setResolvedFor(""); // 待解析：先不渲染 iframe，等拿到真地址
    webApi
      .frameUrl(entry.id)
      .then((r) => {
        if (cancelled) return;
        setSrcUrl(r.url);
        setResolvedFor(key);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        // 解析失败（多半是 .env 里缺该变量）→ 明确提示，不静默空白
        const msg = e instanceof Error ? e.message : "凭据解析失败";
        setCredError(msg);
        setResolvedFor(key);
      });

    return () => {
      cancelled = true;
    };
  }, [entry.id, entry.url, entry.auth_ref, nonce]);

  // 地址就绪 / 手动刷新 → 重走加载流程
  useEffect(() => {
    if (resolvedFor === "") return; // 还没拿到真地址，不计时
    setPhase("loading");
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      setPhase((p) => (p === "loading" ? "timeout" : p));
    }, FRAME_TIMEOUT_MS);
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [srcUrl, nonce, resolvedFor]);

  const onLoad = useCallback(() => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    setPhase("ok");
  }, []);

  const openExternal = useCallback(() => {
    window.open(srcUrl, "_blank", "noopener,noreferrer");
  }, [srcUrl]);

  const copyUrl = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(srcUrl);
    } catch {
      /* 剪贴板不可用时静默（不是关键路径） */
    }
  }, [srcUrl]);

  // ★ sandbox 说明：allow-scripts + allow-same-origin 同时给，浏览器会有安全告警
  //   （理论上被嵌页面可自解除 sandbox）。但被嵌页面与本应用**跨源**，
  //   且我们**不给** allow-top-navigation，拿不到顶层导航能力；
  //   而 SPA 类站点（Flutter/React 打包产物）少这两个直接白屏。
  //   这是"能跑通"与"理论纯洁"之间的**已知权衡**，不是疏忽。
  const sandbox = "allow-scripts allow-same-origin allow-forms allow-popups allow-downloads";

  const title = useMemo(() => entry.title || entry.slug, [entry.title, entry.slug]);

  /** ★ 工具栏/复制用：隐藏凭据，只显示到主机名（token 不进 UI，也不进剪贴板）。 */
  const displayUrl = useMemo(() => {
    try {
      const u = new URL(srcUrl);
      return `${u.origin}${u.pathname}`;
    } catch {
      return entry.url;
    }
  }, [srcUrl, entry.url]);

  return (
    <div className="web-frame">
      <div className="web-frame__bar">
        <span className="web-frame__title" title={title}>
          {title}
        </span>
        <span className="web-frame__url" title={displayUrl}>
          {displayUrl}
        </span>
        <span className="web-frame__spacer" />

        <Button onClick={() => setNonce((n) => n + 1)} title="重新加载">
          刷新
        </Button>
        <Button onClick={openExternal} title="在浏览器新标签打开（内嵌失败时的退路）">
          在新窗口打开
        </Button>
        <Button onClick={copyUrl} title="复制地址">
          复制地址
        </Button>
        {/* ★ T22 已交付：这两个按钮接真开关，状态与窗口标题栏那两个按钮双向同步 */}
        <Button
          onClick={() => instanceId && setPinned(instanceId, !pinned)}
          disabled={!instanceId}
          aria-pressed={pinned}
          className={pinned ? "is-on" : ""}
          title={
            !instanceId
              ? "本窗不在桌面窗口中，无法置顶"
              : pinned
                ? "取消置顶"
                : "置顶（始终盖过普通窗口）"
          }
        >
          ⭐ 置顶
        </Button>
        <Button
          onClick={() => instanceId && setFixedGeometry(instanceId, !fixedGeometry)}
          disabled={!instanceId}
          aria-pressed={fixedGeometry}
          className={fixedGeometry ? "is-on" : ""}
          title={
            !instanceId
              ? "本窗不在桌面窗口中，无法固定几何"
              : fixedGeometry
                ? "解除固定几何"
                : "固定位置与大小（锁定，不可拖拽缩放）"
          }
        >
          📌 固定几何
        </Button>
      </div>

      <div className="web-frame__body">
        {resolvedFor !== "" && (
          <iframe
            key={`${entry.id}-${nonce}`}
            className="web-frame__iframe"
            src={srcUrl}
            title={title}
            sandbox={sandbox}
            referrerPolicy="no-referrer"
            onLoad={onLoad}
          />
        )}

        {resolvedFor === "" && (
          <div className="web-frame__overlay">
            <div className="web-frame__overlay-text">正在准备访问凭据…</div>
          </div>
        )}

        {credError && (
          <div className="web-frame__overlay web-frame__overlay--solid">
            <div className="web-frame__overlay-text">
              <strong>凭据没能解析</strong>
              <p>{credError}</p>
              <p className="web-frame__overlay-hint">
                该条目声明了 <code>auth_ref = {entry.auth_ref}</code>，
                请在后端 <code>.env</code> 里配好对应变量后重试。
              </p>
            </div>
            <div className="web-frame__overlay-actions">
              <Button onClick={() => setNonce((n) => n + 1)}>重试</Button>
            </div>
          </div>
        )}

        {!credError && resolvedFor !== "" && phase === "loading" && (
          <div className="web-frame__overlay">
            <div className="web-frame__overlay-text">正在加载…</div>
          </div>
        )}

        {!credError && phase === "timeout" && (
          <div className="web-frame__overlay web-frame__overlay--solid">
            <div className="web-frame__overlay-text">
              <strong>没能内嵌这个网页</strong>
              <p>可能是该站点禁止被嵌入（X-Frame-Options / CSP），或网络不通。</p>
            </div>
            <div className="web-frame__overlay-actions">
              <Button variant="primary" onClick={openExternal}>
                在新窗口打开
              </Button>
              <Button onClick={() => setNonce((n) => n + 1)}>重试</Button>
            </div>
          </div>
        )}
      </div>

      {/* ★ 常驻兜底：浏览器拿不到"被拒"信号，所以留一条随时可点的出路 */}
      <div className="web-frame__hint">
        画面空白？该站点可能禁止被嵌入 ——
        <button type="button" className="web-frame__link" onClick={openExternal}>
          在新窗口打开 ↗
        </button>
      </div>
    </div>
  );
}
