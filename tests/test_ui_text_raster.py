"""セリフを画像に焼く（ラスタライズ）の検証（要件定義 6.34）。

**焼くと文字に戻せない**ので、ここで押さえるのは主に3つ。

- **焼く前に止まるところ**。字の無いセリフを焼かせない（完全に透明な、
  見えない・掴めない・消せないものがページに残る）
- **焼いた結果が、画面で見えていたものと同じ範囲を含むこと**。セリフは
  枠からはみ出した字も隠さずに出す作りなので（→ 6.5）、焼くときだけ
  切れてはいけない
- **1手にまとまっていること**。消すのと置くのが別の手だと、Undo を1回
  押したときに「セリフも画像も無い」状態が現れる

焼いた画像はマークとして置かれる（→ 6.14）。回せることの検証は
tests/test_ui_sticker.py の `Testマークの回転`。
"""

from __future__ import annotations

import pytest

from manga_layout import Rect
from manga_layout.model import STICKER_KIND_TEXT, StickerObject, TextObject
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state_text import STICKER_KIND_LABELS
from manga_layout.ui.text_raster import RASTER_MAX_PX, RASTER_SCALE, rasterize

PANEL = Rect(100.0, 100.0, 600.0, 500.0)
# 既定の枠（230×422）に近い縦長。焼くと字の並びまで縮むことを見たいので、
# 数文字しか入れない
FRAME = Rect(700.0, 200.0, 230.0, 422.0)


@pytest.fixture
def window(qapp, tmp_path):
    """保存先を持たせておく。焼いた PNG は `assets/` へ入る経路を通る。"""
    win = MainWindow(EditorState())
    win.state.save(tmp_path / "作品")
    with win.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    win.state.select(None)
    yield win
    win.state.history.mark_saved()
    win.close()


def put_text(window, content: str = "ぼそっ", rect: Rect = FRAME) -> TextObject:
    return window.state.add_text(rect, content)


def stickers(window) -> list[StickerObject]:
    return [f for f in window.state.page.floating if isinstance(f, StickerObject)]


def texts(window) -> list[TextObject]:
    return [f for f in window.state.page.floating if isinstance(f, TextObject)]


class Test焼く処理:
    """`text_raster.rasterize` そのもの。置き換えは下の `Test置き換え`。"""

    def test_縦書きも横書きもPNGになる(self, window):
        for direction in ("vertical", "horizontal"):
            text = TextObject(
                id="txt_0001", content="ぼそっ", rect=FRAME, direction=direction
            )
            baked = rasterize(window.state, text)

            assert baked is not None
            data, src_px, placed = baked
            # PNG の署名。**形式まで見る**——`assets/` は中身で名前を決めるので、
            # 別形式が混ざっても名前からは気づけない
            assert data[:8] == b"\x89PNG\r\n\x1a\n"
            assert src_px[0] > 0 and src_px[1] > 0
            assert placed.w > 0.0 and placed.h > 0.0

    def test_字の無いセリフは焼かない(self, window):
        """完全に透明な画像を作らない（→ `can_rasterize_text`）。"""
        for content in ("", "   ", "\n"):
            text = TextObject(id="txt_0001", content=content, rect=FRAME)
            assert rasterize(window.state, text) is None

    def test_余白を落として字に密着させる(self, window):
        """当たり判定が字に付き、回転の中心も字の中心に来る（→ 6.14 と同じ）。"""
        text = TextObject(
            id="txt_0001", content="あ", rect=FRAME, direction="vertical"
        )
        _, _, placed = rasterize(window.state, text)

        assert placed.w < FRAME.w
        assert placed.h < FRAME.h
        # 枠の中に収まっている（字の位置は寄せに従うので、範囲だけ見る）
        assert FRAME.x <= placed.x and placed.right <= FRAME.right

    def test_指定した倍率で焼く(self, window):
        """あとから拡大してもぼけないように、原寸より細かく焼く（→ 6.7）。"""
        text = TextObject(id="txt_0001", content="あい", rect=FRAME)
        _, src_px, placed = rasterize(window.state, text)

        assert src_px[0] == pytest.approx(placed.w * RASTER_SCALE, abs=2.0)
        assert src_px[1] == pytest.approx(placed.h * RASTER_SCALE, abs=2.0)

    def test_枠からはみ出した字も切れない(self, window):
        """枠をそのまま描き紙にすると、焼くときだけ字が切れる（→ 6.5）。"""
        narrow = Rect(100.0, 100.0, 60.0, 40.0)
        text = TextObject(
            id="txt_0001",
            content="はみ出すほど長い横書きのセリフ",
            rect=narrow,
            direction="horizontal",
        )
        _, _, placed = rasterize(window.state, text)

        assert placed.w > narrow.w

    def test_大きすぎるセリフでも上限で止まる(self, window):
        """何千万画素の PNG を作らない。**倍率だけ落とし、範囲は削らない**。"""
        from manga_layout.model import Font

        text = TextObject(
            id="txt_0001",
            content="大",
            rect=Rect(0.0, 0.0, 2000.0, 2000.0),
            font=Font(size_px=1800.0),
        )
        _, src_px, _ = rasterize(window.state, text)

        assert max(src_px) <= RASTER_MAX_PX


class Test置き換え:
    def test_セリフが消えてマークになる(self, window):
        text = put_text(window)
        sticker = window.state.rasterize_text(text.id)

        assert texts(window) == []
        assert stickers(window) == [sticker]
        assert sticker.kind == STICKER_KIND_TEXT

    def test_焼いたものが選ばれる(self, window):
        """続けて回すのが目的なので、選び直させない。"""
        text = put_text(window)
        sticker = window.state.rasterize_text(text.id)

        assert window.state.selected_id == sticker.id
        assert window.state.selected_rotatable is sticker

    def test_実体がassetsに入る(self, window):
        text = put_text(window)
        sticker = window.state.rasterize_text(text.id)

        assert (window.state.project_dir / sticker.asset).exists()

    def test_Undoは1回で戻る(self, window):
        """消すのと置くのが別の手だと、途中に「どちらも無い」状態が現れる。"""
        text = put_text(window, "ぼそっ")
        window.state.rasterize_text(text.id)
        window.state.undo()

        assert stickers(window) == []
        assert [t.content for t in texts(window)] == ["ぼそっ"]

    def test_字の無いセリフは焼かない(self, window):
        text = put_text(window, "   ")

        assert window.state.can_rasterize_text(text) is False
        assert window.state.rasterize_text(text.id) is None
        assert texts(window) == [text]

    def test_保存して読み直しても残る(self, window, tmp_path):
        from manga_layout.storage import load_project

        text = put_text(window)
        sticker = window.state.rasterize_text(text.id)
        window.state.save(window.state.project_dir)

        again = load_project(window.state.project_dir)
        restored = again.pages[0].floating
        assert [f.id for f in restored] == [sticker.id]
        assert restored[0].kind == STICKER_KIND_TEXT

    def test_呼び名は文字画像(self, window):
        """本物のマークと同じページに並ぶので、削除や状態表示で見分けが付く。"""
        from manga_layout.ui.state import object_label

        text = put_text(window)
        sticker = window.state.rasterize_text(text.id)

        assert object_label(sticker) == "文字画像"


class Test動線:
    def test_字があれば押せる(self, window):
        put_text(window)
        window.text_menu.refresh()

        assert window.text_menu.rasterize_action.isEnabled()

    def test_字が無ければ押せない(self, window):
        """押せるが何も起きない、にしない（→ 6.17 と同じ線引き）。"""
        put_text(window, "   ")
        window.text_menu.refresh()

        assert not window.text_menu.rasterize_action.isEnabled()

    def test_セリフを選んでいなければ押せない(self, window):
        window.state.select(None)
        window.text_menu.refresh()

        assert not window.text_menu.rasterize_action.isEnabled()

    def test_右クリックにも出る(self, window):
        text = put_text(window)
        window.state.select(text.id)
        menu = window.context_menu.build(*FRAME.center)

        assert window.text_menu.rasterize_action in menu.actions()
        menu.deleteLater()

    def test_打ち直せないことを知らせる(self, window):
        """確認の窓は出さない代わりに、何が起きたかはその場で1回出す。"""
        seen: list[str] = []
        window.state.message.connect(seen.append)
        put_text(window)
        window.rasterize_text()

        assert any("打ち直し" in message for message in seen)

    def test_置く一覧には出さない(self, window):
        """**呼び名の表と、置ける素材の一覧は別物**（2026-09-06 に直した）。

        「文字画像」は焼いてできるもので、対応する素材の PNG が無い。
        一覧に出すと、押した瞬間に「知らないマークの種類です」で断られる。
        """
        window.state.select(None)
        menu = window.context_menu.build(*PANEL.center)
        labels = [action.text() for action in menu.actions()]

        assert not any(
            STICKER_KIND_LABELS[STICKER_KIND_TEXT] in label for label in labels
        )
        menu.deleteLater()



@pytest.fixture
def state(qapp, tmp_path):
    """窓を出さない編集状態。

    **描くときの縮小は、窓を出すと数えられない。** メインウィンドウを作ると
    ページ一覧のサムネイルが別の大きさで同じページを描くので、写しが2つに
    なる（それ自体は正しい動き）。ここで見たいのは「1回描くと1つできる」
    ほうなので、描き手だけを使う。
    """
    st = EditorState()
    st.save(tmp_path / "作品")
    with st.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    st.select(None)
    return st


def render(state):
    from manga_layout.ui.export import render_page

    return render_page(state, state.page)


class Test描くときの縮小:
    """**文字画像だけ、先に縮めてから描く**（→ 6.34、PySide6の落とし穴 10）。

    `QPainter` の縮小は 2×2 の画素しか見ないので、4:1 では間引きになり、
    細い線の縁が階段状になる。ここで見るのは「その道を通っているか」で、
    見え方そのものは実機と書き出した1枚で確かめる。
    """

    def test_文字画像では先に縮める(self, state):
        text = state.add_text(FRAME, "ぼそっ")
        state.rasterize_text(text.id)
        render(state)

        assert len(state.reduced_cache) == 1

    def test_2回描いても作り直さない(self, state):
        text = state.add_text(FRAME, "ぼそっ")
        state.rasterize_text(text.id)
        render(state)
        render(state)

        assert len(state.reduced_cache) == 1

    def test_マークでは先に縮めない(self, state):
        """**文字だけに掛ける**（→ 6.34）。同じくらい縮めても通らないこと。"""
        from manga_layout.stickers import STICKER_EXCLAIM

        sticker = state.add_sticker(STICKER_EXCLAIM, 300.0, 300.0)
        # 素材は 360x360。長辺 100px に置けば 3.6:1 で、境目（2:1）を超える
        with state.edit_page("小さく") as page:
            page.find(sticker.id).rect = Rect(300.0, 300.0, 100.0, 100.0)
        render(state)

        assert len(state.reduced_cache) == 0

    def test_ほぼ等倍なら何もしない(self, state):
        """拡大して置いたときは写しが要らない。作るだけ無駄になる。"""
        text = state.add_text(FRAME, "ぼそっ")
        sticker = state.rasterize_text(text.id)
        with state.edit_page("大きく") as page:
            found = page.find(sticker.id)
            found.rect = Rect(
                found.rect.x, found.rect.y, found.rect.w * 8.0, found.rect.h * 8.0
            )
        render(state)

        assert len(state.reduced_cache) == 0

    def test_見え方が変わる(self, state):
        """**通しても結果が同じ**なら、この仕組みは何もしていないことになる。

        同じ画像を「文字画像」と「それ以外」で置き、描き上がりを比べる。

        **矩形の大きさを小数にしておく。** 整数だと `QPainter` の側も
        正しい縮小をするので差が出ず、**書体が変わるだけでテストの結果が
        変わる**（実際に、画面なしの書体で矩形が整数に落ちて差が消えた）。
        """
        text = state.add_text(FRAME, "ぼそっ")
        sticker = state.rasterize_text(text.id)
        with state.edit_page("小数にする") as page:
            found = page.find(sticker.id)
            found.rect = Rect(300.5, 300.25, 60.5, 90.75)
        rect = state.page.find(sticker.id).rect
        smoothed = render(state)

        # `kind` を変えると `_to_draw` を通らなくなる＝直す前と同じ道
        with state.edit_page("kind を変える") as page:
            page.find(sticker.id).kind = "mark_like"
        state.reduced_cache.clear()
        naive = render(state)

        differ = sum(
            1
            for y in range(int(rect.y), int(rect.bottom))
            for x in range(int(rect.x), int(rect.right))
            if smoothed.pixel(x, y) != naive.pixel(x, y)
        )
        assert differ > 0

    def test_絵を消したら写しも手放す(self, state):
        text = state.add_text(FRAME, "ぼそっ")
        sticker = state.rasterize_text(text.id)
        render(state)
        assert len(state.reduced_cache) == 1

        with state.edit_page("削除") as page:
            page.remove_floating(sticker.id)
        state.forget_if_unused(sticker.asset)

        assert len(state.reduced_cache) == 0
