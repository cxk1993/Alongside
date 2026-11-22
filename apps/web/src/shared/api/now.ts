/**
 * `/api/v1/sight/now` 的**单一口径**（P0 · 2026-11-22）。
 *
 * 为什么要有这一层：顶栏（TopBar）与共处工作台（collab）此前各写一套取数 ——
 *   TopBar 用 react-query 拉一次、collab 用 useEffect 再拉一次。
 *   同一个「此刻」，两处采样时刻可以不同；界面自己跟自己打架，人无法判断哪个是真的。
 *
 * 本层口径（唯一）：
 *   - 模块级 state：一个 data / 一个 status / 一个 error，所有订阅者共享；
 *   - 订阅者第一次出现时取一次；TTL 内复用；页面可见时每 TTL 刷新一次；
 *   - 并发请求合并（inflight），不做本地增量拼接；
 *   - 最后一个订阅者离开即停表（不留下后台轮询）。
 *
 * 状态语义（与 collab 一致，别再造第三套）：
 *   loading = 首拉未回；ready = 有数据；missing = 404（后端尚未提供）；error = 其它失败。
 *   **404 与 401/500 必须分开**：前者是「还没做」，后者是「坏了」，界面上不能同一句话说。
 */
import { useEffect, useState } from "react";

import { api, ApiError } from "./client";

export type SightStatus = "loading" | "ready" | "missing" | "error";

export interface SightAttentionItem {
  kind?: string;
  title?: string;
}

export interface SightTensionItem {
  id?: string;
  kind?: string;
  summary?: string;
  severity?: string;
  /** 服务端已经给出「建议怎么办」——界面不许把它丢了。 */
  suggested_action?: string;
}

export interface SightDegradedItem {
  source?: string;
  reason?: string;
}

export interface SightNow {
  head?: { now_local?: string; weekday?: string; part_of_day?: string };
  attention?: SightAttentionItem[];
  tensions?: SightTensionItem[];
  degraded?: SightDegradedItem[];
}

interface NowState {
  status: SightStatus;
  data: SightNow | null;
  error: unknown;
}

const TTL_MS = 30_000;

let state: NowState = { status: "loading", data: null, error: null };
let lastFetchedAt = 0;
let inflight: Promise<void> | null = null;
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const fn of listeners) fn();
}

async function load(force: boolean): Promise<void> {
  if (inflight) return inflight;
  if (!force && state.status === "ready" && Date.now() - lastFetchedAt < TTL_MS) return;

  inflight = (async () => {
    // 保留已有数据时不闪回 loading：刷新期间界面不该抖。
    if (state.status !== "ready") {
      state = { status: "loading", data: null, error: null };
      emit();
    }
    try {
      const data = await api.get<SightNow>("/api/v1/sight/now");
      state = { status: "ready", data, error: null };
    } catch (e) {
      const missing = e instanceof ApiError && e.status === 404;
      state = { status: missing ? "missing" : "error", data: null, error: e };
    } finally {
      lastFetchedAt = Date.now();
      inflight = null;
      emit();
    }
  })();

  return inflight;
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  if (!timer) {
    void load(false);
    timer = setInterval(() => {
      // 后台标签页不轮询：省请求，也避免演示时看到「采样时刻」在无人看时变化。
      if (typeof document === "undefined" || document.visibilityState === "visible") {
        void load(true);
      }
    }, TTL_MS);
  }
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export interface UseSightNow {
  status: SightStatus;
  data: SightNow | null;
  error: unknown;
  /** 强制重拉（SSE 事件、用户点「刷新」时用）。 */
  refresh: () => Promise<void>;
}

/** 所有消费者共享同一个「此刻」。 */
export function useSightNow(): UseSightNow {
  const [snap, setSnap] = useState<NowState>(state);
  useEffect(() => subscribe(() => setSnap(state)), []);
  return { ...snap, refresh: () => load(true) };
}

/** 测试专用：清空缓存与定时器（不要在业务代码里调用）。 */
export function __resetSightNowForTests(): void {
  listeners.clear();
  if (timer) clearInterval(timer);
  timer = null;
  inflight = null;
  lastFetchedAt = 0;
  state = { status: "loading", data: null, error: null };
}
