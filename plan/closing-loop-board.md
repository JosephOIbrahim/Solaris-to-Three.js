# Build board: closing-loop-tasks.json

_Jev jev-latest_

## You (1): decisions only you can make

| Task | First | Size | Jev | Waits on |
|---|---|---|---|---|
| C7 Release 0.2.0 | none | medium | 0.34 (unsure) | C6 |

## Desk (5): your workstation, weekday afternoons

| Task | First | Size | Jev | Waits on |
|---|---|---|---|---|
| C1 Write build_hda.py and hou_node.py | ? | medium | 0.68 | - |
| C3 Test the HDA on three builds | fixture | medium | 1.00 | C1 |
| C4 Check the Preview button | ? | small | 0.98 | C1 |
| C6 Merge, test and push wave 2 | fixture | medium | 0.79 | C3, C5 |
| C8 Upload the social preview | none | tiny | 0.85 | - |

## Cloud (2): agents

| Task | First | Size | Jev | Waits on |
|---|---|---|---|---|
| C2 Export an in-memory LOP stage | ? | opus | 0.97 | - |
| C5 Write the operator's card and README rows | ? | opus | 0.96 | C3 |
