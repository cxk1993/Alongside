/**
 * 最小 node:fs 形状声明 —— 仅供 collabLayout.test.ts 读样式文本用。
 *
 * 为什么不用 @types/node：本仓库刻意不装（src/vite-env.d.ts 有说明），
 * 为一个测试引整包 Node 类型不值得。.d.ts 里的 declare module 是环境声明，
 * 写在模块文件里会被 TS 当成「模块扩充」而报 TS2664。
 */
declare module "node:fs" {
  export function readFileSync(path: string, encoding: string): string;
}
