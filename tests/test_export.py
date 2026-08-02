"""PNG 書き出し（要件定義 6.7）の検証。

ここで押さえたいのは4つ。

1. **原寸が使われること。** 画面用の縮小版のまま書き出すと、画面で確かめても
   気づけず、クリスタで開いて初めてぼやけに気づく
2. **画面の道具が出ないこと。** 用紙の縁・目安線・空のセリフの点線枠は
   作品ではない。書き出しに混ざると絵の一部として印刷される
3. **dpi が画像に書き込まれること。** 抜けているとクリスタが 72dpi として
   開き、原稿用紙に対して極端な大きさで貼られる
4. **上書きの前に必ず止まること。** 書き出しは既存ファイルを潰す操作
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog, QMessageBox

from manga_layout import ExportError, Rect, Size
from manga_layout.images import PREVIEW_MAX_PX
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.export import (
    DEFAULT_DPI,
    DEFAULT_SCALE,
    DPI_MAX,
    EXPORT_DIRNAME,
    SCALE_CHOICES,
    ExportDialog,
    FullImages,
    dots_per_meter,
    page_px,
    scale_label,
    existing_paths,
    export_dir_of,
    export_pages,
    missing_assets_in,
    mm_to_px,
    page_filename,
    planned_paths,
    render_page,
    write_png,
)
from manga_layout.ui.render import PAGE_BG, PANEL_FILL, PageRenderer

A4_AT_150 = (1240, 1754)
A4_AT_72 = (595, 842)

# 下のコマ（10,10〜100,70 mm）の内側、枠線から十分離れた点を 72dpi の画素で
PANEL_INSIDE_AT_72 = (156, 113)


@pytest.fixture
def saved_state(qapp, tmp_path):
    """保存済みの作品。書き出し先が決まっている状態。"""
    state = EditorState()
    state.save(tmp_path / "作品")
    return state


@pytest.fixture
def with_panel(saved_state):
    """コマを1つ置いた作品。`PANEL_INSIDE_AT_72` がその内側を指す。"""
    with saved_state.edit("コマ") as project:
        project.add_panel(project.pages[0], Rect(10.0, 10.0, 90.0, 60.0))
    return saved_state


@pytest.fixture
def large_png(fixture_dir) -> bytes:
    """縮小版に落とされる大きさの画像（2000 × 1500）。"""
    return (fixture_dir / "large_2000x1500.png").read_bytes()


def blank_like(image: QImage) -> QImage:
    """同じ大きさの、用紙の白だけの1枚。"""
    empty = QImage(image.size(), image.format())
    empty.fill(PAGE_BG)
    return empty


class Test換算:
    """mm と画素の行き来。ここがずれると出力の寸法が全部ずれる。"""

    def test_A4を150dpiにすると(self):
        assert (mm_to_px(210.0, 150), mm_to_px(297.0, 150)) == A4_AT_150

    def test_dpiに比例する(self):
        assert mm_to_px(210.0, 300) == pytest.approx(mm_to_px(210.0, 150) * 2, abs=1)

    def test_極端に小さくても0にはならない(self):
        # 0 幅の QImage は作れない。丸めて 0 になる経路を塞いでおく
        assert mm_to_px(0.01, 36) == 1

    def test_1メートルあたりの画素数に直す(self):
        # 150dpi ＝ 1インチ 150 画素 ＝ 1メートル 5905.5 画素
        assert dots_per_meter(150) == 5906


class Test画像サイズ:
    """100% / 75% / 50%。**画素数がそのまま減る。**

    この道具はウェブで読む絵の下敷きを作るもので、印刷しない（要件定義
    1章）。紙の上で何 mm になるかを保つ細工はしない。
    """

    def test_3つから選ぶ(self):
        assert SCALE_CHOICES == (1.0, 0.75, 0.5)
        assert DEFAULT_SCALE == 1.0

    def test_表示は百分率(self):
        assert [scale_label(s) for s in SCALE_CHOICES] == ["100%", "75%", "50%"]

    def test_画素数が倍率どおりに減る(self, saved_state):
        page = saved_state.page
        full = render_page(saved_state, page, 150, 1.0)
        three_quarters = render_page(saved_state, page, 150, 0.75)
        half = render_page(saved_state, page, 150, 0.5)

        assert (full.width(), full.height()) == A4_AT_150
        assert (three_quarters.width(), three_quarters.height()) == (930, 1315)
        assert (half.width(), half.height()) == (620, 877)

    def test_縦横比は保つ(self, saved_state):
        page = saved_state.page
        full = render_page(saved_state, page, 150, 1.0)
        half = render_page(saved_state, page, 150, 0.5)

        assert half.width() / half.height() == pytest.approx(
            full.width() / full.height(), abs=0.002
        )

    def test_dpiは選んだ値のまま書き込む(self, saved_state):
        """倍率のぶんを差し引かない。

        印刷しないので、この値は覚え書きでしかない。「紙の大きさが同じに
        見える」ように細工すると、選んだ dpi と書き込まれた値が食い違う。
        """
        half = render_page(saved_state, saved_state.page, 150, 0.5)
        assert half.dotsPerMeterX() == dots_per_meter(150)

    def test_倍率だけの違いは画素数に出る(self, saved_state):
        """dpi を半分にするのと 50% にするのは、画素数としては同じ。"""
        by_scale = render_page(saved_state, saved_state.page, 150, 0.5)
        by_dpi = render_page(saved_state, saved_state.page, 75, 1.0)
        assert (by_scale.width(), by_scale.height()) == (by_dpi.width(), by_dpi.height())

    def test_画素数を直に引ける(self):
        assert page_px(Size(210.0, 297.0), 150) == A4_AT_150
        assert page_px(Size(210.0, 297.0), 150, 0.5) == (620, 877)

    def test_小さいdpiとの組み合わせでも作れる(self, saved_state):
        """36dpi の 50% は 149×210 画素。小さいが、作れないわけではない。"""
        image = render_page(saved_state, saved_state.page, 36, 0.5)
        assert (image.width(), image.height()) == (149, 210)

    def test_倍率を省くと原寸(self, saved_state):
        image = render_page(saved_state, saved_state.page, 150)
        assert (image.width(), image.height()) == A4_AT_150


class Testファイル名:
    """フォルダの並び順が読み順と一致すること。"""

    def test_1始まりの2桁(self):
        assert page_filename(0, 9) == "p01.png"
        assert page_filename(8, 9) == "p09.png"

    def test_100ページ以上は3桁に揃える(self):
        # 2桁のままだと p100 が p99 より前に並ぶ
        assert page_filename(0, 120) == "p001.png"
        assert page_filename(99, 120) == "p100.png"

    def test_同じ総数なら桁が揃う(self):
        names = [page_filename(i, 12) for i in range(12)]
        assert len({len(n) for n in names}) == 1
        assert names == sorted(names)


class Test書き出し先:
    """作品フォルダの中の export/。"""

    def test_作品フォルダの中に作る(self, saved_state, tmp_path):
        assert export_dir_of(saved_state) == tmp_path / "作品" / EXPORT_DIRNAME

    def test_保存前は断る(self, qapp):
        state = EditorState()
        with pytest.raises(ExportError, match="先に作品を保存"):
            export_dir_of(state)


class Test描画:
    """画面と同じ経路を通しつつ、書き出しに要らないものを外す。"""

    def test_指定dpiの大きさになる(self, saved_state):
        image = render_page(saved_state, saved_state.page, 150)
        assert (image.width(), image.height()) == A4_AT_150

    def test_dpiが画像に書き込まれる(self, saved_state):
        image = render_page(saved_state, saved_state.page, 300)
        assert image.dotsPerMeterX() == dots_per_meter(300)
        assert image.dotsPerMeterY() == dots_per_meter(300)

    def test_範囲外のdpiは断る(self, saved_state):
        with pytest.raises(ExportError, match="dpi は"):
            render_page(saved_state, saved_state.page, DPI_MAX + 1)

    def test_用紙の縁も目安線も描かない(self, saved_state):
        """何も置いていないページは、真っ白な1枚になる。

        用紙の縁（灰色の輪郭）が残ると、四辺に線の入った下敷きができる。
        """
        image = render_page(saved_state, saved_state.page, 72)
        assert image == blank_like(image)

    def test_空のセリフの点線枠を描かない(self, saved_state):
        saved_state.add_text(Rect(20.0, 20.0, 40.0, 20.0), "")
        image = render_page(saved_state, saved_state.page, 72)
        assert image == blank_like(image)

    def test_画面には点線枠が出る(self, saved_state):
        """上の2つが「そもそも何も描いていない」で通っていないことの裏取り。"""
        saved_state.add_text(Rect(20.0, 20.0, 40.0, 20.0), "")
        image = QImage(*A4_AT_150, QImage.Format.Format_ARGB32)
        image.fill(PAGE_BG)
        painter = _painter(image, saved_state)
        PageRenderer(saved_state).draw(painter, saved_state.page)
        painter.end()
        assert image != blank_like(image)

    def test_コマの枠線は描かれる(self, with_panel):
        image = render_page(with_panel, with_panel.page, 72)
        assert image != blank_like(image)

    def test_コマの下地を塗らない(self, with_panel):
        """コマの中は用紙の白のまま。範囲は枠線が示す。

        画面の薄い灰色は「どこがコマか」を見分けるための色で、紙の上では
        コマの中は白。下敷きに敷いたときに灰色が乗ると、絵と紙の白の
        境目が分からなくなる。
        """
        image = render_page(with_panel, with_panel.page, 72)
        assert image.pixelColor(*PANEL_INSIDE_AT_72) == PAGE_BG

    def test_画面では下地を塗る(self, with_panel):
        image = QImage(*A4_AT_72, QImage.Format.Format_ARGB32)
        image.fill(PAGE_BG)
        painter = _painter(image, with_panel)
        PageRenderer(with_panel).draw(painter, with_panel.page)
        painter.end()
        assert image.pixelColor(*PANEL_INSIDE_AT_72) == PANEL_FILL


def _painter(image: QImage, state):
    from PySide6.QtGui import QPainter

    painter = QPainter(image)
    painter.scale(image.width() / state.page.size.w, image.height() / state.page.size.h)
    return painter


class Test原寸:
    """書き出しだけは縮小版を使わない（要件定義 6.3）。"""

    def test_書き出しは原寸を返す(self, saved_state, large_png):
        ref, px = saved_state.import_bytes(large_png)
        assert px == (2000, 1500)

        full = FullImages(saved_state)(ref)
        assert full is not None
        assert not full.is_reduced
        assert (full.image.width(), full.image.height()) == (2000, 1500)

    def test_画面は縮小版を使う(self, saved_state, large_png):
        ref, _ = saved_state.import_bytes(large_png)
        preview = saved_state.preview(ref)
        assert preview.is_reduced
        assert max(preview.image.width(), preview.image.height()) == PREVIEW_MAX_PX

    def test_画面用の入れ物とは別に持つ(self, saved_state, large_png):
        """混ざると、書き出しに縮小版が紛れても見た目では気づけない。"""
        ref, _ = saved_state.import_bytes(large_png)
        saved_state.preview(ref)  # 画面側を先に温めておく

        full = FullImages(saved_state)(ref)
        assert not full.is_reduced
        assert saved_state.preview(ref).is_reduced

    def test_欠けた画像を数える(self, saved_state):
        with saved_state.edit("画像") as project:
            page = project.pages[0]
            panel = project.add_panel(page, Rect(10, 10, 90, 60))
            project.add_image(panel, "sha1:missing", Rect(12, 12, 80, 55), (100, 100))
        assert missing_assets_in(saved_state, [0]) == 1


class Test書き出しの実行:
    """ファイルが実際にできること。"""

    def test_ページ数ぶんできる(self, saved_state, tmp_path):
        saved_state.add_page()
        dest = export_dir_of(saved_state)

        written = export_pages(saved_state, [0, 1], dest, 72)

        assert [p.name for p in written] == ["p01.png", "p02.png"]
        assert all(p.is_file() for p in written)

    def test_フォルダが無ければ作る(self, saved_state):
        dest = export_dir_of(saved_state)
        assert not dest.exists()
        export_pages(saved_state, [0], dest, 72)
        assert dest.is_dir()

    def test_一時ファイルを残さない(self, saved_state):
        dest = export_dir_of(saved_state)
        export_pages(saved_state, [0], dest, 72)
        assert [p.name for p in dest.iterdir()] == ["p01.png"]

    def test_書き出したPNGを読み直せる(self, saved_state):
        dest = export_dir_of(saved_state)
        path = export_pages(saved_state, [0], dest, 150)[0]

        loaded = QImage(str(path))
        assert (loaded.width(), loaded.height()) == A4_AT_150
        assert loaded.dotsPerMeterX() == dots_per_meter(150)

    def test_上書きしても壊れない(self, saved_state):
        dest = export_dir_of(saved_state)
        path = export_pages(saved_state, [0], dest, 72)[0]
        before = path.stat().st_size

        export_pages(saved_state, [0], dest, 150)

        assert path.stat().st_size != before
        assert not QImage(str(path)).isNull()

    def test_書けない場所は例外にする(self, saved_state, tmp_path):
        # 同じ名前のフォルダがあると PNG を置けない
        blocked = tmp_path / "ふさがっている"
        (blocked / "p01.png").mkdir(parents=True)
        with pytest.raises(ExportError):
            write_png(render_page(saved_state, saved_state.page, 72), blocked / "p01.png")


class Test上書きの検出:
    """すでにあるファイルを見つけること。"""

    def test_無ければ空(self, saved_state):
        dest = export_dir_of(saved_state)
        assert existing_paths(planned_paths(dest, [0], 1)) == []

    def test_あれば挙げる(self, saved_state):
        saved_state.add_page()
        dest = export_dir_of(saved_state)
        export_pages(saved_state, [0], dest, 72)

        found = existing_paths(planned_paths(dest, [0, 1], 2))

        assert [p.name for p in found] == ["p01.png"]


class Test画面からの書き出し:
    """メニューから押したときの流れ。断る場所が3つある。"""

    @pytest.fixture
    def window(self, qapp, tmp_path):
        win = MainWindow(EditorState())
        win.state.save(tmp_path / "作品")
        yield win
        win.state.history.mark_saved()
        win.close()

    def test_既定のdpiで始まる(self, window):
        assert window._export_dpi == DEFAULT_DPI

    def test_このページだけ書き出す(self, window, monkeypatch):
        window.add_page()
        _accept_dialog(monkeypatch, all_pages=False)

        assert window.export_png()

        dest = export_dir_of(window.state)
        assert [p.name for p in dest.iterdir()] == ["p02.png"]

    def test_全ページ書き出す(self, window, monkeypatch):
        window.add_page()
        _accept_dialog(monkeypatch, all_pages=True)

        assert window.export_png()

        dest = export_dir_of(window.state)
        assert sorted(p.name for p in dest.iterdir()) == ["p01.png", "p02.png"]

    def test_取り消せば何も書かない(self, window, monkeypatch):
        monkeypatch.setattr(
            "manga_layout.ui.window.ExportDialog.exec",
            lambda self: QDialog.DialogCode.Rejected,
        )

        assert not window.export_png()
        assert not export_dir_of(window.state).exists()

    def test_保存前は保存を促す(self, qapp, monkeypatch):
        window = MainWindow(EditorState())
        asked: list[str] = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: (asked.append(a[1]), QMessageBox.StandardButton.Cancel)[1],
        )

        assert not window.export_png()
        assert asked == ["先に保存が必要です"]
        window.state.history.mark_saved()
        window.close()

    def test_上書きを断れば書き換えない(self, window, monkeypatch):
        _accept_dialog(monkeypatch, all_pages=False)
        window.export_png()
        path = export_dir_of(window.state) / "p01.png"
        before = path.read_bytes()

        # 2回目は dpi を上げるが、上書きの確認で断る
        window._export_dpi = 300
        monkeypatch.setattr(
            QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
        )

        assert not window.export_png()
        assert path.read_bytes() == before

    def test_上書きを承知すれば書き換える(self, window, monkeypatch):
        _accept_dialog(monkeypatch, all_pages=False, dpi=72)
        window.export_png()
        path = export_dir_of(window.state) / "p01.png"
        before = path.read_bytes()

        _accept_dialog(monkeypatch, all_pages=False, dpi=150)
        monkeypatch.setattr(
            QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Ok
        )

        assert window.export_png()
        assert path.read_bytes() != before

    def test_欠けた画像があれば止めて聞く(self, window, monkeypatch):
        with window.state.edit("画像") as project:
            page = project.pages[0]
            panel = project.add_panel(page, Rect(10, 10, 90, 60))
            project.add_image(panel, "sha1:missing", Rect(12, 12, 80, 55), (100, 100))
        _accept_dialog(monkeypatch, all_pages=False)
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel
        )

        assert not window.export_png()
        assert not export_dir_of(window.state).exists()


def _accept_dialog(monkeypatch, *, all_pages: bool, dpi: int = 72) -> None:
    """設定の窓を出さずに、選んだことにして進める。"""
    monkeypatch.setattr(
        "manga_layout.ui.window.ExportDialog.exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        "manga_layout.ui.window.ExportDialog.wants_all_pages", lambda self: all_pages
    )
    monkeypatch.setattr(
        "manga_layout.ui.window.ExportDialog.chosen_dpi", lambda self: dpi
    )
