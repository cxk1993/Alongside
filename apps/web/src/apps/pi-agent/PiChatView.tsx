import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/shared/api/client";
import { chatStream, piAgentApi } from "./api";
import type { AgentStatus, SessionInfo } from "./api";

/**
 * ★ Pi 智能体**对话页**。
 *
 * ★ 2026-09-26（主人「AI 编排也收，并进 pi-agent 当一个 tab」）：
 *   本组件由 `PiAgentApp` 更名为 `PiChatView` —— **对话逻辑一字未改**，
 *   只是被新的容器 `PiAgentApp`（MultitabFrame 两页：对话 / 编排）承载。
 *   改法照 `apps/schedule/ScheduleApp.tsx` 容器样板（原 App 不动，仅被 lazy 引用）。
 *
 * 与内置 ai-chat 的区别（也是 pi 的价值所在）：
 *   1. **流式**（SSE 逐字）；
 *   2. **会话可切换/锁定**（一个 session = 一个 pi 子进程，互不干扰）；
 *   3. **降级可见**（L1 可用 / L2 启动中 / L3 熔断，且给「重试」）。
 *
 * 契约：见 ./api.ts（后端 plugins/pi-agent/api/router.py）。
 */
interface Msg {
  role: "user" | "assistant";
  text: string;
  error?: boolean;
}

const LEVEL_TEXT: Record<string, string> = {
  // ★ 2026-09-28（方案 A · 主人候办③「状态点恒显启动中」）：
  //   新增 L0「待命」—— pi 是**懒启动**（首次发消息才起进程），
  //   "从未启动"是**正常态**，此前被后端错报成 L2「启动中」，主人以为坏了。
  L0: "待命",
  L1: "可用",
  L2: "启动失败",
  L3: "已熔断",
  // ★ 2026-09-27 修（主人候办③「状态点恒显启动中」）：
  //   此前 `level = status?.level ?? "L2"` —— **status 为 null（首次未拉到 / 请求失败）
  //   会稳定落回 L2「启动中」**，造成"永远启动中"的假象（后端挂了也不变）。
  //   现在把"没拉到"与"真的在启动"分开：连接中 / 状态未知。
  CONN: "连接中…",
  ERR: "状态未知",
};

export default function PiChatView() {
  const [sessionId, setSessionId] = useState("default");
  const [messages, setMessages] = useState<Msg[]>([
    {
      role: "assistant",
      text: "你好，我是 Pi 智能体。我能直接查 Alongside 的日程、待办、健康、理财等数据，也能做多步任务。",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<AgentStatus | null>(null);
  // ★ 09-27：状态拉取失败标记（fail-loud）—— 不再静默落回 L2 冒充"启动中"
  const [statusErr, setStatusErr] = useState(false);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await piAgentApi.status());
      setStatusErr(false); // ★ 拉到即清错
    } catch {
      setStatus(null);
      setStatusErr(true); // ★ 失败可见（此前静默 → 与 L2 混淆）
    }
    try {
      const d = await piAgentApi.listSessions();
      setSessions(d.sessions ?? []);
    } catch {
      setSessions([]);
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
    const t = setInterval(() => void refreshStatus(), 10000);
    return () => clearInterval(t);
  }, [refreshStatus]);

  // ★ 三态区分（修「恒显启动中」）：真实 level > 拉取失败(ERR) > 首次未拉到(CONN)
  const level = status?.level ?? (statusErr ? "ERR" : "CONN");

  // ★ 2026-09-27（主人候办③「找不到历史」补刀）：**加载会话历史**。
  //   后端新增 `GET /sessions/history`（读 pi 原生 JSONL 归一化）——此前无任何读消息端点。
  //   策略：挂载 + 切换会话时拉一次；**只填充，不打断进行中的对话**（loadedRef 去重）。
  const loadedRef = useRef<string | null>(null);
  useEffect(() => {
    if (loadedRef.current === sessionId) return;
    loadedRef.current = sessionId;
    void (async () => {
      try {
        const h = await piAgentApi.history(sessionId);
        const chat = (h.messages ?? []).filter((m) => m.role !== "tool");
        if (chat.length > 0) {
          setMessages(
            chat.map((m) => ({
              role: m.role === "assistant" ? ("assistant" as const) : ("user" as const),
              text: m.text,
            })),
          );
        }
      } catch {
        /* 历史拉取失败不阻塞对话（可能尚无会话文件 / 后端旧版） */
      }
    })();
  }, [sessionId]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setNotice(null);
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text }, { role: "assistant", text: "" }]);

    const appendDelta = (d: string) =>
      setMessages((m) => {
        const next = [...m];
        const last = next[next.length - 1];
        if (last?.role === "assistant") next[next.length - 1] = { ...last, text: last.text + d };
        return next;
      });

    try {
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      let got = false;
      await chatStream(text, sessionId, {
        onDelta: (d) => {
          got = true;
          appendDelta(d);
        },
        onError: (detail, lv) => {
          setNotice(`降级（${lv ?? level}）：${detail}`);
          setMessages((m) => {
            const next = [...m];
            const last = next[next.length - 1];
            if (last?.role === "assistant" && !last.text) {
              next[next.length - 1] = { role: "assistant", text: "（本次未能回复）", error: true };
            }
            return next;
          });
        },
      }, ctrl.signal);
      if (!got) {
        // 流式一个字都没来 → 退回同步接口（更稳，且能拿到明确原因）
        const r = await piAgentApi.chat(text, sessionId);
        setMessages((m) => {
          const next = [...m];
          const last = next[next.length - 1];
          if (last?.role === "assistant" && !last.text) {
            next[next.length - 1] = r.degraded
              ? { role: "assistant", text: `（降级 ${r.level}）${r.detail ?? ""}`, error: true }
              : { role: "assistant", text: r.reply };
          }
          return next;
        });
        if (r.degraded) setNotice(`降级（${r.level}）：${r.detail ?? ""}`);
      }
    } catch (e) {
      const err =
        e instanceof ApiError && e.status >= 500
          ? "服务异常，请稍后再试"
          : "发送失败，请稍后再试";
      setMessages((m) => {
        const next = [...m];
        const last = next[next.length - 1];
        if (last?.role === "assistant" && !last.text) {
          next[next.length - 1] = { role: "assistant", text: err, error: true };
        }
        return next;
      });
    } finally {
      abortRef.current = null;
      setBusy(false);
      void refreshStatus();
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    setBusy(false);
  };

  const lockSession = async () => {
    try {
      await piAgentApi.sessionAction("lock", sessionId);
      setNotice(`已锁定会话「${sessionId}」`);
      void refreshStatus();
    } catch {
      setNotice("锁定失败（后端不可达）");
    }
  };

  const retry = async () => {
    try {
      await piAgentApi.resetCircuit();
      setNotice("已重置熔断，重试中…");
      void refreshStatus();
    } catch {
      setNotice("重置失败");
    }
  };

  return (
    <div className="pi-agent" data-testid="pi-agent-app">
      <div className="pi-agent__bar">
        <span className={`pi-agent__level is-${level}`} data-testid="pi-level">
          {LEVEL_TEXT[level] ?? level}
        </span>
        <input
          className="pi-agent__session"
          value={sessionId}
          onChange={(e) => setSessionId(e.target.value)}
          aria-label="会话名"
          placeholder="会话名"
        />
        <button
          type="button"
          className="btn"
          data-testid="pi-lock"
          onClick={lockSession}
          disabled={busy}
        >
          锁定
        </button>
        <span className="pi-agent__meta" data-testid="pi-sessions">
          {sessions.length} 个活跃会话
          {status?.model ? ` · ${status.model}` : ""}
        </span>
        {level === "L3" ? (
          <button type="button" className="btn" onClick={retry}>
            重试
          </button>
        ) : null}
      </div>

      {notice ? (
        <div className="pi-agent__notice" role="status">
          {notice}
        </div>
      ) : null}

      <div className="pi-agent__msgs" data-testid="pi-msgs" role="log" aria-live="polite">
        {messages.map((m, i) => (
          <div key={i} className={`pi-agent__msg is-${m.role}${m.error ? " is-error" : ""}`}>
            <div className="pi-agent__bubble">{m.text || (busy ? "…" : "")}</div>
          </div>
        ))}
      </div>

      <div className="pi-agent__composer">
        <textarea
          className="pi-agent__input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          placeholder="问点什么…（Enter 发送，Shift+Enter 换行）"
          aria-label="消息输入"
          data-testid="pi-input"
          rows={2}
        />
        {busy ? (
          <button type="button" className="btn" onClick={stop}>
            停止
          </button>
        ) : (
          <button
            type="button"
            className="btn btn--primary"
            data-testid="pi-send"
            onClick={send}
            disabled={!input.trim()}
          >
            发送
          </button>
        )}
      </div>
    </div>
  );
}
