# Operator's Card: web census

**One question:** can this Solaris export go to the web?

## Run

At your desk, with Houdini's Python:

```
hython tools/web_census.py <export folder>/<root>.usda
```

Any Python with `pip install usd-core` works too. Add `--json census.json` to save everything for a machine, or `--no-jev` to skip Jev. The exit code is 1 when there is something to fix first, so it can gate a script.

## Read the first word

| Verdict | Means | Do |
|---|---|---|
| **FIX FIRST** | It won't load or won't look right | Fix the `x` lines, re-export, run again |
| **GO, WITH NOTES** | It loads, but it will differ from Karma | Read the `!` lines, then open it |
| **GO** | Nothing stops it | Copy the folder to `stages/<name>/` and open `/?stage=/stages/<name>/<root>.usda` |

**Marks:** `x` stops it, `!` will look different, `-` is worth knowing.

## Common `x` fixes

- Set the USD ROP to relative paths, and export every payload and texture into the folder. The settings are in [export.md](export.md).
- **Files outside the export folder:** a `../` path loads in Houdini and 404s on the web. Copy the file into the export folder and re-export; [export.md](export.md) covers referenced assets.
- Convert `.rat` and `.tx` to `.png`, `.jpg` or `.exr`.
- Bake UDIMs to a single map; the browser has no UDIM support.
- Give every MaterialX mesh UVs (`primvars:st`).

## How it decides

The census keeps three kinds of work apart. **USD supplies the facts:** shader IDs, texture paths, lights, layers and UVs. **Code makes every decision:** the verdict and each rule. **Jev judges one narrow question:** is a shader node the census doesn't recognise portable MaterialX, or renderer-only?

All unrecognised nodes go to Jev in one request. An answer under 0.5 confidence counts as "needs your eyes". Without a key, or if Jev fails, the census still runs and marks those nodes "needs your eyes". Nothing is sent anywhere else.

## Jev setup

Set the key in the environment, never in a file:

```
$env:TYPESAFE_API_KEY="..."      # PowerShell
export TYPESAFE_API_KEY="..."    # bash
```

## Self-test

`fixtures/stage-dirty/dirty.usda` has eight planted problems. Run the census on it with `--no-jev` and expect **6 `x` and 3 `!`**. `fixtures/stage-escape/export/shot.usda` points at three files outside its folder; expect them under **files outside the export folder**. `python -m unittest tests.test_census` checks both automatically. Rebuild the fixture with `python tools/make_dirty_stage.py`.

**Never** paste the API key into a chat, an issue or a commit.
