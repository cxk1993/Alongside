# ISSUE-019 · 浮窗模型字段退役手术单（`z` / `pinned` / `pinZ` / `minimized` / `maximized` / `fixedGeometry` / `_prev` / `topZ` / `topPinZ`）

- **提出者**：bidiu（2026-11-22 17:47 A2A 受凝析委托落卡；盘点结论来自同日 17:16 的只读反查）
- **影响谁**：凝析（lane = `apps/web/src/kernel/store.ts`，逐项勾）／汐瑶（区块模型 RGL 落地，本卡的前置）／bidiu 只读复核，不动前端目录
- **状态**：open —— **前置未满足，当前不要开刀**
- **优先级**：low（RGL 定版后再执行；在此之前只做「标注 + 切断新增依赖」）
- **日期**：2026-11-22
- **盘点基线**：`E:\ai work\work\Alongside` @ `main` `57fc8fe`（含当日 P0 去桌面化 `ddc423d`）。**行号不是契约，动手前重新 grep，代码天天在动。**

---

## 一句话定性（先看这条，别当「死字段清理」做）

这不是「几个没人读的字段顺手删掉」。**这九个字段共同承载一套还在跑的功能——自由层叠窗口模型。**

| 字段 | 它现在真实承载的功能 | 删掉后先坏什么 |
|:--|:--|:--|
| `z`（`WindowState.z`） | 「点击窗口提到最前」的**唯一实现**；`zIndexOf()` 直接喂 `style.zIndex` | 多窗互相遮挡后无法切换，点谁都不上浮 |
| `pinned` / `pinZ` | 窗口标题栏「置顶」按钮；`PIN_BASE=10000` 两层计数不混算 | 置顶功能消失；`WebFrame` 工具栏置顶按钮同步失效 |
| `minimized` | 窗口「最小化」+ 顶栏 `minimizeAllUnpinned` + 命令面板 `act:minimize-unpinned` | 最小化后窗口不消失（`WindowFrame.tsx:216` 的早退失效） |
| `maximized` | 窗口「最大化」按钮 + `_prev` 几何还原 | 最大化后无法还原 |
| `fixedGeometry` | 「固定位置与大小」按钮，`canDrag` 与缩放手柄都读它 | 锁定几何功能消失（**注意：它不是孤儿，见下文更正**） |
| `_prev` | `maximized` 的还原几何，唯一读写点在 store 内部 | 随 `maximized` 一起死，可当孤儿处理 |
| `topZ` / `topPinZ`（`WorkspaceState`） | 每个工作区独立的层叠计数器（T23 契约 #2） | 新开窗的 `z` 无从分配 |

`kernel/TopBar.tsx:26-31` 的注释已经把结论写死了：

> 撤除「最小化全部窗口」「整理桌面」两个 icon-btn …… store 动作（`minimizeAllUnpinned` / `tidyDesktop`）与命令面板条目**本轮不动**，**待区块模型（RGL）落地后随浮窗模型一并退役**。

**所以本卡是「RGL 落地后的手术清单」，不是「现在就可以动手」的通知。** 前置没满足就动 `z`，等于把窗口层叠拆了却还没有新布局接。

---

## 我遇到的现象（实测盘点，逐项可核）

### A. 字段定义与写入点（`apps/web/src/kernel/store.ts`）

| 符号 | 位置 | 说明 |
|:--|:--|:--|
| `WindowState.z` | `store.ts:23-53`（`z` 在 `:26`） | 普通层序号 |
| `WindowState.minimized` | `:27` | 由 `min()` / `restore()` / `focus()` / `tidyDesktop()` 写 |
| `WindowState.maximized` | `:28` | 由 `toggleMax()` 写 |
| `WindowState._prev` | `:31` | 仅 `toggleMax()`（`:517`/`:521`/`:529`）与 `tidyDesktop()`（`:584`）读写 |
| `WindowState.pinned` | `:37` | 由 `setPinned()` 写 |
| `WindowState.fixedGeometry` | `:38` | 由 `open()`（`:463`）、`setFixedGeometry()`（`:631`）写 |
| `WindowState.pinZ` | `:40` | 由 `setPinned()` 分配 |
| `WorkspaceState.topZ` / `topPinZ` | `:55-68`（`:59`/`:61`） | `newWorkspace()` `:128-129` 初始化；`open`/`focus`/`toggleMax`/`setPinned`/`tidyDesktop` 全部递增 |
| `PIN_BASE = 10000` | `:82`，`:806` 再导出 | 置顶层基址 |

相关动作：`open :451-468`、`focus :480-492`、`min :497`、`restore :504`、`toggleMax :511-532`、`setGeo :539-545`、`minimizeAllUnpinned :566-577`、`tidyDesktop :578-589`、`setPinned :600-620`、`setFixedGeometry :628-632`。

导出 helper：`zIndexOf :762`、`topmostNormalId :772`、`normalStack :786`。

### B. 渲染与行为消费端（不含 `kernel/store.ts` 自身）

| 文件 | 行 | 读/写什么 |
|:--|:--|:--|
| `kernel/WindowFrame.tsx` | `:143` `setPinned`；`:162` `fixedGeometry → canDrag`；`:216` `minimized` 早退；`:220` 活动态（`pinned/pinZ/topPinZ` 或 `z/topZ`）；`:229-231` 类名（`win--max`/`--pinned`/`--fixed`）；`:238` `zIndexOf → style.zIndex`；`:260-265` 置顶钮；`:271-276` 固定几何钮；`:285`/`:292-296` 最小化/最大化钮；`:333-334` 缩放手柄 | 整个窗口外壳 |
| `kernel/Desktop.tsx` | `:6` import；`:47` `topmostNormalId` → Esc/Ctrl+W 关闭；`:61` `normalStack` → Alt+Tab 轮换；`:93` `tidyDesktop`；`:141`/`:178-179`/`:194` `minimizeAllUnpinned` | 快捷键与命令注入 |
| `kernel/commands.ts` | `:49`/`:51` 接口；`:105-109` `act:tidy`；`:113-117` `act:minimize-unpinned` | 命令面板条目 |
| `apps/web/WebFrame.tsx` | `:49-50` 读 `pinned`；`:52-54` 读 `fixedGeometry`；`:57` `setPinned`；`:168-176` 置顶按钮；`:183-190` 固定几何按钮 | 网页窗工具栏，与标题栏双向同步 |
| `kernel/TopBar.tsx` | `:26-31` 注释 | 已声明「随浮窗模型一并退役」 |

### C. 假阳性（同名不同义，**不许顺手删**）

| 文件 | 行 | 为什么不是 |
|:--|:--|:--|
| `apps/sidebar/SidebarDock.tsx` | `:36`、`:74` | 侧栏条目自己的 `pinned`，来自后端 `ItemOut`，与 `WindowState` 无关 |
| `apps/sidebar/SidebarApp.tsx` | `:17` | `ItemOut.pinned?: boolean` 类型定义 |
| `apps/collab/capabilities.ts` | `:115`、`:117` | 局部变量 `const pinned = entries.filter(e => e.quick)` |
| `main.tsx` | `:52` | PWA 注释里的英文 pinned |

无影响、可放心：`kernel/Dock.tsx:30` 只读 `moduleId` 判「已打开」；`kernel/Desktop.tsx:33` 只按 `workspaceId` 过滤。

### D. 对上一版盘点的两处更正（重要）

1. **`fixedGeometry` 不是孤儿。** 我 17:16 的清单把它归进「清单外必须一起删的孤儿」，那是错的：`WindowFrame.tsx:162/231/271-276/334` 与 `apps/web/WebFrame.tsx:52-54/183-190` 都在真读真写。它是「随窗口外壳一起退役」的**活功能**，退役时必须同时改两个文件的按钮与 `canDrag`，不是删字段就完。
2. **测试文件路径我上次给错了两条。** 正确位置：`kernel/store.windowCaps.test.ts`、`kernel/store.workspaces.test.ts`、`kernel/usePointerCancel.test.tsx`、`apps/web/WebApp.test.tsx`、`kernel/shell/DesktopSidebar.test.tsx`（不是 `__tests__/` 或 `web/` 下）。

---

## 为什么这会挡住我（前置为什么必须先满足）

1. **`z` 退役 = 换布局模型，不是删代码。** 现在「活动窗口」的判定就是 `z === topZ` / `pinZ === topPinZ`（`WindowFrame.tsx:220`），Esc/Ctrl+W 与 Alt+Tab 又都建立在 `topmostNormalId` / `normalStack` 上。没有 RGL 的网格定位之前，这些语义没有替身。
2. **RGL 版本还没定。** 凝析正在查 `react-grid-layout` 1.5.0–1.5.3 混入 `ip_fetcher` 可执行文件的供应链问题（同批次工作）。选哪个版本、要不要换库，直接决定区块模型什么时候能落地。
3. **持久化键是 `alongside.windows.v1`（`store.ts:75`）。** 见下面那个机制坑——字段退役不做迁移，磁盘上永远退不掉。

### 机制坑：`hydrate` 的 `...w` 会把旧字段原样带回来

`store.ts:727-733` 现在是：

```ts
return {
  ...w,
  workspaceId: wid,
  pinned: w.pinned === true,
  fixedGeometry: w.fixedGeometry === true,
  pinZ: typeof w.pinZ === "number" ? w.pinZ : undefined,
};
```

`...w` 把旧快照里的 `z/minimized/maximized/_prev` 全部展开进 state；`savePersisted()`（`:242-253`）又是 **`windows: s.windows` 整体序列化**。结果：字段从类型里删了，运行时仍然被读入、被写回——用户浏览器里那份 localStorage 会带着幽灵字段一直活下去，下一次 `grep` 又「发现」它们还在，反复清零。

⇒ 退役必须同时把这里改成**显式 pick**（白名单式重建窗口对象），并决定旧快照怎么处理：升 `STORE_KEY` 版本（`v2`）一次性净化，或首次 hydrate 时写回一份净化后的快照。**两件事要么一起做，要么别做。**

---

## 我认为应该怎么改（手术单，按序勾）

> lane 约定：A/B/C/D 段全在 `kernel/store.ts` 与 `kernel/*`，归凝析；E 段注释分散，谁改到自己文件谁勾；bidiu 不改前端目录，只在合并后复核。

### A. `kernel/store.ts` 类型与动作（核心）

- [ ] `WindowState` 删 `z` / `minimized` / `maximized` / `_prev` / `pinned` / `pinZ` / `fixedGeometry`（`:26-40`），保留 `instanceId/moduleId/geo/workspaceId`
- [ ] `WorkspaceState` 删 `topZ` / `topPinZ`（`:59-61`），保留 `id/name/seq`（`seq` 还负责默认位置级联，不在本卡范围）
- [ ] 删 `PIN_BASE`（`:82`）与 `:806` 的再导出
- [ ] 删动作：`min` / `restore` / `toggleMax` / `setPinned` / `setFixedGeometry` / `minimizeAllUnpinned`，`tidyDesktop` 只保留重排几何的部分（去掉 `minimized/maximized/_prev` 写法）
- [ ] `open()` 不再写 `z` / `fixedGeometry`；`focus()` 不再写 `z`/`minimized`
- [ ] 删 helper：`zIndexOf` / `topmostNormalId` / `normalStack`（`:762-791`）——**前提是 Desktop 的 Esc/Ctrl+W 与 Alt+Tab 已有新语义，见 A0**
- [ ] **A0（前置）**：先定新语义——哪个窗口算「活动/最上」？Esc/Ctrl+W 关谁？Alt+Tab 还留不留？**没有答案就别勾上面任何一条**

### B. `kernel/` 渲染与快捷键

- [ ] `WindowFrame.tsx`：删 `:216` 早退、`:220` 活动态判定、`:229-231` 三个类名、`:238` `zIndex`、`:260-276` 置顶/固定几何两钮、`:285/292-296` 最小化/最大化两钮、`:333-334` 缩放手柄（或按新模型保留缩放手柄）
- [ ] `Desktop.tsx`：`:47` Esc/Ctrl+W 与 `:61` Alt+Tab 换新实现；`:93`/`:141`/`:178-179`/`:194` 删 `tidyDesktop`/`minimizeAllUnpinned` 的接线（若动作整体退役）
- [ ] `commands.ts`：删 `act:tidy` / `act:minimize-unpinned` 两条（`:105-117`）与接口 `:49`/`:51`
- [ ] `apps/web/WebFrame.tsx`：删 `:49-57` 两个 selector、`:168-176` 置顶按钮、`:183-190` 固定几何按钮

### C. 持久化迁移（和 A 同批，不许拆）

- [ ] `hydrate()` 的 `:727-733` 从 `...w` 改成显式 `pick`（只挑 `instanceId/moduleId/geo/workspaceId`）
- [ ] `:737-746` 按 `pinZ`/`z` 重算计数器的整段删掉
- [ ] `STORE_KEY` 决策：升 `alongside.windows.v2` + 一次性读旧键迁移；或保留 v1 但在 hydrate 后写回净化快照。**二选一，写进 commit message**
- [ ] `PersistedShape`（`:93-101` 附近的旧顶层 `topZ/topPinZ` 兼容字段）一并清理

### D. 测试（改完必须由红转绿，不许删用例凑绿）

必红、需同步改的 9 个文件：

- [ ] `__tests__/store.test.ts`
- [ ] `kernel/store.windowCaps.test.ts`
- [ ] `kernel/store.workspaces.test.ts`
- [ ] `kernel/WindowFrame.caps.test.tsx`
- [ ] `kernel/WindowFrame.hotplug.test.tsx`
- [ ] `kernel/WindowFrame.workspaces.test.tsx`
- [ ] `kernel/WindowFrame.backendShell.test.tsx`
- [ ] `kernel/usePointerCancel.test.tsx`
- [ ] `apps/web/WebApp.test.tsx`
- [ ] `kernel/shell/DesktopSidebar.test.tsx`

判据：**用例数只许因「被测功能整体删除」而减少，且每个减少的用例要在 commit message 里点名**；因改动而红、最后靠删断言变绿 = 不算。

### E. 注释（4 处说明书，留着会骗后来人）

- [ ] `kernel/TopBar.tsx:26-31`（「待 RGL 落地后随浮窗模型一并退役」→ 落地后改成「已退役」）
- [ ] `kernel/store.ts:30-40`、`:759-761`（两层 z 语义的 T22 注释）
- [ ] `kernel/WindowFrame.tsx` 顶部窗口模型注释
- [ ] `apps/web/WebFrame.tsx` 工具栏按钮注释

---

## 建议的验证方式

```bash
# 1) 字段真没了（含类型与字符串键）
cd apps/web/src
grep -rn "minimized\|maximized\|fixedGeometry\|pinZ\|PIN_BASE" --include=*.ts --include=*.tsx . \
  | grep -v "\.test\." | grep -v "SidebarDock\|SidebarApp\|capabilities.ts\|main.tsx"
# 期望：空

# 2) 旧快照不再被写回幽灵字段（真浏览器，非 jsdom）
#    localStorage 里 alongeside.windows.* 的每个 window 对象，键集合 === {instanceId,moduleId,geo,workspaceId}
#    刷新两次后仍是同一键集合

# 3) 定向回归
npx vitest run src/kernel src/__tests__ src/apps/web src/kernel/shell
npx tsc --noEmit
```

真浏览器必做的三条（jsdom 测不到层叠与命中）：

1. 开三个窗，点最底下那个 → 它到最前（或新模型下等价行为）
2. Esc / Ctrl+W 关的是「你以为的那个窗」
3. 刷新后窗口位置、工作区归属、`iframe` 登录态都还在（契约 #3/#4）

---

## 附：本卡与相邻两件事的边界

- **RGL 供应链**（凝析在查 1.5.0–1.5.3 混入 `ip_fetcher`）：结果决定选版，选版决定本卡何时能开。**本卡不背书任何版本。**
- **pointer-events 手术单**：等 P1 退 `auto` 时再写，与本卡无代码重叠（唯一交集是 `WindowFrame` 的类名，但那两处已在 `f824b08` 钉住）。