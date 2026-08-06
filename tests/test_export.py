"""PNG / JPG 書き出し（要件定義 6.7）の検証。

ここで押さえたいのは5つ。

1. **原寸が使われること。** 画面用の縮小版のまま書き出すと、画面で確かめても
   気づけず、クリスタで開いて初めてぼやけに気づく
2. **画面の道具が出ないこと。** 用紙の縁・目安線・空のセリフの点線枠は
   作品ではない。書き出しに混ざると絵の一部として印刷される
3. **画像サイズが指定どおりになること。** 座標系が px なので、100% は
   ページの寸法そのもの
4. **上書きの前に必ず止まること。** 書き出しは既存ファイルを潰す操作
5. **JPG も選べ、品質は設定から取ること。** ダイアログには出さない
   （`AppSettings.jpg_quality`、既定90）
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog, QMessageBox

from manga_layout import ExportError, ImageObject, Rect, Size
from manga_layout.images import PREVIEW_MAX_PX
from manga_layout.settings import AppSettings, save_settings
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.export import (
    DEFAULT_FORMAT,
    DEFAULT_SCALE,
    EXPORT_DIRNAME,
    EXPORT_FORMATS,
    HIGH_DPI,
    HIGH_SCALE,
    MAX_SIDE_PX,
    REFERENCE_DPI,
    SCALE_CHOICES,
    FullImages,
    dots_per_meter,
    existing_paths,
    export_dir_of,
    export_dpi,
    export_pages,
    missing_assets_in,
    page_filename,
    page_px,
    paper_hint,
    planned_paths,
    render_page,
    scale_label,
    write_image,
)
from manga_layout.ui.render import PAGE_BG, PANEL_FILL, PageRenderer

# 既定のページ（A4 相当）の画素数。座標系が px なので、これがそのまま原寸
A4_PX = (1240, 1754)

# 下のコマ（60,60〜600,420 px）の内側で、枠線から十分に離れた点
PANEL_INSIDE = (330, 240)


@pytest.fixture
def saved_state(qapp, tmp_path):
    """保存済みの作品。書き出し先が決まっている状態。"""
    state = EditorState()
    state.save(tmp_path / "作品")
    return state


@pytest.fixture
def with_panel(saved_state):
    """コマを1つ置いた作品。`PANEL_INSIDE` がその内側を指す。"""
    with saved_state.edit("コマ") as project:
        project.add_panel(project.pages[0], Rect(60.0, 60.0, 540.0, 360.0))
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


class Test参考表示:
    """紙の大きさは**出力に関わらない参考値**（要件定義 6.7）。"""

    def test_150dpi換算で紙の大きさを出す(self):
        hint = paper_hint(Size(*A4_PX))
        assert "210" in hint and "297" in hint
        assert "150dpi" in hint

    def test_1メートルあたりの画素数に直す(self):
        # 150dpi ＝ 1インチ 150 画素 ＝ 1メートル 5905.5 画素
        assert dots_per_meter(REFERENCE_DPI) == 5906

    def test_拡大したぶんは紙の大きさに出さない(self):
        """117% は「同じ紙をきめ細かく」なので、mm は A4 のまま。"""
        width, height = page_px(Size(*A4_PX), HIGH_SCALE)
        hint = paper_hint(Size(width, height), export_dpi(HIGH_SCALE))
        assert "210" in hint and "297" in hint
        assert "175dpi" in hint


class Test画像サイズ:
    """117% / 100% / 75% / 50%。**画素数がそのまま増減する。**

    この道具はウェブで読む絵の下敷きを作るもので、印刷しない（要件定義
    1章）。紙の上で何 mm になるかを保つ細工はしない。
    """

    def test_4つから選ぶ(self):
        assert SCALE_CHOICES == (HIGH_SCALE, 1.0, 0.75, 0.5)

    def test_既定は拡大側(self):
        """原寸のままだと、貼り込んだ画像素材の細かさを捨てて書き出す。"""
        assert DEFAULT_SCALE == HIGH_SCALE
        assert HIGH_DPI == 175

    def test_表示は百分率(self):
        assert [scale_label(s) for s in SCALE_CHOICES] == ["117%", "100%", "75%", "50%"]

    def test_拡大するとA4相当の長辺が約2048px(self):
        """素材の原寸（長辺 2,048px 程度）を捨てずに書き出せる大きさ。"""
        width, height = page_px(Size(*A4_PX), HIGH_SCALE)
        assert (width, height) == (1447, 2046)

    def test_拡大は全ページ一律(self):
        """用紙ごとに長辺を揃えるのではなく、どの用紙でも同じ倍率。"""
        b5 = page_px(Size(1075.0, 1518.0), HIGH_SCALE)
        assert b5 == (1254, 1771)

    def test_100パーセントはページの寸法そのもの(self, saved_state):
        """座標系が px なので、換算を挟まない。"""
        page = saved_state.page
        image = render_page(saved_state, page, 1.0)
        assert (image.width(), image.height()) == (round(page.size.w), round(page.size.h))
        assert (image.width(), image.height()) == A4_PX

    def test_画素数が倍率どおりに減る(self, saved_state):
        page = saved_state.page
        three_quarters = render_page(saved_state, page, 0.75)
        half = render_page(saved_state, page, 0.5)

        assert (three_quarters.width(), three_quarters.height()) == (930, 1316)
        assert (half.width(), half.height()) == (620, 877)

    def test_縦横比は保つ(self, saved_state):
        page = saved_state.page
        full = render_page(saved_state, page, 1.0)
        half = render_page(saved_state, page, 0.5)

        assert half.width() / half.height() == pytest.approx(
            full.width() / full.height(), abs=0.002
        )

    def test_画素数を直に引ける(self):
        assert page_px(Size(1240.0, 1754.0)) == A4_PX
        assert page_px(Size(1240.0, 1754.0), 0.5) == (620, 877)

    def test_0画素にはしない(self):
        # 0 幅の QImage は作れない。丸めて 0 になる経路を塞いでおく
        assert page_px(Size(1.0, 1.0), 0.5) == (1, 1)

    def test_倍率を省くと原寸(self, saved_state):
        image = render_page(saved_state, saved_state.page)
        assert (image.width(), image.height()) == A4_PX


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

    def test_JPGは拡張子が変わる(self):
        assert page_filename(0, 9, "JPG") == "p01.jpg"

    def test_既定はPNG(self):
        assert DEFAULT_FORMAT == "PNG"
        assert EXPORT_FORMATS == ("PNG", "JPG")


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

    def test_参考用のdpiを書き込む(self, saved_state):
        """入れないとクリスタが 72dpi の画像として開く。

        印刷しないので覚え書きでしかないが、抜けると原稿用紙に対して
        極端な大きさで貼られる。
        """
        image = render_page(saved_state, saved_state.page)
        assert image.dotsPerMeterX() == dots_per_meter(REFERENCE_DPI)
        assert image.dotsPerMeterY() == dots_per_meter(REFERENCE_DPI)

    def test_拡大したらdpiも一緒に上げる(self, saved_state):
        """上げないと、クリスタで 1.17 倍に膨れて毎回縮めることになる。

        欲しいのは「同じ大きさで、きめが細かい」下敷き。
        """
        image = render_page(saved_state, saved_state.page, HIGH_SCALE)
        assert image.dotsPerMeterX() == dots_per_meter(HIGH_DPI)
        assert image.dotsPerMeterY() == dots_per_meter(HIGH_DPI)

    def test_縮小してもdpiは据え置く(self, saved_state):
        """75dpi と書くと A4 のまま粗く貼られ、「小さくしたのに小さく
        ならない」になる（要件定義 6.7 で撤回した設計）。
        """
        image = render_page(saved_state, saved_state.page, 0.5)
        assert image.dotsPerMeterX() == dots_per_meter(REFERENCE_DPI)
        assert export_dpi(0.5) == REFERENCE_DPI

    def test_大きすぎるページは断る(self, saved_state):
        with saved_state.edit("巨大なページ") as project:
            project.pages[0].size = Size(MAX_SIDE_PX + 1.0, 100.0)
        with pytest.raises(ExportError, match="大きすぎます"):
            render_page(saved_state, saved_state.page)

    def test_用紙の縁も目安線も描かない(self, saved_state):
        """何も置いていないページは、真っ白な1枚になる。

        用紙の縁（灰色の輪郭）が残ると、四辺に線の入った下敷きができる。
        """
        image = render_page(saved_state, saved_state.page, 0.5)
        assert image == blank_like(image)

    def test_空のセリフの点線枠を描かない(self, saved_state):
        saved_state.add_text(Rect(120.0, 120.0, 240.0, 120.0), "")
        image = render_page(saved_state, saved_state.page, 0.5)
        assert image == blank_like(image)

    def test_画面には点線枠が出る(self, saved_state):
        """上の2つが「そもそも何も描いていない」で通っていないことの裏取り。"""
        saved_state.add_text(Rect(120.0, 120.0, 240.0, 120.0), "")
        image = _screen_render(saved_state)
        assert image != blank_like(image)

    def test_コマの枠線は描かれる(self, with_panel):
        image = render_page(with_panel, with_panel.page)
        assert image != blank_like(image)

    def test_コマの下地を塗らない(self, with_panel):
        """コマの中は用紙の白のまま。範囲は枠線が示す。

        画面の薄い灰色は「どこがコマか」を見分けるための色で、紙の上では
        コマの中は白。下敷きに敷いたときに灰色が乗ると、絵と紙の白の
        境目が分からなくなる。
        """
        image = render_page(with_panel, with_panel.page)
        assert image.pixelColor(*PANEL_INSIDE) == PAGE_BG

    def test_画面では下地を塗る(self, with_panel):
        image = _screen_render(with_panel)
        assert image.pixelColor(*PANEL_INSIDE) == PANEL_FILL


def _screen_render(state) -> QImage:
    """画面と同じ設定（補助表示あり）で、原寸に描いた1枚。"""
    from PySide6.QtGui import QPainter

    page = state.page
    image = QImage(round(page.size.w), round(page.size.h), QImage.Format.Format_ARGB32)
    image.fill(PAGE_BG)
    painter = QPainter(image)
    PageRenderer(state).draw(painter, page)
    painter.end()
    return image


class Test原寸:
    """書き出しだけは縮小版を使わない（要件定義 6.3）。"""

    def test_書き出しは原寸を返す(self, saved_state, large_png):
        ref, px = saved_state.import_bytes(large_png)
        assert px == (2000, 1500)

        full = FullImages(saved_state)(ImageObject(id="img", asset=ref))
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

        full = FullImages(saved_state)(ImageObject(id="img", asset=ref))
        assert not full.is_reduced
        assert saved_state.preview(ref).is_reduced

    def test_欠けた画像を数える(self, saved_state):
        with saved_state.edit("画像") as project:
            page = project.pages[0]
            panel = project.add_panel(page, Rect(10, 10, 90, 60))
            project.add_image(panel, "sha1:missing", Rect(12, 12, 80, 55), (100, 100))
        assert missing_assets_in(saved_state, [0]) == 1

    def test_欠けたマークも数える(self, saved_state):
        """マークはページ直下にあり、コマの子を辿るだけでは見つからない。

        画面には×印が出るのに警告だけ出ない、という抜けが起きていた。
        """
        with saved_state.edit("マーク") as project:
            page = project.pages[0]
            project.add_sticker(
                page, "exclamation", "assets/missing.png", Rect(10, 10, 40, 40), (100, 100)
            )
        assert missing_assets_in(saved_state, [0]) == 1


class Test書き出しの実行:
    """ファイルが実際にできること。"""

    def test_ページ数ぶんできる(self, saved_state, tmp_path):
        saved_state.add_page()
        dest = export_dir_of(saved_state)

        written = export_pages(saved_state, [0, 1], dest, 0.5)

        assert [p.name for p in written] == ["p01.png", "p02.png"]
        assert all(p.is_file() for p in written)

    def test_フォルダが無ければ作る(self, saved_state):
        dest = export_dir_of(saved_state)
        assert not dest.exists()
        export_pages(saved_state, [0], dest, 0.5)
        assert dest.is_dir()

    def test_一時ファイルを残さない(self, saved_state):
        dest = export_dir_of(saved_state)
        export_pages(saved_state, [0], dest, 0.5)
        assert [p.name for p in dest.iterdir()] == ["p01.png"]

    def test_書き出したPNGを読み直せる(self, saved_state):
        dest = export_dir_of(saved_state)
        path = export_pages(saved_state, [0], dest)[0]

        loaded = QImage(str(path))
        assert (loaded.width(), loaded.height()) == A4_PX
        assert loaded.dotsPerMeterX() == dots_per_meter(REFERENCE_DPI)

    def test_上書きしても壊れない(self, saved_state):
        dest = export_dir_of(saved_state)
        path = export_pages(saved_state, [0], dest, 0.5)[0]
        before = path.stat().st_size

        export_pages(saved_state, [0], dest, 1.0)

        assert path.stat().st_size != before
        assert not QImage(str(path)).isNull()

    def test_書けない場所は例外にする(self, saved_state, tmp_path):
        # 同じ名前のフォルダがあると PNG を置けない
        blocked = tmp_path / "ふさがっている"
        (blocked / "p01.png").mkdir(parents=True)
        with pytest.raises(ExportError):
            write_image(render_page(saved_state, saved_state.page, 0.5), blocked / "p01.png")


class TestJPG書き出し:
    """PNG に加えて JPG も選べること（要件定義 6.7）。

    品質はここでは引数で直に渡す。実際の出どころ（設定ファイルから読む
    こと）は `Test画面からの書き出し` の側で確かめる。
    """

    def test_拡張子がjpgになる(self, saved_state):
        dest = export_dir_of(saved_state)
        written = export_pages(saved_state, [0], dest, 0.5, "JPG")
        assert [p.name for p in written] == ["p01.jpg"]

    def test_書き出したJPGを読み直せる(self, saved_state):
        dest = export_dir_of(saved_state)
        path = export_pages(saved_state, [0], dest, 0.5, "JPG", 90)[0]

        loaded = QImage(str(path))
        assert not loaded.isNull()
        assert (loaded.width(), loaded.height()) == page_px(saved_state.page.size, 0.5)

    def test_品質が高いほどファイルが大きい(self, saved_state, large_png):
        """横縞の画像で圧縮の効き方に差が出ることを確かめる。"""
        ref, _ = saved_state.import_bytes(large_png)
        with saved_state.edit("画像") as project:
            page = project.pages[0]
            panel = project.add_panel(page, Rect(60.0, 60.0, 540.0, 360.0))
            project.add_image(panel, ref, Rect(60.0, 60.0, 540.0, 360.0), (2000, 1500))
        dest = export_dir_of(saved_state)

        low = export_pages(saved_state, [0], dest, 0.5, "JPG", 1)[0]
        low_size = low.stat().st_size
        high = export_pages(saved_state, [0], dest, 0.5, "JPG", 100)[0]

        assert high.stat().st_size > low_size

    def test_PNGとJPGは共存できる(self, saved_state):
        """別形式で書き出しても、もう片方は消さない（放置でよい方針）。"""
        dest = export_dir_of(saved_state)
        export_pages(saved_state, [0], dest, 0.5, "PNG")
        export_pages(saved_state, [0], dest, 0.5, "JPG")
        assert sorted(p.name for p in dest.iterdir()) == ["p01.jpg", "p01.png"]


class Test上書きの検出:
    """すでにあるファイルを見つけること。"""

    def test_無ければ空(self, saved_state):
        dest = export_dir_of(saved_state)
        assert existing_paths(planned_paths(dest, [0], 1)) == []

    def test_形式ごとに判定する(self, saved_state):
        dest = export_dir_of(saved_state)
        export_pages(saved_state, [0], dest, 0.5, "PNG")

        assert existing_paths(planned_paths(dest, [0], 1, "JPG")) == []
        found = existing_paths(planned_paths(dest, [0], 1, "PNG"))
        assert [p.name for p in found] == ["p01.png"]

    def test_あれば挙げる(self, saved_state):
        saved_state.add_page()
        dest = export_dir_of(saved_state)
        export_pages(saved_state, [0], dest, 0.5)

        found = existing_paths(planned_paths(dest, [0, 1], 2))

        assert [p.name for p in found] == ["p01.png"]


class Test画面からの書き出し:
    """メニューから押したときの流れ。断る場所が3つある。"""

    @pytest.fixture
    def window(self, qapp, tmp_path):
        win = MainWindow(EditorState())
        # 実物の data/settings.json を読み書きしないよう、テスト用の場所に差し替える
        win.settings_file = tmp_path / "settings.json"
        win.state.save(tmp_path / "作品")
        yield win
        win.state.history.mark_saved()
        win.close()

    def test_既定の倍率を覚えている(self, window):
        assert window.files.export_scale == DEFAULT_SCALE

    def test_既定の形式を覚えている(self, window):
        assert window.files.export_format == DEFAULT_FORMAT

    def test_このページだけ書き出す(self, window, monkeypatch):
        window.add_page()
        _accept_dialog(monkeypatch, all_pages=False)

        assert window.files.export_image()

        dest = export_dir_of(window.state)
        assert [p.name for p in dest.iterdir()] == ["p02.png"]

    def test_全ページ書き出す(self, window, monkeypatch):
        window.add_page()
        _accept_dialog(monkeypatch, all_pages=True)

        assert window.files.export_image()

        dest = export_dir_of(window.state)
        assert sorted(p.name for p in dest.iterdir()) == ["p01.png", "p02.png"]

    def test_JPG形式を選べる(self, window, monkeypatch):
        _accept_dialog(monkeypatch, all_pages=False, fmt="JPG")

        assert window.files.export_image()

        dest = export_dir_of(window.state)
        assert [p.name for p in dest.iterdir()] == ["p01.jpg"]
        assert window.files.export_format == "JPG"

    def test_品質は設定ファイルから読む(self, window, monkeypatch):
        """ダイアログには出さず、`data/settings.json` の `jpg_quality` を使う。"""
        save_settings(AppSettings(jpg_quality=42), window.settings_file)
        _accept_dialog(monkeypatch, all_pages=False, fmt="JPG")

        captured: dict = {}

        def fake_export_pages(state, indexes, dest, scale, fmt, quality):
            captured["quality"] = quality
            return [dest / page_filename(indexes[0], state.page_count, fmt)]

        monkeypatch.setattr("manga_layout.ui.project_io.export_pages", fake_export_pages)

        assert window.files.export_image()
        assert captured["quality"] == 42

    def test_取り消せば何も書かない(self, window, monkeypatch):
        monkeypatch.setattr(
            "manga_layout.ui.project_io.ExportDialog.exec",
            lambda self: QDialog.DialogCode.Rejected,
        )

        assert not window.files.export_image()
        assert not export_dir_of(window.state).exists()

    def test_保存前は保存を促す(self, qapp, monkeypatch):
        window = MainWindow(EditorState())
        asked: list[str] = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: (asked.append(a[1]), QMessageBox.StandardButton.Cancel)[1],
        )

        assert not window.files.export_image()
        assert asked == ["先に保存が必要です"]
        window.state.history.mark_saved()
        window.close()

    def test_上書きを断れば書き換えない(self, window, monkeypatch):
        _accept_dialog(monkeypatch, all_pages=False)
        window.files.export_image()
        path = export_dir_of(window.state) / "p01.png"
        before = path.read_bytes()

        # 2回目は原寸に上げるが、上書きの確認で断る
        window.files.export_scale = 1.0
        monkeypatch.setattr(
            QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
        )

        assert not window.files.export_image()
        assert path.read_bytes() == before

    def test_上書きを承知すれば書き換える(self, window, monkeypatch):
        _accept_dialog(monkeypatch, all_pages=False, scale=0.5)
        window.files.export_image()
        path = export_dir_of(window.state) / "p01.png"
        before = path.read_bytes()

        _accept_dialog(monkeypatch, all_pages=False, scale=1.0)
        monkeypatch.setattr(
            QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Ok
        )

        assert window.files.export_image()
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

        assert not window.files.export_image()
        assert not export_dir_of(window.state).exists()


def _accept_dialog(
    monkeypatch, *, all_pages: bool, scale: float = 0.5, fmt: str = DEFAULT_FORMAT
) -> None:
    """設定の窓を出さずに、選んだことにして進める。"""
    monkeypatch.setattr(
        "manga_layout.ui.project_io.ExportDialog.exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        "manga_layout.ui.project_io.ExportDialog.wants_all_pages", lambda self: all_pages
    )
    monkeypatch.setattr(
        "manga_layout.ui.project_io.ExportDialog.chosen_scale", lambda self: scale
    )
    monkeypatch.setattr(
        "manga_layout.ui.project_io.ExportDialog.chosen_format", lambda self: fmt
    )
