# i山大（`isdu`）· Alongside 内置模块

把山东大学 iSDU / 校园服务的一部分只读能力接进 Alongside：

- 通知公告（公开接口 + 本地缓存）
- 课表（网格展示 + 一键幂等导入 `course`）
- 成绩、考试、志愿、校历
- 生活服务：一卡通（只读余额/流水/卡号）、宿舍电量、空闲教室、校车时刻表
- 图书馆：实时座位、座位预约记录、馆藏检索
- 校园资讯、校内电话

**只读优先**：不实现充值、缴费、选座、借还书等写操作；需要支付的页面只放跳转链接。

## 0. 登录链路（为什么不是账号密码）

iSDU 的登录走学校 CAS，页面有验证码与「信任此设备」，App 密码接口又是运行时下发的
RSA/AES 加密参数。因此插件不保存账号密码，而是复用浏览器 CAS 登录后产生的
`CASTGC`：

1. 用户在浏览器/桌面壳登录 CAS；
2. 登录成功瞬间拿到 `CASTGC`（形如 `TGT-...`，仅此一次出现）；
3. 本机后端 `POST /v2/api/auth/login/castgc` 兑换 iSDU `token`；
4. `CASTGC` 兑换后立即丢弃，`token` 只存在后端进程内存。

实现要点：

- 请求体是**整条 cookie 串**（`CASTGC=...`），只发裸 TGT 会被上游拒绝；
- 数据接口要求请求头 `token: <32 位 hex>`，不是 `Authorization: Bearer`；
- 兑换接口默认只允许本机来源（可用 `ISDU_ALLOW_REMOTE_LOGIN` 显式放开，供反向代理场景）。

## 1. 端点一览

### 1.1 网关

- 基地址：`https://i.sdu.edu.cn/isduapi`
- 登录：`POST /v2/api/auth/login/castgc`
- 数据：`/v2/api/...`，需要 `token` 头

### 1.2 通知与资讯

| 接口 | 用途 |
|---|---|
| `GET /v2/api/core/notice/latest` | 公开公告列表 |
| `GET /v2/api/core/news/list` | 校园资讯列表 |
| `GET /v2/api/core/news?id=` | 校园资讯详情（返回 `{title, content(HTML), attachments}`，无 `code` 外壳） |

### 1.3 学业

| 接口 | 用途 |
|---|---|
| `GET /v2/api/edu/course_schedule` | 课表原始行 |
| `GET /v2/api/edu/grades` | 成绩 |
| `GET /v2/api/edu/exam_schedule` | 考试安排 |
| `GET /v2/api/edu/school_calendar` | 校历 |
| `GET /v2/api/core/volunteer/get_volunteer_info` | 志愿时长与明细 |

### 1.4 生活服务

| 接口 | 用途 |
|---|---|
| `GET /v2/api/core/card/bill?start_date=&end_date=` | 校园卡余额与流水（只读） |
| `GET /v2/api/core/dorm-elec/areas` / `buildings` / `electricity` | 宿舍电量（校区 → 楼栋 → 房间） |
| `GET /v2/api/edu/studyrooms/buildings?campus=` | 自习室楼栋 |
| `GET /v2/api/edu/studyrooms/data?campusId=&buildingId=&date=` | 自习室数据（上游当前可能返回空） |
| `GET /v2/api/edu/library/...` | 图书馆座位/馆藏（见下） |
| `GET http://www.sd114.sdu.edu.cn:8080/web/bus/load?type=0\|1` | 校车时刻表（独立 H5 系统，无鉴权） |
| `GET /v2/api/core/phone/blocks` / `phone?param0=&param1=` | 校内电话 |

### 1.5 图书馆

| 接口 | 说明 |
|---|---|
| `GET /v2/api/library/room/list?library=` | `library` 必须是**图书馆全名**；返回楼层/区域树，每层带 `freeSeatCount` / `totalSeatCount` |
| `GET /v2/api/library/seat/list?roomId=` | 座位列表，`status`：1=空闲，6=使用中，7=临时离开 |
| `GET /v2/api/library/seat/history` | 我的预约记录（只读） |
| `GET /v2/api/library/book/search?key=` | 馆藏检索（最多 20 条） |
| `GET /v2/api/library/book/info?bookId=` | 书目详情（馆藏地/索书号/在架状态） |

### 1.6 空闲教室（独立公开 JSON）

`https://xxbdeploy.sdu.edu.cn/prod-api/free/free-classrooms/...` 是学校「空闲教室查询」
App 的公开接口，无需登录：

- `GET .../buildings`
- `GET .../classrooms?buildingId=`
- `GET .../classrooms-in-use?date=&buildingId=`

后端把「教室列表 + 当天的有课记录」拼成占用表（节次 1~10），前端画成
「教室 × 节次」矩阵：课=有课，空白=空闲。不判断「整栋楼有没有空教室」。

## 2. 课表导入怎么保证不重复

`GET /v2/api/edu/course_schedule` 返回的字段与 `course` 模块不同，`map_courses.py` 做两件事：

- **字段归一**：`courseName/teacher/weeks/weekday/order` 等做别名映射，未知字段写进
  `unknown_keys` 返回（不静默丢弃）；
- **小节换算**：`courseOrder` 是第几大节（1..5），换算成 `start=2*order-1, end=2*order`；
  `courseWeeks` 是 JSON 字符串（如 `"[1,2,3,4]"`），归一成 `"1-4"`；
  `courseLocation == "无"` 视为没有地点。

写入 `course` 时用**内容指纹**做幂等键（课程名 + 星期 + 节次 + 周次），重复导入不会造重复课；
一次导入上限 200 门，超过会拒绝。

## 3. 前端页签

| 页签 | 内容 |
|---|---|
| 公告 | 本地缓存 + 同步按钮 + 登录态卡片（手动 CASTGC 兜底） |
| 课表 | 周一~周日 × 第 1~12 节网格；周次过滤；块上显示课程/地点/教师；一键导入 `course` |
| 成绩 | 学期胶囊 + 全部学期聚合；加权平均分 / 加权绩点 / 学分 |
| 考试 | 考试安排列表 |
| 志愿 | 志愿时长 + 明细 |
| 自习室 | 空闲教室占用表 |
| 校车 | 工作日/双休日时刻表 + 校区筛选 |
| 图书馆 | 图书馆 → 楼层/区域（带空位数）→ 座位矩阵（30 秒刷新）+ 预约记录 + 馆藏检索 |
| 一卡通 | 余额、卡号、账单日期区间、宿舍电量查询（只读 + 服务大厅跳转） |
| 电话 | 分组 → 部门 → 号码 |

## 4. 已知限制 / 未做

- **校园卡流水**：部分账号上游恒为空，属上游数据问题，插件只读展示；
- **自习室数据**：`studyrooms/data` 在当前环境实测可能返回空，页面只展示楼栋；
- **教学计划**：iSDU 没有 JSON 接口，只有教务系统 SSO 链接，未做内嵌；
- **图书馆选座/预约、借书续借、充值缴费**：均为写操作，明确不做；
- 学校接口可能变更；字段不匹配时 `map_courses` 会如实上报 `unknown_keys`，不猜测。

## 5. 测试

```bash
cd services/api
.venv/Scripts/python -m pytest tests/test_isdu_timetable.py tests/test_isdu_security.py -q
```

覆盖：字段别名、1-based/0-based 星期、周次归一、重复导入幂等；
以及 `/raw` 路径白名单、`device` 清洗、40101 → 重新登录、上游故障 → 503、校历脏数据容错。

## 6. 安全与数据边界

- `CASTGC` 只在兑换请求里出现一次；后端不保存原文，只记 `has_castgc` 布尔；
- iSDU `token` 只在进程内存：不写数据库、不写 `.env`、不写日志、不回显前端；
- 上游错误信息不包含票据/token；错误统一映射为 503 并提示重新登录；
- `/raw` 排障端点只允许 `/v2/api/edu/<段>` 形式的只读路径，拒绝 `..`、编码绕过与绝对 URL；
- 插件出站只访问学校服务（`i.sdu.edu.cn`、`www.sd114.sdu.edu.cn`、
  `xxbdeploy.sdu.edu.cn`），不经过任何 Alongside 服务器，无遥测；
- 本地 SQLite（`data/`）与日志均在用户机器上，`.gitignore` 默认排除。
