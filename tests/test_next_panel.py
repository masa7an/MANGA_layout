"""ページと定石の橋渡しの検証（→ `next_panel.py`）。

定石そのものは `test_joseki.py` で見ている。ここで押さえるのは**つなぎ目**。

- **読み順。** `page.panels` は重なり順であって読む順ではない
- **座標。** px と正規化のあいだを往復しても値が変わらないこと
- **ページの文脈。** 番号と直前のページ（見開きの定石が使う）
- **反映。** 提案どおりにコマが増え、枠線が既にあるコマから写ること
- **対象外の断り。** 斜めのコマがあるページには出さないこと

Qt は起動しない。ここまでは画面と関係なく確かめられる。
"""

from __future__ import annotations

import pytest

from manga_layout import Polygon, Rect, Size, new_project
from manga_layout.layout import LayoutSettings
from manga_layout.next_panel import (
    BLEED_OVERFLOW,
    add_suggestion,
    context_for,
    guide_frame,
    is_rectangular,
    reading_order,
    suggestions,
    supported,
    to_boxes,
    to_rect,
)

# A4（1240x1754）を前提にした px の矩形。ページ寸法の既定と同じ
PAGE_W, PAGE_H = 1240.0, 1754.0


def page_with(*rects):
    project = new_project()
    page = project.pages[0]
    for rect in rects:
        project.add_panel(page, rect)
    return project, page


def band(top, height, right_x=0.52, right_w=0.42, left_x=0.06, left_w=0.42):
    """左右2コマの段。既定では左右の幅が揃う。"""
    return (
        Rect(right_x * PAGE_W, top * PAGE_H, right_w * PAGE_W, height * PAGE_H),
        Rect(left_x * PAGE_W, top * PAGE_H, left_w * PAGE_W, height * PAGE_H),
    )


def reading_positions(page):
    """読み順に並べた (x, y) を、ページ比で返す。"""
    return [
        (round(p.bounds().x / PAGE_W, 2), round(p.bounds().y / PAGE_H, 2))
        for p in reading_order(page.panels)
    ]


class TestReadingOrder:
    def test_ふつうのページは行優先(self):
        # 段の中は右から左。段は上から下
        project, page = page_with(*band(0.06, 0.28), *band(0.38, 0.28))
        ordered = reading_order(page.panels)
        xs = [p.bounds().x for p in ordered]
        assert xs == pytest.approx(
            [0.52 * PAGE_W, 0.06 * PAGE_W, 0.52 * PAGE_W, 0.06 * PAGE_W]
        )

    def test_縦4コマ列2本は列優先(self):
        # 右列を上から下、続いて左列を上から下。**慣習で決まっている**
        rects = []
        for top in (0.05, 0.29, 0.53, 0.77):
            rects.extend(band(top, 0.20))
        project, page = page_with(*rects)
        ordered = reading_order(page.panels)
        xs = [round(p.bounds().x) for p in ordered]
        right, left = round(0.52 * PAGE_W), round(0.06 * PAGE_W)
        assert xs == [right] * 4 + [left] * 4

    def test_段ごとに幅が違う3段ページは行優先(self):
        # ページを貫く縦の切れ目が無い。**ふつうの漫画のページはこちら**
        _project, page = page_with(
            *band(0.06, 0.28, 0.38, 0.56, 0.06, 0.30),
            *band(0.38, 0.22, 0.54, 0.40, 0.06, 0.46),
            *band(0.64, 0.30, 0.46, 0.48, 0.06, 0.38),
        )
        assert reading_positions(page) == [
            (0.38, 0.06), (0.06, 0.06),
            (0.54, 0.38), (0.06, 0.38),
            (0.46, 0.64), (0.06, 0.64),
        ]

    def test_列がぴったり揃う3段ページも行優先(self):
        # **列優先は4コマのページだけの読み方。** 3段には広げない。
        # 左右の列が縦に切れていても、ふつうの漫画のページとして読む
        _project, page = page_with(
            *band(0.06, 0.28), *band(0.38, 0.22), *band(0.64, 0.30)
        )
        assert [y for _x, y in reading_positions(page)] == [
            0.06, 0.06, 0.38, 0.38, 0.64, 0.64
        ]

    def test_コマが無ければ空(self):
        _project, page = page_with()
        assert reading_order(page.panels) == []

    def test_高さが0のコマがあっても数え落とさない(self):
        # **高さ 0 のコマは、重なりの判定では自分自身とすら重ならない。**
        # 段に入れ損ねると数が減らず、**ここは落ちるのではなく永久に回る**
        _project, page = page_with(
            Rect(0.06 * PAGE_W, 0.06 * PAGE_H, 0.88 * PAGE_W, 0.0),
            Rect(0.06 * PAGE_W, 0.40 * PAGE_H, 0.88 * PAGE_W, 0.30 * PAGE_H),
        )
        assert len(reading_order(page.panels)) == 2

    def test_面積が0のコマだけのページも並べ替えられる(self):
        # 空白ページへの提案が置きうる形（幅も高さも 0）
        _project, page = page_with(Rect(0.5 * PAGE_W, 0.5 * PAGE_H, 0.0, 0.0))
        assert len(reading_order(page.panels)) == 1


class TestCoordinates:
    def test_往復しても値が変わらない(self):
        rect = Rect(100.0, 200.0, 300.0, 400.0)
        _project, page = page_with(rect)
        box = to_boxes(page)[0]
        back = to_rect(box, page)
        assert (back.x, back.y, back.w, back.h) == pytest.approx(
            (rect.x, rect.y, rect.w, rect.h)
        )

    def test_正規化はページ寸法で割った値(self):
        _project, page = page_with(Rect(0.0, 0.0, PAGE_W / 2, PAGE_H / 4))
        box = to_boxes(page)[0]
        assert (box.x, box.y, box.w, box.h) == pytest.approx((0.0, 0.0, 0.5, 0.25))


class TestContext:
    def test_1ページ目には直前が無い(self):
        project, page = page_with(*band(0.06, 0.28))
        ctx = context_for(project, page)
        assert ctx.number == 1
        assert ctx.previous is None

    def test_2ページ目は直前のコマを持つ(self):
        project, first = page_with(*band(0.06, 0.28))
        second = project.add_page()
        ctx = context_for(project, second)
        assert ctx.number == 2
        assert ctx.previous is not None
        assert ctx.previous.number == 1
        assert len(ctx.previous.boxes) == len(first.panels)


class TestSupported:
    def test_矩形のコマは対象(self):
        _project, page = page_with(*band(0.06, 0.28))
        assert supported(page)
        assert all(is_rectangular(p) for p in page.panels)

    def test_斜めのコマがあるページは対象外(self):
        # 定石は矩形を前提にしている。`split_panel` が斜めを断るのと同じ線引き
        _project, page = page_with(*band(0.06, 0.28))
        page.panels[0].shape = Polygon(
            ((100.0, 100.0), (400.0, 140.0), (400.0, 500.0), (100.0, 460.0))
        )
        assert not is_rectangular(page.panels[0])
        assert not supported(page)


class TestSuggestions:
    def test_描きかけのページには提案が出る(self):
        project, page = page_with(*band(0.06, 0.28))
        found = suggestions(project, page)
        assert found
        assert all(s.rects for s in found)

    def test_埋まったページには出さない(self):
        rects = []
        for top, height in ((0.06, 0.28), (0.38, 0.22), (0.64, 0.30)):
            rects.extend(band(top, height))
        project, page = page_with(*rects)
        assert suggestions(project, page) == []

    def test_斜めのコマがあれば出さない(self):
        project, page = page_with(*band(0.06, 0.28))
        page.panels[0].shape = Polygon(
            ((100.0, 100.0), (400.0, 140.0), (400.0, 500.0), (100.0, 460.0))
        )
        assert suggestions(project, page) == []

    def test_提案はページの中に収まる(self):
        # 描きかけのページへの提案は、どれもページの中。
        # **外へ出るのは空白ページの断ち切りの案だけ**（別に確かめている）
        project, page = page_with(*band(0.06, 0.28))
        for suggestion in suggestions(project, page):
            for rect in suggestion.rects:
                assert rect.x >= -0.5
                assert rect.y >= -0.5
                assert rect.right <= PAGE_W + 0.5
                assert rect.bottom <= PAGE_H + 0.5

    def test_見出しは定石と案の名前(self):
        project, page = page_with(*band(0.06, 0.28))
        text = suggestions(project, page)[0].text()
        assert "/" in text or text


class TestGuideFrame:
    """基本枠（`LayoutSettings.margin` の内側）。**空白ページの置き場所に効く。**"""

    def test_pxの余白は縦横で違う比になる(self):
        # A4 は縦長なので、同じ 89px でも横 0.072・縦 0.051。
        # **換算せずに1つの値を使うと、枠からはみ出す**
        _project, page = page_with()
        frame = guide_frame(page, 89.0)
        assert frame.x == pytest.approx(89.0 / PAGE_W)
        assert frame.y == pytest.approx(89.0 / PAGE_H)
        assert frame.right == pytest.approx(1.0 - 89.0 / PAGE_W)
        assert frame.bottom == pytest.approx(1.0 - 89.0 / PAGE_H)

    def test_余白が無ければ枠も無い(self):
        _project, page = page_with()
        assert guide_frame(page, 0.0) is None

    def test_空白ページの提案は基本枠にぴったり付く(self):
        margin = LayoutSettings().margin
        project, page = page_with()
        rect = suggestions(project, page, margin=margin)[0].rects[0]
        assert rect.y == pytest.approx(margin)                      # 枠の上辺
        assert rect.right == pytest.approx(PAGE_W - margin)         # 枠の右辺
        assert rect.bottom <= PAGE_H - margin + 0.5                 # 枠の下辺より内側

    def test_幅ちがいのどの案も枠に収まる(self):
        margin = LayoutSettings().margin
        project, page = page_with()
        found = [
            s for s in suggestions(project, page, margin=margin)
            if "断ち切り" not in s.label            # 断ち切りは枠の外へ出るのが仕事
        ]
        assert len(found) > 1
        for suggestion in found:
            for rect in suggestion.rects:
                assert rect.x >= margin - 0.5
                assert rect.right <= PAGE_W - margin + 0.5
                assert rect.bottom <= PAGE_H - margin + 0.5

    def test_断ち切りの案は上と右がページの外へ出る(self):
        # **端ちょうどで止めない。** 断裁のずれで白い筋が出る。
        # 越える量は線が出ない分だけでよく、**多いほど良いものではない**
        margin = LayoutSettings().margin
        project, page = page_with()
        rect = next(
            s for s in suggestions(project, page, margin=margin)
            if "断ち切り" in s.label
        ).rects[0]
        assert rect.y == pytest.approx(-BLEED_OVERFLOW)
        assert rect.right == pytest.approx(PAGE_W + BLEED_OVERFLOW)
        assert rect.x > 0                                  # 左と下は紙の中
        assert rect.bottom <= PAGE_H - margin + 0.5

    def test_紙に残るのはページの右半分(self):
        # 越えた分は切り落とされる。**残る側が半分**になるように左辺を取る
        margin = LayoutSettings().margin
        project, page = page_with()
        rect = next(
            s for s in suggestions(project, page, margin=margin)
            if "断ち切り" in s.label
        ).rects[0]
        assert PAGE_W - rect.x == pytest.approx(PAGE_W / 2)

    def test_断ち切りコマの次の提案は枠の中に収める(self):
        # **断ち切りから余白を借りない。** 借りると枠がページいっぱいに広がり、
        # 次のコマまで断ち切りになる（断ち切りが続くページは多くない）
        margin = LayoutSettings().margin
        project, page = page_with(Rect(620.0, -10.0, 630.0, 624.3))   # 上と右を断ち切り
        found = suggestions(project, page, margin=margin, gutter=LayoutSettings().gutter)
        assert found
        for suggestion in found:
            for rect in suggestion.rects:
                assert rect.x >= margin - 0.5
                assert rect.y >= margin - 0.5
                assert rect.right <= PAGE_W - margin + 0.5
                assert rect.bottom <= PAGE_H - margin + 0.5

    def test_最下段の案に幅いっぱいがある(self):
        """3段のうち2段を埋めた状態。**最後の段は帯まるごとにもできる。**

        **左右は既存コマに揃う。基本枠ではない。** この材料は `band()` が作る形で、
        左端 74.4px・右端 1165.6px と**基本枠（89〜1151）の 14.6px 外側**にある。
        断ち切りではないので、余白は既存コマから借りる（→ `joseki.borrow_frame`）。
        以前はここに `settings.margin` を書いていたが、それは**借りた余白を基本枠が
        上書きした値**だった（2026-09-05 修正）。
        """
        settings = LayoutSettings()
        project, page = page_with(*band(0.06, 0.28), *band(0.38, 0.22))
        found = suggestions(project, page, margin=settings.margin,
                            gutter=settings.gutter)
        widest = next(s for s in found if s.label == "幅 いっぱい")
        rect = widest.rects[0]
        assert rect.x == pytest.approx(min(p.bounds().x for p in page.panels))
        assert rect.right == pytest.approx(max(p.bounds().right for p in page.panels))

    def test_枠の外に描いたページでも既存コマの余白を使う(self):
        # **断ち切りでなければ、枠の外に描かれていても借りる。** 余白 40px で
        # 描いたページに、アプリの余白 89px を押し付けない
        project, page = page_with(
            Rect(640.0, 40.0, 560.0, 460.0), Rect(40.0, 40.0, 560.0, 460.0)
        )
        found = suggestions(project, page, margin=LayoutSettings().margin, gutter=35.0)
        assert found
        for suggestion in found:
            for rect in suggestion.rects:
                assert rect.right <= 1200.0 + 0.5      # 既存コマの右端
                assert rect.x >= 40.0 - 0.5            # その鏡写しの左端
        widest = next(s for s in found if s.label == "幅 いっぱい")
        assert widest.rects[0].right == pytest.approx(1200.0)
        assert widest.rects[0].x == pytest.approx(40.0)

    def test_段の左隣は既存コマの上端と高さに揃う(self):
        # 上端を基本枠で挟むと、右コマ（y=40）と左コマ（y=89）で段がずれる
        project, page = page_with(Rect(640.0, 40.0, 560.0, 460.0))
        found = suggestions(project, page, margin=LayoutSettings().margin, gutter=35.0)
        left = next(s for s in found if "同じ段の左" in s.title)
        for rect in left.rects:
            assert rect.y == pytest.approx(40.0)
            assert rect.h == pytest.approx(460.0)

    def test_断ち切りページは今までどおり枠に収める(self):
        # **借りた辺そのものが断ち切りのときだけ**、置いてよい範囲へ落ちる
        margin = LayoutSettings().margin
        project, page = page_with(Rect(620.0, -10.0, 630.0, 624.3))
        found = suggestions(project, page, margin=margin, gutter=LayoutSettings().gutter)
        assert found
        for suggestion in found:
            for rect in suggestion.rects:
                assert rect.x >= margin - 0.5
                assert rect.right <= PAGE_W - margin + 0.5

    def test_細い断ち切りコマの次の提案も枠の中に収める(self):
        """幅 1/3 以下の断ち切りコマがあるページ。**どの案も紙の外へ出さない。**

        `test_断ち切りコマの次の提案は枠の中に収める` は幅 0.508 のコマで試して
        おり、**幅 1/3 以下でしか当たらない「細い右上コマの左隣」を通らない。**
        その定石だけ上端と高さを写したままで、y=-10px の案が最初に出ていた
        （2026-09-05 修正）。
        """
        margin = LayoutSettings().margin
        project, page = page_with(Rect(920.0, -10.0, 330.0, 950.0))
        found = suggestions(project, page, margin=margin, gutter=LayoutSettings().gutter)
        assert any("細い右上" in s.title for s in found)
        for suggestion in found:
            for rect in suggestion.rects:
                assert rect.x >= margin - 0.5
                assert rect.y >= margin - 0.5
                assert rect.right <= PAGE_W - margin + 0.5
                assert rect.bottom <= PAGE_H - margin + 0.5

    def test_雛形は最後に出す(self):
        margin = LayoutSettings().margin
        project, page = page_with()
        labels = [s.label for s in suggestions(project, page, margin=margin)]
        assert labels[-1] == "4コマ×2列"

    def test_4コマの雛形を置くと列優先で読まれる(self):
        # **提案した順と、置いたあとに読み直した順が一致する。**
        # 食い違うと、次の提案が別のコマを「最後のコマ」とみなしてしまう
        settings = LayoutSettings()
        project, page = page_with()
        grid = next(
            s
            for s in suggestions(project, page, margin=settings.margin,
                                 gutter=settings.gutter)
            if s.label == "4コマ×2列"
        )
        added = add_suggestion(project, page, grid)
        assert reading_order(page.panels) == added

    def test_余白を渡さなければ枠に合わせない(self):
        # 既定の余白で置く。**枠を知らないのだから、合わせようがない**
        project, page = page_with()
        rect = suggestions(project, page)[0].rects[0]
        assert rect.y < LayoutSettings().margin

    def test_どのページ寸法でも面積の無いコマを提案しない(self):
        # 小さいページでは余白が幅・高さの半分に届き、**基本枠が潰れる。**
        # 面積の無いコマは何とも重ならないので、重なりの判定では捕まらない
        margin = LayoutSettings().margin
        for w, h in ((1240.0, 1754.0), (1075.0, 1518.0), (400.0, 400.0),
                     (178.0, 178.0), (180.0, 300.0)):
            project, page = page_with()
            page.size = Size(w, h)
            for found in suggestions(project, page, margin=margin, gutter=35.0):
                for rect in found.rects:
                    assert rect.w > 0 and rect.h > 0, f"{w}x{h}: {found.text()}"

    def test_枠が潰れるページでも断ち切りの案は残る(self):
        # 断ち切りは枠を使わずページの外まで伸ばす。**潰れても作れる**
        project, page = page_with()
        page.size = Size(178.0, 178.0)
        found = suggestions(project, page, margin=LayoutSettings().margin, gutter=35.0)
        assert [s.label for s in found] == [
            "上と右を断ち切り（幅 1/2）", "上半分を断ち切り",
        ]


class TestAddSuggestion:
    def test_提案どおりにコマが増える(self):
        project, page = page_with(*band(0.06, 0.28))
        first = suggestions(project, page)[0]
        before = len(page.panels)
        added = add_suggestion(project, page, first)
        assert len(page.panels) == before + len(first.rects)
        assert len(added) == len(first.rects)
        for panel, rect in zip(added, first.rects, strict=True):
            box = panel.bounds()
            assert (box.x, box.y, box.w, box.h) == pytest.approx(
                (rect.x, rect.y, rect.w, rect.h)
            )

    def test_枠線は既にあるコマから写す(self):
        project, page = page_with(*band(0.06, 0.28))
        page.panels[0].border.width = 7.5
        added = add_suggestion(project, page, suggestions(project, page)[0])
        assert added[0].border.width == 7.5
        # 写しであって共有ではない。あとから片方を変えても、もう片方は動かない
        added[0].border.width = 1.0
        assert page.panels[0].border.width == 7.5

    def test_足したコマは手前に来る(self):
        project, page = page_with(*band(0.06, 0.28))
        added = add_suggestion(project, page, suggestions(project, page)[0])
        assert added[0].z > max(p.z for p in page.panels if p not in added)
