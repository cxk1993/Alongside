import { describe, expect, it } from "vitest";

import {
  addFav,
  FAVS_DEFAULT,
  FAVS_KEY,
  FAVS_MAX,
  loadFavs,
  moveFav,
  moveFavTo,
  normalizeFavs,
  removeFav,
  saveFavs,
} from "./homeFavs";

/**
 * 「我的常用」纯函数层。
 *
 * 锁六件事：
 *   ① 首次打开不是空框子（默认 5 个）—— 主人原话「根本就没有那个小框子」；
 *   ② 坏值不当真：非 JSON / 非字符串 / 嵌套 → 退回默认，绝不白屏；
 *   ③ 显式空数组尊重用户（他清空了就是清空，别偷偷塞回来）；
 *   ④ 上限 8：超了就忽略（不然就是第二个 Dock）；
 *   ⑤ 挪序越界不动（按到边界不能把顺序打乱）；
 *   ⑥ 落盘读回一致（键名必须就是 `alongside.favs.v1`，旧键不复活）。
 */

function memStorage(init: Record<string, string> = {}) {
  const map = new Map(Object.entries(init));
  return {
    getItem: (k: string) => (map.has(k) ? (map.get(k) as string) : null),
    setItem: (k: string, v: string) => void map.set(k, v),
    dump: () => Object.fromEntries(map),
  };
}

describe("我的常用 · 读取", () => {
  it("键不存在时给默认 5 个（空框子等于没做）", () => {
    expect(loadFavs(memStorage())).toEqual([...FAVS_DEFAULT]);
    expect(FAVS_DEFAULT).toEqual(["calendar", "todo", "habits", "finance", "notes"]);
  });

  it("坏 JSON 退回默认，不抛错", () => {
    expect(loadFavs(memStorage({ [FAVS_KEY]: "{不是 JSON" }))).toEqual([...FAVS_DEFAULT]);
  });

  it("显式空数组被尊重（不偷偷塞回默认值）", () => {
    expect(loadFavs(memStorage({ [FAVS_KEY]: "[]" }))).toEqual([]);
  });

  it("非字符串元素视作坏值 → 默认", () => {
    expect(normalizeFavs([1, "todo"])).toBeNull();
    expect(loadFavs(memStorage({ [FAVS_KEY]: JSON.stringify([1, "todo"]) }))).toEqual([
      ...FAVS_DEFAULT,
    ]);
  });

  it("存储不可用时也不炸（隐私模式）", () => {
    expect(loadFavs(undefined)).toEqual([...FAVS_DEFAULT]);
    expect(() => saveFavs(["todo"], undefined)).not.toThrow();
  });
});

describe("我的常用 · 落盘", () => {
  it("写进去再读回来一致，且键名正确", () => {
    const s = memStorage();
    saveFavs(["notes", "todo"], s);
    expect(s.dump()[FAVS_KEY]).toBe(JSON.stringify(["notes", "todo"]));
    expect(loadFavs(s)).toEqual(["notes", "todo"]);
  });

  it("写入时会截断到上限（防止历史脏数据越滚越长）", () => {
    const s = memStorage();
    saveFavs(Array.from({ length: 20 }, (_, i) => `m${i}`), s);
    expect(JSON.parse(s.dump()[FAVS_KEY])).toHaveLength(FAVS_MAX);
  });
});

describe("我的常用 · 增删挪", () => {
  it("加：去重、超上限忽略", () => {
    expect(addFav(["a"], "a")).toEqual(["a"]);
    const full = Array.from({ length: FAVS_MAX }, (_, i) => `m${i}`);
    expect(addFav(full, "x")).toBe(full);
  });

  it("删：只删那一个", () => {
    expect(removeFav(["a", "b", "c"], "b")).toEqual(["a", "c"]);
  });

  it("挪一格：正常换位，越界原样返回", () => {
    expect(moveFav(["a", "b", "c"], "b", -1)).toEqual(["b", "a", "c"]);
    expect(moveFav(["a", "b", "c"], "a", -1)).toEqual(["a", "b", "c"]);
    expect(moveFav(["a", "b", "c"], "c", 1)).toEqual(["a", "b", "c"]);
    expect(moveFav(["a"], "不存在", 1)).toEqual(["a"]);
  });

  it("拖拽落位：挪到目标位置；目标不存在则不动", () => {
    expect(moveFavTo(["a", "b", "c"], "c", "a")).toEqual(["c", "a", "b"]);
    expect(moveFavTo(["a", "b", "c"], "a", "zzz")).toEqual(["a", "b", "c"]);
  });
});