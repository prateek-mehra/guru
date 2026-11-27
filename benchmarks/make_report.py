#!/usr/bin/env python3
"""Turn benchmarks/results/*.csv into the markdown tables used in RESULTS.md / README.md.

  python3 benchmarks/make_report.py        # prints markdown tables to stdout
Only reads measured data; no estimation.
"""
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

BENCH = Path(__file__).resolve().parent
RES = BENCH / "results"


def load():
    raw = list(csv.DictReader((RES / "raw_results.csv").open(encoding="utf-8")))
    summ = list(csv.DictReader((RES / "summary.csv").open(encoding="utf-8")))
    env = json.loads((RES / "environment.json").read_text()) if (RES / "environment.json").exists() else {}
    return raw, summ, env


def f(x, nd=0, pre="", suf=""):
    if x in ("", None):
        return "–"
    v = float(x)
    return f"{pre}{v:,.{nd}f}{suf}"


def headline(summ):
    out = ["| Action | Model | Calls | Error rate | p50 latency | p95 latency | Mean in / out tokens | Mean cost / call | Total cost |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summ:
        tok = (f"{f(s['input_tokens_mean'])} / {f(s['output_tokens_mean'])}" if s["input_tokens_mean"] else "–")
        out.append(f"| `{s['action']}` | {s['model']} | {s['calls']} | {float(s['error_rate']) * 100:.1f}% | "
                   f"{f(float(s['latency_p50_ms']) / 1000 if s['latency_p50_ms'] else '', 2, suf=' s')} | "
                   f"{f(float(s['latency_p95_ms']) / 1000 if s['latency_p95_ms'] else '', 2, suf=' s')} | {tok} | "
                   f"{f(s['cost_inr_mean'], 4, '₹')} (${float(s['cost_usd_mean']):.5f}) | "
                   f"{f(s['cost_inr_total'], 2, '₹')} (${float(s['cost_usd_total']):.3f}) |"
                   if s["cost_inr_mean"] else
                   f"| `{s['action']}` | {s['model']} | {s['calls']} | {float(s['error_rate']) * 100:.1f}% | "
                   f"{f(float(s['latency_p50_ms']) / 1000 if s['latency_p50_ms'] else '', 2, suf=' s')} | "
                   f"{f(float(s['latency_p95_ms']) / 1000 if s['latency_p95_ms'] else '', 2, suf=' s')} | {tok} | – | – |")
    return "\n".join(out)


def detail(summ):
    out = ["| Action | Model | Mean latency | Max latency | Upstream (Sarvam) mean | Output tok/s | Cached input tok | Retries | Mean output chars | Audio s | Real-time factor | STT similarity | ₹ per 1K input chars |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summ:
        out.append(f"| `{s['action']}` | {s['model']} | {f(s['latency_mean_ms'], 0, suf=' ms')} | {f(s['latency_max_ms'], 0, suf=' ms')} | "
                   f"{f(s['upstream_mean_ms'], 0, suf=' ms')} | {f(s['output_tokens_per_s_mean'], 1)} | {f(s['cached_input_tokens_mean'])} | "
                   f"{s['retries_total']} | {f(s['output_chars_mean'])} | {f(s['audio_seconds_mean'], 1)} | "
                   f"{f(s['realtime_factor_mean'], 2)} | {f(s['stt_similarity_mean'], 2)} | {f(s['cost_inr_per_1k_input_chars'], 4, '₹')} |")
    return "\n".join(out)


def by_group(raw, key):
    g = defaultdict(list)
    for r in raw:
        if r["success"] == "True":
            g[(r["action"], r["model"], r[key])].append(r)
    return g


def per_paper(raw):
    out = ["| Paper | explain p50 | explain mean in tok | explain mean ₹ | ask p50 | ask mean ₹ |",
           "|---|---:|---:|---:|---:|---:|"]
    g = by_group(raw, "paper")
    papers = list(dict.fromkeys(r["paper"] for r in raw))
    for p in papers:
        e = g.get(("explain", "sarvam-105b", p), [])
        a = g.get(("ask", "sarvam-105b", p), [])
        med = lambda rs: f(statistics.median([float(r["latency_ms"]) for r in rs]) / 1000, 2, suf=" s") if rs else "–"  # noqa
        mn = lambda rs, k, nd: f(statistics.mean([float(r[k]) for r in rs if r[k]]), nd) if rs else "–"  # noqa
        out.append(f"| {p} | {med(e)} | {mn(e, 'input_tokens', 0)} | ₹{mn(e, 'cost_inr', 4)} | {med(a)} | ₹{mn(a, 'cost_inr', 4)} |")
    return "\n".join(out)


def per_length(raw):
    out = ["| Paragraph | Mean chars | explain p50 | explain mean out tok | tts v4-flash p50 | tts v3 p50 |",
           "|---|---:|---:|---:|---:|---:|"]
    g = by_group(raw, "para_id")
    for pid in ("short_abstract", "medium_method", "long_results"):
        e = g.get(("explain", "sarvam-105b", pid), [])
        t4 = g.get(("tts", "bulbul:v4-flash", pid), [])
        t3 = g.get(("tts", "bulbul:v3", pid), [])
        med = lambda rs: f(statistics.median([float(r["latency_ms"]) for r in rs]) / 1000, 2, suf=" s") if rs else "–"  # noqa
        chars = f(statistics.mean([float(r["input_chars"]) for r in e]), 0) if e else "–"
        out_tok = f(statistics.mean([float(r["output_tokens"]) for r in e if r["output_tokens"]]), 0) if e else "–"
        out.append(f"| {pid} | {chars} | {med(e)} | {out_tok} | {med(t4)} | {med(t3)} |")
    return "\n".join(out)


def errors(raw):
    errs = [r for r in raw if r["success"] != "True"]
    if not errs:
        return "No failed calls."
    lines = ["| Paper | Paragraph | Action | Model | HTTP | Error (truncated) |", "|---|---|---|---|---:|---|"]
    for r in errs:
        lines.append(f"| {r['paper']} | {r['para_id']} | {r['action']} | {r['model']} | {r['http_status']} | "
                     f"{r['error'][:120].replace('|', '/')} |")
    return "\n".join(lines)


if __name__ == "__main__":
    raw, summ, env = load()
    print("## Headline\n\n" + headline(summ))
    print("\n## Detail\n\n" + detail(summ))
    print("\n## Per paper\n\n" + per_paper(raw))
    print("\n## Per paragraph\n\n" + per_length(raw))
    print("\n## Errors\n\n" + errors(raw))
    print("\n## Env\n\n" + json.dumps({k: v for k, v in env.items() if k != "pricing_inr"}, indent=1))
