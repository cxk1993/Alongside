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
      get: vi.fn(() => Promise.reject(new ApiError(404))),
    },
  };
});

describe("共处工作台 v1 灰盒", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("渲染三个面与在场状态条", async () => {
    render(<CollabApp />);
    expect(await screen.findByTestId("collab-status")).toBeTruthy();
    expect(screen.getByTestId("collab-root")).toBeTruthy();
    expect(screen.getByText("它看见的")).toBeTruthy();
    expect(screen.getByText("它想说的")).toBeTruthy();
    expect(screen.getByText("它做过的")).toBeTruthy();
    expect(screen.getByTestId("collab-status")).toBeTruthy();
  });

  it("接口 404 时诚实空态，不造假数据", async () => {
    render(<CollabApp />);
    expect(await screen.findByText(/视野数据未就绪/)).toBeTruthy();
    expect(await screen.findByText(/提案层未接线/)).toBeTruthy();
  });
});
