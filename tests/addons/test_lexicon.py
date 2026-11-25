import json
import os
import time

from vizipvoice_addons.lexicon import PronunciationLexicon


def _write(path, entries):
    path.write_text(
        json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8"
    )


def test_apply_whole_word_case_sensitive_longest_first(tmp_path):
    p = tmp_path / "lex.json"
    _write(p, {"COVID": "cô vít", "COVID-19": "cô vít mười chín", "AI": "ây ai"})
    lex = PronunciationLexicon(p)
    assert lex.apply("COVID-19 đã qua") == "cô vít mười chín đã qua"
    assert lex.apply("AI và COVID") == "ây ai và cô vít"
    # whole-word: XCOVID / COVIDX untouched; lowercase untouched
    assert lex.apply("XCOVID covid COVIDX") == "XCOVID covid COVIDX"


def test_missing_file_is_empty(tmp_path):
    lex = PronunciationLexicon(tmp_path / "absent.json")
    assert lex.get() == {}
    assert lex.apply("COVID") == "COVID"


def test_mtime_reload(tmp_path):
    p = tmp_path / "lex.json"
    _write(p, {"A": "a1"})
    lex = PronunciationLexicon(p)
    assert lex.apply("A B") == "a1 B"
    time.sleep(0.05)
    _write(p, {"A": "a2", "B": "b2"})
    os.utime(p, (time.time() + 1, time.time() + 1))
    assert lex.apply("A B") == "a2 b2"


def test_replace_atomic(tmp_path):
    p = tmp_path / "lex.json"
    lex = PronunciationLexicon(p)
    lex.replace({"GDP": "gi đi pi"})
    assert lex.get() == {"GDP": "gi đi pi"}
    assert json.loads(p.read_text(encoding="utf-8"))["entries"] == {"GDP": "gi đi pi"}
    # fresh instance sees the same data
    assert PronunciationLexicon(p).apply("GDP tăng") == "gi đi pi tăng"
