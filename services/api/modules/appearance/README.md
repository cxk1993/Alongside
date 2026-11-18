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
| `--radius` | 圆角系数 × 令牌基准（0.5–1.5） | **单一真源**；`--r-xs/sm/md/lg` 由它 `calc()` 派生 |
| `--sp-1` … `--sp-6` | 令牌基准 × 密度（0.85 / 1 / 1.15） | |
| `--font-sans` | 无衬线 / 等宽 / 衬线 | |
| `--shadow-strength` | 阴影档位（0.6 / 1 / 1.6） | **唯一阴影旋钮**；几何与颜色由 `--shadow-color` 派生 |

## 存储格式（v2 三分区）

`localStorage["alongside.skin.v1"]`（键名不变，值结构升级）：

```json
{
  "version": 2,
  "base": "dark",
  "tokens": {
    "theme": { "--accent": "#5b8cff", "--radius": "10px" },
    "light": { "--bg": "#ffffff" },
    "dark":  { "--bg": "#0e1116" }
  },
  "meta": { "name": "…", "author": "…", "license": "…" }
}
```

- `tokens.theme` 与明暗无关；`light` / `dark` 按当前 `data-theme` 覆盖前者（换主题会重算）。
- 与 shadcn `registry-item.json` 的 `cssVars: { theme, light, dark }` 同构，可直接互喂
  （`--accent` 语义不同：shadcn 是 hover 底色，我们是品牌色，导入侧需映射）。
- v1（`accent/radius/density/font/shadow` 五标量）读取时**自动幂等迁移**并回写 v2。

## 写前校验 `validateSkinPack()`

| 档 | 判据 |
|---|---|
| error 拒收 | 结构不成形；缺 `--bg`/`--txt`；值含 `var(` / `url(` / `!important` / `{` `}` `;`；`--radius` 非 px/rem；表面亮度非单调 |
| warning 放行 | 未知令牌（保留，本内核暂不消费）；相邻表面亮度差 < 0.002；缺 `meta.name` |

校验不过时 `applySkin()` **一个变量都不写**并返回原因，页面保持默认外观。

## 验证

```bash
cd apps/web
node node_modules/vitest/vitest.mjs run src/shared/styles src/apps/appearance
node node_modules/typescript/bin/tsc --noEmit
```

后端探针：`services/api/tests/test_appearance.py`。
