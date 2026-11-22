import { describe, expect, it } from "vitest";

/**
 * ★ T30 · 布局回归钉子：`.collab-card--timeline` 不许再用**百分比 max-height**。
 *
 * 为什么需要一条读 CSS 文本的测试：这是纯样式 bug，jsdom 没有排版引擎，
 * 渲染测试量不出「卡片被压成 26px」；而它上线后造成的后果是「记录全在、人一条看不见」。
 * 所以钉子钉在**规则本身**：百分比上限只许放在列表（能被内容撑开的那层），不许放在卡上。
 *
 * 实测（5188 / 1440×900 / main 007389f）：max-height:30% 让卡高 26px
 * （clientHeight 24 / scrollHeight 74），时间线每一行的「详情」按钮 elementFromPoint
 * 命中的是底层 DIV.windows —— 看不见也点不到。置 none 后卡高 76、按钮可命中。
 *
 * 注意：`import css from "./collab.css?raw"` 在 vitest 下拿到的是**空串**（实测 LEN 0，
 * CSS 走的是 vitest 的样式管线），所以这里直接从磁盘读同一份文件（只读，不改）。
 */
// 本仓库刻意不装 @types/node（见 src/vite-env.d.ts）；这条测试只用到 readFileSync，
// 形状声明放在同目录 node-fs.d.ts（.d.ts 才是全局环境声明，写在模块里会被当成「扩充」）。
import { readFileSync } from "node:fs";

// ⚠️ 不能用 `new URL(..., import.meta.url)`：jsdom 下 import.meta.url 是 http://localhost/
//    （实测抛 "The URL must be of scheme file"）。vitest 的 cwd = apps/web，
//    所以按项目根相对路径读；读错时下面第一条断言会红——不会假绿。
const raw = readFileSync("src/apps/collab/collab.css", "utf8");

/** 取某个选择器的声明块正文（选择器须独占行首；同选择器多次出现则合并）。 */
function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

function blockOf(css: string, selector: string): string {
  css = stripComments(css);
  const out: string[] = [];
  let i = 0;
  while ((i = css.indexOf(selector, i)) !== -1) {
    const open = css.indexOf("{", i);
    const close = css.indexOf("}", open);
    if (open === -1 || close === -1) break;
    // 行首前缀为空才算「这个选择器自己的块」，否则 .collab-timeline 会命中 .collab-timeline__row
    const lineStart = css.lastIndexOf("\n", i) + 1;
    if (css.slice(lineStart, i).trim() === "") out.push(css.slice(open + 1, close));
    i = close;
  }
  return out.join("\n");
}

describe("T30 · 它做过的面板高度（CSS 自指陷阱回归钉子）", () => {
  it("读到的是真样式表（空串会让下面两条假绿）", () => {
    expect(raw.length).toBeGreaterThan(1000);
  });

  it("时间线卡不许用百分比 max-height（自指 → 被压成一条缝）", () => {
    const card = blockOf(raw, ".collab-card--timeline");
    expect(card).not.toMatch(/max-height\s*:\s*\d+(\.\d+)?%/);
    expect(card).toMatch(/max-height\s*:\s*none/);
  });

  it("上限放在列表上：像素级 + 内滚（超 5 行内滚，不是把记录藏掉）", () => {
    const list = blockOf(raw, ".collab-timeline");
    expect(list).toMatch(/max-height\s*:\s*\d+px/);
    expect(list).toMatch(/overflow-y\s*:\s*auto/);
  });
});
/**
 * ★ T31 · 浮层鼠标穿透回归钉子（2026-11-22 05:36 实测撞出来的）。
 *
 * `desktop.css` 里 `.windows__surface` 与它下面的 `.collab-surface` 都是
 * **pointer-events:none**（背景面不吃鼠标，桌面右键/拖选才不被挡），靠一份**白名单**
 * 把可交互的层重新打开 auto。
 *
 * 白名单是「列进来才可点」，不是「列出去才不可点」：漏一个 = 那个层整块鼠标穿透。
 * 05:36 真浏览器实测：提案详情抽屉 `.collab-detail` 漏在名单外 →
 *   · 「关闭」「执行参数 · 展开」elementFromPoint 全落到下面卡片的 BUTTON 上，点不动
 *   · 抽屉连滚轮滚动都收不到
 *   · T27 补的键盘路径（Esc / 焦点）掩盖了它 —— 键盘用户没事，鼠标用户整个卡死
 *
 * 为什么只能钉规则文本：jsdom 没有排版与命中测试，`elementFromPoint` 不存在，
 * 渲染测试永远测不出这一类。真验收必须在真浏览器里 elementFromPoint 一次。
 */
const desktopCss = readFileSync("src/kernel/desktop.css", "utf8");

describe("T31 · 共处工作台浮层的 pointer-events 白名单", () => {
  it("读到的是真样式表（空串会让下面两条假绿）", () => {
    expect(desktopCss.length).toBeGreaterThan(1000);
  });

  it("背景面整体不吃鼠标（白名单的前提还在，否说明重构后这条钉子已无意义）", () => {
    expect(blockOf(desktopCss, ".windows__surface .collab-surface")).toMatch(/pointer-events\s*:\s*none/);
  });

  it("提案详情抽屉在白名单里（漏掉 = 看得见、点不到）", () => {
    expect(blockOf(desktopCss, ".windows__surface .collab-detail")).toMatch(/pointer-events\s*:\s*auto/);
  });

  it("已签/撤销等卡片与状态条仍在白名单（防止改这条时顺手删掉旧的）", () => {
    const card = blockOf(desktopCss, ".windows__surface .collab-card,");
    const status = blockOf(desktopCss, ".windows__surface .collab-status,");
    expect(card + status).toMatch(/pointer-events\s*:\s*auto/);
  });
});

/**
 * ★ P0-c · 能力条 / 活动条的布局回归钉子（2026-11-22 10:5x）。
 *
 * 两块都是「新做出来的东西把主墙挤破」的高风险区：
 *   · 能力条 21 个芯片若不强制单行横向滚动，1366×768 下会换三行、把提案卡挤没；
 *   · 展开态若不设像素上限，全量网格会把主墙顶出可视区（而人被挤走后不知道该滚哪里）；
 *   · 活动条展开同理。
 *
 * 与 T30 同一条教训：**max-height 在 auto 行里写百分比等于自指**，只许 px。
 * 这条做成全表断言（不止这一次的两个块），以后谁写百分比都会红。
 */
describe("P0-c · 能力条与活动条的高度/滚动规则", () => {
  it("能力条芯片单行横向滚动（换行会把主墙挤掉）", () => {
    const chips = blockOf(raw, ".collab-caps__chips");
    expect(chips).toMatch(/flex-wrap\s*:\s*nowrap/);
    expect(chips).toMatch(/overflow-x\s*:\s*auto/);
  });

  it("全量网格与活动条展开态：像素上限 + 内滚", () => {
    const grid = blockOf(raw, ".collab-caps__grid");
    expect(grid).toMatch(/max-height\s*:\s*\d+px/);
    expect(grid).toMatch(/overflow\s*:\s*auto/);

    const act = blockOf(raw, ".collab-act.is-open");
    expect(act).toMatch(/max-height\s*:\s*\d+px/);
    expect(act).toMatch(/overflow\s*:\s*auto/);
  });

  it("全表不许出现百分比 max-height（T30 那类自指一处都不许回潮）", () => {
    expect(stripComments(raw)).not.toMatch(/max-height\s*:\s*\d+(\.\d+)?%/);
  });

  it("能力芯片与「全部」按钮有 focus 环（键盘用户找得到自己在哪）", () => {
    expect(blockOf(raw, ".collab-chip:focus-visible")).toMatch(/box-shadow\s*:\s*var\(--focus-ring\)/);
    expect(blockOf(raw, ".collab-caps__more:focus-visible")).toMatch(/box-shadow\s*:\s*var\(--focus-ring\)/);
  });
});

/**
 * ★ P0-c · 鼠标命中前提（与 T31 同一坑，2026-11-22 11:0x 实测）。
 *
 * 宿主背景面 `pointer-events: none` + 白名单是**继承**关系：新层不显式写 auto
 * 就继承 none，浏览器里看得见却点不到（elementFromPoint 落到 DIV.windows）。
 * jsdom 测不出命中，只能钉规则文本 —— 真验收另做 elementFromPoint。
 */
describe("P0-c · 新层的 pointer-events（继承陷阱）", () => {
  it("能力条与活动条自己打开指针事件（漏了 = 看得见、点不到）", () => {
    const block = blockOf(raw, ".collab-caps,");
    expect(block).toMatch(/pointer-events\s*:\s*auto/);
  });
});
