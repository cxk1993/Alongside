import { beforeEach, describe, expect, it } from "vitest";
import {
  SKIN_PLUGIN_KEY,
  applyPluginSkin,
  clearPluginSkins,
  loadPluginSkins,
  syncPluginSkins,
  type PluginSkinSource,
} from "./skin-plugin-store";
import { _resetSkinRuntime, commitSkin, getCurrentSkin } from "./skin-runtime";

/** 一份最小可用的皮肤包（alongsideSkin:1 外壳 + tokens.theme）。 */
function pack(name: string, accent: string) {
  return {
    alongsideSkin: 1,
    skin: {
      version: 2,
      base: "dark",
      tokens: { theme: { "--bg": "#101014", "--txt": "#e8e8ee", "--accent": accent } },
      meta: { name },
    },
  };
}

function src(id: string, extra: Partial<PluginSkinSource> = {}): PluginSkinSource {
  return { id, name: `${id} 插件`, enabled: true, manifest: {}, ...extra };
}

describe("skin-plugin-store：插件皮肤是「只读派生数据」", () => {
  beforeEach(() => {
    window.localStorage.clear();
    _resetSkinRuntime();
    clearPluginSkins();
  });

  it("把 manifest.skinPack 收进插件皮肤缓存", () => {
    const r = syncPluginSkins([src("skin-a", { manifest: { skinPack: pack("夜紫", "#a78bfa") } })]);
    expect(r.added).toBe(1);
    const items = loadPluginSkins();
    expect(items).toHaveLength(1);
    expect(items[0].id).toBe("plugin:skin-a");
    expect(items[0].pluginId).toBe("skin-a");
    expect(items[0].name).toBe("夜紫");
    expect(items[0].accent).toBe("#a78bfa");
  });

  it("停用的插件不提供皮肤（与资产端点的 phase 闸同口径）", () => {
    syncPluginSkins([
      src("skin-a", { enabled: false, manifest: { skinPack: pack("夜紫", "#a78bfa") } }),
    ]);
    expect(loadPluginSkins()).toHaveLength(0);
  });

  it("后端给了 skinError 就收进 errors，且不阻断其它插件", () => {
    const r = syncPluginSkins([
      src("broken", { manifest: { skinError: "skin 文件不存在：skin.json" } }),
      src("ok", { manifest: { skinPack: pack("青", "#2dd4bf") } }),
    ]);
    expect(r.errors).toHaveLength(1);
    expect(r.errors[0]).toContain("skin 文件不存在");
    expect(loadPluginSkins().map((it) => it.pluginId)).toEqual(["ok"]);
  });

  it("整组重建：插件消失后它的皮肤也被清掉（不留孤儿条目）", () => {
    syncPluginSkins([src("a", { manifest: { skinPack: pack("甲", "#111111") } })]);
    expect(loadPluginSkins()).toHaveLength(1);
    const r = syncPluginSkins([]);
    expect(r.removed).toBe(1);
    expect(loadPluginSkins()).toHaveLength(0);
  });

  it("正在用的皮肤来自消失的插件 → 必须回退默认", () => {
    const p = pack("甲", "#112233");
    syncPluginSkins([src("a", { manifest: { skinPack: p } })]);
    // 真实路径：applyPluginSkin 应用的是 entry.skin（归一后的本体），
    // 不是外层 alongsideSkin 信封 —— 这里必须照同一条路径来。
    const applied = commitSkin(p.skin);
    expect(applied.ok).toBe(true);
    expect(getCurrentSkin()).not.toBeNull();

    const r = syncPluginSkins([]);
    expect(r.resetApplied).toBe(true);
  });

  it("正在用的是别的皮肤时，插件消失不动当前外观", () => {
    syncPluginSkins([src("a", { manifest: { skinPack: pack("甲", "#112233") } })]);
    commitSkin(pack("乙", "#445566").skin);
    const r = syncPluginSkins([]);
    expect(r.resetApplied).toBe(false);
    expect(getCurrentSkin()).not.toBeNull();
  });

  it("幂等：重复同步不产生重复条目", () => {
    const s = [src("a", { manifest: { skinPack: pack("甲", "#112233") } })];
    syncPluginSkins(s);
    const r = syncPluginSkins(s);
    expect(r.added).toBe(0);
    expect(loadPluginSkins()).toHaveLength(1);
  });

  it("应用插件皮肤走运行时同一个 commitSkin 路径", () => {
    syncPluginSkins([src("a", { manifest: { skinPack: pack("甲", "#112233") } })]);
    const api = {
      get: () => getCurrentSkin() ?? ({} as never),
      apply: (s: unknown) => commitSkin(s),
      reset: () => {},
      export: () => "",
      import: (raw: string) => JSON.parse(raw),
      subscribe: () => () => {},
    };
    const r = applyPluginSkin("plugin:a", api as never);
    expect(r.ok).toBe(true);
    expect(getCurrentSkin()?.tokens?.theme?.["--accent"]).toBe("#112233");
  });

  it("应用一份已经不存在的插件皮肤 → 明确报错，不静默改外观", () => {
    const api = {
      get: () => ({}) as never,
      apply: () => ({ ok: true, errors: [], warnings: [] }),
      reset: () => {},
      export: () => "",
      import: () => ({}),
      subscribe: () => () => {},
    };
    const r = applyPluginSkin("plugin:ghost", api as never);
    expect(r.ok).toBe(false);
    expect(r.errors[0]).toContain("已不在");
  });

  it("坏缓存（非 JSON）静默丢弃，不抛", () => {
    window.localStorage.setItem(SKIN_PLUGIN_KEY, "{不是 JSON");
    expect(() => loadPluginSkins()).not.toThrow();
    expect(loadPluginSkins()).toEqual([]);
  });
});
