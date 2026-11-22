// HTTP security and header contract tests for server/serve.mjs.
// Spawns server/serve.mjs on a dedicated ephemeral port, runs checks, and shuts it down.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import http from "node:http";
import { fileURLToPath } from "node:url";

const SERVER = fileURLToPath(new URL("../../server/serve.mjs", import.meta.url));
const THREE_MOD = fileURLToPath(new URL("../../node_modules/three/build/three.module.js", import.meta.url));
const PORT = Number(process.env.TEST_PORT || 5197);

function rawRequest(path, { method = "GET", host = `127.0.0.1:${PORT}` } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port: PORT, path, method, headers: { Host: host } }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks).toString("utf-8"),
      }));
    });
    req.on("error", reject);
    req.end();
  });
}

async function waitForServer(timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await rawRequest("/");
      if (r.status === 200) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("server/serve.mjs did not start in time");
}

const proc = spawn(process.execPath, [SERVER], {
  env: { ...process.env, PORT: String(PORT) },
  stdio: ["ignore", "pipe", "pipe"],
});

try {
  await waitForServer();

  const home = await rawRequest("/");
  const head = await rawRequest("/", { method: "HEAD" });
  const post = await rawRequest("/", { method: "POST" });
  const badHost = await rawRequest("/", { host: "evil.example:5197" });
  const badUri = await rawRequest("/%E0%A4%A");
  const nullByte = await rawRequest("/%00index.html");
  const travRoot = await rawRequest("/%2e%2e/package.json");
  const travFix = await rawRequest("/fixtures/%2e%2e/package.json");
  const travBackslash = await rawRequest("/fixtures/..%5cpackage.json");
  const travDir = await rawRequest("/fixtures/stage");
  const vendorBlocked = await rawRequest("/vendor/playwright/package.json");
  const vendorOk = await rawRequest("/vendor/three/build/three.module.js", { method: "HEAD" });
  const x3d = await rawRequest("/fixtures/stage-flat/hero.x3d", { method: "HEAD" });
  const glb = await rawRequest("/fixtures/stage-flat/hero.glb", { method: "HEAD" });
  const hdr = await rawRequest("/fixtures/stage/env/sky.hdr", { method: "HEAD" });

  const checks = [
    ["GET / returns 200 with COOP/COEP/CORP/nosniff",
      home.status === 200
      && home.headers["cross-origin-opener-policy"] === "same-origin"
      && home.headers["cross-origin-embedder-policy"] === "require-corp"
      && home.headers["cross-origin-resource-policy"] === "same-origin"
      && home.headers["x-content-type-options"] === "nosniff"
      && home.headers["cache-control"] === "no-store"],
    ["HEAD / returns 200 with empty body", head.status === 200 && head.body.length === 0],
    ["POST / returns 405 with Allow header", post.status === 405 && post.headers.allow === "GET, HEAD"],
    ["non-loopback Host header returns 403", badHost.status === 403],
    ["malformed percent-encoding returns 400", badUri.status === 400],
    ["null byte in path returns 400", nullByte.status === 400],
    ["root traversal /%2e%2e/package.json blocked (403/404, no leak)",
      (travRoot.status === 403 || travRoot.status === 404) && !travRoot.body.includes('"solaris-to-three"')],
    ["mount traversal /fixtures/%2e%2e/package.json blocked (403/404, no leak)",
      (travFix.status === 403 || travFix.status === 404) && !travFix.body.includes('"solaris-to-three"')],
    ["encoded backslash traversal /fixtures/..%5cpackage.json blocked (403/404, no leak)",
      (travBackslash.status === 403 || travBackslash.status === 404) && !travBackslash.body.includes('"solaris-to-three"')],
    ["directory path /fixtures/stage returns 404 (not 500)", travDir.status === 404],
    ["unlisted /vendor/ package returns 403", vendorBlocked.status === 403],
    [existsSync(THREE_MOD)
      ? "allowed /vendor/three caches public max-age=3600 (T10)"
      : "allowed /vendor/three passes allowlist (404 without node_modules)",
      existsSync(THREE_MOD)
        ? (vendorOk.status === 200 && vendorOk.headers["cache-control"] === "public, max-age=3600")
        : vendorOk.status === 404],
    ["MIME types for .x3d, .glb, .hdr match spec",
      x3d.status === 200 && x3d.headers["content-type"] === "model/x3d+xml"
      && glb.status === 200 && glb.headers["content-type"] === "model/gltf-binary"
      && hdr.status === 200 && hdr.headers["content-type"] === "image/vnd.radiance"],
  ];

  for (const [name, ok] of checks) {
    console.log(`${ok ? "ok  " : "FAIL"}  ${name}`);
  }
  if (checks.some(([, ok]) => !ok)) process.exitCode = 1;
} finally {
  proc.kill();
}
