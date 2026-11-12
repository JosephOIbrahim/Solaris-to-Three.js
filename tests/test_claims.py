"""Claims-consistency tests: the prose has to agree with the code. Standard library only.

    py -3.12 -m unittest discover -s tests -v

Every check here reads a document the tests never used to read. A green suite only proved the
code; it did not prove that the version line, the release section, the format list or the links
still described that code. Each failure names what drifted and how to fix it.

`webexport.api` is imported for FORMATS only. It imports pxr, so that one check skips on a
machine without usd-core, the same way the census tests do.
"""
import json
import re
import sys
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"
PACKAGE = ROOT / "package.json"
WEB_EXPORT_DOC = ROOT / "docs" / "web-export.md"
DOCS = sorted((ROOT / "docs").glob("*.md"))

sys.path.insert(0, str(ROOT / "python"))
try:
    from webexport.api import FORMATS  # noqa: E402
except ModuleNotFoundError as exc:  # no pxr on this machine
    if not (exc.name or "").startswith("pxr"):
        raise
    FORMATS = None

# api.FORMATS holds the keys; the README names the formats. Keep the two lists in step.
FORMAT_LABELS = {
    "usd_folder": "USD Folder",
    "gltf": "glTF",
    "x3d": "X3D",
    "usdz": "USDZ",
}
# Plausible format names that must not appear in the status paragraph unless FORMATS carries them.
OTHER_FORMAT_WORDS = ("OBJ", "FBX", "STL", "PLY", "Alembic")

# Repo-relative roots that a path in the operator's card can name. Anything else (external/,
# Documents/, ../) is an output path or a prefs path, not a file in this repo.
REPO_ROOTS = {
    "docs", "fixtures", "houdini", "packages", "plan", "python",
    "scripts", "server", "stages", "tests", "tools", "viewer",
}

VERSION_RE = re.compile(r"\bVersion\s+(\d+\.\d+\.\d+)\.")
SECTION_RE = re.compile(r"^##\s+\[([^\]]+)\]")
BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")
ATTR_RE = re.compile(r"\b(?:src|href|srcset|poster)\s*=\s*([\"'])(.*?)\1", re.IGNORECASE)
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def read(path):
    return path.read_text(encoding="utf-8")


def line_number(text, offset):
    return text.count("\n", 0, offset) + 1


def status_paragraph(text):
    """The paragraph around the README's 'Version X.Y.Z.' line, blank lines excluded."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if VERSION_RE.search(line):
            start = i
            while start > 0 and lines[start - 1].strip():
                start -= 1
            end = i
            while end + 1 < len(lines) and lines[end + 1].strip():
                end += 1
            return "\n".join(lines[start:end + 1])
    return ""


def changelog_sections(text):
    """[(name, [bullet text, ...]), ...] in file order, newest section first."""
    sections = []
    current = None
    for line in text.splitlines():
        match = SECTION_RE.match(line)
        if match:
            current = (match.group(1), [])
            sections.append(current)
        elif current is not None:
            bullet = BULLET_RE.match(line)
            if bullet:
                current[1].append(bullet.group(1))
    return sections


def normalized(bullet):
    return " ".join(bullet.split()).casefold()


def masked_code(text):
    """The text with fenced and inline code blanked out, newlines kept for line numbers."""
    def blank(match):
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return INLINE_CODE_RE.sub(blank, FENCE_RE.sub(blank, text))


def relative_targets(path):
    """[(target, line), ...] for every Markdown link/image and HTML src/href/srcset/poster."""
    text = read(path)
    masked = masked_code(text)
    targets = []
    for match in LINK_RE.finditer(masked):
        targets.append((match.group(1), line_number(masked, match.start())))
    for match in ATTR_RE.finditer(masked):
        value = match.group(2).strip()
        if value.lower().startswith("data:"):
            continue
        attribute = match.group(0).lower()
        if attribute.startswith("srcset") or "srcset" in attribute:
            parts = [part.strip().split()[0] for part in value.split(",") if part.strip()]
        else:
            parts = [value]
        targets.extend((part, line_number(masked, match.start())) for part in parts)
    return targets


def local_target(target):
    """The path part of a relative target, or None for web, mail, anchor and absolute links."""
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if not target or target.startswith("#"):
        return None
    if target.lower().startswith(("http://", "https://", "mailto:", "data:", "tel:")):
        return None
    if target.startswith(("/", "//")):
        return None
    clean = urllib.parse.unquote(target.split("#", 1)[0].split("?", 1)[0])
    return clean or None


def format_words(text):
    """The format labels in text, longest first so 'USD Folder' never reads as a bare 'USD'."""
    vocabulary = list(FORMAT_LABELS.values()) + list(OTHER_FORMAT_WORDS)
    pattern = re.compile("|".join(re.escape(word) for word in sorted(vocabulary, key=len, reverse=True)))
    return pattern.findall(text)


def code_blobs(text):
    """The contents of fenced code blocks and inline code spans, where commands live."""
    return FENCE_RE.findall(text) + INLINE_CODE_RE.findall(FENCE_RE.sub("", text))


def repo_paths_in_commands(text):
    """Path-looking tokens in code whose first segment is a repo folder."""
    paths = []
    for blob in code_blobs(text):
        for token in re.split(r"\s+", blob.strip()):
            token = token.strip("()[]{}.,;:'\"`")
            if not token or "/" not in token or any(char in token for char in "<>*$"):
                continue
            if token.replace("\\", "/").split("/", 1)[0] in REPO_ROOTS:
                paths.append(token)
    return paths


class VersionClaims(unittest.TestCase):
    def test_readme_version_matches_package_json(self):
        version = json.loads(read(PACKAGE))["version"]
        paragraph = status_paragraph(read(README))
        self.assertTrue(
            paragraph,
            "README.md has no 'Version X.Y.Z.' status paragraph. Add the current version to the "
            "Status section, e.g. 'Version %s.'" % version,
        )
        match = VERSION_RE.search(paragraph)
        self.assertIsNotNone(
            match,
            "README.md's status paragraph does not name a 'Version X.Y.Z.'. "
            "Write 'Version %s.' in the Status paragraph." % version,
        )
        readme_version = match.group(1)
        self.assertEqual(
            readme_version,
            version,
            "README.md's status line says Version %s, but package.json says %s. "
            "Update the Status paragraph in README.md to 'Version %s.'"
            % (readme_version, version, version),
        )


class ChangelogClaims(unittest.TestCase):
    def test_changelog_has_a_section_with_notes_for_the_current_version(self):
        version = json.loads(read(PACKAGE))["version"]
        sections = dict(changelog_sections(read(CHANGELOG)))
        self.assertIn(
            version,
            sections,
            "CHANGELOG.md has no '## [%s]' section. Add one for the version in package.json, "
            "with at least one bullet that says what changed." % version,
        )
        self.assertTrue(
            sections[version],
            "CHANGELOG.md's '## [%s]' section has no bullets. Add at least one bullet under "
            "Added, Changed or Fixed so the release has notes." % version,
        )

    def test_no_section_repeats_a_bullet_from_an_older_section(self):
        sections = changelog_sections(read(CHANGELOG))
        problems = []
        older = {}
        for name, bullets in reversed(sections):  # oldest first
            for bullet in bullets:
                key = normalized(bullet)
                if key in older:
                    snippet = bullet if len(bullet) <= 120 else bullet[:117] + "..."
                    problems.append(
                        "'## [%s]' repeats a bullet from the older '## [%s]': %s"
                        % (name, older[key], snippet)
                    )
            for bullet in bullets:
                older.setdefault(normalized(bullet), name)
        self.assertEqual(
            problems,
            [],
            "CHANGELOG.md repeats these bullets across sections:\n  %s\n"
            "Keep each bullet only in the release that shipped it. Remove the copy from the "
            "older section (the anchor trap puts new work in an old release)."
            % "\n  ".join(problems),
        )


@unittest.skipIf(FORMATS is None, "needs pxr (pip install usd-core, or run under hython)")
class FormatClaims(unittest.TestCase):
    def test_readme_status_formats_match_api_formats(self):
        unknown = [key for key in FORMATS if key not in FORMAT_LABELS]
        if unknown:
            self.fail(
                "api.FORMATS has %r with no display label in tests/test_claims.py's "
                "FORMAT_LABELS. Add each new key's README name to that map, then name it in "
                "README.md's status paragraph." % unknown
            )
        expected = [FORMAT_LABELS[key] for key in FORMATS]
        paragraph = status_paragraph(read(README))
        found = format_words(paragraph)
        self.assertEqual(
            found,
            expected,
            "README.md's status paragraph names the formats as %r, but webexport.api.FORMATS is "
            "%r, which reads as %r. Update the format list in README.md's Status paragraph to "
            "match api.FORMATS." % (found, list(FORMATS), expected),
        )


class LinkClaims(unittest.TestCase):
    def test_relative_links_and_images_resolve(self):
        missing = []
        for path in [README] + DOCS:
            for target, line in relative_targets(path):
                clean = local_target(target)
                if clean is None:
                    continue
                if not (path.parent / clean).resolve().exists():
                    where = path.relative_to(ROOT).as_posix()
                    missing.append("%s:%d -> %s" % (where, line, target))
        self.assertEqual(
            missing,
            [],
            "Unresolved relative link(s) or image path(s):\n  %s\n"
            "Create the missing file, or correct the path so it resolves from the document's "
            "folder." % "\n  ".join(missing),
        )


class OperatorCardClaims(unittest.TestCase):
    def test_files_named_in_web_export_commands_exist(self):
        missing = []
        for token in repo_paths_in_commands(read(WEB_EXPORT_DOC)):
            clean = token.replace("\\", "/").split("#", 1)[0].rstrip("/")
            if clean and not (ROOT / clean).exists():
                missing.append(token)
        self.assertEqual(
            sorted(set(missing)),
            [],
            "docs/web-export.md names these repo paths in its commands or code, but they do not "
            "exist: %r. Update the command to the file's real path, or drop the stale path."
            % sorted(set(missing)),
        )


if __name__ == "__main__":
    unittest.main()
