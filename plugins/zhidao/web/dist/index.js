import React from "react";
const h = React.createElement;
const TARGET = "https://onlinestuh5.zhihuishu.com/";

/**
 * 内嵌窗口（知到）。只做互操作：不抓数据、不注入脚本、不代操作。
 *
 * ★ r8 修掉一个会误导用户的假警报（2026-11-16 真机实测）：
 *   旧版起一个 9 秒定时器无条件置 slow=true，只有 onLoad 会把它清回去 ——
 *   但定时器本身从不取消，于是页面已经正常渲染的 9 秒后，提示条照样弹出来
 *   （「一直空白？」）。用户看到的是「明明有内容，界面却说可能空白」。
 *   现在：onLoad 到达即取消定时器；定时器只在「9 秒内没有任何 load 事件」时生效
 *   （= 网络卡住 / 被墙 / 迟迟无响应）。
 *
 * ★ 为什么不靠 onLoad 判断「被拒内嵌」：实测（127.0.0.1:8123 探针，2026-11-16）
 *   X-Frame-Options: DENY 的 iframe **同样会触发 load**（错误页也会 load，
 *   209ms vs 正常页 174ms），所以 onLoad 不能证明「内容真的画出来了」。
 *   ⇒ 给一个用户可主动打开的「打不开？」说明，而不是假装能自动诊断。
 */
function Component() {
  const [n, setN] = React.useState(0);
  const [loaded, setLoaded] = React.useState(false);
  const [timedOut, setTimedOut] = React.useState(false);
  const [hintOpen, setHintOpen] = React.useState(false);
  const timer = React.useRef(null);

  React.useEffect(() => {
    setLoaded(false);
    setTimedOut(false);
    setHintOpen(false);
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

  const showHint = hintOpen || timedOut;

  return h("div", { style: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0 } },
    h("div", { style: { display: "flex", alignItems: "center", gap: 8, padding: "6px 10px",
                        borderBottom: "1px solid rgba(128,128,128,.25)", fontSize: 12 } },
      h("strong", null, "知到"),
      h("span", { style: { opacity: .6 } }, "内嵌原站 · 登录状态由浏览器决定"),
      h("span", { style: { flex: 1 } }),
      h("button", { type: "button", onClick: () => setHintOpen((v) => !v),
                    title: "内容空白 / 打不开时的说明", "aria-expanded": showHint }, "打不开？"),
      h("button", { type: "button", onClick: () => setN(n + 1) }, "重新加载"),
      h("a", { href: TARGET, target: "_blank", rel: "noreferrer" }, "在浏览器打开")
    ),
    h("iframe", {
      key: n, src: TARGET, title: "知到", onLoad,
      style: { flex: 1, width: "100%", border: 0, background: "#fff" },
      sandbox: "allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads",
      referrerPolicy: "no-referrer-when-downgrade"
    }),
    showHint ? h("div", { style: { padding: "6px 10px", fontSize: 12, color: "#b45309", background: "rgba(245,158,11,.1)" } },
      timedOut && !hintOpen
        ? "9 秒内没有收到加载完成信号：可能网络不通、被墙，或对方站点拒绝被内嵌。点「重新加载」重试，或「在浏览器打开」。"
        : "内容空白？常见原因有三：① 对方站点拒绝被内嵌（X-Frame-Options / CSP）；② 需要先登录——点「在浏览器打开」登录后再回来；③ 第三方 Cookie 策略让内嵌窗口拿不到登录态。"
    ) : null,
    loaded ? null : h("div", { style: { padding: "2px 10px", fontSize: 11, opacity: .5 } }, "加载中…")
  );
}

export default { manifestId: "zhidao", Component, slots: {} };
export { Component };

