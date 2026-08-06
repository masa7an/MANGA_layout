"""PSD（Photoshop の保存形式）を書く処理の検証（要件定義 10.1）。

**書いたものを読み返して確かめる。** 形式の決まりを守れているかは、
書く側の処理をもう一度呼んでも分からない。ここでは PSD を読む最小限の
処理をテスト側に持ち、バイト列から寸法・レイヤー名・画素を取り出して
突き合わせる。

押さえたいのは5つ。

1. **縮めたものが元に戻ること。** PackBits を取り違えると、絵が斜めに
   ずれた状態で開く（行の途中から読み始めるため）
2. **日本語のレイヤー名が化けないこと。** 古い名前欄に日本語を入れると
   化ける。`luni` のほうへ書けているか
3. **非表示の目印が立つこと。** ラフを非表示で入れる（→ 10.1）ための要
4. **透明な縁が落ちること。** キャンバス全面のまま持つと枚数ぶん膨らむ
5. **合成済みの1枚が正しく入ること。** これが無いと開けないソフトがある
"""

from __future__ import annotations

import struct

import pytest
from PySide6.QtGui import QColor, QImage, QPainter

from manga_layout import ExportError
from manga_layout.psd import (
    MAX_SIDE_PX,
    PsdLayer,
    content_bounds,
    crop_to_content,
    packbits,
    psd_bytes,
    write_psd,
)

# ---------------------------------------------------------------------------
# 読み返すための最小限の処理（テスト専用）
# ---------------------------------------------------------------------------


def unpackbits(data: bytes, expected: int) -> bytes:
    """PackBits を元に戻す。`expected` バイトぶん取り出す。"""
    out = bytearray()
    i = 0
    while len(out) < expected:
        head = data[i]
        i += 1
        if head < 128:
            size = head + 1
            out += data[i : i + size]
            i += size
        elif head > 128:
            out += data[i : i + 1] * (257 - head)
            i += 1
    return bytes(out)


def _rle_rows(data: bytes, width: int, height: int) -> list[bytes]:
    """行ごとの長さの表が先に来る形を読む。"""
    counts = [
        struct.unpack_from(">H", data, i * 2)[0] for i in range(height)
    ]
    rows = []
    pos = height * 2
    for count in counts:
        rows.append(unpackbits(data[pos : pos + count], width))
        pos += count
    return rows


def parse_psd(raw: bytes) -> dict:
    """PSD を読み解く。**この検証に要る所だけ。**"""
    sig, version, channels, height, width, depth, mode = struct.unpack_from(
        ">4sH6xHIIHH", raw, 0
    )
    pos = 26

    (color_len,) = struct.unpack_from(">I", raw, pos)
    pos += 4 + color_len

    (res_len,) = struct.unpack_from(">I", raw, pos)
    resources = raw[pos + 4 : pos + 4 + res_len]
    pos += 4 + res_len

    (lm_len,) = struct.unpack_from(">I", raw, pos)
    layers = _parse_layers(raw[pos + 4 : pos + 4 + lm_len])
    pos += 4 + lm_len

    # 合成済みの1枚は**3つの面を縦に積んだ形**で読む（行の長さの表が
    # 面をまたいで先にまとまっているため）。中身を見たいときは
    # `merged_raw` から `_rle_rows` を呼ぶ
    return {
        "signature": sig,
        "version": version,
        "channels": channels,
        "width": width,
        "height": height,
        "depth": depth,
        "mode": mode,
        "resources": resources,
        "layers": layers,
        "merged_compression": struct.unpack_from(">H", raw, pos)[0],
        "merged_raw": raw[pos:],
    }


def _parse_layers(section: bytes) -> list[dict]:
    (info_len,) = struct.unpack_from(">I", section, 0)
    info = section[4 : 4 + info_len]
    (count,) = struct.unpack_from(">h", info, 0)
    pos = 2

    layers = []
    for _ in range(count):
        top, left, bottom, right = struct.unpack_from(">4i", info, pos)
        pos += 16
        (channel_count,) = struct.unpack_from(">H", info, pos)
        pos += 2
        channels = []
        for _ in range(channel_count):
            channel_id, length = struct.unpack_from(">hI", info, pos)
            pos += 6
            channels.append((channel_id, length))
        blend = info[pos : pos + 8]
        pos += 8
        opacity, clipping, flags, _filler = info[pos : pos + 4]
        pos += 4
        (extra_len,) = struct.unpack_from(">I", info, pos)
        pos += 4
        extra = info[pos : pos + extra_len]
        pos += extra_len
        layers.append(
            {
                "rect": (left, top, right - left, bottom - top),
                "channels": channels,
                "blend": blend,
                "opacity": opacity,
                "clipping": clipping,
                "flags": flags,
                **_parse_extra(extra),
            }
        )

    for layer in layers:
        _, _, width, height = layer["rect"]
        layer["planes"] = {}
        for channel_id, length in layer["channels"]:
            body = info[pos : pos + length]
            pos += length
            (compression,) = struct.unpack_from(">H", body, 0)
            assert compression == 1, "PackBits で書いているはず"
            layer["planes"][channel_id] = _rle_rows(body[2:], width, height)
    return layers


def _parse_extra(extra: bytes) -> dict:
    (mask_len,) = struct.unpack_from(">I", extra, 0)
    pos = 4 + mask_len
    (blend_len,) = struct.unpack_from(">I", extra, pos)
    pos += 4 + blend_len

    size = extra[pos]
    alias = extra[pos + 1 : pos + 1 + size].decode("ascii")
    pos += -(1 + size) % 4 + 1 + size

    name = None
    while pos + 12 <= len(extra):
        key = extra[pos + 4 : pos + 8]
        (length,) = struct.unpack_from(">I", extra, pos + 8)
        body = extra[pos + 12 : pos + 12 + length]
        if key == b"luni":
            (chars,) = struct.unpack_from(">I", body, 0)
            name = body[4 : 4 + chars * 2].decode("utf-16-be")
        pos += 12 + length + length % 2
    return {"alias": alias, "name": name}


# ---------------------------------------------------------------------------
# 材料
# ---------------------------------------------------------------------------


def solid(width: int, height: int, color: str) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def transparent(width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    return image


def with_box(width: int, height: int, x: int, y: int, w: int, h: int) -> QImage:
    """透明な紙の一部だけ塗った1枚。"""
    image = transparent(width, height)
    painter = QPainter(image)
    painter.fillRect(x, y, w, h, QColor("#204080"))
    painter.end()
    return image


def pixels_of(layer: dict) -> list[list[tuple[int, int, int, int]]]:
    """読み返したレイヤーを (R, G, B, A) の並びに戻す。"""
    rows = []
    for y in range(layer["rect"][3]):
        row = []
        for x in range(layer["rect"][2]):
            row.append(
                (
                    layer["planes"][0][y][x],
                    layer["planes"][1][y][x],
                    layer["planes"][2][y][x],
                    layer["planes"][-1][y][x],
                )
            )
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 1. 縮めたものが元に戻る
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00",
        b"\x00" * 3,
        b"\x00" * 500,
        b"\xff" * 129,
        b"\x01\x02\x03\x04",
        bytes(range(256)),
        b"\x00" * 200 + bytes(range(64)) + b"\xff" * 300,
        bytes((i * 37) % 256 for i in range(1000)),
        b"\xab" * 50,
    ],
)
def test_縮めたものが元に戻る(data):
    assert unpackbits(packbits(data), len(data)) == data


def test_同じ値が続く所は大きく縮む():
    assert len(packbits(b"\x00" * 4096)) < 100


def test_縮まないものでも極端には増えない():
    data = bytes((i * 37) % 256 for i in range(4096))
    # そのまま並べる形でも 128 バイトごとに1バイト増えるだけ
    assert len(packbits(data)) <= len(data) + len(data) // 128 + 1


# ---------------------------------------------------------------------------
# 2. 全体の形
# ---------------------------------------------------------------------------


def test_見出しに寸法と色数が入る():
    merged = solid(40, 30, "#FFFFFF")
    parsed = parse_psd(psd_bytes([], merged, 150.0))
    assert parsed["signature"] == b"8BPS"
    assert parsed["version"] == 1
    assert (parsed["width"], parsed["height"]) == (40, 30)
    assert parsed["depth"] == 8
    assert parsed["mode"] == 3  # RGB
    assert parsed["channels"] == 3


def test_解像度が書き込まれる():
    """入れておかないとクリスタが 72dpi の画像として開く（→ 6.7）。"""
    parsed = parse_psd(psd_bytes([], solid(10, 10, "#FFFFFF"), 175.0))
    resources = parsed["resources"]
    assert resources[:4] == b"8BIM"
    assert struct.unpack_from(">H", resources, 4)[0] == 1005
    (fixed,) = struct.unpack_from(">I", resources, 12)
    assert fixed / 65536 == pytest.approx(175.0)


def test_大きすぎるものは断る():
    """形式そのものの上限。実際は書き出し側の上限で先に止まる。"""
    huge = QImage(1, 1, QImage.Format.Format_ARGB32)
    with pytest.raises(ExportError, match=f"{MAX_SIDE_PX:,}"):
        # 実物を確保せずに確かめたいので、寸法だけ差し替えた偽物を渡す
        psd_bytes([], _FakeSize(MAX_SIDE_PX + 1, 10, huge), 150.0)


class _FakeSize:
    """寸法だけ大きいと言い張る1枚。上限の判定だけを確かめる。"""

    def __init__(self, width: int, height: int, image: QImage) -> None:
        self._w, self._h, self._image = width, height, image

    def width(self) -> int:
        return self._w

    def height(self) -> int:
        return self._h

    def __getattr__(self, name):
        return getattr(self._image, name)


# ---------------------------------------------------------------------------
# 3. レイヤー
# ---------------------------------------------------------------------------


def test_日本語のレイヤー名が化けない():
    """古い名前欄には入らないので `luni` のほうへ書く（→ 10.1）。"""
    layer = PsdLayer(name="フキダシ", alias="balloon", image=solid(8, 6, "#FF0000"))
    parsed = parse_psd(psd_bytes([layer], solid(8, 6, "#FFFFFF"), 150.0))
    assert parsed["layers"][0]["name"] == "フキダシ"
    assert parsed["layers"][0]["alias"] == "balloon"


def test_並びは下から上():
    """最初に渡したものが一番奥になる。"""
    images = solid(4, 4, "#FFFFFF")
    layers = [
        PsdLayer(name="用紙", alias="paper", image=images),
        PsdLayer(name="絵", alias="art", image=images),
        PsdLayer(name="セリフ", alias="text", image=images),
    ]
    parsed = parse_psd(psd_bytes(layers, images, 150.0))
    assert [layer["name"] for layer in parsed["layers"]] == ["用紙", "絵", "セリフ"]


def test_非表示の目印が立つ():
    """ラフを非表示で入れるための要（→ 10.1）。"""
    image = solid(4, 4, "#00FF00")
    layers = [
        PsdLayer(name="ラフ", alias="rough", image=image, visible=False),
        PsdLayer(name="絵", alias="art", image=image),
    ]
    parsed = parse_psd(psd_bytes(layers, image, 150.0))
    assert parsed["layers"][0]["flags"] & 2  # 2 のビットが立っていれば非表示
    assert not parsed["layers"][1]["flags"] & 2


def test_不透明度が入る():
    image = solid(4, 4, "#00FF00")
    layer = PsdLayer(name="ラフ", alias="rough", image=image, opacity=0.4)
    parsed = parse_psd(psd_bytes([layer], image, 150.0))
    assert parsed["layers"][0]["opacity"] == round(0.4 * 255)


def test_重ね方は通常で固定():
    image = solid(4, 4, "#00FF00")
    layer = PsdLayer(name="絵", alias="art", image=image)
    parsed = parse_psd(psd_bytes([layer], image, 150.0))
    assert parsed["layers"][0]["blend"] == b"8BIMnorm"


def test_置き場所が矩形に入る():
    """切り詰めた分を x / y で戻す。"""
    layer = PsdLayer(name="絵", alias="art", image=solid(5, 3, "#123456"), x=12, y=7)
    parsed = parse_psd(psd_bytes([layer], solid(40, 40, "#FFFFFF"), 150.0))
    assert parsed["layers"][0]["rect"] == (12, 7, 5, 3)


def test_レイヤーの画素が元に戻る():
    """透明度を含めて4面とも往復する。"""
    source = with_box(10, 8, 2, 3, 5, 4)
    layer = PsdLayer(name="絵", alias="art", image=source)
    parsed = parse_psd(psd_bytes([layer], solid(10, 8, "#FFFFFF"), 150.0))

    got = pixels_of(parsed["layers"][0])
    for y in range(8):
        for x in range(10):
            color = source.pixelColor(x, y)
            assert got[y][x] == (color.red(), color.green(), color.blue(), color.alpha())


def test_合成済みの1枚が入る():
    """これが無いと開けないソフトがある。"""
    merged = QImage(6, 4, QImage.Format.Format_ARGB32)
    merged.fill(QColor("#FFFFFF"))
    painter = QPainter(merged)
    painter.fillRect(1, 1, 3, 2, QColor("#3366CC"))
    painter.end()

    parsed = parse_psd(psd_bytes([], merged, 150.0))
    assert parsed["merged_compression"] == 1
    rows = _rle_rows(parsed["merged_raw"][2:], 6, 4 * 3)
    # 面ごとに縦に積まれている（R が4行、G が4行、B が4行）
    for y in range(4):
        for x in range(6):
            color = merged.pixelColor(x, y)
            assert rows[y][x] == color.red()
            assert rows[4 + y][x] == color.green()
            assert rows[8 + y][x] == color.blue()


# ---------------------------------------------------------------------------
# 4. 透明な縁を落とす
# ---------------------------------------------------------------------------


def test_中身のある範囲を見つける():
    box = content_bounds(with_box(40, 30, 5, 6, 10, 8))
    assert (box.x(), box.y(), box.width(), box.height()) == (5, 6, 10, 8)


def test_全部透明なら範囲は無い():
    assert content_bounds(transparent(20, 20)) is None
    assert crop_to_content(transparent(20, 20)) is None


def test_全部塗ってあれば全面():
    box = content_bounds(solid(20, 12, "#000000"))
    assert (box.x(), box.y(), box.width(), box.height()) == (0, 0, 20, 12)


def test_切り詰めた1枚と置き場所が返る():
    cropped, x, y = crop_to_content(with_box(40, 30, 5, 6, 10, 8))
    assert (x, y) == (5, 6)
    assert (cropped.width(), cropped.height()) == (10, 8)
    assert cropped.pixelColor(0, 0).alpha() == 255


def test_半透明も中身として数える():
    """薄いラフ（→ 6.23）を落としてしまわない。"""
    image = transparent(20, 20)
    painter = QPainter(image)
    painter.fillRect(3, 4, 2, 2, QColor(0, 0, 0, 8))
    painter.end()
    box = content_bounds(image)
    assert (box.x(), box.y(), box.width(), box.height()) == (3, 4, 2, 2)


# ---------------------------------------------------------------------------
# 5. ファイルとして書く
# ---------------------------------------------------------------------------


def test_ファイルに書ける(tmp_path):
    path = tmp_path / "深い" / "p01.psd"
    layer = PsdLayer(name="用紙", alias="paper", image=solid(8, 8, "#FFFFFF"))
    write_psd(path, [layer], solid(8, 8, "#FFFFFF"), 150.0)

    assert path.exists()
    assert path.read_bytes()[:4] == b"8BPS"


def test_途中の名前を残さない(tmp_path):
    """別名で書き切ってから置き換える（`write_image` と同じ）。"""
    path = tmp_path / "p01.psd"
    write_psd(path, [], solid(8, 8, "#FFFFFF"), 150.0)
    assert [p.name for p in tmp_path.iterdir()] == ["p01.psd"]
