# Operator's Card: workstation loop

**One question:** how does a change made on the workstation get to GitHub, and out as a release?

**Where:** `E:\Solaris-to-Three.js`. GitHub's `main` is the source of truth. Changes go straight to `main` (Joe's call, 2026-11-02), so **the local tests are the gate before every push**, and CI re-checks `main` after.

## Start a session

```
cd E:\Solaris-to-Three.js
git pull --rebase
```

Run `npm ci` as well if `package-lock.json` changed.

## Each change

1. Edit, then add one line under `## [Unreleased]` in `CHANGELOG.md`.
2. Test with `npm test`. If you touched the census, also run the suite under Houdini's Python, which ships the same USD 26.05 as the browser: `& "C:\Program Files\Side Effects Software\Houdini 22.0.466\bin\hython.exe" -m unittest discover -s tests`.
3. Commit: `git add <files>`, then `git commit -m "fix: what changed"`.
4. Push: `git pull --rebase`, then `git push`. If the push is rejected, someone pushed first: pull again, re-test, push.
5. Check CI with `gh run list --limit 1`.

## Release

| Since the last release | Version |
|---|---|
| fixes only | patch: 0.1.0 to 0.1.1 |
| a new capability | minor: 0.1.1 to 0.2.0 |

1. `npm version X.Y.Z --no-git-tag-version` updates `package.json` and the lockfile.
2. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add a fresh empty `## [Unreleased]` above it, and update the `[Unreleased]` and `[X.Y.Z]` links at the bottom.
3. Commit `release: vX.Y.Z`, push, and wait for CI to pass.
4. `python tools/release.py --dry-run` shows the checks and the notes. `python tools/release.py` creates the release.

`release.py` refuses unless you're on a clean `main` that matches GitHub, the changelog has notes for the version, the tag is new, and CI has passed on that exact commit. While the version is 0.x, releases are marked pre-release.

## Cloud sessions

Cloud sessions push branches and open PRs, and they can't create releases. Merge their PR on GitHub, then `git pull --rebase` here before your next change. Releases are always cut from this workstation.

## Never

- `git push --force` to `main`.
- Commit anything in `stages/`; client exports stay git-ignored.
- Put `TYPESAFE_API_KEY` in a file.