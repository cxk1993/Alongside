import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import pkg from "./package.json";

// ★ 2026-09-27（主人「正式版 1.0.0」）：把版本号 + **构建时刻**注入前端。
//   构建时刻是关键 —— 它能一眼区分「跑的是新版还是被 PWA 缓存的旧版」。
const BUILD_AT = new Date().toISOString().slice(0, 16).replace("T", " ");

const ROOT = import.meta.dirname;

/**
 * ★ r5 · P2：共享 React 运行时（kernel-runtime）+ import map。
 *
 * 第三方插件 UI 是运行时加载的原生 ESM，其裸说明符 `import React from "react"` 必须
 * 解析到**宿主自己的** React，否则两套 React → hooks 崩。做法：
 *   - manualChunks 把 react / react-dom / scheduler 归进**同一个**固定名 chunk
 *     `kernel-runtime.js`（不入 hash），主应用与 facade 共用它 → 单实例从结构上成立；
 *   - 四个 facade（src/kernel/runtime/*.js）作为独立入口产出固定名 kernel-*.js，
 *     只 re-export 对应包；
 *   - index.html 注入 import map，把裸 `react` / `react-dom` / `react/jsx-runtime` /
 *     `react-dom/client` 指到 facade。dev 指源码（Vite 现转译）、prod 指固定名产物。
 * 判据：离线可用（全同源、无 CDN）、插件侧零适配、双 React 结构上不可能、dev/prod 两态可跑。
 */
const RUNTIME_FACADES = {
  "kernel-react": "src/kernel/runtime/react.js",
  "kernel-react-dom": "src/kernel/runtime/react-dom.js",
  "kernel-jsx-runtime": "src/kernel/runtime/jsx-runtime.js",
  "kernel-react-dom-client": "src/kernel/runtime/react-dom-client.js",
} as const;

// import map 的两态目标：dev 走 Vite 源码图，prod 走固定名构建产物。
const IMPORT_MAP_PROD: Record<string, string> = {
  react: "/assets/kernel-react.js",
  "react-dom": "/assets/kernel-react-dom.js",
  "react/jsx-runtime": "/assets/kernel-jsx-runtime.js",
  "react-dom/client": "/assets/kernel-react-dom-client.js",
};
const IMPORT_MAP_DEV: Record<string, string> = {
  react: "/src/kernel/runtime/react.js",
  "react-dom": "/src/kernel/runtime/react-dom.js",
  "react/jsx-runtime": "/src/kernel/runtime/jsx-runtime.js",
  "react-dom/client": "/src/kernel/runtime/react-dom-client.js",
};

function kernelRuntimeImportMap() {
  let isBuild = false;
  return {
    name: "alongside-kernel-runtime-importmap",
    configResolved(config: { command: string }) {
      isBuild = config.command === "build";
    },
    transformIndexHtml: {
      // 必须早于任何 module script（含 /@vite/client）→ 用 pre 并插在 <head> 开头。
      order: "pre" as const,
      handler(html: string) {
        const imports = isBuild ? IMPORT_MAP_PROD : IMPORT_MAP_DEV;
        const tag = `<script type="importmap">${JSON.stringify({ imports })}</script>`;
        return html.replace("<head>", `<head>\n    ${tag}`);
      },
    },
  };
}

/**
 * ★ r5：dev/preview 同源于后端。前端所有 API 走根相对路径（/api/…、/plugin-assets/…），
 * 开发态把这两个前缀代理到本机后端，登录与插件资产才可达（否则 5173 直连 /api 会 404）。
 * 生产由 nginx 反代（deploy/nginx/alongside.conf，归 bidiu），此处只管 dev/preview。
 * 目标固定为 task.py dev 的后端端口（README 口径：前端 5173 / 后端 8000）。
 */
const PROXY_TARGET = "http://localhost:8000";
const devProxy = {
  "/api": { target: PROXY_TARGET, changeOrigin: true },
  "/plugin-assets": { target: PROXY_TARGET, changeOrigin: true },
  "/healthz": { target: PROXY_TARGET, changeOrigin: true },
};

export default defineConfig({
  plugins: [react(), kernelRuntimeImportMap()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    __BUILD_AT__: JSON.stringify(BUILD_AT),
  },
  resolve: {
    alias: {
      "@": import.meta.dirname + "/src",
    },
  },
  server: {
    port: 5173,
    // 端口被占时直接报错，不要偷偷换端口 —— 否则主人按文档访问会打不开
    strictPort: true,
    proxy: devProxy,
  },
  preview: {
    port: 4173,
    proxy: devProxy,
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    rollupOptions: {
      // 主入口 index.html + 四个共享运行时 facade（固定名，供 import map 指向）。
      input: {
        index: ROOT + "/index.html",
        ...Object.fromEntries(
          Object.entries(RUNTIME_FACADES).map(([name, rel]) => [name, `${ROOT}/${rel}`]),
        ),
      },
      // ★ r5：facade 入口的导出**必须原样保留** —— 它们在包内没有消费者
      //   （只在运行时经 import map 被插件模块 import），默认的入口签名裁剪会把
      //   它们剥成光秃秃的 `import "./kernel-runtime.js"`，插件侧拿不到 React。
      preserveEntrySignatures: "strict",
      output: {
        // react/react-dom/scheduler 归进同一固定名 chunk → 宿主与插件单实例。
        manualChunks(id) {
          if (
            id.includes("node_modules/react/") ||
            id.includes("node_modules/react-dom/") ||
            id.includes("node_modules/scheduler/")
          ) {
            return "kernel-runtime";
          }
        },
        // facade 入口固定名（import map 依赖稳定 URL）；其余入口照常带 hash。
        entryFileNames(chunkInfo) {
          return chunkInfo.name.startsWith("kernel-")
            ? "assets/[name].js"
            : "assets/[name]-[hash].js";
        },
        // kernel-runtime 固定名（决策要求不入 hash）；其余 chunk 照常带 hash。
        chunkFileNames(chunkInfo) {
          return chunkInfo.name === "kernel-runtime"
            ? "assets/kernel-runtime.js"
            : "assets/[name]-[hash].js";
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    // ★ 全局隔离：zustand store 是模块级单例，--singleFork 下跨测试文件共享。
    //   某个测试 setState 造了假模块却不清理，就会漏到后面所有测试里
    //   （实测：WindowFrame.caps 的 `@/apps/__t22_none__` 让全量跑 128 项失败、单跑全绿）。
    setupFiles: ["./src/test-setup.ts"],
    // ★ 必须开 globals：@testing-library/react 的自动 cleanup 依赖全局 afterEach，
    //   关掉的话测试之间不会 unmount，上一个测试的 DOM 会残留，
    //   表现为 "Found multiple elements with ..." 这类假失败。
    globals: true,
  },
});
