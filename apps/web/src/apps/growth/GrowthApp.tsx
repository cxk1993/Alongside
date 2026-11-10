/**
 * ★ 成长罗盘 · 独立窗口（主人 ⑧⑪：「合并到**成长罗盘的窗口展示页**里面去，多页化展示」）
 *
 * 实现（astrbot 下场 · 2026-09-25；2026-11-09 重构）：
 *   复用 `kernel/MultitabFrame`，页签**只从 manifest.tabs 来**
 *   （`kernel/containerPages.tsx` 装配；页视图仍复用各模块 App，不重写业务、
 *   不把三模块打进本 chunk）。原先此处手写「习惯/人格/健康」三页，
 *   而 Dock 的吸收表里没有 habits → 底端栏同时出现「成长罗盘」与「习惯」两个入口。
 *   现在 tabs 是唯一真相源，habits 归口后自动从底端栏消失。
 *
 * 与 `dashboard/GrowthPanel.tsx` 的关系：
 *   那个是 dashboard 里的**卡片**（保留不动）；本组件是**独立窗口**版本。
 *   两者共用同一套"三页"结构，`persistKey` 用不同键（`growth-win`）以免互相干扰。
 */
import MultitabFrame from "@/kernel/MultitabFrame";
import { useContainerPages } from "@/kernel/containerPages";

export default function GrowthApp() {
  const pages = useContainerPages("growth");

  return (
    <div className="growth-win" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <MultitabFrame ariaLabel="成长罗盘" persistKey="growth-win" pages={pages} />
    </div>
  );
}
