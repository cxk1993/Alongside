/**
 * 「外观与皮肤」独立设置窗（module: appearance）。
 *
 * 做什么：把一组 CSS 变量（强调色 / 圆角 / 间距 / 字体 / 阴影）做成可调控件；
 * 改一次即时生效、持久化到 localStorage，并在下次启动前由 `initSkin()` 应用。
 *
 * 适配（与需求逐条对应）：
 *   1. 明暗双主题：默认只写强调色/形状类变量，不碰背景与文字；
 *      `--accent-fg` 按亮度自动取黑/白；皮肤包若自带 `light`/`dark` 分区则按当前主题选；
 *   2. 小窗口 / 窄屏：栅格 auto-fill + minmax(0,1fr)，360px 宽不溢出，面板整体可滚动；
 *   3. 样式门禁：组件不写十六进制，色板与阴影全部来自 tokens.css 的变量；
 *   4. 结构：控件仍是五个标量（这是 UI 概念），提交时折算成 v2 的 `tokens.theme` 具体值；
 *      圆角只写 `--radius` 一个基准，四档由 CSS calc() 派生。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  choicesFromSkin,
  DENSITY_LABEL,
  defaultChoices,
  FONT_LABEL,
  isColor,
  loadSkin,
  RADIUS_MAX,
  RADIUS_MIN,
  readSwatches,
  SHADOW_LABEL,
  skinFromChoices,
  toHex6,
  type Density,
  type FontChoice,
  type ShadowChoice,
  type Skin,
  type SkinChoices,
} from "@/shared/styles/skin";
import { openDesktopPersonalization } from "@/kernel/wallpaper";
import {
  applyFromLibrary,
  exportFromLibrary,
  importToLibrary,
  loadLibrary,
  removeFromLibrary,
  safeFileName,
  saveCurrentToLibrary,
  type SkinEntry,
} from "@/shared/styles/skin-library";
import {
  applyPluginSkin,
  loadPluginSkins,
  type PluginSkinEntry,
} from "@/shared/styles/skin-plugin-store";
import {
  commitSkin,
  resetSkin,
  subscribeSkin,
  type AlongsideSkinApi,
} from "@/shared/styles/skin-runtime";

import "./appearance.css";

const DENSITIES: Density[] = ["compact", "standard", "comfortable"];
const FONTS: FontChoice[] = ["sans", "mono", "serif"];
const SHADOWS: ShadowChoice[] = ["soft", "standard", "strong"];

export default function AppearanceApp() {
  const [choices, setChoices] = useState<SkinChoices>(() => choicesFromSkin(loadSkin()));
  const [hexDraft, setHexDraft] = useState(() => toHex6(choicesFromSkin(loadSkin()).accent));
  const [savedAt, setSavedAt] = useState<string>("");
  const swatches = useMemo(() => readSwatches(), []);

  // ── 本地皮肤库（收藏 / 分享 / 切换）────────────────────────────
  const [library, setLibrary] = useState<SkinEntry[]>(() => loadLibrary());
  // 插件提供的皮肤：只读派生数据，随插件来、随插件走（见 skin-plugin-store.ts）。
  // 与「我的皮肤」分开渲染 —— 两者归属不同，放在一张列表里会让人以为能删能改名。
  const [pluginSkins, setPluginSkins] = useState<PluginSkinEntry[]>(() => loadPluginSkins());
  const refreshPluginSkins = useCallback(() => setPluginSkins(loadPluginSkins()), []);
  const [libMsg, setLibMsg] = useState<string>("");
  const fileRef = useRef<HTMLInputElement | null>(null);

  const skinApi = (): AlongsideSkinApi | null =>
    (typeof window === "undefined"
      ? null
      : (window as unknown as { AlongsideSkin?: AlongsideSkinApi }).AlongsideSkin) ?? null;

  const refreshLibrary = useCallback(() => setLibrary(loadLibrary()), []);
  const flash = useCallback((msg: string) => {
    setLibMsg(msg);
    window.setTimeout(() => setLibMsg(""), 4000);
  }, []);

  const applyPlugin = useCallback(
    (id: string) => {
      const api = skinApi();
      if (!api) return;
      const r = applyPluginSkin(id, api);
      flash(r.ok ? "已应用（插件皮肤）" : `应用失败：${r.errors[0] ?? "未知错误"}`);
      refreshPluginSkins();
    },
    [flash, refreshPluginSkins],
  );

  const saveToLib = useCallback(() => {
    const api = skinApi();
    if (!api) return;
    const entry = saveCurrentToLibrary(api);
    refreshLibrary();
    flash(`已存入皮肤库：${entry.name}`);
  }, [flash, refreshLibrary]);

  const applyLib = useCallback(
    (id: string) => {
      const api = skinApi();
      if (!api) return;
      const r = applyFromLibrary(id, api);
      flash(r.ok ? "已应用" : `应用失败：${r.errors[0] ?? "未知错误"}`);
    },
    [flash],
  );

  const exportLib = useCallback(
    (id: string) => {
      const pack = exportFromLibrary(id);
      if (!pack) return;
      const entry = loadLibrary().find((it) => it.id === id);
      const blob = new Blob([pack], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${safeFileName(entry?.name ?? "skin")}.skin.json`;
      a.click();
      URL.revokeObjectURL(url);
      flash("已导出皮肤包");
    },
    [flash],
  );

  const deleteLib = useCallback(
    (id: string) => {
      removeFromLibrary(id);
      refreshLibrary();
      flash("已从皮肤库删除（当前外观不受影响）");
    },
    [flash, refreshLibrary],
  );

  const importFile = useCallback(
    async (file: File) => {
      const text = await file.text();
      const r = importToLibrary(text);
      if (!r.ok) {
        flash(`导入失败：${r.errors[0] ?? "未知错误"}`);
        return;
      }
      refreshLibrary();
      flash(`已导入：${r.entry?.name ?? "皮肤"}`);
    },
    [flash, refreshLibrary],
  );

  // ★ 外部（皮肤插件 / window.AlongsideSkin）改了皮肤时，面板要跟着刷新 —— 同一真相。
  useEffect(
    () =>
      subscribeSkin((skin) => {
        const next = choicesFromSkin(skin ?? loadSkin());
        choicesRef.current = next;
        setChoices(next);
      }),
    [],
  );

  // ★ 同一 tick 里连点两次（色板 + 密度）时，只靠 state 闭包会丢掉前一次改动：
  //   第二次用的是旧 choices。用一个 ref 串起来，保证「后一次基于前一次」。
  const choicesRef = useRef(choices);

  // 外部改了强调色（取色器/色板）时同步输入框
  useEffect(() => {
    setHexDraft(toHex6(choices.accent));
  }, [choices.accent]);

  const commit = useCallback((next: SkinChoices) => {
    // ★ 落盘的是 v2 皮肤（tokens.theme 里的具体变量值），不是标量 —— 与导入的皮肤包同构。
    // ★ 统一走 runtime.commitSkin：校验 → 应用 → 持久化 → 广播，插件 API 与面板同一真相。
    const skin: Skin = skinFromChoices(next);
    commitSkin(skin);
    choicesRef.current = next;
    setChoices(next);
    setSavedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
  }, []);

  const update = useCallback(
    (patch: Partial<SkinChoices>) => {
      commit(choicesRef.current ? { ...choicesRef.current, ...patch } : defaultChoices());
    },
    [commit],
  );

  const reset = useCallback(() => {
    // resetSkin 撤销「皮肤写过的整张变量表」，并广播 null（插件据此同步）。
    resetSkin();
    setChoices(defaultChoices());
    setSavedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
  }, []);

  const applyHex = useCallback(
    (value: string) => {
      if (!isColor(value)) {
        setHexDraft(toHex6(choices.accent));
        return;
      }
      update({ accent: value });
    },
    [choices.accent, update],
  );

  return (
    <div className="appearance" data-testid="appearance-app">
      <section className="appearance__section" aria-labelledby="ap-preview">
        <h3 id="ap-preview" className="appearance__title">
          预览
        </h3>
        <div className="appearance__preview">
          <button type="button" className="appearance__btn-primary">
            主按钮
          </button>
          <span className="appearance__chip">强调色</span>
          <input className="appearance__input" placeholder="输入框" aria-label="预览输入框" />
          <span className="appearance__dot" aria-hidden="true" />
        </div>
      </section>

      <section className="appearance__section" aria-labelledby="ap-accent">
        <h3 id="ap-accent" className="appearance__title">
          主色
        </h3>
        <div className="appearance__swatches" role="group" aria-label="预设色板">
          {swatches.map((s) => (
            <button
              key={s.label}
              type="button"
              className="appearance__swatch"
              style={{ background: s.value }}
              aria-label={s.label}
              aria-pressed={choices.accent.toLowerCase() === s.value.toLowerCase()}
              data-testid={`swatch-${s.label}`}
              onClick={() => update({ accent: s.value })}
            />
          ))}
        </div>
        <div className="appearance__row">
          <input
            type="color"
            aria-label="取色器"
            value={toHex6(choices.accent)}
            onChange={(e) => update({ accent: e.target.value })}
          />
          <input
            className="appearance__hex"
            aria-label="十六进制颜色"
            value={hexDraft}
            onChange={(e) => setHexDraft(e.target.value)}
            onBlur={(e) => applyHex(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") applyHex((e.target as HTMLInputElement).value);
            }}
          />
        </div>
      </section>

      <section className="appearance__section" aria-labelledby="ap-radius">
        <h3 id="ap-radius" className="appearance__title">
          圆角
          <span className="appearance__value">{choices.radius.toFixed(2)}×</span>
        </h3>
        <input
          type="range"
          min={RADIUS_MIN}
          max={RADIUS_MAX}
          step={0.05}
          value={choices.radius}
          aria-label="圆角系数"
          onChange={(e) => update({ radius: Number(e.target.value) })}
        />
      </section>

      <section className="appearance__section" aria-labelledby="ap-density">
        <h3 id="ap-density" className="appearance__title">
          密度
        </h3>
        <div className="appearance__seg" role="group" aria-label="密度">
          {DENSITIES.map((d) => (
            <button
              key={d}
              type="button"
              aria-pressed={choices.density === d}
              className={choices.density === d ? "is-active" : ""}
              onClick={() => update({ density: d })}
            >
              {DENSITY_LABEL[d]}
            </button>
          ))}
        </div>
      </section>

      <section className="appearance__section" aria-labelledby="ap-font">
        <h3 id="ap-font" className="appearance__title">
          字体
        </h3>
        <div className="appearance__seg" role="group" aria-label="字体">
          {FONTS.map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={choices.font === f}
              className={choices.font === f ? "is-active" : ""}
              onClick={() => update({ font: f })}
            >
              {FONT_LABEL[f]}
            </button>
          ))}
        </div>
      </section>

      <section className="appearance__section" aria-labelledby="ap-shadow">
        <h3 id="ap-shadow" className="appearance__title">
          阴影
        </h3>
        <div className="appearance__seg" role="group" aria-label="阴影">
          {SHADOWS.map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={choices.shadow === s}
              className={choices.shadow === s ? "is-active" : ""}
              onClick={() => update({ shadow: s })}
            >
              {SHADOW_LABEL[s]}
            </button>
          ))}
        </div>
      </section>

      {pluginSkins.length > 0 && (
        <section className="appearance__section" aria-labelledby="ap-plugin-skins">
          <h3 id="ap-plugin-skins" className="appearance__title">
            插件皮肤
            <span className="appearance__value">{pluginSkins.length} 份</span>
          </h3>
          <div className="appearance__hint">
            由已启用的插件提供。这里只能应用，不能改不能删 —— 停用插件它就会消失。
          </div>
          <ul className="appearance__lib" data-testid="skin-plugin-library">
            {pluginSkins.map((it) => (
              <li key={it.id} className="appearance__lib-row">
                <span
                  className="appearance__lib-dot"
                  style={{ background: it.accent ?? "var(--accent)" }}
                  aria-hidden="true"
                />
                <span className="appearance__lib-name" title={it.name}>
                  {it.name}
                </span>
                <span className="appearance__lib-tag">插件</span>
                <button
                  type="button"
                  className="appearance__btn"
                  onClick={() => applyPlugin(it.id)}
                >
                  应用
                </button>
              </li>
            ))}
          </ul>
          <div className="appearance__row">
            <button type="button" className="appearance__btn" onClick={refreshPluginSkins}>
              刷新插件皮肤
            </button>
          </div>
        </section>
      )}

      <section className="appearance__section" aria-labelledby="ap-library">
        <h3 id="ap-library" className="appearance__title">
          我的皮肤
          <span className="appearance__value">{library.length} 份</span>
        </h3>
        {library.length === 0 ? (
          <div className="appearance__hint">
            还没有收藏。调好外观后点「存入皮肤库」，或导入别人分享的 .skin.json。
          </div>
        ) : (
          <ul className="appearance__lib" data-testid="skin-library">
            {library.map((it) => (
              <li key={it.id} className="appearance__lib-row">
                <span
                  className="appearance__lib-dot"
                  style={{ background: it.accent ?? "var(--accent)" }}
                  aria-hidden="true"
                />
                <span className="appearance__lib-name" title={it.name}>
                  {it.name}
                </span>
                <button type="button" className="appearance__btn" onClick={() => applyLib(it.id)}>
                  应用
                </button>
                <button type="button" className="appearance__btn" onClick={() => exportLib(it.id)}>
                  导出
                </button>
                <button
                  type="button"
                  className="appearance__btn appearance__btn--danger"
                  aria-label={`删除皮肤 ${it.name}`}
                  onClick={() => deleteLib(it.id)}
                >
                  删除
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="appearance__row">
          <button
            type="button"
            className="appearance__btn"
            onClick={saveToLib}
            data-testid="skin-save"
          >
            存入皮肤库
          </button>
          <button
            type="button"
            className="appearance__btn"
            onClick={() => fileRef.current?.click()}
            data-testid="skin-import"
          >
            导入…
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            aria-label="选择皮肤包文件"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void importFile(f);
              e.target.value = "";
            }}
          />
        </div>
        {libMsg ? (
          <span className="appearance__hint" role="status">
            {libMsg}
          </span>
        ) : null}
      </section>

      <section className="appearance__section" aria-labelledby="ap-wallpaper">
        <h3 id="ap-wallpaper" className="appearance__title">
          壁纸
        </h3>
        <div className="appearance__row">
          <button
            type="button"
            className="appearance__btn"
            onClick={() => openDesktopPersonalization()}
          >
            打开桌面个性化
          </button>
          <span className="appearance__hint">
            壁纸仍是原来那套四态（网格/图片/纯色/关闭），只存在本机
          </span>
        </div>
      </section>

      <footer className="appearance__footer">
        <button
          type="button"
          className="appearance__btn"
          onClick={reset}
          data-testid="appearance-reset"
        >
          恢复默认皮肤
        </button>
        <span className="appearance__hint" role="status">
          {savedAt ? `已保存 · ${savedAt}` : "改动即时生效并保存在本机"}
        </span>
      </footer>
    </div>
  );
}
