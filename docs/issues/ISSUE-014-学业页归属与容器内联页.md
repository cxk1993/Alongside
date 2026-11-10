# ISSUE-014 · 「学业」页不是模块，`schedule.tabs` 里那条 `entry:"todo"` 是假数据

> **用途**：跨 agent 提问题 / 报障碍 / 要求别人改东西。
> **规矩**：永远不要直接修改别人的目录。需要对方改，就在这里写一张卡。
> **文件命名**：`ISSUE-001-简短标题.md`，编号递增，不要复用。

- **提出者**：bidiu（前端 kernel · 容器页装配这一刀）
- **影响谁**：前端 kernel（`apps/web/src/kernel/moduleGraph.ts` / `containerPages.tsx`）· `services/api/modules/schedule` · 未来做「容器页装配」的人
- **状态**：**open（数据待修 · 有已知错配，已锁成判据）**
- **优先级**：normal（不阻塞初版，但会阻塞 `schedule` 迁 `useContainerPages`）
- **日期**：2026-11-10

---

## 我遇到的现象

`services/api/modules/schedule/manifest.json` 声明了四页：

```json
{ "key": "calendar", "label": "日程表", "entry": "calendar" },
{ "key": "todo",     "label": "待办",   "entry": "todo" },
{ "key": "study",    "label": "学业",   "entry": "todo" },   // ← 这条
{ "key": "course",   "label": "课程表", "entry": "course" }
```

而仓库里**没有 `study` 这个模块**（`apps/web/src/apps/study/` 只有 `StudyApp.tsx` +
`study.css` + 测试，没有 `index.tsx`，也没有 `services/api/modules/study/`）。
学业页的真实来源是 `apps/web/src/apps/schedule/ScheduleApp.tsx` 里手写的第三页：

```tsx
const StudyApp = lazy(() => import("../study/StudyApp"));
...
{ key: "study", label: "学业", content: wrap(<StudyApp />) },
```

于是机器可读层面出现两处失真：

1. `resolveTabPage({key:"study", entry:"todo"})` 的落点是 **todo 模块** ——
   走通用装配会渲染出**第二个待办页**（不是学业页）。实测见
   `apps/web/src/kernel/moduleGraph.test.ts` ⑧「迁移前置条件」；
2. 页签表说「本容器有一页指向 todo」，实际语义是「本容器有一页由自己渲染」，
   协议里**没有这个态**。

## 为什么这会挡住我

- **它挡住 `schedule` 迁 `useContainerPages`**：容器页装配现在只认 `manifest.tabs`
  （`kernel/containerPages.tsx`），一迁就会静默丢学业页、多出第二个待办页。
  `ScheduleApp` 至今仍是唯一一个手写 `pages` 数组的容器，就是为了绕开这个坑。
- **它和「manifest = 唯一真相源」正面冲突**：`absorbs` / `tabs` 已经是吸收与页序的
  唯一来源，唯一条目里写着一句假话，早晚会被当真（下一把刀就可能照着它写代码）。
- **它不阻塞初版**：学业页现在是能用的，只是「地址」不真。

## 我认为应该怎么改

三条路，我的判断写在每条后面：

**(a) 让 `study` 成为真模块** —— 加 `services/api/modules/study/manifest.json`
（`kind:"builtin"`、`entry:"@/apps/study"`、其余字段全空）+ `apps/web/src/apps/study/index.tsx`，
把 `schedule.tabs.study.entry` 改成 `"study"`。

> ★ 必须**与 `schedule` 迁 `useContainerPages` 同一次做完**。
> 只加 `index.tsx` 而没人按目录约定加载它，那才是死代码（ningxi 这个判断成立）——
> 它不死的唯一前提是「manifest 是唯一入口，且容器真的按它装配」。
> 形态上不突兀：`growth` / `knowledge` / `schedule` / `system` 四个容器本来就**只有
> `manifest.json`、零后端文件**，`study` 只是第一个非容器型的「纯前端模块」。

**(b) 协议加一态「容器内联页」**（`TabPageMode` 多一个 `inline`）—— 语义上最诚实，
但 manifest 是后端 JSON，它无法表达「这块 JSX 由容器 App 自己渲染」。
若 `inline` 只表示「装配器跳过这一页」，就会出现**两个装配者**（容器 App 与
`useContainerPages` 各拥有一部分页序），页序真相又裂成两半。要走必须先回答：
页序归谁、`inline` 页的标签归谁、`MultitabFrame` 的 `persistKey` 冲突怎么算。
**不推荐现在做**。

**(c) 保持 `entry:"todo"` + 注释说明已知错配** —— 最小改动，但机器可读数据继续撒谎。
**只是权宜，不能当终点。**

**⇒ 判断：目标是 (a)，且与 `schedule` 迁移成对落地；现在不改。**
理由是它不阻塞初版，而 (a) 会把 `schedule` 从「手写页容器」变成「manifest 驱动容器」，
是独立一刀、需要单独验收。当前状态已用判据锁住，不会静默：

- `apps/web/src/kernel/moduleGraph.test.ts` ⑧
  「容器迁 `useContainerPages` 的前置条件（不满足就是静默丢功能）」；
- `apps/web/src/kernel/containerPages.tsx` 文件头第 13–18 行（讲清为何 `schedule` 不得先迁）。

## 走 (a) 之前必须先回答

1. **零后端模块允许吗**：`study` 没有任何 `router.py` / `service.py`，它要不要出现在
   能力目录（`catalog`）、能力地图导出、`/api/v1/_modules` 清单里？现在的 29 个模块里
   没有先例（四个容器除外，而容器是容器）。
2. **`study` 复用 todo 的数据**：`StudyApp` 的数据源是 `todoApi.list({tag:"学业"})`。
   它的 manifest 该不该写 `consumes: ["todo.item.*"]`？不写就是绕过能力声明，
   写了又和「consumes 是事件订阅入口」（`core/events.py`）语义不符 —— 需要一次口径决定。
3. **模块 id 与页签 key 必须同名吗**：`tabs[].entry` 允许与 `key` 不同（协议明确允许，
   见 `resolveTabPage` 注释）。`study` 走 (a) 后两者同名，要不要把「同名优先」写进 schema 说明？
4. **包与基线锁**：`study` 不属于任何 pack，`packs/official.life-tasks/pack.lock.json`
   要不要因此重签？（按现行规则：只要不是 pack 成员就不进锁。）

## 建议的验证方式

走 (a) 之后：

1. `resolveTabPage({key:"study", entry:"study"}, modules)` 返回 `mode:"app"`、
   `appEntry:"@/apps/study"`、`moduleId:"study"`；
2. `buildModuleGraph(31+1 份 manifest).tabs.schedule` 四页 order 不变，
   `absorbed("study") === "schedule"`，`conflicts` 仍为 `[]`；
3. `ScheduleApp` 删掉手写 `pages`、改调 `useContainerPages("schedule")` 后，
   `ScheduleApp.test.tsx`（四页签 / 默认日程表 / 切页）全绿；
4. `grep -rn "entry\": \"todo\"" services/api/modules/schedule/manifest.json` 应无结果。
