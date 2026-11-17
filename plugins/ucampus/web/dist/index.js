import React from "react";
const h = React.createElement;
const TARGET = "http://u.unipus.cn/index.html/";

/**
 * 内嵌窗口（U校园）。只做互操作：不抓数据、不注入脚本、不代操作。
 *
 * ★ r9 实测拍板（2026-11-16）：「u.unipus.cn 无法 https 内嵌」。
 *   服务器端强制 https→http 301：https://u.unipus.cn/ 与 /index.html 均
 *   301 → http://u.unipus.cn/index.html/（http 版 200、无 XFO/CSP 拦截）。
 *   在 https 宿主（Alongside 前端）里内嵌它 = mixed-content 被浏览器直接
 *   拦掉 ⇒ 白屏，且无任何 https 可用入口可换。
 *   ⇒ 运行时按宿主协议自动兜底：https 宿主放弃 iframe、改「在浏览器打开」
 *   大按钮；http 宿主（本地开发等）仍正常内嵌 http 目标。
 *
 * ★ r9 弱提示「打开即显示」：首帧常显「若窗口一直空白……」；9 秒无 load
 *   才升级措辞（「迟迟未加载」），onLoad 到达即取消定时器（r8 假警报回归闸）。
 *   「打不开？」按钮展开/收起详细原因。
 */
// 宿主协议在模块加载时已可判定（浏览器环境）；守卫让 SSR/测试导入也安全。
const CAN_EMBED = typeof window === "undefined" || window.location.protocol !== "https:";

function Component() {
  const canEmbed = CAN_EMBED;
  const [n, setN] = React.useState(0);
  const [loaded, setLoaded] = React.useState(false);
  const [timedOut, setTimedOut] = React.useState(false);
  const [detailOpen, setDetailOpen] = React.useState(false);
  const timer = React.useRef(null);

  React.useEffect(() => {
    if (!canEmbed) return; // 不可内嵌：不渲染 iframe，也不起慢载定时器
    setLoaded(false);
    setTimedOut(false);
    timer.current = setTimeout(() => {
      timer.current = null;
      setTimedOut(true); // 只有「一直没 load」才成立；onLoad 会连定时器一起清掉
    }, 9000);
    return () => {
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = null;
      }
    };
  }, [n, canEmbed]);

  const onLoad = () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    setLoaded(true);
    setTimedOut(false);
  };

  return h("div", { style: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0 } },
    h("div", { style: { display: "flex", alignItems: "center", gap: 8, padding: "6px 10px",
                        borderBottom: "1px solid rgba(128,128,128,.25)", fontSize: 12 } },
      h("strong", null, "U校园"),
      h("span", { style: { opacity: .6 } }, canEmbed ? "内嵌原站 · 登录状态由浏览器决定" : "站点仅支持 http，无法内嵌"),
      h("span", { style: { flex: 1 } }),
      h("button", { type: "button", onClick: () => setDetailOpen((v) => !v),
                    title: "内容空白 / 打不开时的说明", "aria-expanded": detailOpen }, "打不开？"),
      h("button", { type: "button", onClick: () => setN(n + 1) }, "重新加载"),
      h("a", { href: TARGET, target: "_blank", rel: "noreferrer" }, "在浏览器打开")
    ),
    canEmbed ? h("div", { style: { padding: "6px 10px", fontSize: 12, color: "#b45309", background: "rgba(245,158,11,.1)" } },
      timedOut
        ? "9 秒内没有收到加载完成信号（迟迟未加载）：可能网络不通、被墙，或对方站点拒绝被内嵌。点「重新加载」重试，或「在浏览器打开」。"
        : "若窗口一直空白：站点可能拒绝内嵌或存在 mixed-content，点「在浏览器打开」。"
    ) : h("div", { style: { padding: "6px 10px", fontSize: 12, color: "#b45309", background: "rgba(245,158,11,.1)" } },
      "U校园仅提供 http 服务（https 会强制跳转 http），本页面为 https，浏览器会按 mixed content 直接拦截内嵌窗口。请用「在浏览器打开」使用。"
    ),
    detailOpen ? h("div", { style: { padding: "6px 10px", fontSize: 12, opacity: .85 } },
      "打不开的常见原因：① 对方站点拒绝被内嵌（X-Frame-Options / CSP）或协议不匹配（mixed content）；② 需要先登录——点「在浏览器打开」登录后再回来；③ 第三方 Cookie 策略让内嵌窗口拿不到登录态。"
    ) : null,
    canEmbed
      ? h("iframe", {
          key: n, src: TARGET, title: "U校园", onLoad,
          style: { flex: 1, width: "100%", border: 0, background: "#fff" },
          sandbox: "allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads",
          referrerPolicy: "no-referrer-when-downgrade"
        })
      : h("div", { style: { flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, padding: 24 } },
          h("p", { style: { fontSize: 13, opacity: .8, textAlign: "center", margin: 0 } },
            "U校园（u.unipus.cn）服务器强制 https→http 跳转，在 https 页面内嵌会被浏览器拦截，无法显示。"),
          h("a", { href: TARGET, target: "_blank", rel: "noreferrer",
                   style: { display: "inline-block", padding: "8px 20px", fontSize: 13, color: "#fff",
                            background: "#2563eb", borderRadius: 6, textDecoration: "none" } },
            "在浏览器打开")
        ),
    loaded ? null : h("div", { style: { padding: "2px 10px", fontSize: 11, opacity: .5 } }, "加载中…")
  );
}

export default { manifestId: "ucampus", Component, slots: {} };
export { Component };