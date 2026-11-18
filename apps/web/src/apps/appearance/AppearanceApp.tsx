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
  applySkin,
  choicesFromSkin,
  clearSkin,
  DENSITY_LABEL,
  defaultChoices,
  FONT_LABEL,
  isColor,
  loadSkin,
  RADIUS_MAX,
  RADIUS_MIN,
  readSwatches,
  saveSkin,
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

import "./appearance.css";

const DENSITIES: Density[] = ["compact", "standard", "comfortable"];
const FONTS: FontChoice[] = ["sans", "mono", "serif"];
const SHADOWS: ShadowChoice[] = ["soft", "standard", "strong"];

export default function AppearanceApp() {
  const [choices, setChoices] = useState<SkinChoices>(() => choicesFromSkin(loadSkin()));
  const [hexDraft, setHexDraft] = useState(() => toHex6(choicesFromSkin(loadSkin()).accent));
  const [savedAt, setSavedAt] = useState<string>("");
  const swatches = useMemo(() => readSwatches(), []);

  // ★ 同一 tick 里连点两次（色板 + 密度）时，只靠 state 闭包会丢掉前一次改动：
  //   第二次用的是旧 choices。用一个 ref 串起来，保证「后一次基于前一次」。
  const choicesRef = useRef(choices);

  // 外部改了强调色（取色器/色板）时同步输入框
  useEffect(() => {
    setHexDraft(toHex6(choices.accent));
  }, [choices.accent]);

  const commit = useCallback((next: SkinChoices) => {
    // ★ 落盘的是 v2 皮肤（tokens.theme 里的具体变量值），不是标量 —— 与导入的皮肤包同构。
    const skin: Skin = skinFromChoices(next);
    applySkin(skin);
    saveSkin(skin);
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
    // clearSkin 撤销「皮肤写过的整张变量表」，不只是某几个硬编码名字。
    clearSkin();
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
