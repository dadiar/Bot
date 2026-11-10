import pytest

from bot.config import load_config
from bot.sources import tiktok_live_url, youtube_video_id


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=u576qOKSTAk",
    "https://www.youtube.com/watch?v=u576qOKSTAk&feature=share",
    "https://youtu.be/u576qOKSTAk?t=3",
    "https://www.youtube.com/live/u576qOKSTAk",
])
def test_youtube_video_id(url):
    assert youtube_video_id(url) == "u576qOKSTAk"


def test_non_youtube_url_has_no_id():
    assert youtube_video_id("https://www.tiktok.com/@x/live") is None


def test_tiktok_url():
    assert tiktok_live_url("@abc") == "https://www.tiktok.com/@abc/live"


def write(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text(body)
    return p


def test_config_accepts_youtube_url_without_streamer(tmp_path):
    cfg = load_config(write(tmp_path, "stream_url: https://youtu.be/u576qOKSTAk\nredeem: {url: 'https://x.test'}\n"))
    assert cfg.stream_url.endswith("u576qOKSTAk") and cfg.mode == "dry-run"


def test_config_requires_some_stream(tmp_path):
    with pytest.raises(ValueError, match="stream_url"):
        load_config(write(tmp_path, "redeem: {url: 'https://x.test'}\n"))


def test_config_requires_redeem_url(tmp_path):
    with pytest.raises(ValueError, match="redeem.url"):
        load_config(write(tmp_path, "streamer: x\n"))


def test_config_accounts_become_tabs(tmp_path):
    cfg = load_config(write(tmp_path, """
stream_url: https://youtu.be/u576qOKSTAk
redeem:
  error_selector: .err
  accounts:
    - {name: 2k flex, url: "https://x.test/2kflex"}
    - {name: 50k pro, url: "https://x.test/50kpro", error_selector: .bad}
"""))
    tabs = cfg.redeem.tabs()
    assert [t.name for t in tabs] == ["2k flex", "50k pro"]
    assert tabs[0].error_selector == ".err" and tabs[1].error_selector == ".bad"


def test_config_account_needs_url(tmp_path):
    with pytest.raises(ValueError, match="no url"):
        load_config(write(tmp_path, "streamer: x\nredeem: {accounts: [{name: a, url: ''}]}\n"))
