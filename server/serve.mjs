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
import { readFile, realpath, stat } from "node:fs/promises";
import { extname, isAbsolute, join, normalize, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url)); // repo root
const port = Number(process.env.PORT || 5180);

const mounts = [
  ["/vendor/", join(root, "node_modules")],
  ["/fixtures/", join(root, "fixtures")],
  ["/stages/", join(root, "stages")],
  ["/", join(root, "viewer")],
];

const realDirs = new Map();
async function getRealDir(dir) {
  let cached = realDirs.get(dir);
  if (!cached) {
    cached = await realpath(dir);
    realDirs.set(dir, cached);
  }
  return cached;
}

const ALLOWED_HOST = /^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/i;
const VENDOR_ALLOW = /^(three|three-r185|@needle-tools)\//;
const httpErr = (status, publicMessage) => Object.assign(new Error(publicMessage), { status, publicMessage });

const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".hdr": "image/vnd.radiance",
  ".exr": "image/x-exr",
  ".glb": "model/gltf-binary",
  ".gltf": "model/gltf+json",
  ".x3d": "model/x3d+xml",
  ".usda": "text/plain; charset=utf-8",
  ".usdc": "application/octet-stream",
  ".usdz": "model/vnd.usdz+zip",
  ".mtlx": "application/xml; charset=utf-8",
};

createServer(async (req, res) => {
  // Cross-origin isolation + MIME/resource hardening on every response.
  res.setHeader("Cross-Origin-Opener-Policy", "same-origin");
  res.setHeader("Cross-Origin-Embedder-Policy", "require-corp");
  res.setHeader("Cross-Origin-Resource-Policy", "same-origin");
  res.setHeader("X-Content-Type-Options", "nosniff");
  try {
    if (req.method !== "GET" && req.method !== "HEAD") {
      throw httpErr(405, "method not allowed");
    }
    if (req.headers.host && !ALLOWED_HOST.test(req.headers.host)) {
      throw httpErr(403, "forbidden");
    }
    let pathname;
    try {
      const url = new URL(req.url || "/", "http://127.0.0.1");
      if (url.origin !== "http://127.0.0.1") throw new Error("bad origin");
      pathname = decodeURIComponent(url.pathname);
    } catch {
      throw httpErr(400, "bad request");
    }
    if (pathname.includes("\0")) throw httpErr(400, "bad request");
    if (pathname.endsWith("/")) pathname += "index.html";

    const mount = mounts.find(([p]) => pathname.startsWith(p));
    if (!mount) throw httpErr(404, "not found");
    const [prefix, dir] = mount;

    const file = normalize(join(dir, pathname.slice(prefix.length)));
    const rel = relative(dir, file).replaceAll("\\", "/");
    if (rel === ".." || rel.startsWith("../") || isAbsolute(rel)) {
      throw httpErr(403, "forbidden");
    }
    if (prefix === "/vendor/" && !VENDOR_ALLOW.test(rel)) {
      throw httpErr(403, "forbidden");
    }

    const realDir = await getRealDir(dir);
    const realFile = await realpath(file);
    if (realFile !== realDir && !realFile.startsWith(realDir + sep)) {
      throw httpErr(403, "forbidden");
    }
    if (prefix === "/vendor/") {
      const realRel = relative(realDir, realFile).replaceAll("\\", "/");
      if (!VENDOR_ALLOW.test(realRel)) throw httpErr(403, "forbidden");
    }

    const info = await stat(realFile);
    if (!info.isFile()) throw httpErr(404, "not found");
    const data = req.method === "HEAD" ? null : await readFile(realFile);
    res.writeHead(200, {
      "Content-Type": types[extname(realFile).toLowerCase()] || "application/octet-stream",
      "Content-Length": data ? data.byteLength : info.size,
      "Cache-Control": prefix === "/vendor/" ? "public, max-age=3600" : "no-store",
    });
    res.end(data ?? undefined);
  } catch (err) {
    if (!res.headersSent) {
      const status = err.status || (err.code === "ENOENT" || err.code === "ENOTDIR" ? 404 : err.code === "EACCES" ? 403 : 500);
      const msg = err.publicMessage || (status === 400 ? "bad request" : status === 403 ? "forbidden" : status === 404 ? "not found" : status === 405 ? "method not allowed" : "internal error");
      const headers = { "Content-Type": "text/plain; charset=utf-8" };
      if (status === 405) headers.Allow = "GET, HEAD";
      res.writeHead(status, headers);
      res.end(req.method === "HEAD" ? undefined : msg);
    } else if (!res.writableEnded) {
      res.end();
    }
  }
}).listen(port, "127.0.0.1", () => console.log(`serving on http://127.0.0.1:${port}`));
