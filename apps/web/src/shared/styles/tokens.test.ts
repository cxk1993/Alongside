/**
 * 设计令牌的「单一真源 + 派生」契约测试。
 *
 * ★ 为什么不能直接 `getComputedStyle(root).getPropertyValue("--r-xs")`：
 *   jsdom **不做** 自定义属性里的 `var()` 替换与 `calc()` 求值（实测返回
 *   字面量 `calc(var(--radius) * 0.4)`），拿它当判据等于什么都没验。
 *   所以这里读 tokens.css 的**真源码**（`?raw`），按 CSS 的替换规则自己求值，
 *   再把结果与「改造前的四个硬编码像素 + 两条 8 位十六进制阴影」逐项对齐 ——
 *   这才是「视觉零变化」的可判定形式。
 *
 * 真浏览器里的 `getComputedStyle` 结果另由人工验收记录（见 agentF-tokens.md），
 * 因为 jsdom 无论如何都给不出解析后的值。
 */
import { describe, expect, it } from "vitest";

import tokensCss from "./tokens.css?raw";

import { SPACE_BASE } from "./skin";

/** 抽出某个选择器块的声明（只支持本文件用的单层块；先剥注释，再按 `;` 切分）。 */
function block(css: string, selectorRe: RegExp): Record<string, string> {
  const m = selectorRe.exec(css);
  if (!m) throw new Error(`找不到选择器 ${selectorRe}`);
  const open = css.indexOf("{", m.index);
  const close = css.indexOf("}", open);
  const body = css.slice(open + 1, close).replace(/\/\*[\s\S]*?\*\//g, "");
  const out: Record<string, string> = {};
  for (const decl of body.split(";")) {
    const i = decl.indexOf(":");
    if (i < 0) continue;
    const name = decl.slice(0, i).trim();
    const value = decl
      .slice(i + 1)
      .replace(/\s+/g, " ")
      .trim();
    if (/^(--[\w-]+|color-scheme)$/.test(name) && value) out[name] = value;
  }
  return out;
}

const DARK = block(tokensCss, /:root,\s*\n\[data-theme="dark"\]\s*\{/);
// 注释里也提到过 `[data-theme="light"]`，所以必须要求「选择器后紧跟 {」。
// ★ 亮色块是**部分覆盖**（只重写需要变的变量），真实级联是 dark ← light，这里照抄这个语义。
const LIGHT = { ...DARK, ...block(tokensCss, /\[data-theme="light"\]\s*\{/) };

/** 反复替换 `var(--x)` 直到没有变量；每轮只替换一层，天然支持链式派生。 */
function resolveVars(value: string, scope: Record<string, string>): string {
  let out = value;
  for (let i = 0; i < 12 && out.includes("var("); i += 1) {
    out = out.replace(/var\(\s*(--[\w-]+)\s*\)/g, (whole, name: string) => scope[name] ?? whole);
  }
  return out;
}

/** 求值本文件用到的极简算术：`calc(10px * 0.4)` / `calc(0.6 * 1)`。 */
function evalCalc(value: string): string {
  return value.replace(/calc\(([^()]+)\)/g, (_whole, expr: string) => {
    const mul = /^\s*(-?[\d.]+)(px)?\s*\*\s*(-?[\d.]+)(px)?\s*$/.exec(expr);
    if (mul) {
      const n = Number(mul[1]) * Number(mul[3]);
      const unit = mul[2] ?? mul[4] ?? "";
      // 与浏览器一致：像素值不做小数截断（4.000000000000001px 也等价于 4px）。
      return `${Math.round(n * 1e6) / 1e6}${unit}`;
    }
    const raw = /^\s*(-?[\d.]+)(px)?\s*$/.exec(expr);
    if (raw) return `${Number(raw[1])}${raw[2] ?? ""}`;
    throw new Error(`不支持的 calc 表达式：${expr}`);
  });
}

function resolve(name: string, scope: Record<string, string> = DARK): string {
  const raw = scope[name];
  if (raw === undefined) throw new Error(`没有令牌 ${name}`);
  return evalCalc(resolveVars(raw, scope));
}

describe("圆角：单一真源 + 乘法派生", () => {
  it("只有 --radius 一个基准值，其余全是 calc() 派生", () => {
    expect(DARK["--radius"]).toBe("10px");
    expect(DARK["--r-md"]).toBe("var(--radius)");
    expect(DARK["--r-xs"]).toBe("calc(var(--radius) * 0.4)");
    expect(DARK["--r-sm"]).toBe("calc(var(--radius) * 0.6)");
    expect(DARK["--r-lg"]).toBe("calc(var(--radius) * 1.4)");
    expect(DARK["--r-pill"]).toBe("999px");
  });

  it("解析后与改造前的 4 / 6 / 10 / 14 px 逐一相等（视觉零变化）", () => {
    expect(resolve("--r-xs")).toBe("4px");
    expect(resolve("--r-sm")).toBe("6px");
    expect(resolve("--r-md")).toBe("10px");
    expect(resolve("--r-lg")).toBe("14px");
  });

  it("改基准会穿透到四档（这是「单一真源」的实际收益）", () => {
    const scope = { ...DARK, "--radius": "20px" };
    expect(resolve("--r-xs", scope)).toBe("8px");
    expect(resolve("--r-sm", scope)).toBe("12px");
    expect(resolve("--r-md", scope)).toBe("20px");
    expect(resolve("--r-lg", scope)).toBe("28px");
  });

  it("直角主题（--radius: 0px）各档一起归零，不会出现负值", () => {
    const scope = { ...DARK, "--radius": "0px" };
    for (const v of ["--r-xs", "--r-sm", "--r-md", "--r-lg"]) {
      expect(resolve(v, scope)).toBe("0px");
    }
  });
});

describe("阴影：颜色 × 强度两个旋钮", () => {
  /** `0 1px 2px rgb(0 0 0 / 0.501961)` → { geometry, channels, alpha } */
  function shadow(name: string, scope = DARK) {
    const v = resolve(name, scope);
    const m = /^(.+?)\s+rgb\(\s*([\d\s]+)\s*\/\s*([\d.]+)\s*\)$/.exec(v);
    if (!m) throw new Error(`阴影形状不认识：${v}`);
    return {
      geometry: m[1].trim(),
      channels: m[2].trim().split(/\s+/).map(Number),
      alpha: Number(m[3]),
    };
  }

  it("dark：几何不变，颜色为纯黑", () => {
    const s1 = shadow("--shadow-1");
    const s2 = shadow("--shadow-2");
    expect(s1.geometry).toBe("0 1px 2px");
    expect(s2.geometry).toBe("0 12px 40px");
    expect(s1.channels).toEqual([0, 0, 0]);
    expect(s2.channels).toEqual([0, 0, 0]);
  });

  it("dark：alpha 折算回 8 位整数后等于改造前的 #00000080 / #00000099", () => {
    // 8 位十六进制的 0x80 = 128，0x99 = 153；浏览器把 alpha 存成 8 位 → 必须落在同一格。
    expect(Math.round(shadow("--shadow-1").alpha * 255)).toBe(0x80);
    expect(Math.round(shadow("--shadow-2").alpha * 255)).toBe(0x99);
  });

  it("light：颜色 26 34 51 = 原 #1a2233，alpha 回折算等于 #1a22331f / #1a22333d", () => {
    const s1 = shadow("--shadow-1", LIGHT);
    const s2 = shadow("--shadow-2", LIGHT);
    expect(s1.geometry).toBe("0 1px 2px");
    expect(s2.geometry).toBe("0 12px 40px");
    expect(s1.channels).toEqual([0x1a, 0x22, 0x33]);
    expect(s2.channels).toEqual([0x1a, 0x22, 0x33]);
    expect(Math.round(s1.alpha * 255)).toBe(0x1f);
    expect(Math.round(s2.alpha * 255)).toBe(0x3d);
  });

  it("--shadow-strength 是唯一倍率：调 0 等于没有阴影，调 2 等于双倍", () => {
    for (const name of ["--shadow-1", "--shadow-2"]) {
      expect(shadow(name, { ...DARK, "--shadow-strength": "0" }).alpha).toBe(0);
      const base = shadow(name).alpha;
      expect(shadow(name, { ...DARK, "--shadow-strength": "2" }).alpha).toBeCloseTo(base * 2, 5);
    }
  });
});

describe("color-scheme：原生控件配色", () => {
  it("默认（dark）与 light 各声明一次", () => {
    expect(DARK["color-scheme"]).toBe("dark");
    expect(LIGHT["color-scheme"]).toBe("light");
  });
});

describe("与 skin.ts 的常量保持一致", () => {
  it("--sp-1..6 必须等于 SPACE_BASE（改令牌忘了改常量会在这里红）", () => {
    const props = ["--sp-1", "--sp-2", "--sp-3", "--sp-4", "--sp-5", "--sp-6"] as const;
    props.forEach((p, i) => expect(resolve(p)).toBe(`${SPACE_BASE[i]}px`));
  });

  it("--radius 等于 RADIUS_BASE 的像素值", () => {
    expect(resolve("--radius")).toBe("10px");
  });
});
