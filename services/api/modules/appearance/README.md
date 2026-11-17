# 外观与皮肤（appearance）

把「一组 CSS 变量」做成可调的独立设置窗：**主色 / 圆角 / 密度 / 字体 / 阴影**，
改一次即时生效、持久化在 localStorage，壁纸沿用原来的「桌面个性化」四态。

## 为什么后端是空的

皮肤是纯前端状态：不进数据库、不出网、不需要同步。这个模块存在的唯一理由是
**让内核按 manifest 机械地长出窗口与入口**（一切皆插件），而不是在桌面里硬编码设置窗。

`router.py` 只有 `/health`，`provides` 为空——它不向 AI/MCP 暴露任何能力。

## 适配（这份实现要满足的四条）

| 要求 | 做法 |
|---|---|
| 明暗双主题 | 只写强调色/形状类变量，不碰 `--bg`/`--txt`；`--accent-fg` 按相对亮度自动取黑/白 |
| 小窗口 / 窄屏 | 栅格 `auto-fit + minmax(0,1fr)`，420px 以下预览改两列，面板整体可滚动 |
| 样式门禁 | 组件与 CSS 都不写十六进制；色板、衬线字体、阴影档位全部来自 `tokens.css` |
| 旧配置兼容 | `normalizeSkin()` 对缺字段/脏值/旧版本静默回退默认；`loadSkin()` 不抛错 |

## 变量表

| 变量 | 来源 | 说明 |
|---|---|---|
| `--accent` / `--accent-fg` / `--accent-soft` | 用户选择 | fg 自动对比，soft 为 15% 透明 |
| `--r-xs` … `--r-lg` | 令牌基准 × 圆角系数（0.5–1.5） | |
| `--sp-1` … `--sp-6` | 令牌基准 × 密度（0.85 / 1 / 1.15） | |
| `--font-sans` | 无衬线 / 等宽 / 衬线 | |
| `--shadow-1` / `--shadow-2` | 标准=令牌原值；轻/重来自 `--skin-shadow-*` | |

## 验证

```bash
cd apps/web
node node_modules/vitest/vitest.mjs run src/shared/styles/skin.test.ts src/apps/appearance
node node_modules/typescript/bin/tsc --noEmit
```

后端探针：`services/api/tests/test_appearance.py`。
