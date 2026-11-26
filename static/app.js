/* Guru frontend: PDF -> paragraphs -> Kanpuriya explanation with voice + whiteboard. */
pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

const NS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const ink = $("ink"), board = $("board"), pagesEl = $("pages");

const S = {
  title: "", paper: "", paras: [], views: {}, cur: 0,
  steps: null, step: 0,
  mode: "idle", // idle | loading | playing | paused | doubt | await_go
  playId: 0, history: [], savedBoard: null, savedViewBox: null,
};
const explainCache = new Map();
const ttsCache = new Map();
const audio = new Audio();
let stopSpeaking = null;

// ---------------------------------------------------------------- utils
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function setStatus(text, cls = "") { const s = $("status"); s.textContent = text; s.className = "status " + cls; }
function setMode(m) {
  S.mode = m;
  const playing = m === "playing";
  $("play").textContent = playing ? "⏸ Ruko" : m === "paused" ? "▶ Chalo" : "▶ Shuru karo";
  $("interrupt").disabled = !(m === "playing" || m === "paused" || m === "loading");
  $("prev").disabled = $("next").disabled = !S.paras.length || m === "doubt" || m === "await_go";
  $("play").disabled = !S.paras.length || m === "doubt" || m === "await_go";
  $("para-count").textContent = S.paras.length ? `para ${S.cur + 1} / ${S.paras.length}` : "";
  setStatus({ idle: "tayyar", loading: "soch rahe hain…", playing: "samjha rahe hain", paused: "ruke hue",
    doubt: "doubt mode", await_go: "aapke 'aage badho' ka intezaar" }[m], m === "doubt" || m === "await_go" ? "doubt" : m === "loading" ? "busy" : "");
}
async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { let d = r.statusText; try { d = (await r.json()).detail || d; } catch {} throw new Error(d); }
  return r.json();
}

// ---------------------------------------------------------------- PDF -> paragraphs
function extractParas(items, pageW, pageNo) {
  const its = items.filter((it) => it.str && it.str.trim()).map((it) => {
    const t = it.transform;
    return { s: it.str, x: t[4], y: t[5], w: it.width, h: Math.hypot(t[2], t[3]) || it.height || 10 };
  });
  its.sort((a, b) => b.y - a.y || a.x - b.x);

  // 1. items -> lines (same baseline, small horizontal gap so columns don't merge)
  const lines = [];
  for (const it of its) {
    let L = null;
    for (let k = lines.length - 1; k >= 0 && k >= lines.length - 40; k--) {
      const c = lines[k];
      const hh = Math.max(c.h, it.h); // sub/superscripts sit a bit off the baseline
      if (Math.abs(c.y - it.y) < hh * 0.6 && it.x - c.x1 < hh * 1.5 && it.x > c.x0 - 2) { L = c; break; }
    }
    if (!L) { lines.push({ x0: it.x, x1: it.x + it.w, y: it.y, h: it.h, s: it.s, best: it.s.length }); continue; }
    const gap = it.x - L.x1;
    L.s += (gap > it.h * 0.15 && !/\s$/.test(L.s) && !/^\s/.test(it.s) ? " " : "") + it.s;
    L.x1 = Math.max(L.x1, it.x + it.w);
    if (it.s.length > L.best) { L.best = it.s.length; L.h = it.h; L.y = it.y; } // line size = its main text
  }

  // 2. reading order for 1- or 2-column layouts
  const mid = pageW / 2;
  lines.sort((a, b) => b.y - a.y);
  const ordered = []; let left = [], right = [];
  const flush = () => { ordered.push(...left, ...right); left = []; right = []; };
  for (const l of lines) {
    if (l.x0 < mid - 20 && l.x1 > mid + 20) { flush(); ordered.push(l); }
    else if (l.x1 <= mid + 20) left.push(l); else right.push(l);
  }
  flush();

  // 3. lines -> paragraphs
  const paras = []; let P = null, prev = null;
  for (const l of ordered) {
    const brk = !prev ||
      prev.y - l.y > Math.max(prev.h, l.h) * 1.75 || prev.y - l.y < -l.h ||   // big gap, or jumped to next column
      (Math.abs(l.h - prev.h) > 1.2 && l.s.length > 3 && prev.s.length > 3) || // font size change (headings)
      Math.abs(l.x0 - prev.x0) > pageW * 0.3 ||
      (l.x0 > prev.x0 + l.h * 0.8 && /[.:?!]$/.test(prev.s.trim()));          // indented new paragraph
    if (brk) { P = { page: pageNo, text: "", rects: [], h: l.h }; paras.push(P); }
    const t = l.s.trim();
    if (P.text.endsWith("-") && /^[a-z]/.test(t)) P.text = P.text.slice(0, -1) + t;
    else P.text += (P.text ? " " : "") + t;
    P.rects.push([l.x0, l.y - l.h * 0.25, l.x1, l.y + l.h * 0.85, pageNo]);
    prev = l;
  }
  return paras;
}

function tidyParas(raw) {
  const out = [];
  for (let i = 0; i < raw.length; i++) {
    const p = { ...raw[i], rects: [...raw[i].rects] };
    if (p.text.length < 4 || /^[\d\s.,:]+$/.test(p.text) || /^arXiv:/i.test(p.text)) continue;
    if (/^(\d+\.?\s*)?(references|bibliography)\s*$/i.test(p.text)) break; // stop at references
    const last = out[out.length - 1];
    // mid-sentence break (column/page/figure) -> continue the same paragraph
    if (last && !last.short && !/[.:?!)\]]$/.test(last.text.trim()) && /^[a-z(]/.test(p.text)) {
      last.text += " " + p.text; last.rects.push(...p.rects); continue;
    }
    // short pieces (headings, caption scraps) get glued onto the following paragraph
    if (last && last.short && last.page === p.page) {
      last.text = last.text + " — " + p.text; last.rects.push(...p.rects); last.short = p.text.length < 150; continue;
    }
    p.short = p.text.length < 150;
    out.push(p);
  }
  // split huge blocks
  const final = [];
  for (const p of out) {
    if (p.text.length <= 2200) { final.push(p); continue; }
    const sents = p.text.match(/[^.!?]+[.!?]+(\s|$)|[^.!?]+$/g) || [p.text];
    let buf = "";
    for (const s of sents) { if ((buf + s).length > 1400 && buf) { final.push({ ...p, text: buf.trim() }); buf = ""; } buf += s; }
    if (buf.trim()) final.push({ ...p, text: buf.trim() });
  }
  return final;
}

async function loadPdf(file) {
  stopAll();
  $("drop").hidden = true; pagesEl.innerHTML = ""; explainCache.clear(); S.history = [];
  setStatus("PDF padh rahe hain…", "busy");
  const pdf = await pdfjsLib.getDocument({ data: await file.arrayBuffer() }).promise;
  const width = pagesEl.clientWidth - 4;
  let raw = [], title = "", titleH = 0;
  for (let p = 1; p <= pdf.numPages; p++) {
    const page = await pdf.getPage(p);
    const vp1 = page.getViewport({ scale: 1 });
    const vp = page.getViewport({ scale: width / vp1.width });
    const div = document.createElement("div");
    div.className = "page"; div.dataset.page = p; div.style.width = vp.width + "px"; div.style.height = vp.height + "px";
    const canvas = document.createElement("canvas");
    const dpr = window.devicePixelRatio || 1;
    canvas.width = vp.width * dpr; canvas.height = vp.height * dpr;
    div.appendChild(canvas); pagesEl.appendChild(div);
    await page.render({ canvasContext: canvas.getContext("2d"), viewport: page.getViewport({ scale: (width / vp1.width) * dpr }) }).promise;
    S.views[p] = vp;
    const tc = await page.getTextContent();
    const paras = extractParas(tc.items, vp1.width, p);
    if (p === 1) for (const q of paras.slice(0, 6)) if (q.h > titleH && q.text.length > 8) { titleH = q.h; title = q.text; }
    raw.push(...paras);
  }
  S.paras = tidyParas(raw);
  S.title = title.slice(0, 200) || file.name;
  S.paper = S.paras.map((p) => p.text).join("\n\n");
  S.cur = 0;
  S.paras.forEach((p, i) => addHitArea(p, i));
  clearBoard();
  $("caption").textContent = `"${S.title}" — ${S.paras.length} paragraphs mile. ▶ dabao, shuru karte hain! (kisi paragraph pe click karke wahin se bhi shuru kar sakte ho)`;
  setMode("idle");
  explain(0).catch(() => {}); // warm up
}

function rectToCss(p, r) {
  const [x1, y1, x2, y2] = S.views[r[4]].convertToViewportRectangle(r.slice(0, 4));
  return { left: Math.min(x1, x2), top: Math.min(y1, y2), width: Math.abs(x2 - x1), height: Math.abs(y2 - y1) };
}
const pageDiv = (n) => pagesEl.querySelector(`.page[data-page="${n}"]`);
function addHitArea(p, i) {
  for (const r of p.rects) {
    const div = pageDiv(r[4]);
    const c = rectToCss(p, r), el = document.createElement("div");
    el.className = "para-hit"; Object.assign(el.style, { left: c.left + "px", top: c.top + "px", width: c.width + "px", height: c.height + "px" });
    el.title = "Yahan se samjhao"; el.onclick = () => { if (S.mode !== "doubt" && S.mode !== "await_go") startPara(i); };
    div.appendChild(el);
  }
}
function highlight(i) {
  pagesEl.querySelectorAll(".hl").forEach((e) => e.remove());
  const p = S.paras[i]; if (!p) return;
  let first = null;
  for (const r of p.rects) {
    const div = pageDiv(r[4]);
    const c = rectToCss(p, r), el = document.createElement("div");
    el.className = "hl"; Object.assign(el.style, { left: c.left - 2 + "px", top: c.top + "px", width: c.width + 4 + "px", height: c.height + "px" });
    div.appendChild(el); first = first || el;
  }
  first && first.scrollIntoView({ behavior: "smooth", block: "center" });
}

// ---------------------------------------------------------------- whiteboard
function clearBoard() { ink.innerHTML = ""; board.setAttribute("viewBox", "0 0 800 500"); }
function draw(fragment) {
  if (!fragment || !fragment.trim()) return;
  const clean = DOMPurify.sanitize(`<svg xmlns="${NS}">${fragment}</svg>`, { USE_PROFILES: { svg: true } });
  const tmp = document.createElementNS(NS, "g");
  tmp.innerHTML = clean;
  const src = tmp.querySelector("svg") || tmp;
  const g = document.createElementNS(NS, "g");
  while (src.firstChild) g.appendChild(src.firstChild);
  ink.appendChild(g);
  animate(g);
  fitBoard();
}
function animate(g) {
  let delay = 0;
  g.querySelectorAll("path,line,rect,circle,ellipse,polyline,polygon").forEach((el) => {
    if (!el.getAttribute("stroke") && (!el.getAttribute("fill") || el.getAttribute("fill") === "none")) {
      el.setAttribute("stroke", "#1f2937"); el.setAttribute("fill", "none");
    }
    if (!el.getAttribute("stroke-width")) el.setAttribute("stroke-width", "3");
    el.setAttribute("stroke-linecap", "round"); el.setAttribute("stroke-linejoin", "round");
    let len = 0; try { len = el.getTotalLength(); } catch {}
    if (len > 0) {
      el.style.strokeDasharray = len; el.style.strokeDashoffset = len;
      el.style.transition = `stroke-dashoffset ${Math.min(1.6, 0.4 + len / 600)}s ease ${delay}s, fill-opacity .6s ease ${delay + 0.6}s`;
      const fo = el.getAttribute("fill-opacity") || "1"; el.style.fillOpacity = 0;
      requestAnimationFrame(() => requestAnimationFrame(() => { el.style.strokeDashoffset = 0; el.style.fillOpacity = fo; }));
      delay += 0.35;
    }
  });
  g.querySelectorAll("text").forEach((t) => {
    if (!t.getAttribute("fill")) t.setAttribute("fill", "#1f2937");
    t.removeAttribute("font-family"); t.removeAttribute("stroke");
    t.style.opacity = 0; t.style.transition = `opacity .5s ease ${delay}s`;
    requestAnimationFrame(() => requestAnimationFrame(() => (t.style.opacity = 1)));
    delay += 0.25;
  });
}
function fitBoard() { // keep everything visible even if the model drew outside 800x500
  try {
    const b = ink.getBBox(); if (!b.width) return;
    const x0 = Math.min(0, b.x - 10), y0 = Math.min(0, b.y - 10);
    const x1 = Math.max(800, b.x + b.width + 10), y1 = Math.max(500, b.y + b.height + 10);
    board.setAttribute("viewBox", `${x0} ${y0} ${x1 - x0} ${y1 - y0}`);
  } catch {}
}

// ---------------------------------------------------------------- voice out (Sarvam Bulbul, browser fallback)
function pace() { return parseFloat($("pace").value); }
function ttsUrl(text) {
  const key = pace() + "|" + text;
  if (!ttsCache.has(key)) {
    ttsCache.set(key, postJSON("/api/tts", { text, pace: pace() }).then((d) => {
      if (!d.audio) return null;
      const bin = atob(d.audio), arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      return URL.createObjectURL(new Blob([arr], { type: "audio/mpeg" }));
    }).catch((e) => { console.warn("TTS failed, using browser voice", e); ttsCache.delete(key); return null; }));
  }
  return ttsCache.get(key);
}
async function speak(text) {
  const url = await ttsUrl(text);
  return new Promise((resolve) => {
    stopSpeaking = () => { stopSpeaking = null; resolve("stopped"); };
    const done = () => { if (stopSpeaking) { stopSpeaking = null; resolve("done"); } };
    if (url) {
      audio.src = url; audio.onended = done; audio.onerror = done;
      audio.play().catch(done);
    } else if ("speechSynthesis" in window) {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "hi-IN"; u.rate = pace();
      const v = speechSynthesis.getVoices().find((v) => v.lang === "hi-IN"); if (v) u.voice = v;
      u.onend = done; u.onerror = done; speechSynthesis.speak(u);
    } else setTimeout(done, 300 + text.length * 60);
  });
}
function hush() {
  audio.pause(); audio.onended = null;
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  if (stopSpeaking) stopSpeaking();
}

// ---------------------------------------------------------------- lesson engine
function explain(i) {
  if (!explainCache.has(i)) {
    const p = S.paras[i];
    const req = postJSON("/api/explain", { title: S.title, paper: S.paper, index: i, paragraph: p.text, prev: S.paras[i - 1]?.text || "" })
      .then((d) => d.steps)
      .catch((e) => { explainCache.delete(i); throw e; });
    explainCache.set(i, req);
  }
  return explainCache.get(i);
}

async function playSteps(steps, from, id, track = true, redrawFirst = true) {
  for (let k = from; k < steps.length; k++) {
    if (id !== S.playId) return false;
    if (track) S.step = k; // doubt answers must not move the paragraph's resume point
    $("caption").textContent = steps[k].say;
    if (steps[k + 1]) ttsUrl(steps[k + 1].say); // prefetch next sentence's audio
    if (k !== from || redrawFirst) draw(steps[k].svg); // on resume, that step's drawing is already on the board
    await speak(steps[k].say);
    if (id !== S.playId) return false;
    await sleep(350);
  }
  return id === S.playId;
}

async function runFrom(i, stepFrom = 0, keepBoard = false) {
  const id = ++S.playId;
  S.cur = i; highlight(i);
  if (!keepBoard) clearBoard();
  setMode("loading");
  $("board-label").textContent = `Whiteboard · para ${i + 1}`;
  $("thinking").hidden = false;
  let steps;
  try { steps = await explain(i); }
  catch (e) {
    if (id !== S.playId) return;
    $("thinking").hidden = true; setMode("idle");
    $("caption").textContent = "⚠️ Gadbad ho gayi: " + e.message + " — ▶ dabake phir try karo.";
    return;
  }
  if (id !== S.playId) return;
  $("thinking").hidden = true;
  S.steps = steps;
  if (i + 1 < S.paras.length) explain(i + 1).catch(() => {}); // prefetch next paragraph
  setMode("playing");
  const finished = await playSteps(steps, stepFrom, id, true, !keepBoard);
  if (!finished) return;
  if (i + 1 < S.paras.length) { await sleep(900); if (id === S.playId) runFrom(i + 1); }
  else { setMode("idle"); $("caption").textContent = "बस गुरु, पूरा पेपर निपट गया! एकदम भौकाल पढ़ाई हुई। 🎉"; speak($("caption").textContent); }
}
function startPara(i) { hush(); S.history = []; runFrom(Math.max(0, Math.min(S.paras.length - 1, i))); }
function stopAll() { S.playId++; hush(); }

// ---------------------------------------------------------------- doubts / interrupts
function interrupt(focus = true) {
  if (!["playing", "paused", "loading"].includes(S.mode)) return;
  S.playId++; hush();
  $("thinking").hidden = true;
  S.savedBoard = ink.innerHTML; S.savedViewBox = board.getAttribute("viewBox");
  setMode("doubt");
  $("doubt").hidden = false; $("go-row").hidden = true;
  $("caption").textContent = "हाँ गुरु, बोलो — का नहीं समझे?";
  $("question").value = ""; if (focus) $("question").focus();
}

async function ask(q) {
  q = (q || "").trim(); if (!q) return;
  const id = ++S.playId; hush();
  setMode("doubt"); setStatus("jawaab soch rahe hain…", "busy");
  document.querySelector(".board-wrap").classList.add("doubt-mode");
  clearBoard(); $("thinking").hidden = false; $("ask").disabled = true;
  $("board-label").textContent = `Doubt: ${q.slice(0, 60)}`;
  try {
    const { steps } = await postJSON("/api/ask", { title: S.title, paper: S.paper, paragraph: S.paras[S.cur].text, question: q, history: S.history });
    if (id !== S.playId) return;
    $("thinking").hidden = true;
    S.history.push({ q, a: steps.map((s) => s.say).join(" ") });
    $("question").value = "";
    setMode("doubt"); setStatus("doubt samjha rahe hain", "doubt");
    const ok = await playSteps(steps, 0, id, false);
    if (ok) awaitGo();
  } catch (e) {
    $("thinking").hidden = true;
    $("caption").textContent = "⚠️ " + e.message; awaitGo();
  } finally { $("ask").disabled = false; }
}
function awaitGo() { setMode("await_go"); $("go-row").hidden = false; $("question").placeholder = "ek aur sawaal? (ya 'aage badho')"; }

function goAhead() {
  if (S.mode !== "doubt" && S.mode !== "await_go") return;
  hush();
  $("doubt").hidden = true;
  document.querySelector(".board-wrap").classList.remove("doubt-mode");
  ink.innerHTML = S.savedBoard || ""; board.setAttribute("viewBox", S.savedViewBox || "0 0 800 500");
  // resume the interrupted paragraph from the sentence where we stopped
  runFrom(S.cur, S.steps ? S.step : 0, true);
}
const GO_WORDS = /(aage|aagey|chalo|continue|go ahead|carry on|next|samajh|haan|ha+n|\bok\b|okay|theek|ठीक|आगे|चलो|हाँ|हां|समझ)/i;
const NOT_WORDS = /(nahi|nahin|nhi|not|no\b|नहीं|नही|ना\b|kyu|kyon|kya|क्या|क्यों|\?)/i;
const GO_RE = { test: (t) => GO_WORDS.test(t) && !NOT_WORDS.test(t) };

// ---------------------------------------------------------------- voice in (Sarvam Saaras)
let rec = null, chunks = [];
async function toggleMic() {
  if (rec && rec.state === "recording") { rec.stop(); return; }
  if (S.mode === "playing" || S.mode === "paused" || S.mode === "loading") interrupt(false);
  else if (S.mode === "idle") { S.savedBoard = ink.innerHTML; S.savedViewBox = board.getAttribute("viewBox"); setMode("doubt"); $("doubt").hidden = false; $("go-row").hidden = true; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = []; rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop()); $("mic").classList.remove("rec"); $("mic").textContent = "🎤";
      const fd = new FormData(); fd.append("file", new Blob(chunks, { type: rec.mimeType || "audio/webm" }), "speech.webm");
      setStatus("sun rahe hain…", "busy");
      try {
        const r = await fetch("/api/stt", { method: "POST", body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || r.statusText);
        const transcript = d.transcript;
        if (!transcript) { setStatus("kuch sunai nahi diya", "doubt"); return; }
        $("question").value = transcript;
        if (S.mode === "await_go" && GO_RE.test(transcript) && transcript.split(/\s+/).length <= 5) goAhead();
        else ask(transcript);
      } catch (e) { setStatus("mic error: " + e.message, "doubt"); }
    };
    rec.start(); $("mic").classList.add("rec"); $("mic").textContent = "⏹";
    setStatus("🎙 bolo… phir M dabao", "doubt");
    $("caption").textContent = "🎙 सुन रहे हैं गुरु, बोलो… (बोल के फिर से M दबाओ)";
    setTimeout(() => rec && rec.state === "recording" && rec.stop(), 25000);
  } catch (e) { alert("Mic ki permission chahiye: " + e.message); }
}

// ---------------------------------------------------------------- wiring
$("file").onchange = (e) => e.target.files[0] && loadPdf(e.target.files[0]);
const drop = $("paper-pane");
drop.ondragover = (e) => { e.preventDefault(); $("drop").classList.add("over"); };
drop.ondragleave = () => $("drop").classList.remove("over");
drop.ondrop = (e) => { e.preventDefault(); $("drop").classList.remove("over"); const f = e.dataTransfer.files[0]; if (f) loadPdf(f); };

$("play").onclick = () => {
  if (S.mode === "idle") runFrom(S.cur, 0);
  else if (S.mode === "playing") { audio.pause(); if ("speechSynthesis" in window) speechSynthesis.pause(); setMode("paused"); }
  else if (S.mode === "paused") { if (audio.src && !audio.ended) audio.play(); if ("speechSynthesis" in window) speechSynthesis.resume(); setMode("playing"); }
};
$("prev").onclick = () => startPara(S.cur - 1);
$("next").onclick = () => startPara(S.cur + 1);
$("interrupt").onclick = interrupt;
$("ask").onclick = () => {
  const q = $("question").value;
  if (S.mode === "await_go" && GO_RE.test(q) && q.trim().split(/\s+/).length <= 3) goAhead(); else ask(q);
};
$("question").onkeydown = (e) => { if (e.key === "Enter") $("ask").click(); };
$("mic").onclick = toggleMic;
$("go").onclick = goAhead;
$("cancel-doubt").onclick = goAhead;
document.addEventListener("keydown", (e) => {
  // while recording, M always means "done, send it" — even if a text box has focus
  if (e.code === "KeyM" && !e.repeat && rec && rec.state === "recording") { e.preventDefault(); toggleMic(); return; }
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.code === "Space") { e.preventDefault(); if (S.mode === "await_go") goAhead(); else interrupt(); }
  // M: stop Guru and record a spoken question; press M again to send it
  if (e.code === "KeyM" && !e.repeat && !e.metaKey && !e.ctrlKey && !e.altKey && S.paras.length) { e.preventDefault(); toggleMic(); }
});
setMode("idle");

// exposed for debugging/testing
window.__guru = { S, explainCache };
