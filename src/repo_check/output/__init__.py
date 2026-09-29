"""Renderers for analysis results. Input findings must already be sorted."""

from repo_check.output.json import render_json
from repo_check.output.text import render_text

__all__ = ["render_json", "render_text"]
