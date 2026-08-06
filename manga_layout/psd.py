"""PSD（Photoshop の保存形式）を書き出す（要件定義 10.1）。

**ここは形式のことしか知らない。** ページ・コマ・フキダシといった作品の
言葉は出てこず、受け取るのは「名前の付いた画像を下から順に並べたもの」
だけ。作品をレイヤーへ分解するのは `ui.psd_export` の仕事で、分けて
おくと形式の細かい決まりと、何をレイヤーにするかの判断が混ざらない。

## 扱う範囲

8bit RGB のラスターレイヤーだけ（要件定義 10.1）。調整レイヤー・
スマートオブジェクト・ベクター・編集できるテキスト・レイヤーマスクは
書かない。**クリスタ側で作れるものを、こちらで持つ意味が無い。**

レイヤーフォルダは第2段階（コマごとに割るとき）に足す。いまは平らに
並べるだけ。

## 外部のライブラリを使わない理由

書き込みができるものは numpy と C 言語の拡張を要求し、更新が止まって
いる。**出来合いのパッケージが無ければその場でコンパイルが要り、2台の
PC で同じ環境を作れなくなる**（`requirements.txt` はバージョンを固定
して運用している）。必要な範囲だけなら標準機能で書けるので、依存を
増やさない。トーン（→ 6.27）で numpy を足さずに済んだのと同じ判断。

## 踏むと分かりにくい所

- **レイヤー名の欄は2つある。** 古いほうは1バイト文字しか入らないので、
  日本語は `luni` のほうへ UTF-16 で書く。古いほうだけに書くと**クリスタ
  でレイヤー名が文字化けする**
- **並びは下から上。** 最初のレイヤーが一番奥になる
- **矩形の下端・右端は「含まない」。** `bottom = top + 高さ`
- **透明度は掛けていない値（ストレート）。** Qt の `Format_ARGB32` が
  そのまま使える（`_Premultiplied` は掛けてあるので、渡す前に戻す）
- **合成済みの1枚（merged image）も要る。** これが無いと開けないソフトが
  ある。レイヤーを重ねた結果と食い違っていても形式としては通ってしまうので、
  渡す側が責任を持つ（→ `ui.psd_export`）
"""

from __future__ import annotations

import os
import pathlib
import struct
import sys
from dataclasses import dataclass

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from .errors import ExportError

SIGNATURE = b"8BPS"
VERSION = 1
DEPTH = 8
COLOR_MODE_RGB = 3

#: 1辺の上限。形式そのものの制限（超えるなら PSB という別形式になる）。
#: 書き出し側の上限（`ui.export.MAX_SIDE_PX` = 8,000）のほうが厳しいので、
#: ここに引っかかることは実際には無い
MAX_SIDE_PX = 30000

# 圧縮の種類。0 が無圧縮、1 が PackBits（同じ値の連続をまとめる方式）。
# **1 を使う。** 無圧縮だと1ページで数十MBになり、2 以降（zip）は読めない
# ソフトがある
COMPRESSION_RLE = 1

# PackBits のひとかたまりの上限（127+1 個）
_MAX_RUN = 128

# 書き出し中のファイルが完成品に見えないよう、いったんこの名前で書いて
# 置き換える（`ui.export.write_image` と同じ考え方）
TMP_SUFFIX = ".tmp"


@dataclass(frozen=True)
class PsdLayer:
    """PSD に並べる1枚。

    `image` は**すでに切り詰めてある**ものを渡す（`crop_to_content`）。
    キャンバス全面のまま渡しても書けるが、透明なだけの所まで圧縮に
    掛けることになり、枚数ぶんの待ち時間が積み上がる。

    `x` / `y` はキャンバスの中での左上の位置。切り詰めた分をここで戻す。

    `alias` は古い名前欄に入れる1バイト文字の名前。日本語の `name` は
    `luni` のほうへ書くが、そちらを読まないソフトのために**役割が分かる
    英字**を別に持たせる（全部同じ名前にすると見分けが付かなくなる）。
    """

    name: str
    alias: str
    image: QImage
    x: int = 0
    y: int = 0
    visible: bool = True
    opacity: float = 1.0


# -- 画素を取り出す ----------------------------------------------------------


def _planes(image: QImage) -> tuple[list[bytes], list[bytes], list[bytes], list[bytes]]:
    """1行ずつの R / G / B / A を取り出す。

    PSD は色ごとに分けて（面ごとに）持つが、`QImage` は1画素ぶんを
    まとめて持つ。並び替えは拡張スライス（`raw[off::4]`）で行う。
    Python の繰り返しで1バイトずつ拾うと、300万画素で数十秒かかる。
    """
    raw, w, h, bpl = _raw_of(image)

    # `Format_ARGB32` は「32bit の整数」として決まっているので、メモリの
    # 上での並びは環境の endian（バイトの並び順）で変わる。Windows は
    # little なので B, G, R, A の順に入っている
    if sys.byteorder == "little":
        b, g, r, a = 0, 1, 2, 3
    else:
        a, r, g, b = 0, 1, 2, 3

    def plane(off: int) -> list[bytes]:
        return [raw[y * bpl + off : y * bpl + w * 4 : 4] for y in range(h)]

    return plane(r), plane(g), plane(b), plane(a)


def _alpha_rows(image: QImage) -> list[bytes]:
    """1行ずつの透明度だけ。中身のある範囲を探すのに使う。"""
    raw, w, h, bpl = _raw_of(image)
    off = 3 if sys.byteorder == "little" else 0
    return [raw[y * bpl + off : y * bpl + w * 4 : 4] for y in range(h)]


def _raw_of(image: QImage) -> tuple[bytes, int, int, int]:
    """生バイト列と寸法。**`Format_ARGB32` に揃えてから取る。**

    `_Premultiplied`（透明度を色に掛け込んである形式）のまま取ると、
    半透明の所の色が暗く沈む。PSD が持つのは掛けていない値なので、
    ここで戻しておく。
    """
    if image.format() != QImage.Format.Format_ARGB32:
        image = image.convertToFormat(QImage.Format.Format_ARGB32)
    return (
        bytes(image.constBits()),
        image.width(),
        image.height(),
        image.bytesPerLine(),
    )


def content_bounds(image: QImage) -> QRect | None:
    """透明でない画素を囲む一番小さい矩形。全部透明なら None。

    **行ごとに標準の文字列操作で済ませる。** 1画素ずつ見ると300万回の
    繰り返しになるが、`count` / `lstrip` / `rstrip` は中で C の処理が
    走るので、行の数（数千回）しか Python の繰り返しが要らない。
    """
    rows = _alpha_rows(image)
    top: int | None = None
    bottom = 0
    left = image.width()
    right = 0
    for y, row in enumerate(rows):
        if row.count(0) == len(row):
            continue
        if top is None:
            top = y
        bottom = y
        first = len(row) - len(row.lstrip(b"\x00"))
        last = len(row.rstrip(b"\x00"))
        left = min(left, first)
        right = max(right, last)
    if top is None:
        return None
    return QRect(left, top, right - left, bottom - top + 1)


def crop_to_content(image: QImage) -> tuple[QImage, int, int] | None:
    """透明な縁を落とした1枚と、その左上の位置。全部透明なら None。"""
    box = content_bounds(image)
    if box is None:
        return None
    return image.copy(box), box.x(), box.y()


# -- 圧縮 --------------------------------------------------------------------

# 「同じ値が3つ以上続く場所」を探すときの手掛かり。**0x00 と 0xFF だけ
# 見る。** 透明な所（0）と、白・黒で塗られた所（255 / 0）がこの2つで、
# 長く続くのは実際ほぼこれしかない。絵の部分は元々ほとんど縮まないので、
# そこを丁寧に探しても待ち時間が増えるだけになる。
_RUN_SEEDS = (b"\x00\x00\x00", b"\xff\xff\xff")


def packbits(data: bytes) -> bytes:
    """PackBits で縮める。

    決まりは2つだけ。

    - 先頭が 0〜127 なら、続く「その数+1」個をそのまま並べたもの
    - 先頭が 129〜255 なら、次の1バイトを「257-その数」回（2〜128回）繰り返す

    **一番縮む並べ方でなくてよい。** 読む側は上の決まりどおりに戻すだけ
    なので、取りこぼした繰り返しはそのまま並べても壊れない。
    """
    out = bytearray()
    n = len(data)
    pos = 0
    while pos < n:
        start = _next_run(data, pos)
        if start < 0:
            start = n
        _literals(out, data, pos, start)
        pos = start
        if pos >= n:
            break
        # `_next_run` が見つけた以上、ここから3個以上は続いている
        chunk = data[pos : pos + _MAX_RUN]
        value = chunk[:1]
        run = len(chunk) - len(chunk.lstrip(value))
        out.append(257 - run)
        out += value
        pos += run
    return bytes(out)


def _next_run(data: bytes, pos: int) -> int:
    """次に同じ値が3つ以上続く場所。見つからなければ -1。"""
    found = -1
    for seed in _RUN_SEEDS:
        i = data.find(seed, pos)
        if i >= 0 and (found < 0 or i < found):
            found = i
    return found


def _literals(out: bytearray, data: bytes, start: int, end: int) -> None:
    """そのまま並べる区間を、128 バイトずつに切って書く。"""
    while start < end:
        size = min(_MAX_RUN, end - start)
        out.append(size - 1)
        out += data[start : start + size]
        start += size


def _rle_channel(rows: list[bytes]) -> bytes:
    """1つの面を PackBits で書く。**行ごとの長さの表が先に来る。**"""
    packed = [packbits(row) for row in rows]
    counts = b"".join(struct.pack(">H", len(p)) for p in packed)
    return struct.pack(">H", COMPRESSION_RLE) + counts + b"".join(packed)


# -- 各部を組み立てる --------------------------------------------------------


def _header(width: int, height: int, channels: int = 3) -> bytes:
    return struct.pack(
        ">4sH6xHIIHH",
        SIGNATURE,
        VERSION,
        channels,
        height,
        width,
        DEPTH,
        COLOR_MODE_RGB,
    )


def _resources(dpi: float) -> bytes:
    """解像度だけ書く（番号 1005）。

    入れておかないと**クリスタが 72dpi の画像として開く**。PNG に
    `setDotsPerMeterX/Y` を入れているのと同じ理由（→ 要件定義 6.7）。

    値は 16.16 の固定小数点（整数部と小数部を 16bit ずつ）。単位の 1 は
    「1インチあたりの画素数」。
    """
    fixed = round(dpi * 65536)
    data = struct.pack(">IHHIHH", fixed, 1, 1, fixed, 1, 1)
    # 名前は空（長さ0の文字列＋詰めもの1バイト）
    return b"8BIM" + struct.pack(">H", 1005) + b"\x00\x00" + struct.pack(">I", len(data)) + data


def _pascal(text: str, pad: int) -> bytes:
    """先頭に長さを持つ古い形式の文字列。`pad` の倍数まで詰める。"""
    raw = text.encode("ascii", "replace")[:255]
    body = bytes([len(raw)]) + raw
    return body + b"\x00" * (-len(body) % pad)


def _additional(key: bytes, data: bytes) -> bytes:
    return b"8BIM" + key + struct.pack(">I", len(data)) + data + b"\x00" * (len(data) % 2)


def _unicode_name(name: str) -> bytes:
    """`luni`。**日本語のレイヤー名はここにしか入らない。**"""
    encoded = name.encode("utf-16-be")
    return _additional(b"luni", struct.pack(">I", len(encoded) // 2) + encoded)


def _layer_record(layer: PsdLayer, lengths: list[int]) -> bytes:
    """1枚ぶんの見出し。**画素そのものはここには入らない**（後ろにまとめて置く）。

    `lengths` は面ごとのデータの長さで、順は透明度・R・G・B。圧縮の
    種類を書く2バイトを**含んだ**長さを渡す。
    """
    top = layer.y
    left = layer.x
    bottom = layer.y + layer.image.height()
    right = layer.x + layer.image.width()

    parts = [struct.pack(">4i", top, left, bottom, right), struct.pack(">H", 4)]
    for channel_id, length in zip((-1, 0, 1, 2), lengths, strict=True):
        parts.append(struct.pack(">hI", channel_id, length))

    # 8BIM + 重ね方（通常）+ 不透明度 + 切り抜き + 目印 + 詰めもの。
    # 目印の 2 のビットが立っていると**非表示**（0 が表示）。ラフを
    # 非表示で入れるのにこれを使う（→ 要件定義 10.1）
    flags = 0 if layer.visible else 2
    opacity = max(0, min(255, round(layer.opacity * 255)))
    parts.append(b"8BIMnorm" + bytes([opacity, 0, flags, 0]))

    extra = (
        struct.pack(">I", 0)  # レイヤーマスク（使わない）
        + struct.pack(">I", 0)  # 重ねる範囲の指定（使わない）
        + _pascal(layer.alias, 4)
        + _unicode_name(layer.name)
    )
    parts.append(struct.pack(">I", len(extra)) + extra)
    return b"".join(parts)


def _layer_section(layers: list[PsdLayer]) -> bytes:
    """レイヤーの見出しと画素をまとめた一区画。

    見出しには面ごとの長さが入るので、**先に全部の画素を圧縮してから**
    見出しを組む。
    """
    records: list[bytes] = []
    pixels: list[bytes] = []
    for layer in layers:
        r, g, b, a = _planes(layer.image)
        channels = [_rle_channel(rows) for rows in (a, r, g, b)]
        records.append(_layer_record(layer, [len(c) for c in channels]))
        pixels.extend(channels)

    info = struct.pack(">h", len(layers)) + b"".join(records) + b"".join(pixels)
    info += b"\x00" * (len(info) % 2)

    section = struct.pack(">I", len(info)) + info + struct.pack(">I", 0)
    return struct.pack(">I", len(section)) + section


def _merged(image: QImage) -> bytes:
    """合成済みの1枚。**行の長さの表は、3つの面をまとめて先に置く。**

    レイヤーの側（面ごとに表を持つ）と決まりが違うので、そのまま
    真似すると開けないファイルになる。
    """
    r, g, b, _a = _planes(image)
    packed = [[packbits(row) for row in rows] for rows in (r, g, b)]
    counts = b"".join(struct.pack(">H", len(p)) for rows in packed for p in rows)
    body = b"".join(p for rows in packed for p in rows)
    return struct.pack(">H", COMPRESSION_RLE) + counts + body


# -- 書き出す ----------------------------------------------------------------


def psd_bytes(layers: list[PsdLayer], merged: QImage, dpi: float) -> bytes:
    """PSD 1つぶんのバイト列。

    `layers` は**下から上**の順（最初が一番奥）。`merged` は全部を
    重ねた結果で、キャンバスの大きさもここから取る。
    """
    width, height = merged.width(), merged.height()
    if width < 1 or height < 1:
        raise ExportError(f"PSD の大きさが不正です（{width} × {height} px）")
    if max(width, height) > MAX_SIDE_PX:
        raise ExportError(
            f"{width} × {height} 画素は PSD にできません（1辺の上限 {MAX_SIDE_PX:,}px）"
        )

    return b"".join(
        [
            _header(width, height),
            struct.pack(">I", 0),  # 色の対応表（RGB では使わない）
            _with_length(_resources(dpi)),
            _layer_section(layers),
            _merged(merged),
        ]
    )


def _with_length(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def write_psd(
    path: pathlib.Path, layers: list[PsdLayer], merged: QImage, dpi: float
) -> None:
    """1つ書く。**別名で書き切ってから置き換える。**

    途中で落ちても、前回の書き出しが壊れた状態で残らない
    （`ui.export.write_image` と同じ）。
    """
    data = psd_bytes(layers, merged, dpi)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    try:
        tmp.write_bytes(data)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise ExportError(f"書き出せませんでした: {path}（{e}）") from e
    try:
        os.replace(tmp, path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise ExportError(
            f"{path.name} を置き換えられませんでした（{e}）。"
            "他のアプリで開いたままになっていないか確かめてください"
        ) from e
