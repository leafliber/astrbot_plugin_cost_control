import { execSync } from "node:child_process";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// 版本参数用 git 短哈希（源码未变则构建产物字节级一致），而非 Date.now()：
// 后者每次构建都变，入库产物会与源码产生幽灵漂移（重构建同源码也得提交产物 diff）。
function buildVersion(): string {
  const opts = {
    cwd: fileURLToPath(new URL(".", import.meta.url)),
    stdio: ["ignore", "pipe", "ignore"] as const,
  };
  try {
    const short = execSync("git rev-parse --short HEAD", opts).toString().trim();
    const dirty = execSync(
      "git status --porcelain -- src index.html vite.config.ts",
      opts,
    )
      .toString()
      .trim();
    return dirty ? `${short}-dirty` : short;
  } catch {
    return "0";
  }
}

function preserveBridgeSdk() {
  const BRIDGE = "/api/plugin/page/bridge-sdk.js";
  return {
    name: "preserve-bridge-sdk",
    transformIndexHtml: {
      // order:"post" 确保在 Vite 注入 module script / css link 之后执行，
      // 才能看到并改写这些标签。
      order: "post",
      handler(html: string) {
        // 让产物与原 app.js 版格式一致：
        // 1) 去掉 Vite 生成的 "./" 前缀（dashboard 重写相对路径资源引用，原版用 "app.js"/"style.css"）；
        // 2) 把 app.js module script 移到 bridge 标签之后，保证 window.AstrBotPluginPage
        //    在 React bundle 执行前就绪（module script defer，文本顺序即执行顺序）。
        let out = html;
        // 版本参数（git 短哈希）：源码变化 → 新哈希 → 浏览器强制重新拉取
        // （服务端 no-store 之外的保险），同源码重构建产物保持一致。
        const v = `v=${buildVersion()}`;
        const appScriptMatch = out.match(/<script[^>]*src="\.\/app\.js"[^>]*><\/script>/);
        let appScript = "";
        if (appScriptMatch) {
          appScript = appScriptMatch[0].replace('src="./app.js"', `src="app.js?${v}"`);
          out = out.replace(appScriptMatch[0], "");
        }
        out = out.replace(/href="\.\/style\.css"/g, `href="style.css?${v}"`);
        if (out.includes(BRIDGE)) {
          out = out.replace(
            `<script src="${BRIDGE}"></script>`,
            `<script src="${BRIDGE}"></script>\n    ${appScript}`,
          );
        } else {
          out = out.replace(
            "<body>",
            `<body>\n    <script src="${BRIDGE}"></script>\n    ${appScript}`,
          );
        }
        return out;
      },
    },
  };
}

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  base: "./",
  plugins: [react(), preserveBridgeSdk()],
  build: {
    outDir: fileURLToPath(new URL("../pages/dashboard", import.meta.url)),
    emptyOutDir: true,
    assetsDir: ".",
    target: "esnext",
    cssCodeSplit: false,
    minify: "esbuild",
    sourcemap: false,
    rollupOptions: {
      input: fileURLToPath(new URL("index.html", import.meta.url)),
      output: {
        entryFileNames: "app.js",
        chunkFileNames: "app.js",
        assetFileNames: (info) => {
          const name = info.name || "";
          return name.endsWith(".css") ? "style.css" : "assets/[name][extname]";
        },
        inlineDynamicImports: true,
      },
    },
  },
});
