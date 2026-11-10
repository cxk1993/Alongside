# 初版发布验收清单

> 一条命令回答「这版能不能发」：`python tools/release_check.py`
> 退出码 **0 = 可以发**，**1 = 不能发**，**2 = 参数错**。
> 机器判据 11 项（**9 项阻塞 + 2 项仅报告**），外加 §4 的人工项。
> 分档依据见 `reports/initial-version-scope.md`（A 必带 / B 标注 / C 剥离）。

---

## 0. 怎么用

```bash
# 全跑（约 5~8 分钟，最慢的是全量 pytest）
python tools/release_check.py

# 只看会跑哪些项 / 只跑几项（改一处验一处时用）
python tools/release_check.py --list
python tools/release_check.py --only pytest --only frontend-tsc

# 机读（CI 用），退出码语义同上
python tools/release_check.py --json
```

输出形如：

```
[2/11] 后端全量 pytest（tests + tests_protocol + tests_packs）
      ✓ 960 passed / 0 failed / 0 error / 8 skipped
...
结论：✓ 可以发 —— 9 项阻塞判据全部通过（共跑 11 项）
EXIT=0
```

三条使用纪律：

1. **在干净工作区跑。** 工作区脏时结论只对「当前工作区」成立，不对 HEAD 成立——
   脚本第一项会打印 △ 提醒。多会话并行改仓库时，红灯先分清「代码真坏了」还是「别人改到一半」。
2. **不要并发跑。** 脚本内部已严格串行；本机内存紧张，别同时开第二个全量 pytest。
3. **红了先看证据行，再看 `--only` 单跑那一项**；不要一上来就改测试。

判据分级（脚本里的 `blocking`）：

| 级别 | 含义 | 红了怎样 |
|---|---|---|
| **阻塞** | 协议/边界/构建的硬判据 | 不许发，退出码 1 |
| **仅报告** | 已知债务（如前端内核纯度的 10 处业务词） | 只提示，不拦发布；但**不许扩大** |

---

## 1. 机器判据（脚本按此顺序串行跑）

| # | 级别 | 判据 | 命令 | 不过怎么办 |
|---|---|---|---|---|
| 1 | 仅报告 | **工作区干净**：被测对象 == HEAD | `git status --porcelain` | 脏工作区下的结论只对当前工作区成立。**发布前必须在干净工作区重跑一次**；红灯若来自别人的在制改动，等它落地或让出 |
| 2 | 阻塞 | **后端全量测试绿**：`tests` + `tests_protocol` + `tests_packs` 0 failed / 0 error | `services/api/.venv/Scripts/python.exe -m pytest tests tests_protocol tests_packs` | 看 `short test summary` 的 `FAILED` 行，用 `pytest <文件>::<用例> -x` 单跑。先判「代码坏了」还是「测试没跟上契约」：**改测试必须能说出契约哪一条变了**，否则就是拿测试迁就 bug |
| 3 | 阻塞 | **内核纯度·后端**：`core/` 里 0 处业务词汇（16 文件） | `python scripts/check_kernel_purity.py --scope backend`（cwd `services/api`） | 业务逻辑搬进 `modules/<id>/`；确需保留就在该行写 `purity-ok: <理由>` 显式豁免——**不许沉默放过** |
| 4 | 仅报告 | **内核纯度·前端**（已知债务 C1）：容器吸收未统一，`kernel/` 里仍有业务词 | `python scripts/check_kernel_purity.py --scope frontend --warn-only` | **不拦发布**，但数量不许增长（当前 10 处，集中在 `Dock.tsx` / `DesktopSidebar.tsx` / `TimeWidget.tsx`）。根治归 C1 |
| 5 | 阻塞 | **协议字段有消费者**：36 字段每个要么有运行时消费点，要么在「已知未消费」登记表；登记表与扫描结果一致 | `python scripts/check_protocol_consumption.py`（cwd `services/api`，`--list` 看明细） | 二选一：① 补消费代码；② 进 `KNOWN_UNCONSUMED` 并写明「为什么现在不消费」。门禁自带 `--selfcheck`（摘登记 / 幽灵字段 / 空扫描根三种必红），改完用它自证 |
| 6 | 阻塞 | **官方包自洽**：`pack.py check` 对 `packs/official.life-tasks` 无问题 | `python scripts/pack.py check ../../packs/official.life-tasks`（cwd `services/api`） | 修 `pack.json` / 成员声明。★ 注意：`pack check` **不校验成员内容摘要**，摘要由第 2 项（`tests_packs`）兜底——两条都必须绿 |
| 7 | 阻塞 | **数据作用域接线（G16，运行时判据）**：after_begin 真挂、未声明即拒、插件白名单拦读/写/删、连接不泄漏、只约束第三方 | `python tools/gates/verify_db_scope_wiring.py`（cwd 仓库根，`--verbose` / `--json`） | 按失败的 W1~W8 与 hint 改 `core/db_scope.py` / `core/deps.py` / `db/engine.py`。**连接泄漏（W5）最优先**：它会让全量测试 180s 卡死 |
| 8 | 阻塞 | **门禁清单自洽**：登记项全部落地，没有「像门禁却没登记」的脚本 | `python tools/gate_registry.py` | 新门禁要么登记（`id` / `blocking` / `test_ref`），要么改名去掉门禁特征词。当前 12 个门禁里 9 个没有回归测试守着——登记在册的风险，不是错 |
| 9 | 阻塞 | **数据作用域三态实测**：7/7（kernel 放行 / 插件读自己 OK、读别人 DENIED / 未声明 DENIED） | 脚本内置探针（临时库，不碰 `data/`）；也可单跑 `python tools/release_check.py --only scope-tristate` | 期望 `DENIED` 却 `OK` = 边界漏了（安全事故级）；期望 `OK` 却 `DENIED` = 误伤内置模块（症状是「数据静默变空」，日志里没有 ERROR） |
| 10 | 阻塞 | **前端类型检查**：`tsc --noEmit` 0 错误 | `node node_modules/typescript/bin/tsc --noEmit`（cwd `apps/web`，等价 `npx tsc --noEmit`） | 修类型。不许 `@ts-ignore` 遮盖；确属三方类型缺失才 `// @ts-expect-error` 并写理由 |
| 11 | 阻塞 | **前端单进程 vitest** | `node node_modules/vitest/vitest.mjs run --poolOptions.forks.singleFork=true`（cwd `apps/web`） | 先单文件复跑 `vitest run src/x.test.ts`。★ 必须 `singleFork`：默认多进程在本机会把内存打满，表现为**随机超时**而不是断言失败 |

---

## 2. 为什么是这十一项

它们覆盖的是**「声明与实现的对齐」**，不是「功能齐不齐」：

- 内核纯度 + 协议消费 → 让「一切皆插件」这句话在代码里站得住；
- 包层 check + 基线锁（`tests_packs` 里的 `test_lock_file_is_committed_and_matches`）→ 让「改了模块必须重签锁」成为机器判据，而不是等别人装上才发现摘要不符；
- 三态实测 + G16 → 让数据边界**运行时真的在拦**，而不是「文件里有个 `event.listen`」；
- tsc + vitest → 让壳能构建、页面不白屏。

**能跑 ≠ 声明成立**，这是这一版最贵的一课：`pack.py check` 绿着，`tests_packs` 仍可能因成员摘要过期而红——两条都得看。

## 3. 发布流程（推荐顺序）

```bash
# 1) 确认工作区就是你要发的东西
git status --short            # 应为空（README.md.bak-* 之类的临时文件先删/忽略）

# 2) 一条命令跑全部判据
python tools/release_check.py

# 3) 绿了才推
git tag -a v1.0.0 -m "初版" && git push origin main --tags
```

红灯时的最短路径：`--only <id>` 单跑 → 修 → 再 `--only <id>` → 最后全跑一次收口。
**不要**在红灯状态下先 commit 再「后面补」——这一版的卖点就是地基扎实。

---

## 4. 人工判据（机器判不了的，发布前逐条打勾）

| # | 判据 | 怎么核 |
|---|---|---|
| M1 | **对外口径与 A/B/C 分档一致**：A 档说「已绿」，B 档说「声明位，运行时未建」，C 档**不提** | 对照 `reports/initial-version-scope.md` §5 的「对外怎么说」，逐句核 README / 宣发稿 |
| M2 | **experimental 标注到位**：B1 `consumes`、B2 `absorbs`、B3 `lifecycle` 自定义钩子、B4 container 页签分叉 | 在协议文档与 `docs/specs` 搜索这四个词，确认都带「未建/预留/分叉」字样 |
| M3 | **不承诺前端「一切皆插件」**：C1 未完成，容器吸收仍是硬编码 | 检查对外材料里没有「全部由插件长出来」这类无限定的表述 |
| M4 | **死文档改掉**：`summary` 模块的 `/today` 是死代码（BFF 同前缀先匹配），文档必须写明走 BFF | `docs/specs/today-summary数据源规范-v1.md` 有没有这句 |
| M5 | **仓库干净**：无 `_probe_*.py`、无 `tmp_*.db` 被跟踪、无 `.bak` | `git ls-files \| grep -E "_probe_|tmp_.*\.db\|\.bak$"` 应为空（当前 tmp db 均已被 gitignore） |
| M6 | **法律面**：`LICENSE`(Apache-2.0) + `NOTICE` 齐全，第三方许可已在 NOTICE 列明 | `ls LICENSE NOTICE`；新增依赖的许可是否补进 NOTICE |
| M7 | **版本号一致**：`apps/web/package.json` 与后端 `pyproject.toml` 的主版本，且与 tag 一致 | 三处对齐 |

---

## 5. 已知红灯台账（跑之前先知道）

| 现象 | 是不是真红灯 | 处置 |
|---|---|---|
| `tests_packs` 报「成员 X 的内容摘要与基线不符」 | **是**（包锁过期） | `python scripts/pack.py lock ../../packs/official.life-tasks` 重签，然后**重跑全量**。若对不上是哪次改动，先 `git log -p -- services/api/modules/<id>`，别盲签 |
| 三态/`G16` 全绿但全量测试红 | **是** | 门禁只测机制、不测系统。以全量测试为准，并记一笔「门禁缺哪条判据」 |
| 前端内核纯度 △（10 处） | 否（已知债务） | 不新增即可，C1 修 |
| `worktree` △ | 否，但**结论降级** | 干净工作区重跑一次再发 |
| pytest 卡住 180s 不动 | **是**（大概率连接池泄漏） | 单跑 `tools/gates/verify_db_scope_wiring.py --verbose` 看 W5 |

---

## 6. 一句话

> 九项阻塞判据全绿 + §4 人工项全勾 ⇒ 可以发；
> 任何一项阻塞判据红 ⇒ 不许发，不管功能看起来多完整。
