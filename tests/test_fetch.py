"""ネット上の画像を取ってくる経路（`manga_layout.fetch`）の検証。

ブラウザから絵を直接ドラッグしたときに通る。**本物の通信はしない。**
`_opener.open` を差し替えて、応答の形だけを作って渡す。ここで実際に
外へ出ると、テストが回線の状態と相手の都合で落ちるようになる。

押さえたいのは3つ。

1. **http/https 以外へ取りに行かないこと。** `file:` を通すと、住所を
   落とすだけで手元のどのファイルでも読めてしまう。入口だけでなく
   転送先まで見る
2. **際限なく溜め込まないこと。** 上限を超えたらその場で断つ
3. **失敗を日本語1文にまとめること。** 通信の失敗は形が多いが、
   利用者にできることは「落とし直す」だけなので区別させない
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from manga_layout import fetch as fetch_module
from manga_layout.errors import ImageFetchError
from manga_layout.fetch import (
    _HttpOnlyRedirect,
    display_name,
    fetch_bytes,
    is_fetchable,
)

URL = "https://example.com/img/photo.png"


class FakeResponse:
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def read(self, size: int) -> bytes:
        return self._buf.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


@pytest.fixture
def opened(monkeypatch):
    """`_opener.open` の呼ばれ方を記録し、応答を差し替える。

    `calls` に (Request, timeout) が積まれる。`reply` を差し替えると
    返すものを変えられる
    """

    class Recorder:
        def __init__(self):
            self.calls: list[tuple] = []
            self.reply = lambda: FakeResponse(b"\x89PNG\r\n\x1a\n body")

        def __call__(self, request, timeout=None):
            self.calls.append((request, timeout))
            return self.reply()

    recorder = Recorder()
    monkeypatch.setattr(fetch_module._opener, "open", recorder)
    return recorder


class TestIsFetchable:
    @pytest.mark.parametrize("scheme", ["http", "https", "HTTPS"])
    def test_取りに行ける(self, scheme):
        assert is_fetchable(scheme)

    @pytest.mark.parametrize("scheme", ["file", "ftp", "data", "javascript", ""])
    def test_取りに行かない(self, scheme):
        assert not is_fetchable(scheme)


class TestDisplayName:
    """エラーに添える短い名前。住所をそのまま出すと状態表示に収まらない。"""

    def test_ファイル名を取り出す(self):
        assert display_name(URL) == "photo.png"

    def test_問い合わせは落とす(self):
        assert display_name("https://a.example/p.jpg?w=800&h=600") == "p.jpg"

    def test_符号化された日本語を戻す(self):
        assert display_name("https://a.example/%E7%B5%B5.png") == "絵.png"

    def test_ファイル名が無ければ配信元(self):
        assert display_name("https://images.example.com/") == "images.example.com"


class TestScheme:
    def test_取りに行かない住所は断る(self, opened):
        with pytest.raises(ImageFetchError):
            fetch_bytes("file:///C:/Windows/win.ini")

    def test_断るときは通信しない(self, opened):
        """判定だけして帰る。開いてしまってからでは遅い"""
        with pytest.raises(ImageFetchError):
            fetch_bytes("file:///C:/Windows/win.ini")
        assert opened.calls == []

    def test_転送先が別種なら断る(self):
        """入口が https でも、途中で連れて行かれることがある"""
        request = urllib.request.Request(URL)
        with pytest.raises(ImageFetchError):
            _HttpOnlyRedirect().redirect_request(
                request, None, 302, "Found", {}, "file:///C:/Windows/win.ini"
            )


class TestFetch:
    def test_取ってこられる(self, opened):
        opened.reply = lambda: FakeResponse(b"abcdef")
        assert fetch_bytes(URL) == b"abcdef"

    def test_区切って読んでもつながる(self, opened):
        """応答は何回にも分かれて届く。継ぎ目で欠けないこと"""
        body = bytes(range(256)) * 700  # 読み取り単位（64KB）をまたぐ大きさ
        opened.reply = lambda: FakeResponse(body)
        assert fetch_bytes(URL) == body

    def test_名乗る(self, opened):
        """既定の名前のままだと断る配信元がある"""
        fetch_bytes(URL)
        request, _ = opened.calls[0]
        assert "MANGA_layout" in request.get_header("User-agent")

    def test_待ち時間の上限を渡す(self, opened):
        fetch_bytes(URL, timeout=3.5)
        assert opened.calls[0][1] == 3.5

    def test_大きすぎれば断る(self, opened):
        opened.reply = lambda: FakeResponse(b"x" * 100)
        with pytest.raises(ImageFetchError, match="大きすぎます"):
            fetch_bytes(URL, max_bytes=10)

    def test_ちょうど上限までは通す(self, opened):
        opened.reply = lambda: FakeResponse(b"x" * 10)
        assert fetch_bytes(URL, max_bytes=10) == b"x" * 10

    def test_空なら断る(self, opened):
        """0 バイトを `assets/` へ入れると、名前だけの残骸ができる"""
        opened.reply = lambda: FakeResponse(b"")
        with pytest.raises(ImageFetchError, match="空"):
            fetch_bytes(URL)


class TestFailure:
    """失敗の形はどれも `ImageFetchError` 1つにまとめる。"""

    def _raise(self, opened, error: Exception):
        def boom():
            raise error

        opened.reply = boom

    def test_断られたら番号を添える(self, opened):
        """404 と 403 では利用者のやることが違う。番号を残す"""
        self._raise(opened, urllib.error.HTTPError(URL, 404, "Not Found", {}, None))
        with pytest.raises(ImageFetchError, match="404"):
            fetch_bytes(URL)

    def test_つながらないとき(self, opened):
        self._raise(opened, urllib.error.URLError("名前を解決できません"))
        with pytest.raises(ImageFetchError, match="つながりませんでした"):
            fetch_bytes(URL)

    def test_時間切れ(self, opened):
        self._raise(opened, TimeoutError("timed out"))
        with pytest.raises(ImageFetchError):
            fetch_bytes(URL)

    def test_証明書などの失敗(self, opened):
        self._raise(opened, OSError("certificate verify failed"))
        with pytest.raises(ImageFetchError):
            fetch_bytes(URL)

    def test_壊れた住所(self, opened):
        self._raise(opened, ValueError("unknown url type"))
        with pytest.raises(ImageFetchError):
            fetch_bytes(URL)
