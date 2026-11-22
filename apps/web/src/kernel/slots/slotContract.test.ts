/**
 * 槽位契约台账（P1 · 2026-11-22）。
 *
 * 为什么要有这一条测试：
 *   全仓 14 个扩展点，产品代码里只有 5 个宿主；后端 manifest 已往 8 个槽投了 31 份贡献，
 *   其中 11 份（desktop.dock 8 / settings.page 2 / search.provider 1）**无处可去**。
 *   过去没人报错：插件写了贡献、契约不校验、UI 不显示 —— 「接了但看不见」是静默失败。
 *
 * 本测试的口径（不是禁止，而是**逼着人记账**）：
 *   1. 每个槽只有三种合法状态：HOSTED（有宿主）/ KNOWN_DEAD（明确欠账，带原因与贡献数）/ 零贡献未宿主（无害）。
 *   2. 任何槽一旦有贡献却没有宿主，必须在 KNOWN_DEAD 里登记 —— 否则红。
 *   3. KNOWN_DEAD 登记的贡献数必须与 manifest 实际一致 —— 数量变了也要红（逼着更新台账）。
 *   4. manifest 里出现的槽名必须是 SLOTS 里定义过的 —— 拼错/新增未走 RFC 也要红。
 *
 * 修好一个欠账（补宿主或删定义）时，把对应项从 KNOWN_DEAD 删掉即可；测试会提醒你数字对不上。
 */
import { existsSync, readFileSync, readdirSync } from "node:fs";

import { describe, expect, it } from "vitest";

import type { SlotName } from "../plugins/types";
import { SLOT_NAMES } from "./slots";

/** 真宿主表：代码里消费这四个槽的地方（改代码必须同步改这里）。 */
const HOSTED: Partial<Record<SlotName, string>> = {
  "desktop.widget": "kernel/Desktop.tsx · <SlotHost slot=\"desktop.widget\">",
  "dashboard.card": "apps/dashboard/DashboardApp.tsx · <SlotHost slot=\"dashboard.card\">",
  "desktop.dock-left": "kernel/shell/DesktopSidebar.tsx · useSlotContributions(slot)",
  "desktop.dock-right": "kernel/shell/DesktopSidebar.tsx · useSlotContributions(slot)",
  "window.sidecar": "kernel/WindowFrame.tsx · useSlotContributions(\"window.sidecar\")",
};

/**
 * 已知欠账（有贡献、无宿主）。每项必须写「为什么」和「当前贡献数」。
 * 欠账清零 = 从这张表删掉；若只想退役该槽，改 SLOTS 定义并同步契约文档。
 */
const KNOWN_DEAD: Partial<Record<SlotName, { reason: string; contributions: number }>> = {
  "desktop.dock": {
    reason: "Dock 默认隐藏（T17）且从不读 slot 贡献（只遍历 store）；8 份贡献待迁到 dock-left/right 或随 Dock 退役",
    contributions: 8,
  },
  "settings.page": {
    reason: "设置页尚未接 SlotHost；2 份贡献（mcp 控制台、push 设置页）目前只能靠容器页签进入",
    contributions: 2,
  },
  "search.provider": {
    reason: "命令面板 v1 只读 store（模块/动作/能力），尚未消费 search.provider；1 份贡献",
    contributions: 1,
  },
};

/** manifest 贡献总数快照：变化会强制更新台账（防止悄悄多出死贡献）。 */
const EXPECTED_TOTAL_CONTRIBUTIONS = 31;

/**
 * vitest 的 cwd = apps/web（与 collabLayout.test.ts 同一口径），
 * 所以 `../../services/api/modules` 就是仓库根的模块目录；读不到直接抛，不静默跳过。
 */
const MODULES_DIR = "../../services/api/modules";

function findModulesDir(): string {
  if (!existsSync(MODULES_DIR)) {
    throw new Error(`找不到 ${MODULES_DIR} —— 测试必须在 apps/web 下运行`);
  }
  return MODULES_DIR;
}

function readContributions(): Map<string, string[]> {
  const dir = findModulesDir();
  const out = new Map<string, string[]>();
  for (const name of readdirSync(dir)) {
    const manifestPath = `${dir}/${name}/manifest.json`;
    if (!existsSync(manifestPath)) continue;
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as {
      slots?: Array<string | { slot?: string }>;
    };
    for (const entry of manifest.slots ?? []) {
      const slot = typeof entry === "string" ? entry : entry.slot;
      if (!slot) continue;
      out.set(slot, [...(out.get(slot) ?? []), name]);
    }
  }
  return out;
}

describe("槽位契约台账（manifest 贡献 → 宿主 / 欠账）", () => {
  const contributions = readContributions();

  it("槽定义与契约文档一致（新增槽必须走 RFC，别偷偷加）", () => {
    expect(SLOT_NAMES).toHaveLength(15); // desktop.dock / widget / dashboard.card / topbar.action / calendar×2 / inspector / settings / search / ai.tool / notification / command.palette / sidecar / dock-left / dock-right
  });

  it("manifest 里的槽名都必须是已定义的槽（拼错 = 永远不显示）", () => {
    for (const slot of contributions.keys()) {
      expect(SLOT_NAMES, `未知槽名：${slot}`).toContain(slot);
    }
  });

  it("有贡献的槽：要么有宿主，要么在欠账台账里登记（含正确贡献数）", () => {
    const offenders: string[] = [];
    for (const [slot, modules] of contributions) {
      if (HOSTED[slot as SlotName]) continue;
      const debt = KNOWN_DEAD[slot as SlotName];
      if (!debt) {
        offenders.push(`${slot}（${modules.length} 份：${modules.join(",")}）未宿主且未登记`);
      } else if (debt.contributions !== modules.length) {
        offenders.push(`${slot} 台账写 ${debt.contributions} 份，实际 ${modules.length} 份`);
      }
    }
    expect(offenders, offenders.join("\n")).toEqual([]);
  });

  it("欠账台账里的槽确实仍无宿主（补了宿主就该删欠账）", () => {
    for (const slot of Object.keys(KNOWN_DEAD) as SlotName[]) {
      expect(HOSTED[slot], `${slot} 已有宿主，请从 KNOWN_DEAD 删除`).toBeUndefined();
    }
  });

  it(`贡献总数快照（${EXPECTED_TOTAL_CONTRIBUTIONS}）——manifest 变更必须同步台账`, () => {
    const total = [...contributions.values()].reduce((n, arr) => n + arr.length, 0);
    expect(total).toBe(EXPECTED_TOTAL_CONTRIBUTIONS);
  });
});
