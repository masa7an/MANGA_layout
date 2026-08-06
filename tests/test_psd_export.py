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
from manga_layout.psd import PsdLayer
from manga_layout.ui import EditorState
from manga_layout.ui.export import DEFAULT_SCALE, HIGH_SCALE, export_pages, render_page
from manga_layout.ui.psd_export import (
    export_psd_pages,
    flatten,
    page_layers,
)
from tests.test_psd import parse_psd

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


def names_of(layers: list[PsdLayer]) -> list[str]:
    return [layer.name for layer in layers]


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

        assert "ラフ" in names_of(layers)
        assert max_difference(merged, render_page(state, page, 1.0)) <= 1


# ---------------------------------------------------------------------------
# 2. 並びと名前
# ---------------------------------------------------------------------------


class Test並びと名前:
    def test_描く順のまま下から並ぶ(self, with_focus):
        assert names_of(page_layers(with_focus, with_focus.project.pages[0], 1.0)) == [
            "用紙",
            "絵",
            "集中線・流線",
            "コマ枠",
            "フキダシ",
            "セリフ",
        ]

    def test_ラフは用紙のすぐ上(self, state, png_bytes):
        """コマより奥。なぞる相手なので、絵の下に敷く（→ 6.23）。"""
        state.place_rough(png_bytes)
        names = names_of(page_layers(state, state.project.pages[0], 1.0))
        assert names[:3] == ["用紙", "ラフ", "絵"]

    def test_古い名前欄には英字を入れる(self, state):
        """日本語が読めないソフトでも役割が分かるように（→ `psd.PsdLayer`）。"""
        layers = page_layers(state, state.project.pages[0], 1.0)
        assert [layer.alias for layer in layers] == [
            "paper",
            "art",
            "frames",
            "balloons",
            "text",
        ]
        assert all(layer.alias.isascii() for layer in layers)


# ---------------------------------------------------------------------------
# 3. 中身の無いレイヤーは出さない
# ---------------------------------------------------------------------------


class Test中身の無いレイヤー:
    def test_置いていない種類は出ない(self, state):
        """集中線もマークもラフも置いていない作品。"""
        names = names_of(page_layers(state, state.project.pages[0], 1.0))
        assert "集中線・流線" not in names
        assert "マーク" not in names
        assert "ラフ" not in names

    def test_何も無いページでも用紙だけは出る(self, qapp, tmp_path):
        editor = EditorState()
        editor.save(tmp_path / "作品")
        layers = page_layers(editor, editor.project.pages[0], 0.5)
        assert names_of(layers) == ["用紙"]

    def test_集中線を入れると増える(self, state, with_focus):
        assert "集中線・流線" in names_of(page_layers(with_focus, with_focus.project.pages[0], 1.0))


# ---------------------------------------------------------------------------
# 4. ラフは非表示・切り詰め
# ---------------------------------------------------------------------------


class Testレイヤーの持ち方:
    def test_ラフだけが非表示(self, state, png_bytes):
        state.place_rough(png_bytes)
        layers = page_layers(state, state.project.pages[0], 1.0)
        hidden = [layer.name for layer in layers if not layer.visible]
        assert hidden == ["ラフ"]

    def test_透明な縁は落ちる(self, state):
        """キャンバス全面のまま持つと、枚数ぶんファイルが膨らむ。"""
        page = state.project.pages[0]
        layers = {layer.name: layer for layer in page_layers(state, page, 1.0)}

        balloon = layers["フキダシ"]
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
        layers = {layer.name: layer for layer in page_layers(state, page, 1.0)}
        frames = layers["コマ枠"]
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
        assert [layer["name"] for layer in parsed["layers"]] == [
            "用紙",
            "絵",
            "集中線・流線",
            "コマ枠",
            "フキダシ",
            "セリフ",
        ]

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
