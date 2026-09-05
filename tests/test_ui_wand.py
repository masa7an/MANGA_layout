"""切り抜きの道具（自動領域選択 → 要件定義 10.3）。

**押した所が消える。** 選ぶ・確かめる・確定するの段取りを置かない代わりに、
1回押すと1手ぶん履歴に積まれる（違えば Undo → 本人の判断 2026-08-27）。

塗りつぶしの計算そのものは tests/test_wand.py。ここで見るのは
**どこから手が届くか**——道具・押した座標の翻訳・履歴・メニューの出入り。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter

from manga_layout import Rect
from manga_layout.assets import AssetStore
from manga_layout.image_masks import decode_mask
from manga_layout.images import size_px, to_png_bytes
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import TOOL_SELECT, TOOL_WAND

# コマは用紙の左上に大きく取り、絵をその中いっぱいに置く
PANEL = Rect(100.0, 100.0, 600.0, 600.0)
IMAGE_PX = (120, 120)


@pytest.fixture
def window(qapp, tmp_path):
    win = MainWindow(EditorState())
    win.state.save(tmp_path / "作品")
    yield win
    win.state.history.mark_saved()
    win.close()


def boxed_png() -> bytes:
    """白地の真ん中に、黒い枠で囲った四角を1つ描いた絵。"""
    image = QImage(IMAGE_PX[0], IMAGE_PX[1], QImage.Format.Format_ARGB32)
    image.fill(QColor("#FFFFFF"))
    painter = QPainter(image)
    painter.setPen(QColor("#000000"))
    painter.drawRect(30, 30, 60, 60)
    painter.end()
    return to_png_bytes(image)


@pytest.fixture
def window_with_image(window):
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    panel_id = window.state.page.panels[0].id
    window.state.place_image(panel_id, boxed_png())
    window.state.set_tool(TOOL_WAND)
    return window


def image_of(window):
    return window.state.page.panels[0].children[0]


def _mouse_event(window, kind, u: float, v: float, shift: bool) -> QMouseEvent:
    """絵の中の割合（0〜1）を、ページ座標を経由して canvas の座標へ直す。"""
    image = image_of(window)
    x = image.rect.x + image.rect.w * u
    y = image.rect.y + image.rect.h * v
    view = window.view
    position = QPointF(view.mapFromScene(QPointF(x, y)))
    return QMouseEvent(
        kind,
        position,
        view.viewport().mapToGlobal(position),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier
        if shift
        else Qt.KeyboardModifier.NoModifier,
    )


def click(window, u: float, v: float, *, shift: bool = False) -> None:
    """絵の中の割合（0〜1）で押す。

    **押すだけ**（`test_ui_balloon.press` の Shift 付き版）。この道具は
    ドラッグを持たないので、離す動きは要らない。
    """
    window.view.mousePressEvent(
        _mouse_event(window, QMouseEvent.Type.MouseButtonPress, u, v, shift)
    )


def quick_second_click(window, u: float, v: float, *, shift: bool = False) -> None:
    """**素早い2回目の押下。** Qt はこれを「押下」ではなく「ダブルクリック」として配る。

    `click` を2回呼んでも再現できない——あちらは押下が2回届く経路で、
    **ゆっくり2回押した場合**にあたる。連打のほうを確かめるにはこちらが要る。
    """
    window.view.mouseDoubleClickEvent(
        _mouse_event(window, QMouseEvent.Type.MouseButtonDblClick, u, v, shift)
    )


def mask_of(window):
    image = image_of(window)
    if not image.mask_asset:
        return None
    return decode_mask(window.state.read_asset(image.mask_asset))


class Test押した所が消える:
    def test_1回押すと切り抜きが入る(self, window_with_image):
        assert image_of(window_with_image).mask_asset == ""
        click(window_with_image, 0.5, 0.5)  # 枠の中
        assert image_of(window_with_image).mask_asset != ""

    def test_押した区画だけが消える(self, window_with_image):
        click(window_with_image, 0.5, 0.5)
        mask = mask_of(window_with_image)
        assert size_px(mask) == IMAGE_PX, "マスクは元画像と同じ寸法"
        assert mask.pixelColor(60, 60).value() == 0, "押した中は消えた"
        assert mask.pixelColor(5, 5).value() == 255, "外は残っている"
        assert mask.pixelColor(30, 60).value() == 255, "枠線も残っている"

    def test_続けて押すと足していける(self, window_with_image):
        """**掛け直しではなく、今のマスクから引く。** 区画ごとに消していける。"""
        click(window_with_image, 0.5, 0.5)  # 中
        click(window_with_image, 0.05, 0.05)  # 外
        mask = mask_of(window_with_image)
        assert mask.pixelColor(60, 60).value() == 0, "1回目のぶんが残っている"
        assert mask.pixelColor(5, 5).value() == 0, "2回目のぶんも消えた"

    def test_Shiftで押すとそこだけ残る(self, window_with_image):
        click(window_with_image, 0.5, 0.5, shift=True)
        mask = mask_of(window_with_image)
        assert mask.pixelColor(60, 60).value() == 255, "押した所が残る"
        assert mask.pixelColor(5, 5).value() == 0, "それ以外が消える"

    def test_1回押すと1手(self, window_with_image):
        click(window_with_image, 0.5, 0.5)
        assert image_of(window_with_image).mask_asset != ""
        window_with_image.state.undo()
        assert image_of(window_with_image).mask_asset == "", "1手で元の絵へ戻る"
        window_with_image.state.redo()
        assert image_of(window_with_image).mask_asset != ""

    def test_履歴の名前で何をしたか分かる(self, window_with_image):
        click(window_with_image, 0.5, 0.5)
        click(window_with_image, 0.05, 0.05, shift=True)
        assert window_with_image.state.history.undo_label == "押した所だけ残す"
        window_with_image.state.undo()
        assert window_with_image.state.history.undo_label == "押した所を消す"


class Test手の届く範囲:
    def test_絵の外を押しても何も起きない(self, window_with_image):
        """コマの中でも、絵の載っていない所は対象にならない。"""
        state = window_with_image.state
        with state.edit_page("絵を小さくする") as page:
            page.panels[0].children[0].rect = Rect(120.0, 120.0, 100.0, 100.0)
        click(window_with_image, 5.0, 5.0)  # 絵の外
        assert image_of(window_with_image).mask_asset == ""

    def test_絵を選んでいなくても押せる(self, window_with_image):
        """**先に選ばせない。** 押した所の絵が対象（トーンと違う所）。"""
        window_with_image.state.select(None)
        click(window_with_image, 0.5, 0.5)
        assert image_of(window_with_image).mask_asset != ""

    def test_選択の道具では消えない(self, window_with_image):
        """道具を持ち替えている間だけ消える（ラフ・トーン範囲と同じ切り分け）。"""
        window_with_image.state.set_tool(TOOL_SELECT)
        click(window_with_image, 0.5, 0.5)
        assert image_of(window_with_image).mask_asset == ""


class Test使えない絵:
    """**使えない絵を押しても、黙って何も起きないままにしない**（2026-09-05 に直した）。

    押した瞬間の処理から例外が漏れると、PySide6 は traceback を**コンソールへ
    出して先へ進む**（アプリは落ちない）。`run.bat` から起動した利用者に
    コンソールは見えないので、**押したのに何も起きない**だけが残る。

    **「実体が無い」と「実体はあるが開けない」を分けない**
    （→ `EditorState.has_asset`、点検の「使えない画像」と同じ言い分け）。
    """

    def 壊す(self, window, fixture_dir):
        """実体を、署名だけ正しい壊れたファイルへ差し替える。"""
        ref = image_of(window).asset
        broken = (fixture_dir / "broken.png").read_bytes()
        AssetStore(window.state.project_dir).resolve(ref).write_bytes(broken)
        window.state.image_cache.forget(ref)
        window.state.baked_cache.forget(ref)

    def test_実体が壊れていても例外が漏れない(self, window_with_image, fixture_dir):
        self.壊す(window_with_image, fixture_dir)
        click(window_with_image, 0.5, 0.5)  # 例外が漏れればここで落ちる
        assert image_of(window_with_image).mask_asset == "", "切り抜きは掛からない"

    def test_実体が壊れていたら理由を言う(self, window_with_image, fixture_dir):
        said = []
        window_with_image.state.message.connect(said.append)
        self.壊す(window_with_image, fixture_dir)
        click(window_with_image, 0.5, 0.5)
        assert said, "黙って終わらない"
        assert "使えません" in said[-1], said

    def test_実体が無いときと同じ案内(self, window_with_image, fixture_dir):
        """**2つを分けない。** 分けると、同じ「切り抜けない」に2通りの言い方が並ぶ。"""
        欠け = []
        window_with_image.state.message.connect(欠け.append)
        ref = image_of(window_with_image).asset
        AssetStore(window_with_image.state.project_dir).resolve(ref).unlink()
        window_with_image.state.image_cache.forget(ref)
        click(window_with_image, 0.5, 0.5)

        壊れ = []
        window_with_image.state.message.connect(壊れ.append)
        self.壊す(window_with_image, fixture_dir)
        click(window_with_image, 0.5, 0.5)

        assert 欠け[-1] == 壊れ[-1]


class Test連打:
    """**素早い2回目も1手として消える**（2026-09-05 に発見して直した）。

    Qt は素早い2回目の押下を `mousePressEvent` ではなく
    `mouseDoubleClickEvent` へ配る。切り抜きは**押すこと自体が1手**なので、
    そちらでも同じ処理へ回さないと、要件定義 10.3 の「続けて押せば足せる」を
    素直にやったときに2回目が落ちる。
    """

    def test_素早い2回目も消える(self, window_with_image):
        click(window_with_image, 0.5, 0.5)  # 枠の中
        quick_second_click(window_with_image, 0.05, 0.05)  # 枠の外
        mask = mask_of(window_with_image)
        assert mask.pixelColor(60, 60).value() == 0, "1回目のぶん"
        assert mask.pixelColor(5, 5).value() == 0, "素早い2回目のぶんも消える"

    def test_素早い2回目も1手として積まれる(self, window_with_image):
        click(window_with_image, 0.5, 0.5)
        quick_second_click(window_with_image, 0.05, 0.05)
        window_with_image.state.undo()
        mask = mask_of(window_with_image)
        assert mask.pixelColor(5, 5).value() == 255, "2回目だけが戻る"
        assert mask.pixelColor(60, 60).value() == 0, "1回目は残っている"

    def test_素早い2回目でも選択は動かない(self, window_with_image):
        """**押した所を消すだけ**（→ `PageView.mousePressEvent` の注記）。

        踏み込みの巡回に入ると、連打しただけで絵やコマが選ばれる。
        """
        window_with_image.state.select(None)
        click(window_with_image, 0.5, 0.5)
        quick_second_click(window_with_image, 0.5, 0.5)
        assert window_with_image.state.selected_id is None

    def test_素早い2回目でも_Shiftは裏返しのまま届く(self, window_with_image):
        """2回目だけ Shift を落とすと、**残すつもりが消す**に化ける。"""
        click(window_with_image, 0.05, 0.05)  # 枠の外を消す
        quick_second_click(window_with_image, 0.5, 0.5, shift=True)  # 中だけ残す
        mask = mask_of(window_with_image)
        assert mask.pixelColor(60, 60).value() == 255, "押した中が残る"
        assert mask.pixelColor(30, 60).value() == 0, "残す指定なので枠線は落ちる"


class Test許容差:
    def test_メニューから広げ狭められる(self, window_with_image):
        state = window_with_image.state
        始め = state.wand_tolerance
        assert state.step_wand_tolerance(1) is True
        assert state.wand_tolerance > 始め
        assert state.step_wand_tolerance(-1) is True
        assert state.wand_tolerance == 始め

    def test_端で止まる(self, window_with_image):
        state = window_with_image.state
        for _ in range(50):
            state.step_wand_tolerance(1)
        assert state.step_wand_tolerance(1) is False, "上限で止まる"
        for _ in range(50):
            state.step_wand_tolerance(-1)
        assert state.step_wand_tolerance(-1) is False, "下限で止まる"

    def test_端の項目はグレーになる(self, window_with_image):
        """押しても何も起きない項目を、押せるままにしない。"""
        window = window_with_image
        for _ in range(50):
            window.state.step_wand_tolerance(1)
        window._refresh()
        assert window.image_menu.wand.wider_action.isEnabled() is False
        assert window.image_menu.wand.narrower_action.isEnabled() is True

    def test_広げると隣の区画までつながる(self, window_with_image):
        """つまみが効いていることを、結果で確かめる。"""
        state = window_with_image.state
        state.wand_tolerance = 0
        click(window_with_image, 0.5, 0.5)
        狭い = mask_of(window_with_image)
        state.undo()

        state.wand_tolerance = 255  # 何もかも似ていると見なす
        click(window_with_image, 0.5, 0.5)
        広い = mask_of(window_with_image)
        assert 狭い.pixelColor(5, 5).value() == 255, "狭ければ枠の外は残る"
        assert 広い.pixelColor(5, 5).value() == 0, "広ければ枠を越えて消える"


class Test外す:
    def test_メニューから切り抜きを外せる(self, window_with_image):
        window = window_with_image
        click(window, 0.5, 0.5)
        window.state.select(image_of(window).id)
        window._refresh()
        assert window.image_menu.wand.clear_action.isEnabled() is True

        window.clear_image_mask()
        assert image_of(window).mask_asset == ""

    def test_掛かっていなければグレー(self, window_with_image):
        window = window_with_image
        window.state.select(image_of(window).id)
        window._refresh()
        assert window.image_menu.wand.clear_action.isEnabled() is False


def test_持っている間ずっと案内が出る(window_with_image):
    """項目名は4文字しかない。**押すと何が起きるかは状態表示が持つ**（→ 10.3）。"""
    window = window_with_image
    window._refresh_hint()
    hint = window.hint_label.text()
    assert "押すとその区画が消える" in hint
    assert "Shift" in hint
    assert str(window.state.wand_tolerance) in hint, "今の許容差も出る"
    assert "もう一度、メニュー「切り抜き」を押すと解除" in hint, (
        "出口は道具の名前で言う。どの項目のことか書いていないと辿れない"
    )


def test_道具を持ち替えれば案内も消える(window_with_image):
    window = window_with_image
    window.state.set_tool(TOOL_SELECT)
    window._refresh_hint()
    assert "切り抜き中" not in window.hint_label.text()
