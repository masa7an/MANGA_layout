"""ページを PSD のレイヤーへ分ける処理の検証（要件定義 10.1）。

**一番大事なのは「重ね直すと PNG と同じ絵になる」こと。** 分け方を
間違えても PSD としては開けてしまうので、形式の検証（`test_psd.py`）
では捕まらない。ここで PNG 書き出しと突き合わせておくと、
**クリスタを開かなくても分解が正しいと言える**。

そのほかに押さえるのは4つ。

1. **並びと名前**が描く順のままであること
2. **中身の無いレイヤーを出さない**こと（空のレイヤーはクリスタで邪魔になる）
3. **ラフが非表示で入る**こと（→ 10.1 で決めた）
4. **画面の道具が出ない**こと（用紙の縁・目安線・欠けた画像の×印）
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QImage

from manga_layout import ExportError, Rect, Size, Tail
from manga_layout.psd import PsdGroup, PsdLayer
from manga_layout.ui import EditorState
from manga_layout.ui.export import DEFAULT_SCALE, HIGH_SCALE, export_pages, render_page
from manga_layout.ui.psd_export import (
    export_psd_pages,
    flatten,
    page_layers,
    reading_order,
)
from tests.test_psd import layer_tree, parse_psd

# 突き合わせを Python の繰り返しで回せる大きさにする。分解が正しいかは
# 画素数に依らないので、ここを A4 相当にしても得るものが無い
SMALL_PAGE = Size(300.0, 400.0)


def channels(image: QImage) -> bytes:
    return bytes(image.convertToFormat(QImage.Format.Format_ARGB32).constBits())


def max_difference(a: QImage, b: QImage) -> int:
    """一番大きく食い違っている所の差。同じなら 0。"""
    left, right = channels(a), channels(b)
    assert len(left) == len(right)
    if left == right:
        return 0
    return max(abs(x - y) for x, y in zip(left, right, strict=True))


@pytest.fixture
def state(qapp, tmp_path, png_bytes):
    """コマ・絵・集中線・フキダシ・セリフを1つずつ置いた作品。

    **8枚のレイヤーが全部そろう**ように作る。どれか欠けていると、
    そのレイヤーの分け方が間違っていても気づけない。
    """
    editor = EditorState()
    editor.save(tmp_path / "作品")
    ref, px = editor.import_bytes(png_bytes)

    with editor.edit("材料") as project:
        page = project.pages[0]
        page.size = SMALL_PAGE
        panel = project.add_panel(page, Rect(20.0, 20.0, 160.0, 140.0))
        project.add_image(panel, ref, Rect(25.0, 25.0, 150.0, 130.0), px)
        balloon = project.add_balloon(page, Rect(40.0, 200.0, 120.0, 70.0))
        # しっぽの先を決めておく。既定のままだと用紙の左上を指し、
        # 「フキダシのレイヤーが用紙の隅まで広がる」ことになって、
        # 切り詰めの検証にならない
        balloon.tail = Tail(enabled=True, tip=(90.0, 160.0), width=8.0)
        project.add_text(page, "テスト\nセリフ", Rect(45.0, 210.0, 110.0, 50.0))
    return editor


@pytest.fixture
def with_focus(state):
    """集中線を入れる。"""
    from manga_layout.focus import default_focus

    with state.edit("集中線") as project:
        project.pages[0].panels[0].focus_lines = default_focus()
    return state


@pytest.fixture
def overlapping(qapp, tmp_path, png_bytes):
    """コマを2枚重ねた作品（→ 要件定義 10.1 の第2段階）。

    **第1段階で下のコマの枠線が上のコマの絵を貫いた**のがこの形。
    フォルダに分けたことで直っているかを、ここで見る。
    """
    editor = EditorState()
    editor.save(tmp_path / "作品")
    ref, px = editor.import_bytes(png_bytes)

    with editor.edit("重なり") as project:
        page = project.pages[0]
        page.size = Size(400.0, 300.0)
        lower = project.add_panel(page, Rect(30.0, 30.0, 220.0, 170.0))
        upper = project.add_panel(page, Rect(190.0, 120.0, 180.0, 140.0))
        project.add_image(lower, ref, Rect(30.0, 30.0, 220.0, 170.0), px)
        project.add_image(upper, ref, Rect(190.0, 120.0, 180.0, 140.0), px)
    return editor


def names_of(items: list[PsdLayer | PsdGroup]) -> list[str]:
    """上から下に読んだ名前。フォルダは中身を入れ子で返す。"""
    out = []
    for item in reversed(items):
        if isinstance(item, PsdGroup):
            out.append({"name": item.name, "children": names_of(item.children)})
        else:
            out.append(item.name)
    return out


def flat_names(items: list[PsdLayer | PsdGroup]) -> list[str]:
    """フォルダを崩して名前だけ並べたもの（下から上）。"""
    out = []
    for item in items:
        if isinstance(item, PsdGroup):
            out.extend(flat_names(item.children))
        else:
            out.append(item.name)
    return out


def layer_named(items: list[PsdLayer | PsdGroup], name: str) -> PsdLayer:
    for item in items:
        if isinstance(item, PsdGroup):
            for child in item.children:
                if child.name == name:
                    return child
        elif item.name == name:
            return item
    raise AssertionError(f"{name} が無い: {names_of(items)}")


# ---------------------------------------------------------------------------
# 1. 重ね直すと PNG と同じ絵になる
# ---------------------------------------------------------------------------


class Test重ね直すとPNGと一致する:
    """**クリスタを開かずに分解の正しさを言える唯一の手段。**

    重ねる回数が1つ増えるぶん、半透明の所で**256段階への丸めが1回多く
    掛かる**。実測でも食い違いは 1 までなので、そこを上限にする。ずれが
    2 以上になったらそれは丸めではなく分け方の間違い。
    """

    def test_ページ全体が一致する(self, with_focus):
        page = with_focus.project.pages[0]
        layers = page_layers(with_focus, page, 1.0)
        merged = flatten(layers, round(page.size.w), round(page.size.h))

        assert max_difference(merged, render_page(with_focus, page, 1.0)) <= 1

    def test_倍率を変えても一致する(self, state):
        """倍率は `render_page` と同じ掛け方で効く。"""
        page = state.project.pages[0]
        layers = page_layers(state, page, HIGH_SCALE)
        expected = render_page(state, page, HIGH_SCALE)
        merged = flatten(layers, expected.width(), expected.height())

        assert (merged.width(), merged.height()) == (expected.width(), expected.height())
        assert max_difference(merged, expected) <= 1

    def test_ラフを敷いても一致する(self, state, png_bytes):
        """ラフは非表示なので、重ねた結果には出ない（→ 6.23、10.1）。"""
        state.place_rough(png_bytes)
        page = state.project.pages[0]
        layers = page_layers(state, page, 1.0)
        merged = flatten(layers, round(page.size.w), round(page.size.h))

        assert "ラフ" in flat_names(layers)
        assert max_difference(merged, render_page(state, page, 1.0)) <= 1

    def test_コマが重なっていても一致する(self, overlapping):
        """**第2段階を入れた理由そのもの**（→ 要件定義 10.1）。

        種類ごとに1枚ずつまとめていたときは、下のコマの枠線が上のコマの
        絵を貫いて出た。コマごとのフォルダにすると、上のコマの絵が下の
        コマの枠線より手前に来るので、重なりが自動で隠す。
        """
        page = overlapping.project.pages[0]
        layers = page_layers(overlapping, page, 1.0)
        merged = flatten(layers, round(page.size.w), round(page.size.h))

        assert max_difference(merged, render_page(overlapping, page, 1.0)) <= 1


# ---------------------------------------------------------------------------
# 2. 並びと名前
# ---------------------------------------------------------------------------


class Test並びと名前:
    def test_コマはフォルダにまとまる(self, with_focus):
        """上から読んだ形。中身は奥から手前（絵 → 集中線 → 枠）の逆順。"""
        assert names_of(page_layers(with_focus, with_focus.project.pages[0], 1.0)) == [
            "セリフ",
            "フキダシ",
            {"name": "コマ1", "children": ["コマ枠", "集中線・流線", "絵"]},
            "用紙",
        ]

    def test_ラフは用紙のすぐ上(self, state, png_bytes):
        """コマより奥。なぞる相手なので、絵の下に敷く（→ 6.23）。"""
        state.place_rough(png_bytes)
        items = page_layers(state, state.project.pages[0], 1.0)
        assert [items[0].name, items[1].name] == ["用紙", "ラフ"]
        assert isinstance(items[2], PsdGroup)

    def test_フォルダは重なり順に並ぶ(self, overlapping):
        """読み順ではなく**奥から手前**。番号だけが読み順（→ 10.1）。"""
        items = page_layers(overlapping, overlapping.project.pages[0], 1.0)
        groups = [x for x in items if isinstance(x, PsdGroup)]
        panels = sorted(overlapping.project.pages[0].panels, key=lambda p: p.z)
        assert len(groups) == len(panels) == 2
        # 奥のコマが先（＝PSD では下）に来る
        assert [g.name for g in groups] == ["コマ1", "コマ2"]

    def test_古い名前欄には英字を入れる(self, state):
        """日本語が読めないソフトでも役割が分かるように（→ `psd.PsdLayer`）。"""
        items = page_layers(state, state.project.pages[0], 1.0)
        aliases = []
        for item in items:
            aliases.append(item.alias)
            if isinstance(item, PsdGroup):
                aliases += [c.alias for c in item.children]
        assert aliases == ["paper", "panel1", "art", "frames", "balloons", "text"]
        assert all(a.isascii() for a in aliases)


class Test読み順の番号:
    """フォルダに振る番号。上から下・右から左（→ 要件定義 10.1）。"""

    def make(self, *rects: Rect) -> list:
        from manga_layout import new_project

        project = new_project()
        page = project.pages[0]
        return [project.add_panel(page, r) for r in rects], page

    def test_上の段が先(self):
        panels, page = self.make(Rect(10, 200, 100, 80), Rect(10, 10, 100, 80))
        assert reading_order(page.panels, 35.0) == [panels[1], panels[0]]

    def test_同じ段なら右から(self):
        """日本の漫画の読み順。左右を逆にすると番号が裏返る。"""
        panels, page = self.make(Rect(10, 10, 100, 80), Rect(200, 10, 100, 80))
        assert reading_order(page.panels, 35.0) == [panels[1], panels[0]]

    def test_わずかな縦のずれは同じ段と見なす(self):
        """隙間より小さいずれは、揃えたつもりの並び。"""
        panels, page = self.make(Rect(10, 10, 100, 80), Rect(200, 30, 100, 80))
        assert reading_order(page.panels, 35.0) == [panels[1], panels[0]]

    def test_隙間より離れていれば別の段(self):
        panels, page = self.make(Rect(10, 10, 100, 80), Rect(200, 60, 100, 80))
        assert reading_order(page.panels, 35.0) == [panels[0], panels[1]]

    def test_番号は1から振る(self, overlapping):
        items = page_layers(overlapping, overlapping.project.pages[0], 1.0)
        names = sorted(x.name for x in items if isinstance(x, PsdGroup))
        assert names == ["コマ1", "コマ2"]


# ---------------------------------------------------------------------------
# 3. 中身の無いレイヤーは出さない
# ---------------------------------------------------------------------------


class Test中身の無いレイヤー:
    def test_置いていない種類は出ない(self, state):
        """集中線もマークもラフも置いていない作品。"""
        names = flat_names(page_layers(state, state.project.pages[0], 1.0))
        assert "集中線・流線" not in names
        assert "マーク" not in names
        assert "ラフ" not in names

    def test_何も無いページでも用紙だけは出る(self, qapp, tmp_path):
        editor = EditorState()
        editor.save(tmp_path / "作品")
        layers = page_layers(editor, editor.project.pages[0], 0.5)
        assert names_of(layers) == ["用紙"]

    def test_コマが無ければフォルダも出ない(self, qapp, tmp_path):
        editor = EditorState()
        editor.save(tmp_path / "作品")
        items = page_layers(editor, editor.project.pages[0], 0.5)
        assert not any(isinstance(x, PsdGroup) for x in items)

    def test_集中線を入れると増える(self, state, with_focus):
        assert "集中線・流線" in flat_names(
            page_layers(with_focus, with_focus.project.pages[0], 1.0)
        )


# ---------------------------------------------------------------------------
# 4. ラフは非表示・切り詰め
# ---------------------------------------------------------------------------


class Testレイヤーの持ち方:
    def test_ラフだけが非表示(self, state, png_bytes):
        state.place_rough(png_bytes)
        items = page_layers(state, state.project.pages[0], 1.0)
        assert [x.name for x in items if not x.visible] == ["ラフ"]

    def test_透明な縁は落ちる(self, state):
        """キャンバス全面のまま持つと、枚数ぶんファイルが膨らむ。"""
        page = state.project.pages[0]
        balloon = layer_named(page_layers(state, page, 1.0), "フキダシ")
        assert balloon.image.width() < round(page.size.w)
        assert balloon.image.height() < round(page.size.h)
        # 落とした分は置き場所に戻っている
        assert balloon.x > 0 and balloon.y > 0

    def test_用紙は全面(self, state):
        page = state.project.pages[0]
        paper = page_layers(state, page, 1.0)[0]
        assert paper.name == "用紙"
        assert (paper.x, paper.y) == (0, 0)
        assert (paper.image.width(), paper.image.height()) == (
            round(page.size.w),
            round(page.size.h),
        )


# ---------------------------------------------------------------------------
# 5. 画面の道具を出さない
# ---------------------------------------------------------------------------


class Test画面の道具:
    """用紙の縁・目安線・コマの下地は作品ではない（→ 6.7 と同じ線引き）。"""

    def test_用紙は白一色(self, state):
        """縁の線が入っていると、四辺に線が残ったまま書き出される。"""
        paper = page_layers(state, state.project.pages[0], 1.0)[0]
        assert paper.image.pixelColor(0, 0).name() == "#ffffff"
        assert paper.image.pixelColor(150, 200).name() == "#ffffff"

    def test_コマの下地を塗らない(self, state):
        """塗ると紙の白ではなく薄い灰色になる（画面だけの色）。"""
        page = state.project.pages[0]
        frames = layer_named(page_layers(state, page, 1.0), "コマ枠")
        # 枠の内側は透明のまま（下の用紙の白が見える）
        inside = frames.image.pixelColor(
            frames.image.width() // 2, frames.image.height() // 2
        )
        assert inside.alpha() == 0


# ---------------------------------------------------------------------------
# 6. ファイルとして書く
# ---------------------------------------------------------------------------


class Testファイルに書く:
    def test_ページごとに1ファイル(self, state, tmp_path):
        dest = tmp_path / "出力"
        with state.edit("2ページ目") as project:
            project.pages.append(
                type(project.pages[0])(id="p2", size=SMALL_PAGE)
            )
        written = export_psd_pages(state, [0, 1], dest, 1.0)

        assert [p.name for p in written] == ["p01.psd", "p02.psd"]
        assert all(p.read_bytes()[:4] == b"8BPS" for p in written)

    def test_書いたものを読み返せる(self, with_focus, tmp_path):
        path = export_psd_pages(with_focus, [0], tmp_path, 1.0)[0]
        parsed = parse_psd(path.read_bytes())

        assert (parsed["width"], parsed["height"]) == (300, 400)
        assert layer_tree(parsed) == [
            "セリフ",
            "フキダシ",
            {"name": "コマ1", "children": ["コマ枠", "集中線・流線", "絵"]},
            "用紙",
        ]

    def test_重なったページも読み返せる(self, overlapping, tmp_path):
        """フォルダが2つ並び、中身がこぼれていないこと。"""
        path = export_psd_pages(overlapping, [0], tmp_path, 1.0)[0]
        tree = layer_tree(parse_psd(path.read_bytes()))

        assert [x["name"] for x in tree if isinstance(x, dict)] == ["コマ2", "コマ1"]
        assert all(x["children"] == ["コマ枠", "絵"] for x in tree if isinstance(x, dict))

    def test_PNGと同じ場所に並ぶ(self, state, tmp_path):
        """`export/` の中で拡張子だけが違う（→ 6.7）。"""
        dest = tmp_path / "export"
        export_pages(state, [0], dest, 1.0)
        export_psd_pages(state, [0], dest, 1.0)
        assert sorted(p.name for p in dest.iterdir()) == ["p01.png", "p01.psd"]

    def test_大きすぎるページは断る(self, state, tmp_path):
        """PNG と同じ所で止まる（`checked_page_px`）。"""
        with state.edit("巨大") as project:
            project.pages[0].size = Size(20000.0, 20000.0)
        with pytest.raises(ExportError, match="大きすぎます"):
            export_psd_pages(state, [0], tmp_path, 1.0)

    def test_倍率が画素数に効く(self, state, tmp_path):
        path = export_psd_pages(state, [0], tmp_path, 0.5)[0]
        parsed = parse_psd(path.read_bytes())
        assert (parsed["width"], parsed["height"]) == (150, 200)

    def test_既定の倍率で書ける(self, state, tmp_path):
        path = export_psd_pages(state, [0], tmp_path)[0]
        parsed = parse_psd(path.read_bytes())
        assert parsed["width"] == round(SMALL_PAGE.w * DEFAULT_SCALE)
