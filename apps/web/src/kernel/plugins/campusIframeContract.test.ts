/**
 * r9 · 校园内嵌窗口插件（zhidao / mooc / ucampus）的**产物契约测试**。
 *
 * 这三个插件的 UI 是单文件 ESM 产物入库（web/dist/index.js，无构建步骤、无 src）。
 * 契约测试锁住四件事：
 *   ① 沙箱形态（N3）：sandbox 必须仍含 allow-same-origin / allow-scripts，
 *      且**不得**出现 allow-top-navigation（被嵌页不得把宿主窗口导航走）；
 *      产物里不得出现 postMessage / window.parent / eval / innerHTML
 *      （宿主 origin 不经桥暴露给被嵌页）。
 *   ② 兜底动作（N1）：「重新加载」「在浏览器打开」「打不开？」三件套必须在；
 *      9 秒慢载定时器必须在 onLoad 时被清除（r8 修掉的假警报回归闸）。
 *   ③ r9 新增：弱提示**打开即显示**——首帧就常显「若窗口一直空白……」
 *      （不能等 9 秒定时器或用户点击），9 秒无 load 才升级为「迟迟未加载」措辞：
 *      job 书明确「首帧就有提示，不等 9 秒」。旧实现的 `hintOpen` 开关已删除。
 *   ④ r9 新增（ucampus 专属）：服务器强制 https→http 301（实测 2026-11-16），
 *      https 宿主内嵌必然 mixed-content 白屏 ⇒ 运行时按宿主协议兜底：
 *      https 宿主放弃 iframe、改「在浏览器打开」大按钮；http 宿主照常内嵌。
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
  // ucampus：https://u.unipus.cn/ 服务器端强制 301 → http（r9 实测），
  // 所以 TARGET 直接指向实测 200 的 http 入口，https 宿主走协议兜底。
  { id: "ucampus", target: "http://u.unipus.cn/index.html/", src: ucampus },
] as const;

describe("校园内嵌窗口插件 · 产物契约（r9）", () => {
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

    it(`[${p.id}] 弱提示打开即显示：首帧常显「若窗口一直空白」，9 秒才升级「迟迟未加载」`, () => {
      // r9 契约：首帧就有提示，不等 9 秒定时器、不等用户点「打不开？」
      // 提示条是无条件渲染的 div——timedOut 只切换 **内部文案**（三元在引号里），
      // 不是 `showHint ? 整条提示 : null` 那种条件隐藏。
      expect(p.src).toMatch(/: "若窗口一直空白：站点可能拒绝内嵌或存在 mixed-content[^"]*"/);
      // 慢载定时器升级措辞仍在（「迟迟未加载」）
      expect(p.src).toContain("迟迟未加载");
      expect(p.src).toContain("在浏览器打开");
      // 旧的「等点击/9 秒才显示」开关已删除（showHint/hintOpen 均不存在；
      // detailOpen 只负责收起详情，不影响提示条首帧可见性）
      expect(p.src).not.toMatch(/showHint/);
      expect(p.src).not.toMatch(/hintOpen/);
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

  it("[ucampus] 协议兜底：https 宿主自动放弃 iframe，改「在浏览器打开」", () => {
    // r9 实测：u.unipus.cn 无任何 https 可用入口（连 /index.html 都 301 → http），
    // https 宿主内嵌 = mixed-content 白屏 ⇒ 运行时按 window.location.protocol 判定
    expect(ucampus).toMatch(/location\.protocol\s*!==\s*"https:"/);
    // 不可内嵌时仍有「在浏览器打开」大按钮兜底（不是只显示一行字）
    expect(ucampus).toContain("在浏览器打开");
    // 协议判定有解释性注释，防止后人以为 TARGET 写 http 是笔误而改回去
    expect(ucampus).toMatch(/强制\s*https→http|https→http/);
  });
});