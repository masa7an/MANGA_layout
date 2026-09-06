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
