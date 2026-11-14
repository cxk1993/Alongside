/**
 * hello-seam 的前端源码 —— 第三方插件 UI 的正确写法（构建期）。
 *
 * 契约要点（完整版见 plugins/hello-seam/README.md）：
 *  1. 产物 = **单文件 ESM**，落 `web/dist/index.js`；后端从
 *     `GET /plugin-assets/<id>/index.js` 服务它，前端 import() 它。
 *  2. react / react-dom / react/jsx-runtime 必须 **external**（两份 React 会炸 hooks，
 *     失败模式隐蔽：能渲染，useState 一调就崩）。
 *  3. `export default` 一个 `{ manifestId, Component, slots }` 组件模块。
 *  4. 不引 CDN、不用绝对外部 URL（要离线可用）。
 */
import React from "react";

export function Component() {
  const [greeting, setGreeting] = React.useState<string>("…");

  React.useEffect(() => {
    // 调自己的 API：相对路径，避免写死 host；令牌由宿主统一注入。
    fetch("/api/v1/hello-seam/greeting", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => setGreeting(String(d.message)))
      .catch((e) => setGreeting(`API 取数失败：${e}`));
  }, []);

  return (
    <div style={{ padding: 16 }}>
      <h2 style={{ margin: 0, fontSize: 18 }}>接缝示例（hello-seam）</h2>
      <p style={{ opacity: 0.7, fontSize: 13, marginTop: 8 }}>
        这个窗口是运行时加载的：它不在前端构建产物里，
        而是后端从插件目录服务 index.js，前端 import() 进来。
      </p>
      <p style={{ fontFamily: "monospace", fontSize: 12, marginTop: 12 }}>
        {greeting}
      </p>
    </div>
  );
}

const HelloSeamModule = {
  manifestId: "hello-seam",
  Component,
  slots: {},
};

export default HelloSeamModule;
