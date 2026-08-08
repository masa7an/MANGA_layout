"""画像の実体を内容ハッシュで管理する。

要件定義 5章「assets/ の扱い」のとおり、ファイル名は中身の SHA1
（内容から作る指紋のような文字列）にする。同じ画像を何度貼り付けても
実体はひとつで済み、重複排除が何もしなくても効く。

この層はバイト列しか扱わず、Qt に依存しない。おかげでテストが
画面なしで速く回る。

**画像として復号できるかの検証はここでは行わない。** 署名だけ正しくて
中身が壊れているファイル（tests/fixtures/broken.png がまさにそれ）は
ここでは弾けないので、取り込み側で QImage による復号に成功したものだけを
渡すこと。壊れたデータを assets/ に入れてしまうと、内容ハッシュ名なので
あとから「どれが壊れているか」を人が見分けられなくなる。
"""

from __future__ import annotations

import hashlib
import os
import pathlib
from collections.abc import Iterable

from .errors import AssetError, UnknownImageFormatError

ASSETS_DIRNAME = "assets"
UNUSED_DIRNAME = "_unused"

# 先頭のバイト列から形式を見分ける。拡張子は信用しない
# （クリスタから貼り付けた画像には、そもそも名前が無い）
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
)


def sniff_format(data: bytes) -> str | None:
    """バイト列の先頭から画像形式を推定する。分からなければ None。"""
    for signature, ext in _SIGNATURES:
        if data.startswith(signature):
            return ext
    # WebP は RIFF コンテナなので、離れた位置に印がある
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def ref_for(data: bytes) -> str:
    """バイト列に対応する参照文字列（`assets/<sha1>.<拡張子>`）。

    書き込みはしない。保存先フォルダが決まる前でも参照だけは確定させたい
    `PendingAssets` と、実際に書き込む `AssetStore` で同じ名前になるよう、
    計算をここ1箇所に置く。
    """
    if not data:
        raise UnknownImageFormatError("空のデータは取り込めません")

    ext = sniff_format(data)
    if ext is None:
        head = data[:8].hex(" ")
        raise UnknownImageFormatError(
            f"画像として認識できないデータです（先頭 8 バイト: {head}）"
        )
    return f"{ASSETS_DIRNAME}/{hashlib.sha1(data).hexdigest()}.{ext}"


def _atomic_write_bytes(path: pathlib.Path, data: bytes) -> None:
    """途中で落ちても半端なファイルが残らないように書く。

    内容ハッシュを名前にしている以上、中身が欠けたファイルが正しい名前で
    残ってしまうと、以降ずっと壊れた画像を「あるもの」として使い続ける。
    """
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class AssetStore:
    """プロジェクトフォルダ内の assets/ を受け持つ。"""

    def __init__(self, project_dir: pathlib.Path | str) -> None:
        self.project_dir = pathlib.Path(project_dir)

    @property
    def dir(self) -> pathlib.Path:
        return self.project_dir / ASSETS_DIRNAME

    @property
    def unused_dir(self) -> pathlib.Path:
        return self.dir / UNUSED_DIRNAME

    # -- 取り込み ----------------------------------------------------------

    def add_bytes(self, data: bytes) -> str:
        """バイト列を取り込み、project.json に書く参照文字列を返す。

        戻り値は `assets/<sha1>.<拡張子>` の形。プロジェクトフォルダからの
        相対パスなので、フォルダごと移動しても壊れない。

        同じ中身が既にあれば何も書かずに同じ参照を返す（重複排除）。
        """
        ref = ref_for(data)
        path = self.resolve(ref)

        if not path.exists():
            self.dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_bytes(path, data)
        return ref

    def add_file(self, src: pathlib.Path | str) -> str:
        """ファイルから取り込む。ドラッグ&ドロップとファイル読み込みの経路。"""
        src = pathlib.Path(src)
        try:
            data = src.read_bytes()
        except OSError as e:
            raise AssetError(f"画像を読めませんでした: {src}（{e}）") from e
        return self.add_bytes(data)

    # -- 参照 --------------------------------------------------------------

    def resolve(self, ref: str) -> pathlib.Path:
        """参照文字列を実ファイルのパスに変換する。

        参照は project.json 由来＝外から来た文字列なので、
        `assets/../../` のような細工でフォルダの外へ出られないことを確かめる。
        """
        if not ref:
            raise AssetError("空の参照です")
        normalized = ref.replace("\\", "/")
        parts = normalized.split("/")
        if len(parts) != 2 or parts[0] != ASSETS_DIRNAME:
            raise AssetError(f"assets/ 直下を指していない参照です: {ref!r}")
        name = parts[1]
        if not name or name in (".", "..") or ":" in name:
            raise AssetError(f"扱えない参照です: {ref!r}")
        return self.dir / name

    def exists(self, ref: str) -> bool:
        try:
            return self.resolve(ref).is_file()
        except AssetError:
            return False

    def read(self, ref: str) -> bytes:
        path = self.resolve(ref)
        try:
            return path.read_bytes()
        except OSError as e:
            raise AssetError(f"画像を読めませんでした: {ref}（{e}）") from e

    def list_refs(self) -> list[str]:
        """assets/ 直下にある画像の参照一覧。`_unused/` の中は含めない。"""
        if not self.dir.is_dir():
            return []
        return sorted(
            f"{ASSETS_DIRNAME}/{p.name}"
            for p in self.dir.iterdir()
            if p.is_file() and not p.name.endswith(".tmp")
        )

    # -- 掃除 --------------------------------------------------------------

    def collect_unused(self, referenced: Iterable[str]) -> list[str]:
        """どこからも参照されていない画像を `assets/_unused/` へ移す。

        削除ではなく移動なのは、判断を誤ったときに戻せるようにするため。

        **保存のたびに自動で呼んではいけない。** Undo で消した画像を
        復活させたとき、実体が `_unused/` へ移っていると参照が切れる。
        利用者が明示的に「未使用ファイルを整理」を選んだときだけ呼ぶこと。
        """
        keep = set(referenced)
        moved: list[str] = []
        for ref in self.list_refs():
            if ref in keep:
                continue
            src = self.resolve(ref)
            self.unused_dir.mkdir(parents=True, exist_ok=True)
            dest = self.unused_dir / src.name
            if dest.exists():
                # 内容ハッシュ名なので、同名なら中身も同じ。移動元を消せばよい
                src.unlink()
            else:
                os.replace(src, dest)
            moved.append(ref)
        return moved


class PendingAssets:
    """保存先フォルダが決まる前に取り込んだ画像を、メモリ上で預かる。

    新しい作品はフォルダを持たないため、貼り付けた画像を書く場所が無い。
    「先に保存してください」と断ると、この道具の主動線である
    「クリスタから Ctrl+V」がいきなり止まってしまうので、預かっておいて
    保存時に `flush_to()` で書き出す。

    参照文字列は `AssetStore` と同じ計算（内容の SHA1）で作るため、
    保存の前後で project.json の中身は変わらない。
    """

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def __contains__(self, ref: str) -> bool:
        return ref in self._data

    def __len__(self) -> int:
        return len(self._data)

    def add(self, data: bytes) -> str:
        ref = ref_for(data)
        self._data[ref] = data
        return ref

    def get(self, ref: str) -> bytes | None:
        return self._data.get(ref)

    def flush_to(self, store: AssetStore) -> list[str]:
        """預かっている画像をすべて書き出す。**控えはここでは手放さない。**

        **参照されていないものも書き出す。** 保存の時点で参照が無くても、
        Undo で戻せば復活する（要件定義 5章「assets/ の扱い」）。
        余ったファイルは利用者が「未使用ファイルを整理」を選んだときに片付く。

        呼ぶ側（`EditorState.save`）は、この後に `project.json` を書く。
        そちらが失敗した場合に備えて、控えは呼ぶ側が成功を確かめてから
        `clear()` で手放す。**先に手放すと、失敗を跨いで控えが消え、
        別の場所へ保存し直しても実体が書かれないまま project.json だけが
        できる**（2026-08-08 に発見）。`add_bytes` は内容ハッシュ名で
        既にあれば書き直さないため、ここで何度呼んでも安全
        （＝失敗しての再試行でも重複や余計な書き込みは起きない）。
        """
        written = sorted(self._data)
        for ref in written:
            store.add_bytes(self._data[ref])
        return written

    def clear(self) -> None:
        """控えを手放す。呼ぶのは書き出しの成功を確かめたあと（→ `flush_to`）。"""
        self._data.clear()
