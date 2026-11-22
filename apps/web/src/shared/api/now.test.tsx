/**
 * P0 回归钉子：`/sight/now` 只能有一个口径。
 *
 * 为什么钉：
 *   - 顶栏与 collab 共享同一个模块级缓存 —— 两个消费者只许发一次请求；
 *   - 404 与 500 必须分开（「还没做」vs「坏了」）；
 *   - refresh() 必须真重拉（SSE/手动刷新靠它）。
 * 这三条任何一条破了，界面就会出现两处采样时刻不一致或把故障说成未接入。
 */
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/shared/api/client", () => {
  class ApiError extends Error {
    status: number;
    constructor(status: number) {
      super("err");
      this.status = status;
    }
  }
  return { ApiError, api: { get: vi.fn() } };
});

import { api, ApiError } from "@/shared/api/client";
import { __resetSightNowForTests, useSightNow } from "./now";

const get = api.get as unknown as ReturnType<typeof vi.fn>;
const ErrCtor = ApiError as unknown as new (status: number) => Error;

const brief = {
  head: { now_local: "2026-11-22T13:00:00+08:00", part_of_day: "下午" },
  tensions: [{ id: "t1", summary: "两件事撞了" }],
};

describe("shared/api/now · 单一「此刻」", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetSightNowForTests();
  });
  afterEach(() => {
    __resetSightNowForTests();
  });

  it("两个消费者共享一次请求与同一份数据（顶栏与 collab 不许各拉一次）", async () => {
    get.mockResolvedValue(brief);
    const a = renderHook(() => useSightNow());
    const b = renderHook(() => useSightNow());
    await waitFor(() => expect(a.result.current.status).toBe("ready"));
    await waitFor(() => expect(b.result.current.status).toBe("ready"));
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith("/api/v1/sight/now");
    expect(a.result.current.data).toBe(b.result.current.data);
  });

  it("404 = missing，500 = error —— 界面不能把两者说成同一句", async () => {
    get.mockRejectedValueOnce(Object.assign(new ErrCtor(404), {}));
    const a = renderHook(() => useSightNow());
    await waitFor(() => expect(a.result.current.status).toBe("missing"));

    __resetSightNowForTests();
    get.mockRejectedValueOnce(new ErrCtor(500));
    const b = renderHook(() => useSightNow());
    await waitFor(() => expect(b.result.current.status).toBe("error"));
  });

  it("refresh() 真重拉（不是改个状态就算）", async () => {
    get.mockResolvedValue(brief);
    const a = renderHook(() => useSightNow());
    await waitFor(() => expect(a.result.current.status).toBe("ready"));
    expect(get).toHaveBeenCalledTimes(1);
    await a.result.current.refresh();
    expect(get).toHaveBeenCalledTimes(2);
    expect(a.result.current.status).toBe("ready");
  });
});
