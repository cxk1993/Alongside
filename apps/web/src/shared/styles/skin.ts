/**
 * 皮肤引擎（Skin）——「一组 CSS 变量」的读写与应用。
 *
 * 设计边界（与主题、壁纸的关系）：
 *   · 主题（theme.ts）管 `data-theme="dark|light"`，决定基础令牌；
 *   · 壁纸（wallpaper.ts）管 `.desktop` 的 `--wp-*`；
 *   · 皮肤只往 `<html>` 写**内联变量**（强调色 / 圆角 / 间距 / 字体 / 阴影），
 *     不碰上面两者，也不覆盖背景与文字色 —— 所以明暗两套主题下都成立。
 *
 * 适配约定：
 *   1. 强调色只来自用户输入或 tokens.css 里的 `--skin-swatch-*`（源码里不写十六进制）；
 *   2. `--accent-fg` 由强调色的相对亮度算出，浅色/深色都保证可读；
 *   3. 圆角与间距按「基准值 × 系数」缩放，基准值在首次应用前从令牌里读一次并缓存；
 *   4. 旧配置（缺字段 / 脏值 / 非本版本）一律静默回退默认，不抛错、不白屏。
 */

export type Density = "compact" | "standard" | "comfortable";
export type FontChoice = "sans" | "mono" | "serif";
export type ShadowChoice = "soft" | "standard" | "strong";

export interface Skin {
  version: 1;
  /** CSS 颜色值（#rrggbb / rgb() / hsl()）。 */
  accent: string;
  /** 圆角系数：0.5（直角）~ 1.5（很圆）。 */
  radius: number;
  density: Density;
  font: FontChoice;
  shadow: ShadowChoice;
}

export const SKIN_KEY = "alongside.skin.v1";
export const RADIUS_MIN = 0.5;
export const RADIUS_MAX = 1.5;

/** 色板：变量名 → 中文名。颜色值只在 tokens.css 里定义。 */
export const SWATCHES: ReadonlyArray<{ varName: string; label: string }> = [
  { varName: "--skin-swatch-blue", label: "海蓝" },
  { varName: "--skin-swatch-violet", label: "夜紫" },
  { varName: "--skin-swatch-teal", label: "青碧" },
  { varName: "--skin-swatch-green", label: "新绿" },
  { varName: "--skin-swatch-amber", label: "琥珀" },
  { varName: "--skin-swatch-rose", label: "玫红" },
  { varName: "--skin-swatch-cyan", label: "天青" },
  { varName: "--skin-swatch-slate", label: "岩灰" },
];

export const DENSITY_FACTOR: Record<Density, number> = {
  compact: 0.85,
  standard: 1,
  comfortable: 1.15,
};

export const DENSITY_LABEL: Record<Density, string> = {
  compact: "紧凑",
  standard: "标准",
  comfortable: "宽松",
};

export const FONT_LABEL: Record<FontChoice, string> = {
  sans: "无衬线",
  mono: "等宽",
  serif: "衬线",
};

export const SHADOW_LABEL: Record<ShadowChoice, string> = {
  soft: "轻",
  standard: "标准",
  strong: "重",
};

/* ───────── 基准值（首次应用前从令牌读一次） ───────── */

interface Base {
  radii: [number, number, number, number];
  spaces: number[];
  fonts: Record<FontChoice, string>;
}

const RADIUS_VARS = ["--r-xs", "--r-sm", "--r-md", "--r-lg"] as const;
const SPACE_VARS = ["--sp-1", "--sp-2", "--sp-3", "--sp-4", "--sp-5", "--sp-6"] as const;

/** 没有 CSS 环境（测试 / 极端降级）时的兜底，与 tokens.css 的默认值一致。 */
const FALLBACK_BASE: Base = {
  radii: [4, 6, 10, 14],
  spaces: [4, 8, 12, 16, 24, 32],
  fonts: {
    sans: "system-ui, sans-serif",
    mono: "ui-monospace, monospace",
    serif: "Georgia, serif",
  },
};

let baseCache: Base | null = null;

function cssVar(name: string): string {
  if (typeof document === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * 取 localStorage 的稳妥入口。
 * ★ 不直接写 `localStorage`：在 jsdom（测试）与部分隐私模式下它是**未定义的全局**，
 *   裸引用会 ReferenceError，而 try/catch 会把「没存上」伪装成「存上了」。
 */
function storage(): Storage | null {
  try {
    return typeof window !== "undefined" && window.localStorage ? window.localStorage : null;
  } catch {
    return null;
  }
}

function pxNumber(value: string): number | null {
  const m = /^(-?\d+(?:\.\d+)?)px$/.exec(value);
  return m ? Number(m[1]) : null;
}

export function readBase(): Base {
  if (baseCache) return baseCache;
  const radii = RADIUS_VARS.map((v, i) => pxNumber(cssVar(v)) ?? FALLBACK_BASE.radii[i]);
  const spaces = SPACE_VARS.map((v, i) => pxNumber(cssVar(v)) ?? FALLBACK_BASE.spaces[i]);
  baseCache = {
    radii: [radii[0], radii[1], radii[2], radii[3]] as [number, number, number, number],
    spaces,
    fonts: {
      sans: cssVar("--font-sans") || FALLBACK_BASE.fonts.sans,
      mono: cssVar("--font-mono") || FALLBACK_BASE.fonts.mono,
      serif: cssVar("--font-serif") || FALLBACK_BASE.fonts.serif,
    },
  };
  return baseCache;
}

/** 仅供测试：清掉基准缓存（模拟另一套令牌）。 */
export function _resetBaseCache(): void {
  baseCache = null;
}

/* ───────── 颜色 ───────── */

const HEX_RE = /^#[0-9a-f]{6}$/i;
const RGB_RE = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i;
const HSL_RE = /^hsla?\(\s*([\d.]+)(?:deg)?[\s,]+([\d.]+)%[\s,]+([\d.]+)%/i;

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const hp = (h % 360) / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  const [r1, g1, b1] = (
    hp < 1
      ? [c, x, 0]
      : hp < 2
        ? [x, c, 0]
        : hp < 3
          ? [0, c, x]
          : hp < 4
            ? [0, x, c]
            : hp < 5
              ? [x, 0, c]
              : [c, 0, x]
  ) as [number, number, number];
  const m = l - c / 2;
  return [Math.round((r1 + m) * 255), Math.round((g1 + m) * 255), Math.round((b1 + m) * 255)];
}

/** 解析颜色 → [r,g,b]；解析不了返回 null（调用方据此回退）。 */
export function parseColor(value: string): [number, number, number] | null {
  const raw = (value || "").trim();
  const hex = HEX_RE.exec(raw);
  if (hex) {
    return [
      parseInt(raw.slice(1, 3), 16),
      parseInt(raw.slice(3, 5), 16),
      parseInt(raw.slice(5, 7), 16),
    ];
  }
  const rgb = RGB_RE.exec(raw);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
  const hsl = HSL_RE.exec(raw);
  if (hsl) return hslToRgb(Number(hsl[1]), Number(hsl[2]) / 100, Number(hsl[3]) / 100);
  return null;
}

export function isColor(value: string): boolean {
  return parseColor(value) !== null;
}

function luminance([r, g, b]: [number, number, number]): number {
  const channel = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** 强调色上的文字色：按相对亮度二选一，保证两套主题下都可读。 */
export function contrastFg(color: string): string {
  const rgb = parseColor(color);
  if (!rgb) return "rgb(255, 255, 255)";
  // 0.42 比 WCAG 的 0.5 分界更保守：品牌色多偏亮，深色字更容易读。
  return luminance(rgb) > 0.42 ? "rgb(0, 0, 0)" : "rgb(255, 255, 255)";
}

/** 强调色的 15% 透明版本（用于 --accent-soft / focus ring）。 */
export function softColor(color: string): string {
  const rgb = parseColor(color);
  if (!rgb) return "rgba(91, 140, 255, 0.15)";
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.15)`;
}

/** #rrggbb 形式的颜色（供 <input type="color"> 使用）；非十六进制时转成 hex。 */
export function toHex6(color: string): string {
  const rgb = parseColor(color);
  if (!rgb) return "#000000";
  return `#${rgb.map((v) => Math.max(0, Math.min(255, v)).toString(16).padStart(2, "0")).join("")}`;
}

/* ───────── 规范化 / 存取 ───────── */

function clampRadius(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return 1;
  return Math.min(RADIUS_MAX, Math.max(RADIUS_MIN, Math.round(n * 100) / 100));
}

function pickEnum<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return (allowed as readonly string[]).includes(String(value)) ? (value as T) : fallback;
}

/** 把任意输入（旧配置 / 手改 / 脏值）收敛成合法皮肤；永不抛错。 */
export function normalizeSkin(raw: unknown): Skin {
  const base = defaultSkin();
  if (!raw || typeof raw !== "object") return base;
  const r = raw as Partial<Skin>;
  return {
    version: 1,
    accent: typeof r.accent === "string" && isColor(r.accent) ? r.accent : base.accent,
    radius: clampRadius(r.radius),
    density: pickEnum(r.density, ["compact", "standard", "comfortable"], "standard"),
    font: pickEnum(r.font, ["sans", "mono", "serif"], "sans"),
    shadow: pickEnum(r.shadow, ["soft", "standard", "strong"], "standard"),
  };
}

export function defaultSkin(): Skin {
  const accent = cssVar("--accent") || "rgb(91, 140, 255)";
  return {
    version: 1,
    accent: isColor(accent) ? accent : "rgb(91, 140, 255)",
    radius: 1,
    density: "standard",
    font: "sans",
    shadow: "standard",
  };
}

export function loadSkin(): Skin {
  const store = storage();
  if (!store) return defaultSkin();
  try {
    const raw = store.getItem(SKIN_KEY);
    if (!raw) return defaultSkin();
    return normalizeSkin(JSON.parse(raw));
  } catch {
    return defaultSkin();
  }
}

export function saveSkin(skin: Skin): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(SKIN_KEY, JSON.stringify(skin));
  } catch {
    /* 隐私模式 / 配额满：皮肤仍生效，只是不持久化 */
  }
}

/* ───────── 应用 ───────── */

const RADIUS_PROPS = ["--r-xs", "--r-sm", "--r-md", "--r-lg"] as const;
const SPACE_PROPS = ["--sp-1", "--sp-2", "--sp-3", "--sp-4", "--sp-5", "--sp-6"] as const;

function shadowPair(choice: ShadowChoice): [string, string] {
  if (choice === "standard") return [cssVar("--shadow-1"), cssVar("--shadow-2")];
  if (choice === "soft") return [cssVar("--skin-shadow-1-soft"), cssVar("--skin-shadow-2-soft")];
  return [
    cssVar("--skin-shadow-1-strong") || cssVar("--shadow-1"),
    cssVar("--skin-shadow-2-strong"),
  ];
}

export function applySkin(skin: Skin): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const base = readBase();

  root.style.setProperty("--accent", skin.accent);
  root.style.setProperty("--accent-fg", contrastFg(skin.accent));
  root.style.setProperty("--accent-soft", softColor(skin.accent));

  RADIUS_PROPS.forEach((prop, i) => {
    root.style.setProperty(prop, `${Math.round(base.radii[i] * skin.radius)}px`);
  });

  const factor = DENSITY_FACTOR[skin.density];
  SPACE_PROPS.forEach((prop, i) => {
    root.style.setProperty(prop, `${Math.round(base.spaces[i] * factor)}px`);
  });

  root.style.setProperty("--font-sans", base.fonts[skin.font]);

  const [s1, s2] = shadowPair(skin.shadow);
  if (skin.shadow === "standard") {
    root.style.removeProperty("--shadow-1");
    root.style.removeProperty("--shadow-2");
  } else {
    if (s1) root.style.setProperty("--shadow-1", s1);
    if (s2) root.style.setProperty("--shadow-2", s2);
  }
}

/** 撤销全部皮肤内联变量，回到令牌原样。 */
export function clearSkin(): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  [
    "--accent",
    "--accent-fg",
    "--accent-soft",
    ...RADIUS_PROPS,
    ...SPACE_PROPS,
    "--font-sans",
    "--shadow-1",
    "--shadow-2",
  ].forEach((prop) => root.style.removeProperty(prop));
  root.removeAttribute("data-skin");
  const store = storage();
  try {
    store?.removeItem(SKIN_KEY);
  } catch {
    /* 同上 */
  }
}

/** 启动时调用：读出持久化皮肤并应用（无皮肤时什么也不做，保持令牌默认）。 */
export function initSkin(): Skin | null {
  const store = storage();
  if (!store) return null;
  let raw: string | null = null;
  try {
    raw = store.getItem(SKIN_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  const skin = normalizeSkin(parsed);
  applySkin(skin);
  document.documentElement.setAttribute("data-skin", "custom");
  return skin;
}

/** 面板用：把色板变量读成实际颜色（颜色值只在 tokens.css 里写一次）。 */
export function readSwatches(): Array<{ label: string; value: string }> {
  return SWATCHES.map(({ varName, label }) => ({ label, value: cssVar(varName) })).filter((s) =>
    isColor(s.value),
  );
}
