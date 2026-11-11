# Build board: web-export-tasks.json

_Jev jev-latest_

## You (1): decisions only you can make

| Task | First | Size | Jev | Waits on |
|---|---|---|---|---|
| W10 Decide when glTF and X3D start | none | tiny | 1.00 | - |

## Desk (8): your workstation, weekday afternoons

| Task | First | Size | Jev | Waits on |
|---|---|---|---|---|
| W1 Install the package into Houdini prefs | none | small | 0.96 | - |
| W2 Check hython imports webexport | none | tiny | 0.98 | W1 |
| W4 Probe the 43 open questions in hython | spike | small | 0.98 | W2 |
| W5 Build the Web Export HDA | spike | large | 0.97 | W3, W4 |
| W6 Test the HDA on three Houdini builds | fixture | medium | 0.98 | W5 |
| W7 Check the Preview button can start the server | spike | small | 0.91 | W5 |
| W8 Merge wave 2, test and push | ? | medium | 0.64 | W6, W7 |
| W9 Write the Web Export card and release 0.2.0 | none | medium | 0.60 | W8 |

## Cloud (1): agents

| Task | First | Size | Jev | Waits on |
|---|---|---|---|---|
| W3 Export in-memory LOP stages | spike | opus | 0.91 | - |
