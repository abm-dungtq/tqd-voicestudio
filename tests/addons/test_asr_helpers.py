import pytest

from vizipvoice_addons.asr import cer, normalize_for_compare


def test_normalize_for_compare_basic():
    assert normalize_for_compare("  Xin   Chào, Việt Nam! ") == "xin chào việt nam"
    assert normalize_for_compare("ABC.def") == "abc def"
    assert normalize_for_compare("") == ""


def test_normalize_for_compare_nfc():
    # decomposed form normalised to NFC before compare
    decomposed = "a\u0301"  # a + combining acute
    assert normalize_for_compare(decomposed) == "á"


def test_normalize_for_compare_with_normalizer():
    fake = lambda t: t.replace("một", "1")  # noqa: E731
    assert normalize_for_compare("một hai", normalizer=fake) == "1 hai"


def test_cer():
    assert cer("abc", "abc") == 0.0
    assert cer("abc", "abd") == pytest.approx(1 / 3)
    assert cer("abc", "") == 1.0
    assert cer("", "") == 0.0
    assert cer("", "x") == 1.0
    assert cer("xin chào", "xin chao") < 0.2


def test_resample_to_16k():
    import numpy as np

    from vizipvoice_addons.asr import _resample_to_16k

    wav = np.sin(np.linspace(0, 100, 48000)).astype(np.float32)
    out = _resample_to_16k(wav, 48000)
    assert out.dtype == np.float32
    assert abs(len(out) - 16000) <= 2
    same = _resample_to_16k(wav[:1600], 16000)
    assert len(same) == 1600
