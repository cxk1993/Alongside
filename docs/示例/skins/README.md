# 皮肤包示例：夜紫（night-violet.skin.json）

> 这是 **可直接导入的皮肤包**，也是格式的活文档。
> 导入方式：外观与皮肤窗 → 「我的皮肤」→ 导入… → 选这个文件；或控制台 `window.AlongsideSkin.import(<内容>)`。
>
> ★ 为什么仓库里要放一份真包而不是只写规范：
>   规范会漂，能被导入的真包不会——`services/api/tests_protocol/test_skin_pack_examples.py`
>   会逐个把 `docs/示例/skins/*.skin.json` 走一遍 `validateSkinPack`，改坏了当场红。

## 格式要点（与 shadcn registry-item 的 cssVars 三分区同构）

| 字段 | 必填 | 说明 |
|---|---|---|
| `alongsideSkin` | ✅ | 外壳版本，当前 `1` |
| `skin.version` | ✅ | 皮肤结构版本，当前 `2` |
| `skin.base` | ✅ | `dark` / `light`，声明这份皮肤按哪套底色调的 |
| `skin.tokens.theme` | ✅ | 三处都生效的令牌（**必须含 `--bg` 与 `--txt`**） |
| `skin.tokens.dark` / `light` | ⬜ | 只在对应主题下覆盖（切主题自动重算） |
| `skin.meta` | 建议 | `name` / `author` / `license`（导出分享时体现） |

## 会被拒的写法（`validateSkinPack` 真拦，不是警告）

- 值里出现 `var(` / `url(` / `!important` / `{}` `;`
- 变量名不以 `--` 开头
- 缺 `--bg` 或 `--txt`
- 四层表面色亮度不单调（`--bg` / `--surface` / `--surface-2` / `--surface-3` 出现「中间翘头」）

## 能改哪些令牌

**稳定承诺**（皮肤可依赖）：`--bg` `--bg-grid` `--surface` `--surface-2` `--surface-3` `--overlay`
`--txt` `--txt-dim` `--txt-faint` `--line` `--line-strong` `--accent` `--accent-fg` `--accent-soft`
`--ok` `--warn` `--danger`（及 `-fg`）`--radius` `--sp-1..6` `--font-sans` `--font-mono`
`--shadow-color` `--shadow-strength` `--chart-1..8` `--method-get/post/put/delete`

**内核私有**（不承诺，可能变）：`--skin-swatch-*`、`--win-*`、`--topbar-h`、`--dock-h`

## 三个可直接导出的示例

| 文件 | 特征 |
|---|---|
| `night-violet.skin.json` | 夜紫：深底 + 紫强调，演示「只改 theme 三件套」的最小形态 |
| `paper-light.skin.json` | 纸白：完整亮色皮肤，演示 `dark`/`light` 分区各给一套值 |
| `high-contrast.skin.json` | 高对比：演示「换掉整套表面层级 + 加粗线条」而不只是换主色 |