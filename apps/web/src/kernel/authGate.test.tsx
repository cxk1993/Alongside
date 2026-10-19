/**
 * T30 · ★ 登录窗 + token 引导的流程测试。
 *
 * 覆盖（对齐卡验收清单）：
 *   1. 无 token → 只见登录页；有 token → 直入桌面
 *   2. 登录成功 → 进桌面（token 落 localStorage）
 *   3. 错误密码 → 人话报错（不是内核崩溃）
 *   4. 业务请求 401 → refresh 单飞 → 成功重放
 *   5. refresh 失败 → token 清空 + 鉴权门切回登录页
 *   6. 登出 → 回登录页
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "./AuthGate";
import { api, hasToken, setToken, setUnauthorizedHandler } from "@/shared/api/client";
import { logout } from "@/shared/api/auth";

// ───────────────────────── fetch mock 工具 ─────────────────────────

type Res = { status: number; body: unknown; contentType?: string };

function jsonResponse(res: Res): globalThis.Response {
  return new Response(JSON.stringify(res.body), {
    status: res.status,
    headers: { "content-type": res.contentType ?? "application/json" },
  });
}

/** 按调用顺序回放响应；calls 记录每次 fetch 的 (url, method)。 */
function stubFetchSequence(seq: Res[]): { calls: Array<{ url: string; method: string }> } {
  const calls: Array<{ url: string; method: string }> = [];
  const fn = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({
      url: typeof input === "string" ? input : String(input),
      method: init?.method ?? "GET",
    });
    const next = seq.shift();
    if (!next) throw new Error("fetch mock: 响应序列耗尽");
    return Promise.resolve(jsonResponse(next));
  });
  vi.stubGlobal("fetch", fn);
  return { calls };
}

beforeEach(() => {
  localStorage.clear();
  setUnauthorizedHandler(() => {}); // 每个用例自己注册
});

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(() => {});
});

// ───────────────────────── 鉴权门 ─────────────────────────

describe("★ T30 AuthGate：无 token 只见登录页 / 有 token 直入桌面", () => {
  it("无 token → 渲染登录页，children 不渲染", () => {
    render(
      <AuthGate>
        <div data-testid="desktop">桌面</div>
      </AuthGate>,
    );
    expect(screen.getByLabelText("登录 Alongside")).toBeTruthy();
    expect(screen.queryByTestId("desktop")).toBeNull();
  });

  it("有 token → 直接渲染桌面", () => {
    setToken("pre-seeded");
    render(
      <AuthGate>
        <div data-testid="desktop">桌面</div>
      </AuthGate>,
    );
    expect(screen.getByTestId("desktop")).toBeTruthy();
    expect(screen.queryByLabelText("登录 Alongside")).toBeNull();
  });
});

// ───────────────────────── 登录成功 / 失败 ─────────────────────────

describe("★ T30 登录页：成功进桌面 / 失败人话报错", () => {
  it("登录成功 → token 落地、进入桌面", async () => {
    stubFetchSequence([
      {
        status: 200,
        body: {
          access_token: "jwt-access-1",
          token_type: "Bearer",
          expires_in: 900,
          user: { sub: "admin" },
        },
      },
    ]);

    render(
      <AuthGate>
        <div data-testid="desktop">桌面</div>
      </AuthGate>,
    );
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "test-password" } });
    fireEvent.click(screen.getByText("进入"));

    await waitFor(() => expect(screen.getByTestId("desktop")).toBeTruthy());
    expect(hasToken()).toBe(true);
  });

  it("错误密码 → 人话报错（不白屏、不崩内核边界）", async () => {
    stubFetchSequence([
      {
        status: 401,
        contentType: "application/problem+json",
        body: {
          type: "about:blank",
          title: "Unauthorized",
          status: 401,
          detail: "密码错误",
        },
      },
    ]);

    render(
      <AuthGate>
        <div data-testid="desktop">桌面</div>
      </AuthGate>,
    );
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByText("进入"));

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByRole("alert").textContent).toContain("密码错误");
    // 仍在登录页
    expect(screen.getByLabelText("登录 Alongside")).toBeTruthy();
  });
});

// ───────────────────────── 401 → refresh → 重放 / 回登录页 ─────────────────────────

describe("★ T30 401 拦截：refresh 单飞重放 / 失败回登录页", () => {
  it("业务请求 401 → refresh 成功 → 原请求重放成功", async () => {
    const { calls } = stubFetchSequence([
      {
        status: 401,
        contentType: "application/problem+json",
        body: { title: "Unauthorized", status: 401 },
      },
      {
        status: 200,
        body: {
          access_token: "jwt-access-2",
          token_type: "Bearer",
          expires_in: 900,
          user: { sub: "admin" },
        },
      },
      { status: 200, body: [{ id: "n1" }] },
    ]);

    setToken("expired-token");
    const data = await api.get<Array<{ id: string }>>("/api/v1/todo/items");
    expect(data).toEqual([{ id: "n1" }]);
    // 调用序列：原请求 → refresh → 重放
    expect(calls.map((c) => c.url)).toEqual([
      "/api/v1/todo/items",
      "/api/v1/auth/refresh",
      "/api/v1/todo/items",
    ]);
    expect(hasToken()).toBe(true); // 新 access 已落地
  });

  it("refresh 失败 → token 清空 + 鉴权门切回登录页", async () => {
    stubFetchSequence([
      {
        status: 401,
        contentType: "application/problem+json",
        body: { title: "Unauthorized", status: 401 },
      },
      {
        status: 401,
        contentType: "application/problem+json",
        body: { title: "Unauthorized", status: 401 },
      },
    ]);

    setToken("expired-token");
    render(
      <AuthGate>
        <div data-testid="desktop">桌面</div>
      </AuthGate>,
    );
    expect(screen.getByTestId("desktop")).toBeTruthy(); // 先在桌面

    let rejected = false;
    api.get("/api/v1/todo/items").catch(() => {
      rejected = true; // 401 最终仍抛给调用方
    });
    await waitFor(() => expect(rejected).toBe(true));
    await waitFor(() => expect(screen.getByLabelText("登录 Alongside")).toBeTruthy());
    expect(hasToken()).toBe(false); // token 已清
  });
});

// ───────────────────────── 登出 ─────────────────────────

describe("★ T30 登出：清 token + 回登录页", () => {
  it("logout() → 后端调用一次、本地 token 清空、鉴权门切回登录页", async () => {
    const { calls } = stubFetchSequence([{ status: 200, body: { ok: true } }]);
    setToken("jwt-access-1");

    render(
      <AuthGate>
        <div data-testid="desktop">桌面</div>
      </AuthGate>,
    );
    expect(screen.getByTestId("desktop")).toBeTruthy();

    await act(async () => {
      await logout();
    });

    await waitFor(() => expect(screen.getByLabelText("登录 Alongside")).toBeTruthy());
    expect(hasToken()).toBe(false);
    expect(calls).toEqual([{ url: "/api/v1/auth/logout", method: "POST" }]);
  });

  it("后端不可达时 logout 也把本地退干净（不崩）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("network down"))),
    );
    setToken("jwt-access-1");

    render(
      <AuthGate>
        <div data-testid="desktop">桌面</div>
      </AuthGate>,
    );
    await act(async () => {
      await logout();
    });

    await waitFor(() => expect(screen.getByLabelText("登录 Alongside")).toBeTruthy());
    expect(hasToken()).toBe(false);
  });
});
