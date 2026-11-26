import json
import re
from pathlib import Path

from vizipvoice_addons.chunker import chunk_sentences, split_long, weight
from zipvoice.vizipvoice import split_text_into_sentences

TEXTS = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "plans/261125-1606-tqd-voice/assets/longform_texts.json"
    ).read_text(encoding="utf-8")
)["texts"]


def _doc_sentences(text):
    out = []
    for para in re.split(r"\n\s*\n", text):
        if para.strip():
            out.extend(split_text_into_sentences(para))
    return out


def test_weight():
    assert weight("Giá 1290000 đồng") == 8
    assert weight("năm 2026") == 5
    assert weight("") == 0


def test_short_sentences_merge():
    got = chunk_sentences(["Xin chào.", "Cảm ơn bạn rất nhiều.", "Hôm nay trời đẹp quá!"])
    assert got == ["Xin chào. Cảm ơn bạn rất nhiều. Hôm nay trời đẹp quá!"]


def test_split_long_exact_limit():
    s = " ".join(["từ"] * 24) + " cuối."
    assert len(split_long(s)) == 1


def test_essay_first_sentence_within_max():
    essay = next(t for t in TEXTS if t["id"] == "essay")["text"]
    first = split_text_into_sentences(essay)[0]
    assert all(weight(c) <= 25 for c in chunk_sentences([first]))


def test_content_preserved():
    for t in TEXTS:
        sents = _doc_sentences(t["text"])
        chunks = chunk_sentences(sents)
        assert " ".join(chunks).split() == " ".join(sents).split(), t["id"]


def test_short_tail_merges_back():
    twenty = " ".join(["một"] * 19) + " hai."
    assert len(chunk_sentences([twenty, "Xong."])) == 1


def test_fewer_short_chunks_than_sentences():
    sents, chunks = [], []
    for t in TEXTS:
        s = _doc_sentences(t["text"])
        sents += s
        chunks += chunk_sentences(s)
    r_sent = sum(weight(s) < 13 for s in sents) / len(sents)
    r_chunk = sum(weight(c) < 13 for c in chunks) / len(chunks)
    assert r_chunk < r_sent, f"chunk short ratio {r_chunk:.3f} vs sentence {r_sent:.3f}"
