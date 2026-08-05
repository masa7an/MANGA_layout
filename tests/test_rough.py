"""ラフ（下敷き）の検証（要件定義 6.23）。

押さえたいのは6つ。

1. **敷いていない作品の保存形式が変わらないこと。** 付箋・集中線と同じ線引きで、
   使っていなければ `project.json` は追加前と一字一句同じ
2. **「未使用ファイルを整理」がラフの実体を持っていかないこと。** ラフには
   欠けたときの×印が無いので、持っていかれると黙って消えたように見える
3. **書き出しに出ないこと。** なぞる相手であって作品の中身ではない
4. **コマを置いた部分が隠れること。** 置き終わった場所からラフが消えていく、
   という狙った見え方そのもの
5. **青く淡くするのと元の色に戻すのが1手で切り替わること**
6. **道具を持ち替えている間だけ掴めること。** ラフは一番下にあるので、
   普段の選択で掴めると「コマを選んだつもりでラフが動く」経路ができる
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from test_ui_balloon import drag, move_to, press, release

from manga_layout import ProjectFormatError, Rect, new_project
from manga_layout.images import ROUGH_BLUE, to_blue_pencil, to_png_bytes
from manga_layout.model import Page, PageRough
from manga_layout.settings import (
    ROUGH_OPACITY_DEFAULT,
    AppSettings,
    load_settings,
    save_settings,
)
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.canvas import PENCIL_TIP, pencil_cursor
from manga_layout.ui.export import render_page
from manga_layout.ui.menus import ROUGH_FADED_LABELS
from manga_layout.ui.render import PAGE_BG, PageRenderer
from manga_layout.ui.state import TOOL_ROUGH, TOOL_SELECT

# ラフを敷く矩形。数を丸くして、動かした量をそのまま確かめられるようにする
ROUGH_RECT = Rect(100.0, 200.0, 400.0, 400.0)
# その内側で、つまみから十分に離れた点
ROUGH_INSIDE = (300.0, 400.0)


def solid_png(color: str, size: int = 64) -> bytes:
    """一色で塗った PNG。**中身が分かっているので、描いた色を確かめられる。**"""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return to_png_bytes(image)


@pytest.fixture
def black_png(qapp) -> bytes:
    """真っ黒な1枚。青く染めると `ROUGH_BLUE` そのものになる。"""
    return solid_png("#000000")


@pytest.fixture
def state(qapp) -> EditorState:
    return EditorState()


@pytest.fixture
def state_with_rough(state, black_png) -> EditorState:
    """ラフを1枚敷き、位置と大きさを決め打ちにした状態。"""
    state.place_rough(black_png)
    state.set_rough_rect(ROUGH_RECT, "ラフの位置")
    return state


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def window_with_rough(window, black_png):
    window.state.place_rough(black_png)
    window.state.set_rough_rect(ROUGH_RECT, "ラフの位置")
    return window


def screen_render(state) -> QImage:
    """画面と同じ設定（補助表示あり）で、原寸に描いた1枚。"""
    page = state.page
    image = QImage(round(page.size.w), round(page.size.h), QImage.Format.Format_ARGB32)
    image.fill(PAGE_BG)
    painter = QPainter(image)
    PageRenderer(state).draw(painter, page)
    painter.end()
    return image


def color_at(image: QImage, point: tuple[float, float]) -> QColor:
    return QColor(image.pixelColor(round(point[0]), round(point[1])))


class Test保存形式:
    """`Page.rough`。付箋（`note`）とまったく同じ線引き（→ 6.18）。"""

    def test_敷いていないページには項目ごと書かない(self):
        page = Page(id="page_0001")
        assert "rough" not in page.to_dict()

    def test_敷いていない作品の保存形式は追加前と変わらない(self, sample_project):
        # ここで見たいのは「rough という語がどこにも出ないこと」。
        # 出るようになったら、既にある作品の project.json が変わっている
        text = json.dumps(sample_project.to_dict(), ensure_ascii=False)
        assert "rough" not in text

    def test_往復しても変わらない(self):
        page = Page(id="page_0001")
        page.rough = PageRough(
            asset="assets/abc.png", rect=ROUGH_RECT, src_px=(800, 600), faded=False
        )
        restored = Page.from_dict(page.to_dict(), "pages[0]")

        assert restored.rough == page.rough

    def test_faded_を省いたら青く淡い扱いで読む(self):
        data = {
            "id": "page_0001",
            "rough": {
                "asset": "assets/abc.png",
                "rect": ROUGH_RECT.to_dict(),
                "src_px": [800, 600],
            },
        }
        assert Page.from_dict(data, "pages[0]").rough.faded is True

    def test_asset_が無ければ弾く(self):
        data = {
            "id": "page_0001",
            "rough": {"rect": ROUGH_RECT.to_dict(), "src_px": [800, 600]},
        }
        with pytest.raises(ProjectFormatError, match="asset"):
            Page.from_dict(data, "pages[0]")

    def test_src_px_の要素数が違えば弾く(self):
        data = {
            "id": "page_0001",
            "rough": {
                "asset": "assets/abc.png",
                "rect": ROUGH_RECT.to_dict(),
                "src_px": [800, 600, 400],
            },
        }
        with pytest.raises(ProjectFormatError, match="src_px"):
            Page.from_dict(data, "pages[0]")

    def test_複製に残る(self):
        """Undo はプロジェクトの写しで動く（→ 6.8）。ここが抜けると戻せない。"""
        project = new_project()
        project.pages[0].rough = PageRough(
            asset="assets/abc.png", rect=ROUGH_RECT, src_px=(800, 600)
        )
        assert project.copy().pages[0].rough == project.pages[0].rough


class Test実体の参照:
    """数え漏らすと「未使用ファイルを整理」がラフを `_unused/` へ移す。"""

    def test_参照している実体に数える(self):
        project = new_project()
        project.pages[0].rough = PageRough(
            asset="assets/rough.png", rect=ROUGH_RECT, src_px=(800, 600)
        )
        assert "assets/rough.png" in project.referenced_assets()

    def test_敷いていなければ増えない(self, sample_project):
        assert not any("rough" in ref for ref in sample_project.referenced_assets())


class Test設定:
    """濃さは作品ではなく好みなので `settings.json` に置く（→ 6.23）。"""

    def test_既定は40パーセント(self):
        assert AppSettings().rough_opacity == pytest.approx(0.4)
        assert ROUGH_OPACITY_DEFAULT == pytest.approx(0.4)

    def test_書いたものが読める(self, tmp_path):
        path = tmp_path / "settings.json"
        save_settings(AppSettings(rough_opacity=0.8), path)
        assert load_settings(path).rough_opacity == pytest.approx(0.8)

    @pytest.mark.parametrize("value", [0.0, -1, 2.5, "濃いめ", True, None])
    def test_受け付けられない値は既定に落とす(self, tmp_path, value):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"rough_opacity": value}), encoding="utf-8")
        assert load_settings(path).rough_opacity == ROUGH_OPACITY_DEFAULT


class Test青鉛筆:
    """色を捨ててから青を乗せる（→ `images.to_blue_pencil`）。"""

    def test_黒は青になる(self, qapp):
        black = QImage(4, 4, QImage.Format.Format_ARGB32)
        black.fill(QColor("#000000"))

        assert QColor(to_blue_pencil(black).pixelColor(2, 2)) == ROUGH_BLUE

    def test_白は白のまま(self, qapp):
        white = QImage(4, 4, QImage.Format.Format_ARGB32)
        white.fill(QColor("#FFFFFF"))

        assert QColor(to_blue_pencil(white).pixelColor(2, 2)) == QColor("#FFFFFF")

    @pytest.mark.parametrize("color", ["#FF0000", "#00FF00", "#0000FF", "#808080"])
    def test_どの色を入れても青寄りになる(self, qapp, color):
        """色を先に捨てるので、元が何色でも同じ青の階調に乗る。

        捨てずに青を乗せると、赤い線だけが黒く沈んで濃さが揃わない。
        """
        image = QImage(4, 4, QImage.Format.Format_ARGB32)
        image.fill(QColor(color))
        out = QColor(to_blue_pencil(image).pixelColor(2, 2))

        assert out.red() < out.green() < out.blue()

    def test_濃淡はそのまま残る(self, qapp):
        """線の濃さが潰れると、なぞる手がかりが減る。"""
        def dyed(color: str) -> QColor:
            image = QImage(4, 4, QImage.Format.Format_ARGB32)
            image.fill(QColor(color))
            return QColor(to_blue_pencil(image).pixelColor(2, 2))

        # 暗い灰は青に近く、明るい灰は白に近い
        assert dyed("#303030").red() < dyed("#C0C0C0").red()


class Test描画:
    """用紙のすぐ上、コマより奥（→ 6.23）。"""

    def test_画面には出る(self, state_with_rough):
        assert color_at(screen_render(state_with_rough), ROUGH_INSIDE) != PAGE_BG

    def test_書き出しには出ない(self, state_with_rough):
        image = render_page(state_with_rough, state_with_rough.page)
        assert color_at(image, ROUGH_INSIDE) == PAGE_BG

    def test_コマを置くとその部分は見えなくなる(self, state_with_rough):
        before = color_at(screen_render(state_with_rough), ROUGH_INSIDE)
        with state_with_rough.edit("コマの追加") as project:
            project.add_panel(project.pages[0], Rect(150.0, 250.0, 300.0, 300.0))
        after = color_at(screen_render(state_with_rough), ROUGH_INSIDE)

        from manga_layout.ui.render import PANEL_FILL

        assert before != after
        assert after == PANEL_FILL

    def test_濃さの設定が効く(self, state_with_rough):
        state_with_rough.rough_opacity = 1.0
        dark = color_at(screen_render(state_with_rough), ROUGH_INSIDE)
        state_with_rough.rough_opacity = 0.1
        light = color_at(screen_render(state_with_rough), ROUGH_INSIDE)

        # 薄いほうが用紙の白に近い
        assert light.blue() > dark.blue()

    def test_青く淡くしていれば青くなる(self, state_with_rough):
        state_with_rough.rough_opacity = 1.0
        assert color_at(screen_render(state_with_rough), ROUGH_INSIDE) == ROUGH_BLUE

    def test_元の色に戻すと写真のまま出る(self, state_with_rough):
        state_with_rough.set_rough_faded(False)
        # 元が真っ黒なので、そのまま出れば黒。淡さも掛からない
        assert color_at(screen_render(state_with_rough), ROUGH_INSIDE) == QColor(
            "#000000"
        )

    def test_実体が無ければ何も描かない(self, state):
        """×印（`_draw_missing`）に当たるものは出さない（→ 6.23）。"""
        with state.edit_page("壊れたラフ") as page:
            page.rough = PageRough(
                asset="assets/missing.png", rect=ROUGH_RECT, src_px=(800, 600)
            )
        assert color_at(screen_render(state), ROUGH_INSIDE) == PAGE_BG


class Test読み込みと取り外し:
    def test_ページに収まる大きさで敷く(self, state, black_png):
        rough = state.place_rough(black_png)
        size = state.page.size

        assert rough.rect.w <= size.w + 1 and rough.rect.h <= size.h + 1
        # 正方形の画像なので、短いほうの辺に合わせて中央に収まる
        assert rough.rect.w == pytest.approx(rough.rect.h)
        assert rough.rect.center == pytest.approx((size.w / 2.0, size.h / 2.0))

    def test_1ページに1枚(self, state, black_png):
        first = state.place_rough(black_png)
        second = state.place_rough(solid_png("#FFFFFF"))

        # 置き換わるだけ。ページが持つのは常に1枚
        assert state.page.rough == second
        assert second.asset != first.asset

    def test_外せる(self, state_with_rough):
        state_with_rough.remove_rough()
        assert state_with_rough.page.rough is None

    def test_外しても実体は消さない(self, state_with_rough):
        ref = state_with_rough.page.rough.asset
        state_with_rough.remove_rough()
        # Undo で戻せる操作なので、実体まで消すと戻したときに絵が出ない
        assert state_with_rough.read_asset(ref) is not None

    def test_Undo_で戻る(self, state_with_rough):
        rough = state_with_rough.page.rough
        state_with_rough.remove_rough()
        state_with_rough.undo()

        assert state_with_rough.page.rough == rough

    def test_色の切り替えも_Undo_で戻る(self, state_with_rough):
        state_with_rough.set_rough_faded(False)
        state_with_rough.undo()

        assert state_with_rough.page.rough.faded is True


class Test調整の道具:
    """掴めるのは道具を持ち替えている間だけ（→ 6.23）。"""

    def test_ドラッグで動く(self, window_with_rough):
        view = window_with_rough.view
        window_with_rough.state.set_tool(TOOL_ROUGH)
        drag(view, *ROUGH_INSIDE, ROUGH_INSIDE[0] + 60.0, ROUGH_INSIDE[1] + 40.0)

        rect = window_with_rough.state.page.rough.rect
        assert (rect.x, rect.y) == pytest.approx((ROUGH_RECT.x + 60.0, ROUGH_RECT.y + 40.0))
        assert (rect.w, rect.h) == pytest.approx((ROUGH_RECT.w, ROUGH_RECT.h))

    def test_つまみで大きさが変わる(self, window_with_rough):
        view = window_with_rough.view
        window_with_rough.state.set_tool(TOOL_ROUGH)
        drag(view, ROUGH_RECT.right, ROUGH_RECT.bottom, ROUGH_RECT.right + 100.0, ROUGH_RECT.bottom + 100.0)

        rect = window_with_rough.state.page.rough.rect
        assert rect.w > ROUGH_RECT.w
        # 元が正方形なので、比を保っていれば正方形のまま
        assert rect.w == pytest.approx(rect.h)

    def test_縦横比は常に保つ(self, window_with_rough):
        """写真なので、歪めた下敷きをなぞっても使いものにならない（→ 6.23）。"""
        view = window_with_rough.view
        window_with_rough.state.set_tool(TOOL_ROUGH)
        # 横だけを大きく引いても、縦が付いてくる
        drag(view, ROUGH_RECT.right, ROUGH_RECT.bottom, ROUGH_RECT.right + 200.0, ROUGH_RECT.bottom + 10.0)

        rect = window_with_rough.state.page.rough.rect
        assert rect.w == pytest.approx(rect.h)

    def test_1回のドラッグで1手(self, window_with_rough):
        view = window_with_rough.view
        state = window_with_rough.state
        state.set_tool(TOOL_ROUGH)
        depth = state.history.depth
        drag(view, *ROUGH_INSIDE, ROUGH_INSIDE[0] + 60.0, ROUGH_INSIDE[1])

        assert state.history.depth == depth + 1

    def test_動いていなければ履歴に積まない(self, window_with_rough):
        view = window_with_rough.view
        state = window_with_rough.state
        state.set_tool(TOOL_ROUGH)
        depth = state.history.depth
        press(view, *ROUGH_INSIDE)
        release(view, *ROUGH_INSIDE)

        assert state.history.depth == depth

    def test_調整中はコマを掴めない(self, window_with_rough):
        state = window_with_rough.state
        with state.edit("コマの追加") as project:
            project.add_panel(project.pages[0], Rect(700.0, 900.0, 400.0, 400.0))
        state.select(None)
        state.set_tool(TOOL_ROUGH)
        # ラフの外、コマの内側を押す
        press(window_with_rough.view, 900.0, 1100.0)

        assert state.selected_id is None
        release(window_with_rough.view, 900.0, 1100.0)

    def test_調整中のカーソルは鉛筆(self, window_with_rough):
        """ラフだけが掴めることを手元の形で示す（→ 6.23）。"""
        view = window_with_rough.view
        window_with_rough.state.set_tool(TOOL_ROUGH)
        move_to(view, *ROUGH_INSIDE)

        assert view.viewport().cursor().shape() == Qt.CursorShape.BitmapCursor
        assert view.viewport().cursor().hotSpot() == QPoint(*PENCIL_TIP)

    def test_つまみの上では伸びる向きを出す(self, window_with_rough):
        """鉛筆で塗り潰すと、どちらへ伸びるつまみか分からなくなる。"""
        view = window_with_rough.view
        window_with_rough.state.set_tool(TOOL_ROUGH)
        move_to(view, ROUGH_RECT.right, ROUGH_RECT.bottom)

        assert view.viewport().cursor().shape() == Qt.CursorShape.SizeFDiagCursor

    def test_鉛筆カーソルは作り直さない(self, qapp):
        """マウスを動かすたびに絵を描き直すことになる。"""
        assert pencil_cursor() is pencil_cursor()

    def test_普段の選択ではラフを掴めない(self, window_with_rough):
        state = window_with_rough.state
        state.set_tool(TOOL_SELECT)
        rect = state.page.rough.rect
        drag(window_with_rough.view, *ROUGH_INSIDE, ROUGH_INSIDE[0] + 80.0, ROUGH_INSIDE[1])

        assert state.page.rough.rect == rect
        assert state.selected_id is None


class Testメニュー:
    def test_ラフが無ければ押せない(self, window):
        menu = window.file_menu
        assert not menu.rough_faded_action.isEnabled()
        assert not menu.rough_remove_action.isEnabled()
        assert not menu.rough_tool_action.isEnabled()

    def test_敷けば押せる(self, window_with_rough):
        menu = window_with_rough.file_menu
        assert menu.rough_faded_action.isEnabled()
        assert menu.rough_remove_action.isEnabled()
        assert menu.rough_tool_action.isEnabled()

    def test_文言はどちらに変わるかを出す(self, window_with_rough):
        menu = window_with_rough.file_menu
        assert menu.rough_faded_action.text() == ROUGH_FADED_LABELS[False]

        window_with_rough.toggle_rough_faded()
        assert menu.rough_faded_action.text() == ROUGH_FADED_LABELS[True]

    def test_切り替えは1手で色と濃さの両方に効く(self, window_with_rough):
        window_with_rough.toggle_rough_faded()
        assert window_with_rough.state.page.rough.faded is False

        window_with_rough.toggle_rough_faded()
        assert window_with_rough.state.page.rough.faded is True

    def test_外すと道具が選択へ戻る(self, window_with_rough):
        state = window_with_rough.state
        state.set_tool(TOOL_ROUGH)
        window_with_rough.remove_rough()

        assert state.tool == TOOL_SELECT

    def test_ラフの無いページへ移ると道具が選択へ戻る(self, window_with_rough):
        state = window_with_rough.state
        with state.edit("ページの追加") as project:
            project.add_page()
        state.set_tool(TOOL_ROUGH)
        state.set_page_index(1)

        assert state.tool == TOOL_SELECT

    def test_Undo_でラフが消えても道具が選択へ戻る(self, window_with_rough):
        state = window_with_rough.state
        state.set_tool(TOOL_ROUGH)
        # ラフを敷く前まで戻す（位置の1手 → 読み込みの1手）
        state.undo()
        state.undo()

        assert state.page.rough is None
        assert state.tool == TOOL_SELECT

    def test_右クリックにラフの項目が出る(self, window_with_rough):
        menu = window_with_rough.context_menu.build(50.0, 50.0)
        try:
            assert window_with_rough.file_menu.rough_faded_action in menu.actions()
        finally:
            menu.deleteLater()

    def test_ラフが無ければ右クリックにも出さない(self, window):
        menu = window.context_menu.build(50.0, 50.0)
        try:
            assert window.file_menu.rough_faded_action not in menu.actions()
        finally:
            menu.deleteLater()
