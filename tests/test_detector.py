from bot.detector import CodeDetector, normalize_spoken


def det(**kw):
    return CodeDetector(**kw)


def test_extracts_code_from_sentence():
    assert det().extract("ok guys the code is FREEEVAL2024X go go go") == ["FREEEVAL2024X"]


def test_ignores_plain_words_and_brands():
    d = det()
    assert d.extract("topstep is giving away free accounts follow for more") == []
    assert d.extract("TOPSTEPX TIKTOK GIVEAWAY") == []


def test_requires_digit_by_default_but_configurable():
    assert det().extract("code ABCDEFGH") == []
    assert det(require_digit=False).extract("code ABCDEFGH") == ["ABCDEFGH"]


def test_length_bounds():
    d = det()
    assert d.extract("A1B2C") == []  # too short
    assert d.extract("A1" * 11) == []  # too long


def test_submit_case():
    assert det().extract("abc123xyz") == ["ABC123XYZ"]
    assert det(submit_case="keep").extract("abc123xyz") == ["abc123xyz"]


def test_dedupe_exact_and_ocr_misread():
    d = det()
    assert [c.code for c in d.offer("code TS8K2P9Q", "chat")] == ["TS8K2P9Q"]
    assert d.offer("code TS8K2P9Q", "ocr") == []  # same code seen again
    assert d.offer("code TS8K2P9O", "ocr") == []  # 1 char off (Q->O misread)
    assert d.offer("code ZS8K2P9Q", "ocr") == []  # 1 off from the first code too
    assert [c.code for c in d.offer("code ZZ8K2P9Q", "ocr")] == ["ZZ8K2P9Q"]  # 2 off => treated as new
    assert [c.code for c in d.offer("code AB12CD34", "ocr")] == ["AB12CD34"]  # genuinely new


def test_forget_allows_retry():
    d = det()
    d.offer("TS8K2P9Q", "chat")
    d.forget("TS8K2P9Q")
    assert [c.code for c in d.offer("TS8K2P9Q", "chat")] == ["TS8K2P9Q"]


def test_hyphenated_code():
    assert det().extract("use FREE-4829-XQ now") == ["FREE-4829-XQ"]


def test_custom_pattern():
    d = det(pattern=r"TS[A-Z0-9]{6}", min_length=8)
    assert d.extract("ABCD1234 TSK4M9X2") == ["TSK4M9X2"]


def test_spoken_normalisation():
    assert normalize_spoken("the code is T S eight K two P nine") == "the code is TS8K2P9"
    assert normalize_spoken("alpha bravo one two three") == "AB123"
    # short runs of letters/digits are left as ordinary words
    assert normalize_spoken("I have one two") == "I have one two"


def test_spoken_then_detect():
    d = det()
    text = normalize_spoken("okay the code is F R E E four eight two nine")
    assert [c.code for c in d.offer(text, "audio")] == ["FREE4829"]
