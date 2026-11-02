# Versions

Every version below is pinned in `package.json` or in CI. This page records why, so an upgrade is a decision and not an accident.

## Pinned stack

| Part | Version | Where it is pinned | Licence |
|---|---|---|---|
| three.js | 0.187.0 (r187) | `package.json` | MIT |
| three.js, comparison route | 0.185.1 (r185), installed as `three-r185` | `package.json` | MIT |
| `@needle-tools/usd` (OpenUSD 26.05 in WebAssembly, three.js Hydra delegate) | 1.1.2 | `package.json` | PolyForm Noncommercial 1.0.0 |
| `@needle-tools/materialx` | 1.8.0, forced by `overrides` | `package.json` | PolyForm Noncommercial 1.0.0 |
| Node.js | 22 or newer | `.nvmrc`, `engines` | n/a |
| usd-core (Python, for the census in CI) | 26.5, the same OpenUSD release the browser runs | `.github/workflows/ci.yml` | OpenUSD licence |

## Why MaterialX is overridden

**`@needle-tools/usd` 1.1.2 asks for MaterialX 1.7.3, and 1.7.3 does not work on current three.js.** The repo forces 1.8.0 with an npm `overrides` entry. This is the single most important pin in the project.

What breaks with 1.7.3, as tested:

| three.js | `@needle-tools/materialx` | Result |
|---|---|---|
| r185 | 1.7.3 | Works. This is the combination Needle shipped against. |
| r186 | 1.7.3 | Crashes at render time on the `sunLights` uniform. |
| r187 | 1.7.3 | The MaterialX module fails to import, and every MaterialX material falls back to grey. r187 moved PMREM to cube render targets and removed `CubeUVReflectionMapping` and the `cube_uv_reflection_fragment` shader chunk that 1.7.3 imports. |
| r187 | 1.8.0 | Works. This is the default here. |

The `?three=185` comparison route also runs with 1.8.0, because the override applies to the whole install. r186 with 1.8.0 has not been tested.

## When to recheck

Recheck the override when `@needle-tools/usd` moves past 1.1.2. If its new release asks for MaterialX 1.8.0 or newer on its own, delete the `overrides` block.

Recheck everything when three.js moves past r187. Run `npm run test:browser` on the new version before changing the pin, and watch the status line for "fell back to a default material".

Keep usd-core in CI on the same OpenUSD release as the browser runtime. The census decides what the viewer can load, so both should read USD the same way.
