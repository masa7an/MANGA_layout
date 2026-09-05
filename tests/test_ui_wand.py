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
from manga_layout.layout import image_at
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


def plain_png() -> bytes:
    """一様な白だけの絵。**1回押すと全部が選ばれる**（漏れた疑いの側を通す）。"""
    image = QImage(IMAGE_PX[0], IMAGE_PX[1], QImage.Format.Format_ARGB32)
    image.fill(QColor("#FFFFFF"))
    return to_png_bytes(image)


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


class Test案内の言い方:
    """**履歴に積む名前と、案内の言い方を分ける**（2026-09-05 に直した）。

    履歴は辞書形で並ぶ（→ Undo の一覧）ので、その名前に「ました」を足すと
    **「押した所を消すました」**になる。押すたびに実際にそう出ていた。
    """

    def 拾う(self, window):
        said = []
        window.state.message.connect(said.append)
        return said

    def test_消したときの言い方(self, window_with_image):
        said = self.拾う(window_with_image)
        click(window_with_image, 0.5, 0.5)
        assert said[-1].startswith("押した所を消しました"), said

    def test_残したときの言い方(self, window_with_image):
        said = self.拾う(window_with_image)
        click(window_with_image, 0.5, 0.5, shift=True)
        assert said[-1].startswith("押した所だけ残しました"), said

    def test_漏れた疑いのときも同じ言い方(self, window_with_image):
        """割合が高いと「線に隙間が」を足す。**言い方の作りは変わらない。**"""
        state = window_with_image.state
        state.place_image(state.page.panels[0].id, plain_png())  # 一様な白
        said = self.拾う(window_with_image)
        click(window_with_image, 0.5, 0.5)
        assert said[-1].startswith("押した所を消しました"), said
        assert "線に隙間" in said[-1]

    def test_履歴の名前は辞書形のまま(self, window_with_image):
        """案内を直したついでに、履歴の名前まで変えない。"""
        click(window_with_image, 0.5, 0.5)
        assert window_with_image.state.history.undo_label == "押した所を消す"


class Test何も変わらなかったとき:
    """**変わっていないなら、変わったとは言わない**（2026-09-05 に直した）。

    既にそうなっている所を押すと、マスクの中身は押す前と同じになる。
    履歴には積まれない（`History.commit` が変化の無い手を弾く）のに、
    案内だけが「消しました」と出ていた。
    """

    def test_同じ所をもう一度残しても_変わったとは言わない(self, window_with_image):
        state = window_with_image.state
        click(window_with_image, 0.5, 0.5, shift=True)
        said = []
        state.message.connect(said.append)
        click(window_with_image, 0.5, 0.5, shift=True)

        assert said[-1] == "もう、そこだけが残っています", said
        state.undo()
        assert image_of(window_with_image).mask_asset == "", "積まれた1手は1つだけ"

    def test_消す側も同じ判断をする(self, window_with_image):
        """画面からは届きにくい（消えた所は下の絵へ抜ける → `Test重なった絵`）。

        **道具の入口を変えても、この判断は state 側に残す。** 消えた所を
        押す経路は、道具の作り次第でまた生まれる。
        """
        state = window_with_image.state
        image_id = image_of(window_with_image).id
        assert state.erase_region_at(image_id, (60, 60)) is True

        said = []
        state.message.connect(said.append)
        assert state.erase_region_at(image_id, (60, 60)) is False
        assert said[-1] == "そこはもう消えています", said


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


class Test重なった絵:
    """**切り抜いて透けた所を押したら、下の絵が対象になる**（2026-09-05 に直した）。

    背景の上にキャラを重ねる作り（→ `EditorState.replace_image` の注記）で、
    キャラを切り抜いたあと、透けて見えている背景を切り抜けなかった。
    """

    @pytest.fixture
    def 二枚重ね(self, window_with_image):
        """同じ場所に、同じ大きさでもう1枚重ねる。**後から置いたほうが手前。**"""
        panel_id = window_with_image.state.page.panels[0].id
        window_with_image.state.place_image(panel_id, boxed_png())
        return window_with_image

    def 手前と奥(self, window):
        並び = sorted(window.state.page.panels[0].children, key=lambda i: i.z)
        return 並び[-1].id, 並び[0].id

    def mask_ref(self, window, image_id):
        return window.state.page.find(image_id).mask_asset

    def test_1回目は手前の絵が消える(self, 二枚重ね):
        手前, 奥 = self.手前と奥(二枚重ね)
        click(二枚重ね, 0.5, 0.5)
        assert self.mask_ref(二枚重ね, 手前) != ""
        assert self.mask_ref(二枚重ね, 奥) == "", "奥はまだ触られていない"

    def test_透けた所をもう一度押すと奥の絵が消える(self, 二枚重ね):
        手前, 奥 = self.手前と奥(二枚重ね)
        click(二枚重ね, 0.5, 0.5)  # 手前の中を消す
        前の手前 = self.mask_ref(二枚重ね, 手前)
        click(二枚重ね, 0.5, 0.5)  # 同じ所——手前は消えているので奥へ抜ける

        assert self.mask_ref(二枚重ね, 奥) != "", "奥の絵が対象になる"
        assert self.mask_ref(二枚重ね, 手前) == 前の手前, "手前は変わらない"

    def test_残っている所を押せば手前のまま(self, 二枚重ね):
        """**素通りするのは消えた所だけ。** 残っている所は今までどおり手前が対象。"""
        手前, 奥 = self.手前と奥(二枚重ね)
        click(二枚重ね, 0.5, 0.5)  # 中を消す
        click(二枚重ね, 0.05, 0.05)  # 外——手前にまだ残っている
        assert self.mask_ref(二枚重ね, 奥) == ""

    def test_効いていないマスクでは素通りしない(self, 二枚重ね):
        """実体の欠けたマスクは**画面では効いていない**。押せる所と見える所を揃える。"""
        手前, 奥 = self.手前と奥(二枚重ね)
        click(二枚重ね, 0.5, 0.5)
        state = 二枚重ね.state
        AssetStore(state.project_dir).resolve(self.mask_ref(二枚重ね, 手前)).unlink()
        click(二枚重ね, 0.5, 0.5)
        assert self.mask_ref(二枚重ね, 奥) == "", "切り抜き前の絵が出ているので、手前が対象"

    def test_下に絵が無ければ理由を言う(self, window_with_image):
        """1枚だけの絵を消した所を押した場合。**絵が無いのとは案内を分ける。**"""
        said = []
        window_with_image.state.message.connect(said.append)
        click(window_with_image, 0.5, 0.5)
        click(window_with_image, 0.5, 0.5)
        assert "切り抜いてあります" in said[-1], said

    def test_選ぶほうは今までどおり手前(self, 二枚重ね):
        """**素通りさせるのは切り抜きの道具だけ。**

        選ぶ側まで同じにすると、マスクが絵をまるごと消したときに掴む手立てが
        無くなる（→ `layout.image_at` の注記）。
        """
        手前, _ = self.手前と奥(二枚重ね)
        click(二枚重ね, 0.5, 0.5)
        page = 二枚重ね.state.page
        image = page.panels[0].children[0]
        x = image.rect.x + image.rect.w * 0.5
        y = image.rect.y + image.rect.h * 0.5
        assert image_at(page.panels[0], x, y).id == 手前


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
