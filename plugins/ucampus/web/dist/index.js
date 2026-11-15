import React from "react";
const h = React.createElement;
const TARGET = "https://u.unipus.cn/";

function Component() {
  const [n, setN] = React.useState(0);
  const [slow, setSlow] = React.useState(false);
  React.useEffect(() => {
    setSlow(false);
    const t = setTimeout(() => setSlow(true), 9000);
    return () => clearTimeout(t);
  }, [n]);
  return h("div", { style: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0 } },
    h("div", { style: { display: "flex", alignItems: "center", gap: 8, padding: "6px 10px",
                        borderBottom: "1px solid rgba(128,128,128,.25)", fontSize: 12 } },
      h("strong", null, "U校园"),
      h("span", { style: { opacity: .6 } }, "内嵌原站 · 登录状态由浏览器决定"),
      h("span", { style: { flex: 1 } }),
      h("button", { onClick: () => setN(n + 1) }, "重新加载"),
      h("a", { href: TARGET, target: "_blank", rel: "noreferrer" }, "在浏览器打开")
    ),
    h("iframe", {
      key: n, src: TARGET, title: "U校园", onLoad: () => setSlow(false),
      style: { flex: 1, width: "100%", border: 0, background: "#fff" },
      sandbox: "allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads",
      referrerPolicy: "no-referrer-when-downgrade"
    }),
    slow ? h("div", { style: { padding: "6px 10px", fontSize: 12, color: "#b45309", background: "rgba(245,158,11,.1)" } },
      "一直空白？可能对方站点拒绝被内嵌，或需要先登录——点「在浏览器打开」登录后再回来。") : null
  );
}

export default { manifestId: "ucampus", Component, slots: {} };
export { Component };
