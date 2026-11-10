/**
 * ★ 知识库 · 三合一窗口（主人 ⑤：「复盘 / 笔记 / 文档 可以合并成一个入口端，
 *   并且也在同一个窗口内展示，你可以分成窗口内多个页面的分页形式」）
 *
 * 实现（astrbot 下场 · 2026-09-25 · 接替下线的知默；2026-11-09 重构）：
 *   复用 `kernel/MultitabFrame`，页签**只从 manifest.tabs 来**
 *   （`kernel/containerPages.tsx` 装配；页视图仍复用各模块 App，不重写业务）。
 *   原先此处手写了 review/notes/docs 三页，与 Dock 的吸收表分叉 —— diary 明明被
 *   吸收进知识库却没有页签，入口就此消失。现在 tabs 是唯一真相源，
 *   diary 归口后自动出现「日记」页（2026-11-10 起 diary 已写进
 *   `services/api/modules/knowledge/manifest.json` 的 tabs，页签只有一个来源）。
 *
 * ★ 未做（留后续，如实标注）：OB 树深同步 / 附件映射 / MathJax / 笔记可写自动保存 /
 *   复盘增删笔记 —— 属后端与桥层，非本刀范围。
 */
import MultitabFrame from "@/kernel/MultitabFrame";
import { useContainerPages } from "@/kernel/containerPages";

export default function KnowledgeApp() {
  const pages = useContainerPages("knowledge");

  return (
    <div className="knowledge-win" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <MultitabFrame ariaLabel="知识库" persistKey="knowledge-win" pages={pages} />
    </div>
  );
}
