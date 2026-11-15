/**
 * r8 · 校园内嵌窗口插件（zhidao / mooc / ucampus）的**产物契约测试**。
 *
 * 这三个插件的 UI 是单文件 ESM 产物入库（web/dist/index.js，无构建步骤、无 src）。
 * 契约测试锁住两件事：
 *   ① 沙箱形态（N3）：sandbox 必须仍含 allow-same-origin / allow-scripts，
 *      且**不得**出现 allow-top-navigation（被嵌页不得把宿主窗口导航走）；
 *      产物里不得出现 postMessage / window.parent / eval / innerHTML
 *      （宿主 origin 不经桥暴露给被嵌页）。
 *   ② 兜底动作（N1）：「重新加载」「在浏览器打开」「打不开？」三件套必须在；
 *      9 秒慢载定时器必须在 onLoad 时被清除（r8 修掉的假警报回归闸）。
 *
 * ★ 用 Vite 的 `?raw` 读产物文本，不引 node:fs —— 本仓库刻意不装 @types/node
 *   （见 src/vite-env.d.ts 的说明），`?raw` 由 vite/client 类型与转译层提供。
 *   不执行产物：它 import 裸说明符 react，由宿主 import map 解析，jsdom 下不可独立执行。
 */
import { describe, it, expect } from "vitest";

import zhidao from "../../../../../plugins/zhidao/web/dist/index.js?raw";
import mooc from "../../../../../plugins/mooc/web/dist/index.js?raw";
import ucampus from "../../../../../plugins/ucampus/web/dist/index.js?raw";

const CAMPUS = [
  { id: "zhidao", target: "https://onlinestuh5.zhihuishu.com/", src: zhidao },
  { id: "mooc", target: "https://www.icourse163.org/", src: mooc },
  { id: "ucampus", target: "https://u.unipus.cn/", src: ucampus },
] as const;

describe("校园内嵌窗口插件 · 产物契约（r8）", () => {
  for (const p of CAMPUS) {
    it(`[${p.id}] 产物存在且指向原站 ${p.target}`, () => {
      expect(p.src).toContain(p.target);
    });

    it(`[${p.id}] 沙箱：五旗 + allow-same-origin，且无 allow-top-navigation`, () => {
      const m = /sandbox:\s*"([^"]+)"/.exec(p.src);
      expect(m, "sandbox 属性必须存在").toBeTruthy();
      const flags = (m as RegExpExecArray)[1].split(/\s+/);
      for (const f of [
        "allow-same-origin",
        "allow-scripts",
        "allow-forms",
        "allow-popups",
        "allow-popups-to-escape-sandbox",
        "allow-downloads",
      ]) {
        expect(flags, `缺 ${f}`).toContain(f);
      }
      // 被嵌页不得把宿主窗口导航走（tabnabbing / frame-busting 防线）
      expect(flags).not.toContain("allow-top-navigation");
    });

    it(`[${p.id}] 无宿主 origin 暴露路径：postMessage / window.parent / eval / innerHTML`, () => {
      expect(p.src).not.toMatch(/postMessage/);
      expect(p.src).not.toMatch(/window\.parent/);
      expect(p.src).not.toMatch(/eval\s*\(/);
      expect(p.src).not.toMatch(/innerHTML/);
    });

    it(`[${p.id}] 兜底三件套 + 慢载定时器在 onLoad 时被清除（假警报回归闸）`, () => {
      expect(p.src).toContain("重新加载");
      expect(p.src).toContain("在浏览器打开");
      expect(p.src).toContain("打不开？");
      // onLoad 处理器里 clearTimeout（r8：加载完成后 9 秒定时器必须停，
      // 否则已渲染的页面也会弹「一直空白？」——2026-11-16 真机实测的假警报）
      const onLoadIdx = p.src.indexOf("const onLoad = () =>");
      expect(onLoadIdx).toBeGreaterThan(-1);
      const clearIdx = p.src.indexOf("clearTimeout(timer.current)", onLoadIdx);
      expect(clearIdx).toBeGreaterThan(onLoadIdx);
      // key={n} 语义仍在（「重新加载」= 重建 iframe，不是只改 src）
      expect(p.src).toContain("key: n");
    });

    it(`[${p.id}] 产物为单文件 ESM 且 react 走共享运行时（不自带第二份 React）`, () => {
      expect(p.src).toMatch(/^import React from "react";/);
      expect(p.src).not.toMatch(/from "https?:\/\//); // 不引 CDN（离线可用契约）
      expect(p.src).toMatch(/export default \{ manifestId: "(zhidao|mooc|ucampus)"/);
    });
  }
});
