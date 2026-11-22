import { describe, expect, it } from "vitest";
// 本仓库刻意不装 @types/node；node:fs 的形状声明在 src/apps/collab/node-fs.d.ts（全局环境声明）。
import { readFileSync } from "node:fs";

/**
 * ★ P0-bug 回归钉子（2026-11-22 凝析）：顶栏必须给「◉ 罗盘」留实位，
 *   罗盘不许盖在主题 / 登出上。
 *
 * 为什么钉规则而不是渲染：这是纯样式 bug，jsdom 没有排版与命中测试，
 * 渲染测试永远量不出「看得见、点不到」。
 *
 * 实测（5188 / 1366×720 / main ddc423d，修复前）：
 *   罗盘 fixed 矩形 x∈[1285,1354] y∈[10,42]，z-index 95000 > 顶栏 90000；
 *   `.topbar__actions` x∈[1278,1350] → elementFromPoint(主题 1294,24 / 登出 1334,24)
 *   两个都命中 BUTTON.desktop__compass-btn（self=false），主题与登出全被吃掉。
 * 修复后（同视口）：主题 x∈[1194,1226]、登出 x∈[1234,1266]，均命中自身；
 *   真鼠标点击 (1210,24) → isTrusted click 落在 BUTTON.icon-btn，主题 dark→light→dark；
 *   1366 / 1024 / 900 / 768 四档宽度均无重叠。
 *
 * 为什么钉「规格」而不钉具体像素坐标：坐标随窗口宽度变，钉死会假红；
 *   不变量是两条 —— ① 顶栏右侧留位 ≥ 罗盘宽 + 右距；② 罗盘 z 高于顶栏。
 *   有人把留位删掉（回到叠压）或把 z 调低（罗盘自己被盖）都会立刻红。
 *
 * ⚠️ 不可用 `import css from "./desktop.css?raw"`：vitest 下拿到空串（实测 LEN 0），
 *   所以从磁盘读同一份文件（只读，不改）。cwd = apps/web。
 */
const css = readFileSync("src/kernel/desktop.css", "utf8");
const tokens = readFileSync("src/shared/styles/tokens.css", "utf8");

const NL = String.fromCharCode(10);

function stripComments(s: string): string {
  return s.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** 取选择器自身的声明块（选择器须独占行首；同选择器多次出现则合并）。 */
function blockOf(source: string, selector: string): string {
  const s = stripComments(source);
  const out: string[] = [];
  let i = 0;
  while ((i = s.indexOf(selector, i)) !== -1) {
    const lineStart = s.lastIndexOf(NL, i) + 1;
    const ownLine = s.slice(lineStart, i).trim() === "";
    const open = s.indexOf("{", i);
    const close = s.indexOf("}", open);
    if (open === -1 || close === -1) break;
    if (ownLine) out.push(s.slice(open + 1, close));
    i = close;
  }
  return out.join(NL);
}

function tokenPx(source: string, name: string): number {
  const m = source.match(new RegExp(`${name}\\s*:\\s*(\\d+)px`));
  return m ? Number(m[1]) : NaN;
}

const topbar = blockOf(css, ".topbar");
const compass = blockOf(css, ".desktop__compass-btn");

describe("罗盘与顶栏右侧控件的占位契约（P0-bug 回归钉子）", () => {
  it("读到的是真样式表（空串会让下面几条假绿）", () => {
    expect(css.length).toBeGreaterThan(10000);
    expect(topbar.length).toBeGreaterThan(50);
    expect(compass.length).toBeGreaterThan(50);
  });

  it("设计令牌仍是 12/16（留位算式的前提，变了要回来复核这条规则）", () => {
    expect(tokenPx(tokens, "--sp-3")).toBe(12);
    expect(tokenPx(tokens, "--sp-4")).toBe(16);
  });

  it("顶栏右侧留位 ≥ 罗盘宽 69 —— 主题/登出不被叠压", () => {
    // 期望形态：padding: 0 calc(var(--sp-4) + Npx) 0 var(--sp-4);
    const m = topbar.match(
      /padding:\s*0\s+calc\(var\(--sp-4\)\s*\+\s*(\d+)px\)\s+0\s+var\(--sp-4\)/,
    );
    expect(m, "顶栏右侧未留位（回到「罗盘压住主题/登出」的状态）").not.toBeNull();
    const extra = Number(m![1]);
    // 罗盘实测宽 69（1366×720，"◉ 罗盘" 13px 字形）+ 8px 呼吸位 = 77 是下限，当前 84
    expect(extra).toBeGreaterThanOrEqual(77);
  });

  it("罗盘 z-index 必须高于顶栏（低于它 = 罗盘自己被盖，收起态就没入口了）", () => {
    const zCompass = Number((compass.match(/z-index:\s*(\d+)/) ?? [])[1]);
    const zTopbar = Number((topbar.match(/z-index:\s*(\d+)/) ?? [])[1]);
    expect(Number.isFinite(zCompass)).toBe(true);
    expect(Number.isFinite(zTopbar)).toBe(true);
    expect(zCompass).toBeGreaterThan(zTopbar);
  });

  it("罗盘默认仍钉在顶栏带内（top < 顶栏高 48）—— 不许掉进窗口可拖达区", () => {
    const top = Number((compass.match(/top:\s*(\d+)px/) ?? [])[1]);
    expect(Number.isFinite(top)).toBe(true);
    expect(top).toBeLessThan(48);
  });
});