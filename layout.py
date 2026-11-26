"""Layout-free whiteboard language -> SVG.

The model is bad at picking coordinates (giant boxes, arrows to nowhere, text
piled up at the bottom), so it no longer gives any. It says WHAT to draw and
this module decides WHERE, in fixed zones on the 800 x 500 board:

    TITLE "Residual Learning"                 top band
    NODE x "Input x" [color] [circle]         flowchart, auto-laid out left->right
    LINK x f "weights" [color]                arrow between nodes (skips become arcs)
    NOTE "deeper = worse accuracy" [color]    bullet in the side column
    FORMULA "H(x) = F(x) + x"                 highlighted equation in the side column
    GRAPH fall "layers" "error" ["caption"]   small chart (axes) in the side column
    PLOT fall red "56-layer" [high|low]       a line on the last GRAPH
    SAAR "deep nets ab aasaan"                one-line summary, bottom band

Old coordinate commands (box/arrow/text/...) are still accepted and laid out
around the zones by draw.Board.
"""

import math
import shlex

from draw import COLORS, W, H, Board, _arrowhead, _c, _t, fit_text, text_w

SEMANTIC = ("title", "node", "link", "note", "formula", "graph", "plot", "saar", "summary")
RAW = ("box", "rect", "circle", "text", "arrow", "line", "curve", "axes", "underline")

# Two-colour theme: deep indigo ink + marigold (a deeper marigold for lines/text on the board).
BLACK = INK = "#1C2541"
GOLD = "#B7873C"
GOLD_BRIGHT = "#E3A33B"
LINE_H = 1.2

SHAPES = {
    "rise": lambda t: 1 - math.exp(-3.2 * t),
    "fall": lambda t: math.exp(-3.2 * t),
    "up": lambda t: t,
    "down": lambda t: 1 - t,
    "peak": lambda t: math.sin(math.pi * t),
    "valley": lambda t: 1 - math.sin(math.pi * t),
    "plateau": lambda t: 1 - math.exp(-6 * t),
    "flat": lambda t: 0.5,
    "zigzag": lambda t: 0.5 + 0.38 * math.sin(7 * math.pi * t) * (1 - 0.4 * t),
    "s": lambda t: 1 / (1 + math.exp(-10 * (t - 0.5))),
}
ALIASES = {"badhna": "rise", "increase": "rise", "girna": "fall", "decrease": "fall", "drop": "fall",
           "linear": "up", "u": "valley", "hill": "peak", "sigmoid": "s", "noisy": "zigzag",
           "saturate": "plateau", "constant": "flat"}


def shape_fn(name):
    name = (name or "").lower()
    return SHAPES.get(ALIASES.get(name, name))


def text_block(lines, x, y_top, size, color, anchor="start", weight=None):
    w = f' font-weight="{weight}"' if weight else ""
    spans = "".join(f'<tspan x="{x:.1f}" y="{y_top + size * 0.95 + i * size * LINE_H:.1f}">{_t(l)}</tspan>'
                    for i, l in enumerate(lines))
    return f'<text font-size="{size:.0f}" fill="{color}" text-anchor="{anchor}"{w}>{spans}</text>'


def _split(line):
    try:
        return shlex.split(line.strip())
    except ValueError:
        return line.replace('"', " ").split()


class Scene:
    def __init__(self):
        self.items = []    # (step, kind, data) semantic
        self.raw = []      # (step, parsed raw command)
        self.board = Board()

    # ------------------------------------------------------------ parsing
    def add(self, step, line):
        tok = _split(line)
        if not tok:
            return
        kind = tok[0].lower().rstrip(":")
        args = tok[1:]
        if kind in RAW:
            c = Board.parse(line)
            if c:
                self.raw.append((step, c))
            return
        if kind not in SEMANTIC:
            return
        colors = [a for a in args if a.lower() in COLORS]
        flags = {a.lower() for a in args if a.lower() in ("circle", "high", "low", "mid")}
        words = [a for a in args if a not in colors and a.lower() not in flags]
        col = _c(colors[0]) if colors else None
        d = {"col": col, "flags": flags, "words": words}
        if kind == "summary":
            kind = "saar"
        self.items.append((step, kind, d))

    # ------------------------------------------------------------ layout
    def layout(self):
        kinds = [k for _, k, _ in self.items]
        nodes = self._collect_nodes()
        formulas = [it for it in self.items if it[1] == "formula"][:2]
        side = [it for it in self.items if it[1] == "note" or it in formulas][:6]
        graphs = [(s, k, d) for s, k, d in self.items if k == "graph"][:1]
        has_nodes, has_side, has_graph = bool(nodes), bool(side), bool(graphs)

        top = 70 if "title" in kinds else 18
        bottom = 432 if "saar" in kinds else 488
        if has_nodes and (has_side or has_graph):
            diagram = (16, top, 500, bottom - top)
            col_x, col_w = 530, 254
        elif has_nodes:
            diagram = (16, top, W - 32, bottom - top)
            col_x, col_w = None, 0
        else:
            diagram = None
            col_x, col_w = (16, W - 32) if not has_graph else (16, 450)
        out = {}
        out.update(self._place_title_saar(bottom))
        if has_nodes:
            out.update(self._place_nodes(nodes, diagram))
        out.update(self._place_side(side, graphs, has_nodes, col_x, col_w, top, bottom))
        return out

    def _place_title_saar(self, bottom):
        out = {}
        for i, (s, k, d) in enumerate(self.items):
            if k == "title" and not any(kk == "title" for _, kk, _ in self.items[:i]):
                txt = " ".join(d["words"])
                sz, lines, _ = fit_text(txt, W - 40, 30, min_size=20, max_lines=1)
                out[i] = text_block(lines[:1], 24, 16, sz, d["col"] or BLACK)
                tw = min(W - 40, text_w(lines[0], sz))
                out[i] += (f'<path d="M24 {16 + sz * 1.25:.1f} Q{24 + tw / 2:.1f} {20 + sz * 1.25:.1f} '
                           f'{24 + tw:.1f} {14 + sz * 1.25:.1f}" stroke="{GOLD_BRIGHT}" stroke-width="4" fill="none"/>')
                self.board.texts.append((20, 10, 28 + tw, 24 + sz * 1.3))
            elif k == "saar" and not any(kk == "saar" for _, kk, _ in self.items[:i]):
                txt = " ".join(d["words"])
                sz, lines, _ = fit_text(txt, W - 60, 24, min_size=17, max_lines=2)
                h = len(lines) * sz * LINE_H + 14
                y = H - 8 - h
                col = d["col"] or "#B7873C"
                out[i] = (f'<rect x="16" y="{y:.1f}" width="{W - 32}" height="{h:.1f}" rx="12" stroke="{col}" '
                          f'stroke-width="2.5" fill="{col}" fill-opacity="0.06"/>'
                          + text_block(lines, W / 2, y + 7, sz, col, "middle"))
                self.board.shapes.append(((16, y, W - 16, y + h), "box", None))
        return out

    # ---- flowchart
    def _collect_nodes(self):
        nodes, order = {}, []
        for i, (s, k, d) in enumerate(self.items):
            if k == "node" and d["words"] and d["words"][0].strip():
                nid = d["words"][0].lower().strip()
                if nid in nodes:
                    continue
                label = " ".join(d["words"][1:]) or d["words"][0]
                nodes[nid] = {"i": i, "step": s, "label": label, "col": d["col"],
                              "circle": "circle" in d["flags"]}
                order.append(nid)
        self.links = []
        for i, (s, k, d) in enumerate(self.items):
            if k == "link" and len(d["words"]) >= 2 and d["words"][0].strip() and d["words"][1].strip():
                a, b = d["words"][0].lower().strip(), d["words"][1].lower().strip()
                # links only join declared NODEs; "LINK a Something" is usually a stray label, so skip it
                if a in nodes and b in nodes and a != b:
                    self.links.append({"i": i, "a": a, "b": b, "label": " ".join(d["words"][2:]),
                                       "col": d["col"] or BLACK})
        order = order[:7]
        self.links = [l for l in self.links if l["a"] in order and l["b"] in order]
        return {n: nodes[n] for n in order}

    def _place_nodes(self, nodes, region):
        rx, ry, rw, rh = region
        ids = list(nodes)
        layer = {n: 0 for n in ids}
        if self.links:
            for _ in range(len(ids)):  # longest-path layering (cycles just stop growing)
                for l in self.links:
                    if layer[l["b"]] < layer[l["a"]] + 1 and layer[l["a"]] + 1 < len(ids):
                        layer[l["b"]] = layer[l["a"]] + 1
            linked = {l["a"] for l in self.links} | {l["b"] for l in self.links}
            loose = [n for n in ids if n not in linked]   # nodes nobody linked: own row underneath
            flow = [n for n in ids if n in linked]
            used = sorted({layer[n] for n in flow})
            layer = {n: used.index(layer[n]) for n in flow}
            L = len(used)
            if L > 4:  # snake into two rows
                per = math.ceil(L / 2)
                cell = {n: (layer[n] // per, layer[n] % per) for n in flow}
                rows, cols = 2, per
            else:
                cell = {n: (0, layer[n]) for n in flow}
                rows, cols = 1, L
            if loose:
                for k, n in enumerate(loose):
                    cell[n] = (rows, k % cols)
                rows += 1
            # order nodes inside each column by their parents' positions (fewer crossing arrows)
            rank = {n: k for k, n in enumerate(ids)}
            for _ in range(2):
                for n in flow:
                    parents = [rank[l["a"]] for l in self.links if l["b"] == n and cell[l["a"]][1] < cell[n][1]]
                    if parents:
                        rank[n] = sum(parents) / len(parents) + rank[n] * 1e-3
            ids = sorted(ids, key=lambda n: (cell[n], rank[n]))
        else:  # no links: a tidy grid in declaration order
            cols = {1: 1, 2: 2, 3: 3, 4: 2}.get(len(ids), 3)
            rows = math.ceil(len(ids) / cols)
            cell = {n: (k // cols, k % cols) for k, n in enumerate(ids)}
        cw, ch = rw / cols, rh / rows
        groups = {}
        for n in ids:
            groups.setdefault(cell[n], []).append(n)
        out = {}
        self.pos = {}
        for (r, c), members in groups.items():
            slot_h = ch / len(members)
            for j, n in enumerate(members):
                nd = nodes[n]
                max_w = max(80, min(cw - 40, 240))
                base = 26 if len(nodes) <= 2 else 24 if len(nodes) <= 4 else 21
                if nd["circle"]:
                    max_w = min(max_w, 130)
                sz, lines, _ = fit_text(nd["label"], max_w - 20, base, min_size=15, max_lines=3)
                tw = max(text_w(l, sz) for l in lines)
                th = len(lines) * sz * LINE_H
                cx = rx + cw * (c + 0.5)
                cy = ry + ch * r + slot_h * (j + 0.5)
                col = nd["col"] or [INK, GOLD][len(self.pos) % 2]
                if nd["circle"]:
                    rad = math.hypot(tw, th) / 2 + 8
                    b = (cx - rad, cy - rad, cx + rad, cy + rad)
                    svg = (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rad:.1f}" stroke="{col}" stroke-width="3" '
                           f'fill="{col}" fill-opacity="0.08"/>')
                else:
                    bw, bh = max(tw + 32, 110), th + 26
                    b = (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
                    svg = (f'<rect x="{b[0]:.1f}" y="{b[1]:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="12" '
                           f'stroke="{col}" stroke-width="3" fill="{col}" fill-opacity="0.08"/>')
                svg += text_block(lines, cx, cy - th / 2, sz, col, "middle")
                self.pos[n] = {"b": b, "cell": cell[n], "circle": nd["circle"]}
                self.board.shapes.append((b, "box", None))
                key = nd["i"] if nd["i"] is not None else ("auto", n)
                out[key] = out.get(key, "") + svg
        for l in self.links:
            out[l["i"]] = out.get(l["i"], "") + self._edge(l)
        return out

    def _edge(self, l):
        A, B = self.pos[l["a"]], self.pos[l["b"]]
        a, b = A["b"], B["b"]
        (ra, ca), (rb, cb) = A["cell"], B["cell"]
        col = l["col"]
        acx, acy, bcx, bcy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if ra == rb and cb == ca + 1:                       # neighbours: straight, side to side
            p0, p2 = (a[2] + 4, acy), (b[0] - 6, bcy)
            ctrl = None
        elif ra == rb and ca == cb:                          # same column: straight, top/bottom
            if bcy > acy:
                p0, p2 = (acx, a[3] + 4), (bcx, b[1] - 6)
            else:
                p0, p2 = (acx, a[1] - 4), (bcx, b[3] + 6)
            ctrl = None
        elif ra == rb and cb > ca:                           # skip connection: arc over the top
            p0, p2 = (acx, a[1] - 4), (bcx, b[1] - 6)
            ctrl = ((acx + bcx) / 2, max(8, min(a[1], b[1]) - 75))
        elif ra == rb:                                       # backwards: arc underneath
            p0, p2 = (acx, a[3] + 4), (bcx, b[3] + 6)
            ctrl = ((acx + bcx) / 2, min(H - 70, max(a[3], b[3]) + 70))
        else:                                                # to the next row
            p0, p2 = (acx, a[3] + 4), (bcx, b[1] - 6)
            ctrl = None
        if ctrl is None:
            d = f"M{p0[0]:.1f} {p0[1]:.1f} L{p2[0]:.1f} {p2[1]:.1f}"
            head = _arrowhead(p0[0], p0[1], p2[0], p2[1], col)
            mid = ((p0[0] + p2[0]) / 2, (p0[1] + p2[1]) / 2)
        else:
            d = f"M{p0[0]:.1f} {p0[1]:.1f} Q{ctrl[0]:.1f} {ctrl[1]:.1f} {p2[0]:.1f} {p2[1]:.1f}"
            head = _arrowhead(ctrl[0], ctrl[1], p2[0], p2[1], col)
            mid = (0.25 * p0[0] + 0.5 * ctrl[0] + 0.25 * p2[0], 0.25 * p0[1] + 0.5 * ctrl[1] + 0.25 * p2[1])
        svg = f'<path d="{d}" stroke="{col}" stroke-width="3" fill="none"/>' + head
        if l["label"]:
            horizontal = ctrl is None and abs(p2[1] - p0[1]) < abs(p2[0] - p0[0])
            if horizontal:   # sit just above the arrow
                prefer = lambda w, h: (mid[0] - w / 2, mid[1] - h - 4, mid[0] + w / 2, mid[1] - 4)
            elif ctrl is None:  # vertical: to the right of it
                prefer = lambda w, h: (mid[0] + 8, mid[1] - h / 2, mid[0] + 8 + w, mid[1] + h / 2)
            else:            # on the arc's peak
                prefer = lambda w, h: (mid[0] - w / 2, mid[1] - h - 2, mid[0] + w / 2, mid[1] - 2)
            gap = abs(p2[0] - p0[0]) if horizontal else 160
            svg += self.board._place_label(l["label"], mid[0], mid[1], 17, col, max_w=max(70, min(gap, 170)),
                                           prefer=prefer)
        return svg

    # ---- side column: graph, formula, notes
    def _place_side(self, side, graphs, has_nodes, x, w, top, bottom):
        out = {}
        if x is None:
            return out
        y = top
        if graphs:
            gi = self.items.index(graphs[0])
            if has_nodes or side:
                gx, gw = (x, w) if has_nodes else (16, 450)
                gh = min(230, bottom - top) if (has_nodes and side) else bottom - top
                gy = top
                if not has_nodes:  # graph on the left, notes on the right
                    x, w = 490, W - 506
            else:
                gx, gy, gw, gh = 60, top, W - 120, bottom - top
            out[gi] = self._graph(gi, gx, gy, gw, gh)
            if has_nodes:
                y = gy + gh + 14
        # notes / formulas stacked; shrink the font until they all fit
        avail = bottom - y
        base = 22 if not has_nodes and not graphs else 19
        for size in range(base, 14, -1):
            blocks, total = [], 0
            for s, k, d in side:
                txt = " ".join(d["words"])
                if k == "formula":
                    sz, lines, _ = fit_text(txt, w - 24, size + 2, min_size=14, max_lines=2)
                    h = len(lines) * sz * LINE_H + 18
                else:
                    sz, lines, _ = fit_text(txt, w - 22, size, min_size=14, max_lines=3)
                    h = len(lines) * sz * LINE_H + 10
                blocks.append((s, k, d, sz, lines, h))
                total += h + 8
            if total <= avail:
                break
        for s, k, d, sz, lines, h in blocks:
            i = self.items.index((s, k, d))
            if y + h > bottom + 4:
                break  # out of room: drop the rest rather than overlap
            if k == "formula":
                col = d["col"] or "#1C2541"
                out[i] = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="10" stroke="{col}" '
                          f'stroke-width="3" fill="{col}" fill-opacity="0.08"/>'
                          + text_block(lines, x + w / 2, y + 9, sz, col, "middle"))
            else:
                col = d["col"] or BLACK
                out[i] = (f'<circle cx="{x + 7:.1f}" cy="{y + 5 + sz * 0.6:.1f}" r="4" fill="{col}" stroke="{col}"/>'
                          + text_block(lines, x + 20, y + 5, sz, col))
            self.board.texts.append((x, y, x + w, y + h))
            y += h + 8
        return out

    def _graph(self, gi, x, y, w, h):
        d = self.items[gi][2]
        words = d["words"]
        self.graph_shape = shape_fn(words[0]) if words else None
        labels = words[1:] if self.graph_shape else words
        xl = labels[0] if len(labels) > 0 else ""
        yl = labels[1] if len(labels) > 1 else ""
        cap = labels[2] if len(labels) > 2 else ""
        top_pad = 30 if cap else 10
        ox, oy = x + 30, y + h - 30
        aw, ah = w - 44, h - 30 - top_pad - 8
        self.graph_box = (ox, oy, aw, ah)
        self.plot_count = 0
        svg = ""
        if cap:
            sz, lines, _ = fit_text(cap, w, 18, min_size=14, max_lines=1)
            svg += text_block(lines[:1], x + w / 2, y, sz, BLACK, "middle")
        svg += (f'<path d="M{ox:.1f} {oy:.1f} L{ox + aw:.1f} {oy:.1f}" stroke="{BLACK}" stroke-width="3" fill="none"/>'
                + _arrowhead(ox, oy, ox + aw, oy, BLACK)
                + f'<path d="M{ox:.1f} {oy:.1f} L{ox:.1f} {oy - ah:.1f}" stroke="{BLACK}" stroke-width="3" fill="none"/>'
                + _arrowhead(ox, oy, ox, oy - ah, BLACK))
        if xl:
            svg += text_block([xl], ox + aw / 2, oy + 4, 16, BLACK, "middle")
        if yl:
            svg += (f'<text font-size="16" fill="{BLACK}" text-anchor="middle" '
                    f'transform="rotate(-90 {ox - 12:.1f} {oy - ah / 2:.1f})" x="{ox - 12:.1f}" y="{oy - ah / 2:.1f}">'
                    f'{_t(yl)}</text>')
        self.board.shapes.append(((x, y, x + w, y + h), "box", None))
        if self.graph_shape:  # GRAPH itself named a shape: draw that line now
            svg += self._plot_line(self.graph_shape, "#1C2541", "", "mid")
        return svg

    def _plot_line(self, fn, col, legend, level):
        ox, oy, aw, ah = self.graph_box
        lo, span = {"high": (0.38, 0.55), "low": (0.06, 0.5)}.get(level, (0.12, 0.72))
        pts = [(ox + 8 + (aw - 24) * t / 24, oy - ah * (lo + span * max(0, min(1, fn(t / 24))))) for t in range(25)]
        d = "M" + " L".join(f"{px:.1f} {py:.1f}" for px, py in pts)
        svg = f'<path d="{d}" stroke="{col}" stroke-width="3" fill="none"/>'
        if legend:
            ex, ey = pts[-1]
            sz, lines, _ = fit_text(legend, 110, 15, min_size=13, max_lines=1)
            lw = text_w(lines[0], sz)
            bx = min(ex - lw, ox + aw - lw)
            svg += text_block(lines[:1], bx, ey - sz - 6 if level != "low" else ey + 4, sz, col)
        self.plot_count += 1
        return svg

    # ------------------------------------------------------------ render
    def render(self, n_steps):
        svgs = [""] * n_steps
        placed = self.layout()
        for key, svg in placed.items():
            if isinstance(key, tuple):  # auto-created node: show with the link that created it
                step = next((s for s, k, d in self.items if k == "link" and key[1] in
                             [w.lower() for w in d["words"][:2]]), 0)
            else:
                step = self.items[key][0]
            svgs[step] += svg
        # plots draw on the graph in their own step
        for i, (s, k, d) in enumerate(self.items):
            if k == "plot" and getattr(self, "graph_box", None) and self.plot_count < 4:
                fn = next((shape_fn(w) for w in d["words"] if shape_fn(w)), SHAPES["fall"])
                legend = " ".join(w for w in d["words"] if not shape_fn(w))
                level = next((f for f in d["flags"] if f in ("high", "low", "mid")), "mid")
                col = d["col"] or [GOLD, INK][self.plot_count % 2]
                svgs[s] += self._plot_line(fn, col, legend, level)
        # legacy coordinate commands, laid out around everything above
        for _, c in self.raw:
            self.board.plan(c)
        for s, c in self.raw:
            svgs[s] += self.board.render(c)
        return svgs


def parse_lesson(raw: str):
    """Parse the model's SAY/DRAW script into [{say, svg}] steps."""
    steps, scene = [], Scene()
    for line in (raw or "").splitlines():
        line = line.strip().strip("`").strip()
        if not line:
            continue
        head, _, rest = line.partition(":")
        key = head.strip().lstrip("-*# ").upper()
        if key in ("SAY", "BOL", "BOLO") and rest.strip():
            steps.append({"say": rest.strip().strip('"'), "svg": ""})
            continue
        if not steps:
            continue
        if key == "DRAW":
            scene.add(len(steps) - 1, rest)
        elif line.split(" ", 1)[0].lower().rstrip(":") in SEMANTIC + RAW:
            scene.add(len(steps) - 1, line)
    if steps:
        for i, svg in enumerate(scene.render(len(steps))):
            steps[i]["svg"] = svg
    return steps
