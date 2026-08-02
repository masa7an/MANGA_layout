"""PNG 書き出し（要件定義 6.7）。

画面・サムネイルと**同じ `PageRenderer`** を通す。書き出し用に描き直すと
「画面で見た通りに出ない」が起き、しかもクリスタで開くまで気づけない。

画面と違うのは3点だけで、いずれも `PageRenderer` の引数で切り替える。

1. **画像は原寸を使う**（`FullImages`）。画面用の縮小版（長辺 1600px）を
   600dpi へ引き伸ばすと、書き出したものだけがぼやける
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..errors import ExportError
from ..images import ImageCache, Preview, full_from_bytes
from ..model import Page
from .render import PAGE_BG, PageRenderer

EXPORT_DIRNAME = "export"

MM_PER_INCH = 25.4

# 既定の dpi（要件定義 6.7）。ネームの下敷きとしてはこれで足りる
DEFAULT_DPI = 150

# 選択肢に並べる dpi。上限を 600 に留めているのは `render_page` の注記のとおり
DPI_CHOICES = (72, 150, 300, 600)
DPI_MIN = 36
DPI_MAX = 600

# 書き出し中の PNG が完成品に見えないよう、いったんこの名前で書いて置き換える
TMP_SUFFIX = ".tmp"


# -- 換算とファイル名 --------------------------------------------------------


def mm_to_px(mm: float, dpi: int) -> int:
    """mm を画素数にする。1px 未満にはしない（0 幅の画像は作れない）。"""
    return max(1, round(mm * dpi / MM_PER_INCH))


def dots_per_meter(dpi: int) -> int:
    """PNG に書き込む解像度。

    Qt が持つのは「1メートルあたりの画素数」なので、ここで直す。
    **入れておかないと、クリスタが 72dpi の画像として開く。** 画素数は
    合っていても原稿用紙に対して極端に大きく貼られ、毎回縮める羽目になる。
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


def render_page(state, page: Page, dpi: int = DEFAULT_DPI) -> QImage:
    """1ページを指定 dpi の画像にする。

    大きさの上限は `DPI_MAX`。A4 を 600dpi で描くと 4961×7016（約 3500 万
    画素・140MB）で、ここから上は確保できても待たされるだけになる。
    """
    if page.size.w <= 0 or page.size.h <= 0:
        raise ExportError(f"ページの大きさが不正です（{page.size.w} × {page.size.h} mm）")
    if not DPI_MIN <= dpi <= DPI_MAX:
        raise ExportError(f"dpi は {DPI_MIN}〜{DPI_MAX} の範囲で指定してください（指定: {dpi}）")

    width = mm_to_px(page.size.w, dpi)
    height = mm_to_px(page.size.h, dpi)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    if image.isNull():
        raise ExportError(
            f"{width} × {height} 画素の画像を確保できませんでした。dpi を下げてください"
        )

    # 用紙の白で塗る。透明のまま渡すと、クリスタで下敷きにしたときに
    # 白地のつもりの部分が抜けて、下のレイヤーが透けて見える
    image.fill(PAGE_BG)
    image.setDotsPerMeterX(dots_per_meter(dpi))
    image.setDotsPerMeterY(dots_per_meter(dpi))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    # 倍率は dpi からではなく**丸めたあとの画素数から**出す。dpi から出すと
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
    state, indexes, dest: pathlib.Path, dpi: int = DEFAULT_DPI
) -> list[pathlib.Path]:
    """指定したページを PNG にする。書いたファイルの一覧を返す。

    途中で失敗したらそこで止める。残りを飛ばして進めると、どこまでが
    今回の書き出しなのか分からないファイルの山になる。
    """
    total = state.page_count
    written: list[pathlib.Path] = []
    for i in indexes:
        image = render_page(state, state.project.pages[i], dpi)
        path = dest / page_filename(i, total)
        write_png(image, path)
        written.append(path)
    return written


# -- 書き出しの設定を選ぶ ----------------------------------------------------


class ExportDialog(QDialog):
    """書き出す範囲と dpi を選ぶ。

    書き出し先は選ばせず、決まった場所（`export/`）を**表示だけ**する。
    毎回選ばせると、書き出すたびに置き場所が散らばって「最新がどれか」を
    見失う。別の場所へ出したくなったら、書き出してから移せばよい。
    """

    def __init__(
        self,
        dest: pathlib.Path,
        page_index: int,
        page_count: int,
        dpi: int = DEFAULT_DPI,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("PNG で書き出し")

        self.current_only = QRadioButton(f"このページ（{page_index + 1} ページ目）", self)
        self.all_pages = QRadioButton(f"全ページ（{page_count} 枚）", self)
        self.current_only.setChecked(True)

        self.dpi = QComboBox(self)
        for value in DPI_CHOICES:
            self.dpi.addItem(f"{value} dpi", value)
        self.dpi.addItem("カスタム", None)

        self.custom_dpi = QSpinBox(self)
        self.custom_dpi.setRange(DPI_MIN, DPI_MAX)
        self.custom_dpi.setSuffix(" dpi")
        self.custom_dpi.setValue(dpi)

        form = QFormLayout()
        form.addRow("範囲", self.current_only)
        form.addRow("", self.all_pages)
        form.addRow("解像度", self.dpi)
        form.addRow("", self.custom_dpi)

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

        self.dpi.currentIndexChanged.connect(self._on_dpi_changed)
        self.custom_dpi.valueChanged.connect(self._update_note)
        self.dpi.setCurrentIndex(self._index_of(dpi))
        self._on_dpi_changed()

    def _index_of(self, dpi: int) -> int:
        for row in range(self.dpi.count()):
            if self.dpi.itemData(row) == dpi:
                return row
        return self.dpi.count() - 1  # カスタム

    def _on_dpi_changed(self) -> None:
        """決まった値を選んでいる間は、数値欄を触らせない。

        打ち込めるままにすると「300 dpi と書いてあるのに 300 でない」状態が
        作れてしまう（ページサイズの用紙選択と同じ理由）。
        """
        value = self.dpi.currentData()
        self.custom_dpi.setEnabled(value is None)
        if value is not None:
            self.custom_dpi.setValue(value)
        self._update_note()

    def _update_note(self) -> None:
        dpi = self.chosen_dpi()
        self.note.setText(
            f"A4（210 × 297 mm）で {mm_to_px(210.0, dpi):,} × {mm_to_px(297.0, dpi):,} 画素。"
            "同じ名前のファイルがあるときは、上書きする前に確認します。"
        )

    def chosen_dpi(self) -> int:
        value = self.dpi.currentData()
        return self.custom_dpi.value() if value is None else int(value)

    def wants_all_pages(self) -> bool:
        return self.all_pages.isChecked()
