"""ネット上の画像を取ってくる。

ブラウザから絵を直接ドラッグしたときの経路。**ブラウザが渡してくるのは
絵そのものとは限らず、住所（URL）だけのことがある。** そのときはここで
取りに行かないと絵が手に入らない。

Qt を使わない。バイト列を返すところまでが仕事で、画像として展開できるか
どうかは `images.decode` が見る（取り込み経路を1本に保つため）。

**取り終わるまで画面を止める形にしてある。** 落とした直後に絵が出るのが
当たり前の操作なので、裏で取って後から差し込む形にすると「落ちたのか
落ちていないのか分からない時間」ができ、その間にもう一度落とされる。
代わりに待ち時間の上限を短く切り、待っている間は砂時計を出す
（→ `ui.canvas.PageView.dropEvent`）。
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from .errors import ImageFetchError

# 取りに行ってよい種類。**ここを緩めない。**
# `file:` を通すと、住所を落とすだけで手元のどのファイルでも読めてしまう
FETCHABLE_SCHEMES = ("http", "https")

# 待ち時間の上限（秒）。画面が止まる形なので短く切る。
# 応答の無い相手にこれ以上つきあうより、断って落とし直させるほうが早い
FETCH_TIMEOUT = 10.0

# 受け取る大きさの上限。原寸のスキャン画像でも数十MBに収まる。
# 上限が無いと、動画のような大物を落とされたときに際限なく溜め込む
FETCH_MAX_BYTES = 64 * 1024 * 1024

_CHUNK = 64 * 1024

# 名乗らないと断る配信元がある（既定の `Python-urllib/3.x` は弾かれやすい）
_USER_AGENT = "MANGA_layout"


def is_fetchable(scheme: str) -> bool:
    return scheme.lower() in FETCHABLE_SCHEMES


def display_name(url: str) -> str:
    """画面に出す短い名前。取れなければ住所そのもの。

    エラーを「どれが駄目だったか」の形で出すために要る。住所は長いので、
    まるごと出すと状態表示に収まらない
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    name = urllib.parse.unquote(parts.path.rsplit("/", 1)[-1])
    return name or parts.netloc or url


class _HttpOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """転送先まで http/https に限る。

    入口だけ見ても足りない。標準の転送処理は ftp も許すため、
    最初は https でも途中で別の種類へ連れて行かれうる
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        if not is_fetchable(urllib.parse.urlsplit(newurl).scheme):
            raise ImageFetchError(f"転送先が http/https ではありません: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpOnlyRedirect)


def fetch_bytes(
    url: str,
    *,
    timeout: float = FETCH_TIMEOUT,
    max_bytes: int = FETCH_MAX_BYTES,
) -> bytes:
    """住所からバイト列を取ってくる。取れなければ `ImageFetchError`。

    **中身が画像かどうかは見ない。** 配信元が名乗る種類（Content-Type）は
    当てにならず、画像を `application/octet-stream` で返す置き場所がある。
    判定は取り込み口（`assets.sniff_format` と `images.decode`）に任せる。
    """
    if not is_fetchable(urllib.parse.urlsplit(url).scheme):
        raise ImageFetchError(f"取りに行けない種類の住所です: {url}")

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with _opener.open(request, timeout=timeout) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ImageFetchError(
                        f"大きすぎます（{max_bytes // (1024 * 1024)}MB まで）"
                    )
                chunks.append(chunk)
    except urllib.error.HTTPError as e:
        raise ImageFetchError(f"取ってこられませんでした（{e.code} {e.reason}）") from e
    except urllib.error.URLError as e:
        raise ImageFetchError(f"つながりませんでした（{e.reason}）") from e
    except (OSError, ValueError) as e:
        # 時間切れ・証明書・壊れた住所。どれも利用者が落とし直せば済む
        raise ImageFetchError(f"取ってこられませんでした（{e}）") from e

    data = b"".join(chunks)
    if not data:
        raise ImageFetchError("中身が空でした")
    return data
