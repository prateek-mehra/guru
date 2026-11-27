#!/usr/bin/env python3
"""Benchmark Guru's AI-backed server APIs (Sarvam AI) on paragraphs from 5 famous AI papers.

What it measures
----------------
The real Guru FastAPI server (guru.server:app) is started with uvicorn on a local port
inside this process, and every benchmark call goes over HTTP to it, exactly like the
browser UI. An httpx event hook on the server's own client records each upstream Sarvam
response, so token usage, retries and upstream latency come from Sarvam's real responses.

Actions x models benchmarked (everything the server calls):
  explain  POST /api/explain   sarvam-105b      (chat completions, reasoning off)
  ask      POST /api/ask       sarvam-105b      (doubt answering)
  tts      POST /api/tts       bulbul:v4-flash  (speaker shubh_enhi_banking, the default)
                               bulbul:v3        (speaker shubh, the server's v3 path)
  stt      POST /api/stt       saaras:v4        (codemix; input = audio Guru itself spoke)

Usage (from the repo root, with SARVAM_API_KEY in .env):
  .venv/bin/python benchmarks/run_benchmark.py            # full run, 3 reps
  .venv/bin/python benchmarks/run_benchmark.py --reps 1   # quicker
  .venv/bin/python benchmarks/run_benchmark.py --prepare-only   # just fetch/select inputs

Inputs: on first run, papers are downloaded from ar5iv (arXiv HTML) and paragraphs are
selected by the deterministic rule in select_paragraphs(); the selection is frozen in
benchmarks/inputs/paragraphs.json and reused on later runs (use --refresh-inputs to redo).
Full-paper context text (sent as the `paper` field, like the UI does) is cached under
benchmarks/inputs/.cache/ (git-ignored).

Outputs: benchmarks/results/raw_results.csv, summary.csv, environment.json.
Nothing here is estimated: a metric that could not be measured is left blank.
"""

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import os
import platform
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "benchmarks"
INPUTS = BENCH / "inputs"
CACHE = INPUTS / ".cache"
RESULTS = BENCH / "results"
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Pricing (INR, excl. GST), Sarvam pay-as-you-go, read 2026-11-27 from
#   https://docs.sarvam.ai/api/getting-started/pricing  and  https://www.sarvam.ai/api-pricing
# sarvam-105b rates are "introductory pricing" valid through end of 2026.
# ---------------------------------------------------------------------------
PRICING = {
    "sarvam-105b": {"input_per_m": 10.0, "cached_input_per_m": 0.25, "output_per_m": 25.0},
    "bulbul:v4-flash": {"per_1k_chars": 2.0},
    "bulbul:v3": {"per_1k_chars": 3.0},
    "saaras:v4": {"per_minute": 0.49},  # billed per second
}
USD_INR = 94.0  # approx. mid-market rate, late Nov 2026 (override with --usd-inr)
BUDGET_INR = 400.0  # hard stop (~US$4.25); override with --budget-inr

PAPERS = [
    {"id": "transformer", "arxiv": "1706.03762", "title": "Attention Is All You Need (Vaswani et al., 2017)"},
    {"id": "bert", "arxiv": "1810.04805", "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding (Devlin et al., 2018)"},
    {"id": "gpt3", "arxiv": "2005.14165", "title": "Language Models are Few-Shot Learners (Brown et al., 2020)"},
    {"id": "resnet", "arxiv": "1512.03385", "title": "Deep Residual Learning for Image Recognition (He et al., 2015)"},
    {"id": "ddpm", "arxiv": "2006.11239", "title": "Denoising Diffusion Probabilistic Models (Ho et al., 2020)"},
]

METHOD_RE = re.compile(r"model|method|architecture|approach|background|residual|diffusion|attention|"
                       r"pre-?training|fine-?tuning|training|formulation|network", re.I)
RESULTS_RE = re.compile(r"result|experiment|evaluation|ablation|analysis|imagenet|cifar|translation|"
                        r"glue|squad|sample quality|comparison|performance|benchmark", re.I)
SKIP_RE = re.compile(r"reference|bibliograph|acknowledg|appendix", re.I)


# ---------------------------------------------------------------------------
# ar5iv (LaTeXML HTML) -> paragraphs
# ---------------------------------------------------------------------------

class Ar5ivParser(HTMLParser):
    """Collects the abstract and body paragraphs (div.ltx_para) with their nearest heading.
    Math is replaced by its TeX alttext; figures, tables, footnotes and the bibliography are skipped."""

    SKIP_CLASSES = ("ltx_bibliography", "ltx_note_outer", "ltx_figure", "ltx_table", "ltx_tabular",
                    "ltx_authors", "ltx_pagination", "ltx_page_footer", "ltx_dates", "ltx_role_footnote")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []  # (tag, flags)
        self.paras = []  # {"heading", "text", "abstract"}
        self.heading = ""
        self._head_buf = None
        self._para_buf = None
        self._para_depth = None
        self._skip = 0
        self._math = 0
        self._in_abstract = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        flags = set()
        if any(c in cls.split() for c in self.SKIP_CLASSES) or tag in ("figure", "table", "nav", "footer", "script", "style"):
            flags.add("skip")
            self._skip += 1
        if "ltx_abstract" in cls.split():
            flags.add("abstract")
            self._in_abstract += 1
        if tag == "math":
            flags.add("math")
            if self._math == 0 and not self._skip:
                self._emit(" " + (a.get("alttext") or "") + " ")
            self._math += 1
        if re.fullmatch(r"h[1-6]", tag) and "ltx_title" in cls and not self._skip:
            flags.add("heading")
            self._head_buf = []
        is_para = tag == "div" and "ltx_para" in cls.split()
        is_abs_p = tag == "p" and self._in_abstract  # ar5iv abstracts are bare <p class="ltx_p">
        if (is_para or is_abs_p) and self._para_buf is None and not self._skip:
            flags.add("para")
            self._para_buf = []
        if tag in ("p", "br") and self._para_buf is not None:
            self._emit(" ")
        if tag in ("br", "img", "hr", "meta", "link", "input"):
            return  # void elements: no end tag
        self.stack.append((tag, flags))

    def handle_endtag(self, tag):
        # pop up to the matching tag (HTML can be sloppy)
        while self.stack:
            t, flags = self.stack.pop()
            self._close(flags)
            if t == tag:
                break

    def _close(self, flags):
        if "skip" in flags:
            self._skip -= 1
        if "math" in flags:
            self._math -= 1
        if "heading" in flags and self._head_buf is not None:
            self.heading = _norm(" ".join(self._head_buf))
            self._head_buf = None
        if "para" in flags and self._para_buf is not None:
            text = _norm("".join(self._para_buf))
            if text:
                self.paras.append({"heading": "Abstract" if self._in_abstract else self.heading,
                                   "text": text, "abstract": bool(self._in_abstract)})
            self._para_buf = None
        if "abstract" in flags:
            self._in_abstract -= 1

    def _emit(self, s):
        if self._head_buf is not None:
            self._head_buf.append(s)
        elif self._para_buf is not None:
            self._para_buf.append(s)

    def handle_data(self, data):
        if self._skip or self._math:
            return
        if self._head_buf is not None or self._para_buf is not None:
            self._emit(data)


def _norm(s):
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s+([,.;:)])", r"\1", s)


def fetch(url, dest):
    if dest.exists() and dest.stat().st_size > 10_000:
        return dest.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": "guru-benchmark/1.0 (research benchmark)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        text = r.read().decode("utf-8", "replace")
    dest.write_text(text, encoding="utf-8")
    time.sleep(1.0)  # be polite to arXiv
    return text


def load_paper(p):
    CACHE.mkdir(parents=True, exist_ok=True)
    last = None
    for url in (f"https://ar5iv.labs.arxiv.org/html/{p['arxiv']}", f"https://ar5iv.org/html/{p['arxiv']}"):
        try:
            raw = fetch(url, CACHE / f"{p['id']}.html")
            parser = Ar5ivParser()
            parser.feed(raw)
            if len(parser.paras) >= 10:
                return url, parser.paras
            last = f"only {len(parser.paras)} paragraphs parsed from {url}"
            (CACHE / f"{p['id']}.html").unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            last = f"{url}: {e}"
    raise RuntimeError(f"Could not load {p['arxiv']}: {last}")


def select_paragraphs(paras):
    """Deterministic pick: short = abstract; medium = first 450-1000 char paragraph under a
    method-like heading; long = longest paragraph (<= 3000 chars) under a results-like heading."""
    body = [x for x in paras if not x["abstract"] and not SKIP_RE.search(x["heading"])]
    abstract = " ".join(x["text"] for x in paras if x["abstract"])
    if not abstract:  # some renderings put the abstract in a plain paragraph
        abstract = body[0]["text"]
    med = [x for x in body if METHOD_RE.search(x["heading"]) and 450 <= len(x["text"]) <= 1000
           and not re.search(r"introduction", x["heading"], re.I)]
    med = med or [x for x in body if 450 <= len(x["text"]) <= 1000]
    res = [x for x in body if RESULTS_RE.search(x["heading"]) and len(x["text"]) <= 3000]
    longp = max(res or [x for x in body if len(x["text"]) <= 3000], key=lambda x: len(x["text"]))
    return [
        {"para_id": "short_abstract", "kind": "short", "heading": "Abstract", "text": abstract},
        {"para_id": "medium_method", "kind": "medium", "heading": med[0]["heading"], "text": med[0]["text"]},
        {"para_id": "long_results", "kind": "long", "heading": longp["heading"], "text": longp["text"]},
    ]


def prepare_inputs(refresh=False):
    INPUTS.mkdir(parents=True, exist_ok=True)
    out = INPUTS / "paragraphs.json"
    questions = json.loads((INPUTS / "questions.json").read_text(encoding="utf-8"))
    if out.exists() and not refresh:
        data = json.loads(out.read_text(encoding="utf-8"))
    else:
        data = {"created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "source": "ar5iv.labs.arxiv.org HTML renderings of the arXiv papers",
                "rule": select_paragraphs.__doc__.strip(), "papers": []}
        for p in PAPERS:
            url, paras = load_paper(p)
            print(f"  {p['id']}: {len(paras)} paragraphs from {url}")
            sel = select_paragraphs(paras)
            for s in sel:
                s["chars"] = len(s["text"])
                s["sha256"] = hashlib.sha256(s["text"].encode()).hexdigest()[:16]
                s["question"] = questions[p["id"]][s["para_id"]]
            data["papers"].append({**p, "url": url, "n_paragraphs": len(paras), "paragraphs": sel})
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # full-paper context (what the UI sends as `paper`): every body paragraph, cached locally
    for p in data["papers"]:
        ctx_file = CACHE / f"{p['id']}.context.txt"
        if not ctx_file.exists():
            _, paras = load_paper(p)
            ctx_file.write_text("\n\n".join(x["text"] for x in paras if not SKIP_RE.search(x["heading"])),
                                encoding="utf-8")
        p["context"] = ctx_file.read_text(encoding="utf-8")
    return data


# ---------------------------------------------------------------------------
# MP3 duration (frame walk, no dependencies) for STT/TTS audio seconds
# ---------------------------------------------------------------------------
_BR = {1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
       2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]}
_SR = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def mp3_seconds(b):
    i, secs, frames = 0, 0.0, 0
    if b[:3] == b"ID3" and len(b) > 10:
        i = 10 + ((b[6] & 0x7F) << 21 | (b[7] & 0x7F) << 14 | (b[8] & 0x7F) << 7 | (b[9] & 0x7F))
    while i + 4 <= len(b):
        if b[i] != 0xFF or (b[i + 1] & 0xE0) != 0xE0:
            i += 1
            continue
        ver = (b[i + 1] >> 3) & 3          # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
        layer = (b[i + 1] >> 1) & 3        # 1 = Layer III
        bri, sri, pad = (b[i + 2] >> 4) & 15, (b[i + 2] >> 2) & 3, (b[i + 2] >> 1) & 1
        if ver == 1 or layer != 1 or bri in (0, 15) or sri == 3:
            i += 1
            continue
        br = _BR[1 if ver == 3 else 2][bri] * 1000
        sr = _SR[ver][sri]
        spf = 1152 if ver == 3 else 576
        size = (144 if ver == 3 else 72) * br // sr + pad
        secs += spf / sr
        frames += 1
        i += max(size, 1)
    return round(secs, 3) if frames > 5 else None


# ---------------------------------------------------------------------------
# Server + upstream capture
# ---------------------------------------------------------------------------

class Upstream:
    """httpx event hooks on guru.server.http: one record per real Sarvam response."""

    def __init__(self):
        self.lock = threading.Lock()
        self.records = []

    def on_request(self, request):
        request.extensions["bench_t0"] = time.perf_counter()

    def on_response(self, response):
        response.read()
        t0 = response.request.extensions.get("bench_t0")
        rec = {"path": response.request.url.path, "status": response.status_code,
               "ms": (time.perf_counter() - t0) * 1000 if t0 else None, "usage": None, "json": None}
        try:
            j = response.json()
            rec["json"] = j
            rec["usage"] = j.get("usage")
        except Exception:  # noqa: BLE001
            pass
        with self.lock:
            self.records.append(rec)

    def drain(self):
        with self.lock:
            r, self.records = self.records, []
        return r


def start_server(port):
    import uvicorn
    import guru.server as srv
    up = Upstream()
    srv.http.event_hooks = {"request": [up.on_request], "response": [up.on_response]}
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("uvicorn did not start")
    return srv, server, up


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

FIELDS = ["run_id", "timestamp_utc", "paper", "para_id", "para_kind", "input_chars", "action", "model",
          "speaker", "rep", "success", "http_status", "error", "latency_ms", "ttft_ms", "upstream_ms",
          "upstream_calls", "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
          "total_tokens", "output_tokens_per_s", "cost_inr", "cost_usd", "cost_inr_per_1k_input_chars",
          "output_chars", "steps", "audio_seconds", "realtime_factor", "stt_similarity", "finish_reason"]


def llm_cost(inp, cached, out):
    p = PRICING["sarvam-105b"]
    return ((inp - cached) * p["input_per_m"] + cached * p["cached_input_per_m"] + out * p["output_per_m"]) / 1e6


def say_text(steps):
    return " ".join(s.get("say", "") for s in steps or [])


def similarity(a, b):
    import difflib
    norm = lambda s: re.sub(r"[^\w]+", " ", s.lower()).strip()  # noqa: E731
    return round(difflib.SequenceMatcher(None, norm(a), norm(b)).ratio(), 3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--actions", default="explain,ask,tts,stt")
    ap.add_argument("--papers", default=",".join(p["id"] for p in PAPERS))
    ap.add_argument("--tts-chars", type=int, default=400, help="max chars of Guru's narration sent per TTS call")
    ap.add_argument("--budget-inr", type=float, default=BUDGET_INR)
    ap.add_argument("--usd-inr", type=float, default=USD_INR)
    ap.add_argument("--refresh-inputs", action="store_true")
    ap.add_argument("--prepare-only", action="store_true")
    args = ap.parse_args()

    print("Preparing inputs ...")
    data = prepare_inputs(args.refresh_inputs)
    if args.prepare_only:
        print(f"Inputs ready: {INPUTS / 'paragraphs.json'}")
        return
    RESULTS.mkdir(parents=True, exist_ok=True)

    port = free_port()
    srv, server, up = start_server(port)
    base = f"http://127.0.0.1:{port}"
    import httpx
    client = httpx.Client(timeout=300)
    health = client.get(f"{base}/api/health").json()
    print("Server:", health)
    if health.get("mock"):
        print("WARNING: MOCK=1 - numbers will NOT reflect Sarvam. Use only to test the harness.")
    if not srv.SARVAM_KEY and not health.get("mock"):
        sys.exit("SARVAM_API_KEY is not set (put it in .env). Aborting; nothing was measured.")

    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    actions = args.actions.split(",")
    wanted = set(args.papers.split(","))
    rows, spent = [], 0.0
    raw_path = RESULTS / "raw_results.csv"
    fh = raw_path.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=FIELDS)
    writer.writeheader()
    tts_audio = {}   # (paper, para) -> (mp3 bytes, source text) from bulbul:v4-flash, for STT
    narration = {}   # (paper, para) -> narration text from first successful explain

    def record(row):
        nonlocal spent
        spent += row.get("cost_inr") or 0.0
        row["run_id"] = run_id
        row["timestamp_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        if row.get("cost_inr") is not None:
            row["cost_usd"] = round(row["cost_inr"] / args.usd_inr, 6)
            row["cost_inr"] = round(row["cost_inr"], 6)
            if row.get("input_chars"):
                row["cost_inr_per_1k_input_chars"] = round(row["cost_inr"] / row["input_chars"] * 1000, 6)
        writer.writerow({k: row.get(k, "") for k in FIELDS})
        fh.flush()
        rows.append(row)
        print(f"  {row['paper']:<11} {row['para_id']:<15} {row['action']:<7} {row['model']:<16} rep{row['rep']} "
              f"{'ok ' if row['success'] else 'ERR'} {row.get('latency_ms', 0):>8.0f} ms  "
              f"tok {row.get('input_tokens', '')}/{row.get('output_tokens', '')}  "
              f"Rs{row.get('cost_inr') or 0:.4f}  (total Rs{spent:.2f})")
        if spent > args.budget_inr:
            raise SystemExit(f"Budget guard: spent Rs{spent:.2f} > Rs{args.budget_inr}. Stopping.")

    def call(method, path, **kw):
        up.drain()
        t0 = time.perf_counter()
        try:
            r = client.request(method, f"{base}{path}", **kw)
            ms = (time.perf_counter() - t0) * 1000
            return r, ms, up.drain(), None
        except Exception as e:  # noqa: BLE001
            return None, (time.perf_counter() - t0) * 1000, up.drain(), repr(e)

    def base_row(p, para, action, model, rep, input_chars, speaker=""):
        return {"paper": p["id"], "para_id": para["para_id"], "para_kind": para["kind"], "input_chars": input_chars,
                "action": action, "model": model, "speaker": speaker, "rep": rep, "ttft_ms": ""}

    def finish(row, r, ms, ups, exc):
        row["latency_ms"] = round(ms, 1)
        row["http_status"] = r.status_code if r is not None else ""
        row["success"] = bool(r is not None and r.status_code == 200)
        row["error"] = exc or ("" if row["success"] else (r.text[:300] if r is not None else ""))
        row["upstream_calls"] = len(ups)
        row["upstream_ms"] = round(sum(u["ms"] or 0 for u in ups), 1) if ups else ""
        return row

    papers = [p for p in data["papers"] if p["id"] in wanted]

    # ---- LLM actions: explain + ask (sarvam-105b) ----
    for action in [a for a in ("explain", "ask") if a in actions]:
        print(f"\n== {action} / sarvam-105b ==")
        for p in papers:
            for para in p["paragraphs"]:
                for rep in range(1, args.reps + 1):
                    if action == "explain":
                        body = {"title": p["title"], "paper": p["context"], "index": 0,
                                "paragraph": para["text"], "prev": ""}
                    else:
                        body = {"title": p["title"], "paper": p["context"], "paragraph": para["text"],
                                "question": para["question"], "history": []}
                    r, ms, ups, exc = call("POST", f"/api/{action}", json=body)
                    row = finish(base_row(p, para, action, "sarvam-105b", rep, len(para["text"])), r, ms, ups, exc)
                    usage = [u["usage"] for u in ups if u.get("usage")]
                    if usage:
                        inp = sum(u.get("prompt_tokens", 0) for u in usage)
                        out = sum(u.get("completion_tokens", 0) for u in usage)
                        cached = sum(((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0) for u in usage)
                        reason = sum(((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
                                     for u in usage)
                        row.update(input_tokens=inp, output_tokens=out, cached_input_tokens=cached,
                                   reasoning_tokens=reason,
                                   total_tokens=sum(u.get("total_tokens", 0) for u in usage) or inp + out,
                                   cost_inr=llm_cost(inp, cached, out))
                        if row.get("upstream_ms"):
                            row["output_tokens_per_s"] = round(out / (row["upstream_ms"] / 1000), 2)
                    fr = [((u["json"] or {}).get("choices") or [{}])[0].get("finish_reason") for u in ups if u.get("json")]
                    row["finish_reason"] = "|".join(str(f) for f in fr)
                    if row["success"]:
                        steps = r.json().get("steps", [])
                        text = say_text(steps)
                        row.update(output_chars=len(text), steps=len(steps))
                        if action == "explain" and (p["id"], para["para_id"]) not in narration:
                            narration[(p["id"], para["para_id"])] = text
                    record(row)

    # ---- TTS: bulbul:v4-flash and bulbul:v3 ----
    if "tts" in actions:
        voices = [("bulbul:v4-flash", "shubh_enhi_banking"), ("bulbul:v3", "shubh")]
        for model, speaker in voices:
            srv.TTS_SPEAKER, srv.TTS_MODEL = speaker, model  # server reads these per request
            print(f"\n== tts / {model} ({speaker}) ==")
            for p in papers:
                for para in p["paragraphs"]:
                    text = narration.get((p["id"], para["para_id"]))
                    if not text:
                        continue  # no real narration to speak (explain failed or not run)
                    # whole sentences up to --tts-chars, like the UI's per-sentence calls
                    cut = text[:args.tts_chars]
                    m = re.search(r".*[।?!.]", cut)
                    text = m.group(0) if m and len(m.group(0)) > 40 else cut
                    for rep in range(1, args.reps + 1):
                        r, ms, ups, exc = call("POST", "/api/tts", json={"text": text, "pace": 0.9})
                        row = finish(base_row(p, para, "tts", model, rep, len(text), speaker), r, ms, ups, exc)
                        if row["success"]:
                            import base64
                            audio = base64.b64decode(r.json()["audio"] or "")
                            secs = mp3_seconds(audio)
                            row.update(output_chars=len(audio), audio_seconds=secs if secs else "")
                            if audio:  # billed per input character when audio is synthesized
                                row["cost_inr"] = len(text) / 1000 * PRICING[model]["per_1k_chars"]
                            if secs:
                                row["realtime_factor"] = round(ms / 1000 / secs, 3)
                            if model == "bulbul:v4-flash" and (p["id"], para["para_id"]) not in tts_audio:
                                tts_audio[(p["id"], para["para_id"])] = (audio, text)
                        record(row)
        srv.TTS_SPEAKER = os.getenv("TTS_SPEAKER", "shubh_enhi_banking")
        srv.TTS_MODEL = "bulbul:v4-flash" if "_" in srv.TTS_SPEAKER else "bulbul:v3"

    # ---- STT: saaras:v4 on Guru's own v4-flash audio ----
    if "stt" in actions:
        print("\n== stt / saaras:v4 ==")
        for p in papers:
            for para in p["paragraphs"]:
                if (p["id"], para["para_id"]) not in tts_audio:
                    continue
                audio, src = tts_audio[(p["id"], para["para_id"])]
                secs = mp3_seconds(audio)
                for rep in range(1, args.reps + 1):
                    r, ms, ups, exc = call("POST", "/api/stt", files={"file": ("speech.mp3", audio, "audio/mpeg")},
                                           data={"language": "hi-IN"})
                    row = finish(base_row(p, para, "stt", "saaras:v4", rep, len(src)), r, ms, ups, exc)
                    row["audio_seconds"] = secs or ""
                    if row["success"]:
                        tr = r.json().get("transcript", "")
                        row.update(output_chars=len(tr), stt_similarity=similarity(src, tr))
                        if secs:
                            row["cost_inr"] = secs / 60 * PRICING["saaras:v4"]["per_minute"]
                            row["realtime_factor"] = round(ms / 1000 / secs, 3)
                    record(row)

    fh.close()
    server.should_exit = True
    write_summary(rows, args.usd_inr)
    env = {"run_id": run_id, "machine": platform.node(), "platform": platform.platform(),
           "python": sys.version.split()[0], "processor": platform.processor() or platform.machine(),
           "server": health, "reps": args.reps, "tts_chars": args.tts_chars, "usd_inr": args.usd_inr,
           "pricing_inr": PRICING, "total_cost_inr": round(spent, 4), "total_calls": len(rows),
           "git_commit": _git("rev-parse", "HEAD"), "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD")}
    (RESULTS / "environment.json").write_text(json.dumps(env, indent=2), encoding="utf-8")
    print(f"\nDone: {len(rows)} calls, Rs{spent:.2f} (~${spent / args.usd_inr:.2f}).")
    print(f"Wrote {raw_path}, {RESULTS / 'summary.csv'}, {RESULTS / 'environment.json'}")


def _git(*a):
    try:
        return subprocess.check_output(["git", *a], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return ""


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return ""
    k = (len(xs) - 1) * q
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return round(xs[f] + (xs[c] - xs[f]) * (k - f), 1)


SUMMARY_FIELDS = ["action", "model", "calls", "successes", "error_rate", "latency_mean_ms", "latency_p50_ms",
                  "latency_p95_ms", "latency_max_ms", "upstream_mean_ms", "input_chars_mean", "input_tokens_mean",
                  "cached_input_tokens_mean", "output_tokens_mean", "total_tokens_mean", "output_tokens_per_s_mean",
                  "output_chars_mean", "audio_seconds_mean", "realtime_factor_mean", "stt_similarity_mean",
                  "retries_total", "cost_inr_mean", "cost_inr_total", "cost_usd_mean", "cost_usd_total",
                  "cost_inr_per_1k_input_chars"]


def write_summary(rows, usd_inr):
    groups = {}
    for r in rows:
        groups.setdefault((r["action"], r["model"]), []).append(r)
    out = []
    for (action, model), rs in groups.items():
        ok = [r for r in rs if r["success"]]
        num = lambda k, src=ok: [float(r[k]) for r in src if r.get(k) not in ("", None)]  # noqa: E731
        mean = lambda xs, nd=1: round(statistics.mean(xs), nd) if xs else ""  # noqa: E731
        lat = num("latency_ms")
        costs = num("cost_inr", rs)
        chars = sum(float(r["input_chars"]) for r in rs if r.get("cost_inr") not in ("", None))
        out.append({
            "action": action, "model": model, "calls": len(rs), "successes": len(ok),
            "error_rate": round(1 - len(ok) / len(rs), 3), "latency_mean_ms": mean(lat),
            "latency_p50_ms": pct(lat, 0.5), "latency_p95_ms": pct(lat, 0.95), "latency_max_ms": max(lat) if lat else "",
            "upstream_mean_ms": mean(num("upstream_ms")), "input_chars_mean": mean(num("input_chars")),
            "input_tokens_mean": mean(num("input_tokens")), "cached_input_tokens_mean": mean(num("cached_input_tokens")),
            "output_tokens_mean": mean(num("output_tokens")), "total_tokens_mean": mean(num("total_tokens")),
            "output_tokens_per_s_mean": mean(num("output_tokens_per_s"), 2), "output_chars_mean": mean(num("output_chars")),
            "audio_seconds_mean": mean(num("audio_seconds"), 2), "realtime_factor_mean": mean(num("realtime_factor"), 3),
            "stt_similarity_mean": mean(num("stt_similarity"), 3),
            "retries_total": sum(max(int(r.get("upstream_calls") or 0) - 1, 0) for r in rs),
            "cost_inr_mean": mean(costs, 5), "cost_inr_total": round(sum(costs), 4) if costs else "",
            "cost_usd_mean": round(statistics.mean(costs) / usd_inr, 6) if costs else "",
            "cost_usd_total": round(sum(costs) / usd_inr, 5) if costs else "",
            "cost_inr_per_1k_input_chars": round(sum(costs) / chars * 1000, 5) if costs and chars else "",
        })
    with (RESULTS / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(out)


if __name__ == "__main__":
    main()
