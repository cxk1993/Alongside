/**
 * 皮肤引擎（Skin）——「一份 CSS 变量表」的读写、校验与应用。
 *
 * 设计边界（与主题、壁纸的关系）：
 *   · 主题（theme.ts）管 `data-theme="dark|light"`，决定基础令牌；
 *   · 壁纸（wallpaper.ts）管 `.desktop` 的 `--wp-*`；
 *   · 皮肤只往 `<html>` 写**内联变量**，整表写入整表撤销（VS Code 同款），
 *     不碰壁纸，也不依赖任何执行能力 —— 皮肤是**纯数据**。
 *
 * 格式（v2）：对齐 shadcn `registry-item.json` 的 `cssVars: { theme, light, dark }` 三分区。
 *   · `tokens.theme` 与明暗无关（强调色、`--radius`、`--sp-*`、`--font-*`、`--shadow-strength`）；
 *   · `tokens.light` / `tokens.dark` 是两套主题各自的覆盖值；
 *   · `base` 只声明「这份皮肤是按哪套底子调的」，切换主题时按当前 `data-theme` 选分区。
 *   这样 shadcn 主题 JSON 可以直接喂进来（`--accent` 语义不同，导入侧需映射）。
 *
 * 适配约定：
 *   1. 皮肤包**不含代码**，只有变量名 → 值；写之前必须过 `validateSkinPack()`；
 *   2. 值里出现 `var(` / `url(` / `!important` / `}` 一律拒绝 —— 那是往外扩权的口子；
 *   3. `--accent-fg` / `--accent-soft` 只在皮肤显式给了 `--accent` 而没给这两项时自动推导；
 *   4. 旧配置（v1 五标量 / 脏值 / 非本版本）一律先迁移再校验，永不抛错、不白屏。
 */

export type Density = "compact" | "standard" | "comfortable";
export type FontChoice = "sans" | "mono" | "serif";
export type ShadowChoice = "soft" | "standard" | "strong";
export type SkinBase = "dark" | "light";

/** 变量表：变量名 → CSS 值。键必须带 `--` 前缀。 */
export type TokenTable = Record<string, string>;

export interface SkinMeta {
  name?: string;
  author?: string;
  license?: string;
}

/** v2：三分区（与 shadcn registry-item 的 cssVars 同构）。 */
export interface Skin {
  version: 2;
  /** 这份皮肤是按哪套底色调的；切主题时按当前 `data-theme` 选 `light` / `dark` 分区。 */
  base: SkinBase;
  tokens: {
    theme?: TokenTable;
    light?: TokenTable;
    dark?: TokenTable;
  };
  meta?: SkinMeta;
}

/** 老版本的扁平结构，只用于迁移识别。 */
export interface SkinV1 {
  version: 1;
  accent: string;
  radius: number;
  density: Density;
  font: FontChoice;
  shadow: ShadowChoice;
}

/**
 * ★ 键名保持 `alongside.skin.v1` 不变 —— 值结构升级到 v2，但**存储位不能动**：
 *   迁移动力恰恰来自「读到老的这份数据」，换键等于把老用户的皮肤直接丢掉。
 */
export const SKIN_KEY = "alongside.skin.v1";
export const RADIUS_MIN = 0.5;
export const RADIUS_MAX = 1.5;
/** 令牌的圆角基准（与 tokens.css 的 `--radius` 默认值一致）。 */
export const RADIUS_BASE = 10;

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

/* ───────── 基准值 ───────── */

/**
 * ★ 圆角不再有「基准表」：唯一真源是 `--radius`（tokens.css），四档由 CSS `calc()` 派生。
 *   皮肤只写一个 `--radius`，`baseCache` 那类「读一次永不失效」的坑从结构上消失。
 *
 * 间距与字体档位仍需要具体值（皮肤给的是「密度 → --sp-1..6」），这里只留**默认值常量**，
 * 由 `tokens.test.ts` 对着 tokens.css 的实际声明做一致性断言 —— 改令牌忘了改这里会红。
 */
export const SPACE_BASE: readonly [number, number, number, number, number, number] = [
  4, 8, 12, 16, 24, 32,
];

const SPACE_PROPS = ["--sp-1", "--sp-2", "--sp-3", "--sp-4", "--sp-5", "--sp-6"] as const;

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

/**
 * 当前圆角基准（像素）。**不缓存** —— 每次读实时的 `--radius`，
 * 这样令牌值变了、或者别的皮肤改过 `--radius`，都不会拿到过期快照。
 * 只在 v1 迁移（系数 → 像素）时用到。
 */
export function readRadiusBase(): number {
  return pxNumber(cssVar("--radius")) ?? pxNumber(cssVar("--r-md")) ?? RADIUS_BASE;
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

const FONT_FALLBACK: Record<FontChoice, string> = {
  sans: "system-ui, sans-serif",
  mono: "ui-monospace, monospace",
  serif: "Georgia, serif",
};

/**
 * 阴影档位 → `--shadow-strength` 倍率（1 = 令牌原样）。
 *
 * 取代旧的 `shadowPair()` 三分支：几何不再重写，只改一个倍率，
 * 亮色下同一倍率自然更淡（亮色的基准不透明度本来就低）。
 */
export const SHADOW_STRENGTH: Record<ShadowChoice, number> = {
  soft: 0.6,
  standard: 1,
  strong: 1.6,
};

function resolveFont(choice: FontChoice): string {
  const varName =
    choice === "mono" ? "--font-mono" : choice === "serif" ? "--font-serif" : "--font-sans";
  return cssVar(varName) || FONT_FALLBACK[choice];
}

function currentThemeBase(): SkinBase {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

/** 空皮肤：不含任何令牌 → 应用后等于令牌原样。 */
export function defaultSkin(): Skin {
  return { version: 2, base: currentThemeBase(), tokens: {} };
}

/* ───────── v1 → v2 迁移 ───────── */

/** 面板可调的五个标量（v1 的全部内容，也是面板的 UI 状态形状）。 */
export interface SkinChoices {
  accent: string;
  radius: number;
  density: Density;
  font: FontChoice;
  shadow: ShadowChoice;
}

/** 面板初值：全部取「令牌原样」，即不含任何覆盖。 */
export function defaultChoices(): SkinChoices {
  const accent = cssVar("--accent");
  return {
    accent: isColor(accent) ? accent : "rgb(91, 140, 255)",
    radius: 1,
    density: "standard",
    font: "sans",
    shadow: "standard",
  };
}

/**
 * 标量 → v2 皮肤：折算成 `tokens.theme` 里的**具体变量值**。
 *
 * 为什么折算成具体值而不是保留系数：v2 的 `--radius` 是唯一真源，
 * 「系数」属于面板的显示概念，不属于皮肤包；一个数字进来、一组变量出去，
 * shadcn 主题（它们也只存具体值）才能与我们的包同构。
 */
export function skinFromChoices(input: Partial<SkinChoices>): Skin {
  const accentRaw = input.accent;
  const accent = typeof accentRaw === "string" && isColor(accentRaw) ? accentRaw : "";
  const radius = clampRadius(input.radius);
  const factor =
    DENSITY_FACTOR[pickEnum(input.density, ["compact", "standard", "comfortable"], "standard")];
  const theme: TokenTable = {};

  if (accent) {
    theme["--accent"] = accent;
    theme["--accent-fg"] = contrastFg(accent);
    theme["--accent-soft"] = softColor(accent);
  }

  theme["--radius"] = `${Math.round(readRadiusBase() * radius)}px`;
  SPACE_PROPS.forEach((prop, i) => {
    theme[prop] = `${Math.round(SPACE_BASE[i] * factor)}px`;
  });
  theme["--font-sans"] = resolveFont(pickEnum(input.font, ["sans", "mono", "serif"], "sans"));
  theme["--shadow-strength"] = String(
    SHADOW_STRENGTH[pickEnum(input.shadow, ["soft", "standard", "strong"], "standard")],
  );

  return { version: 2, base: currentThemeBase(), tokens: { theme } };
}

/** v1 扁平结构 → v2（脏值走 `skinFromChoices` 的同一套收敛规则）。 */
export function migrateV1(v1: SkinV1): Skin {
  return skinFromChoices({
    accent: v1.accent,
    radius: v1.radius,
    density: v1.density,
    font: v1.font,
    shadow: v1.shadow,
  });
}

/**
 * v2 皮肤 → 面板标量（`skinFromChoices` 的反向读法，只用于把已存皮肤还原成控件位置）。
 * 读不出来的一律回到「令牌原样」，所以老包 / 手改包都不会让面板显示成乱码。
 */
export function choicesFromSkin(skin: Skin): SkinChoices {
  const table = resolveTokens(skin, skin.base);
  const fallback = defaultChoices();

  const accent = table["--accent"];
  const radiusPx = table["--radius"] ? pxNumber(table["--radius"]) : null;
  const sp1 = table["--sp-1"] ? pxNumber(table["--sp-1"]) : null;
  const font = table["--font-sans"] ?? "";
  const strength = Number(table["--shadow-strength"]);

  const density: Density =
    sp1 === null
      ? "standard"
      : ((["compact", "standard", "comfortable"] as const).find(
          (d) => Math.abs(Math.round(SPACE_BASE[0] * DENSITY_FACTOR[d]) - sp1) < 0.51,
        ) ?? "standard");

  const fontChoice: FontChoice = /mono/i.test(font)
    ? "mono"
    : /serif|georgia/i.test(font)
      ? "serif"
      : "sans";
  const shadow: ShadowChoice =
    Number.isFinite(strength) && strength > 0
      ? ((["soft", "standard", "strong"] as const).find(
          (s) => Math.abs(SHADOW_STRENGTH[s] - strength) < 0.01,
        ) ?? "standard")
      : "standard";

  return {
    accent: accent && isColor(accent) ? accent : fallback.accent,
    radius:
      radiusPx === null ? 1 : clampRadius(Math.round((radiusPx / readRadiusBase()) * 100) / 100),
    density,
    font: fontChoice,
    shadow,
  };
}

function isV1Shape(raw: unknown): raw is SkinV1 {
  if (!raw || typeof raw !== "object") return false;
  const r = raw as Record<string, unknown>;
  return r.version === 1 || ("accent" in r && "radius" in r && !("tokens" in r));
}

function sanitizeTable(raw: unknown): TokenTable | undefined {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const out: TokenTable = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    const key = k.startsWith("--") ? k : `--${k}`; // shadcn 的 radius 是不带前缀的
    if (typeof v === "string" || typeof v === "number") out[key] = String(v);
  }
  return Object.keys(out).length ? out : undefined;
}

/** 把任意输入（v1 / v2 / 手改 / 脏值）收敛成 v2 皮肤；永不抛错。 */
export function normalizeSkin(raw: unknown): Skin {
  if (isV1Shape(raw)) return migrateV1(raw);
  if (!raw || typeof raw !== "object") return defaultSkin();

  const r = raw as Record<string, unknown>;
  const rawTokens = (r.tokens ?? {}) as Record<string, unknown>;
  const tokens: Skin["tokens"] = {
    theme: sanitizeTable(rawTokens.theme),
    light: sanitizeTable(rawTokens.light),
    dark: sanitizeTable(rawTokens.dark),
  };
  const metaRaw = (r.meta ?? {}) as Record<string, unknown>;
  const meta: SkinMeta = {};
  for (const k of ["name", "author", "license"] as const) {
    if (typeof metaRaw[k] === "string") meta[k] = metaRaw[k] as string;
  }

  return {
    version: 2,
    base: pickEnum(r.base, ["dark", "light"], currentThemeBase()),
    tokens,
    ...(Object.keys(meta).length ? { meta } : {}),
  };
}

export function loadSkin(): Skin {
  const store = storage();
  if (!store) return defaultSkin();
  let raw: string | null = null;
  try {
    raw = store.getItem(SKIN_KEY);
  } catch {
    return defaultSkin();
  }
  if (!raw) return defaultSkin();
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return defaultSkin();
  }
  const skin = normalizeSkin(parsed);
  // ★ 迁移幂等：v1 迁完立刻回写 v2，下次读到就是 v2（同一份数据、同一个键）。
  if (isV1Shape(parsed)) saveSkin(skin);
  return skin;
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

/* ───────── 校验（写前闸门） ───────── */

/** 未知令牌：只是不在本内核的命名表里 —— 不拦，留给未来版本与插件。 */
const KNOWN_TOKENS = new Set<string>([
  "--bg",
  "--bg-grid",
  "--surface",
  "--surface-2",
  "--surface-3",
  "--overlay",
  "--txt",
  "--txt-dim",
  "--txt-faint",
  "--line",
  "--line-strong",
  "--accent",
  "--accent-fg",
  "--accent-soft",
  "--ok",
  "--ok-fg",
  "--warn",
  "--warn-fg",
  "--danger",
  "--danger-fg",
  "--radius",
  "--r-xs",
  "--r-sm",
  "--r-md",
  "--r-lg",
  "--r-pill",
  "--sp-1",
  "--sp-2",
  "--sp-3",
  "--sp-4",
  "--sp-5",
  "--sp-6",
  "--font-sans",
  "--font-mono",
  "--font-serif",
  "--shadow-color",
  "--shadow-strength",
  "--shadow-1",
  "--shadow-2",
  "--shadow-1-alpha",
  "--shadow-2-alpha",
  "--topbar-h",
  "--dock-h",
  "--dock-pad",
  "--win-min-w",
  "--win-min-h",
  "--skin-swatch-blue",
  "--skin-swatch-violet",
  "--skin-swatch-teal",
  "--skin-swatch-green",
  "--skin-swatch-amber",
  "--skin-swatch-rose",
  "--skin-swatch-cyan",
  "--skin-swatch-slate",
]);

/** 值里出现这些串就直接拒绝 —— 都是「从变量扩权到代码」的口子。 */
const FORBIDDEN_VALUE = [
  { re: /var\s*\(/i, why: "值里不许用 var()（会形成跨变量间接引用）" },
  { re: /url\s*\(/i, why: "值里不许用 url()（会引入外部资源与追踪）" },
  { re: /!\s*important/i, why: "值里不许用 !important（会压过宿主与主题）" },
  { re: /[{};]/i, why: "值里不许出现 { } ;（防注入新声明）" },
];

export interface SkinValidation {
  ok: boolean;
  errors: string[];
  warnings: string[];
}

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function relLuminance(rgb: [number, number, number]): number {
  return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
}

/** 按 `base`（缺省=当前主题）合并三张表，后写的覆盖先写的。 */
export function resolveTokens(skin: Skin, base: SkinBase = currentThemeBase()): TokenTable {
  const { theme, light, dark } = skin.tokens ?? {};
  return { ...theme, ...(base === "light" ? light : dark) };
}

/**
 * 皮肤包自洽校验 —— 写进 DOM 之前的唯一闸门。
 *
 * 判据分三档：
 *   · error（拒收）：结构不成形、缺 `--bg`/`--txt`、值里有 var()/url()/!important/{}/;、
 *     `--radius` 不是可解析的 px/rem、表面亮度单调性反序；
 *   · warning（收但提示）：未知令牌名、亮度差 < 2%（几乎分不开）、缺 `meta.name`；
 *   · ok：其余。
 */
export function validateSkinPack(raw: unknown): SkinValidation {
  return validateSkin(raw, true);
}

/**
 * 校验核心。
 *
 * `requireBase` 区分两种输入：
 *   · true —— **皮肤包**（导入的 .json）：它声称定义整套外观，必须自带 `--bg` 与 `--txt`；
 *   · false —— **覆盖层**（本机面板 / v1 迁移产物）：只声明它想改的那几个变量即可，
 *     背景文字继续由主题供给（否则皮肤一写死背景，明暗切换就失效了）。
 * 两条路的结构与取值规则（注入串、变量名、圆角可解析、亮度单调）完全一致。
 */
function validateSkin(raw: unknown, requireBase: boolean): SkinValidation {
  const errors: string[] = [];
  const warnings: string[] = [];

  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { ok: false, errors: ["皮肤包必须是对象"], warnings };
  }
  const r = raw as Record<string, unknown>;
  const tokens = r.tokens;
  if (!tokens || typeof tokens !== "object" || Array.isArray(tokens)) {
    return { ok: false, errors: ["缺少 tokens 三分区（theme / light / dark）"], warnings };
  }

  const skin = normalizeSkin(raw);
  const parts: Array<[string, TokenTable | undefined]> = [
    ["theme", skin.tokens.theme],
    ["light", skin.tokens.light],
    ["dark", skin.tokens.dark],
  ];

  for (const [zone, table] of parts) {
    if (!table) continue;
    for (const [name, value] of Object.entries(table)) {
      if (!name.startsWith("--") || name.length < 3) {
        errors.push(`[${zone}] 变量名不合法：${name}`);
        continue;
      }
      if (value.trim() === "") {
        errors.push(`[${zone}] ${name} 的值为空`);
        continue;
      }
      const hit = FORBIDDEN_VALUE.find((f) => f.re.test(value));
      if (hit) errors.push(`[${zone}] ${name}: ${hit.why}`);
      if (!KNOWN_TOKENS.has(name))
        warnings.push(`[${zone}] 未知令牌 ${name}（已保留，本内核暂不消费）`);
    }
  }

  // 必须有背景与正文色（判在合并后的 theme+base 上，分区里给也算）。
  const merged = resolveTokens(skin, skin.base);
  if (requireBase) {
    for (const required of ["--bg", "--txt"]) {
      if (!(required in merged))
        errors.push(`皮肤包缺少必需令牌 ${required}（至少要给背景与正文色）`);
    }
  }

  // 圆角必须可解析成 px 或 rem —— 否则 calc() 派生链会整体失效。
  const radius = merged["--radius"];
  if (radius !== undefined && !/^-?\d*\.?\d+(px|rem)$/i.test(radius.trim())) {
    errors.push(`--radius 必须形如 10px / 0.625rem，收到「${radius}」`);
  }

  // 表面阶梯单调：--bg → --surface → --surface-2 → --surface-3 四层必须**同向**递进。
  // ★ 为什么不写死「越往上越亮」：我们自己的官方令牌 dark 是逐步变亮
  //   (#0e1116 → #171c26 → #1f2632 → #283042)，light 则是卡片最亮、凹陷层递暗。
  //   方向由主题决定，能判的只有「单调」本身 —— 中间翘起来（反序）才是漂移。
  const ladder = ["--bg", "--surface", "--surface-2", "--surface-3"];
  const lums = ladder.map((k) => {
    const v = merged[k];
    const rgb = v ? parseColor(v) : null;
    return rgb ? relLuminance(rgb) : null;
  });
  if (lums.every((l) => l !== null)) {
    const nums = lums as number[];
    const diffs = nums.slice(1).map((l, i) => l - nums[i]);
    const rising = diffs.every((d) => d > 0);
    const falling = diffs.every((d) => d < 0);
    if (!rising && !falling) {
      const shown = ladder.map((k, i) => `${k}(${nums[i].toFixed(3)})`).join(" → ");
      errors.push(`表面亮度必须单调（同向递进），实际 ${shown}`);
    } else if (diffs.some((d) => Math.abs(d) < 0.002)) {
      warnings.push("相邻表面亮度几乎相同（差 < 0.002），层次会看不出来");
    }
  }

  if (!skin.meta?.name) warnings.push("缺 meta.name（皮肤市场里会显示为未命名）");

  return { ok: errors.length === 0, errors, warnings };
}

/* ───────── 应用 ───────── */

/** 皮肤写过的变量名（撤销时按这张表逐个 removeProperty，不猜）。 */
const appliedProps = new Set<string>();

/**
 * 最后一次成功应用的非空皮肤。
 * ★ 用途：`data-theme` 变了要按新的亮暗分区**重算一次** —— 三分区的意义就在这，
 *   不重算的话 `tokens.light` 永远是死数据。由 theme.ts 换主题后回调 `reapplySkin()`。
 */
let lastApplied: Skin | null = null;

function writeTable(root: HTMLElement, table: TokenTable): void {
  for (const [name, value] of Object.entries(table)) {
    root.style.setProperty(name, value);
    appliedProps.add(name);
  }
}

/**
 * 应用一份皮肤：校验 → 整表写 `<html>` 内联变量。
 *
 * ★ 为什么整表写而不是「只写差异」：内联变量会**压过**令牌与主题的全部分区，
 *   于是「这份皮肤在亮色下是什么样」只取决于皮肤自己给了什么 —— 与 VS Code
 *   「一个 <style> 整体换 textContent」同一个道理，不留半套新旧混合的状态。
 * 校验不过时**一个变量都不写**并返回原因（不抛错、不白屏）。
 */
export function applySkin(skin: Skin): SkinValidation {
  const noop = (msg: string): SkinValidation => ({ ok: false, errors: [msg], warnings: [] });
  if (typeof document === "undefined") return noop("没有 DOM 环境");

  const check = validateSkin(skin, false);
  if (!check.ok) return check;

  const root = document.documentElement;
  clearApplied(root);
  const table = resolveTokens(skin);
  writeTable(root, table);
  // 强调色给了但没给派生值 → 推导，保证按钮文字在任何主色下都可读。
  const accent = table["--accent"];
  if (accent) {
    if (!("--accent-fg" in table)) {
      root.style.setProperty("--accent-fg", contrastFg(accent));
      appliedProps.add("--accent-fg");
    }
    if (!("--accent-soft" in table)) {
      root.style.setProperty("--accent-soft", softColor(accent));
      appliedProps.add("--accent-soft");
    }
  }
  root.setAttribute("data-skin", "custom");
  lastApplied = skin;
  return check;
}

/**
 * 换主题后调用：按新的 `data-theme` 重挑亮暗分区，重算并整表重写。
 * 无皮肤时什么也不做（保持令牌原样）。返回值同 `applySkin`。
 */
export function reapplySkin(): SkinValidation | null {
  if (!lastApplied) return null;
  return applySkin(lastApplied);
}

function clearApplied(root: HTMLElement): void {
  for (const prop of appliedProps) root.style.removeProperty(prop);
  appliedProps.clear();
}

/** 撤销全部皮肤内联变量，回到令牌原样，并清掉持久化。 */
export function clearSkin(): void {
  if (typeof document !== "undefined") {
    const root = document.documentElement;
    clearApplied(root);
    root.removeAttribute("data-skin");
  }
  lastApplied = null;
  const store = storage();
  try {
    store?.removeItem(SKIN_KEY);
  } catch {
    /* 同上 */
  }
}

/** 启动时调用：读出持久化皮肤并应用（无皮肤 / 校验不过时什么也不做，保持令牌默认）。 */
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
  if (isV1Shape(parsed)) saveSkin(skin); // 迁移幂等：老的只迁一次
  const result = applySkin(skin);
  // ★ 校验不过 → 不应用、打回默认外观（用户至少能看见界面），并把坏包留在原处不删。
  return result.ok ? skin : null;
}

/** 面板用：把色板变量读成实际颜色（颜色值只在 tokens.css 里写一次）。 */
export function readSwatches(): Array<{ label: string; value: string }> {
  return SWATCHES.map(({ varName, label }) => ({ label, value: cssVar(varName) })).filter((s) =>
    isColor(s.value),
  );
}
