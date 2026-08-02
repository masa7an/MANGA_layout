"""テスト用の境界条件画像を生成する。

標準ライブラリ（zlib / struct）だけで PNG を書き出すため、Pillow も venv も要らない。
生成物は git 管理下に置くので、通常このスクリプトを実行する必要はない。
画像を追加・作り直すときだけ実行する:

    ./venv/Scripts/python.exe tests/fixtures/make_fixtures.py

各画像が何を検証するためのものかは README.md を参照。
"""

import pathlib
import struct
import zlib

# PNG のカラータイプ → 1画素あたりのバイト数
_BYTES_PER_PIXEL = {0: 1, 2: 3, 6: 4}

FIXTURE_DIR = pathlib.Path(__file__).parent


def _chunk(tag: bytes, data: bytes) -> bytes:
    """PNG のチャンク（長さ・種別・中身・CRC の並び）を1つ組み立てる。"""
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def write_png(path: pathlib.Path, width: int, height: int, colortype: int, pixels: bytes) -> None:
    """生の画素バイト列を PNG として書き出す。

    pixels は左上から右下へ向かう画素の並び。フィルタは使わない（全行 0）。
    """
    stride = width * _BYTES_PER_PIXEL[colortype]
    expected = stride * height
    if len(pixels) != expected:
        raise ValueError(f"{path.name}: 画素数が合わない（期待 {expected} / 実際 {len(pixels)}）")

    raw = b"".join(
        b"\x00" + pixels[y * stride : (y + 1) * stride]
        for y in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, colortype, 0, 0, 0)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def make_rgba_transparent() -> None:
    """アルファチャンネル（透明度）ありの4象限。Day 1 のクリップボード検証の基準画像。

    左上=不透明な赤 / 右上=半透明の緑 / 左下=完全に透明 / 右下=不透明な青。
    透明度が落ちる経路を通ると、左下が黒や白に化けるので一目で分かる。
    """
    size = 64
    half = size // 2
    rows = []
    for y in range(size):
        for x in range(size):
            if y < half:
                rows.append((255, 0, 0, 255) if x < half else (0, 255, 0, 128))
            else:
                rows.append((0, 0, 0, 0) if x < half else (0, 0, 255, 255))
    px = b"".join(struct.pack("4B", *c) for c in rows)
    write_png(FIXTURE_DIR / "rgba_transparent.png", size, size, 6, px)


def make_rgb_opaque() -> None:
    """アルファなしの基本形。横長（4:3）なので縦横比の保持も一緒に確認できる。"""
    w, h = 64, 48
    px = b"".join(
        struct.pack("3B", (x * 4) % 256, (y * 5) % 256, 128)
        for y in range(h)
        for x in range(w)
    )
    write_png(FIXTURE_DIR / "rgb_opaque.png", w, h, 2, px)


def make_gray8() -> None:
    """グレースケール。読み込み時に RGB/RGBA へ変換されるかを確認する。"""
    size = 32
    px = bytes((x * 8) % 256 for _ in range(size) for x in range(size))
    write_png(FIXTURE_DIR / "gray8.png", size, size, 0, px)


def make_pixel_1x1() -> None:
    """1×1。コマにフィットさせる際の極端な拡大と、ゼロ除算の境界。"""
    write_png(FIXTURE_DIR / "pixel_1x1.png", 1, 1, 6, struct.pack("4B", 255, 0, 255, 255))


def make_extreme_aspect() -> None:
    """幅または高さが 1px。等比リサイズの丸め処理が 0 を作らないかを確認する。"""
    tall = b"".join(struct.pack("3B", 255, (y * 3) % 256, 0) for y in range(256))
    write_png(FIXTURE_DIR / "tall_1x256.png", 1, 256, 2, tall)

    wide = b"".join(struct.pack("3B", 0, (x * 3) % 256, 255) for x in range(256))
    write_png(FIXTURE_DIR / "wide_256x1.png", 256, 1, 2, wide)


def make_large() -> None:
    """大きい画像。表示用の縮小版が要るかの判断材料（要件定義 9章）。

    横縞なので圧縮がよく効き、画素数のわりにファイルは小さい。
    """
    w, h = 2000, 1500
    row_a = struct.pack("3B", 200, 200, 210) * w
    row_b = struct.pack("3B", 40, 40, 50) * w
    px = b"".join(row_a if (y // 20) % 2 == 0 else row_b for y in range(h))
    write_png(FIXTURE_DIR / "large_2000x1500.png", w, h, 2, px)


def make_duplicate_pair() -> None:
    """中身が完全に同じで名前だけ違う2枚。SHA1 による重複排除の検証用。

    両方を貼り付けても assets/ には1つしか増えないのが正しい挙動。
    """
    src = FIXTURE_DIR / "dup_source.png"
    size = 16
    px = b"".join(
        struct.pack("4B", 10, 200, 90, 255)
        for _ in range(size * size)
    )
    write_png(src, size, size, 6, px)
    (FIXTURE_DIR / "dup_copy.png").write_bytes(src.read_bytes())


def make_broken() -> None:
    """拡張子は .png だが中身が PNG ではないファイル。

    ドラッグ&ドロップで落とされたときに、落ちずにエラーを返せるかを確認する。
    """
    (FIXTURE_DIR / "broken.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"this is not a valid png body")


def main() -> None:
    for func in (
        make_rgba_transparent,
        make_rgb_opaque,
        make_gray8,
        make_pixel_1x1,
        make_extreme_aspect,
        make_large,
        make_duplicate_pair,
        make_broken,
    ):
        func()

    total = 0
    for path in sorted(FIXTURE_DIR.glob("*.png")):
        size = path.stat().st_size
        total += size
        print(f"{path.name:24} {size:8,} bytes")
    print(f"{'合計':24} {total:8,} bytes")


if __name__ == "__main__":
    main()
