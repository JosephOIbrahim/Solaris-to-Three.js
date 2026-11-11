<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-lockup-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-lockup-light.png">
  <img src="docs/assets/logo-lockup-light.png" width="512" alt="Solaris to three.js, OpenUSD in the browser">
</picture>

Publish Houdini Solaris output on the web. Real OpenUSD composes your stage inside a browser tab, and three.js r187 draws it as a Hydra render delegate, the same way Karma does in Houdini.

Reading path: read Status to Quickstart first, five sections and about three minutes, then run the viewer; the rest is reference, about six minutes more, for when your own export is ready.

---

### <img src="docs/assets/swatch-ochre.svg" width="11" height="11" alt=""> Status

Version 0.1.3. It has run on Joe's RTX 4090 under Windows (T02, see [CHANGELOG 0.1.2](CHANGELOG.md)). Checking a real Karma export (T03) is still open. The Needle runtime is PolyForm Noncommercial, so it is for **local evaluation only**.

---

### <img src="docs/assets/swatch-stone.svg" width="11" height="11" alt=""> See it first

<a href="docs/media/explainer.mp4"><img src="docs/media/explainer-poster.png" width="640" alt="Poster frame of the explainer video. Select it to open the video."></a>

Select the poster to open the explainer video.

<img src="docs/images/variants.png" width="320" alt="The fixture stage in the browser: one asset, three look variants switched live">

The still is the fixture stage in the browser: one asset, three look variants switched live.

---

### <img src="docs/assets/swatch-stone.svg" width="11" height="11" alt=""> The idea

**three.js becomes another Hydra render delegate.** A Solaris network produces a USD stage, and Houdini's renderers read that stage through Hydra. Needle compiled OpenUSD 26.05 to WebAssembly and wrote a Hydra delegate that outputs three.js objects. So the browser runs the same composition engine Houdini does: payloads, variants, purpose and instancing are resolved by USD itself, not guessed by a converter.

In plain words, Hydra works like a lens mount. Karma is one lens on it, and this project makes three.js another lens on the same mount, inside a browser tab. The camera body, your USD stage, stays the same, so nothing has to convert your scene before the browser can show it.

---

### <img src="docs/assets/swatch-stone.svg" width="11" height="11" alt=""> Two small pieces close the gaps

A lighting bridge turns the dome light into an environment map and converts USD light units into three.js units. It reads `DomeLight_1`, the schema Houdini 22's Dome Light LOP writes, as well as the older `DomeLight`, and it orients the dome by its transform and its `poleAxis`.

A **web census** checks an export before you open it, and tells you in one word whether it will load. It also catches any file outside the export folder, which loads in Houdini and 404s in the browser.

In plain words, the census is a pilot's walk-around before take-off. It reads your export the way the browser will and flags what only works on your workstation, such as a `.rat` texture or a `../` path, so you find out at your desk rather than from a grey material or a 404 on the web.

---

### <img src="docs/assets/swatch-moss.svg" width="11" height="11" alt=""> Quickstart

You need Node 22 or newer and a desktop Chrome or Edge.

```
git clone https://github.com/JosephOIbrahim/Solaris-to-Three.js.git
cd Solaris-to-Three.js
npm ci
npm start
```

Open http://127.0.0.1:5180. The status line reports the three.js version, whether the page is cross-origin isolated and how long each stage took.

| URL | What you get |
|:---|:---|
| `/` | The fixture stage on three r187 with MaterialX 1.8.0 |
| `/?three=185` | The same page on three r185, for comparison |
| `/?clean` | Hides the HUD and status line, for screenshots |
| `/?stage=/stages/<name>/<root>.usda` | Your own export, from `stages/<name>/` |

---

### <img src="docs/assets/swatch-moss.svg" width="11" height="11" alt=""> Your own Solaris export

01. On the USD ROP, write the root layer, payloads and textures into one folder with relative paths. The settings and output processors are in [docs/export.md](docs/export.md).

02. Check it: `hython tools/web_census.py <folder>/<root>.usda`. Fix anything under **FIX FIRST**. The card is [docs/census.md](docs/census.md).

03. Copy the folder to `stages/<name>/`. Everything in `stages/` except the placeholder is git-ignored, so client work never lands in the repo.

04. Open `/?stage=/stages/<name>/<root>.usda` and read the status line. It names every prim whose material fell back to the grey default.

---

### <img src="docs/assets/swatch-stone.svg" width="11" height="11" alt=""> How it works

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "Archivo, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica Neue, Helvetica, Arial, Noto Sans, Liberation Sans, DejaVu Sans", "fontSize": "16px", "primaryColor": "#FAF8F4", "primaryTextColor": "#2E2B28", "primaryBorderColor": "#927D70", "secondaryColor": "#F1ECE4", "tertiaryColor": "#F1ECE4", "lineColor": "#927D70", "arrowheadColor": "#927D70", "textColor": "#83776D", "nodeTextColor": "#2E2B28", "clusterBkg": "#F1ECE4", "clusterBorder": "#927D70", "titleColor": "#2E2B28", "edgeLabelBackground": "#FAF8F4", "noteBkgColor": "#F1ECE4", "noteTextColor": "#2E2B28", "noteBorderColor": "#927D70"}}}%%
flowchart TB
  rop["Solaris LOP network, USD ROP"]:::accent --> census{"web census"}:::ochre
  census -->|FIX FIRST| fix["a file outside the export folder, or another blocker: fix it and re-export"]:::clay
  fix -.-> rop
  census -->|"GO, or GO, WITH NOTES"| copy["copy the folder to stages/name/"]:::moss
  copy --> server["serve.mjs: static files plus COOP and COEP headers"]:::umber
  server --> usd["OpenUSD 26.05 in WebAssembly composes the stage"]:::stone
  usd --> delegate["Needle's three.js Hydra delegate, MaterialX 1.8.0"]
  delegate --> scene["three.js r187 scene, WebGL"]
  bridge["lighting bridge: reads DomeLight_1, orients the dome by its transform and poleAxis, converts light units to candela"]:::stone --> scene
  classDef stone fill:#DEE4E5,stroke:#7096A7,stroke-width:1.5px,color:#2E2B28
  classDef moss fill:#E5E8DC,stroke:#91A87B,stroke-width:1.5px,color:#2E2B28
  classDef ochre fill:#EBE2D2,stroke:#AE8B4C,stroke-width:1.5px,color:#2E2B28
  classDef clay fill:#EADAD4,stroke:#AA6455,stroke-width:1.5px,color:#2E2B28
  classDef umber fill:#E5DFDA,stroke:#927D70,stroke-width:1.5px,color:#2E2B28
  classDef accent fill:#C8321A,stroke:#C8321A,stroke-width:1.5px,color:#FAF8F4
```

**The server matters more than it looks.** The OpenUSD build is threaded and needs `SharedArrayBuffer`, which browsers only allow on a page sent with `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp`. Those two headers make the page cross-origin isolated, and any host you deploy to must send both.

In plain words, the two headers are a clean-room seal. The runtime's threads share one workbench of memory, and a browser only allows a shared workbench in a page that has sealed itself off from other sites. Without the seal, the runtime refuses to start, and the page tells you it is not cross-origin isolated.

The full walk-through is in [docs/architecture.md](docs/architecture.md).

---

### <img src="docs/assets/swatch-umber.svg" width="11" height="11" alt=""> Tools

**Start here:** `tools/web_census.py` for every export, `tools/build_router.py` when you plan the next tasks, and `tools/release.py` when you ship a version.

| Tool | What it answers | Needs |
|:---|:---|:---|
| `tools/web_census.py` | Can this export go to the web? | `pxr`: hython, or `pip install usd-core` |
| `tools/build_router.py` | Which build tasks run in a cloud agent, at the desk, or as Joe's decision? See [docs/router.md](docs/router.md). | Python 3 |
| `tools/release.py` | Is this version ready to release? It checks, then creates the GitHub release. The loop is in [docs/workflow.md](docs/workflow.md). | Python 3, git, a signed-in `gh` |
| `tools/jev.py` | The shared door to TypeSafe's Jev, used by the census and the router. Standard library only; fails open. | Python 3 |
| `tools/make_stage.py` | Rebuilds the Solaris-shaped fixture stage | `pxr`, `numpy` |
| `tools/make_dirty_stage.py` | Rebuilds the fixture with eight planted problems | `pxr` |
| `tools/make_escape_stage.py` | Rebuilds the fixture that points outside its export folder | `pxr` |

Jev is optional everywhere. Set `TYPESAFE_API_KEY` in your environment to turn it on; without it, the census and the router still run and say so.

---

### <img src="docs/assets/swatch-umber.svg" width="11" height="11" alt=""> Repo map

**Start here:** `viewer/main.js` for the viewer, `server/serve.mjs` for the headers, and `stages/` for your own exports.

<details>
<summary>The full map, 13 entries</summary>

```
server/serve.mjs          static server: COOP/COEP headers, four mounts, no traversal
viewer/index.html         import map (no bundler) and the HUD
viewer/main.js            Hydra route, USDLoader route, lighting bridge, USD camera, variants
tools/                    census, router, Jev client, fixture builders
fixtures/stage/           Solaris-shaped test stage: shot.usda, hero/asset.usda, env/sky.hdr
fixtures/stage-flat/      the same asset flattened to .usdz for three's own USDLoader
fixtures/stage-dirty/     a stage with eight planted problems, for the census
fixtures/stage-escape/    an export that points outside its own folder, for the census
stages/                   your exports go here (git-ignored)
plan/                     the 16 roadmap tasks, the router's reference labels, the Web Export blueprint
tests/                    Python unit tests and a local Jev stand-in
tests/browser/            headless Chromium smoke test and screenshots
docs/                     architecture, versions, lighting, gotchas, operator's cards, brand assets
```

</details>

---

### <img src="docs/assets/swatch-moss.svg" width="11" height="11" alt=""> Testing

`npm test` runs the syntax checks and the Python unit tests. The census tests need `pxr` and skip themselves without it; the router tests need only Python. Jev is tested against a local stand-in that speaks TypeSafe's documented response shapes, so no key is needed and nothing leaves the machine.

The viewer works end to end on a synthetic Solaris-shaped stage in headless Chromium, and `npm run test:browser` is the check: it opens the viewer in headless Chromium, exercises every feature, saves screenshots to `shots/` and fails if the page errors, a material falls back or a route doesn't load. **Start the server first**, and install Playwright with Chromium (`npm i --no-save playwright && npx playwright install chromium`).

CI runs the unit tests and syntax checks on every push and pull request. The browser test runs on demand from the Actions tab, because software rendering takes several minutes.

---

### <img src="docs/assets/swatch-clay.svg" width="11" height="11" alt=""> Versions

**The one pin that matters is MaterialX.** `@needle-tools/usd` 1.1.2 asks for MaterialX 1.7.3, which breaks on three r186 and later, so `package.json` forces 1.8.0. The full matrix and when to recheck it are in [docs/versions.md](docs/versions.md).

---

### <img src="docs/assets/swatch-clay.svg" width="11" height="11" alt=""> Licensing

Read this before you put it in front of a client.

This repository's own code is MIT-licensed; see [LICENSE](LICENSE). Copyright (c) 2026 Joseph O. Ibrahim.

The MIT licence covers this repo's code only. `@needle-tools/usd` and `@needle-tools/materialx` are licensed **PolyForm Noncommercial 1.0.0**. They are installed from npm, not redistributed here. Use them for local evaluation only until Needle grants commercial terms (hi@needle.tools). three.js is MIT. OpenUSD is under its own licence.

---

### <img src="docs/assets/swatch-moss.svg" width="11" height="11" alt=""> Roadmap

| Plan | What it holds |
|:---|:---|
| [`plan/web-export-blueprint.md`](plan/web-export-blueprint.md) | **Planned**, and the next step: the Web Export LOP node. It will sit at the end of a Solaris network, write the stage to web formats, and report first what each format would lose. |
| [`plan/tasks.json`](plan/tasks.json) | The 16 roadmap tasks, from the first run on a real GPU to a live Solaris viewport. Run `npm run route` to see which ones a cloud agent can take and which need a person. |

Known traps and open gaps are in [docs/gotchas.md](docs/gotchas.md), and every release is in [CHANGELOG.md](CHANGELOG.md).

---

Solaris builds the stage, real OpenUSD composes it in a browser tab, and three.js draws it as one more Hydra render delegate, so no converter guesses at your scene on the way to the web.
