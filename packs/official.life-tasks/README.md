# 生活事务整理（official.life-tasks）

首发官方整合包。把「钱、事、时间」三件日常事务装进一个包：
账本（`finance`）、待办（`todo`）、日程（`calendar`）为必需成员，
复盘（`review`）与推送（`push`）为可选成员。

## 这个包解决什么

单装一个待办插件，用户得到的是一个孤立的清单；
装了这个包，账本的一笔支出、待办的一条事项、日程表上的一段时间
是同一套「今天」的三个面——`x.summary.today` 由包显式选定提供者，
而不是让内核去猜。

## 成员

| 成员 | 版本 | 必需 | 作用 |
|---|---|---|---|
| `finance` | 0.2.0 | 是 | 个人账本 + BeeCount 只读快照同步 |
| `todo` | 0.1.0 | 是 | 待办与周期清单 |
| `calendar` | 0.1.0 | 是 | 可嵌套日程表 |
| `review` | 0.1.0 | 否 | Work-Review 只读接入 |
| `push` | 0.1.0 | 否 | Web Push 浏览器推送 |

版本号是**精确版本**，必须与 `services/api/modules/<id>/manifest.json`
里的 `version` 一致。插件升版时这个包必须跟着改——
`tests_packs/test_official_pack.py` 会拿真 manifest 比对，漂了就红。

## 权限预算

`objectLevel` 为 `L1`（只碰用户自己的数据对象），
`capabilities` 是必需成员 `provides` 之并集的**真子集**：
只列读与写，**不列** `*.delete` / `*.patch`。
包只能收紧成员的声明，不能放大——删改历史条目属于更高风险的动作，
不该由「装一个包」顺带授予。

## 基线锁

`pack.lock.json` 由工具生成，**不要手写**：

```bash
cd services/api
.venv/Scripts/python.exe -m alongside pack lock ../../packs/official.life-tasks
```

（或直接调用 `packs.author.lock_pack()`。）
锁里记的是每个成员**实际内容**的摘要，不是清单里写的版本号——
同一版本下代码被改过是常态，只记版本号的锁会在这种时候给出
「没变」的错误结论。

改了本目录里的任何文件（包括这份 README）都要重新生成锁：
包级摘要覆盖整个包目录，摘要不符时装配会硬拒绝。

## 许可

包自身：Apache-2.0（见仓库根 `LICENSE`）。
成员均为本仓库内置插件（`kind: builtin`），同一许可。
