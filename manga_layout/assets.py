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
from typing import Iterable

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
        if not data:
            raise UnknownImageFormatError("空のデータは取り込めません")

        ext = sniff_format(data)
        if ext is None:
            head = data[:8].hex(" ")
            raise UnknownImageFormatError(
                f"画像として認識できないデータです（先頭 8 バイト: {head}）"
            )

        digest = hashlib.sha1(data).hexdigest()
        ref = f"{ASSETS_DIRNAME}/{digest}.{ext}"
        path = self.dir / f"{digest}.{ext}"

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
