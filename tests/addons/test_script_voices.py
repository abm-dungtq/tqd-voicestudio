from vizipvoice_addons.script_voices import split_voice_runs


def test_no_tags_single_run():
    assert split_voice_runs("xin chào thế giới", "Đinh-Quyết") == [
        ("Đinh-Quyết", "xin chào thế giới")
    ]


def test_voice_and_giong_tags():
    text = "[voice=MC]Chào buổi sáng.[giọng=Thanh-Vân] Tin tức hôm nay."
    runs = split_voice_runs(text, "Default")
    assert runs == [("MC", "Chào buổi sáng."), ("Thanh-Vân", " Tin tức hôm nay.")]


def test_default_voice_prefix_and_diacritic_names():
    text = "Mở đầu. [voice=Nhã Uyên]Phần hai."
    runs = split_voice_runs(text, "Đinh-Quyết")
    assert runs == [("Đinh-Quyết", "Mở đầu. "), ("Nhã Uyên", "Phần hai.")]


def test_empty_runs_dropped():
    text = "[voice=A]  [voice=B]Nội dung [voice=C]   "
    runs = split_voice_runs(text, "D")
    assert runs == [("B", "Nội dung ")]


def test_empty_input():
    assert split_voice_runs("", "D") == []
    assert split_voice_runs("   ", "D") == []
