import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
import { __resetSightNowForTests } from "@/shared/api/now";
const get = api.get as unknown as ReturnType<typeof vi.fn>;
const post = api.post as unknown as ReturnType<typeof vi.fn>;

/** 404 响应替身：用 mock 的 ApiError 类构造（组件内 instanceof 判定 404→missing）。 */
const ErrCtor = ApiError as unknown as new (status: number) => Error;

/** ★ T27：已签区默认收起 —— 需要验撤销/已签明细的用例先展开（计数与开关始终可见）。 */
async function expandApproved() {
  fireEvent.click(await screen.findByTestId("approved-toggle"));
}
function err404(): Promise<never> {
  return Promise.reject(new ErrCtor(404));
}

describe("共处工作台 v1.1（T10 接真端点）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // ★ P0：/sight/now 是模块级共享缓存（顶栏与 collab 同一口径）——
    //   用例之间必须清掉，否则上一条的 ready 会被下一条当成自己的数据。
    __resetSightNowForTests();
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
      if (path.includes("status=undone")) return Promise.resolve([]);
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
    await expandApproved();
    expect(screen.getByText("撤销")).toBeTruthy();
  });

  it("★ T20：delete 提案带 before → 折叠快照可展开，正文在", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("status=approved")) return Promise.resolve([]);
      if (path.includes("status=undone")) return Promise.resolve([]);
      if (path.includes("/proposals")) {
        return Promise.resolve([
          {
            id: "p2",
            title: "删除待办 #2",
            action: "delete",
            actor: "mcp:pat",
            before: { id: "t2", text: "要删的那条" },
          },
        ]);
      }
      return err404();
    });
    render(<CollabApp />);
    expect(await screen.findByText("变更前快照")).toBeTruthy();
    expect(screen.getByText(/要删的那条/)).toBeTruthy();
  });

  it("★ T20：before 缺失 → 如实说「服务端未提供」，绝不渲染空对象", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("status=approved")) return Promise.resolve([]);
      if (path.includes("status=undone")) return Promise.resolve([]);
      if (path.includes("/proposals")) {
        return Promise.resolve([
          { id: "p4", title: "改个标题", action: "update", actor: "mcp:pat", before: null },
        ]);
      }
      return err404();
    });
    render(<CollabApp />);
    expect(await screen.findByText("变更前快照：服务端未提供")).toBeTruthy();
    expect(screen.queryByText("{}")).toBeNull();
  });

  it("★ T20：action=create → 明说「无变更前快照」", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("status=approved")) return Promise.resolve([]);
      if (path.includes("status=undone")) return Promise.resolve([]);
      if (path.includes("/proposals")) {
        return Promise.resolve([
          { id: "p5", title: "新建待办", action: "create", actor: "human", before: null },
        ]);
      }
      return err404();
    });
    render(<CollabApp />);
    expect(await screen.findByText(/新建 · 无变更前快照/)).toBeTruthy();
  });

  it("★ T20 修正：create 的已签提案**可撤销**（契约 8ccef3b）——按钮文案说清删的是什么", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("status=pending")) return Promise.resolve([]);
      if (path.includes("status=undone")) return Promise.resolve([]);
      if (path.includes("/proposals")) {
        return Promise.resolve([
          { id: "p3", title: "新建待办", action: "create", actor: "human", before: null },
        ]);
      }
      return err404();
    });
    render(<CollabApp />);
    await expandApproved();
    const btn = (await screen.findByTestId("undo-btn")) as HTMLButtonElement;
    // 原契约「create 不可撤销 409」已被实现推翻 → 按钮必须可点，但文案要说清撤掉什么
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toContain("删掉刚建的那条");
    expect(screen.getByText(/撤销 = 删除刚建的那条/)).toBeTruthy();
    fireEvent.click(btn);
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/v1/proposals/p3/undo", {}));
  });

  it("★ T20：delete 的撤销文案说明「会得到新 id，不是同一行」（契约 §撤销语义）", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("status=pending")) return Promise.resolve([]);
      if (path.includes("status=undone")) return Promise.resolve([]);
      if (path.includes("/proposals")) {
        return Promise.resolve([
          {
            id: "p6",
            title: "删除待办",
            action: "delete",
            actor: "mcp:pat",
            before: { id: "t9", text: "x" },
          },
        ]);
      }
      return err404();
    });
    render(<CollabApp />);
    await expandApproved();
    expect(await screen.findByText(/撤销 = 用快照重建（会得到新 id，不是同一行）/)).toBeTruthy();
  });

  it("★ T20：actor 读得懂（AI 提案 vs 人自己签发一眼分开）", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("status=approved")) return Promise.resolve([]);
      if (path.includes("status=undone")) return Promise.resolve([]);
      if (path.includes("/proposals")) {
        return Promise.resolve([
          { id: "a1", title: "AI 的提案", action: "delete", actor: "mcp:nx-t10-ui" },
          { id: "a2", title: "人的提案", action: "delete", actor: "human" },
        ]);
      }
      return err404();
    });
    render(<CollabApp />);
    expect(await screen.findByText(/AI（nx-t10-ui）/)).toBeTruthy();
    expect(screen.getByText(/你签发/)).toBeTruthy();
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

  it("★ T22：它做过的 = 已执行 + 已撤销，按时间倒序（撤销在 UI 上留痕）", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("status=pending")) return Promise.resolve([]);
      if (path.includes("status=undone")) {
        return Promise.resolve([
          {
            id: "u1",
            title: "删除待办 #7",
            action: "delete",
            actor: "mcp:pat",
            status: "undone",
            executed_at: "2026-11-22T02:30:00+08:00",
            undone_at: "2026-11-22T02:42:00+08:00",
          },
        ]);
      }
      if (path.includes("/proposals")) {
        return Promise.resolve([
          {
            id: "a9",
            title: "更新日程 #3",
            action: "update",
            actor: "mcp:pat",
            status: "approved",
            executed_at: "2026-11-22T02:35:00+08:00",
          },
        ]);
      }
      return err404();
    });
    render(<CollabApp />);
    const list = await screen.findByTestId("collab-timeline");
    const text = list.textContent ?? "";
    expect(text).toContain("已撤销");
    expect(text).toContain("02:42");
    expect(text).toContain("删除待办 #7");
    expect(text).toContain("已执行");
    expect(text).toContain("更新日程 #3");
    // 倒序：撤销（02:42）在已执行（02:35）上面
    expect(text.indexOf("已撤销")).toBeLessThan(text.indexOf("已执行"));
  });

  it("★ T22：没有任何历史 → 诚实空态（不是空白框）", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("/proposals")) return Promise.resolve([]);
      return err404();
    });
    render(<CollabApp />);
    expect(await screen.findByText("还没有签过、撤过或否过的东西")).toBeTruthy();
    expect(screen.queryByTestId("collab-timeline")).toBeNull();
  });

  it("★ T25：待签卡「详情」→ 抽屉显示执行目标（打到哪个端点）", async () => {
    get.mockImplementation((path: string) => {
      if (path === "/api/v1/proposals/p1") {
        return Promise.resolve({
          id: "p1",
          title: "删除待办 #1",
          tool_name: "todo_item_delete",
          action: "delete",
          method: "DELETE",
          path: "/api/v1/todo/items/{id}",
          payload: { id: "t1" },
          before: { id: "t1", text: "x" },
          actor: "mcp:pat",
          status: "pending",
          created_at: "2026-11-22T03:00:00+08:00",
        });
      }
      if (
        path.includes("status=approved") ||
        path.includes("status=undone") ||
        path.includes("status=rejected")
      )
        return Promise.resolve([]);
      if (path.includes("/proposals")) {
        return Promise.resolve([
          { id: "p1", title: "删除待办 #1", action: "delete", status: "pending", actor: "mcp:pat" },
        ]);
      }
      return err404();
    });
    render(<CollabApp />);
    const btn = await screen.findByTestId("detail-btn");
    fireEvent.click(btn);
    const d = await screen.findByTestId("detail-target");
    expect(d.textContent).toContain("DELETE /api/v1/todo/items/{id}");
    expect(get).toHaveBeenCalledWith("/api/v1/proposals/p1");
    // 关闭后抽屉消失
    fireEvent.click(screen.getByTestId("detail-close"));
    expect(screen.queryByTestId("proposal-detail")).toBeNull();
  });

  it("★ T25：抽屉开着时签字 → 列表刷新会把抽屉一起刷新（不许一屏两块矛盾证据）", async () => {
    const pendingRow = {
      id: "p1",
      title: "删除待办 #1",
      action: "delete",
      method: "DELETE",
      path: "/api/v1/todo/items/{id}",
      status: "pending",
      actor: "mcp:pat",
    };
    let detailCalls = 0;
    get.mockImplementation((path: string) => {
      if (path === "/api/v1/proposals/p1") {
        detailCalls += 1;
        // 第一次读 = 还没签字；之后 = 已执行
        return Promise.resolve(
          detailCalls === 1
            ? pendingRow
            : { ...pendingRow, status: "approved", executed_at: "2026-11-22T03:40:00+08:00", executed_status: 204 },
        );
      }
      if (path.includes("status=pending")) return Promise.resolve([pendingRow]);
      if (
        path.includes("status=approved") ||
        path.includes("status=undone") ||
        path.includes("status=rejected")
      )
        return Promise.resolve([]);
      return err404();
    });
    render(<CollabApp />);
    fireEvent.click(await screen.findByTestId("detail-btn"));
    expect(await screen.findByTestId("detail-not-executed")).toBeTruthy();
    // 签字（post 已 mock 成功）→ reload → version+1 → 抽屉重取
    fireEvent.click(screen.getByText("签字"));
    await waitFor(() => expect(detailCalls).toBe(2));
    expect(await screen.findByText("HTTP 204")).toBeTruthy();
    expect(screen.queryByTestId("detail-not-executed")).toBeNull();
  });

  it("★ T25：时间线「详情」→ 打开的是那一行的提案（不是最后一行的）", async () => {
    get.mockImplementation((path: string) => {
      if (path === "/api/v1/proposals/u1") {
        return Promise.resolve({
          id: "u1",
          title: "删除待办 #7",
          action: "delete",
          method: "DELETE",
          path: "/api/v1/todo/items/{id}",
          status: "undone",
          executed_at: "2026-11-22T02:30:00+08:00",
          executed_status: 204,
          undone_at: "2026-11-22T02:42:00+08:00",
        });
      }
      if (path.includes("status=pending")) return Promise.resolve([]);
      if (path.includes("status=undone")) {
        return Promise.resolve([
          {
            id: "u1",
            title: "删除待办 #7",
            action: "delete",
            status: "undone",
            executed_at: "2026-11-22T02:30:00+08:00",
            undone_at: "2026-11-22T02:42:00+08:00",
          },
        ]);
      }
      if (path.includes("/proposals")) return Promise.resolve([]);
      return err404();
    });
    render(<CollabApp />);
    const list = await screen.findByTestId("collab-timeline");
    const open = list.querySelector('[data-testid="detail-btn"]') as HTMLButtonElement;
    fireEvent.click(open);
    expect((await screen.findByTestId("detail-status")).textContent).toContain("已撤销");
    expect(get).toHaveBeenCalledWith("/api/v1/proposals/u1");
  });

  it("★ 撤销失败（422，例如 executed_id 已悬空）→ 错误行如实回显，提案仍在已签组", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("status=pending")) return Promise.resolve([]);
      if (path.includes("status=undone")) return Promise.resolve([]);
      if (path.includes("/proposals")) {
        return Promise.resolve([
          { id: "p7", title: "新建待办", action: "create", actor: "human", executed_id: "gone" },
        ]);
      }
      return err404();
    });
    post.mockImplementationOnce(() => Promise.reject(new ErrCtor(422)));
    render(<CollabApp />);
    await expandApproved();
    const btn = (await screen.findByTestId("undo-btn")) as HTMLButtonElement;
    fireEvent.click(btn);
    // 服务端拒了 → 界面必须说失败，且**不许**把这条从已签组里抹掉（那会是"假成功"）
    expect(await screen.findByTestId("proposal-error")).toBeTruthy();
    expect(screen.getByTestId("proposal-row-approved")).toBeTruthy();
  });

  it("★ T27：已签区默认收起——计数与开关常显，明细要展开才出现（收起的是展示，不是数据）", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("status=pending")) return Promise.resolve([]);
      if (path.includes("status=undone")) return Promise.resolve([]);
      if (path.includes("/proposals")) {
        return Promise.resolve([
          { id: "a1", title: "AI 想清掉过期待办", action: "delete", actor: "mcp:nx", status: "approved" },
          { id: "a2", title: "AI 想沉淀经验卡", action: "create", actor: "mcp:nx", status: "approved" },
        ]);
      }
      return err404();
    });
    render(<CollabApp />);
    const toggle = await screen.findByTestId("approved-toggle");
    expect(toggle.textContent).toContain("展开 2 条");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // 收起时：明细不在 DOM，但「有几条」和「在哪展开」都写着
    expect(screen.queryByTestId("proposal-row-approved")).toBeNull();
    expect(screen.getByTestId("approved-collapsed")).toBeTruthy();
    fireEvent.click(toggle);
    expect((await screen.findAllByTestId("proposal-row-approved")).length).toBe(2);
    expect(screen.queryByTestId("approved-collapsed")).toBeNull();
    expect(screen.getByTestId("approved-toggle").textContent).toBe("收起");
  });

  it("★ T27：签字成功后已签区自动展开（刚签的卡片不能像凭空消失）", async () => {
    let approvedCalls = 0;
    get.mockImplementation((path: string) => {
      if (path.includes("status=pending")) {
        return Promise.resolve(
          approvedCalls === 0
            ? [{ id: "p1", title: "删除待办", action: "delete", actor: "mcp:nx", status: "pending" }]
            : [],
        );
      }
      if (path.includes("status=undone")) return Promise.resolve([]);
      // ★ T29：驳回列表是第四条拉取——不 stub 就会 404，整个面板会判「未就绪」
      if (path.includes("status=rejected")) return Promise.resolve([]);
      if (path.includes("status=approved")) {
        approvedCalls += 1;
        return Promise.resolve(
          approvedCalls > 1
            ? [{ id: "p1", title: "删除待办", action: "delete", actor: "mcp:nx", status: "approved" }]
            : [],
        );
      }
      return err404();
    });
    render(<CollabApp />);
    const row = await screen.findByTestId("proposal-row");
    const approve = [...row.querySelectorAll("button")].find((b) => b.textContent === "签字");
    expect(approve).toBeTruthy();
    fireEvent.click(approve as HTMLButtonElement);
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/v1/proposals/p1/approve", {}));
    expect(await screen.findByTestId("proposal-row-approved")).toBeTruthy();
  });

  it("★ T27：Esc 关抽屉，焦点回到打开它的「详情」按钮", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("/proposals/")) {
        return Promise.resolve({
          id: "p1",
          title: "删除待办",
          action: "delete",
          status: "pending",
          method: "DELETE",
          path: "/api/v1/todo/items/{id}",
        });
      }
      if (path.includes("status=pending")) {
        return Promise.resolve([{ id: "p1", title: "删除待办", action: "delete", actor: "mcp:nx", status: "pending" }]);
      }
      if (path.includes("status=undone")) return Promise.resolve([]);
      if (path.includes("/proposals")) return Promise.resolve([]);
      return err404();
    });
    render(<CollabApp />);
    const open = (await screen.findByTestId("detail-btn")) as HTMLButtonElement;
    open.focus();
    fireEvent.click(open);
    expect(await screen.findByTestId("proposal-detail")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("proposal-detail")).toBeNull());
    expect(document.activeElement).toBe(open);
  });
  it("★ T28：视野的采样时刻上屏（它是每次现算的，不是常量）", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("/sight/now")) {
        return Promise.resolve({
          head: { now_local: "2026-11-22T04:26:05+08:00", part_of_day: "late_night" },
          tensions: [],
          attention: [],
          degraded: [],
        });
      }
      return err404();
    });
    render(<CollabApp />);
    const at = await screen.findByTestId("sight-sampled-at");
    expect(at.textContent).toContain("04:26");
    expect(at.textContent).toContain("采样");
  });

  it("★ T28：张力带建议动作就上屏；没给就不编一行", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("/sight/now")) {
        return Promise.resolve({
          head: { now_local: "2026-11-22T04:26:05+08:00", part_of_day: "late_night" },
          tensions: [
            {
              id: "t1",
              kind: "schedule_conflict",
              summary: "两节课重叠约 30 分钟",
              severity: "high",
              suggested_action: "把其中一件挪开，或确认是否可以不参加",
            },
            { id: "t2", kind: "stale_item", summary: "这笔账挂了 11 天", severity: "warn" },
          ],
          attention: [],
          degraded: [],
        });
      }
      return err404();
    });
    render(<CollabApp />);
    expect(await screen.findByText(/把其中一件挪开/)).toBeTruthy();
    // 第二条没有 suggested_action → 不该出现任何编出来的建议
    expect(screen.queryByText(/（无建议）/)).toBeNull();
  });

  it("★ T28：待签卡显示「提出于 HH:MM」——什么时候提的很关键，别让人以为刚刚", async () => {
    get.mockImplementation((path: string) => {
      if (path.includes("status=pending")) {
        return Promise.resolve([
          {
            id: "p1",
            title: "删除待办",
            action: "delete",
            actor: "mcp:nx",
            status: "pending",
            created_at: "2026-11-22T04:05:04.746731+08:00",
          },
        ]);
      }
      if (path.includes("status=undone")) return Promise.resolve([]);
      if (path.includes("/proposals")) return Promise.resolve([]);
      return err404();
    });
    render(<CollabApp />);
    expect(await screen.findByText(/提出于 04:05/)).toBeTruthy();
  });

  it("★ T29：驳回后不消失——时间线留「已驳回 · 提出于」，详情说「不会执行」", async () => {
    const rejectedRow = {
      id: "r1",
      title: "删除待办 #9",
      action: "delete",
      actor: "mcp:nx",
      status: "rejected",
      created_at: "2026-11-22T04:05:04.746731+08:00",
    };
    get.mockImplementation((path: string) => {
      if (path === "/api/v1/proposals/r1") {
        return Promise.resolve({ ...rejectedRow, method: "DELETE", path: "/api/v1/todo/items/{id}" });
      }
      if (path.includes("status=pending")) return Promise.resolve([]);
      if (path.includes("status=approved") || path.includes("status=undone")) return Promise.resolve([]);
      if (path.includes("status=rejected")) return Promise.resolve([rejectedRow]);
      return err404();
    });
    render(<CollabApp />);
    const list = await screen.findByTestId("collab-timeline");
    // 否掉的东西只出现在「它做过的」，不能再混进待签区
    expect(screen.queryAllByTestId("proposal-row")).toHaveLength(0);
    expect(list.textContent).toContain("已驳回");
    expect(list.textContent).toContain("提出于 04:05");
    fireEvent.click(list.querySelector('[data-testid="detail-btn"]') as HTMLButtonElement);
    expect((await screen.findByTestId("detail-status")).textContent).toContain("已作废");
    expect((await screen.findByTestId("detail-not-executed")).textContent).toContain("不会执行");
    // 服务端没记驳回时间/驳回人 —— 界面必须说「未记录」，不能让空白冒充「系统记得」
    expect(screen.getByTestId("detail-reject-unaudited").textContent).toContain("未记录");
  });
});
