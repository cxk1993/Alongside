import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import CollabApp from "./CollabApp";

vi.mock("@/shared/api/client", () => {
  class ApiError extends Error {
    status: number;
    constructor(status: number) {
      super("err");
      this.status = status;
    }
  }
  return {
    ApiError,
    api: {
      get: vi.fn(),
      post: vi.fn(() => Promise.resolve({ id: "p1", status: "approved", detail: "ok" })),
    },
  };
});

import { api, ApiError } from "@/shared/api/client";
const get = api.get as unknown as ReturnType<typeof vi.fn>;

/** 404 响应替身：用 mock 的 ApiError 类构造（组件内 instanceof 判定 404→missing）。 */
const ErrCtor = ApiError as unknown as new (status: number) => Error;
function err404(): Promise<never> {
  return Promise.reject(new ErrCtor(404));
}

describe("共处工作台 v1.1（T10 接真端点）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    get.mockImplementation(() => err404());
  });

  it("渲染三个面与在场状态条", async () => {
    render(<CollabApp />);
    expect(await screen.findByTestId("collab-status")).toBeTruthy();
    expect(screen.getByTestId("collab-root")).toBeTruthy();
    expect(screen.getByText("它看见的")).toBeTruthy();
    expect(screen.getByText("它想说的")).toBeTruthy();
    expect(screen.getByText("它做过的")).toBeTruthy();
  });

  it("接口 404 时诚实空态，不造假数据", async () => {
    render(<CollabApp />);
    expect(await screen.findByText(/视野数据未就绪/)).toBeTruthy();
    expect(await screen.findByText(/提案层未接线/)).toBeTruthy();
  });

  it("拿到真提案时渲染三按钮（签字/驳回/撤销）", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("/proposals")) {
        return Promise.resolve([
          { id: "p1", title: "删除待办 #1", action: "delete", status: "pending", actor: "mcp:pat" },
        ]);
      }
      return err404();
    });
    render(<CollabApp />);
    expect(await screen.findByTestId("proposal-row")).toBeTruthy();
    expect(screen.getByText("签字")).toBeTruthy();
    expect(screen.getByText("驳回")).toBeTruthy();
    expect(screen.getByText("撤销")).toBeTruthy();
  });

  it("sight 就绪时显示 tensions 与降级说明", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("/sight/now")) {
        return Promise.resolve({
          head: { part_of_day: "late_night" },
          tensions: [{ id: "t1", kind: "stale_item", summary: "这笔账挂了 11 天", severity: "warn" }],
          attention: [{ kind: "todo_overdue", title: "交材料" }],
          degraded: [{ source: "calendar", reason: "timeout" }],
        });
      }
      return err404();
    });
    render(<CollabApp />);
    expect(await screen.findByText(/这笔账挂了 11 天/)).toBeTruthy();
    expect(await screen.findByText(/交材料/)).toBeTruthy();
    expect(await screen.findByText(/calendar\(timeout\)/)).toBeTruthy();
  });
});
