# tools/accept_probe · L2 验收判据自动化套件

> **署名**：Qoder CN（L2 真机验收席）｜ **开工**：2026-09-20 12:05

## 用法

```bash
# 口令只走环境变量（绝不写入文件/回帖/日志）
export ALONGSIDE_ADMIN_PASSWORD=…
python tools/accept_probe/probe.py --target production --suite all      # 全套件
python tools/accept_probe/probe.py --target production --suite gate     # A6 三快腿
python tools/accept_probe/probe.py --target production --suite hash     # 入口基线
python tools/accept_probe/probe.py --target production --suite modules  # 坞 census
python tools/accept_probe/probe.py --target production --suite docsprobe # 根计数
python tools/accept_probe/probe.py --target production --suite o1status  # O1 四态（未部署=SKIP）
python tools/accept_probe/probe.py --target production --suite deploycheck  # 部署后四合一
```

退出码：`0` 全绿 ｜ `1` 有判据未过 ｜ `2` 用法/输入错误。可 `set -e` 串进部署 runbook。

## RFC-001 · 机读 verdict 事件（2026-09-20 13:5x 实现）

```bash
python tools/accept_probe/probe.py --target production --suite all --emit-verdict
```

- 人读表格与退出码语义**完全不变**，`--emit-verdict` 仅追加一行 JSON：
  `{"schema":"alongside.probe.verdict/1","ts":…,"target":…,"suite":…,"verdict":"PASS|FAIL","summary":{total,passed,failed},"contract_sha256":…,"rows":[…]}`
- **零凭证**：rows 只含判据名/期望/实测/判定，绝无 token/cookie/口令；
- **契约不动**：schema 版本号只进事件 `schema` 字段；`contract_sha256` = expected.json 全文 sha256（运行时计算）——期望值被静默修改会在此留下指纹；
- **并单**：本事件为 O 项（O1/O2）判据留痕标准件，O 项完工时按此格式附机读行；
- 旧用法（不带 flag）零影响。

## 套件 ↔ 判据来源映射（约束3：来源可溯）

| 套件 | 判据 | 出处 |
|:--|:--|:--|
| `gate` | login 200 / refresh cookie 可存且**零 Secure** / refresh 200 / `/me` sub=admin | ACCEPT-T30 A6 三快腿（02:23 全验判定帖 §1；02:53 延迟探针加固；11:4x 路线A 对表复绿） |
| `hash` | 入口 `index-*.js` = 基线 | hash 链（02:36 判定记录 §5 追记 → 12:00 快照未漂移） |
| `modules` | 计数 17 + id 集合 | 生产三项快照（12:00）；11:4x 逐数 id 复验 |
| `docsprobe` | 活跃人格根=1、日记根=1（根合计 2） | BUG-T16-1 销账 + T17 幂等线上（09:15 盘点 / 10:26 三合一眼验 / API 对表） |
| `o1status` | O1 四态契约：ok/count=len(modules)=17/id 集合≡modules 段/status∈四态/summary 和=count/scheduler_enabled=false | **判据先行**：契约源=MiMo《O1 字段契约卡》17:55（`00915b1`）；未部署 404→SKIP（非绿非红），部署后转实验 |
| `deploycheck` | healthz + gate + modules + hash 四合一 | 部署留痕帖的标准复核动作（ISSUE-006 路线A 对表首用） |

## ISSUE-008 · MCP 双闸门审计（2026-09-20 17:5x 升级）

```bash
python tools/accept_probe/mcp_path_audit.py                  # path+名集合双闸门
python tools/accept_probe/mcp_path_audit.py --verbose        # 附每条工具明细
python tools/accept_probe/mcp_path_audit.py --base <repo>    # 指定仓库根（负例沙栏专用）
```

- **闸门一 · path 全对表**：25 工具推导 path（registry_adapter 规则）vs 各模块真实路由，差集必须 0（ISSUE-008 验收件，`4483c98` 后 25/25）；
- **闸门二 · 名集合契约对表**：代码派生工具名集合 vs `expected.json mcp_tools.names`（人审写死的 25 名契约）——**缺/多都要红**：provides 或声明任何增删都会被抓，逼一次契约回帖（禁静默增删）；
- **`--base` 沙栏参数**（2026-09-20 事故教训）：负例测试一律在副本目录跑，**真文件零接触**；
- 退出码：`0` 双闸门全绿 ｜ `1` 任一闸门红 ｜ `2` 判据源失效。

## 契约纪律（约束2：expected.json 即契约）

- `expected.json` 是**唯一期望值源**；任何变动必须**说明理由并记录在案**，禁止静默放宽（历史教训：判据被「顺手放宽」）；
- 未实测验证的项一律 `enabled=false`（如 local 18000 开发实例），不得凭猜测置真；
- 判据期望值变更时，同帖附「旧值→新值+依据」。

## 边界与安全

- **🖥 腿边界**：本套件只做 **API 级 census + hash 对表**；浏览器 DOM 级断言（开窗/渲染/console 零错）归真机腿，**不 jsdom 化**（防 flake）；
- **只读**：零生产写入、零三方依赖（stdlib）；
- **凭证纪律**：口令只经 `ALONGSIDE_ADMIN_PASSWORD` 环境变量/`--password` 进程内存传入；token/cookie 全程内存不落盘；
- **领地**：仅本目录三个文件；`tools/deploy_package_preflight.py` 与其它 `tools/*` 不碰。

## 已知边界（v1）

- `docsprobe` 依赖 `/api/v1/docs/nodes` 当前响应形态（list 或 `{nodes:[]}` 均已适配）；形态若变，套件报「根合计」异常即预警；
- `hash` 只提取 `index-*.js`（入口束），不覆盖 chunk 级 hash；
- 被嵌页握手（B2 情形 B）机器判据 B2-2/3/4 属另一交付（候归卡派定），不在本目录。
