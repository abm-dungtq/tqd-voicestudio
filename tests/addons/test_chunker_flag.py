import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE = (
    "import vizipvoice_openai_server as s; "
    "print(s.CHUNKER_MODE, len(s.parse_expressive_script("
    "'Xin chào. Cảm ơn bạn rất nhiều. Hôm nay trời đẹp quá!')))"
)


def _run(mode):
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8="1", TQD_CHUNKER=mode)
    return subprocess.run(
        [sys.executable, "-c", CODE],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
    )


def test_v2_merges_into_one_clause():
    r = _run("v2")
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip().splitlines()[-1] == "v2 1"


def test_bogus_mode_rejected():
    assert _run("bogus").returncode != 0
