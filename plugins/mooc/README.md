# 中国大学MOOC · 内嵌窗口插件

把 [中国大学MOOC](https://www.icourse163.org/) 的网页版装进 Alongside 的一个窗口。**只做互操作**：不抓数据、不注入脚本、不代操作。

- 入口：`/plugin-assets/mooc/index.js`
- 数据能力：无（`provides: []`）
- 登录：由浏览器自己决定；窗口顶部有「在浏览器打开」直链

## 已知限制

1. 需要内核 **2026-11-15（r5）之后**的版本：旧版没有 `/plugin-assets` 资产端点，
   第三方插件 UI 无法运行。
2. 对方站点若加 `X-Frame-Options`/`frame-ancestors`，内嵌会空白（目前三家都没加）。
3. 第三方 Cookie 策略可能让 iframe 里的登录态失效；此时用「在浏览器打开」。
