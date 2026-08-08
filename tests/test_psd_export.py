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
    panels_overlap,
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


def differing_pixels(a: QImage, b: QImage) -> int:
    """食い違っている画素の数。**縁だけのずれかどうか**を見るのに使う。"""
    left, right = channels(a), channels(b)
    return sum(
        1 for i in range(0, len(left), 4) if left[i : i + 4] != right[i : i + 4]
    )


# トーンを4枚に分けたページで、重ねた結果が PNG からずれてよい幅
# （→ 要件定義 6.28）。**トーンの範囲の境目 1px にだけ出る。**
#
# 分けない普通のページは今までどおり 1 まで（→ `Test重ね直すとPNGと一致する`）。
# ここだけ緩いのは、絵とトーンが**別々に縮んでから重なる**ため
TONE_EDGE_TOLERANCE = 40


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
def dark_png(qapp) -> bytes:
    """半分が黒ベタの画像。

    **基準画像（`png_bytes`）にはしきい値より暗い画素が1つも無い**ので、
    トーンを入れてもマスクが空になり、出た・出ないを見分けられない
    （tests/test_ui_tone.py が同じ理由で持っているのと同じもの）。
    """
    from PySide6.QtGui import QColor, QPainter

    from manga_layout.images import to_png_bytes

    image = QImage(120, 120, QImage.Format.Format_ARGB32)
    image.fill(QColor("#FFFFFF"))
    painter = QPainter(image)
    painter.fillRect(10, 10, 100, 50, QColor("#000000"))
    painter.end()
    return to_png_bytes(image)


@pytest.fixture
def with_tone(qapp, tmp_path, dark_png):
    """黒ベタの絵にトーンを入れた作品（→ 要件定義 6.28 の「トーン範囲」）。"""
    from manga_layout.tone import default_tone

    editor = EditorState()
    editor.save(tmp_path / "作品")
    ref, px = editor.import_bytes(dark_png)
    with editor.edit("材料") as project:
        page = project.pages[0]
        page.size = SMALL_PAGE
        panel = project.add_panel(page, Rect(20.0, 20.0, 160.0, 140.0))
        image = project.add_image(panel, ref, Rect(25.0, 25.0, 150.0, 130.0), px)
        image.tone = default_tone()
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


@pytest.fixture
def four_panels(qapp, tmp_path, png_bytes):
    """重なっていない4コマ。**作った順と読み順をわざとずらしてある。**

    上段 → 下段の左 → 下段の右 の順に作るので、読み順（右から）とは
    2枚目・3枚目が入れ替わる。作った順で並べてしまっても気づけるようにする。
    """
    editor = EditorState()
    editor.save(tmp_path / "作品")
    ref, px = editor.import_bytes(png_bytes)

    with editor.edit("4コマ") as project:
        page = project.pages[0]
        page.size = Size(400.0, 400.0)
        boxes = [
            Rect(20.0, 20.0, 360.0, 100.0),  # コマ1（上段）
            Rect(20.0, 160.0, 160.0, 100.0),  # 左 → コマ3
            Rect(220.0, 160.0, 160.0, 100.0),  # 右 → コマ2
            Rect(20.0, 300.0, 360.0, 80.0),  # コマ4（下段）
        ]
        for box in boxes:
            panel = project.add_panel(page, box)
            project.add_image(panel, ref, box, px)
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

    def test_重なっていなければ一覧は読み順(self, four_panels):
        """一覧の上から コマ1、コマ2……（→ 要件定義 10.1）。

        PSD には並び順と重なり順の区別が無いが、**重ならないコマの前後は
        絵に出ない**ので、探しやすい向きにできる。
        """
        items = page_layers(four_panels, four_panels.project.pages[0], 1.0)
        assert [x["name"] for x in names_of(items) if isinstance(x, dict)] == [
            "コマ1",
            "コマ2",
            "コマ3",
            "コマ4",
        ]

    def test_重なっていれば重なり順を保つ(self, overlapping):
        """並びが絵に出るページでは、読みやすさより絵を採る。"""
        items = page_layers(overlapping, overlapping.project.pages[0], 1.0)
        groups = [x for x in items if isinstance(x, PsdGroup)]
        assert len(groups) == 2
        # 奥のコマが先（＝PSD では下、一覧では下）に来る
        assert [g.name for g in groups] == ["コマ1", "コマ2"]

    def test_並べ替えても絵は変わらない(self, four_panels):
        """**並べ替えてよい根拠そのもの。** ここが崩れたら並べ替えない。"""
        page = four_panels.project.pages[0]
        layers = page_layers(four_panels, page, 1.0)
        merged = flatten(layers, round(page.size.w), round(page.size.h))
        assert max_difference(merged, render_page(four_panels, page, 1.0)) <= 1

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

    def test_作った順ではなく読み順で振る(self, four_panels):
        """下段は左を先に作ってあるが、番号は右が先（→ 10.1）。"""
        page = four_panels.project.pages[0]
        items = page_layers(four_panels, page, 1.0)
        groups = {x.name: x for x in items if isinstance(x, PsdGroup)}
        # 3枚目に作った右のコマが コマ2、2枚目に作った左のコマが コマ3
        assert groups["コマ2"].children[0].x > groups["コマ3"].children[0].x


class Test重なりの判定:
    """並べ替えてよいかの判断（→ `psd_export.panels_overlap`）。"""

    def test_離れていれば重ならない(self, four_panels):
        assert not panels_overlap(four_panels.project.pages[0].panels)

    def test_重ねていれば重なる(self, overlapping):
        assert panels_overlap(overlapping.project.pages[0].panels)

    def test_枠線の太さぶんも数える(self, qapp, tmp_path):
        """形が触れていなくても、枠線どうしが触れれば絵に出る。

        枠線は形の線の上に中心を置いて引かれるので、太さの半分だけ外へ
        はみ出す。形だけで見ると「触れていない」と誤って判断する。
        """
        from manga_layout import new_project

        project = new_project()
        page = project.pages[0]
        width = page.panels[0].border.width if page.panels else 3.5
        # 隙間は枠線の太さより狭い（＝はみ出した分どうしが触れる）
        project.add_panel(page, Rect(10.0, 10.0, 100.0, 100.0))
        project.add_panel(page, Rect(110.0 + width / 4, 10.0, 100.0, 100.0))
        assert panels_overlap(page.panels)


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
# 4-2. トーン範囲（クリスタで貼り直すためのマスク → 要件定義 6.28）
# ---------------------------------------------------------------------------


class Testトーンを4枚に分ける:
    """**絵と同じ経路で描いた3枚**であることを確かめる（→ `_Painters`）。

    別に描くと、斜めのコマ・回した絵・絞った矩形でトーンだけがずれる。
    ずれても画面には出ないので、**クリスタで貼ってから気づく**ことになる。
    """

    def test_トーンを入れると3枚増える(self, with_tone):
        names = flat_names(page_layers(with_tone, with_tone.project.pages[0], 1.0))
        assert {"白ベタ", "トーン範囲", "トーン"} <= set(names)

    def test_トーンが無ければ1枚も出ない(self, state):
        names = flat_names(page_layers(state, state.project.pages[0], 1.0))
        assert not {"白ベタ", "トーン範囲", "トーン"} & set(names)

    def test_絵にはトーンを焼かない(self, with_tone):
        """**焼いたままでは差し替えられない**（本人の指摘 2026-08-06）。

        トーンを入れた作品の「絵」が、**トーンを消した作品の「絵」と1画素も
        違わない**こと。トーンの有無で変わるなら焼き込まれている。
        """
        toned = layer_named(
            page_layers(with_tone, with_tone.project.pages[0], 1.0), "絵"
        )
        with with_tone.edit("トーンを消す") as project:
            project.pages[0].panels[0].children[0].tone = None
        plain = layer_named(
            page_layers(with_tone, with_tone.project.pages[0], 1.0), "絵"
        )
        assert channels(toned.image) == channels(plain.image)

    def test_トーン範囲だけが非表示(self, with_tone):
        """白ベタとトーンは絵の一部。目印だけが作品の中身ではない。"""
        page = with_tone.project.pages[0]
        group = next(x for x in page_layers(with_tone, page, 1.0) if isinstance(x, PsdGroup))
        assert [c.name for c in group.children if not c.visible] == ["トーン範囲"]

    def test_並びは白ベタ_トーン範囲_トーン(self, with_tone):
        """トーン範囲を選んだまま作る新しいレイヤーが、白ベタの手前に入る。"""
        items = page_layers(with_tone, with_tone.project.pages[0], 1.0)
        group = next(x for x in items if isinstance(x, PsdGroup))
        assert [c.name for c in group.children] == [
            "絵", "白ベタ", "トーン範囲", "トーン", "コマ枠",
        ]

    def test_重ね直すと縁を除いてPNGと一致する(self, with_tone):
        """**トーンの範囲の境目だけがずれる**（→ 要件定義 6.28）。

        分けると、絵とトーンが**別々に縮んでから重なる**ので、混ざる順番が
        入れ替わる。ずれるのは縁の1px ぶんだけなので、**食い違う画素が
        ページのごく一部に収まっていること**まで確かめる（面ごとずれて
        いれば、ここで捕まる）。
        """
        page = with_tone.project.pages[0]
        width, height = round(page.size.w), round(page.size.h)
        merged = flatten(page_layers(with_tone, page, 1.0), width, height)
        plain = render_page(with_tone, page, 1.0)
        assert max_difference(merged, plain) <= TONE_EDGE_TOLERANCE
        assert differing_pixels(merged, plain) < width * height * 0.02

    def test_黒ベタの所だけを指す(self, with_tone):
        """白い所まで指していたら、クリスタで貼るときに使えない。"""
        page = with_tone.project.pages[0]
        items = page_layers(with_tone, page, 1.0)
        mask = layer_named(items, "トーン範囲")
        art = layer_named(items, "絵")
        assert mask.image.width() < art.image.width(), "ベタは絵の一部"
        assert mask.image.height() < art.image.height()

    def test_白ベタはトーン範囲からはみ出さない(self, with_tone):
        """縁の中間の濃さを落としてあるので、広がることはない
        （→ `tone._fully_masked`）。はみ出すと、絵を余計に隠す。
        """
        items = page_layers(with_tone, with_tone.project.pages[0], 1.0)
        mask = layer_named(items, "トーン範囲")
        fill = layer_named(items, "白ベタ")
        assert 0 < fill.image.width() <= mask.image.width()
        assert 0 < fill.image.height() <= mask.image.height()
        assert (fill.x, fill.y) >= (mask.x, mask.y)

    def test_絞ると狭くなる(self, with_tone):
        """矩形で絞ったぶんは、3枚とも効く。"""
        page = with_tone.project.pages[0]
        before = layer_named(page_layers(with_tone, page, 1.0), "トーン範囲")
        with with_tone.edit("範囲を絞る") as project:
            project.pages[0].panels[0].children[0].tone.area = Rect(0.0, 0.0, 0.3, 1.0)
        after = layer_named(page_layers(with_tone, page, 1.0), "トーン範囲")
        assert after.image.width() < before.image.width()

    def test_絵を重ねているコマは分けない(self, with_tone, dark_png):
        """トーンの入った絵の上に別の絵があると、分けたトーンが手前に出る。

        **重なりまでは見ない**（→ `_splittable`）。外したときの結果が
        「絵の上にトーンが乗る」という気づきにくい間違いになるため。
        """
        ref, px = with_tone.import_bytes(dark_png)
        with with_tone.edit("重ねる") as project:
            panel = project.pages[0].panels[0]
            project.add_image(panel, ref, Rect(30.0, 30.0, 60.0, 60.0), px)
        names = flat_names(page_layers(with_tone, with_tone.project.pages[0], 1.0))
        assert "白ベタ" not in names and "トーン" not in names
        assert "トーン範囲" in names, "目印だけは出す"

    def test_不透明度が1未満なら分けない(self, with_tone):
        """分けると絵・白ベタ・トーンの別々のレイヤーへ不透明度が個別に
        掛かるが、焼き込み（PNG）は合成してから1回だけ掛けるため、
        分けたままでは重ね直した結果が PNG と一致しなくなる
        （2026-08-08 に発見）。
        """
        with with_tone.edit("不透明度を下げる") as project:
            project.pages[0].panels[0].children[0].opacity = 0.5
        names = flat_names(page_layers(with_tone, with_tone.project.pages[0], 1.0))
        assert "白ベタ" not in names and "トーン" not in names
        assert "トーン範囲" in names, "目印だけは出す"

    def test_不透明度が1未満でも重ね直せばPNGと一致する(self, with_tone):
        with with_tone.edit("不透明度を下げる") as project:
            project.pages[0].panels[0].children[0].opacity = 0.5
        page = with_tone.project.pages[0]
        width, height = round(page.size.w), round(page.size.h)
        layers = page_layers(with_tone, page, 1.0)
        assert max_difference(flatten(layers, width, height), render_page(with_tone, page, 1.0)) <= 1

    def test_重ねていても重ね直せばPNGと一致する(self, with_tone, dark_png):
        ref, px = with_tone.import_bytes(dark_png)
        with with_tone.edit("重ねる") as project:
            panel = project.pages[0].panels[0]
            project.add_image(panel, ref, Rect(30.0, 30.0, 60.0, 60.0), px)
        page = with_tone.project.pages[0]
        width, height = round(page.size.w), round(page.size.h)
        layers = page_layers(with_tone, page, 1.0)
        assert max_difference(flatten(layers, width, height), render_page(with_tone, page, 1.0)) <= 1


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

    def test_画像の確保に失敗すると分かるエラーになる(self, state, tmp_path, monkeypatch):
        """PNG 側（`export.render_page`）と同じ守りを PSD 側にも持たせる。

        以前はここだけ `isNull()` を確認しておらず、確保に失敗すると
        ヌルの画像のまま進んで、無関係な例外か誤った文言になっていた
        （実際の到達には巨大なページ＋メモリ逼迫が要るため、ここでは
        `isNull` を差し替えて模擬する。2026-08-08 に発見）。
        """
        from PySide6.QtGui import QImage

        monkeypatch.setattr(QImage, "isNull", lambda self: True)
        with pytest.raises(ExportError, match="確保できませんでした"):
            export_psd_pages(state, [0], tmp_path, 1.0)


class Test途中で止める:
    """`on_page` の口（→ `project_io._run_export` の進捗窓）。

    PSD は1ページ 10〜30MB あり全ページで数百MB になり得る
    （→ 要件定義 10.1）ので、進捗と中止の意味がPNGよりはっきり出る所。
    仕組み自体は `export.export_pages` と同じ（→ `tests/test_export.py`）。
    """

    def test_1枚ごとに呼ばれる(self, state, tmp_path):
        with state.edit("2ページ目") as project:
            project.pages.append(type(project.pages[0])(id="p2", size=SMALL_PAGE))
        dest = tmp_path / "出力"
        seen = []

        export_psd_pages(
            state, [0, 1], dest, 1.0, on_page=lambda d, t: seen.append((d, t)) or True
        )

        assert seen == [(1, 2), (2, 2)]

    def test_偽を返すとそこで打ち切る(self, state, tmp_path):
        with state.edit("2ページ目") as project:
            project.pages.append(type(project.pages[0])(id="p2", size=SMALL_PAGE))
        dest = tmp_path / "出力"

        written = export_psd_pages(state, [0, 1], dest, 1.0, on_page=lambda d, t: False)

        assert [p.name for p in written] == ["p01.psd"]
        assert [p.name for p in dest.iterdir()] == ["p01.psd"]

    def test_既定の倍率で書ける(self, state, tmp_path):
        path = export_psd_pages(state, [0], tmp_path)[0]
        parsed = parse_psd(path.read_bytes())
        assert parsed["width"] == round(SMALL_PAGE.w * DEFAULT_SCALE)
