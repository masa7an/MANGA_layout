"""コマ割りの計算の検証。"""

from __future__ import annotations

import pytest

from manga_layout import Rect, Size, new_project, vertical
from manga_layout.layout import (
    LayoutSettings,
    aspect_of,
    contain_rect_in,
    cover_rect_in,
    default_panel_rect,
    full_page_rect,
    handle_at,
    handle_positions,
    image_at,
    image_orphaned_at,
    panel_at,
    panel_rect_orphans,
    resize_rect,
    resize_rect_keep_aspect,
    set_panel_rect,
    snap_candidates,
    snap_delta,
    snap_moved_rect,
    snap_point,
    split_panel,
    text_at,
    text_frame,
    text_ink_bands,
)

# 座標の計算そのものは単位に依らないので、値は px 化の前から変えていない。
# ページの寸法（A4 相当 1240×1754px）だけが px になっている
SETTINGS = LayoutSettings(gutter=6.0, margin=15.0, min_panel_size=5.0)


@pytest.fixture
def page_with_panels():
    """A4 に横並びの2コマ。"""
    project = new_project()
    page = project.pages[0]
    project.add_panel(page, Rect(15.0, 15.0, 87.0, 80.0))
    project.add_panel(page, Rect(108.0, 15.0, 87.0, 80.0))
    return project, page


class TestPanelAt:
    def test_コマの上を指せば返る(self, page_with_panels):
        _, page = page_with_panels
        assert panel_at(page, 50.0, 50.0) is page.panels[0]
        assert panel_at(page, 150.0, 50.0) is page.panels[1]

    def test_何もない所ではNone(self, page_with_panels):
        _, page = page_with_panels
        assert panel_at(page, 105.0, 50.0) is None
        assert panel_at(page, 50.0, 200.0) is None

    def test_重なっていれば手前が返る(self):
        project = new_project()
        page = project.pages[0]
        back = project.add_panel(page, Rect(10.0, 10.0, 100.0, 100.0))
        front = project.add_panel(page, Rect(20.0, 20.0, 50.0, 50.0))
        assert front.z > back.z
        assert panel_at(page, 30.0, 30.0) is front

    def test_zが同値でも後から描いたものが返る(self):
        """描く順（render.py の昇順ソート）と当たり判定の順が食い違って
        いた（2026-08-08 に発見）。`z` が同じとき、`sorted(reverse=True)`
        は同値の並びをそのまま保つため、描画では手前に来る後方の要素が、
        判定では逆に後回しになっていた。
        """
        project = new_project()
        page = project.pages[0]
        back = project.add_panel(page, Rect(10.0, 10.0, 100.0, 100.0))
        front = project.add_panel(page, Rect(20.0, 20.0, 50.0, 50.0))
        front.z = back.z  # 同値にする

        assert panel_at(page, 30.0, 30.0) is front


class TestHandles:
    def test_8方向すべてある(self):
        assert set(handle_positions(Rect(0.0, 0.0, 10.0, 10.0))) == {
            "nw", "n", "ne", "e", "se", "s", "sw", "w"
        }

    def test_角の位置が正しい(self):
        pos = handle_positions(Rect(10.0, 20.0, 30.0, 40.0))
        assert pos["nw"] == (10.0, 20.0)
        assert pos["se"] == (40.0, 60.0)
        assert pos["n"] == (25.0, 20.0)

    def test_つまみを掴める(self):
        rect = Rect(10.0, 20.0, 30.0, 40.0)
        assert handle_at(rect, 10.0, 20.0, 4.0) == "nw"
        assert handle_at(rect, 39.0, 59.0, 4.0) == "se"
        assert handle_at(rect, 25.0, 20.5, 4.0) == "n"

    def test_離れていれば掴めない(self):
        assert handle_at(Rect(10.0, 20.0, 30.0, 40.0), 25.0, 40.0, 4.0) is None

    def test_角が辺より優先される(self):
        # 小さなコマではつまみ同士が重なる。角を掴んだつもりが
        # 辺になる誤操作を防ぐ
        tiny = Rect(0.0, 0.0, 6.0, 6.0)
        assert handle_at(tiny, 0.0, 0.0, 5.0) == "nw"


class TestResize:
    def test_右下を引くと大きくなる(self):
        r = resize_rect(Rect(10.0, 10.0, 50.0, 40.0), "se", 100.0, 80.0, 5.0)
        assert (r.x, r.y, r.w, r.h) == (10.0, 10.0, 90.0, 70.0)

    def test_左上を引くと原点が動く(self):
        r = resize_rect(Rect(10.0, 10.0, 50.0, 40.0), "nw", 5.0, 5.0, 5.0)
        assert (r.x, r.y, r.w, r.h) == (5.0, 5.0, 55.0, 45.0)

    def test_辺のつまみは一方向だけ動かす(self):
        r = resize_rect(Rect(10.0, 10.0, 50.0, 40.0), "n", 99.0, 5.0, 5.0)
        assert (r.x, r.w) == (10.0, 50.0)
        assert (r.y, r.h) == (5.0, 45.0)

    def test_最小の大きさで止まる(self):
        r = resize_rect(Rect(10.0, 10.0, 50.0, 40.0), "se", 0.0, 0.0, 5.0)
        assert (r.w, r.h) == (5.0, 5.0)

    def test_反対側を追い越して裏返らない(self):
        # 追い越しを許すと幅が負になり、以降の計算が全部おかしくなる
        r = resize_rect(Rect(10.0, 10.0, 50.0, 40.0), "nw", 999.0, 999.0, 5.0)
        assert r.w > 0.0 and r.h > 0.0
        assert (r.x, r.y) == (55.0, 45.0)


class TestSnap:
    def test_近ければ吸い付く(self):
        assert snap_delta([9.0], [10.0, 50.0], 2.0) == pytest.approx(1.0)

    def test_遠ければ動かない(self):
        assert snap_delta([9.0], [20.0], 2.0) == 0.0

    def test_一番近い候補を選ぶ(self):
        assert snap_delta([9.0], [8.5, 10.0], 2.0) == pytest.approx(-0.5)

    def test_ページの端と余白が候補になる(self, page_with_panels):
        _, page = page_with_panels
        xs, ys = snap_candidates(page, None, SETTINGS)
        assert 0.0 in xs and 1240.0 in xs
        assert 15.0 in xs and 1225.0 in xs
        assert 0.0 in ys and 1754.0 in ys

    def test_隙間ぶん離れた位置も候補になる(self, page_with_panels):
        # コマを並べるときに隙間を目分量で合わせずに済む
        _, page = page_with_panels
        xs, _ = snap_candidates(page, page.panels[0].id, SETTINGS)
        # 2コマ目の左端 108.0 から隙間 6.0 ぶん手前
        assert 102.0 in xs

    def test_自分自身は候補から外す(self, page_with_panels):
        _, page = page_with_panels
        target = page.panels[0]
        xs, _ = snap_candidates(page, target.id, SETTINGS)
        # 自分の左端 15.0 は余白と同じ値なので、右端 102.0 で見る
        assert xs.count(102.0) == 1  # 2コマ目の 108-6 だけ

    def test_移動中の矩形が吸い付く(self, page_with_panels):
        _, page = page_with_panels
        xs, ys = snap_candidates(page, page.panels[0].id, SETTINGS)
        moved = snap_moved_rect(Rect(14.2, 14.6, 87.0, 80.0), xs, ys, 2.0)
        assert moved.x == pytest.approx(15.0)
        assert moved.y == pytest.approx(15.0)

    def test_動かしている辺だけ吸着する(self, page_with_panels):
        # 上辺を掴んでいるのに左右へ吸着すると、意図しない方向に形が変わる
        _, page = page_with_panels
        xs, ys = snap_candidates(page, page.panels[0].id, SETTINGS)
        x, y = snap_point("n", 14.5, 14.5, xs, ys, 2.0)
        assert x == 14.5
        assert y == pytest.approx(15.0)


class TestSplit:
    def test_横に割ると上下2つになる(self, page_with_panels):
        project, page = page_with_panels
        panel = page.panels[0]

        first, second = split_panel(
            project, page, panel.id, horizontal=True, position=55.0, settings=SETTINGS
        )

        assert first is panel
        assert first.shape.as_rect() == Rect(15.0, 15.0, 87.0, 37.0)
        assert second.shape.as_rect() == Rect(15.0, 58.0, 87.0, 37.0)
        # 隙間ぶんだけ空く
        assert second.shape.bounds().y - first.shape.bounds().bottom == pytest.approx(6.0)

    def test_縦に割ると左右2つになる(self, page_with_panels):
        project, page = page_with_panels
        panel = page.panels[0]

        first, second = split_panel(
            project, page, panel.id, horizontal=False, position=58.5, settings=SETTINGS
        )

        assert first.shape.as_rect().w == pytest.approx(40.5)
        assert second.shape.as_rect().w == pytest.approx(40.5)

    def test_元のコマがidごと残る(self, page_with_panels):
        # 紐づいた吹き出しの追随先が変わらないようにするため
        project, page = page_with_panels
        panel = page.panels[0]
        balloon = project.add_balloon(page, Rect(20.0, 20.0, 30.0, 20.0), attached_panel_id=panel.id)

        split_panel(project, page, panel.id, horizontal=True, position=55.0, settings=SETTINGS)

        assert balloon.attached_panel_id == panel.id
        assert page.attached_to(panel.id) == [balloon]

    def test_新しいコマが元の直後に並ぶ(self, page_with_panels):
        project, page = page_with_panels
        first = page.panels[0]
        _, second = split_panel(
            project, page, first.id, horizontal=True, position=55.0, settings=SETTINGS
        )
        assert [p.id for p in page.panels][:2] == [first.id, second.id]

    def test_枠線の設定を引き継ぐ(self, page_with_panels):
        project, page = page_with_panels
        panel = page.panels[0]
        panel.border.width = 1.2
        panel.border.visible = False

        _, second = split_panel(
            project, page, panel.id, horizontal=True, position=55.0, settings=SETTINGS
        )

        assert second.border.width == 1.2
        assert second.border.visible is False
        # 引き継ぎであって共有ではない
        second.border.width = 0.3
        assert panel.border.width == 1.2

    def test_画像は中心のある側へ行く(self, page_with_panels):
        project, page = page_with_panels
        panel = page.panels[0]
        top = project.add_image(panel, "assets/a.png", Rect(20.0, 20.0, 20.0, 20.0), (10, 10))
        bottom = project.add_image(panel, "assets/b.png", Rect(20.0, 70.0, 20.0, 20.0), (10, 10))

        first, second = split_panel(
            project, page, panel.id, horizontal=True, position=55.0, settings=SETTINGS
        )

        assert first.children == [top]
        assert second.children == [bottom]

    def test_隙間の上にある画像は近い側へ行く(self, page_with_panels):
        # 隙間は 52〜58。どちらのコマにも入らない中心位置になる
        project, page = page_with_panels
        panel = page.panels[0]
        # 中心 y=53。上のコマの下辺 52 に近い
        near_top = project.add_image(panel, "assets/a.png", Rect(20.0, 48.0, 20.0, 10.0), (10, 10))
        # 中心 y=57。下のコマの上辺 58 に近い
        near_bottom = project.add_image(panel, "assets/b.png", Rect(50.0, 52.0, 20.0, 10.0), (10, 10))

        first, second = split_panel(
            project, page, panel.id, horizontal=True, position=55.0, settings=SETTINGS
        )

        assert first.children == [near_top]
        assert second.children == [near_bottom]

    def test_小さすぎる分割を断る(self, page_with_panels):
        project, page = page_with_panels
        with pytest.raises(ValueError, match="最小"):
            split_panel(
                project, page, page.panels[0].id,
                horizontal=True, position=17.0, settings=SETTINGS,
            )

    def test_斜めのコマは分割できないと伝える(self, page_with_panels):
        from manga_layout import Polygon

        project, page = page_with_panels
        panel = page.panels[0]
        panel.shape = Polygon(((15.0, 15.0), (102.0, 20.0), (102.0, 95.0), (15.0, 90.0)))

        with pytest.raises(ValueError, match="斜め"):
            split_panel(
                project, page, panel.id, horizontal=True, position=55.0, settings=SETTINGS
            )

    def test_分割を繰り返せる(self, page_with_panels):
        project, page = page_with_panels
        panel = page.panels[0]

        _, lower = split_panel(
            project, page, panel.id, horizontal=True, position=55.0, settings=SETTINGS
        )
        split_panel(
            project, page, lower.id, horizontal=False, position=58.5, settings=SETTINGS
        )

        assert len(page.panels) == 4
        assert all(p.shape.is_axis_aligned_rect() for p in page.panels)


class TestSetPanelRect:
    def test_中身は動かさない(self, page_with_panels):
        # リサイズは「絵に対する窓の大きさを変える操作」。
        # 中身が付いて回ると、絵の位置を合わせ直すことになる
        project, page = page_with_panels
        panel = page.panels[0]
        image = project.add_image(panel, "assets/a.png", Rect(20.0, 20.0, 40.0, 30.0), (10, 10))
        balloon = project.add_balloon(page, Rect(25.0, 25.0, 30.0, 20.0), attached_panel_id=panel.id)

        set_panel_rect(panel, Rect(30.0, 30.0, 60.0, 50.0))

        assert image.rect == Rect(20.0, 20.0, 40.0, 30.0)
        assert balloon.rect == Rect(25.0, 25.0, 30.0, 20.0)

    def test_逆向きの矩形を正す(self):
        project = new_project()
        panel = project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
        set_panel_rect(panel, Rect(50.0, 60.0, -30.0, -40.0))
        assert panel.shape.as_rect() == Rect(20.0, 20.0, 30.0, 40.0)


class TestImageFit:
    """画像をコマに合わせる計算。

    「収める」は貼り付け直後の置き場所、「埋める」は コマにフィット。
    取り違えると、埋めたつもりで隙間が残る／全体を見たいのに切れる、
    という分かりにくい不具合になる。
    """

    PANEL = Rect(10.0, 20.0, 90.0, 60.0)  # 横長のコマ（3:2）

    def test_縦横比を返す(self):
        assert aspect_of((1200, 900)) == pytest.approx(4 / 3)

    @pytest.mark.parametrize("src_px", [(0, 0), (100, 0), (0, 100), (-5, 10)])
    def test_元の寸法が無ければコマそのまま(self, src_px):
        # 読み込んだ作品に src_px が入っていない場合。0 除算で落とさない
        assert contain_rect_in(self.PANEL, src_px) == self.PANEL
        assert cover_rect_in(self.PANEL, src_px) == self.PANEL

    def test_収める_縦長の絵は左右が余る(self):
        rect = contain_rect_in(self.PANEL, (100, 200))  # 1:2
        assert rect.h == pytest.approx(self.PANEL.h)  # 高さがぴったり
        assert rect.w < self.PANEL.w
        assert rect.center == pytest.approx(self.PANEL.center)

    def test_収める_はみ出さない(self):
        for src in [(100, 200), (200, 100), (50, 50)]:
            rect = contain_rect_in(self.PANEL, src)
            assert rect.w <= self.PANEL.w + 1e-9
            assert rect.h <= self.PANEL.h + 1e-9

    def test_埋める_縦長の絵は上下がはみ出す(self):
        rect = cover_rect_in(self.PANEL, (100, 200))
        assert rect.w == pytest.approx(self.PANEL.w)  # 幅がぴったり
        assert rect.h > self.PANEL.h
        assert rect.center == pytest.approx(self.PANEL.center)

    def test_埋める_隙間が残らない(self):
        for src in [(100, 200), (200, 100), (50, 50)]:
            rect = cover_rect_in(self.PANEL, src)
            assert rect.w >= self.PANEL.w - 1e-9
            assert rect.h >= self.PANEL.h - 1e-9

    @pytest.mark.parametrize("src_px", [(100, 200), (200, 100), (1200, 900)])
    def test_どちらも縦横比を保つ(self, src_px):
        want = aspect_of(src_px)
        assert aspect_of_rect(contain_rect_in(self.PANEL, src_px)) == pytest.approx(want)
        assert aspect_of_rect(cover_rect_in(self.PANEL, src_px)) == pytest.approx(want)


def aspect_of_rect(rect: Rect) -> float:
    return rect.w / rect.h


class TestImageOrphanedAt:
    """画像がコマの外へ完全に出て、二度と選べなくなるかの判定。

    `image_at` は「コマの中 かつ 画像の中」の点を要求する。矩形がコマと
    1pxも重ならなくなると、どの点を押してもこの条件を満たせなくなり、
    Undo 以外に取り戻す手段が無い（2026-08-08 に発見）。
    """

    PANEL = Rect(0.0, 0.0, 100.0, 100.0)

    @pytest.fixture
    def panel(self):
        project = new_project()
        return project.add_panel(project.pages[0], self.PANEL)

    def test_コマの中に収まっていれば孤児にならない(self, panel):
        assert not image_orphaned_at(panel, Rect(10.0, 10.0, 50.0, 50.0), 0.0)

    def test_縁からわずかにはみ出すだけなら孤児にならない(self, panel):
        # コマの右下ぎりぎりに寄せる。少しでも重なっていれば選べる
        assert not image_orphaned_at(panel, Rect(90.0, 90.0, 50.0, 50.0), 0.0)

    def test_縁で止まらず完全に出ると孤児になる(self, panel):
        assert image_orphaned_at(panel, Rect(100.0, 0.0, 50.0, 50.0), 0.0)
        assert image_orphaned_at(panel, Rect(-50.0, 0.0, 50.0, 50.0), 0.0)
        assert image_orphaned_at(panel, Rect(0.0, 100.0, 50.0, 50.0), 0.0)
        assert image_orphaned_at(panel, Rect(0.0, -50.0, 50.0, 50.0), 0.0)

    def test_傾いていれば外接矩形の分だけ判定がゆるむ(self, panel):
        # x: 105〜145 で、傾いていなければコマ（0〜100）と1pxも重ならない
        rect = Rect(105.0, 80.0, 40.0, 40.0)
        assert image_orphaned_at(panel, rect, 0.0)
        # 45度傾けると、40×40 の外接矩形が約 56.6×56.6 に広がり
        # (中心 125,100 のまま) x が 96.7〜153.3 になってコマまで届く
        assert not image_orphaned_at(panel, rect, 45.0)


class TestPanelRectOrphans:
    """**コマ側を動かしても同じ孤児になる**（2026-08-09 に発見）。

    コマの大きさ変更は中の画像を動かさない（→ `set_panel_rect`）ので、
    画像の移動を塞いでも、コマを画像の外へ出せば同じ状態が作れた。
    """

    @pytest.fixture
    def panel(self):
        project = new_project()
        panel = project.add_panel(project.pages[0], Rect(0.0, 0.0, 100.0, 100.0))
        project.add_image(panel, "assets/a.png", Rect(20.0, 20.0, 60.0, 60.0), (60, 60))
        return panel

    def test_画像に掛かったままなら孤児にならない(self, panel):
        # 画像（20〜80）の左上の隅にだけ掛かる
        assert not panel_rect_orphans(panel, Rect(-50.0, -50.0, 80.0, 80.0))

    def test_画像から完全に外れると孤児になる(self, panel):
        assert panel_rect_orphans(panel, Rect(200.0, 0.0, 100.0, 100.0))
        assert panel_rect_orphans(panel, Rect(0.0, 200.0, 100.0, 100.0))

    def test_画像が無ければ何をしても孤児にならない(self):
        project = new_project()
        empty = project.add_panel(project.pages[0], Rect(0.0, 0.0, 100.0, 100.0))
        assert not panel_rect_orphans(empty, Rect(900.0, 900.0, 10.0, 10.0))

    def test_1枚でも置き去りになれば真(self):
        """**残る画像があっても許さない。** 1枚失われれば同じこと。"""
        project = new_project()
        panel = project.add_panel(project.pages[0], Rect(0.0, 0.0, 100.0, 100.0))
        project.add_image(panel, "assets/a.png", Rect(20.0, 20.0, 60.0, 60.0), (60, 60))
        project.add_image(panel, "assets/b.png", Rect(210.0, 10.0, 40.0, 40.0), (40, 40))

        # 2枚目には掛かるが、1枚目（20〜80）からは完全に外れる
        assert panel_rect_orphans(panel, Rect(200.0, 0.0, 100.0, 100.0))


class TestImageAtZ同値:
    """`image_at` も `panel_at` と同じ食い違いを持っていた（2026-08-08）。"""

    def test_zが同値でも後から置いたものが返る(self):
        project = new_project()
        page = project.pages[0]
        panel = project.add_panel(page, Rect(0.0, 0.0, 100.0, 100.0))
        back = project.add_image(panel, "assets/a.png", Rect(0.0, 0.0, 100.0, 100.0), (10, 10))
        front = project.add_image(panel, "assets/b.png", Rect(0.0, 0.0, 100.0, 100.0), (10, 10))
        front.z = back.z

        assert image_at(panel, 50.0, 50.0) is front


class TestResizeKeepAspect:
    """Shift を押しながらのリサイズ。絵が歪まないこと。"""

    RECT = Rect(0.0, 0.0, 40.0, 20.0)  # 2:1

    @pytest.mark.parametrize("handle", ["nw", "ne", "se", "sw", "n", "s", "e", "w"])
    def test_どのつまみでも縦横比が保たれる(self, handle):
        result = resize_rect_keep_aspect(self.RECT, handle, 70.0, 55.0, 5.0, 2.0)
        assert aspect_of_rect(result) == pytest.approx(2.0)

    def test_角は対角を動かさない(self):
        # se をつかんだら左上は動かない
        result = resize_rect_keep_aspect(self.RECT, "se", 80.0, 30.0, 5.0, 2.0)
        assert (result.x, result.y) == pytest.approx((0.0, 0.0))
        assert result.w > self.RECT.w

    def test_左上をつかむと右下が動かない(self):
        result = resize_rect_keep_aspect(self.RECT, "nw", -20.0, -20.0, 5.0, 2.0)
        assert (result.right, result.bottom) == pytest.approx((40.0, 20.0))

    def test_辺は反対の辺を動かさない(self):
        # s をつかんだら上辺は動かない。幅は中央から均等に伸びる
        result = resize_rect_keep_aspect(self.RECT, "s", 20.0, 40.0, 5.0, 2.0)
        assert result.y == pytest.approx(0.0)
        assert result.center[0] == pytest.approx(self.RECT.center[0])

    def test_縦横比が無ければ普通のリサイズと同じ(self):
        from manga_layout.layout import resize_rect

        free = resize_rect(self.RECT, "se", 80.0, 30.0, 5.0)
        assert resize_rect_keep_aspect(self.RECT, "se", 80.0, 30.0, 5.0, 0.0) == free

    def test_最小の大きさを下回らない(self):
        result = resize_rect_keep_aspect(self.RECT, "se", -100.0, -100.0, 5.0, 2.0)
        assert result.w >= 5.0 and result.h >= 5.0


class TestFullPage:
    def test_余白を除いた全面(self):
        project = new_project()
        rect = full_page_rect(project.pages[0], SETTINGS)
        assert rect == Rect(15.0, 15.0, 1210.0, 1724.0)


class TestDefaultPanel:
    """クリックだけでコマを置いたときの大きさと位置。"""

    def test_クリック位置が中心になる(self):
        page = new_project().pages[0]
        rect = default_panel_rect(page, 620.0, 877.0, SETTINGS)
        assert rect.center == pytest.approx((620.0, 877.0))

    def test_基本枠のおよそ3分の1(self):
        page = new_project().pages[0]
        inner = full_page_rect(page, SETTINGS)
        rect = default_panel_rect(page, 105.0, 148.5, SETTINGS)
        assert rect.w == pytest.approx(inner.w / 3.0)
        assert rect.h == pytest.approx(inner.h / 3.0)

    @pytest.mark.parametrize(
        "x,y",
        [(0.0, 0.0), (210.0, 297.0), (-50.0, 500.0)],
        ids=["左上の角", "右下の角", "用紙の外"],
    )
    def test_用紙からはみ出さない(self, x, y):
        # はみ出したまま作ると、つまみが画面外に出て掴めなくなる
        page = new_project().pages[0]
        rect = default_panel_rect(page, x, y, SETTINGS)
        assert rect.x >= 0.0 and rect.y >= 0.0
        assert rect.right <= page.size.w and rect.bottom <= page.size.h

    def test_余白のほうが大きい用紙でも潰れない(self):
        """基本枠が0以下になる用紙。3分の1では消えてしまう。

        名刺のような小さな用紙で余白の設定をそのまま使うと起こる。
        """
        page = new_project(size=Size(20.0, 20.0)).pages[0]
        rect = default_panel_rect(page, 10.0, 10.0, SETTINGS)
        assert rect.w >= SETTINGS.min_panel_size
        assert rect.h >= SETTINGS.min_panel_size
        assert rect.right <= page.size.w and rect.bottom <= page.size.h


class TestTextAt:
    """セリフの当たり判定は**字の並んでいる範囲**だけ（要件定義 6.5）。

    枠は既定で 230×422 あり、数文字のセリフでは大半が空いている。
    そこまで拾うと、フキダシの中に置いたマークが**見えているのに
    掴めない**（2026-08-05 の不具合）。吹き出しを外接矩形ではなく
    楕円で判定しているのと同じ線引き（→ 6.4）。
    """

    @pytest.fixture
    def page_with_text(self):
        """縦書き（既定）で1列3文字。枠はそれよりずっと大きい。"""
        project = new_project()
        page = project.pages[0]
        text = project.add_text(page, "セリフ", Rect(250.0, 250.0, 200.0, 150.0))
        return page, text

    def test_字の上なら拾う(self, page_with_text):
        page, text = page_with_text
        # 1列のときの列の中心は枠の横中央（→ `vertical.layout`）
        assert text_at(page, 350.0, 300.0) is text

    def test_字から外れた場所では拾わない(self, page_with_text):
        page, _ = page_with_text
        # 枠の中だが列の左右（列の幅は 42px × 1.33）
        assert text_at(page, 300.0, 300.0) is None
        assert text_at(page, 400.0, 300.0) is None

    def test_列の間は拾う(self):
        """字と字・列と列の隙間で下へ抜けると、掴み所が虫食いになる。"""
        project = new_project()
        page = project.pages[0]
        text = project.add_text(page, "あ\nい", Rect(250.0, 250.0, 200.0, 150.0))
        bands = text_ink_bands(text)
        assert len(bands) == 2
        left, right = sorted(bands, key=lambda b: b.x)
        # 列送り（1.33）ぶんの帯が隙間なく並ぶ
        assert left.right == pytest.approx(right.x)

    def test_空のセリフは枠全体で拾う(self):
        """空のときは点線の枠を描いているので、そこが描いてある範囲。

        字の無い扱いにすると、作った直後のセリフを選べなくなる。
        """
        project = new_project()
        page = project.pages[0]
        rect = Rect(250.0, 250.0, 200.0, 150.0)
        text = project.add_text(page, "", rect)
        assert text_ink_bands(text) == [rect]
        assert text_at(page, 260.0, 260.0) is text

    def test_横書きは行の帯だけに絞る(self):
        """字送りはフォント依存で幅を出せないので、幅は枠のまま。

        既定の枠は縦長で余っているのも上下なので、これで用は足りる。
        """
        project = new_project()
        page = project.pages[0]
        rect = Rect(250.0, 250.0, 200.0, 400.0)
        text = project.add_text(page, "セリフ", rect)
        text.direction = "horizontal"

        band = text_ink_bands(text)[0]
        assert band.x == rect.x and band.w == rect.w
        assert band.center[1] == pytest.approx(rect.center[1])
        # 上下の余りは下へ譲る
        assert text_at(page, 350.0, 260.0) is None
        assert text_at(page, 350.0, 450.0) is text

    def test_重なっていれば手前が返る(self):
        project = new_project()
        page = project.pages[0]
        rect = Rect(250.0, 250.0, 200.0, 150.0)
        back = project.add_text(page, "あ", rect)
        front = project.add_text(page, "あ", rect)
        assert front.z > back.z
        assert text_at(page, 350.0, 325.0) is front


class TestTextFrame:
    """セリフの選択枠は、枠ではなく**字の並びの外接矩形**（→ `text_frame`）。

    掴める範囲（`TestTextAt`）が既に枠ではないので、枠のまま描くと**押しても
    掴めない場所まで枠が伸びる**。「どこを掴んでいるのか分からない」という
    指摘（本人談 2026-08-07）はこの食い違いそのもの。
    """

    def test_枠より狭い(self):
        """1列3文字。幅は列送り、高さは字の大きさ×3 になる。"""
        project = new_project()
        page = project.pages[0]
        rect = Rect(250.0, 250.0, 200.0, 400.0)
        text = project.add_text(page, "セリフ", rect)

        frame = text_frame(text)
        size = text.font.size_px
        assert frame.w == pytest.approx(size * vertical.COLUMN_PITCH)
        assert frame.h == pytest.approx(size * 3)
        assert frame.w < rect.w and frame.h < rect.h

    def test_字の帯をすべて囲む(self):
        """掴める範囲が枠の外に出ないこと。出ると枠の外で掴めてしまう。"""
        project = new_project()
        page = project.pages[0]
        text = project.add_text(page, "あい\nうえお", Rect(250.0, 250.0, 300.0, 400.0))

        frame = text_frame(text)
        for band in text_ink_bands(text):
            assert band.x >= frame.x - 1e-9 and band.right <= frame.right + 1e-9
            assert band.y >= frame.y - 1e-9 and band.bottom <= frame.bottom + 1e-9

    def test_空のセリフは枠そのもの(self):
        """空のときは点線の枠が「描いてある範囲」（→ `text_ink_bands`）。

        ここを狭めると、作った直後のセリフに掴み所が無くなる。
        """
        project = new_project()
        page = project.pages[0]
        rect = Rect(250.0, 250.0, 200.0, 150.0)
        text = project.add_text(page, "", rect)
        assert text_frame(text) == rect

    def test_横書きは高さだけ縮む(self):
        """字送りが分からず幅は出せない（→ `text_ink_bands`）。"""
        project = new_project()
        page = project.pages[0]
        rect = Rect(250.0, 250.0, 200.0, 400.0)
        text = project.add_text(page, "セリフ", rect)
        text.direction = "horizontal"

        frame = text_frame(text)
        assert frame.x == rect.x and frame.w == rect.w
        assert frame.h < rect.h
        assert frame.center[1] == pytest.approx(rect.center[1])

    @pytest.mark.parametrize("direction", ["vertical", "horizontal"])
    @pytest.mark.parametrize("align", ["left", "center", "right"])
    def test_枠に入れ直しても字が動かない(self, direction, align):
        """大きさ変更は、この矩形をそのまま枠に入れる（→ `text_frame`）。

        入れた拍子に字が動くと、掴んだ覚えのない側が動いて見える。
        **2回目以降も動かない**ことまで確かめる。ここが縮み続けると、
        掴み直すたびにセリフが痩せていく。
        """
        project = new_project()
        page = project.pages[0]
        text = project.add_text(page, "あい\nうえお", Rect(250.0, 250.0, 300.0, 400.0))
        text.direction = direction
        text.align = align

        before = text_ink_bands(text)
        frame = text_frame(text)
        text.rect = frame

        assert text_ink_bands(text) == before
        assert text_frame(text) == frame
