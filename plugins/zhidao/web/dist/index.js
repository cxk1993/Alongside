import React from "react";
const h = React.createElement;
const TARGET = "https://onlinestuh5.zhihuishu.com/";

/**
 * 内嵌窗口（知到）。只做互操作：不抓数据、不注入脚本、不代操作。
 *
 * ★ r9：弱提示改为「打开即显示」——不再等用户点「打不开？」或 9 秒超时，
 *   首帧就常显一条「若窗口一直空白：站点可能拒绝内嵌或存在 mixed-content，
 *   点「在浏览器打开」」。慢载定时器保留：9 秒无 load 事件才升级措辞
 *   （「迟迟未加载」），onLoad 到达即取消（r8 的假警报回归闸不变）。
 *   「打不开？」按钮改为展开/收起详细原因（默认收起）。
 *
 * ★ r8 实测（2026-11-16）：X-Frame-Options: DENY 的 iframe 同样会触发 load，
 *   所以 onLoad 不能证明「内容真的画出来了」⇒ 提示是常驻指引，不是自动诊断。
 */
function Component() {
  const [n, setN] = React.useState(0);
  const [loaded, setLoaded] = React.useState(false);
  const [timedOut, setTimedOut] = React.useState(false);
  const [detailOpen, setDetailOpen] = React.useState(false);
  const timer = React.useRef(null);

  React.useEffect(() => {
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
  }, [n]);

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
      h("strong", null, "知到"),
      h("span", { style: { opacity: .6 } }, "内嵌原站 · 登录状态由浏览器决定"),
      h("span", { style: { flex: 1 } }),
      h("button", { type: "button", onClick: () => setDetailOpen((v) => !v),
                    title: "内容空白 / 打不开时的说明", "aria-expanded": detailOpen }, "打不开？"),
      h("button", { type: "button", onClick: () => setN(n + 1) }, "重新加载"),
      h("a", { href: TARGET, target: "_blank", rel: "noreferrer" }, "在浏览器打开")
    ),
    h("div", { style: { padding: "6px 10px", fontSize: 12, color: "#b45309", background: "rgba(245,158,11,.1)" } },
      timedOut
        ? "9 秒内没有收到加载完成信号（迟迟未加载）：可能网络不通、被墙，或对方站点拒绝被内嵌。点「重新加载」重试，或「在浏览器打开」。"
        : "若窗口一直空白：站点可能拒绝内嵌或存在 mixed-content，点「在浏览器打开」。"
    ),
    detailOpen ? h("div", { style: { padding: "6px 10px", fontSize: 12, opacity: .85 } },
      "打不开的常见原因：① 对方站点拒绝被内嵌（X-Frame-Options / CSP）；② 需要先登录——点「在浏览器打开」登录后再回来；③ 第三方 Cookie 策略让内嵌窗口拿不到登录态。"
    ) : null,
    h("iframe", {
      key: n, src: TARGET, title: "知到", onLoad,
      style: { flex: 1, width: "100%", border: 0, background: "#fff" },
      sandbox: "allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads",
      referrerPolicy: "no-referrer-when-downgrade"
    }),
    loaded ? null : h("div", { style: { padding: "2px 10px", fontSize: 11, opacity: .5 } }, "加载中…")
  );
}

export default { manifestId: "zhidao", Component, slots: {} };
export { Component };