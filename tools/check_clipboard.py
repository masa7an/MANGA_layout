"""クリップボードの画像が透明度（アルファチャンネル）を保つか調べる。

要件定義 9章「Day 1 に潰すべき最大のリスク」の検証用。
ワークフロー全体が「クリスタから Ctrl+V」に乗っているため、
ここで透明度が落ちるかどうかを最初に確定させる。

使い方
------
1. 測定器そのものが正しいかを先に確認する（クリスタ不要）:

       ./venv/Scripts/python.exe tools/check_clipboard.py --self-test

   既知の画像を自分でクリップボードへ置き、読み戻して一致するかを見る。
   ここで落ちるならスクリプト側の問題。

2. クリスタで画像を選択して Ctrl+C したあと、引数なしで実行:

       ./venv/Scripts/python.exe tools/check_clipboard.py

   クリップボードが提供する全形式を列挙し、画像として取り出せたものを
   すべて data/clipboard_check/ に保存する。

判定の読み方
------------
`alpha=有` でも `半透明の画素` が 0 なら、**透明度は失われている**。
アルファチャンネルの器だけ残り中身が全部不透明に潰された状態で、
これが最も間違えやすい失敗の形。
"""

import argparse
import pathlib
import sys

# クリップボードは Windows のプラットフォーム連携が要るため、
# QT_QPA_PLATFORM=offscreen では動かない。ここでは指定しない。
from PySide6.QtCore import QByteArray
from PySide6.QtGui import QGuiApplication, QImage

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "rgba_transparent.png"
DEFAULT_OUT = REPO_ROOT / "data" / "clipboard_check"

# 画像として取り出せる可能性のある形式。上ほど優先度が高い
IMAGE_MIMES = ("image/png", "PNG", "image/tiff", "image/bmp", "application/x-qt-image")


def alpha_stats(img: QImage) -> dict:
    """画素を走査して透明度の実態を調べる。

    hasAlphaChannel() は器の有無しか教えてくれないので、
    実際に半透明な画素があるかを数える。
    """
    conv = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    data = bytes(conv.constBits())

    min_a, translucent, transparent = 255, 0, 0
    for y in range(h):
        alphas = data[y * bpl : y * bpl + w * 4][3::4]
        if not alphas:
            continue
        row_min = min(alphas)
        if row_min < min_a:
            min_a = row_min
        translucent += sum(1 for a in alphas if a < 255)
        transparent += sum(1 for a in alphas if a == 0)

    return {
        "min_alpha": min_a,
        "translucent": translucent,
        "transparent": transparent,
        "total": w * h,
    }


def report(label: str, img: QImage, out_dir: pathlib.Path | None = None) -> dict | None:
    """1枚ぶんの解析結果を表示し、必要なら保存する。"""
    print(f"\n[{label}]")
    if img.isNull():
        print("  画像として読み取れませんでした")
        return None

    st = alpha_stats(img)
    has = img.hasAlphaChannel()
    print(f"  寸法          : {img.width()} x {img.height()}")
    print(f"  Qt の形式     : {img.format().name}")
    print(f"  alpha の器    : {'有' if has else '無'}")
    print(f"  最小 alpha    : {st['min_alpha']}  (255 = どこも透けていない)")
    print(f"  半透明の画素  : {st['translucent']:,} / {st['total']:,}")
    print(f"  完全透明の画素: {st['transparent']:,} / {st['total']:,}")

    if not has:
        print("  → 判定: 透明度なし。アルファチャンネルごと失われている")
    elif st["translucent"] == 0:
        print("  → 判定: 器はあるが中身が全て不透明。実質的に透明度は失われている")
    else:
        print("  → 判定: 透明度あり。保たれている")

    if out_dir is not None:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in label)
        path = out_dir / f"{safe}.png"
        img.save(str(path), "PNG")
        print(f"  保存          : {path.relative_to(REPO_ROOT)}")

    return st


def dump_clipboard(out_dir: pathlib.Path) -> None:
    """クリップボードの中身を形式ごとに取り出して調べる。"""
    clip = QGuiApplication.clipboard()
    mime = clip.mimeData()

    if mime is None:
        print("クリップボードが読めません")
        return

    formats = list(mime.formats())
    print("クリップボードが提供する形式:")
    if not formats:
        print("  (空)")
        return
    for f in formats:
        size = mime.data(f).size()
        print(f"  - {f}  ({size:,} bytes)")

    if not mime.hasImage():
        print("\n画像は含まれていません。クリスタ側でコピーし直してください。")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    # 経路1: Qt に任せた取り出し（アプリが実際に使う経路）
    img = clip.image()
    report("1_qt_image", img, out_dir)

    # 経路2: 生の形式データを個別に解釈する（経路1で落ちた場合の代替案）
    for fmt in IMAGE_MIMES:
        if fmt not in formats:
            continue
        raw: QByteArray = mime.data(fmt)
        decoded = QImage()
        if not decoded.loadFromData(raw):
            print(f"\n[2_{fmt}] 解釈できませんでした ({raw.size():,} bytes)")
            continue
        report(f"2_{fmt}", decoded, out_dir)

    print(f"\n保存先: {out_dir.relative_to(REPO_ROOT)}")
    print("画像を開いて、透明部分が黒や白で埋まっていないか目視でも確認してください。")


def self_test() -> int:
    """既知の画像を置いて読み戻し、測定コードが正しいかを確かめる。

    注意: Qt が自前で保持したデータを返す可能性があり、Windows の
    クリップボードを完全に往復したことの証明にはならない。
    あくまで「解析コードにバグが無いこと」の確認に留まる。
    """
    if not FIXTURE.exists():
        print(f"基準画像が見つかりません: {FIXTURE}")
        return 1

    src = QImage(str(FIXTURE))
    print("基準画像を解析します（クリップボードを経由しない）")
    before = report("基準画像", src)

    clip = QGuiApplication.clipboard()
    clip.setImage(src)
    print("\nクリップボードへ置いて読み戻します")
    after = report("読み戻し", clip.image())

    if after is None or before is None:
        print("\n結果: 失敗。読み戻せませんでした")
        return 1

    ok = (
        before["min_alpha"] == after["min_alpha"]
        and before["translucent"] == after["translucent"]
        and before["transparent"] == after["transparent"]
    )
    print("\n" + "=" * 50)
    if ok:
        print("結果: 成功。測定コードは正しく透明度を検出できています")
        print("      クリスタからのコピーを、この物差しで測れます")
    else:
        print("結果: 失敗。読み戻しで値が変わりました")
        print(f"      置く前: {before}")
        print(f"      読み戻し: {after}")
    print("=" * 50)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="既知の画像で測定コード自体を検証する（クリスタ不要）",
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=DEFAULT_OUT,
        help=f"取り出した画像の保存先（既定: {DEFAULT_OUT.relative_to(REPO_ROOT)}）",
    )
    args = parser.parse_args()

    QGuiApplication([])

    if args.self_test:
        return self_test()

    dump_clipboard(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
