#!/usr/bin/env python3
"""Draw benchmark charts as SVG in the same theme as docs/how-it-works.svg
(ink #1C2541 on cream #FDF8EF, marigold #FBEBCD / #B7873C accents).

  python3 benchmarks/make_charts.py   ->  docs/benchmarks/{summary,latency,cost}.svg
Reads only benchmarks/results/summary.csv (measured data).
"""
import csv
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "benchmarks"
INK, CREAM, GOLD_FILL, GOLD, LANE, MUTED, NOTE = "#1C2541", "#FDF8EF", "#FBEBCD", "#B7873C", "#8A6A2E", "#6B6F80", "#4A4F63"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, monospace"
ACTION_LABEL = {"explain": "explain a paragraph", "ask": "answer a doubt", "tts": "speak (TTS)", "stt": "listen (STT)"}


def head(w, h, title, sub):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="{FONT}">',
            "<style>.lane{font-size:12px;font-weight:700;letter-spacing:1.2px;fill:%s}.t{font-size:16px;font-weight:700;fill:%s}"
            ".d{font-size:13.5px;fill:%s}.note{font-size:12.5px;fill:%s;font-style:italic}.pt{font-family:%s;font-size:12px;fill:#7A5418}"
            ".v{font-size:12.5px;font-weight:700;fill:%s}.ax{font-size:11.5px;fill:%s}</style>" % (LANE, INK, INK, NOTE, MONO, INK, MUTED),
            f'<rect width="{w}" height="{h}" rx="14" fill="{CREAM}"/>',
            f'<text class="lane" x="24" y="34">{escape(title)}</text>',
            f'<text class="note" x="24" y="54">{escape(sub)}</text>']


def label(s):
    return f"{ACTION_LABEL.get(s['action'], s['action'])}"


def bar_chart(rows, name, title, sub, val_fn, fmt, sec_fn=None, sec_label=None, unit=""):
    w, row_h, top, left, right = 960, 46, 84, 300, 120
    h = top + row_h * len(rows) + 52
    vmax = max((sec_fn or val_fn)(s) for s in rows) * 1.05 or 1
    scale = (w - left - right) / vmax
    p = head(w, h, title, sub)
    # grid
    for i in range(5):
        x = left + (w - left - right) * i / 4
        p.append(f'<line x1="{x:.1f}" y1="{top - 8}" x2="{x:.1f}" y2="{h - 40}" stroke="{INK}" stroke-opacity=".08"/>')
        p.append(f'<text class="ax" x="{x:.1f}" y="{h - 22}" text-anchor="middle">{fmt(vmax * i / 4)}{unit}</text>')
    for i, s in enumerate(rows):
        y = top + i * row_h
        p.append(f'<text class="t" x="24" y="{y + 18}" style="font-size:14px">{escape(label(s))}</text>')
        p.append(f'<rect x="24" y="{y + 24}" width="{8 + 7.3 * len(s["model"]):.0f}" height="18" rx="9" fill="#FFFFFF" stroke="{GOLD}"/>')
        p.append(f'<text class="pt" x="{28 + 3.65 * len(s["model"]):.0f}" y="{y + 37}" text-anchor="middle">{escape(s["model"])}</text>')
        v = val_fn(s)
        if sec_fn:
            v2 = sec_fn(s)
            p.append(f'<rect x="{left}" y="{y + 6}" width="{max(v2 * scale, 2):.1f}" height="26" rx="8" fill="{GOLD_FILL}" stroke="{GOLD}"/>')
        p.append(f'<rect x="{left}" y="{y + 10}" width="{max(v * scale, 2):.1f}" height="18" rx="7" fill="{INK}"/>')
        end = max(v, sec_fn(s) if sec_fn else 0) * scale + left
        txt = fmt(v) + unit + (f"  ·  {sec_label} {fmt(sec_fn(s))}{unit}" if sec_fn else "")
        p.append(f'<text class="v" x="{end + 10:.1f}" y="{y + 24}">{escape(txt)}</text>')
    if sec_fn:
        p.append(f'<rect x="{w - 300}" y="22" width="14" height="10" rx="3" fill="{INK}"/><text class="ax" x="{w - 280}" y="31">p50</text>')
        p.append(f'<rect x="{w - 236}" y="20" width="14" height="14" rx="4" fill="{GOLD_FILL}" stroke="{GOLD}"/><text class="ax" x="{w - 216}" y="31">{sec_label}</text>')
    p.append("</svg>")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text("\n".join(p), encoding="utf-8")


def summary_card(rows, env_cost):
    w, top, row_h = 960, 96, 40
    cols = [("ACTION", 24), ("MODEL", 210), ("CALLS", 380), ("ERRORS", 450), ("P50", 530), ("P95", 610),
            ("TOKENS IN / OUT", 690), ("₹ / CALL", 840)]
    h = top + row_h * len(rows) + 50
    p = head(w, h, "BENCHMARK AT A GLANCE", "measured on real Sarvam calls, 3 runs of each paragraph through each endpoint")
    for name, x in cols:
        p.append(f'<text class="lane" x="{x}" y="{top - 14}" style="font-size:11px">{name}</text>')
    for i, s in enumerate(rows):
        y = top + i * row_h
        if i % 2 == 0:
            p.append(f'<rect x="12" y="{y}" width="{w - 24}" height="{row_h - 4}" rx="10" fill="{GOLD_FILL}" fill-opacity=".55"/>')
        tok = (f"{float(s['input_tokens_mean']):,.0f} / {float(s['output_tokens_mean']):,.0f}" if s["input_tokens_mean"] else "–")
        cost = f"₹{float(s['cost_inr_mean']):.4f}" if s["cost_inr_mean"] else "–"
        vals = [label(s), s["model"], s["calls"], f"{float(s['error_rate']) * 100:.0f}%",
                f"{float(s['latency_p50_ms']) / 1000:.2f} s", f"{float(s['latency_p95_ms']) / 1000:.2f} s", tok, cost]
        for (name, x), v in zip(cols, vals):
            cls = "pt" if name == "MODEL" else ("t" if name == "ACTION" else "d")
            style = ' style="font-size:14px"' if cls == "t" else ""
            p.append(f'<text class="{cls}" x="{x}" y="{y + 24}"{style}>{escape(str(v))}</text>')
    p.append(f'<text class="note" x="24" y="{h - 18}">total spend for the whole run: ₹{env_cost:.2f}  ·  prices: Sarvam pay-as-you-go, Nov 2026</text>')
    p.append("</svg>")
    (OUT / "summary.svg").write_text("\n".join(p), encoding="utf-8")


def main():
    rows = list(csv.DictReader((ROOT / "benchmarks" / "results" / "summary.csv").open(encoding="utf-8")))
    order = {"explain": 0, "ask": 1, "tts": 2, "stt": 3}
    rows.sort(key=lambda s: (order.get(s["action"], 9), s["model"]))
    total = sum(float(s["cost_inr_total"] or 0) for s in rows)
    OUT.mkdir(parents=True, exist_ok=True)
    summary_card(rows, total)
    bar_chart(rows, "latency.svg", "END-TO-END LATENCY PER CALL", "seconds, Guru server on localhost → Sarvam API and back",
              lambda s: float(s["latency_p50_ms"]) / 1000, lambda v: f"{v:.1f}", lambda s: float(s["latency_p95_ms"]) / 1000, "p95", " s")
    priced = [s for s in rows if s["cost_inr_mean"]]
    bar_chart(priced, "cost.svg", "MEAN COST PER CALL", "Indian rupees, from Sarvam's published per-token / per-character / per-minute prices",
              lambda s: float(s["cost_inr_mean"]), lambda v: f"₹{v:.3f}")
    print("wrote", ", ".join(str(x.relative_to(ROOT)) for x in sorted(OUT.glob("*.svg"))))


if __name__ == "__main__":
    main()
