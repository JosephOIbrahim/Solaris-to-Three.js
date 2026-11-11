"""Web Export core. Pure pxr, no Houdini (blueprint section 2, rule 3).

The contracts live in report.py (LossItem, LossReport), profiles/base.py (Profile),
writers/base.py (Writer, ExportResult) and settings.py (WebSettings). Importing the
package itself does not import pxr; the modules that read or write stages do.
"""
from .report import LossItem, LossReport, verdict_for

__all__ = ["LossItem", "LossReport", "verdict_for"]
