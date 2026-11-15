# WE Learn · Alongside 插件（v0.1.0）

只读同步**你自己账号**里的 WE Learn 课程、单元与学习进度；没刷完的单元可一键转待办。
纯 API 插件，没有窗口。

---

## 验证状态（真实账号实测）

| 能力 | 端点 | 结果 |
|---|---|---|
| 课程列表 | `GET /ajax/authCourse.aspx?action=gmc` | ✅ 1 门课（课程 A，进度 N%） |
| 单元与进度 | `GET /student/course_info.aspx?cid=<cid>`（HTML） | ✅ 8 个单元，首个单元已完成 X/Y |
| 写待办 | `POST /api/v1/todo/items` | ✅ 7 条，第二次同步 `created=0 / skipped=7` |

**鉴权只靠 Cookie**：`.AspNet.Cookies` + `ASP.NET_SessionId`，没有签名、没有风控参数——
这是五家里第二个能服务端直连的（第一个是雨课堂）。

---

## 边界

| 做 | 不做 |
|---|---|
| 只读课程 / 单元 / 进度 | 提交作业、刷课、答题（没有任何写回路径） |
| 只连 `*.sflep.com` | 复制教材、音频、题库 |
| Cookie 可一键删除 | 把 Cookie 写进日志 |

---

## 装

```bash
cd 06-校园插件接入/welearn
tar -a -cf ../welearn-0.1.0.zip manifest.json README.md api

curl -X POST http://127.0.0.1:18000/api/v1/plugins/upload \
  -H "Authorization: Bearer <token>" -F "file=@../welearn-0.1.0.zip"
curl -X POST http://127.0.0.1:18000/api/v1/plugins/welearn/enable \
  -H "Authorization: Bearer <token>"
```

> 旧内核（2026-10 及更早）没有 `/upload`：把目录放进 `/opt/lifeos/plugins/welearn`
> → `POST /plugins/install {"id":"welearn"}` → **重启服务**。

## 拿 Cookie（一次）

1. 浏览器登录 `https://welearn.sflep.com/`；
2. F12 → Application → Cookies → `welearn.sflep.com`；
3. 需要 **`.AspNet.Cookies`** 和 **`ASP.NET_SessionId`** 两个（整串复制最省事）；
4. `POST /session` 时把整串放进 `cookie` 字段（服务端会自己校验）。

## 端点

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 探针 |
| POST | `/session` | 保存并当场验证 Cookie |
| DELETE | `/session` | 撤销 |
| GET | `/status` | 会话状态 |
| GET | `/courses` | 课程列表 |
| GET | `/units?cid=` | 单元与进度 |
| POST | `/sync` | 未完成单元写进待办（幂等） |

## 已知限制

1. **单元是解析 HTML 得到的**：`/student/course_info.aspx` 是服务端渲染，改版会让正则失配；
   `/courses` 走的是 JSON，最稳。
2. 单元没有稳定 id，幂等键用 **`cid:单元名`**——平台改名会产生"新单元"。
3. Cookie 约 3 KB（`.AspNet.Cookies` 很长），已放宽到 4000 字符。
4. `session` 明文存库；内核 `secretRef` 未落地（B3），靠「一键删除 + 不打日志」兜底。
