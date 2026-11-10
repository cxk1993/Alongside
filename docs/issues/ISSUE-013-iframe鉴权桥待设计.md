# ISSUE-013 · iframe 鉴权桥（B2）已删除，跨源内嵌登录态待重新设计

> **用途**：跨 agent 提问题 / 报障碍 / 要求别人改东西。
> **规矩**：永远不要直接修改别人的目录。需要对方改，就在这里写一张卡。
> **文件命名**：`ISSUE-001-简短标题.md`，编号递增，不要复用。

- **提出者**：ningxi（前端消费侧 · 第三轮基底）
- **影响谁**：前端 kernel（`apps/web/src/kernel/**`）· 未来任何要做「跨源自家页面免二次登录」的卡
- **状态**：**wontfix（当前形态删除）** · 需求本身 open（待设计）
- **优先级**：low（无阻塞：现有 `auth_ref` 机制已覆盖真实凭据场景）
- **日期**：2026-11-10

---

## 我遇到的现象

`apps/web/src/kernel/iframeAuthBridge.ts` 曾实现一段 `postMessage` 握手：

- 子页 → `parent.postMessage({ type: "alongside:request-token" })`
- 父页（桥）校验 `event.origin` 在 `ALLOWED_IFRAME_ORIGINS` 白名单内 → 回 `{ type: "alongside:token", token }`

但 `ALLOWED_IFRAME_ORIGINS` 是**空常量**，且全仓（除自身与其单测外）**没有任何被嵌页面发起
`alongside:request-token`**。也就是说：`installIframeAuthBridge()` 在 `Desktop` 里每次启动都装，
装了一个**永远不会回 token** 的监听器 —— 看起来有能力，实际是死路。

## 为什么这会挡住我

不是功能缺失，而是**认知污染**：读代码的人（含后来的 agent）会以为 Alongside 支持
"跨源自家 iframe 免登录"，据此设计新卡，最后发现要改的是白名单常量而不是接入方式。
"写了但永远是死路" 比 "没写" 更糟。

同时它也不是当前的真实需求：

- 同源 iframe 天然共享 `localStorage`，不需要桥；
- 跨源自家页面（独立端口/域）的凭据，`apps/web/src/apps/web/WebFrame.tsx` 已用
  **`auth_ref` → 后端换带凭据的 URL**（`webApi.frameUrl`）解决，凭据从不进前端代码；
- 情形 C（外部任意站点）明确排除，不该共享凭据。

## 我认为应该怎么改（已执行）

1. 删除 `kernel/iframeAuthBridge.ts` 与 `kernel/iframeAuthBridge.test.ts`；
2. 删除 `kernel/Desktop.tsx` 里的安装 / 卸载调用点；
3. 保留本卡作为需求记录：**iframe 鉴权桥待设计**。

## 未来重新设计时，至少要回答

- 消费方是谁？（现在一个都没有 —— 没有真实消费方就不要先写桥）
- 白名单从哪来？候选：`manifest.permissions` / `kernelApi` 声明的可信源，
  而不是硬编码常量；空白名单必须等于「能力不存在」，而不是「静默拒绝」。
- 凭据形态：一次性 + 短过期（单次握手票据），而不是把长期 token 直接 postMessage 过去。
- 如何避免与 `auth_ref` 机制重复？（后者已能覆盖 "给内嵌页带凭据" 的主流场景）

## 建议的验证方式

删除后：`grep -rn "iframeAuthBridge\|alongside:request-token" apps/web/src` 应无结果（本卡除外），
`npx tsc --noEmit` 与 `npx vitest run` 全绿。
