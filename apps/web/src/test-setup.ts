/**
 * 全局测试隔离。
 *
 * ★ 为什么需要：zustand 的 store 是**模块级单例**，vitest 的 `--singleFork`（内存紧张时的
 * 唯一跑法）下所有测试文件共享同一个模块实例。于是某个测试用
 * `useDesktopStore.setState({ modules: {...假模块...} })` 造数据、却不清理时，
 * 假模块会漏到**后面所有测试**里。
 *
 * 实测症状：`WindowFrame.caps.test.tsx` 注册了一个 `entry: "@/apps/__t22_none__"`
 * 的假模块（故意指向不存在的入口，用来验"无入口时走薄壳页"）。它单独跑绿，
 * 但和任何渲染桌面的测试一起跑，`useContainerPages` 就会在无关测试里去
 * `lazy()` 加载这个不存在的入口 → `Uncaught [Error: 未找到插件入口：@/apps/__t22_none__]`
 * → 全量跑时 128 个测试失败，**单跑却全绿**。
 *
 * 这类"单跑绿、全量红"的假失败极难排查（会让人去怀疑产品代码），
 * 所以在**全局**层面收口：每个测试结束后把注册表与窗口清空，不依赖每个测试自觉。
 *
 * 只清「注册表 / 窗口 / 工作区」这三样**测试造数据**的地方；
 * 不收 localStorage（几何持久化的测试自己管），也不重置动作函数。
 */
import { afterEach, beforeEach } from "vitest";
import { cleanup } from "@testing-library/react";

import { useDesktopStore } from "./kernel/store";

// ★ 显式 cleanup：不能指望 `globals: true` 让 RTL 自己探测 globalThis.afterEach。
//   实测引入 setupFiles 后自动 cleanup 失效——DOM 跨测试残留，WindowFrame 测试
//   读到 4 个窗口的 12 个按钮（期望 3 个），一个文件里只有 1 项失败却极难定位。
afterEach(() => {
  cleanup();
});

// ★ 用 beforeEach 而不是 afterEach 清 store：与 DOM cleanup 正交，
//   「开始前清」比「结束后清」更稳，不会和上面这个 afterEach 抢顺序。
beforeEach(() => {
  useDesktopStore.setState({
    modules: {},
    windows: [],
  });
});
