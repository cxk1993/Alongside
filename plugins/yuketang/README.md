# 雨课堂 · Alongside 插件（v0.1.0）

只读同步**你自己账号**里的雨课堂课程、上课记录、公告与考核截止时间；公告和临期考核可一键转进待办。
纯 API 插件，没有窗口。

---

## 验证状态（2026-11-15）

用真实账号（山东大学 · 长江雨课堂 `changjiang.yuketang.cn`）验证：

| 能力 | 端点 | 结果 |
|---|---|---|
| 用户信息 | `GET /v2/api/web/userinfo` | ✅ 200 |
| 课程列表 | `GET /v2/api/web/courses/list?identity=2` | ✅ 200，**N 门课** |
| 班级详情 | `GET /v2/api/web/classrooms/{cid}?role=5` | ✅ 200（`course_sign` / `uv_id`） |
| 上课记录 | `GET /v2/api/web/logs/learn/{cid}?actype=-1&page=0&offset=N&sort=-1` | ✅ 200 |
| 公告 | `GET /v/discussion/v2/announcements/?cid=&limit=&offset=0&type=9` | ✅ 200（多门课有公告） |
| 章节树 | `GET /mooc-api/v1/lms/learn/course/chapter?cid=&sign=&term=latest&uv_id=` | ✅ 200（15 / 8 / 10 条不等） |
| 写待办 | `POST /api/v1/todo/items`（经 `get_plugin_client`） | ✅ 实测：**N 条公告 → N 条待办**，第二次同步 `created=0 / skipped=N` |

**两个实测坑**（已写进 `client.py`）：

1. **区域**：`www.yuketang.cn` 与 `changjiang.yuketang.cn` 的会话不通用。`base_url` 必须填你学校所在的那个域，否则 401/302。
2. **请求头**：章节接口不认 Cookie 里的 `xtbz`，必须带请求头 `xtbz: ykt`、`x-client: web`、
   `terminal-type: web`、`platform-id: 3`、`university-id: <uv_id>`，否则返回
   `{"msg":"XTBZ IS REQUIRED","error_code":40000}`。

---

## 边界

| 做 | 不做 |
|---|---|
| 用你复制的那一个 `sessionid` 只读课程/公告/考核 | 存账号密码、扫码登录、碰验证码 |
| 只发 GET | 交作业、签到、答题、发弹幕（没有任何写回雨课堂的路径） |
| 只连 `*.yuketang.cn` / `*.rainclassroom.com` | 复制课件、题库、视频 |
| 会话可一键删除 | 把 sessionid 写进日志 |

---

## 装

```bash
cd 06-校园插件接入/yuketang
tar -a -cf ../yuketang-0.1.0.zip manifest.json README.md api

curl -X POST http://127.0.0.1:18000/api/v1/plugins/upload \
  -H "Authorization: Bearer <token>" -F "file=@../yuketang-0.1.0.zip"

curl -X POST http://127.0.0.1:18000/api/v1/plugins/yuketang/enable \
  -H "Authorization: Bearer <token>"
```

## 拿 sessionid（一次）

1. 浏览器打开**你学校的雨课堂域名**（山大是 `https://changjiang.yuketang.cn`）并登录；
2. F12 → Application → Cookies → 选这个域；
3. 复制 `sessionid` 的值；
4. `sessionid` 只在它自己的域下有效——`base_url` 要填同一个域。

```bash
curl -X POST http://127.0.0.1:18000/api/v1/yuketang/session \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"sessionid":"<粘贴>","base_url":"https://changjiang.yuketang.cn"}'
```

## 端点

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 探针 |
| POST | `/session` | 保存并当场验证会话 |
| DELETE | `/session` | 撤销 |
| GET | `/status` | 会话状态（值已打码） |
| GET | `/courses` | 课程/班级列表 |
| GET | `/activities?classroom_id=` | 上课记录 |
| GET | `/notices?classroom_id=` | 公告 |
| GET | `/assignments?classroom_id=&days=` | 考核/章节截止时间 |
| POST | `/sync` | 公告 + 临期考核写进待办（幂等） |

## 能力

- `yuketang.course.read` / `yuketang.activity.read` / `yuketang.sync`
- 软依赖 `todo.item.write`：todo 不在时只有 `/sync` 返回 403，其余照常。
- 发 `yuketang.synced`（每次同步成功一条）。

## 已知限制

1. **没有逐条作业的 D-day**：这个账号的章节树里 `score_deadline` 一律是课程统一截止日
   （2027-02-28），不是每条作业的截止时间。所以 `/sync` 只同步 30 天内截止的考核，
   当前数据下通常为 0 条；**公告是主要可同步内容**。
2. `sessionid` 明文存 `yuketang_session` —— 内核 `secretRef` 未落地（B3）；
   现在靠「一键删除 + 不打日志」兜底。
3. 同步是**手动触发**（`POST /sync`），没有定时任务；要自动同步得加 scheduler。
4. **旧版内核装完要重启一次**：`/install` 在 2026-10 的版本里不会把模块登记进
   `app.state.modules`，导致插件路由内的 `get_plugin_client` 认不出调用方（403
   「无法识别调用方插件」）。重启后启动恢复会补登记；11-15 之后的版本没有这个问题。
