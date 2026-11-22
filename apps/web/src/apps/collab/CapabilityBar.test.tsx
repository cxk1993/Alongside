import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ModuleReg } from "../../kernel/store";
import { useDesktopStore } from "../../kernel/store";
import type { ModuleManifest } from "../../kernel/types";
import { CapabilityBar } from "./CapabilityBar";

/**
 * P0-c · 能力条渲染测试（jsdom）。
 *
 * 这里只验**行为**（点了传哪个 id、展开后条目数、筛选是否生效）。
 * 「点了真的开窗」「芯片真的可点」必须在真浏览器用 elementFromPoint 验 ——
 * jsdom 没有命中测试，宿主背景面又是 pointer-events 白名单制，测了也是假绿。
 */

function mod(id: string, name: string, extra: Partial<ModuleManifest> = {}): ModuleReg {
  const manifest: ModuleManifest = {
    id,
    name,
    kind: "builtin",
    entry: `@/apps/${id}`,
    icon: "grid",
    window: { w: 640, h: 420 },
    ...extra,
  } as ModuleManifest;
  return { manifest, enabled: true };
}

function seed(list: ModuleReg[]) {
  useDesktopStore.setState({ modules: Object.fromEntries(list.map((r) => [r.manifest.id, r])) });
}

afterEach(() => {
  useDesktopStore.setState({ modules: {} });
});

describe("P0-c · CapabilityBar", () => {
  it("零模块时零渲染（不留空条、不占位）", () => {
    useDesktopStore.setState({ modules: {} });
    const { container } = render(<CapabilityBar onOpen={() => {}} />);
    expect(container.innerHTML).toBe("");
  });

  it("显示真数与前 8 个芯片", () => {
    seed(Array.from({ length: 12 }, (_, i) => mod(`m${i}`, `模块${i}`)));
    render(<CapabilityBar onOpen={() => {}} />);
    expect(screen.getByTestId("collab-caps")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.getAllByTestId(/^cap-open-/).length).toBe(8);
  });

  it("点芯片把模块 id 交给 openWindow（不是名字）", () => {
    seed([mod("calendar", "日历", { provides: ["calendar.event.read"] })]);
    const onOpen = vi.fn();
    render(<CapabilityBar onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId("cap-open-calendar"));
    expect(onOpen).toHaveBeenCalledWith("calendar");
  });

  it("「全部」展开成全量网格，筛选实时收窄", () => {
    seed([mod("calendar", "日历"), mod("todo", "待办", { provides: ["todo.item.write"] })]);
    render(<CapabilityBar onOpen={() => {}} />);
    fireEvent.click(screen.getByTestId("cap-all"));
    expect(screen.getByTestId("cap-grid")).toBeTruthy();
    expect(screen.getAllByTestId(/^cap-open-/).length).toBe(2);

    fireEvent.change(screen.getByTestId("cap-filter"), { target: { value: "待办" } });
    expect(screen.getAllByTestId(/^cap-open-/).length).toBe(1);
    expect(screen.getByTestId("cap-open-todo")).toBeTruthy();
  });

  it("筛选无命中时给出空态（而不是空白格）", () => {
    seed([mod("todo", "待办")]);
    render(<CapabilityBar onOpen={() => {}} />);
    fireEvent.click(screen.getByTestId("cap-all"));
    fireEvent.change(screen.getByTestId("cap-filter"), { target: { value: "zzz" } });
    expect(screen.getByTestId("cap-none")).toBeTruthy();
  });

  it("Esc 收起筛选并回到常驻态", () => {
    seed([mod("todo", "待办"), mod("calendar", "日历")]);
    render(<CapabilityBar onOpen={() => {}} />);
    fireEvent.click(screen.getByTestId("cap-all"));
    const input = screen.getByTestId("cap-filter");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByTestId("cap-grid")).toBeNull();
  });

  it("停用模块不出现在芯片里", () => {
    seed([mod("todo", "待办"), { ...mod("off", "停用"), enabled: false }]);
    render(<CapabilityBar onOpen={() => {}} />);
    expect(screen.queryByTestId("cap-open-off")).toBeNull();
    expect(screen.getByTestId("cap-open-todo")).toBeTruthy();
  });
});