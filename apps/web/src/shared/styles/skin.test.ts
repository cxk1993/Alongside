/** 皮肤引擎单测：v2 三分区、v1 迁移、写前校验、应用与撤销。 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  applySkin,
  choicesFromSkin,
  clearSkin,
  contrastFg,
  defaultChoices,
  defaultSkin,
  initSkin,
  isColor,
  loadSkin,
  migrateV1,
  normalizeSkin,
  reapplySkin,
  resolveTokens,
  saveSkin,
  SHADOW_STRENGTH,
  skinFromChoices,
  SKIN_KEY,
  softColor,
  toHex6,
  validateSkinPack,
  type Skin,
  type SkinV1,
} from "./skin";

/**
 * 本地 jsdom 在「不透明 origin」下没有 localStorage（访问可能抛 SecurityError）。
 * 这里补一个内存版，保证测试在任何 Node/jsdom 组合下都能跑；
 * 浏览器里走的是真 localStorage，不受影响。
 */
function ensureStorage(): void {
  try {
    if (window.localStorage) return;
  } catch {
    /* opaque origin：下面覆盖掉这个 getter */
  }
  const mem = new Map<string, string>();
  const shim: Storage = {
    get length() {
      return mem.size;
    },
    clear: () => mem.clear(),
    getItem: (k: string) => mem.get(k) ?? null,
    key: (i: number) => Array.from(mem.keys())[i] ?? null,
    removeItem: (k: string) => {
      mem.delete(k);
    },
    setItem: (k: string, v: string) => {
      mem.set(k, String(v));
    },
  };
  Object.defineProperty(window, "localStorage", { configurable: true, value: shim });
}
beforeEach(() => {
  ensureStorage();
  // ★ 必须先清模块级的 lastApplied（否则上一个测试的皮肤会被 reapplySkin 带进来），
  //   clearSkin 同时做这件事 + 撤内联变量 + 删持久化，正好是「干净起点」。
  clearSkin();
  window.localStorage.clear();
  document.documentElement.removeAttribute("style");
  document.documentElement.removeAttribute("data-skin");
  document.documentElement.removeAttribute("data-theme");
});

/** 一份合法的最小皮肤包（自带 bg/txt，表面亮度单调递进）。 */
function pack(over: Partial<Skin> = {}): Skin {
  return {
    version: 2,
    base: "dark",
    tokens: {
      theme: { "--accent": "#ff8800", "--radius": "12px" },
      dark: {
        "--bg": "#101720",
        "--surface": "#18202c",
        "--surface-2": "#202a38",
        "--surface-3": "#2a3546",
        "--txt": "#e8eef6",
      },
    },
    meta: { name: "测试皮肤" },
    ...over,
  };
}

/** 只在一个分区里加/改几条，其余保持合法（避免测试因「顺手丢了 bg」而假红）。 */
function withTheme(extra: Record<string, string>): Skin {
  const base = pack();
  return { ...base, tokens: { ...base.tokens, theme: { ...base.tokens.theme, ...extra } } };
}

describe("v1 → v2 迁移", () => {
  const v1: SkinV1 = {
    version: 1,
    accent: "#ff8800",
    radius: 1.5,
    density: "compact",
    font: "mono",
    shadow: "strong",
  };

  it("五个标量折算成 tokens.theme 的具体变量值", () => {
    const s = migrateV1(v1);
    expect(s.version).toBe(2);
    const theme = s.tokens.theme ?? {};
    expect(theme["--accent"]).toBe("#ff8800");
    expect(theme["--accent-fg"]).toBe("rgb(255, 255, 255)");
    expect(theme["--accent-soft"]).toBe("rgba(255, 136, 0, 0.15)");
    expect(theme["--radius"]).toBe("15px"); // 10 × 1.5
    expect(theme["--sp-1"]).toBe("3px"); // 4 × 0.85
    expect(theme["--sp-6"]).toBe("27px"); // 32 × 0.85
    expect(theme["--font-sans"]).toContain("monospace");
    expect(theme["--shadow-strength"]).toBe(String(SHADOW_STRENGTH.strong));
  });

  it("幂等：迁两次结果完全一致", () => {
    const once = migrateV1(v1);
    expect(normalizeSkin(once)).toEqual(once);
    expect(normalizeSkin(normalizeSkin(once))).toEqual(once);
  });

  it("loadSkin 读到 v1 时自动迁移并回写 v2（同一键、原位置）", () => {
    window.localStorage.setItem(SKIN_KEY, JSON.stringify(v1));
    const s = loadSkin();
    expect(s.version).toBe(2);
    const stored = JSON.parse(window.localStorage.getItem(SKIN_KEY) ?? "{}") as Skin;
    expect(stored.version).toBe(2);
    // 存的是三分区结构，不再是扁平的 accent/radius/density
    expect(stored.tokens.theme?.["--radius"]).toBe("15px");
  });

  it("v1 的脏值不抛错，收敛成合法 v2", () => {
    const s = normalizeSkin({ version: 1, accent: "nope", radius: "abc", density: "huge" });
    expect(s.version).toBe(2);
    expect(s.tokens.theme?.["--accent"]).toBeUndefined(); // 脏色不写，交回主题
    expect(s.tokens.theme?.["--radius"]).toBe("10px"); // 系数回退 1
  });
});

describe("v2 规范化", () => {
  it("非对象 / 缺 tokens 一律回默认空皮肤", () => {
    expect(normalizeSkin(null).version).toBe(2);
    expect(normalizeSkin("x").tokens.theme).toBeUndefined();
    expect(normalizeSkin({ version: 2 }).tokens.theme).toBeUndefined();
  });

  it("变量名没写 -- 前缀的自动补（shadcn 的 radius 就是裸名）", () => {
    const s = normalizeSkin({ version: 2, tokens: { theme: { radius: "8px" } } });
    expect(s.tokens.theme).toEqual({ "--radius": "8px" });
  });

  it("非字符串 / 对象值被丢弃，不污染令牌表", () => {
    const s = normalizeSkin({
      version: 2,
      tokens: { theme: { "--radius": null, "--sp-1": 6, "--bg": {} } },
    });
    expect(s.tokens.theme).toEqual({ "--sp-1": "6" });
  });

  it("meta 只保留三个白名单字段", () => {
    const s = normalizeSkin({
      version: 2,
      tokens: { theme: {} },
      meta: { name: "A", author: "B", license: "MIT", evil: "x" },
    });
    expect(s.meta).toEqual({ name: "A", author: "B", license: "MIT" });
  });

  it("三分区各自独立，按 base 合并（base 分区覆盖 theme）", () => {
    const s = pack({
      tokens: {
        theme: { "--accent": "#111111", "--radius": "10px" },
        light: { "--accent": "#222222" },
        dark: { "--accent": "#333333" },
      },
    });
    expect(resolveTokens(s, "light")["--accent"]).toBe("#222222");
    expect(resolveTokens(s, "dark")["--accent"]).toBe("#333333");
    expect(resolveTokens(s, "light")["--radius"]).toBe("10px"); // theme 与明暗无关
  });
});

describe("面板标量 ↔ v2 皮肤", () => {
  it("skinFromChoices 写的是具体变量值，不是系数", () => {
    const s = skinFromChoices({ ...defaultChoices(), radius: 1.2, density: "comfortable" });
    expect(s.tokens.theme?.["--radius"]).toBe("12px");
    expect(s.tokens.theme?.["--sp-1"]).toBe("5px"); // 4 × 1.15 = 4.6 → 5
  });

  it("choicesFromSkin 能读回控件位置（往返一致）", () => {
    const c = { ...defaultChoices(), accent: "#123456", radius: 1.4, density: "compact" as const };
    const back = choicesFromSkin(skinFromChoices(c));
    expect(back.accent).toBe("#123456");
    expect(back.radius).toBeCloseTo(1.4, 2);
    expect(back.density).toBe("compact");
    expect(back.shadow).toBe("standard");
  });

  it("读不出来的包回到「令牌原样」，不显示乱码", () => {
    const c = choicesFromSkin({
      version: 2,
      base: "dark",
      tokens: { theme: { "--radius": "abc" } },
    });
    expect(c.radius).toBe(1);
    expect(c.density).toBe("standard");
    expect(isColor(c.accent)).toBe(true);
  });
});

describe("颜色", () => {
  it("深底配白字、浅底配黑字", () => {
    expect(contrastFg("#111111")).toBe("rgb(255, 255, 255)");
    expect(contrastFg("#ffffff")).toBe("rgb(0, 0, 0)");
  });

  it("soft 是 15% 透明", () => {
    expect(softColor("#5b8cff")).toBe("rgba(91, 140, 255, 0.15)");
  });

  it("toHex6 把 rgb 归一成 #rrggbb", () => {
    expect(toHex6("rgb(91, 140, 255)")).toBe("#5b8cff");
    expect(toHex6("#ABC")).toBe("#000000"); // 非 6 位 hex 不猜，直接回退
  });

  it("支持 hex / rgb / hsl", () => {
    expect(isColor("#5b8cff")).toBe(true);
    expect(isColor("rgb(1, 2, 3)")).toBe(true);
    expect(isColor("hsl(220 90% 60%)")).toBe(true);
    expect(isColor("red")).toBe(false);
  });
});

describe("validateSkinPack：四条拒收", () => {
  it("① 缺 tokens 三分区", () => {
    const r = validateSkinPack({ version: 2, base: "dark" });
    expect(r.ok).toBe(false);
    expect(r.errors.join()).toContain("三分区");
  });

  it("② 缺 --bg / --txt", () => {
    const r = validateSkinPack({
      version: 2,
      base: "dark",
      tokens: { theme: { "--accent": "#ff0000" } },
    });
    expect(r.ok).toBe(false);
    expect(r.errors.join()).toContain("--bg");
    expect(r.errors.join()).toContain("--txt");
  });

  it("③ 值里含 var( / url( / !important", () => {
    for (const bad of ["var(--x)", "url(https://e/x.png)", "red !important"]) {
      const r = validateSkinPack(withTheme({ "--accent": bad }));
      expect(r.ok, bad).toBe(false);
      expect(r.errors.length).toBeGreaterThan(0);
    }
  });

  it("④ 表面亮度单调反序（中间翘起来）被拒", () => {
    const r = validateSkinPack(
      pack({
        tokens: {
          theme: {},
          dark: {
            "--bg": "#0a0a0a",
            "--surface": "#1a1a1a",
            "--surface-2": "#050505", // 掉头变暗 → 反序
            "--surface-3": "#2a2a2a",
            "--txt": "#ffffff",
          },
        },
      }),
    );
    expect(r.ok).toBe(false);
    expect(r.errors.join()).toContain("表面亮度必须单调");
  });
});

describe("validateSkinPack：三条警告", () => {
  it("① 未知令牌：只 warning，包仍可用且值被保留", () => {
    const r = validateSkinPack(withTheme({ "--my-fancy-token": "1px" }));
    expect(r.ok).toBe(true);
    expect(r.warnings.join()).toContain("未知令牌 --my-fancy-token");
  });

  it("② 相邻表面亮度几乎相同（差 < 0.002）", () => {
    const r = validateSkinPack(
      pack({
        tokens: {
          theme: {},
          dark: {
            "--bg": "#101720",
            "--surface": "#101721",
            "--surface-2": "#101722",
            "--surface-3": "#101723",
            "--txt": "#e8eef6",
          },
        },
      }),
    );
    expect(r.ok).toBe(true);
    expect(r.warnings.join()).toContain("亮度几乎相同");
  });

  it("③ 缺 meta.name", () => {
    const r = validateSkinPack(pack({ meta: undefined }));
    expect(r.ok).toBe(true);
    expect(r.warnings.join()).toContain("meta.name");
  });

  it("干净包零 error 零 warning", () => {
    const r = validateSkinPack(pack());
    expect(r).toEqual({ ok: true, errors: [], warnings: [] });
  });
});

describe("apply / clear", () => {
  it("apply 后 --radius 生效（四档由 CSS 派生，只写一个基准）", () => {
    const r = applySkin(skinFromChoices({ ...defaultChoices(), radius: 1.5 }));
    expect(r.ok).toBe(true);
    const st = document.documentElement.style;
    expect(st.getPropertyValue("--radius")).toBe("15px");
    // ★ 四档不再逐个写内联 —— 它们由 tokens.css 的 calc() 派生，写死反而会盖掉派生链
    expect(st.getPropertyValue("--r-xs")).toBe("");
    expect(st.getPropertyValue("--r-md")).toBe("");
  });

  it("写入强调色、间距、字体与阴影倍率", () => {
    applySkin(
      skinFromChoices({
        accent: "#ff8800",
        radius: 1,
        density: "compact",
        font: "mono",
        shadow: "strong",
      }),
    );
    const st = document.documentElement.style;
    expect(st.getPropertyValue("--accent")).toBe("#ff8800");
    expect(st.getPropertyValue("--accent-fg")).toBe("rgb(255, 255, 255)");
    expect(st.getPropertyValue("--sp-1")).toBe("3px");
    expect(st.getPropertyValue("--font-sans")).toContain("monospace");
    expect(st.getPropertyValue("--shadow-strength")).toBe(String(SHADOW_STRENGTH.strong));
    // ★ 不再重写整条阴影几何
    expect(st.getPropertyValue("--shadow-1")).toBe("");
  });

  it("校验不过时一个变量都不写（坏包不污染页面）", () => {
    const r = applySkin({
      version: 2,
      base: "dark",
      tokens: { theme: { "--accent": "var(--x)" } },
    });
    expect(r.ok).toBe(false);
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("");
    expect(document.documentElement.getAttribute("data-skin")).toBeNull();
  });

  it("整表替换：第二次 apply 会先撤掉第一次写过的变量", () => {
    applySkin(skinFromChoices({ ...defaultChoices(), density: "compact" }));
    expect(document.documentElement.style.getPropertyValue("--sp-1")).toBe("3px");
    applySkin(pack({ tokens: { theme: { "--accent": "#00ff00" } } }));
    expect(document.documentElement.style.getPropertyValue("--sp-1")).toBe("");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#00ff00");
  });

  it("换主题会按新的亮暗分区重算（三分区不是死数据）", () => {
    const s = pack({
      tokens: {
        theme: { "--accent": "#888888" },
        light: { "--accent": "#111111", "--radius": "6px" },
        dark: { "--accent": "#eeeeee", "--radius": "18px" },
        // 亮暗分区都要自带 bg/txt 才自洽
      },
    });
    document.documentElement.setAttribute("data-theme", "dark");
    expect(applySkin(s).ok).toBe(true);
    expect(document.documentElement.style.getPropertyValue("--radius")).toBe("18px");

    document.documentElement.setAttribute("data-theme", "light");
    expect(reapplySkin()?.ok).toBe(true);
    expect(document.documentElement.style.getPropertyValue("--radius")).toBe("6px");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#111111");
  });

  it("没有皮肤时换主题是空操作", () => {
    document.documentElement.setAttribute("data-theme", "light");
    expect(reapplySkin()).toBeNull();
    expect(document.documentElement.style.getPropertyValue("--radius")).toBe("");
  });

  it("恢复默认会清掉内联变量与持久化", () => {
    const skin = skinFromChoices({ ...defaultChoices(), accent: "#123456" });
    applySkin(skin);
    saveSkin(skin);
    clearSkin();
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("");
    expect(document.documentElement.getAttribute("data-skin")).toBeNull();
    expect(window.localStorage.getItem(SKIN_KEY)).toBeNull();
  });
});

describe("持久化", () => {
  it("坏 JSON 不会抛，load 回默认、init 返回 null", () => {
    window.localStorage.setItem(SKIN_KEY, "{oops");
    expect(loadSkin().version).toBe(2);
    expect(initSkin()).toBeNull();
  });

  it("initSkin 会应用持久化皮肤并打 data-skin", () => {
    saveSkin(skinFromChoices({ ...defaultChoices(), accent: "#112233", radius: 1.2 }));
    const s = initSkin();
    expect(s?.tokens.theme?.["--accent"]).toBe("#112233");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#112233");
    expect(document.documentElement.style.getPropertyValue("--radius")).toBe("12px");
    expect(document.documentElement.getAttribute("data-skin")).toBe("custom");
  });

  it("没有持久化时 initSkin 不动任何东西", () => {
    expect(initSkin()).toBeNull();
    expect(document.documentElement.getAttribute("data-skin")).toBeNull();
  });

  it("v1 数据在 initSkin 里也完成迁移（不因启动路径而漏迁）", () => {
    window.localStorage.setItem(
      SKIN_KEY,
      JSON.stringify({ version: 1, accent: "#445566", radius: 1 }),
    );
    const s = initSkin();
    expect(s?.version).toBe(2);
    const stored = JSON.parse(window.localStorage.getItem(SKIN_KEY) ?? "{}") as Skin;
    expect(stored.version).toBe(2);
  });

  it("defaultSkin 是空表（应用后等于令牌原样）", () => {
    expect(defaultSkin().tokens).toEqual({
      theme: undefined,
      light: undefined,
      dark: undefined,
    });
    expect(applySkin(defaultSkin()).ok).toBe(true);
  });
});
