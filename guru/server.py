"""Guru — research-paper explainer in Kanpuriya Hindi, powered by Sarvam AI
(sarvam-105b for explanations, Bulbul v3 for voice, Saaras v4 for listening).

Run:  ./run.sh   (or: .venv/bin/python -m uvicorn guru.server:app --port 8765 from the repo root)
"""

import os
import re
from pathlib import Path
from typing import List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .whiteboard.layout import parse_lesson

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
load_dotenv(HERE.parent / ".env")  # .env lives at the repo root

SARVAM = "https://api.sarvam.ai"
SARVAM_KEY = os.getenv("SARVAM_API_KEY", "")
CHAT_MODEL = "sarvam-105b"
TTS_SPEAKER = os.getenv("TTS_SPEAKER", "shubh_enhi_banking")
# Bulbul v4 Flash personas look like name_lang_style (e.g. shubh_enhi_banking); plain names (shubh, varun) are v3.
TTS_MODEL = "bulbul:v4-flash" if "_" in TTS_SPEAKER else "bulbul:v3"
PAPER_CHARS = 120_000  # sarvam-105b has a 128K-token context window
MOCK = os.getenv("MOCK", "0") == "1"
http = httpx.Client(timeout=120)

app = FastAPI(title="Guru")


@app.middleware("http")
async def no_cache(request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-cache"  # always pick up edited JS/CSS
    return resp

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

STYLE = """Tum "Guru" ho — Kanpur ke ek mast, pyaare teacher jo research papers ekdum aasaan bhasha mein samjhaate hain.

BOLI (Kanpuriya Hindi):
- Kanpur wali boli: "देखो गुरु", "का है कि...", "एकदम चौकस", "भौकाल", "गज़ब", "तनिक", "काहे", "हियाँ/हुआँ", "समझे कि नहीं?", "झाड़ दिया", "लल्लनटॉप", "धांसू", "हम बताते हैं".
- Pyaar se, mazaak ke saath — gaali ya bhadda shabd kabhi nahi.
- Kanpur ki desi misaalein do — गंगा घाट, बड़ा चौराहा, ठग्गू के लड्डू, टेम्पो, चाय की टपरी, क्रिकेट, बाज़ार का हिसाब.
- Technical shabd (transformer, gradient, attention, layer) English/Latin mein hi likho, baaki sab DEVANAGARI mein.

OUTPUT FORMAT — sirf ye script, aur kuch nahi (no JSON, no markdown):
SAY: <ek ya do chhote vaakya, DEVANAGARI lipi mein>
DRAW: <whiteboard command>
DRAW: <whiteboard command>
SAY: <agla vaakya>
...

Har SAY ek "step" hai. Uske neeche ke DRAW commands tab whiteboard par bante hain jab wo vaakya bola jaata hai.
Drawings jud-ti jaati hain (pichhli mitati nahi). COORDINATES KABHI MAT LIKHO — layout computer khud karta hai.
Tum sirf batao KYA banana hai:

WHITEBOARD COMMANDS:
  TITLE "chhota heading"                  — sabse pehle step mein, ek hi baar (2-5 shabd)
  NODE id "label" [color] [circle]        — concept ka dibba. id ek chhota English shabd (jaise x, enc, out)
  LINK id1 id2 ["label"] [color]          — id1 se id2 tak teer (flow / cause→effect / input→output)
  FORMULA "equation"                       — ek zaroori formula (jaise "H(x) = F(x) + x")
  NOTE "chhoti baat" [color]               — side mein bullet point (max 6 shabd)
  GRAPH "x label" "y label" ["caption"]    — trend dikhane ke liye chhota graph (axes)
  PLOT shape color "legend" [high|low]     — GRAPH par ek line. shape: rise fall peak valley plateau up down flat zigzag
  SAAR "ek line ka nichod"                 — aakhri step mein, ek hi baar
Colors: black blue red green orange purple.

ACHHI DRAWING KE NIYAM:
- Ek drawing = ek kahaani. 2 se 6 NODE, aur HAR NODE kam se kam ek LINK se juda ho, taaki flow dikhe (input → process → output, problem → solution, cause → effect).
- Parallel / kai saari cheezein (jaise h attention heads) = ek hi stage pe 2-3 NODE, sab ek hi pichhle NODE se aur ek hi agle NODE mein LINK.
- "A vs B" comparison = do alag flows (A ke nodes alag, B ke alag), ya GRAPH mein do PLOT.
- Labels chhote: NODE 1-4 shabd, NOTE max 6 shabd, LINK label 1-2 shabd. Lambe vaakya SAY mein bolo, board pe nahi.
- Jo number/trend/comparison ho uske liye GRAPH + PLOT use karo. Shape sahi chuno: badhna=rise, girna=fall, pehle badhe phir gire=peak, badh ke ruk jaaye=plateau, uchhal-kood=zigzag. Do lines compare karni ho to ek high, ek low.
- Formula tabhi jab paragraph mein sach mein ho. NOTE max 3. Har cheez ek hi baar banao — repeat mat karo.
- Har step mein 1-3 DRAW. Pehle step mein TITLE, aakhri step mein SAAR.

UDAAHARAN 1 (flow + skip connection):
SAY: देखो गुरु, ये paragraph ResNet का residual block समझा रहा है — एकदम भौकाल idea।
DRAW: TITLE "Residual Block"
SAY: पहले input x आता है, और दो weight layers उस पे काम करती हैं।
DRAW: NODE x "Input x" blue
DRAW: NODE f "Weight layers F(x)" green
DRAW: LINK x f
SAY: फिर layers का output और x जोड़ दिए जाते हैं।
DRAW: NODE out "Output" purple circle
DRAW: LINK f out "+"
SAY: असली जुगाड़ है shortcut — x सीधा टेम्पो पकड़ के आगे पहुँच जाता है, जाम में नहीं फँसता।
DRAW: LINK x out "shortcut" orange
DRAW: FORMULA "H(x) = F(x) + x"
SAY: तो गुरु, कुल मिला के — layers को बस बचा-खुचा फ़र्क सीखना है, पूरा नहीं।
DRAW: SAAR "Layers सिर्फ़ residual F(x) सीखती हैं"

UDAAHARAN 2 (comparison / trend):
SAY: का है कि ज़्यादा गहरा network बनाओ तो उल्टा error बढ़ जाता है।
DRAW: TITLE "Degradation Problem"
DRAW: GRAPH "iterations" "training error" "20 vs 56 layers"
SAY: देखो, 56-layer वाली लाइन ऊपर है — मतलब ज़्यादा गलती, जैसे ज़्यादा मंज़िल चढ़ के भी दुकान बंद।
DRAW: PLOT fall red "56-layer" high
DRAW: PLOT fall green "20-layer" low
DRAW: NOTE "ये overfitting नहीं है" red
SAY: तो गुरु, कुल मिला के — बस layers ठूँसने से काम नहीं बनता।
DRAW: SAAR "गहरा ≠ बेहतर (बिना जुगाड़ के)"
"""

EXPLAIN_TASK = """Ab is paper ka paragraph #{index} samjhao.

Pichhla paragraph (sirf context ke liye, dobara mat samjhao):
\"\"\"{prev}\"\"\"

AAJ KA PARAGRAPH:
\"\"\"{paragraph}\"\"\"

Niyam:
- 4 se 8 SAY steps. Pehle ek line mein batao paragraph kis baare mein hai, phir dheere dheere har idea todo, mushkil shabd/jargon ka matlab desi misaal se batao, aur aakhir mein "तो गुरु, कुल मिला के..." wala ek-line saar.
- Agar paragraph sirf heading, author names, affiliations, copyright, figure caption ka tukda ya kachra/equation ka bikhra text hai, to 1–2 steps mein chhota sa batao.
- Whiteboard par paragraph ka core idea ek saaf drawing mein dikhao (NODE/LINK flow, ya GRAPH, ya FORMULA) — upar diye niyam follow karo, coordinates nahi.
- SAY hamesha Devanagari mein. Sirf script likho."""

ASK_TASK = """Student ne beech mein tok ke sawaal poocha hai.

Jo paragraph abhi samjha rahe the:
\"\"\"{paragraph}\"\"\"

Ab tak ki baatcheet (doubts):
{history}

STUDENT KA SAWAAL: "{question}"

Niyam:
- 2 se 5 SAY steps mein sawaal ka seedha jawaab do (kisi shabd ka matlab poocha ho to simple definition + Kanpur wali misaal).
- Whiteboard naya aur khaali hai — TITLE mein sawaal ka mudda, phir NODE/LINK ya GRAPH se jawaab dikhao, aakhir mein SAAR. Coordinates nahi.
- Aakhri SAY mein poochho ki samajh aaya ki nahi, aur bolo "जब तैयार हो तो बोलो 'आगे बढ़ो', फिर वहीं से चलते हैं।"
- SAY hamesha Devanagari mein. Sirf script likho."""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BAD_TAGS = re.compile(r"<\s*/?\s*(script|foreignObject|image|iframe|style|use|a|svg)\b[^>]*>", re.I)
_BAD_ATTRS = re.compile(r"\s(on\w+|href|xlink:href)\s*=\s*(\"[^\"]*\"|'[^']*')", re.I)


def clean_svg(fragment: str) -> str:
    """Strip any <svg> wrapper and unsafe tags; the browser sanitizes again with DOMPurify."""
    fragment = _BAD_TAGS.sub("", fragment or "")
    return _BAD_ATTRS.sub("", fragment)


def headers():
    if not SARVAM_KEY:
        raise HTTPException(401, "SARVAM_API_KEY set nahi hai (.env dekho).")
    return {"api-subscription-key": SARVAM_KEY}


def _raise_for(r: httpx.Response):
    if r.status_code == 403:
        raise HTTPException(401, "Sarvam API key galat hai.")
    if r.status_code == 429:
        raise HTTPException(429, "Rate limit lag gayi — tanik ruk ke phir try karo.")
    if r.status_code >= 400:
        raise HTTPException(502, f"Sarvam error {r.status_code}: {r.text[:300]}")


def call_llm(title: str, paper: str, task: str, effort: str) -> List[dict]:
    system = STYLE + f"\n\nPURA PAPER (sirf context ke liye) — \"{title}\":\n\n{paper[:PAPER_CHARS]}"
    body = {
        "model": CHAT_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": task}],
        "max_tokens": 3000,
        "reasoning_effort": None,  # thinking off: ~1s replies, and it never eats the token budget
        "temperature": 0.7,
    }
    last_err = None
    for _ in range(3):  # retry on overload / unusable output
        try:
            r = http.post(f"{SARVAM}/v1/chat/completions", headers=headers(), json=body)
        except httpx.HTTPError:
            raise HTTPException(503, "Sarvam API tak pahunch nahi paaye — internet dekho.")
        if r.status_code == 503:
            last_err = "Sarvam overloaded"
            continue
        _raise_for(r)
        choice = r.json()["choices"][0]
        steps = parse_lesson(choice["message"].get("content") or "")
        steps = [s for s in steps if s["say"]][:12]
        if steps:
            return steps
        last_err = f"no SAY lines (finish_reason={choice.get('finish_reason')})"
    raise HTTPException(502, f"Model ka jawaab samajh nahi aaya: {last_err}")


def mock_steps(kind: str, text: str) -> List[dict]:
    snippet = (text or "")[:40].replace("<", "").replace("&", "")
    if kind == "ask":
        return [
            {"say": f"अरे वाह गुरु, बढ़िया सवाल! \"{snippet}\" का मतलब समझो।",
             "svg": '<text x="60" y="70" font-size="30" fill="#dc2626">Doubt: ' + snippet + "</text>"},
            {"say": "ये mock जवाब है — असली जवाब के लिए API key डालो।",
             "svg": '<circle cx="400" cy="260" r="90" stroke="#2563eb" stroke-width="3" fill="none"/>'},
            {"say": "समझ आया? जब तैयार हो तो बोलो 'आगे बढ़ो'।", "svg": ""},
        ]
    return [
        {"say": "देखो गुरु, ये पैराग्राफ एकदम चौकस बात कह रहा है।",
         "svg": '<text x="40" y="60" font-size="28" fill="#1f2937">Para: ' + snippet + "...</text>"},
        {"say": "पहले एक डब्बा बनाते हैं — ये है input।",
         "svg": '<rect x="80" y="180" width="180" height="100" rx="10" stroke="#2563eb" stroke-width="3" fill="none"/>'
                '<text x="125" y="238" font-size="24" fill="#2563eb">Input</text>'},
        {"say": "फिर तीर मारो, और उधर निकला output — एकदम भौकाल!",
         "svg": '<path d="M270 230 L480 230" stroke="#d97706" stroke-width="3" fill="none"/>'
                '<polygon points="480,220 500,230 480,240" fill="#d97706"/>'
                '<rect x="510" y="180" width="200" height="100" rx="10" stroke="#059669" stroke-width="3" fill="none"/>'
                '<text x="555" y="238" font-size="24" fill="#059669">Output</text>'},
        {"say": "तो गुरु, कुल मिला के — ये mock mode है, असली समझाई के लिए API key लगाओ।",
         "svg": '<path d="M80 400 Q400 360 720 400" stroke="#dc2626" stroke-width="3" fill="none"/>'},
    ]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class ExplainReq(BaseModel):
    title: str = "Research paper"
    paper: str
    index: int
    paragraph: str
    prev: str = ""


class QA(BaseModel):
    q: str
    a: str


class AskReq(BaseModel):
    title: str = "Research paper"
    paper: str
    paragraph: str
    question: str
    history: Optional[List[QA]] = None


@app.post("/api/explain")
def explain(req: ExplainReq):
    if MOCK:
        return {"steps": mock_steps("explain", req.paragraph)}
    task = EXPLAIN_TASK.format(index=req.index + 1, prev=req.prev[:3000] or "(koi nahi)",
                               paragraph=req.paragraph[:8000])
    return {"steps": call_llm(req.title, req.paper, task, effort="medium")}


@app.post("/api/ask")
def ask(req: AskReq):
    if MOCK:
        return {"steps": mock_steps("ask", req.question)}
    history = "\n".join(f"Q: {h.q}\nA: {h.a}" for h in (req.history or [])[-6:]) or "(koi nahi)"
    task = ASK_TASK.format(paragraph=req.paragraph[:8000], history=history, question=req.question[:1000])
    return {"steps": call_llm(req.title, req.paper, task, effort="low")}


class TTSReq(BaseModel):
    text: str
    pace: float = 0.9


@app.post("/api/tts")
def tts(req: TTSReq):
    """Kanpuriya narration -> Bulbul voice (base64 mp3)."""
    if MOCK:
        return {"audio": None}
    r = http.post(f"{SARVAM}/text-to-speech", headers=headers(), json={
        "text": req.text[:2500], "language_code": "hi-IN", "model": TTS_MODEL,
        "speaker": TTS_SPEAKER, "pace": max(0.5, min(2.0, req.pace)), "output_audio_codec": "mp3",
    })
    _raise_for(r)
    return {"audio": r.json()["audios"][0]}


@app.post("/api/stt")
async def stt(file: UploadFile = File(...), language: str = Form("hi-IN")):
    """Student's spoken doubt -> text (Saaras v4, Hindi-English codemix)."""
    if MOCK:
        return {"transcript": "attention ka matlab kya hai"}
    data = await file.read()
    # Browsers label recordings like "audio/webm;codecs=opus", which Sarvam rejects:
    # send the bare type, then fall back to a generic binary type.
    ctype = (file.content_type or "audio/webm").split(";")[0].strip()
    for t in (ctype, "application/octet-stream"):
        r = http.post(f"{SARVAM}/speech-to-text", headers=headers(),
                      data={"model": "saaras:v4", "mode": "codemix", "language_code": language},
                      files={"file": (file.filename or "speech.webm", data, t)})
        if r.status_code != 400:
            break
    _raise_for(r)
    return {"transcript": r.json().get("transcript", "")}


class RenderReq(BaseModel):
    script: str


@app.post("/api/render")
def render(req: RenderReq):
    """Render a SAY/DRAW script without calling the model (for testing the whiteboard)."""
    return {"steps": parse_lesson(req.script)}


@app.get("/api/health")
def health():
    return {"ok": True, "mock": MOCK, "model": CHAT_MODEL, "voice": TTS_SPEAKER, "tts_model": TTS_MODEL}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
