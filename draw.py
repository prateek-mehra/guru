"""Tiny whiteboard language -> SVG.

The LLM writes one command per line (easier and far more reliable than raw SVG):

    box x y w h color "label"
    circle x y r color "label"
    text x y size color "words"
    arrow x1 y1 x2 y2 color "label"
    line x1 y1 x2 y2 color
    curve color x1,y1 x2,y2 x3,y3 ...
    axes x y w h "x label" "y label"
    underline x y w color

Canvas is 800 x 500. Colors: black blue red green orange purple.
"""

import html
import shlex

COLORS = {
    "black": "#1f2937", "kaala": "#1f2937", "blue": "#2563eb", "neela": "#2563eb",
    "red": "#dc2626", "laal": "#dc2626", "green": "#059669", "hara": "#059669",
    "orange": "#d97706", "purple": "#7c3aed",
}


def _c(name: str) -> str:
    return COLORS.get((name or "").lower(), "#1f2937")


def _n(v) -> float:
    return round(float(str(v).strip(",")), 1)


def _t(s: str) -> str:
    return html.escape(s or "", quote=True)


def _label(x, y, text, color, size=20):
    if not text:
        return ""
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'text-anchor="middle" dominant-baseline="middle">{_t(text)}</text>')


def _arrowhead(x1, y1, x2, y2, color):
    import math
    a = math.atan2(y2 - y1, x2 - x1)
    p1 = (x2 - 14 * math.cos(a - 0.45), y2 - 14 * math.sin(a - 0.45))
    p2 = (x2 - 14 * math.cos(a + 0.45), y2 - 14 * math.sin(a + 0.45))
    return (f'<path d="M{p1[0]:.1f} {p1[1]:.1f} L{x2} {y2} L{p2[0]:.1f} {p2[1]:.1f}" '
            f'stroke="{color}" stroke-width="3" fill="none"/>')


def command_to_svg(line: str) -> str:
    try:
        tok = shlex.split(line.strip())
    except ValueError:
        tok = line.replace('"', " ").split()
    if not tok:
        return ""
    cmd, a = tok[0].lower(), tok[1:]
    try:
        if cmd in ("box", "rect"):
            x, y, w, h = map(_n, a[:4]); col = _c(a[4] if len(a) > 4 else "")
            lab = " ".join(a[5:])
            return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" stroke="{col}" '
                    f'stroke-width="3" fill="{col}" fill-opacity="0.08"/>' + _label(x + w / 2, y + h / 2, lab, col))
        if cmd == "circle":
            x, y, r = map(_n, a[:3]); col = _c(a[3] if len(a) > 3 else "")
            lab = " ".join(a[4:])
            return (f'<circle cx="{x}" cy="{y}" r="{r}" stroke="{col}" stroke-width="3" fill="{col}" '
                    f'fill-opacity="0.08"/>' + _label(x, y, lab, col))
        if cmd == "text":
            x, y, rest = _n(a[0]), _n(a[1]), a[2:]
            size = 22
            if rest and rest[0].replace(".", "", 1).isdigit():  # size is optional
                size = max(14, min(34, _n(rest[0]))); rest = rest[1:]
            col = "#1f2937"
            if rest and rest[0].lower() in COLORS:
                col = _c(rest[0]); rest = rest[1:]
            return f'<text x="{x}" y="{y}" font-size="{size}" fill="{col}">{_t(" ".join(rest))}</text>'
        if cmd in ("arrow", "line"):
            x1, y1, x2, y2 = map(_n, a[:4]); col = _c(a[4] if len(a) > 4 else "")
            out = f'<path d="M{x1} {y1} L{x2} {y2}" stroke="{col}" stroke-width="3" fill="none"/>'
            if cmd == "arrow":
                out += _arrowhead(x1, y1, x2, y2, col)
                out += _label((x1 + x2) / 2, (y1 + y2) / 2 - 16, " ".join(a[5:]), col, 18)
            return out
        if cmd == "underline":
            x, y, w = map(_n, a[:3]); col = _c(a[3] if len(a) > 3 else "red")
            return f'<path d="M{x} {y} Q{x + w / 2} {y + 6} {x + w} {y - 2}" stroke="{col}" stroke-width="3" fill="none"/>'
        if cmd == "curve":
            col = next((_c(t) for t in a if t.lower() in COLORS), "#1f2937")
            nums = [_n(v) for t in a for v in t.split(",") if v.strip().lstrip("-").replace(".", "", 1).isdigit()]
            pts = list(zip(nums[0::2], nums[1::2]))
            if len(pts) < 2:
                return ""
            d = f"M{pts[0][0]} {pts[0][1]}"
            for i in range(1, len(pts)):  # smooth through points with quadratic midpoints
                (px, py), (cx, cy) = pts[i - 1], pts[i]
                d += f" Q{px} {py} {(px + cx) / 2} {(py + cy) / 2}" if i < len(pts) - 1 else f" Q{px} {py} {cx} {cy}"
            return f'<path d="{d}" stroke="{col}" stroke-width="3" fill="none"/>'
        if cmd == "axes":
            x, y, w, h = map(_n, a[:4]); xl = a[4] if len(a) > 4 else ""; yl = a[5] if len(a) > 5 else ""
            col = "#1f2937"; oy = y + h
            return (f'<path d="M{x} {oy} L{x + w} {oy}" stroke="{col}" stroke-width="3" fill="none"/>'
                    + _arrowhead(x, oy, x + w, oy, col)
                    + f'<path d="M{x} {oy} L{x} {y}" stroke="{col}" stroke-width="3" fill="none"/>'
                    + _arrowhead(x, oy, x, y, col)
                    + _label(x + w / 2, oy + 24, xl, col, 18)
                    + (f'<text x="{x - 12}" y="{y + h / 2}" font-size="18" fill="{col}" text-anchor="middle" '
                       f'transform="rotate(-90 {x - 12} {y + h / 2})">{_t(yl)}</text>' if yl else ""))
    except (ValueError, IndexError):
        return ""
    return ""


DRAW_CMDS = ("box", "rect", "circle", "text", "arrow", "line", "curve", "axes", "underline")


def parse_lesson(raw: str):
    """Parse the model's SAY/DRAW script into [{say, svg}] steps."""
    steps = []
    for line in (raw or "").splitlines():
        line = line.strip().strip("`").strip()
        if not line:
            continue
        head, _, rest = line.partition(":")
        key = head.strip().lstrip("-*# ").upper()
        if key in ("SAY", "BOL", "BOLO") and rest.strip():
            steps.append({"say": rest.strip().strip('"'), "svg": ""})
        elif key in ("DRAW", "DRAW1") and steps:
            steps[-1]["svg"] += command_to_svg(rest)
        elif line.split(" ", 1)[0].lower() in DRAW_CMDS and steps:  # forgot the DRAW: prefix
            steps[-1]["svg"] += command_to_svg(line)
    return steps
