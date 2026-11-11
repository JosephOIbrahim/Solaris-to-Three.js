"""Web Export core. Pure pxr, no Houdini (blueprint section 2, rule 3).

The contracts live in report.py (LossItem, LossReport), profiles/base.py (Profile),
writers/base.py (Writer, ExportResult) and settings.py (WebSettings). Importing the
package does not import pxr; only settings.py needs it.
"""
from .report import LossItem, LossReport, verdict_for

__all__ = ["LossItem", "LossReport", "verdict_for"]
