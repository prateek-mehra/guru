"""Whiteboard language -> SVG (layout.py: semantic commands, draw.py: text measuring and raw commands)."""

from .layout import parse_lesson

__all__ = ["parse_lesson"]
