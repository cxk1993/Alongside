/**
 * 共享 react-dom 运行时 facade（r5 · P2）。见 ./react.js 的说明。
 * 与宿主共用同一 react-dom 实例（kernel-runtime chunk），插件侧零适配。
 */
import * as ReactDOM from "react-dom";

export default ReactDOM;
// ★ 逐名显式导出（理由见 ./react.js）：`export *` 的命名导出会被入口签名裁剪摇掉。
export const {
  __SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED,
  createPortal,
  createRoot,
  findDOMNode,
  flushSync,
  hydrate,
  hydrateRoot,
  render,
  unmountComponentAtNode,
  unstable_batchedUpdates,
  unstable_renderSubtreeIntoContainer,
  version,
} = ReactDOM;