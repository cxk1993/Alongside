/**
 * 共享 React 运行时 facade（r5 · P2）。
 *
 * 第三方插件 UI 以原生 ESM 运行时加载，其 `import React from "react"` 是裸说明符，
 * 由文档 import map 解析到本模块（dev 走源码、prod 走固定名构建产物 kernel-react.js）。
 * 本模块只 re-export 宿主自己的 React —— 与主应用共用同一模块实例（manualChunks 把
 * react/react-dom/scheduler 归进同一个 kernel-runtime chunk），从结构上杜绝双 React。
 * 插件侧零适配：照常 `import React, { useState } from "react"` 即可。
 *
 * 用 .js 而非 .ts：@types/react 以 `export =` 声明，`export *` 无法通过 tsc；
 * 纯 re-export shim 无需类型检查（tsc 默认不收 .js），Vite/rollup 照常构建。
 */
import * as React from "react";

export default React;
// ★ 逐名显式导出（**不是** `export *`）：react 经 CJS interop 进来，`export *` 的命名导出
//   不进入口签名，会被入口签名裁剪摇掉（实测只剩 `export{t as default}`）；
//   显式绑定 + vite.config 的 preserveEntrySignatures:"strict" 才稳。
export const {
  Children,
  Component,
  Fragment,
  Profiler,
  PureComponent,
  StrictMode,
  Suspense,
  __SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED,
  act,
  cloneElement,
  createContext,
  createElement,
  createFactory,
  createRef,
  forwardRef,
  isValidElement,
  lazy,
  memo,
  startTransition,
  unstable_act,
  useCallback,
  useContext,
  useDebugValue,
  useDeferredValue,
  useEffect,
  useId,
  useImperativeHandle,
  useInsertionEffect,
  useLayoutEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  useSyncExternalStore,
  useTransition,
  version,
} = React;