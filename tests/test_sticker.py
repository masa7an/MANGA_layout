"""マーク（ビックリマークなど）の検証（要件定義 6.14）。

ここで押さえるのは3つ。

- **保存形式**。往復しても落ちないこと、`kind` が知らない値でも開けること、
  マークを使っていない作品の見た目が変わらないこと
- **置き場所**。ページ直下にあり、コマを動かすと付いてくるが**切り抜かれない**
- **組み込み素材**。実際に画像として展開でき、透明の余白が削れていること

画面まわり（道具・メニュー・当たり判定の取り合い）は tests/test_ui_sticker.py。
"""

from __future__ import annotations

import pytest

from manga_layout import Project, Rect, new_project
from manga_layout.errors import AssetError, ProjectFormatError
from manga_layout.layout import (
    STICKER_DEFAULT_LONG_PX,
    default_sticker_rect,
    outside_page,
    sticker_at,
)
from manga_layout.model import (
    ID_PREFIX_STICKER,
    LAYER_BALLOON,
    LAYER_STICKER,
    LAYER_TEXT,
    StickerObject,
    floating_layer,
    floating_order,
)
from manga_layout.stickers import (
    STICKER_EXCLAIM,
    STICKER_EXCLAIM_QUESTION,
    STICKER_KINDS,
    read_sticker,
    sticker_path,
)

# 素材は縦長（224×297）。縦横比が保たれているかを見るので、正方形にしない
SRC_PX = (224, 297)


@pytest.fixture
def project():
    """コマを1つ持つ作品。"""
    p = new_project(title="マークのテスト")
    p.add_panel(p.pages[0], Rect(100.0, 100.0, 600.0, 500.0))
    return p


def put(project: Project, rect: Rect, *, attached: str | None = None) -> StickerObject:
    return project.add_sticker(
        project.pages[0], STICKER_EXCLAIM, "assets/mark.png", rect, SRC_PX, attached
    )


class Test保存形式:
    def test_往復しても落ちない(self, project):
        sticker = put(project, Rect(200.0, 150.0, 224.0, 297.0), attached="panel_0002")
        restored = Project.from_dict(project.to_dict())
        assert restored.pages[0].floating == [sticker]

    def test_typeはsticker(self, project):
        assert put(project, Rect(0.0, 0.0, 10.0, 10.0)).to_dict()["type"] == "sticker"

    def test_idの接頭辞(self, project):
        assert put(project, Rect(0.0, 0.0, 10.0, 10.0)).id.startswith(
            f"{ID_PREFIX_STICKER}_"
        )

    def test_回転は常に0(self, project):
        """傾きは素材の PNG に焼き込む。アプリでは回さない。"""
        assert put(project, Rect(0.0, 0.0, 10.0, 10.0)).rotation == 0.0

    def test_知らないkindでも開ける(self):
        """素材が増えたあとの作品を古いアプリで開く場面。

        読み込みごと断ると、**開けなくなる**。呼び名が引けないだけの話
        なので、値はそのまま持って開く（→ 5章）。
        """
        data = {
            "id": "stk_0009",
            "type": "sticker",
            "kind": "まだ無い種類",
            "asset": "assets/x.png",
            "rect": {"x": 0.0, "y": 0.0, "w": 10.0, "h": 10.0},
            "src_px": [10, 10],
        }
        assert StickerObject.from_dict(data, "test").kind == "まだ無い種類"

    def test_マークが無い作品のJSONは変わらない(self):
        """段を1つ増やしても、既にある作品の保存内容には出てこない。"""
        before = new_project(title="あ").to_dict()
        after = new_project(title="あ").to_dict()
        assert before == after
        assert before["pages"][0]["floating"] == []

    def test_複製で落ちない(self, project):
        """Undo のスナップショットは保存形式を往復する（→ 6.8）。"""
        put(project, Rect(1.0, 2.0, 30.0, 40.0), attached="panel_0002")
        assert project.copy().pages[0].floating == project.pages[0].floating

    def test_srcpxが数値でなければ場所を示して弾く(self):
        """以前はここだけ生の `int()` に渡していて、`ValueError` が
        `where` の付かないまま漏れていた（2026-08-08 に発見。→ `ImageObject`
        の同種テストと対）。
        """
        data = {
            "id": "stk_0009",
            "type": "sticker",
            "kind": "exclaim",
            "asset": "assets/x.png",
            "rect": {"x": 0.0, "y": 0.0, "w": 10.0, "h": 10.0},
            "src_px": [10, None],
        }
        with pytest.raises(ProjectFormatError) as exc:
            StickerObject.from_dict(data, "test")
        assert "test.src_px" in str(exc.value)


class Test重なりの段:
    def test_フキダシより手前_セリフより奥(self):
        assert LAYER_BALLOON < LAYER_STICKER < LAYER_TEXT

    def test_種類で決まる(self, project):
        page = project.pages[0]
        rect = Rect(0.0, 0.0, 10.0, 10.0)
        text = project.add_text(page, "あ", rect)
        sticker = put(project, rect)
        balloon = project.add_balloon(page, rect)

        # 作った順は セリフ → マーク → フキダシ。z はこの順に大きい
        assert balloon.z > sticker.z > text.z
        # それでも並びは 段 が先に効く
        assert sorted(page.floating, key=floating_order) == [balloon, sticker, text]

    def test_段は保存しない(self, project):
        """段は種類から毎回求める。保存形式に持たない。"""
        sticker = put(project, Rect(0.0, 0.0, 10.0, 10.0))
        assert "layer" not in sticker.to_dict()
        assert floating_layer(sticker) == LAYER_STICKER


class Testコマとの関係:
    def test_コマを動かすと付いてくる(self, project):
        page = project.pages[0]
        panel = page.panels[0]
        sticker = put(project, Rect(200.0, 150.0, 100.0, 100.0), attached=panel.id)

        page.move_panel(panel.id, 30.0, -20.0)
        assert sticker.rect == Rect(230.0, 130.0, 100.0, 100.0)

    def test_紐づけていなければ動かない(self, project):
        page = project.pages[0]
        sticker = put(project, Rect(200.0, 150.0, 100.0, 100.0))
        page.move_panel(page.panels[0].id, 30.0, -20.0)
        assert sticker.rect == Rect(200.0, 150.0, 100.0, 100.0)

    def test_コマを消しても巻き添えにしない(self, project):
        """紐づけだけ外れてページに残る（フキダシ・セリフと同じ）。"""
        page = project.pages[0]
        panel = page.panels[0]
        sticker = put(project, Rect(200.0, 150.0, 100.0, 100.0), attached=panel.id)

        page.remove_panel(panel.id)
        assert page.floating == [sticker]
        assert sticker.attached_panel_id is None

    def test_存在しないコマへの紐づけは読み込みで外す(self, project):
        put(project, Rect(0.0, 0.0, 10.0, 10.0), attached="panel_9999")
        restored = Project.from_dict(project.to_dict())
        assert restored.pages[0].floating[0].attached_panel_id is None
        assert restored.load_warnings

    def test_マークだけ消せる(self, project):
        page = project.pages[0]
        sticker = put(project, Rect(0.0, 0.0, 10.0, 10.0))
        assert page.remove_floating(sticker.id) is sticker
        assert page.floating == []


class Test実体の参照:
    def test_未使用の判定に数える(self, project):
        """数え漏らすと「未使用ファイルを整理」がマークの実体を運び出す。"""
        put(project, Rect(0.0, 0.0, 10.0, 10.0))
        assert "assets/mark.png" in project.referenced_assets()

    def test_コマの中の画像と一緒に数える(self, project):
        panel = project.pages[0].panels[0]
        project.add_image(panel, "assets/photo.png", Rect(0.0, 0.0, 10.0, 10.0), (10, 10))
        put(project, Rect(0.0, 0.0, 10.0, 10.0))
        assert project.referenced_assets() == {"assets/photo.png", "assets/mark.png"}


class Test置いた直後の大きさ:
    def test_縦横比を保つ(self, project):
        rect = default_sticker_rect(project.pages[0], 620.0, 877.0, SRC_PX)
        assert rect.h == pytest.approx(STICKER_DEFAULT_LONG_PX)
        assert rect.w / rect.h == pytest.approx(SRC_PX[0] / SRC_PX[1])

    def test_横長の素材は幅が長辺になる(self, project):
        rect = default_sticker_rect(project.pages[0], 620.0, 877.0, (400, 100))
        assert rect.w == pytest.approx(STICKER_DEFAULT_LONG_PX)

    def test_押した場所が中心(self, project):
        rect = default_sticker_rect(project.pages[0], 620.0, 877.0, SRC_PX)
        assert rect.center == pytest.approx((620.0, 877.0))

    def test_用紙の中へ寄せる(self, project):
        """はみ出したまま作ると、つまみが画面外に出て掴めなくなる。"""
        page = project.pages[0]
        rect = default_sticker_rect(page, 0.0, 0.0, SRC_PX)
        assert rect.x >= 0.0 and rect.y >= 0.0
        assert rect.right <= page.size.w and rect.bottom <= page.size.h

    def test_元の寸法が取れなくても潰れない(self, project):
        rect = default_sticker_rect(project.pages[0], 620.0, 877.0, (0, 0))
        assert rect.w > 0.0 and rect.h > 0.0


class Test当たり判定:
    def test_矩形の中なら当たる(self, project):
        sticker = put(project, Rect(200.0, 150.0, 100.0, 100.0))
        assert sticker_at(project.pages[0], 250.0, 200.0) is sticker

    def test_外れると当たらない(self, project):
        put(project, Rect(200.0, 150.0, 100.0, 100.0))
        assert sticker_at(project.pages[0], 199.0, 200.0) is None

    def test_重なっていれば手前(self, project):
        put(project, Rect(200.0, 150.0, 100.0, 100.0))
        front = put(project, Rect(210.0, 160.0, 100.0, 100.0))
        assert sticker_at(project.pages[0], 250.0, 200.0) is front

    def test_はみ出しに数える(self, project):
        """ページの大きさを変えたあとに知らせる対象（→ 6.1）。"""
        sticker = put(project, Rect(-50.0, 10.0, 100.0, 100.0))
        assert sticker in outside_page(project.pages[0])


class Test組み込み素材:
    """素材そのものの検証。**画像として壊れていないか**まで見る。

    ここが無いと、素材を差し替えたときに「置いたのに何も出ない」まで
    気づけない。ファイルの有無だけでは、中身が空でも通ってしまう。
    """

    def test_2種類ある(self):
        assert STICKER_KINDS == (STICKER_EXCLAIM, STICKER_EXCLAIM_QUESTION)

    @pytest.mark.parametrize("kind", STICKER_KINDS)
    def test_ファイルがある(self, kind):
        assert sticker_path(kind).is_file()

    @pytest.mark.parametrize("kind", STICKER_KINDS)
    def test_画像として展開できる(self, kind, qapp):
        from manga_layout.images import decode

        image = decode(read_sticker(kind))
        assert image.width() > 0 and image.height() > 0

    @pytest.mark.parametrize("kind", STICKER_KINDS)
    def test_透明を持つ(self, kind, qapp):
        """背景が透明でないと、コマの上に白い四角が乗る。"""
        from manga_layout.images import decode

        assert decode(read_sticker(kind)).hasAlphaChannel()

    @pytest.mark.parametrize("kind", STICKER_KINDS)
    def test_透明の余白が削れている(self, kind, qapp):
        """四辺それぞれに、透明でない画素が1つ以上接していること。

        余白が残っていると、2種の見かけの大きさが揃わず、当たり判定の
        矩形も記号から離れる（→ 6.14）。取り込みは
        `tools/import_sticker.py` が削る。**削り忘れをここで止める。**
        """
        from PySide6.QtGui import QImage

        from manga_layout.images import decode

        image = decode(read_sticker(kind)).convertToFormat(
            QImage.Format.Format_ARGB32
        )
        w, h = image.width(), image.height()

        def opaque(x: int, y: int) -> bool:
            return bool((image.pixel(x, y) >> 24) & 0xFF)

        assert any(opaque(x, 0) for x in range(w)), "上に余白が残っている"
        assert any(opaque(x, h - 1) for x in range(w)), "下に余白が残っている"
        assert any(opaque(0, y) for y in range(h)), "左に余白が残っている"
        assert any(opaque(w - 1, y) for y in range(h)), "右に余白が残っている"

    def test_知らない種類は断る(self):
        """黙って何も置かないと、利用者には理由が分からない。"""
        with pytest.raises(AssetError):
            read_sticker("no_such_mark")
