# ISSUE-017 · `modules.json` 退役：`Desktop.tsx` 的 fetch effect 还背着 `hydrate()`

- **提出者**：bidiu（复核准 `17-modules.json退役核查` 时发现）
- **影响谁**：凝析（`apps/web/**` 在飞区）、任何执行退役的人
- **状态**：open
- **优先级**：normal（数据上安全，但**按原方案落地会丢窗口布局**）
- **日期**：2026-11-22

---

## 我遇到的现象

汐瑶 14:07 的核查（`E:/ai work/item-team/alongside/17-modules.json退役核查-20261122.md`）三条数据，我独立复跑：

- ① **成立**：`modules.json` 17 条 id 全部命中后端 45 个 manifest（`services/api/modules/*/manifest.json` + `plugins/*/manifest.json`），只在静态侧的 **0** 个，交集里 entry 为空的 **0** 个。
- ② 未独立复验（8013 无 token，401 在认证前就返回）。量级可信，不阻塞。
- ③ 逻辑成立（未登录渲染的是登录页）。

**但退役方案漏了一个非可选依赖**：

1. 要删的 `apps/web/src/kernel/Desktop.tsx:98-113` 那个 effect（`Desktop.tsx:28` 是 `MODULES_URL` 常量）不只 `registerModules(list)`，还调了 `useDesktopStore.getState().hydrate()`（**Desktop.tsx:105**）。
2. 全仓 grep `hydrate`：**唯一的生产调用点就是这一处**，其余 20+ 处全部在 `*.test.ts`。而 `store.ts:12` 自述「几何存 localStorage，刷新后由 Desktop 调 hydrate() 恢复」。
   ⇒ 按「删整个 fetch effect」执行 = **刷新后窗口与工作区布局不再恢复**（每个人下次打开桌面全空）。这比 25–45ms 重得多，而且 tsc/vitest 全绿抓不到。
3. `hydrate()` 也**不能**原样留在挂载时：`store.ts` 的 filter 是

   ```ts
   const reg = s.modules[w.moduleId];
   return reg && reg.enabled;   // 只恢复「已注册且启用」的模块的窗口
   ```

   `modules.json` 删掉后，挂载那一刻 `store.modules` 是空的 → 所有窗口被过滤掉。
   顺序依赖已被测试固定：`apps/web/src/__tests__/store.test.ts:111` 的用例是先 `st.registerModule(alpha)` 再 `hydrate()`。

4. 注释要同步的不是 3 处，是 **5 处**：
   - `apps/web/src/kernel/ModuleRegistry.ts:9`
   - `apps/web/src/kernel/plugins/PluginRegistry.ts:135`（历史叙述，可保留）
   - `apps/web/src/kernel/plugins/PluginRegistry.ts:164`（立论会失效，见下）
   - `apps/web/src/apps/collab/capabilities.ts:39`（原文写成 `apps/collab/...`，路径漏了 `web/src`）
   - `apps/web/src/kernel/store.ts:9`
   - `apps/web/src/apps/calendar/README.md:9`

5. `PluginRegistry.ts:164` 那条「不能拿 store 全量做差集，因为 store 里还装着 modules.json 登记的内置模块」——退役后这个**理由**消失，但 `lastBackendModuleIds` 的**逻辑仍然安全**（只注销「上一轮后端带来、本轮消失」的 id）。**不要顺手改逻辑**，只改注释措辞。

## 为什么这会挡住我

退役本身我同意（双源并行已经漂了，见下）。但这是 `apps/web` 内核的活，属凝析领地，我只做后端。若按原「先改注释 → 再删 fetch effect → 最后删 json」落地，第 2 步会以一次「看似清理」的提交换来「刷新丢布局」的回归，而且现有 hydrate 用例自己先 `registerModule`，**永远绿**，门禁不报警。

## 我认为应该怎么改

三步，每步独立提交、可单独 revert：

**第 1 步（零行为变化）**：注释同步上面 5 处；`PluginRegistry.ts:164` 措辞改成「历史上 store 里还有 modules.json 登记的内置模块……；现在注册源收敛为后端清单（+ 运行时 loader），边界仍成立」。

**第 2 步（拆 effect）**：删 `MODULES_URL` 常量 + fetch 链 + 随之无用的 `ModuleManifest` 类型 import；**保留 `initTheme()`**；把 hydrate 改成「模块首次非空后跑一次」：

```tsx
const modules = useDesktopStore((s) => s.modules);
const hydrated = useRef(false);

useEffect(() => {
  initTheme();
}, []);

useEffect(() => {
  if (hydrated.current || Object.keys(modules).length === 0) return;
  hydrated.current = true;
  useDesktopStore.getState().hydrate();
}, [modules]);
```

- **必须 guard**：启用/停用/安装/卸载插件都会走 `PluginProvider.refresh` → `syncPluginsToStore` → `modules` 引用变化。无 guard 的 hydrate 每次都把桌面重置回 localStorage 快照 —— 用户刚拖的窗会被弹回去，比不恢复更烦。
- **必须等非空**：见上面 `reg && reg.enabled` 的 filter。
- 可选：后端 plugins 请求失败（401/500/断网）时 modules 永远空 → 不 hydrate。此时桌面本来也没有模块可恢复，可接受；若要更稳，可在 `PluginProvider` 的 error 分支里给一次「空布局恢复」，但这属于另一张卡。

**第 3 步**：删 `apps/web/public/modules.json`；`grep -rn "modules.json"` 应只剩注释里的历史说明。

## 建议的验证方式

四条缺一不算：

1. `npx tsc --noEmit` → 0；全量 vitest 全绿（当前基线 94 文件 / 810 passed / 1 skip）。
2. **新增持久化回归用例**（不是 mock 源就够）：`registerModule → openWindow → setGeo → 清内存 → 让 modules 非空 → hydrate → 断言 geo 回来`；再补一条「modules 为空时 hydrate 不恢复任何窗口」（锁死 filter 语义）。
3. **真浏览器**（唯一能抓住本次风险的）：开 2 个窗、分到两个工作区 → F5 → 窗口与工作区都在、位置不变；再「停用/启用某插件」触发 refresh → **刚拖的窗位置不被弹回**。
4. 首屏：登录后到 `/api/v1/plugins` 返回之间（25–45ms）不白屏；空态或骨架可接受。

---

## 附带发现（不阻塞，供以后决定）

字段层面双源已经漂了 **257 处**（17 条交集逐字段 diff）：

| 类 | 例子 |
|:--|:--|
| `icon` 全 17 条不同 | 静态侧是中文单字（盘/历/待…），后端是语义名（`box`/`globe`/`bot`/`bell`/`api`） |
| `name` 3 条不同 | 习惯→习惯打卡、智能体→AI 编排、人格→人格体系 |
| `window` 尺寸 8 条不同 | calendar 1000×700 → 960×620；agents 800×600 → 980×680；mcp minW 520→560 |
| `slots`/`api`/`provides` 等 | 静态侧大量为 `null`，后端有值（退役后更全，不是更少） |

但 `store.registerModule` 是**覆盖式**写入（`modules: { ...s.modules, [m.id]: ... }`），`PluginProvider` 登录后同步会用后端值把静态值覆盖掉 —— 所以**退役不改变登录后的视觉**。唯一变化是「首屏 25–45ms 内 Dock 图标用中文单字、之后变 box」→ 退役后全程 box。

`box` 在 `kernel/icons/default-set.tsx` 里存在，所以登录后 17 个应用**本来就全是同一个 box 轮廓图标**。若想让它们各有图标，正解是改后端 manifest 的 `icon` 语义名（`calendar`/`check-square`/`wallet`…），**不是留着 modules.json**。这条与退役无关，单独决定。

---

## 执行结果（凝析 2026-11-22 15:3x）

- 第1步 `9a56aae`：注释同步 5 处，零行为变化。
- 第2步 `4325652`：Desktop 拆 effect（initTheme 保留；hydrate 等 modules 非空 + useRef 只跑一次）；desktop 回归用例两条。
- 第3步 `d32e2fa`：删 `public/modules.json`（全仓仅剩注释里的历史叙述）。
- **额外真栈回归** `df0ddb5`：F5 后 store 以空窗口起来，插件同步的 `savePersisted` 会先把旧快照洗掉 → hydrate 无可恢复。
  加 `hydratedOnce` 守卫（未 hydrate、当前空、快照有窗 → 跳过保存）；store 测试补一条。
- 真栈验收：5188 开 2 窗 → F5 → 2 窗原样恢复（日程待办/笔记），console 0 错；`/modules.json` 请求 0 次。
- 门禁：store+desktop 13 passed；tsc 当时被他人 in-flight 的 2 个未用变量挡住，与本卡改动无关。
- 状态：**done**（比丢可复核；「后端 plugins 失败则不 hydrate」按卡里所述可接受，未另开卡）。
