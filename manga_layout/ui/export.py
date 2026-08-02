"""PNG 書き出し（要件定義 6.7）。

画面・サムネイルと**同じ `PageRenderer`** を通す。書き出し用に描き直すと
「画面で見た通りに出ない」が起き、しかもクリスタで開くまで気づけない。

画面と違うのは3点だけで、いずれも `PageRenderer` の引数で切り替える。

1. **画像は原寸を使う**（`FullImages`）。画面用の縮小版（長辺 1600px）を
   引き伸ばすと、書き出したものだけがぼやける
2. **用紙の縁・目安線・影を描かない**。置き場所の目印であって作品ではない。
   特に縁の線は、用紙そのものが画像の範囲であるぶん四辺に残ってしまう
3. **画面でだけ要る補助表示を描かない**（`aids=False`）。コマの下地（薄い
   灰色）、空のセリフの点線枠、欠けた画像の×印がこれに当たる。コマの範囲は
   枠線が示すし、欠けた画像は書き出す前の警告で知らせる

## 書き出し先

作品フォルダの中の `export/`。作品と一緒に移動・複製でき、クリスタから
開くときも作品フォルダを辿るだけで済む。アプリ側の決まった場所に集めると、
作品を別の PC へ持っていったときに書き出しだけが置き去りになる。

保存前の作品には置き場所が無いので、そのときは書き出しを断る（画像の
貼り付けと違って「あとで書き出す」が成り立たないため、預かる意味がない）。
"""

from __future__ import annotations

import os
import pathlib

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..errors import ExportError
from ..images import ImageCache, Preview, full_from_bytes
from ..geometry import Size
from ..model import DEFAULT_PAGE_SIZE, Page
from .render import PAGE_BG, PageRenderer

EXPORT_DIRNAME = "export"

MM_PER_INCH = 25.4

# 書き出す画像サイズの倍率。100% はページの px 寸法そのまま。
# 座標系が px になったので、dpi の指定は要らなくなった（要件定義 3章・6.7）
SCALE_CHOICES = (1.0, 0.75, 0.5)
DEFAULT_SCALE = 1.0

# 書き出した画像の大きさの目安を出すときに使う dpi。
# **書き出す画素数には一切影響しない、参考表示だけの値。**
# ページの px 寸法は 150dpi 換算の紙寸法を出発点にしている（`PAGE_SIZES`）
REFERENCE_DPI = 150

# 1辺に許す画素数の上限。A4 相当を 600dpi で描いた 7016px を超える大きさは、
# 確保できても待たされるだけになる
MAX_SIDE_PX = 8000

# 書き出し中の PNG が完成品に見えないよう、いったんこの名前で書いて置き換える
TMP_SUFFIX = ".tmp"


# -- 画素数とファイル名 ------------------------------------------------------


def page_px(size: Size, scale: float = DEFAULT_SCALE) -> tuple[int, int]:
    """書き出される画像の画素数。

    **倍率は画素数をそのまま減らす。** この道具の出力はウェブで読む絵の
    下敷きで、印刷しない（要件定義 1章）。75% と言われたら素直に画素数を
    75% にする。1px 未満にはしない（0 幅の画像は作れない）。
    """
    return (max(1, round(size.w * scale)), max(1, round(size.h * scale)))


def scale_label(scale: float) -> str:
    return f"{round(scale * 100)}%"


def paper_hint(size: Size) -> str:
    """紙に置き換えたときのおおよその大きさ。**参考表示だけ。**

    印刷しないので出力には関わらないが、「1240px と言われてもピンと
    こない」ときの手掛かりにはなる（要件定義 6.7）。
    """
    w = size.w / REFERENCE_DPI * MM_PER_INCH
    h = size.h / REFERENCE_DPI * MM_PER_INCH
    return f"紙なら約 {w:.0f} × {h:.0f} mm（{REFERENCE_DPI}dpi 換算）"


def dots_per_meter(dpi: float) -> int:
    """PNG に書き込む解像度。Qt は「1メートルあたりの画素数」で持つ。

    入れておかないと**クリスタが 72dpi の画像として開く**。印刷しない以上
    この値は覚え書きでしかないが、入れないと原稿用紙に対して極端な大きさで
    貼られるので、`REFERENCE_DPI` を書いておく。
    """
    return round(dpi / (MM_PER_INCH / 1000.0))


def page_filename(index: int, total: int) -> str:
    """0 始まりのページ番号からファイル名を作る。

    桁数は総ページ数に合わせて揃える。揃えないと、100 ページ以上の作品で
    `p100` が `p99` より前に並び、フォルダの並び順が読み順と食い違う。
    """
    width = max(2, len(str(max(total, 1))))
    return f"p{index + 1:0{width}d}.png"


def export_dir(project_dir: pathlib.Path | str) -> pathlib.Path:
    return pathlib.Path(project_dir) / EXPORT_DIRNAME


def export_dir_of(state) -> pathlib.Path:
    """開いている作品の書き出し先。保存前なら例外。"""
    if state.project_dir is None:
        raise ExportError(
            "書き出すには先に作品を保存してください。"
            f"書き出し先は作品フォルダの中の {EXPORT_DIRNAME}/ になります"
        )
    return export_dir(state.project_dir)


def planned_paths(dest: pathlib.Path, indexes, total: int) -> list[pathlib.Path]:
    return [dest / page_filename(i, total) for i in indexes]


def existing_paths(paths) -> list[pathlib.Path]:
    """これから書く先のうち、すでにあるもの。上書きの確認に使う。"""
    return [p for p in paths if p.exists()]


def missing_assets_in(state, indexes) -> int:
    """書き出す範囲にある「実体が見つからない画像」の数。

    書き出しでは目印を描かない（`PageRenderer(marks=False)`）ので、
    黙って穴が空く。数だけ先に数えて、書き出す前に知らせる。
    """
    count = 0
    for i in indexes:
        page = state.project.pages[i]
        for panel in page.panels:
            for image in panel.children:
                if state.preview(image.asset) is None:
                    count += 1
    return count


# -- 描画 --------------------------------------------------------------------


class FullImages:
    """書き出しのあいだだけ原寸を持つ画像置き場。

    **1ページ書き出すごとに作り直して捨てる。** 全ページぶんを持ち続けると、
    2048×2048 が何十枚も同時にメモリへ載る。同じ画像を複数ページで使って
    いれば展開し直しになるが、そちらの方がまだ安い。

    `state.image_cache`（画面用）とは別物。混ぜると、書き出しに縮小版が
    紛れ込んでも見た目では気づけない。
    """

    def __init__(self, state) -> None:
        self.state = state
        self._cache = ImageCache(full_from_bytes)

    def __call__(self, ref: str) -> Preview | None:
        return self._cache.get(ref, lambda: self.state.read_asset(ref))


def render_page(state, page: Page, scale: float = DEFAULT_SCALE) -> QImage:
    """1ページを画像にする。100% ならページの px 寸法そのまま。

    1辺の上限は `MAX_SIDE_PX`。ここから上は確保できても待たされるだけになる。
    """
    if page.size.w <= 0 or page.size.h <= 0:
        raise ExportError(f"ページの大きさが不正です（{page.size.w} × {page.size.h} px）")

    width, height = page_px(page.size, scale)
    if max(width, height) > MAX_SIDE_PX:
        raise ExportError(
            f"{width} × {height} 画素は大きすぎます（1辺の上限 {MAX_SIDE_PX:,}px）。"
            "ページを小さくするか、画像サイズを下げてください"
        )

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    if image.isNull():
        raise ExportError(
            f"{width} × {height} 画素の画像を確保できませんでした。"
            "画像サイズを下げてください"
        )

    # 用紙の白で塗る。透明のまま渡すと、クリスタで下敷きにしたときに
    # 白地のつもりの部分が抜けて、下のレイヤーが透けて見える
    image.fill(PAGE_BG)
    image.setDotsPerMeterX(dots_per_meter(REFERENCE_DPI))
    image.setDotsPerMeterY(dots_per_meter(REFERENCE_DPI))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    # 倍率は指定値ではなく**丸めたあとの画素数から**出す。指定値から出すと
    # 丸めのぶんだけ右端・下端に描き残しの筋が出る
    painter.scale(width / page.size.w, height / page.size.h)
    PageRenderer(state, FullImages(state), aids=False).draw(
        painter, page, guides=False, shadow=False, edge=False
    )
    painter.end()
    return image


def write_png(image: QImage, path: pathlib.Path) -> None:
    """PNG を1枚書く。**別名で書き切ってから置き換える。**

    上書きの途中で落ちても、前回の書き出しが壊れた状態で残らない。
    保存（`storage.atomic_write_text`）と同じ考え方。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    if not image.save(str(tmp), "PNG"):
        tmp.unlink(missing_ok=True)
        raise ExportError(f"書き出せませんでした: {path}")
    try:
        os.replace(tmp, path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise ExportError(
            f"{path.name} を置き換えられませんでした（{e}）。"
            "他のアプリで開いたままになっていないか確かめてください"
        ) from e


def export_pages(
    state, indexes, dest: pathlib.Path, scale: float = DEFAULT_SCALE
) -> list[pathlib.Path]:
    """指定したページを PNG にする。書いたファイルの一覧を返す。

    途中で失敗したらそこで止める。残りを飛ばして進めると、どこまでが
    今回の書き出しなのか分からないファイルの山になる。
    """
    total = state.page_count
    written: list[pathlib.Path] = []
    for i in indexes:
        image = render_page(state, state.project.pages[i], scale)
        path = dest / page_filename(i, total)
        write_png(image, path)
        written.append(path)
    return written


# -- 書き出しの設定を選ぶ ----------------------------------------------------


class ExportDialog(QDialog):
    """書き出す範囲・解像度・大きさを選ぶ。

    書き出し先は選ばせず、決まった場所（`export/`）を**表示だけ**する。
    毎回選ばせると、書き出すたびに置き場所が散らばって「最新がどれか」を
    見失う。別の場所へ出したくなったら、書き出してから移せばよい。

    画像サイズは 100% / 75% / 50% の3つだけ。座標系が px なので、
    ページの寸法そのものが「原寸」になる（要件定義 3章）。dpi の指定は
    要らなくなった。

    紙に置き換えた大きさは**参考として1行出すだけ**。印刷しないので出力には
    関わらないが、「1240px と言われてもピンとこない」ときの手掛かりになる。
    """

    def __init__(
        self,
        dest: pathlib.Path,
        page_index: int,
        page_count: int,
        parent: QWidget | None = None,
        page_size: Size | None = None,
        scale: float = DEFAULT_SCALE,
    ):
        super().__init__(parent)
        self.setWindowTitle("PNG で書き出し")
        self.page_size = page_size or DEFAULT_PAGE_SIZE

        self.current_only = QRadioButton(f"このページ（{page_index + 1} ページ目）", self)
        self.all_pages = QRadioButton(f"全ページ（{page_count} 枚）", self)
        self.current_only.setChecked(True)

        self.scale = QComboBox(self)
        for value in SCALE_CHOICES:
            label = scale_label(value)
            self.scale.addItem("100%（原寸）" if value == 1.0 else label, value)
        self.scale.setCurrentIndex(self._scale_index(scale))

        form = QFormLayout()
        form.addRow("範囲", self.current_only)
        form.addRow("", self.all_pages)
        form.addRow("画像サイズ", self.scale)

        self.note = QLabel(self)
        self.note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel(f"書き出し先: {dest}", self))
        layout.addWidget(self.note)
        layout.addWidget(buttons)

        self.scale.currentIndexChanged.connect(self._update_note)
        self._update_note()

    def _scale_index(self, scale: float) -> int:
        for row in range(self.scale.count()):
            if self.scale.itemData(row) == scale:
                return row
        return 0  # 覚えていた値が選択肢から消えていたら原寸に戻す

    def _update_note(self) -> None:
        """書き出される画素数を出す。

        用紙の大きさは表示中のページの実物を使う（A4 相当の決め打ちにすると、
        B5 相当やカスタムの作品で数字が合わない）。
        """
        scale = self.chosen_scale()
        width, height = page_px(self.page_size, scale)
        text = f"書き出される画像: {width:,} × {height:,} 画素"
        if scale != 1.0:
            full_w, full_h = page_px(self.page_size)
            text += f"（原寸なら {full_w:,} × {full_h:,}）"
        self.note.setText(text + "\n" + paper_hint(Size(width, height)))

    def chosen_scale(self) -> float:
        return float(self.scale.currentData())

    def wants_all_pages(self) -> bool:
        return self.all_pages.isChecked()
