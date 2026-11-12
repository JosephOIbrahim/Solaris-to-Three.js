// Minimal static server with the two headers the threaded OpenUSD WASM needs.
// This is the whole "server side" of the pipeline: static files + isolation headers.
//
//   npm start   (node server/serve.mjs)   -> http://127.0.0.1:5180
//
//   /           -> viewer/        the page (index.html, main.js)
//   /fixtures/  -> fixtures/      test stages that ship with the repo
//   /stages/    -> stages/        your own USD ROP exports (git-ignored)
//   /vendor/    -> node_modules/  three r187, @needle-tools/usd, @needle-tools/materialx
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url)); // repo root
const port = Number(process.env.PORT || 5180);

const mounts = [
  ["/vendor/", join(root, "node_modules")],
  ["/fixtures/", join(root, "fixtures")],
  ["/stages/", join(root, "stages")],
  ["/", join(root, "viewer")],
];

const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".glb": "model/gltf-binary",
  ".gltf": "model/gltf+json",
};

createServer(async (req, res) => {
  // Cross-origin isolation: unlocks SharedArrayBuffer for the pthread workers.
  res.setHeader("Cross-Origin-Opener-Policy", "same-origin");
  res.setHeader("Cross-Origin-Embedder-Policy", "require-corp");
  try {
    const url = new URL(req.url, "http://x");
    let pathname = decodeURIComponent(url.pathname);
    if (pathname.endsWith("/")) pathname += "index.html";
    const [prefix, dir] = mounts.find(([p]) => pathname.startsWith(p));
    const file = normalize(join(dir, pathname.slice(prefix.length)));
    if (!file.startsWith(dir)) throw Object.assign(new Error("forbidden"), { code: 403 });
    const info = await stat(file);
    if (!info.isFile()) throw Object.assign(new Error("not a file"), { code: 404 });
    res.writeHead(200, {
      "Content-Type": types[extname(file)] || "application/octet-stream",
      "Content-Length": info.size,
      "Cache-Control": "no-store",
    });
    res.end(await readFile(file));
  } catch (err) {
    res.writeHead(err.code === 403 ? 403 : 404, { "Content-Type": "text/plain" });
    res.end(String(err.message || err));
  }
}).listen(port, "127.0.0.1", () => console.log(`serving on http://127.0.0.1:${port}`));
