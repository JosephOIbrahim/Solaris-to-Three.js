// Renders the brand PNGs in docs/assets/ from the HTML sources next to this file:
//
//   logo-lockup-light.png, logo-lockup-dark.png   lockup.html, 2x, transparent
//   social-preview.png                            social-preview.html, 1280 x 640, paper
//
// Run it from the repo root after `npm ci` and `npm i --no-save playwright@1.65.0`:
//
//   node docs/assets/src/render.mjs
//
// The fonts (Fraunces, Archivo and JetBrains Mono, all SIL OFL) load from Google Fonts
// at render time, so this needs the network. Every text element on each page must be
// drawn in its own web font. If one fell back, or the mark did not load, the script
// stops with the reason and writes no file at all.

import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join, relative } from "node:path";
import { writeFileSync } from "node:fs";

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  console.error("render: Playwright is not installed. Run: npm i --no-save playwright@1.65.0");
  process.exit(1);
}

const here = dirname(fileURLToPath(import.meta.url));
const assets = join(here, "..");

const jobs = [
  { source: "lockup.html", query: "?theme=light&capture", out: "logo-lockup-light.png",
    scale: 2, element: "#lockup", transparent: true, mark: "logo-mark-light.svg" },
  { source: "lockup.html", query: "?theme=dark&capture", out: "logo-lockup-dark.png",
    scale: 2, element: "#lockup", transparent: true, mark: "logo-mark-dark.svg" },
  { source: "social-preview.html", query: "", out: "social-preview.png",
    scale: 1, element: "#card", size: [1280, 640], mark: "logo-mark-light.svg" },
];

// Checks run inside the page. Returns a list of problems; empty means ready to capture.
async function readiness(expectedMark) {
  await document.fonts.ready;
  const problems = [];
  const texts = [...document.querySelectorAll("body *:not(script, style)")].filter((el) =>
    el.checkVisibility() &&
    [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim()));
  for (const el of texts) {
    const cs = getComputedStyle(el);
    const family = cs.fontFamily.split(",")[0].trim().replace(/^["']|["']$/g, "");
    const spec = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} "${family}"`;
    let text = el.textContent.trim();
    if (cs.textTransform === "uppercase") text = text.toUpperCase();
    let faces;
    try {
      faces = await document.fonts.load(spec, text);
    } catch (err) {
      problems.push(`font ${spec} failed to download for "${text}" (${err.name}: ${err.message})`);
      continue;
    }
    // check() alone says true for a family that was never declared, so a loaded face is required too.
    const loaded = faces.length > 0 && faces.every((f) => f.status === "loaded");
    if (!loaded || !document.fonts.check(spec, text)) {
      const why = faces.length ? faces.map((f) => `${f.family} ${f.weight} ${f.status}`).join(", ") : "no @font-face matched";
      problems.push(`font ${spec} did not load for "${text}" (${why})`);
    }
  }
  for (const img of document.images) {
    if (!img.complete || img.naturalWidth === 0) problems.push(`image ${img.src} did not load`);
    else if (!img.currentSrc.endsWith(expectedMark)) problems.push(`image is ${img.currentSrc}, expected ${expectedMark}`);
  }
  return problems;
}

function pngSize(buf) {
  if (buf.toString("ascii", 1, 4) !== "PNG") throw new Error("render: capture is not a PNG");
  return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
}

const browser = await chromium.launch();
const rendered = [];
try {
  for (const job of jobs) {
    const page = await browser.newPage({
      deviceScaleFactor: job.scale,
      viewport: job.size ? { width: job.size[0], height: job.size[1] } : { width: 1200, height: 600 },
    });
    const url = pathToFileURL(join(here, job.source)).href + job.query;
    await page.goto(url, { waitUntil: "networkidle" });
    const problems = await page.evaluate(readiness, job.mark);
    if (problems.length) {
      throw new Error(`render: ${job.source}${job.query} is not ready, nothing was written:\n  ${problems.join("\n  ")}`);
    }
    const png = await page.locator(job.element).screenshot({ omitBackground: Boolean(job.transparent) });
    const [w, h] = pngSize(png);
    if (job.size && (w !== job.size[0] || h !== job.size[1])) {
      throw new Error(`render: ${job.out} came out ${w} x ${h}, expected ${job.size.join(" x ")}; nothing was written`);
    }
    rendered.push({ path: join(assets, job.out), png, w, h });
    await page.close();
  }
} finally {
  await browser.close();
}

for (const r of rendered) {
  writeFileSync(r.path, r.png);
  console.log(`render: wrote ${relative(process.cwd(), r.path)} (${r.w} x ${r.h})`);
}
