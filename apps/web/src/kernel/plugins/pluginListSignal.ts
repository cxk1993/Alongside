/**
 * 插件清单刷新信号（r5 · 「安装/卸载后不刷新页面可见」）。
 *
 * 背景：第三方插件**运行时**装上/卸下后，桌面（Dock 出现/置灰、窗口重渲染）必须
 * 不刷新页面就反映出来。后端 `GET /api/v1/plugins` 已是权威源、注册链路也已运行时，
 * 缺的只是一个「该重取了」的触发点。
 *
 * 为什么是信号而不是直接调用：
 *   `<PluginProvider>` 包在桌面外层，管理面（plugins 页 / packs 页）在窗口内，是同一棵树上的
 *   远亲。信号让任意面都能请求重取，无需把它们串起来，也不引入任何业务名 / 插件 id。
 *
 * 通道选择：**事件通道优先**——后端若将来 emit 插件生命周期事件，订阅那里即可；
 *   当前 `modules/plugins/manifest.json` 的 `emits` 为空（无事件），故退而用
 *   「变更后主动 poke + 管理页手动刷新 + 回到页面时重取（带节流）」这条廉价通道。
 *   ★ 绝不轮询 —— 没有定时器。
 */
type Listener = () => void;

const listeners = new Set<Listener>();

/** 订阅「请重取插件清单」。返回退订函数。 */
export function onPluginListRefresh(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/**
 * 请求重取插件清单。无订阅者时静默 no-op（管理页可独立渲染，不必依赖 provider 在场）。
 * 单个订阅者抛错不影响其它订阅者。
 */
export function requestPluginListRefresh(): void {
  for (const fn of [...listeners]) {
    try {
      fn();
    } catch {
      /* 订阅者自身的问题不该波及信号发射方 */
    }
  }
}
