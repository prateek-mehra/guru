"""Tiny whiteboard language -> SVG, with a small layout engine.

The LLM writes one command per line (easier and far more reliable than raw SVG):

    box x y w h color "label"
    circle x y r color "label"
    text x y size color "words"
    arrow x1 y1 x2 y2 color "label"
    line x1 y1 x2 y2 color
    curve color x1,y1 x2,y2 x3,y3 ...
    axes x y w h "x label" "y label"
    underline x y w color

The model is bad at measuring text and at keeping things apart, so the Board
below fixes its layout as each command arrives:
  * box labels are wrapped / shrunk to fit, and the box grows if they still don't;
  * circle labels that don't fit go underneath the circle;
  * shapes that overlap earlier shapes are nudged into free space, and arrows
    attached to them move along;
  * arrow ends are clipped to the edge of the shape they point into;
  * free text and arrow labels slide to the nearest spot that doesn't collide,
    and long text is shrunk / wrapped to stay on the 800 x 500 canvas.
"""

import html
import math
import shlex

W, H = 800, 500
COLORS = {
    "black": "#1C2541", "kaala": "#1C2541", "blue": "#1C2541", "neela": "#1C2541",
    "red": "#B7873C", "laal": "#B7873C", "green": "#1C2541", "hara": "#1C2541",
    "orange": "#B7873C", "purple": "#1C2541", "gray": "#777C8D", "grey": "#777C8D",
}


def _c(name: str) -> str:
    return COLORS.get((name or "").lower(), "#1C2541")


def _isnum(t: str) -> bool:
    try:
        float(str(t).strip(","))
        return True
    except ValueError:
        return False


def _n(v) -> float:
    return round(float(str(v).strip(",")), 1)


def _t(s: str) -> str:
    return html.escape(s or "", quote=True)


# ---------------------------------------------------------------- text metrics (Kalam font)

def text_w(s: str, size: float) -> float:
    w = 0.0
    for ch in s:
        if "ऀ" <= ch <= "ॿ":
            w += 0.47          # Devanagari (Hind Bold, measured 0.463em)
        elif ch in "mwMW":
            w += 0.78
        elif ch.isupper():
            w += 0.6
        elif ch in "il.,:;'!|()[] ":
            w += 0.3
        else:
            w += 0.5
    return w * size


def wrap(s: str, size: float, max_w: float):
    words, lines, cur = s.split(), [], ""
    for wd in words:
        cand = (cur + " " + wd).strip()
        if not cur or text_w(cand, size) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines or [""]


def fit_text(s: str, max_w: float, size: float, min_size: float = 13, max_lines: int = 2):
    """Largest size (<= size) at which s fits in max_w using <= max_lines lines."""
    sz = size
    while True:
        lines = wrap(s, sz, max_w)
        if len(lines) <= max_lines and all(text_w(l, sz) <= max_w for l in lines):
            return sz, lines, True
        if sz <= min_size:
            return sz, lines, False
        sz -= 1


# ---------------------------------------------------------------- geometry

def overlap(a, b, pad=0.0) -> bool:
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def shift(b, dx, dy):
    return (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy)


def inside_canvas(b, margin=4) -> bool:
    return b[0] >= margin and b[1] >= margin and b[2] <= W - margin and b[3] <= H - margin


def clamp_into_canvas(b, margin=6):
    dx = max(0, margin - b[0]) - max(0, b[2] - (W - margin))
    dy = max(0, margin - b[1]) - max(0, b[3] - (H - margin))
    return shift(b, dx, dy), dx, dy


def candidate_offsets(step=8, reach=200):
    """Offsets ordered by distance: small moves first, prefer down/right."""
    offs = [(0, 0)]
    for r in range(step, reach + 1, step):
        for dx, dy in ((0, r), (r, 0), (0, -r), (-r, 0), (r, r), (-r, r), (r, -r), (-r, -r)):
            offs.append((dx, dy))
    return offs


class Board:
    def __init__(self):
        self.shapes = []   # [(bbox, kind, extra)] boxes & circles
        self.texts = []    # bboxes of text already placed
        self.lines = []    # bboxes of thin horizontal/vertical strokes
        self.moves = []    # [(original bbox, dx, dy)] for shapes we nudged

    # ------------------------------------------------ helpers
    def _blocked(self, b, pad, against=("shapes", "texts", "lines")):
        for name in against:
            items = self.shapes if name == "shapes" else self.texts if name == "texts" else self.lines
            for it in items:
                ib = it[0] if name == "shapes" else it
                if overlap(b, ib, pad):
                    return True
        return False

    def find_spot(self, b, pad=6, against=("shapes", "texts", "lines"), reach=200):
        b, _, _ = clamp_into_canvas(b)
        for dx, dy in candidate_offsets(reach=reach):
            nb = shift(b, dx, dy)
            if inside_canvas(nb) and not self._blocked(nb, pad, against):
                return nb
        return b  # board is full; accept the overlap

    def _moved_point(self, x, y):
        for ob, dx, dy in self.moves:
            if ob[0] - 25 <= x <= ob[2] + 25 and ob[1] - 25 <= y <= ob[3] + 25:
                return x + dx, y + dy
        return x, y

    def _clip_to_shape(self, x_in, y_in, x_out, y_out):
        """If (x_in, y_in) is inside a shape, slide it toward (x_out, y_out) until it's on the edge."""
        for b, kind, extra in self.shapes:
            if kind == "circle":
                cx, cy, r = extra
                d = math.hypot(x_in - cx, y_in - cy)
                if d <= r + 2:
                    L = math.hypot(x_out - x_in, y_out - y_in) or 1
                    ux, uy = (x_out - x_in) / L, (y_out - y_in) / L
                    # walk out along the line
                    t = 0.0
                    while math.hypot(x_in + ux * t - cx, y_in + uy * t - cy) < r + 5 and t < L:
                        t += 1
                    return x_in + ux * t, y_in + uy * t
            elif b[0] - 2 <= x_in <= b[2] + 2 and b[1] - 2 <= y_in <= b[3] + 2:
                L = math.hypot(x_out - x_in, y_out - y_in) or 1
                ux, uy = (x_out - x_in) / L, (y_out - y_in) / L
                t = 0.0
                while b[0] - 5 <= x_in + ux * t <= b[2] + 5 and b[1] - 5 <= y_in + uy * t <= b[3] + 5 and t < L:
                    t += 1
                return x_in + ux * t, y_in + uy * t
        return x_in, y_in

    # ------------------------------------------------ text block rendering
    @staticmethod
    def _text_svg(lines, x, y_top, size, color, anchor="start"):
        lh = size * 1.18
        spans = "".join(
            f'<tspan x="{x:.1f}" y="{y_top + size * 0.95 + i * lh:.1f}">{_t(l)}</tspan>' for i, l in enumerate(lines))
        return f'<text font-size="{size:.0f}" fill="{color}" text-anchor="{anchor}">{spans}</text>'

    def _place_label(self, text, cx, cy, size, color, max_w=260, prefer=None):
        """Place a centred label near (cx, cy) where it doesn't collide; returns svg."""
        sz, lines, _ = fit_text(text, max_w, size, min_size=13, max_lines=2)
        w = max(text_w(l, sz) for l in lines)
        h = len(lines) * sz * 1.18
        b = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        if prefer is not None:
            b = prefer(w, h)
        b = self.find_spot(b, pad=4)
        self.texts.append(b)
        return self._text_svg(lines, (b[0] + b[2]) / 2, b[1], sz, color, "middle")

    # ------------------------------------------------ commands
    def plan_box(self, x, y, w, h, label):
        sz, lines = 24, []
        if label:
            sz, lines, ok = fit_text(label, w - 16, 24, min_size=18, max_lines=2)
            need_h = len(lines) * sz * 1.18 + 12
            if not ok:  # grow the box so the label fits at a readable size
                sz, lines, _ = fit_text(label, 320, 22, min_size=16, max_lines=2)
                need_w = max(text_w(l, sz) for l in lines) + 20
                x -= (need_w - w) / 2
                w = need_w
                need_h = len(lines) * sz * 1.18 + 12
            if need_h > h:
                y -= (need_h - h) / 2
                h = need_h
        orig = (x, y, x + w, y + h)
        b = self.find_spot(orig, pad=10, against=("shapes",))
        dx, dy = b[0] - orig[0], b[1] - orig[1]
        if dx or dy:
            self.moves.append((orig, dx, dy))
        self.shapes.append((b, "box", None))
        return {"b": b, "sz": sz, "lines": lines}

    def box(self, plan, col):
        (x, y, x2, y2), sz, lines = plan["b"], plan["sz"], plan["lines"]
        w, h = x2 - x, y2 - y
        out = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="10" stroke="{col}" '
               f'stroke-width="3" fill="{col}" fill-opacity="0.08"/>')
        if lines:
            th = len(lines) * sz * 1.18
            out += self._text_svg(lines, x + w / 2, y + (h - th) / 2, sz, col, "middle")
        return out

    def plan_circle(self, cx, cy, r):
        r = max(r, 18)
        orig = (cx - r, cy - r, cx + r, cy + r)
        b = self.find_spot(orig, pad=10, against=("shapes",))
        dx, dy = b[0] - orig[0], b[1] - orig[1]
        if dx or dy:
            self.moves.append((orig, dx, dy))
        cx, cy = cx + dx, cy + dy
        self.shapes.append((b, "circle", (cx, cy, r)))
        return {"c": (cx, cy, r)}

    def circle(self, plan, col, label):
        cx, cy, r = plan["c"]
        out = (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" stroke="{col}" stroke-width="3" '
               f'fill="{col}" fill-opacity="0.08"/>')
        if label:
            sz, lines, ok = fit_text(label, 2 * r * 0.82, 22, min_size=15, max_lines=2)
            if ok and len(lines) * sz * 1.18 <= 2 * r * 0.8:
                th = len(lines) * sz * 1.18
                out += self._text_svg(lines, cx, cy - th / 2, sz, col, "middle")
            else:  # label underneath the circle
                out += self._place_label(label, cx, cy + r + 16, 18, col, max_w=180,
                                         prefer=lambda w, h: (cx - w / 2, cy + r + 6, cx + w / 2, cy + r + 6 + h))
        return out

    def text(self, x, y, size, col, words):
        size = max(16, min(34, size))
        max_w = W - 12 - max(6, x)
        if max_w < 220:  # too close to the right edge: pull it left
            x = max(6, W - 12 - min(W - 24, text_w(words, size)))
            max_w = W - 12 - x
        sz, lines, _ = fit_text(words, max_w, size, min_size=15, max_lines=3)
        w = max(text_w(l, sz) for l in lines)
        h = len(lines) * sz * 1.18
        b = self.find_spot((x, y - sz * 0.95, x + w, y - sz * 0.95 + h), pad=5)
        self.texts.append(b)
        return self._text_svg(lines, b[0], b[1], sz, col)

    def arrow(self, x1, y1, x2, y2, col, label, head=True):
        x1, y1 = self._moved_point(x1, y1)
        x2, y2 = self._moved_point(x2, y2)
        x1, y1 = self._clip_to_shape(x1, y1, x2, y2)
        x2, y2 = self._clip_to_shape(x2, y2, x1, y1)
        out = f'<path d="M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}" stroke="{col}" stroke-width="3" fill="none"/>'
        if head and math.hypot(x2 - x1, y2 - y1) > 4:
            out += _arrowhead(x1, y1, x2, y2, col)
        seg = (min(x1, x2) - 3, min(y1, y2) - 3, max(x1, x2) + 3, max(y1, y2) + 3)
        if seg[2] - seg[0] < 14 or seg[3] - seg[1] < 14:
            self.lines.append(seg)
        if label:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            L = math.hypot(x2 - x1, y2 - y1) or 1
            nx, ny = -(y2 - y1) / L, (x2 - x1) / L  # perpendicular
            if ny > 0 or (ny == 0 and nx < 0):
                nx, ny = -nx, -ny                   # prefer above / right of the arrow

            def beside(w, h, nx=nx, ny=ny):
                off = 8 + abs(nx) * w / 2 + abs(ny) * h / 2
                cx, cy = mx + nx * off, my + ny * off
                return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            out += self._place_label(label, mx, my, 17, col, max_w=200, prefer=beside)
        return out

    def underline(self, x, y, w, col):
        return f'<path d="M{x} {y} Q{x + w / 2} {y + 6} {x + w} {y - 2}" stroke="{col}" stroke-width="3" fill="none"/>'

    def curve(self, col, pts):
        if len(pts) < 2:
            return ""
        d = f"M{pts[0][0]} {pts[0][1]}"
        for i in range(1, len(pts)):  # smooth through points with quadratic midpoints
            (px, py), (cx, cy) = pts[i - 1], pts[i]
            d += f" Q{px} {py} {(px + cx) / 2} {(py + cy) / 2}" if i < len(pts) - 1 else f" Q{px} {py} {cx} {cy}"
        return f'<path d="{d}" stroke="{col}" stroke-width="3" fill="none"/>'

    def axes(self, x, y, w, h, xl, yl):
        col, oy = "#1C2541", y + h
        out = (f'<path d="M{x} {oy} L{x + w} {oy}" stroke="{col}" stroke-width="3" fill="none"/>'
               + _arrowhead(x, oy, x + w, oy, col)
               + f'<path d="M{x} {oy} L{x} {y}" stroke="{col}" stroke-width="3" fill="none"/>'
               + _arrowhead(x, oy, x, y, col))
        self.lines += [(x - 3, oy - 3, x + w + 3, oy + 3), (x - 3, y - 3, x + 3, oy + 3)]
        if xl:
            out += self._place_label(xl, x + w / 2, oy + 18, 17, col, max_w=w)
        if yl:
            out += self._place_label(yl, x + 10 + text_w(yl, 17) / 2, y - 14, 17, col, max_w=220)
        return out

    # ------------------------------------------------ dispatcher
    @staticmethod
    def parse(line: str):
        try:
            tok = shlex.split(line.strip())
        except ValueError:
            tok = line.replace('"', " ").split()
        if not tok:
            return None
        cmd, a = tok[0].lower(), tok[1:]
        nums = []
        while a and _isnum(a[0]):
            nums.append(_n(a.pop(0)))
        col = "#1C2541"
        if a and a[0].lower() in COLORS:
            col = _c(a.pop(0))
        return {"cmd": cmd, "tok": tok, "nums": nums, "col": col, "label": " ".join(a).strip()}

    def plan(self, c):
        """Pass 1: decide where every box/circle of the lesson goes, before anything is drawn."""
        n = c["nums"]
        if c["cmd"] in ("box", "rect") and len(n) >= 4:
            c["plan"] = self.plan_box(*n[:4], c["label"])
        elif c["cmd"] == "circle" and len(n) >= 3:
            c["plan"] = self.plan_circle(*n[:3])

    def render(self, c) -> str:
        """Pass 2: emit SVG; text & arrows see the final position of every shape."""
        cmd, n, col, label, tok = c["cmd"], c["nums"], c["col"], c["label"], c["tok"]
        try:
            if "plan" in c:
                return self.box(c["plan"], col) if cmd in ("box", "rect") else self.circle(c["plan"], col, label)
            if cmd == "text" and len(n) >= 2:
                return self.text(n[0], n[1], n[2] if len(n) > 2 else 22, col, label)
            if cmd in ("arrow", "line") and len(n) >= 4:
                return self.arrow(*n[:4], col, label, head=(cmd == "arrow"))
            if cmd == "underline" and len(n) >= 3:
                return self.underline(*n[:3], col if col != "#1C2541" else "#B7873C")
            if cmd == "axes" and len(n) >= 4:
                parts = tok[5:]
                return self.axes(*n[:4], parts[0] if parts else "", parts[1] if len(parts) > 1 else "")
            if cmd == "curve":
                toks = tok[1:]
                ccol = next((_c(t) for t in toks if t.lower() in COLORS), "#1C2541")
                vals = [_n(v) for t in toks for v in t.split(",") if _isnum(v)]
                return self.curve(ccol, list(zip(vals[0::2], vals[1::2])))
        except (ValueError, IndexError, TypeError, ZeroDivisionError):
            return ""
        return ""

    def command(self, line: str) -> str:
        c = self.parse(line)
        if not c:
            return ""
        self.plan(c)
        return self.render(c)


def _arrowhead(x1, y1, x2, y2, color):
    a = math.atan2(y2 - y1, x2 - x1)
    p1 = (x2 - 14 * math.cos(a - 0.45), y2 - 14 * math.sin(a - 0.45))
    p2 = (x2 - 14 * math.cos(a + 0.45), y2 - 14 * math.sin(a + 0.45))
    return (f'<path d="M{p1[0]:.1f} {p1[1]:.1f} L{x2:.1f} {y2:.1f} L{p2[0]:.1f} {p2[1]:.1f}" '
            f'stroke="{color}" stroke-width="3" fill="none"/>')


def command_to_svg(line: str) -> str:
    """Render one command on a fresh board (no layout context)."""
    return Board().command(line)


DRAW_CMDS = ("box", "rect", "circle", "text", "arrow", "line", "curve", "axes", "underline")


def parse_lesson(raw: str):
    """Parse the model's SAY/DRAW script into [{say, svg}] steps, laid out on one shared board."""
    steps, cmds = [], []  # cmds: (step index, parsed command)
    for line in (raw or "").splitlines():
        line = line.strip().strip("`").strip()
        if not line:
            continue
        head, _, rest = line.partition(":")
        key = head.strip().lstrip("-*# ").upper()
        if key in ("SAY", "BOL", "BOLO") and rest.strip():
            steps.append({"say": rest.strip().strip('"'), "svg": ""})
            continue
        if key == "DRAW":
            body = rest
        elif line.split(" ", 1)[0].lower() in DRAW_CMDS:  # forgot the DRAW: prefix
            body = line
        else:
            continue
        c = Board.parse(body)
        if c and steps:
            cmds.append((len(steps) - 1, c))

    board = Board()
    for _, c in cmds:
        board.plan(c)
    for i, c in cmds:
        steps[i]["svg"] += board.render(c)
    return steps
