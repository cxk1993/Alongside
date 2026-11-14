# hello-seam（插件 id：`hello-seam`）

最小第三方插件示例 —— 用来验证**热插拔全链路**：装 → 开 → UI 可见 → 禁 → 卸，
全程不重启后端、不重编前端。

它同时是三条判据的活证据：

| 判据 | 怎么看 |
|---|---|
| **多文件第三方插件可用** | `api/router.py` 里 `from .models import SEAM_MESSAGE` 是**相对导入**。旧内核按文件路径加载 router 且不给 submodule_search_locations，这句直接 ImportError —— 也就是说这个插件装得上，就说明第三方多文件插件真的能写了 |
| **UI 是运行时加载的** | `web/dist/index.js` 是构建产物，不在前端构建树里。前端 `import()` 的是 `GET /plugin-assets/hello-seam/index.js` |
| **热插拔是零重启的** | 装/卸全程只调 API，后端进程不动、前端不重编 |

## 结构

```
plugins/hello-seam/
├── manifest.json          entry = /plugin-assets/hello-seam/index.js
├── build.sh               前端构建脚本（配方示例）
├── api/
│   ├── router.py          路由（用相对导入取兄弟模块）
│   └── models.py          兄弟模块（证明相对导入可用）
└── web/
    ├── src/index.tsx      源码
    └── dist/index.js      构建产物（后端服务的就是它）
```

## ★★ 先读这条：别对这份目录直接 uninstall

本目录在**发行根** `plugins/` 下。`uninstall` 会清状态、摘路由、回滚迁移；
但对**文件**，r5 起有作用域守卫：**只有落在可写数据根 `<data_dir>/plugins/`
里的目录才被回收**，发行根里的目录**只清状态、不删文件**
（`core/plugins/manager.py` 的 uninstall 第 5 步）。

> 这条守卫是实测事故逼出来的：早期实现对 `info.directory` 无条件 `rmtree`，
> 于是对本目录走一遍 install→uninstall 会**删掉示例插件的源码**。
> 回归测试：`tests/test_plugin_hotplug_security.py::test_uninstall_of_bundled_root_keeps_files`。

想试真正的"运行时安装"，请走下面的上传路径 —— 装出来那份在
`<data_dir>/plugins/hello-seam`，卸载只删那一份。

## third-party 的 UI 契约（写第三方插件必读）

1. **产物是单文件 ESM**，落在 `web/dist/index.js`。
   后端用 `GET /plugin-assets/<id>/index.js` 服务它（登录鉴权 + 插件 ACTIVE 才回文件）。

2. **`react` / `react-dom` / `react/jsx-runtime` 必须 external，不许打进产物。**
   两份 React 会炸 hooks，而且失败模式很隐蔽：组件能渲染，`useState` 一调就崩。
   源码照常写 `import React from "react"` —— 由宿主的 import map 提供，插件侧零适配。

3. **`export default` 一个组件模块**，形状与内置插件一致：
   ```js
   { manifestId: "hello-seam", Component, slots: {} }
   ```

4. **不许引 CDN / 绝对外部 URL** —— 本系统要离线可用。

5. **不许 `import` 别的插件、不许 join 别人的表**。要数据就调对方的公开 API，
   并在 `manifest.requires` 里声明能力。

## 构建

```sh
sh plugins/hello-seam/build.sh
```

脚本用 `apps/web` 里已有的 esbuild。核心参数就是那三个 `--external`
（见「UI 契约」第 2 条）。用 Vite 的等价写法：

```js
// vite.config.js
export default {
  build: {
    lib: { entry: "src/index.tsx", formats: ["es"], fileName: "index" },
    rollupOptions: { external: ["react", "react-dom", "react/jsx-runtime"] },
  },
};
```

## 装它（端到端）

```sh
TOKEN=$(...登录取 access token...)

# 0) 先建库（全新部署必做一次；缺了它 plugin_state 表不存在，任何 install 都 500）
python -m db.migrate upgrade head

# 1) 打包（照原样打成 zip；上传后端按 manifest.id 落到 <data_dir>/plugins/<id>）
cd plugins && zip -r ../hello-seam.zip hello-seam
# ★ 路径里有空格时把它换成 Windows 形式（E:/...）——原生 curl 打不开 /tmp/... 这类 MSYS 路径。
curl -sS -X POST http://127.0.0.1:18000/api/v1/plugins/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@../hello-seam.zip;type=application/zip"

# 2) 装（上传只落地，install 才激活/挂路由）
curl -sS -X POST http://127.0.0.1:18000/api/v1/plugins/install \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"id":"hello-seam"}'

# 3) 资产可取（未启用时这里是 404）
curl -sS -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:18000/plugin-assets/hello-seam/index.js \
  -H "Authorization: Bearer $TOKEN"

# 4) 禁 → 卸（卸的是数据根那份；本目录不受影响）
curl -sS -X POST http://127.0.0.1:18000/api/v1/plugins/hello-seam/disable \
  -H "Authorization: Bearer $TOKEN"
curl -sS -X POST http://127.0.0.1:18000/api/v1/plugins/hello-seam/uninstall \
  -H "Authorization: Bearer $TOKEN"
```

## 信任模型（v1）

装第三方插件 = **信任它的代码以你的身份运行**。UI 是用户会话里的任意 JS：
与宿主同源、拿得到令牌、能调你能调的 API。v1 没有沙箱（iframe 方案留二期），
安全性建立在两道闸上：**安装要管理员令牌** + **只有 ACTIVE 的插件代码能被加载**。

所以：只装你信得过的代码。这与装浏览器扩展是同一种信任决定。
