"""Write the glTF fixture the player and the browser test load.

    python tools/make_gltf_fixture.py

It exports fixtures/stage/shot.usda through the real writer (webexport.api.export) into
fixtures/stage-flat/hero.glb, so the player never loads a hand-made file: what the test sees
is what the exporter produces. Needs usd-core (or hython for the same OpenUSD 26.05).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from pxr import Usd  # noqa: E402

from webexport import api  # noqa: E402


def main():
    stage = Usd.Stage.Open(str(ROOT / "fixtures" / "stage" / "shot.usda"))
    results = api.export(stage, ["gltf"], ROOT / "fixtures" / "stage-flat", jev_on=False)
    for result in results:
        print(f"{result.format}: {result.entry_file.name} "
              f"({result.bytes} bytes, validated={result.validated}) {result.report.verdict}")
        if not result.validated:
            print(result.report.render_text())
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
