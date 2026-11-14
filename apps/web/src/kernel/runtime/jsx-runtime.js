/**
 * 共享 react/jsx-runtime facade（r5 · P2）。见 ./react.js 的说明。
 * 自动 JSX runtime 的 `import { jsx } from "react/jsx-runtime"` 经 import map 落到这里，
 * 与宿主共用同一 jsx-runtime（元素 $$typeof 符号一致），插件侧零适配。
 */
import * as jsxRuntime from "react/jsx-runtime";

export default jsxRuntime;
// ★ 逐名显式导出（理由见 ./react.js）：`export *` 的命名导出会被入口签名裁剪摇掉。
//   自动 JSX runtime 只认这三个名字（jsx/jsxs/Fragment），少一个插件就编译产物跑不起来。
export const { Fragment, jsx, jsxs } = jsxRuntime;