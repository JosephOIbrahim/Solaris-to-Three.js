"""LossReport and LossItem: what will not survive the trip to a format, as data.

Blueprint 4.3. Structure out, never prose: the core returns this, and the terminal,
the panel, the node message and Synapse each render it their own way. render_text()
is the terminal rendering and mirrors tools/web_census.py.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Severity = Literal["blocks", "changes", "note"]
Verdict = Literal["FIX FIRST", "GO, WITH NOTES", "GO"]

SEVERITIES: tuple[str, ...] = ("blocks", "changes", "note")
MARKS: dict[str, str] = {"blocks": "x", "changes": "!", "note": "-"}
TITLES: dict[str, str] = {"blocks": "FIX FIRST", "changes": "WILL LOOK DIFFERENT", "note": "WORTH KNOWING"}
LEADS: dict[str, str] = {
    "FIX FIRST": "{n} problem(s) stop this scene loading or looking right.",
    "GO, WITH NOTES": "It will load. Some things will look different from Karma.",
    "GO": "Nothing found that stops it. Open it in the viewer.",
}
WIDTH = 72


@dataclass
class LossItem:
    severity: Severity  # x, !, -
    feature: str        # "variant_set", "rect_light", "materialx", ...
    count: int
    examples: list[str] = field(default_factory=list)  # up to 3 prim paths
    becomes: str = ""   # "baked to textures", "dropped", "Switch node", ...
    fix: str | None = None  # what the artist can do, if anything

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, got {self.severity!r}")


def verdict_for(items: list[LossItem]) -> Verdict:
    """Any blocks gives FIX FIRST; else any changes gives GO, WITH NOTES; else GO."""
    severities = {i.severity for i in items}
    if "blocks" in severities:
        return "FIX FIRST"
    if "changes" in severities:
        return "GO, WITH NOTES"
    return "GO"


@dataclass
class LossReport:
    format: str
    verdict: Verdict
    items: list[LossItem] = field(default_factory=list)
    jev_note: str = ""

    @classmethod
    def from_items(cls, format: str, items: list[LossItem], jev_note: str = "") -> "LossReport":
        """Build a report with the verdict computed by the rule, never typed by hand."""
        items = list(items)
        return cls(format=format, verdict=verdict_for(items), items=items, jev_note=jev_note)

    def render_text(self) -> str:
        lines = [f"LOSS REPORT   {self.format}", "-" * WIDTH]
        n_blocks = sum(1 for i in self.items if i.severity == "blocks")
        lead = LEADS[self.verdict].format(n=n_blocks)
        lines += [f"{self.verdict:<15}{lead}", ""]
        for sev in SEVERITIES:
            group = [i for i in self.items if i.severity == sev]
            if not group:
                continue
            lines.append(TITLES[sev])
            for it in group:
                lines.append(f"  {MARKS[sev]}  {it.feature:<52}{it.count:>4}")
                if it.examples:
                    lines.append(f"     {str(it.examples[0])[:WIDTH - 5]}")
                if it.becomes:
                    lines.append(f"     becomes: {it.becomes}"[:WIDTH])
                if it.fix:
                    lines.append(f"     fix: {it.fix}"[:WIDTH])
            lines.append("")
        if self.jev_note:
            lines.append(f"  {self.jev_note}")
        lines.append("-" * WIDTH)
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        """A plain JSON-serialisable dict (json.dumps-ready). from_json() reads it back."""
        return asdict(self)

    def to_json_str(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_json(), indent=indent)

    @classmethod
    def from_json(cls, data: dict[str, Any] | str) -> "LossReport":
        """Inverse of to_json(). Accepts the dict or its json.dumps string."""
        if isinstance(data, str):
            data = json.loads(data)
        items = [
            LossItem(
                severity=i["severity"],
                feature=i["feature"],
                count=int(i["count"]),
                examples=list(i.get("examples", [])),
                becomes=i.get("becomes", ""),
                fix=i.get("fix"),
            )
            for i in data.get("items", [])
        ]
        return cls(format=data["format"], verdict=data["verdict"], items=items,
                   jev_note=data.get("jev_note", ""))
