# ════════════════════════════════════════════════════════════════
# Alongside · 项目唯一入口
#
#   主人只需要记这一组命令，不用记 npm / uvicorn 的细节。
#
#   ⚠️ 本文件的实现全在 tools/task.py 里。为什么？
#      总纲 §4 雷区 #1/#2：不许假设目标机器有 make / grep / find / wmic。
#      主人这台 Win11 就没有 make。所以真正的实现在跨平台 Python 脚本里，
#      本 Makefile 只是它的薄壳。
#
#   没有 make 的机器，直接用等价入口：
#       python tools/task.py dev
#       python tools/task.py verify
# ════════════════════════════════════════════════════════════════

PY ?= python

.PHONY: help setup dev lint test verify build clean new-plugin

help:
	@$(PY) tools/task.py help

setup:
	@$(PY) tools/task.py setup

# 开发：同时起前端 5173 与后端 8000
dev:
	@$(PY) tools/task.py dev

# 代码检查：前端 tsc+eslint+prettier / 后端 ruff+mypy（+ 内核洁癖检查，若已就位）
lint:
	@$(PY) tools/task.py lint

test:
	@$(PY) tools/task.py test

# 交付前必跑：lint + test + 前端构建
verify:
	@$(PY) tools/task.py verify

# 前端产物（有 docker 时顺带构建后端镜像）
build:
	@$(PY) tools/task.py build

# 清缓存，data/ 绝不动
clean:
	@$(PY) tools/task.py clean

# 生成新插件（脚手架由 T14 提供）
new-plugin:
	@$(PY) tools/task.py new-plugin --id $(id) --name "$(name)"
