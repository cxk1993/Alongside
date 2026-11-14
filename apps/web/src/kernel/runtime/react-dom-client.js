/**
 * 共享 react-dom/client facade（r5 · P2）。见 ./react.js 的说明。
 * 供少数需要自管 root 的插件 UI 使用（createRoot/hydrateRoot），与宿主同实例。
 */
import * as ReactDOMClient from "react-dom/client";

export default ReactDOMClient;
// ★ 逐名显式导出（理由见 ./react.js）：`export *` 的命名导出会被入口签名裁剪摇掉。
export const { createRoot, hydrateRoot } = ReactDOMClient;