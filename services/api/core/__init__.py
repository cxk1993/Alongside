"""Alongside 后端内核（T03 领地）。

内核只负责：配置 / 鉴权 / 日志 / 异常 / 路由挂载 / 事件 / 数据访问接口。
不认识任何业务词汇，那是插件的事（机器判据见 scripts/check_kernel_purity.py）。
"""
