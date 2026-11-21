import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    api: { get: vi.fn(), post: vi.fn() },
  };
});

import { api, ApiError } from "@/shared/api/client";
import { ProposalDetail } from "./ProposalDetail";

const get = api.get as unknown as ReturnType<typeof vi.fn>;
const ErrCtor = ApiError as unknown as new (status: number) => Error;

/** 一条「已执行 + 已撤销」的提案全字段（对齐 services/api/modules/proposals/schema.py ProposalOut）。 */
const undoneRow = {
  id: "p9",
  title: "更新待办 #3",
  tool_name: "todo_item_update",
  action: "update",
  method: "PATCH",
  path: "/api/v1/todo/items/{id}",
  payload: { id: "t3", text: "改后的文本" },
  before: { id: "t3", text: "原文" },
  actor: "mcp:nx-ui",
  status: "undone",
  created_at: "2026-11-22T02:30:11+08:00",
  executed_at: "2026-11-22T02:31:00+08:00",
  executed_id: "t3",
  executed_status: 200,
  executed_by: "human",
  undone_at: "2026-11-22T02:33:00+08:00",
  undone_by: "human",
  undo_result: '{"id": "t3", "text": "原文"}',
};

describe("提案详情抽屉（T25 全字段）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("拿到全字段：执行目标 / 参数 / 执行证据 / 撤销回执都渲染", async () => {
    get.mockResolvedValue(undoneRow);
    render(<ProposalDetail proposalId="p9" onClose={() => {}} />);

    // 打的是单条端点，不是列表
    await waitFor(() => expect(get).toHaveBeenCalledWith("/api/v1/proposals/p9"));

    expect((await screen.findByTestId("detail-target")).textContent).toContain(
      "PATCH /api/v1/todo/items/{id}",
    );
    expect(screen.getByTestId("detail-status").textContent).toContain("已撤销");
    expect(screen.getByText(/AI（nx-ui）/)).toBeTruthy();
    expect(screen.getByTestId("detail-executed-id").textContent).toBe("t3");
    expect(screen.getByText("HTTP 200")).toBeTruthy();
    // 撤销回执原文（不是「成功」两个字）
    expect(screen.getByTestId("detail-undo-result").textContent).toContain('"text": "原文"');
    // 撤销语义照契约原文
    expect(screen.getByText(/撤销 = 用快照回写原行/)).toBeTruthy();
  });

  it("参数折叠：默认不展开 JSON，点开后正文在", async () => {
    get.mockResolvedValue(undoneRow);
    render(<ProposalDetail proposalId="p9" onClose={() => {}} />);
    const toggle = await screen.findByText(/执行参数（2 个字段）/);
    expect(screen.queryByText(/改后的文本/)).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByText(/改后的文本/)).toBeTruthy();
  });

  it("★ 缺失 ≠ 空：payload 没有字段 → 「服务端未返回」；payload={} → 「无参数」", async () => {
    get.mockResolvedValue({ ...undoneRow, payload: undefined });
    const { unmount } = render(<ProposalDetail proposalId="p8" onClose={() => {}} />);
    expect(await screen.findByText(/执行参数：服务端未返回/)).toBeTruthy();
    unmount();

    get.mockResolvedValue({ ...undoneRow, payload: {}, executed_at: null, executed_status: null });
    render(<ProposalDetail proposalId="p7" onClose={() => {}} />);
    expect(await screen.findByText(/执行参数：无参数/)).toBeTruthy();
  });

  it("★ 没有 method/path 的老行 → 如实警告「无法执行」，不渲染 405/undefined", async () => {
    get.mockResolvedValue({ ...undoneRow, method: undefined, path: undefined, status: "pending" });
    render(<ProposalDetail proposalId="p6" onClose={() => {}} />);
    const warn = await screen.findByTestId("detail-target-missing");
    expect(warn.textContent).toContain("无法执行");
    expect(screen.queryByTestId("detail-target")).toBeNull();
  });

  it("★ 未执行 → 「尚未执行」；未撤销 → 「未撤销」＋撤销语义", async () => {
    get.mockResolvedValue({
      ...undoneRow,
      status: "approved",
      executed_at: null,
      executed_status: null,
      executed_id: null,
      undone_at: null,
      undo_result: null,
    });
    render(<ProposalDetail proposalId="p5" onClose={() => {}} />);
    expect(await screen.findByTestId("detail-not-executed")).toBeTruthy();
    expect(screen.getByTestId("detail-not-undone").textContent).toContain("未撤销");
    expect(screen.getByTestId("detail-not-undone").textContent).toContain("回写原行");
  });

  it("create 提案的详情也说清「无变更前快照」（与卡片同一套规则）", async () => {
    get.mockResolvedValue({ ...undoneRow, action: "create", before: null, status: "pending" });
    render(<ProposalDetail proposalId="p4" onClose={() => {}} />);
    expect(await screen.findByText(/新建 · 无变更前快照/)).toBeTruthy();
  });

  it("★ 真栈发现：撤销回执是空串（204 无正文）≠「未记录」，要分开说", async () => {
    get.mockResolvedValue({ ...undoneRow, undo_result: "" });
    const { unmount } = render(<ProposalDetail proposalId="p10" onClose={() => {}} />);
    const empty = await screen.findByTestId("detail-undo-empty");
    expect(empty.textContent).toContain("空回执");
    expect(screen.queryByText(/未记录回执/)).toBeNull();
    unmount();

    get.mockResolvedValue({ ...undoneRow, undo_result: null });
    render(<ProposalDetail proposalId="p11" onClose={() => {}} />);
    expect(await screen.findByText(/服务端未记录回执/)).toBeTruthy();
  });

  it("404 → 明说「服务端没有这条提案」", async () => {
    get.mockRejectedValue(new ErrCtor(404));
    render(<ProposalDetail proposalId="gone" onClose={() => {}} />);
    expect((await screen.findByTestId("detail-missing")).textContent).toContain("404");
  });

  it("其它错误 → 错误行 + 重试按钮真会再发一次 GET", async () => {
    get.mockRejectedValueOnce(new Error("网络断了"));
    render(<ProposalDetail proposalId="p3" onClose={() => {}} />);
    expect((await screen.findByTestId("detail-error")).textContent).toContain("网络断了");

    get.mockResolvedValueOnce(undoneRow);
    fireEvent.click(screen.getByText("重试"));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(await screen.findByTestId("detail-target")).toBeTruthy();
  });

  it("★ T26 审计：谁签的字 / 谁撤销的 都显示；老数据（null）说「未记录」，不冒充 human", async () => {
    get.mockResolvedValue(undoneRow);
    const { unmount } = render(<ProposalDetail proposalId="p12" onClose={() => {}} />);
    expect((await screen.findByTestId("detail-executed-by")).textContent).toBe("你（human）");
    expect(screen.getByTestId("detail-undone-by").textContent).toBe("你（human）");
    unmount();

    // AI 签的字必须一眼分得开（审计字段的真实用途）
    get.mockResolvedValue({ ...undoneRow, executed_by: "mcp:nx-t26", undone_by: "mcp:nx-t26" });
    const r2 = render(<ProposalDetail proposalId="p13" onClose={() => {}} />);
    expect((await screen.findByTestId("detail-executed-by")).textContent).toBe("AI（nx-t26）");
    expect(screen.getByTestId("detail-undone-by").textContent).toBe("AI（nx-t26）");
    r2.unmount();

    // 73a0519 之前的老提案：null 是「没这个字段」，不是「human」
    get.mockResolvedValue({ ...undoneRow, executed_by: null, undone_by: null });
    render(<ProposalDetail proposalId="p14" onClose={() => {}} />);
    expect((await screen.findByTestId("detail-executed-by-missing")).textContent).toContain("未记录");
    expect(screen.getByTestId("detail-undone-by-missing").textContent).toContain("未记录");
  });

  it("关闭按钮回调 onClose", async () => {
    get.mockResolvedValue(undoneRow);
    const onClose = vi.fn();
    render(<ProposalDetail proposalId="p2" onClose={onClose} />);
    await screen.findByTestId("detail-target");
    fireEvent.click(screen.getByTestId("detail-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});