// Renders the explainer video from index.html next to this file:
//
//   docs/media/explainer.mp4          1920 x 1080, 30 fps, H.264, yuv420p, BT.709, +faststart, no audio
//   docs/media/explainer-poster.png   the title card, 1920 x 1080
//
// Run it from the repo root after `npm ci` and `npm i --no-save playwright@1.65.0`, with ffmpeg
// and ffprobe on PATH:
//
//   node docs/media/explainer/render.mjs                     every frame, then the mp4 and the poster
//   node docs/media/explainer/render.mjs --stills 2,30.5,61  only those frames, as PNGs in a temp folder
//   node docs/media/explainer/render.mjs --keep-frames       keep the frame folder after a full render
//
// The page opens over file://, so no server is needed. The fonts (Fraunces, Archivo and JetBrains
// Mono, all SIL OFL) load from Google Fonts at render time, so this needs the network. Frames go to
// a temp folder outside the repo and are deleted afterwards.
//
// Nothing in docs/media is written unless every check passes: each text is drawn in its own web
// font, each image loaded at its expected size, the page's own timing and layout checks hold, and
// ffprobe reads the encoded file back as expected. If one fails, the script stops with the reason.

import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join, relative } from "node:path";
import { mkdtempSync, rmSync, writeFileSync, copyFileSync, statSync, openSync, readSync, closeSync } from "node:fs";
import { tmpdir } from "node:os";
import { execFileSync } from "node:child_process";

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  console.error("render: Playwright is not installed. Run: npm i --no-save playwright@1.65.0");
  process.exit(1);
}

const here = dirname(fileURLToPath(import.meta.url));
const media = join(here, "..");
const OUT_MP4 = join(media, "explainer.mp4");
const OUT_POSTER = join(media, "explainer-poster.png");
const MAX_BYTES = 8_000_000;
const SIZE = { width: 1920, height: 1080 };
const CRF = 20;

// Every image the page may draw, with its natural size. The mark is SVG, so it has none to check.
const IMAGES = {
  "logo-mark-light.svg": null,
  "01-hydra-ember.png": [1280, 720],
  "02-hydra-gold.png": [1280, 720],
  "02-hydra-chrome.png": [1280, 720],
  "03-hydra-proxy-on.png": [1280, 720],
  "06-phone-usd-cam.png": [390, 844],
};
// The only faces the house style allows.
const FACES = { "Fraunces": "600", "Archivo": "400", "JetBrains Mono": "400" };

const args = process.argv.slice(2);
const stillsAt = args.includes("--stills") ? args[args.indexOf("--stills") + 1] : null;
const keepFrames = args.includes("--keep-frames");

// Runs inside the page. Returns a list of problems; empty means ready to capture.
async function readiness({ images, faces }) {
  await document.fonts.ready;
  const problems = [];
  const frame = document.getElementById("frame");
  const texts = [...frame.querySelectorAll("*")].filter((el) =>
    [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim()));
  if (!texts.length) problems.push("the page has no text; did the script run?");
  // Every text element is checked, hidden scenes included, so a face that would only be
  // fetched later in the video is fetched and checked now.
  for (const el of texts) {
    const cs = getComputedStyle(el);
    const family = cs.fontFamily.split(",")[0].trim().replace(/^["']|["']$/g, "");
    const spec = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} "${family}"`;
    let text = [...el.childNodes].filter((n) => n.nodeType === Node.TEXT_NODE).map((n) => n.textContent).join("").trim();
    if (cs.textTransform === "uppercase") text = text.toUpperCase();
    if (faces[family] !== cs.fontWeight) {
      problems.push(`"${text}" is set in ${family} ${cs.fontWeight}; the house faces are ${Object.entries(faces).map(([f, w]) => f + " " + w).join(", ")}`);
      continue;
    }
    for (const ch of text) {
      if (ch.codePointAt(0) > 0xff) problems.push(`"${text}" has ${ch} (U+${ch.codePointAt(0).toString(16)}), outside Latin-1; it may fall back to another font`);
    }
    let loaded;
    try {
      loaded = await document.fonts.load(spec, text);
    } catch (err) {
      problems.push(`font ${spec} failed to download for "${text}" (${err.name}: ${err.message})`);
      continue;
    }
    // check() alone says true for a family that was never declared, so a loaded face is required too.
    const ok = loaded.length > 0 && loaded.every((f) => f.status === "loaded");
    if (!ok || !document.fonts.check(spec, text)) {
      const why = loaded.length ? loaded.map((f) => `${f.family} ${f.weight} ${f.status}`).join(", ") : "no @font-face matched";
      problems.push(`font ${spec} did not load for "${text}" (${why})`);
    }
  }
  const seen = new Set();
  for (const img of document.images) {
    const name = decodeURIComponent(img.currentSrc || img.src).split("/").pop();
    seen.add(name);
    if (!(name in images)) { problems.push(`unexpected image ${name}`); continue; }
    try {
      await img.decode();
    } catch {
      problems.push(`image ${name} did not decode`);
      continue;
    }
    if (!img.complete || img.naturalWidth === 0) problems.push(`image ${name} did not load`);
    const want = images[name];
    if (want && (img.naturalWidth !== want[0] || img.naturalHeight !== want[1])) {
      problems.push(`image ${name} is ${img.naturalWidth} x ${img.naturalHeight}, expected ${want.join(" x ")}`);
    }
    // Never upscale a product shot: it must be drawn at its own size or smaller.
    if (want && img.width > want[0] + 0.5) problems.push(`image ${name} is drawn ${img.width} px wide, wider than its own ${want[0]} px`);
  }
  for (const name of Object.keys(images)) if (!seen.has(name)) problems.push(`image ${name} is not on the page`);
  if (!window.explainer) problems.push("window.explainer is missing; the page script stopped");
  else problems.push(...window.explainer.check());
  return problems;
}

function pngSize(buf) {
  if (buf.toString("ascii", 1, 4) !== "PNG") throw new Error("render: capture is not a PNG");
  return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
}

// The top-level MP4 boxes in order, to confirm +faststart put moov before mdat.
function topBoxes(path) {
  const fd = openSync(path, "r");
  const size = statSync(path).size;
  const boxes = [];
  const head = Buffer.alloc(16);
  try {
    for (let pos = 0; pos < size && boxes.length < 32;) {
      readSync(fd, head, 0, 16, pos);
      let len = head.readUInt32BE(0);
      const type = head.toString("latin1", 4, 8);
      if (len === 1) len = Number(head.readBigUInt64BE(8));
      else if (len === 0) len = size - pos;
      if (len < 8) break;
      boxes.push(type);
      pos += len;
    }
  } finally {
    closeSync(fd);
  }
  return boxes;
}

// The mean colour of a small square of one frame, decoded from the mp4 the way a player does.
function sampleColor(mp4, t, x, y, n = 6) {
  const raw = execFileSync("ffmpeg", ["-v", "error", "-ss", String(t), "-i", mp4, "-frames:v", "1",
    "-vf", `crop=${n}:${n}:${Math.round(x - n / 2)}:${Math.round(y - n / 2)},format=rgb24`,
    "-f", "rawvideo", "-"]);
  const sum = [0, 0, 0];
  for (let i = 0; i < raw.length; i += 3) for (let c = 0; c < 3; c++) sum[c] += raw[i + c];
  return sum.map((v) => Math.round(v / (raw.length / 3)));
}
const hex = (c) => "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase();
const near = (a, b, tol) => a.every((v, i) => Math.abs(v - b[i]) <= tol);

const browser = await chromium.launch();
let frames = null;
try {
  const page = await browser.newPage({ viewport: SIZE, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
  page.on("requestfailed", (req) => errors.push(`${req.url()} failed: ${req.failure()?.errorText}`));
  await page.goto(pathToFileURL(join(here, "index.html")).href + "?render", { waitUntil: "networkidle" });
  if (errors.length) throw new Error(`render: the page reported errors, nothing was written:\n  ${errors.join("\n  ")}`);

  const problems = await page.evaluate(readiness, { images: IMAGES, faces: FACES });
  if (problems.length) throw new Error(`render: the page is not ready, nothing was written:\n  ${problems.join("\n  ")}`);
  const info = await page.evaluate(() => {
    const { DURATION, FRAMES, FPS, POSTER_AT, table } = window.explainer;
    // The sun is the upper half of the mark's 16-unit circle, so this point is inside it.
    const m = document.querySelector(".title .mark").getBoundingClientRect();
    return { DURATION, FRAMES, FPS, POSTER_AT, table: table(), sun: [m.left + m.width / 2, m.top + m.height * 5.5 / 16] };
  });
  console.log(`render: ${info.DURATION} s, ${info.FRAMES} frames at ${info.FPS} fps`);
  console.table(info.table);

  const shoot = async (t, path) => {
    await page.evaluate((s) => window.seek(s), t);
    return page.screenshot({ path, type: "png" });
  };

  if (stillsAt) {
    const dir = mkdtempSync(join(tmpdir(), "solaris-explainer-stills-"));
    for (const t of stillsAt.split(",").map(Number)) {
      const path = join(dir, `still-${t.toFixed(2).padStart(6, "0")}.png`);
      await shoot(t, path);
      console.log(`render: wrote ${path}`);
    }
  } else {
    frames = mkdtempSync(join(tmpdir(), "solaris-explainer-frames-"));
    const started = Date.now();
    for (let i = 0; i < info.FRAMES; i++) {
      await shoot(i / info.FPS, join(frames, `f${String(i).padStart(5, "0")}.png`));
      if ((i + 1) % 300 === 0 || i + 1 === info.FRAMES) {
        console.log(`render: [frames ${i + 1} of ${info.FRAMES}, ${Math.round((Date.now() - started) / 1000)} s]`);
      }
    }
    const poster = await shoot(info.POSTER_AT);
    const [pw, ph] = pngSize(poster);
    if (pw !== SIZE.width || ph !== SIZE.height) throw new Error(`render: the poster came out ${pw} x ${ph}; nothing was written`);

    // RGB to BT.709 limited range, tagged as such, so browsers decode the colours that were drawn.
    // ffmpeg takes the tags from the frames, and scale sets only the matrix and range, so
    // setparams names the primaries and transfer too.
    const mp4 = join(frames, "explainer.mp4");
    execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y",
      "-framerate", String(info.FPS), "-i", join(frames, "f%05d.png"),
      "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p," +
        "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
      "-c:v", "libx264", "-preset", "slow", "-crf", String(CRF), "-profile:v", "high",
      "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
      "-movflags", "+faststart", "-an", mp4], { stdio: "inherit" });

    // Read it back the way a player would, and refuse to publish anything that is off.
    const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-count_frames",
      "-show_format", "-show_streams", "-of", "json", mp4]).toString());
    const bad = [];
    const v = probe.streams.filter((s) => s.codec_type === "video");
    if (probe.streams.length !== 1 || v.length !== 1) bad.push(`expected one video stream and nothing else, found ${probe.streams.map((s) => s.codec_type).join(", ")}`);
    const s = v[0] || {};
    const expect = { codec_name: "h264", pix_fmt: "yuv420p", width: SIZE.width, height: SIZE.height,
      r_frame_rate: `${info.FPS}/1`, nb_read_frames: String(info.FRAMES), color_range: "tv",
      color_space: "bt709", color_primaries: "bt709", color_transfer: "bt709" };
    for (const [k, want] of Object.entries(expect)) if (s[k] !== want) bad.push(`${k} is ${s[k]}, expected ${want}`);
    const duration = Number(probe.format.duration);
    if (Math.abs(duration - info.FRAMES / info.FPS) > 0.05) bad.push(`duration is ${duration} s, expected ${info.FRAMES / info.FPS} s`);
    const bytes = Number(probe.format.size);
    if (bytes >= MAX_BYTES) bad.push(`the file is ${bytes} bytes, over the ${MAX_BYTES} byte limit`);
    const boxes = topBoxes(mp4);
    if (boxes.indexOf("moov") < 0 || boxes.indexOf("moov") > boxes.indexOf("mdat")) bad.push(`moov is not before mdat (${boxes.join(" ")}), so +faststart did not apply`);
    const paper = sampleColor(mp4, info.POSTER_AT, 48, 48);
    const sun = sampleColor(mp4, info.POSTER_AT, info.sun[0], info.sun[1]);
    if (!near(paper, [0xFA, 0xF8, 0xF4], 3)) bad.push(`paper decodes as ${hex(paper)}, expected #FAF8F4`);
    if (!near(sun, [0xC8, 0x32, 0x1A], 8)) bad.push(`the sun decodes as ${hex(sun)}, expected #C8321A`);
    if (bad.length) throw new Error(`render: the encoded file failed its checks, nothing was written:\n  ${bad.join("\n  ")}`);

    copyFileSync(mp4, OUT_MP4);
    writeFileSync(OUT_POSTER, poster);
    console.log(`render: wrote ${relative(process.cwd(), OUT_MP4)} (${(bytes / 1e6).toFixed(2)} MB, ${duration.toFixed(2)} s, ` +
      `${s.codec_name} ${s.profile} ${s.pix_fmt} ${s.width} x ${s.height} at ${s.r_frame_rate}, ` +
      `${s.color_space}/${s.color_primaries}/${s.color_transfer} ${s.color_range}, ${boxes.join(" ")})`);
    console.log(`render: decoded paper ${hex(paper)}, decoded sun ${hex(sun)}`);
    console.log(`render: wrote ${relative(process.cwd(), OUT_POSTER)} (${pw} x ${ph}, the frame at ${info.POSTER_AT} s)`);
  }
} catch (err) {
  console.error(err.message);
  process.exitCode = 1;
} finally {
  await browser.close();
  if (frames && !keepFrames) rmSync(frames, { recursive: true, force: true });
  else if (frames) console.log(`render: frames kept in ${frames}`);
}
