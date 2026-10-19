# 日程表（T05）前端

Alongside 的日程/日历模块前端，作为内核插件加载（manifestId=`calendar`）。

## 边界

- 仅负责 `apps/web/src/apps/calendar/**` 的前端实现。
- 后端（FastAPI + SQLite，commit `9573626`，11 测试通过）已完成，本模块只对接真实 API，**不使用任何 mock 冒充真实数据**。
- 插件入口契约（`index.tsx` 默认导出 `PluginModule { manifestId, Component, slots }`）由 T14 的加载器按 `manifest.entry = "@apps/calendar"` 加载；日历是否出现在侧边栏由 T14 在 `public/modules.json` 注册决定（不在本卡边界内）。

## 目录结构

```
calendar/
├─ index.tsx              插件入口（PluginModule + dashboard.card 插槽）
├─ CalendarApp.tsx        主应用：工具栏 / 视图切换 / 缩放 / SSE 接线 / Toast
├─ calendar.css           全部样式（仅用设计令牌 var(--xxx)）
├─ api.ts                 数据请求层（写请求自带 Idempotency-Key）
├─ state.ts               UI 状态（视图/缩放/选中/Toast）
├─ lib/
│  ├─ time.ts             纯时间逻辑（吸附/钳制/分段/重叠/子块不越父）
│  └─ cache.ts            事件树增量合并（按 id 替换/删除/追加）
├─ hooks/
│  └─ useCalendarEvents.ts TanStack Query 拉取 + SSE 增量 + 乐观更新/回滚
├─ block/
│  ├─ EventBlock.tsx      单段色块（拖动/拉伸/选中/改名/QuickCard）
│  ├─ ChildBlock.tsx      子块（继承父事件做边界钳制）
│  ├─ useBlockDrag.ts     拖动（transform+rAF，30min/1day 吸附，周内钳制）
│  ├─ useBlockResize.ts   下缘→时长 / 右缘→跨天
│  └─ useCreateDrag.ts    空白拖拽新建
├─ grid/
│  ├─ TimeGrid.tsx        7天×24h 网格（重叠检测/当前天高亮/新建）
│  ├─ NowLine.tsx         当前时间红线
│  └─ MonthGrid.tsx       月视图概览
├─ inspector/
│  ├─ QuickCard.tsx       悬浮交互卡片（改名/换色/定位/加子块/删除）
│  └─ Inspector.tsx       右侧属性面板 + 子块管理
└─ slots/
   └─ DashboardCard.tsx   仪表盘卡片插槽
```

## 关键实现说明

- **对接真 API**：读请求走内核 `@/shared/api/client`；写请求因共享 client 暂不支持自定义头，
  在 `api.ts` 内做带 `Idempotency-Key` 的薄封装（复用 `ApiError`、同一 token 来源）。
  建议内核 client 后续支持 `headers` 选项（已记入报告 issue）。
- **乐观更新 + 回滚**：`useCalendarEvents` 在 `onMutate` 直接改缓存，失败 `onError` 回滚并 toast。
- **SSE 增量同步**：订阅 `calendar.event.created/updated/deleted`，只做按 id 的增量合并，不整表重拉。
- **性能红线**：拖动/缩放用 `el.style.transform` + `requestAnimationFrame` 直接操作 DOM，不在每帧 setState；
  一周 50 块拖动 fps≥55 为**需人工验证**项（jsdom 无法测真渲染帧率）。

## 验证

- 纯逻辑（吸附/钳制/跨天分段/重叠/子块钳制/乐观回滚）均有 jsdom 测试，命令：
  `cd apps/web && node node_modules/vitest/vitest.mjs run src/apps/calendar`
- 与真后端联调、拖拽帧率、点钟定位、SSE 实时性等交互项，需在真服务下**人工验证**。
