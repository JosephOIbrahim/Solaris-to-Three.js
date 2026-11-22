"""release.py: create the GitHub release for the version in package.json.

    python tools/release.py --dry-run   check everything and print the notes
    python tools/release.py             check everything, then create release vX.Y.Z

Run it on main after the release commit is pushed. It refuses unless the tree is
clean, local main matches GitHub, CHANGELOG.md has notes for the version, the tag
is new, and CI has passed on this exact commit. Needs git and a signed-in gh.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "JosephOIbrahim/Solaris-to-Three.js"


def notes_for(version, changelog):
    """Body of the '## [version]' section of a Keep a Changelog file, or None."""
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: |\Z)",
                  changelog, re.S | re.M)
    body = m.group(1).strip() if m else ""
    return body or None


def run(*cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def checks(tag, head):
    problems = []
    if run("git", "branch", "--show-current").stdout.strip() != "main":
        problems.append("not on main")
    if run("git", "status", "--porcelain").stdout.strip():
        problems.append("uncommitted changes")
    if run("git", "fetch", "-q", "origin", "main").returncode != 0:
        problems.append("could not reach GitHub (git fetch)")
    elif head != run("git", "rev-parse", "origin/main").stdout.strip():
        problems.append("local main and GitHub main differ: pull or push first")
    if run("git", "ls-remote", "--tags", "origin", tag).stdout.strip():
        problems.append(f"{tag} already exists on GitHub")
    ci = run("gh", "run", "list", "--repo", REPO, "--workflow", "CI", "--commit", head,
             "--json", "event,status,conclusion,workflowName")
    runs = json.loads(ci.stdout) if ci.returncode == 0 and ci.stdout.strip() else []
    ci_runs = [r for r in runs if r.get("workflowName", "CI") == "CI"]
    if not any(r.get("event") == "push" and r.get("conclusion") == "success" for r in ci_runs):
        seen = ", ".join(f"{r.get('workflowName', 'CI')} {r.get('event', '')} {r.get('status', '')} {r.get('conclusion') or ''}".strip()
                         for r in runs) or "no runs found"
        problems.append(f"CI has not passed on {head[:7]} yet ({seen})")
    return problems


def main():
    sys.stdout.reconfigure(errors="replace")  # a Windows console may not take every character in the notes
    ap = argparse.ArgumentParser(description="Create the GitHub release for the version in package.json.")
    ap.add_argument("--dry-run", action="store_true", help="check and print the notes; create nothing")
    ap.add_argument("--asset", action="append", default=[], help="release artifact path to attach (repeatable)")
    a = ap.parse_args()

    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    tag, head = f"v{version}", run("git", "rev-parse", "HEAD").stdout.strip()
    prerelease = version.startswith("0.")
    notes = notes_for(version, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    problems = checks(tag, head)
    for asset in a.asset:
        if not Path(asset).is_file():
            problems.append(f"release asset not found: {asset}")
    if not notes:
        problems.append(f"CHANGELOG.md has no notes under '## [{version}]'")
    elif not any(line.startswith("- ") for line in notes.splitlines()):
        # a rolled section that only carries a sentence means the bullets went somewhere else
        problems.append(f"the notes under '## [{version}]' have no bullet entries: "
                        "the changelog changes were probably written into an older release section")

    print(f"RELEASE  {tag}  at {head[:7]}  ({'pre-release' if prerelease else 'release'})")
    for p in problems:
        print(f"  x  {p}")
    if problems:
        print("Not released. Fix the x lines and run again.")
        return 1
    print("  checks pass\n")
    print(notes)
    if a.asset:
        print("\nAssets:")
        for asset in a.asset:
            print(f"  + {asset}")
    if a.dry_run:
        print("\n(dry run: nothing created)")
        return 0
    cmd = ["gh", "release", "create", tag, "--repo", REPO, "--target", head, "--title", tag, "--notes-file", "-"]
    if prerelease:
        cmd.append("--prerelease")
    cmd.extend(a.asset)
    out = subprocess.run(cmd, cwd=ROOT, input=notes, capture_output=True, text=True, encoding="utf-8")
    print(out.stdout.strip() or out.stderr.strip())
    return out.returncode


if __name__ == "__main__":
    sys.exit(main())