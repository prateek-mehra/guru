"""Regression tests: every endpoint in mock mode, the no-key error path, and the whiteboard renderer."""

import importlib
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

LESSON = '''SAY: देखो गुरु residual block
DRAW: TITLE "Residual Block"
SAY: input x
DRAW: NODE x "Input x" blue
DRAW: NODE f "Weight layers F(x)" green
DRAW: LINK x f
DRAW: LINK x f "shortcut" orange
DRAW: FORMULA "H(x) = F(x) + x"
SAY: graph
DRAW: GRAPH "iterations" "error" "20 vs 56"
DRAW: PLOT fall red "56-layer" high
DRAW: box 100 100 200 80 blue "old style box"
SAY: saar
DRAW: SAAR "Layers sirf residual F(x) seekhti hain"'''


def load_server(**env):
    """Import guru.server fresh with the given env (env wins over .env, which load_dotenv never overrides)."""
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        sys.modules.pop("guru.server", None)
        return importlib.import_module("guru.server")
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class MockModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cwd = os.getcwd()
        os.chdir("/")  # paths must not depend on where the server is started from
        cls.c = TestClient(load_server(MOCK="1").app)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.cwd)

    def test_index_and_static(self):
        for path, kind in [("/", "text/html"), ("/static/app.js", "javascript"), ("/static/style.css", "text/css")]:
            r = self.c.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn(kind, r.headers["content-type"])
            self.assertEqual(r.headers["cache-control"], "no-cache")
        self.assertIn("/static/app.js", self.c.get("/").text)
        self.assertEqual(self.c.get("/static/missing.js").status_code, 404)

    def test_health(self):
        d = self.c.get("/api/health").json()
        self.assertTrue(d["ok"])
        self.assertTrue(d["mock"])

    def test_explain_and_ask(self):
        steps = self.c.post("/api/explain", json={"paper": "p", "index": 0, "paragraph": "Attention"}).json()["steps"]
        self.assertTrue(steps and all("say" in s and "svg" in s for s in steps))
        steps = self.c.post("/api/ask", json={"paper": "p", "paragraph": "x", "question": "q?"}).json()["steps"]
        self.assertTrue(steps)
        self.assertEqual(self.c.post("/api/explain", json={}).status_code, 422)

    def test_tts_stt(self):
        self.assertEqual(self.c.post("/api/tts", json={"text": "नमस्ते"}).json(), {"audio": None})
        r = self.c.post("/api/stt", files={"file": ("a.webm", b"xx", "audio/webm")})
        self.assertEqual(r.status_code, 200)
        self.assertIn("transcript", r.json())

    def test_render(self):
        steps = self.c.post("/api/render", json={"script": LESSON}).json()["steps"]
        self.assertEqual(len(steps), 4)
        board = "".join(s["svg"] for s in steps)
        for text in ("Residual Block", "Input x", "H(x) = F(x) + x", "56-layer", "old style box"):
            self.assertIn(text, board)


class NoKeyTest(unittest.TestCase):
    def test_missing_key_is_401(self):
        c = TestClient(load_server(MOCK="0", SARVAM_API_KEY="").app)
        r = c.post("/api/tts", json={"text": "x"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("SARVAM_API_KEY", r.json()["detail"])


class WhiteboardTest(unittest.TestCase):
    def test_package_import(self):
        from guru.whiteboard import parse_lesson
        from guru.whiteboard.draw import command_to_svg

        self.assertEqual(len(parse_lesson(LESSON)), 4)
        self.assertIn("<rect", command_to_svg('box 10 10 100 50 blue "x"'))


if __name__ == "__main__":
    unittest.main()
