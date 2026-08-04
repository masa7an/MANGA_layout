"""マークの素材を `manga_layout/stickers/` へ取り込む（要件定義 6.14）。

**透明な余白を削ってから置く。** 元の絵には手を加えず、完全に透明な画素
だけを外側から落とす。

余白を残したまま入れると3つ困る。

- 素材ごとに余白の割合が違うため、**同じ大きさで置いたつもりが違って見える**
  （実測: ！は画像の高さの 82%、!? は 63% しか記号が占めていなかった）
- 当たり判定は矩形なので、**何も見えていない余白をクリックしてもマークが掴める**
- 置いた直後の大きさ（長辺 240px）の意味が、素材の余白しだいで変わってしまう

元の素材はリポジトリの外（`data/` など）に置いたまま。ここへ入れるのは
アプリが配る側の実体なので、取り込みの手順をスクリプトとして残しておく。

使い方
------
    ./venv/Scripts/python.exe tools/import_sticker.py data/ビックリマーク.png exclaim

第2引数は保存形式の `kind` と同じ名前にする（`manga_layout/stickers/<名前>.png`）。
"""

import argparse
import pathlib
import sys

from PySide6.QtGui import QGuiApplication, QImage

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
STICKER_DIR = REPO_ROOT / "manga_layout" / "stickers"


def opaque_bounds(image: QImage) -> tuple[int, int, int, int] | None:
    """透明でない画素を囲む矩形 `(x, y, w, h)`。1画素も無ければ None。

    半透明も「見えている」として扱う（アルファが 0 の画素だけを余白とみなす）。
    薄い縁を落とすと輪郭がぎざつくため、閾値は設けない。
    """
    src = image.convertToFormat(QImage.Format.Format_ARGB32)
    left, top = src.width(), src.height()
    right = bottom = -1
    for y in range(src.height()):
        for x in range(src.width()):
            if (src.pixel(x, y) >> 24) & 0xFF:
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
    if right < 0:
        return None
    return left, top, right - left + 1, bottom - top + 1


def trim(image: QImage) -> QImage:
    """透明な余白を落とした画像を返す。全部が透明なら元のまま返す。"""
    box = opaque_bounds(image)
    if box is None:
        return image
    x, y, w, h = box
    return image.copy(x, y, w, h)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="取り込む PNG")
    parser.add_argument("name", help="保存形式の kind と同じ名前（例: exclaim）")
    args = parser.parse_args(argv)

    QGuiApplication([])  # QImage の読み書きに要る

    src_path = pathlib.Path(args.source)
    image = QImage(str(src_path))
    if image.isNull():
        print(f"画像として読めませんでした: {src_path}", file=sys.stderr)
        return 1
    if not image.hasAlphaChannel():
        print(f"透明を持たない画像です: {src_path}", file=sys.stderr)
        return 1

    trimmed = trim(image)
    STICKER_DIR.mkdir(parents=True, exist_ok=True)
    dest = STICKER_DIR / f"{args.name}.png"
    if not trimmed.save(str(dest), "PNG"):
        print(f"書き出せませんでした: {dest}", file=sys.stderr)
        return 1

    print(f"{src_path.name}: {image.width()}×{image.height()}")
    print(f"  → {dest.relative_to(REPO_ROOT)}: {trimmed.width()}×{trimmed.height()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
