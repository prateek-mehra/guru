# Guru — paper samjho, Kanpur style

Upload a research-paper PDF. A teacher, "Guru", explains it **one paragraph at a time, slowly, in a Kanpuriya Hindi voice**, and draws on a whiteboard beside the paper as he talks.

- The current paragraph is highlighted on the PDF. Click any paragraph to start from there.
- Each sentence he speaks has its own drawing (boxes, arrows, graphs), which animates onto the board while he says it.
- **Interrupt any time by pressing `M`**: he stops, you ask your question out loud (like "embedding ka matlab?"), and you press `M` again to send it. He answers on a fresh board, then waits.
- Say or type **"aage badho"** (or click ▶ Aage badho) and he goes back to the paragraph's board and carries on from the sentence where you stopped him. You can also ask another question first.

## How it works

<p align="center">
  <img src="docs/how-it-works.svg" width="100%" alt="How Guru works: 1. you upload a paper and the browser splits it into paragraphs; 2. sarvam-105b explains a paragraph in Kanpuriya Hindi as lines to say and things to draw; 3. Bulbul voices each line and the drawings become a whiteboard sketch; 4. you watch and listen, then it moves to the next paragraph. Any time, press M to ask a doubt: Saaras turns your speech into text, sarvam-105b answers on a fresh whiteboard, and saying aage badho resumes the same sentence.">
</p>

While you listen to one paragraph, the next one is already being prepared, so there's no wait between them.

## Powered by Sarvam AI

| Job | Sarvam model |
|---|---|
| Explanations and doubt answers | `sarvam-105b` (chat completions, reasoning off for ~1–2 s replies) |
| Voice | `bulbul:v4-flash` text-to-speech, `hi-IN`, speaker `shubh_enhi_banking` (a Hindi–English code-mixed voice; set `TTS_SPEAKER` to change it, plain v3 names like `varun` also work) |
| Listening to your questions | `saaras:v4` speech-to-text, `codemix` mode (handles Hindi and English mixed together) |

The PDF is parsed in the browser with pdf.js, which handles one- and two-column layouts, headings and hyphenation, and stops at References. The model writes a small script of `SAY:` and `DRAW:` lines. It never gives coordinates: [`layout.py`](layout.py) decides where everything goes and turns it into SVG.

## Run

```bash
cp .env.example .env    # then put your SARVAM_API_KEY in .env
./run.sh                # opens on http://localhost:8765
```

Needs Python 3.9 or newer. Use Chrome, because the 🎤 button needs microphone permission. To try the UI without calling Sarvam, set `MOCK=1`.

## Files

- `server.py`: FastAPI app. It serves `/api/explain`, `/api/ask`, `/api/tts` and `/api/stt`, plus the prompts.
- `layout.py`: the whiteboard language. The model says *what* to draw (`TITLE`, `NODE`/`LINK` flowchart, `FORMULA`, `NOTE`, `GRAPH`/`PLOT`, `SAAR`) and this lays it out in fixed zones, so nothing overlaps.
- `draw.py`: text measuring and the older coordinate commands, still accepted as a fallback.
- `static/`: the UI (`index.html`, `app.js`, `style.css`).
