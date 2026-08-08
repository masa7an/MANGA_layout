"""保存形式 version 1（mm）→ 2（px）の移行の検証。

単位を変えたので、それ以前に保存した作品は**開いた瞬間に別の大きさ**に
なりかねない。ここで押さえたいのは3つ。

1. **長さは残らず換算されること。** 1つ取りこぼすと、そこだけ 1/5.9 の
   大きさで残る。座標・ページ寸法・枠線・フォント・しっぽがすべて対象
2. **割合と角度には触らないこと。** 単位を持たない値に倍率を掛けると、
   斜めのコマや吹き出しの形が壊れる
3. **黙って変換しないこと。** 上書き保存すると新しい形式になるので、
   何が起きたかを利用者に伝える
"""

from __future__ import annotations

import pytest

from manga_layout import Project, Rect, SlantPair, UnsupportedVersionError, new_project
from manga_layout.model import FORMAT_VERSION, MM_TO_PX

# 旧形式（mm）で書かれた最小限の作品。手で書いた JSON を想定している
V1 = {
    "format_version": 1,
    "app": "MANGA_layout",
    "title": "旧形式の作品",
    "default_page_size": {"w": 210.0, "h": 297.0},
    "reading_direction": "rtl",
    "next_id": 9,
    "pages": [
        {
            "id": "page_0001",
            "size": {"w": 210.0, "h": 297.0},
            "panels": [
                {
                    "id": "panel_0002",
                    "type": "panel",
                    "shape": {"kind": "polygon", "points": [[10, 10], [100, 10], [100, 70], [10, 70]]},
                    "border": {"width": 0.6, "color": "#000000", "visible": True},
                    "children": [
                        {
                            "id": "img_0003",
                            "type": "image",
                            "asset": "assets/abc.png",
                            "rect": {"x": 12.0, "y": 12.0, "w": 80.0, "h": 55.0},
                            "src_px": [1200, 900],
                            "rotation": 0.0,
                            "opacity": 0.5,
                            "z": 0,
                        }
                    ],
                    "z": 0,
                }
            ],
            "floating": [
                {
                    "id": "bal_0004",
                    "type": "balloon",
                    "style": "ellipse",
                    "rect": {"x": 20.0, "y": 15.0, "w": 40.0, "h": 26.0},
                    "fill": "#FFFFFF",
                    "border": {"width": 0.4, "color": "#000000", "visible": True},
                    "tail": {"enabled": True, "tip": [55.0, 45.0], "width": 6.0, "root_y": 0.5},
                    "attached_panel_id": "panel_0002",
                    "z": 10,
                },
                {
                    "id": "txt_0005",
                    "type": "text",
                    "content": "セリフ",
                    "rect": {"x": 22.0, "y": 18.0, "w": 36.0, "h": 19.0},
                    "font": {"family": "Yu Gothic UI", "size_mm": 3.5, "bold": False},
                    "align": "center",
                    "direction": "horizontal",
                    "attached_panel_id": "panel_0002",
                    "attached_balloon_id": "bal_0004",
                    "z": 11,
                },
            ],
            "slant_pairs": [],
        }
    ],
}


@pytest.fixture
def migrated() -> Project:
    return Project.from_dict(V1)


def px(mm: float) -> float:
    return mm * MM_TO_PX


class Test長さの換算:
    """1つでも取りこぼすと、そこだけ違う大きさで残る。"""

    def test_ページの寸法(self, migrated):
        page = migrated.pages[0]
        assert page.size.w == pytest.approx(px(210.0))
        assert page.size.h == pytest.approx(px(297.0))

    def test_既定のページ寸法(self, migrated):
        assert migrated.default_page_size.w == pytest.approx(px(210.0))

    def test_コマの頂点(self, migrated):
        points = migrated.pages[0].panels[0].shape.points
        assert points[0] == pytest.approx((px(10.0), px(10.0)))
        assert points[2] == pytest.approx((px(100.0), px(70.0)))

    def test_コマの枠線(self, migrated):
        assert migrated.pages[0].panels[0].border.width == pytest.approx(px(0.6))

    def test_画像の矩形(self, migrated):
        image = migrated.pages[0].panels[0].children[0]
        assert image.rect == Rect(
            pytest.approx(px(12.0)),
            pytest.approx(px(12.0)),
            pytest.approx(px(80.0)),
            pytest.approx(px(55.0)),
        )

    def test_吹き出しの矩形と枠線(self, migrated):
        balloon = migrated.pages[0].floating[0]
        assert balloon.rect.w == pytest.approx(px(40.0))
        assert balloon.border.width == pytest.approx(px(0.4))

    def test_しっぽの先端と幅(self, migrated):
        tail = migrated.pages[0].floating[0].tail
        assert tail.tip == pytest.approx((px(55.0), px(45.0)))
        assert tail.width == pytest.approx(px(6.0))

    def test_セリフの矩形(self, migrated):
        text = migrated.pages[0].floating[1]
        assert text.rect.w == pytest.approx(px(36.0))

    def test_フォントは名前ごと移る(self, migrated):
        """`size_mm` を `size_px` として読み、値も換算する。"""
        font = migrated.pages[0].floating[1].font
        assert font.size_px == pytest.approx(px(3.5))
        assert font.family == "Yu Gothic UI"


class Test触らないもの:
    """単位を持たない値に倍率を掛けると、意味が壊れる。"""

    def test_しっぽの付け根の割合(self, migrated):
        assert migrated.pages[0].floating[0].tail.root_y == 0.5

    def test_画像の不透明度(self, migrated):
        assert migrated.pages[0].panels[0].children[0].opacity == 0.5

    def test_元画像のピクセル寸法(self, migrated):
        """`src_px` は最初から px。掛けると縦横比の計算が狂う。"""
        assert migrated.pages[0].panels[0].children[0].src_px == (1200, 900)

    def test_id_と採番(self, migrated):
        assert migrated.next_id == 9
        assert migrated.pages[0].panels[0].id == "panel_0002"

    def test_紐づけ(self, migrated):
        text = migrated.pages[0].floating[1]
        assert text.attached_panel_id == "panel_0002"
        assert text.attached_balloon_id == "bal_0004"


class Test欠けた項目の既定値:
    """v1 ファイルで文字・枠線・しっぽ幅が丸ごと欠けている場合。

    `Panel.from_dict` などの「項目が無いときの既定値」は今の px 基準の値
    （例: `Border().width == 3.5`）。素朴に読むと、これが換算（≈5.9倍）の
    対象に入り、文字サイズの既定が 42 × 5.9 ≈ 248px になるなど、値が
    大きく壊れる（2026-08-08 に発見）。

    ここでは**欠けている**場合だけを見る。値がある場合は
    `Test長さの換算` が別に押さえている。
    """

    def _v1_with(self, **overrides) -> dict:
        import copy

        data = copy.deepcopy(V1)
        panel = data["pages"][0]["panels"][0]
        balloon = data["pages"][0]["floating"][0]
        text = data["pages"][0]["floating"][1]
        if overrides.get("no_panel_border"):
            del panel["border"]
        if overrides.get("no_balloon_border"):
            del balloon["border"]
        if overrides.get("no_tail_width"):
            del balloon["tail"]["width"]
        if overrides.get("no_font"):
            del text["font"]
        return data

    def test_コマの枠線が欠けていれば今の既定になる(self):
        project = Project.from_dict(self._v1_with(no_panel_border=True))
        assert project.pages[0].panels[0].border.width == pytest.approx(3.5)

    def test_吹き出しの枠線が欠けていれば今の既定になる(self):
        project = Project.from_dict(self._v1_with(no_balloon_border=True))
        assert project.pages[0].floating[0].border.width == pytest.approx(2.5)

    def test_しっぽ幅が欠けていれば今の既定になる(self):
        project = Project.from_dict(self._v1_with(no_tail_width=True))
        assert project.pages[0].floating[0].tail.width == pytest.approx(35.0)

    def test_フォントが丸ごと欠けていれば今の既定になる(self):
        project = Project.from_dict(self._v1_with(no_font=True))
        assert project.pages[0].floating[1].font.size_px == pytest.approx(42.0)

    def test_値があれば今までどおり換算される(self):
        """欠けた項目の対処が、ある項目の換算を壊していないこと。"""
        project = Project.from_dict(self._v1_with())
        assert project.pages[0].panels[0].border.width == pytest.approx(px(0.6))
        assert project.pages[0].floating[1].font.size_px == pytest.approx(px(3.5))


class Test斜めのコマ:
    """`ratio` と `angle` は単位を持たない。"""

    def test_割合と角度はそのまま(self):
        project = new_project()
        page = project.pages[0]
        left = project.add_panel(page, Rect(0.0, 0.0, 100.0, 100.0))
        right = project.add_panel(page, Rect(100.0, 0.0, 100.0, 100.0))
        page.slant_pairs.append(SlantPair(left.id, right.id, 0.4, 12.0, "/"))

        data = project.to_dict()
        data["format_version"] = 1
        restored = Project.from_dict(data)

        pair = restored.pages[0].slant_pairs[0]
        assert pair.ratio == 0.4
        assert pair.angle == 12.0
        # 長さのほうは換算されている
        assert restored.pages[0].panels[0].shape.bounds().w == pytest.approx(px(100.0))


class Test知らせる:
    def test_換算したことを伝える(self, migrated):
        assert any("px に換算" in w for w in migrated.load_warnings)

    def test_新しい形式では何も言わない(self):
        project = Project.from_dict(V1 | {"format_version": FORMAT_VERSION})
        assert project.load_warnings == []

    def test_保存し直すと新しい形式になる(self, migrated):
        assert migrated.to_dict()["format_version"] == FORMAT_VERSION

    def test_二度は換算しない(self, migrated):
        """一度開いて保存した作品を、もう一度開いても大きさが変わらない。"""
        again = Project.from_dict(migrated.to_dict())
        assert again.pages[0].size.w == pytest.approx(migrated.pages[0].size.w)

    def test_新しすぎる形式は断る(self):
        with pytest.raises(UnsupportedVersionError):
            Project.from_dict(V1 | {"format_version": FORMAT_VERSION + 1})


class Test換算の倍率:
    def test_150dpi換算(self):
        assert MM_TO_PX == pytest.approx(150.0 / 25.4)

    def test_A4はページ既定とほぼ一致する(self):
        """`PAGE_SIZES["A4"]` は同じ換算を丸めた値。

        ずれていると、古い作品を開いたときだけ用紙の選択が
        「カスタム」になる。
        """
        assert px(210.0) == pytest.approx(1240.0, abs=0.5)
        assert px(297.0) == pytest.approx(1754.0, abs=0.5)


def test_サンプルは旧形式のまま置いてある():
    """`samples/basic` を移行の実地確認に使っている（tests/test_ui.py）。

    新形式に書き換えると、その確認が効かなくなる。
    """
    import json

    from tests.conftest import REPO_ROOT

    data = json.loads(
        (REPO_ROOT / "samples" / "basic" / "project.json").read_text(encoding="utf-8")
    )
    assert data["format_version"] == 1
    assert data["default_page_size"] == {"w": 210.0, "h": 297.0}
