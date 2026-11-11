"""Profile contract (blueprint 4.2). Adding a format must not touch another format."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..inventory import Inventory  # owned by a later mission; named, never imported here
    from ..report import LossItem


@runtime_checkable
class Profile(Protocol):
    name: str                      # "usd_folder", "gltf", "x3d", "usdz"
    extensions: tuple[str, ...]

    def assess(self, inv: "Inventory") -> "list[LossItem]": ...
