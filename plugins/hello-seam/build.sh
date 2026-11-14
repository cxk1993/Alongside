#!/bin/sh
# hello-seam 的前端构建脚本 —— 第三方插件 UI 的**配方示例**（r5 热插拔）。
#
# ★ 契约：产物必须是**单文件 ESM**，且 react / react-dom / react/jsx-runtime
#   必须 external —— 两份 React 会炸 hooks，且失败模式隐蔽（组件能渲染，
#   useState 一调就崩）。
#
# 用法（在仓库根执行）：
#     sh plugins/hello-seam/build.sh
#
# 依赖：esbuild（apps/web 的 devDependencies 里已有）。
#
# ★ Windows 注意：esbuild 是原生程序，MSYS 风格的 `/e/ai work/...` 路径它认不出
#   （会被当成字面文件名 → "Could not resolve"）。故这里统一转成 `E:/...` 形式。
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
# MSYS: /e/ai work/... → E:/ai work/...（pwd -W 拿到 Windows 原生形式）
NATIVE=$(cd "$HERE" && pwd -W 2>/dev/null || echo "$HERE")
# ★ 直接调 esbuild.exe，不走 .bin/esbuild.cmd —— 后者是批处理，
#   路径里有空格时 MSYS 会把参数拆坏（实测 'E:\ai' 不是内部或外部命令）。
ESBUILD="${NATIVE}/../../apps/web/node_modules/@esbuild/win32-x64/esbuild.exe"

if [ ! -f "$ESBUILD" ]; then
  echo "找不到 esbuild：$ESBUILD" >&2
  echo "先在 apps/web 下装依赖（npm ci）。" >&2
  exit 1
fi

mkdir -p "${HERE}/web/dist"

"$ESBUILD" "${NATIVE}/web/src/index.tsx" \
  --bundle \
  --format=esm \
  --jsx=automatic \
  --minify \
  --target=es2020 \
  --external:react \
  --external:react-dom \
  --external:react/jsx-runtime \
  --outfile="${NATIVE}/web/dist/index.js"

echo "✅ 已构建 ${NATIVE}/web/dist/index.js"
