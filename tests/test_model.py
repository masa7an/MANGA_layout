"""データモデルの検証。

とくに次の2点を重点的に見る。
- `to_dict()` / `from_dict()` の往復で情報が落ちないこと（Undo の土台）
- コマ移動で、中の画像と紐づいた吹き出し・セリフが一緒に動くこと（要件定義 4章）
"""

from __future__ import annotations

import pytest

from manga_layout import (
    BalloonObject,
    ImageObject,
    Panel,
    Project,
    Rect,
    Size,
    TextObject,
    new_project,
)
from manga_layout.errors import ProjectFormatError, UnsupportedVersionError


class TestIds:
    def test_採番はすべて重複しない(self):
        project = new_project()
        page = project.pages[0]
        panel = project.add_panel(page, Rect(0.0, 0.0, 10.0, 10.0))
        ids = [
            page.id,
            panel.id,
            project.add_image(panel, "assets/a.png", Rect(0.0, 0.0, 1.0, 1.0), (10, 10)).id,
            project.add_balloon(page, Rect(0.0, 0.0, 1.0, 1.0)).id,
            project.add_text(page, "あ", Rect(0.0, 0.0, 1.0, 1.0)).id,
            project.add_panel(page, Rect(0.0, 0.0, 10.0, 10.0)).id,
        ]
        assert len(set(ids)) == len(ids)

    def test_接頭辞で種別が分かる(self):
        project = new_project()
        page = project.pages[0]
        panel = project.add_panel(page, Rect(0.0, 0.0, 10.0, 10.0))
        assert page.id.startswith("page_")
        assert panel.id.startswith("panel_")
        assert project.add_balloon(page, Rect(0.0, 0.0, 1.0, 1.0)).id.startswith("bal_")
        assert project.add_text(page, "", Rect(0.0, 0.0, 1.0, 1.0)).id.startswith("txt_")

    def test_吹き出しとセリフはコマより手前に来る(self):
        project = new_project()
        page = project.pages[0]
        panel = project.add_panel(page, Rect(0.0, 0.0, 10.0, 10.0))
        balloon = project.add_balloon(page, Rect(0.0, 0.0, 1.0, 1.0))
        text = project.add_text(page, "", Rect(0.0, 0.0, 1.0, 1.0))
        assert panel.z < balloon.z < text.z


class TestRoundTrip:
    def test_往復しても一致する(self, sample_project):
        restored = Project.from_dict(sample_project.to_dict())
        assert restored.to_dict() == sample_project.to_dict()

    def test_複製は独立している(self, sample_project):
        clone = sample_project.copy()
        clone.pages[0].panels[0].border.width = 99.0
        clone.pages[0].floating[0].rect = Rect(0.0, 0.0, 1.0, 1.0)
        assert sample_project.pages[0].panels[0].border.width == 0.6
        assert sample_project.pages[0].floating[0].rect.w == 40.0

    def test_複製で採番の続きが保たれる(self, sample_project):
        # Undo で戻したあとに作ったオブジェクトの ID が、
        # 戻す前のものと衝突しないことを保証する
        clone = sample_project.copy()
        assert clone.next_id == sample_project.next_id
        new_id = clone.add_panel(clone.pages[0], Rect(0.0, 0.0, 1.0, 1.0)).id
        assert sample_project.pages[0].find(new_id) is None

    def test_日本語のセリフが保たれる(self, sample_project):
        restored = Project.from_dict(sample_project.to_dict())
        text = next(f for f in restored.pages[0].floating if isinstance(f, TextObject))
        assert text.content == "テスト\nセリフ"

    def test_しっぽの先端が保たれる(self, sample_project):
        restored = Project.from_dict(sample_project.to_dict())
        balloon = next(f for f in restored.pages[0].floating if isinstance(f, BalloonObject))
        assert balloon.tail.tip == (55.0, 45.0)

    def test_縦書き指定を受け入れる(self):
        # MVP では描画しないが、保存形式としては今から通す必要がある
        data = new_project().to_dict()
        page = data["pages"][0]
        page["floating"].append(
            {
                "id": "txt_0900",
                "type": "text",
                "content": "縦書き",
                "rect": {"x": 0, "y": 0, "w": 10, "h": 10},
                "font": {"family": "Yu Gothic UI", "size_mm": 3.5, "bold": False},
                "align": "center",
                "direction": "vertical",
                "attached_panel_id": None,
                "z": 10,
            }
        )
        data["next_id"] = 901
        restored = Project.from_dict(data)
        assert restored.pages[0].floating[0].direction == "vertical"


class TestPanelMove:
    def test_コマを動かすと中の画像も動く(self, sample_project):
        page = sample_project.pages[0]
        panel = page.panels[0]
        image = panel.children[0]
        before = image.rect

        page.move_panel(panel.id, 20.0, -5.0)

        assert panel.shape.bounds().x == pytest.approx(30.0)
        assert image.rect.x == pytest.approx(before.x + 20.0)
        assert image.rect.y == pytest.approx(before.y - 5.0)

    def test_紐づいた吹き出しとセリフも動く(self, sample_project):
        page = sample_project.pages[0]
        panel = page.panels[0]
        balloon = next(f for f in page.floating if isinstance(f, BalloonObject))
        text = next(f for f in page.floating if isinstance(f, TextObject))
        before = (balloon.rect.x, text.rect.x)

        page.move_panel(panel.id, 20.0, 0.0)

        assert balloon.rect.x == pytest.approx(before[0] + 20.0)
        assert text.rect.x == pytest.approx(before[1] + 20.0)

    def test_しっぽの先端も一緒に動く(self, sample_project):
        # 先端はページ座標なので、動かさないとしっぽが伸びて形が崩れる
        page = sample_project.pages[0]
        balloon = next(f for f in page.floating if isinstance(f, BalloonObject))
        page.move_panel(page.panels[0].id, 20.0, -5.0)
        assert balloon.tail.tip == pytest.approx((75.0, 40.0))

    def test_紐づいていない吹き出しは動かない(self, sample_project):
        page = sample_project.pages[0]
        free = sample_project.add_balloon(page, Rect(100.0, 100.0, 20.0, 20.0))
        page.move_panel(page.panels[0].id, 20.0, 0.0)
        assert free.rect.x == 100.0

    def test_コマを消してもセリフは残る(self, sample_project):
        # セリフはコマより手間がかかっているので、巻き添えで消さない
        page = sample_project.pages[0]
        panel = page.panels[0]
        floating_before = len(page.floating)

        page.remove_panel(panel.id)

        assert page.panels == []
        assert len(page.floating) == floating_before
        assert all(f.attached_panel_id is None for f in page.floating)


class TestPages:
    def test_ページを並べ替えられる(self):
        project = new_project()
        second = project.add_page()
        third = project.add_page()
        first = project.pages[0]

        project.move_page(2, 0)

        assert [p.id for p in project.pages] == [third.id, first.id, second.id]

    def test_範囲外の並べ替えを拒む(self):
        project = new_project()
        with pytest.raises(IndexError):
            project.move_page(0, 5)

    def test_ページごとに寸法を変えられる(self):
        project = new_project()
        b5 = project.add_page(size=Size(182.0, 257.0))
        assert project.pages[0].size == Size(210.0, 297.0)
        assert b5.size == Size(182.0, 257.0)


class TestValidation:
    def test_新しすぎる形式は開かない(self):
        # 知らない項目を捨てて保存し直す事故を防ぐため、読み込み自体を断る
        data = new_project().to_dict()
        data["format_version"] = 99
        with pytest.raises(UnsupportedVersionError, match="version 99"):
            Project.from_dict(data)

    def test_ID重複は開かない(self):
        data = new_project().to_dict()
        shape = {"kind": "polygon", "points": [[0, 0], [10, 0], [10, 10], [0, 10]]}
        data["pages"][0]["panels"] = [
            {"id": "panel_0002", "type": "panel", "shape": shape, "z": 0, "children": []},
            {"id": "panel_0002", "type": "panel", "shape": shape, "z": 1, "children": []},
        ]
        data["next_id"] = 10
        with pytest.raises(ProjectFormatError, match="ID が重複"):
            Project.from_dict(data)

    def test_知らない種別は開かない(self):
        data = new_project().to_dict()
        data["pages"][0]["floating"] = [{"id": "eff_0002", "type": "effect"}]
        with pytest.raises(ProjectFormatError, match="知らない種別"):
            Project.from_dict(data)

    def test_知らない整列指定は開かない(self):
        data = new_project().to_dict()
        data["pages"][0]["floating"] = [
            {
                "id": "txt_0002",
                "type": "text",
                "content": "",
                "rect": {"x": 0, "y": 0, "w": 1, "h": 1},
                "align": "justify",
            }
        ]
        with pytest.raises(ProjectFormatError, match="align"):
            Project.from_dict(data)

    def test_不正な色は開かない(self):
        data = new_project().to_dict()
        data["pages"][0]["panels"] = [
            {
                "id": "panel_0002",
                "type": "panel",
                "shape": {"kind": "polygon", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]},
                "border": {"width": 0.6, "color": "black", "visible": True},
                "z": 0,
                "children": [],
            }
        ]
        with pytest.raises(ProjectFormatError, match="色ではありません"):
            Project.from_dict(data)

    def test_不透明度の範囲外を拒む(self):
        data = new_project().to_dict()
        data["pages"][0]["panels"] = [
            {
                "id": "panel_0002",
                "type": "panel",
                "shape": {"kind": "polygon", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]},
                "z": 0,
                "children": [
                    {
                        "id": "img_0003",
                        "type": "image",
                        "asset": "assets/a.png",
                        "rect": {"x": 0, "y": 0, "w": 1, "h": 1},
                        "src_px": [10, 10],
                        "opacity": 1.5,
                        "z": 0,
                    }
                ],
            }
        ]
        with pytest.raises(ProjectFormatError, match="opacity"):
            Project.from_dict(data)

    def test_壊れた箇所の位置が分かる(self):
        data = new_project().to_dict()
        data["pages"][0]["panels"] = [
            {
                "id": "panel_0002",
                "type": "panel",
                "shape": {"kind": "polygon", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]},
                "z": 0,
                "children": [
                    {
                        "id": "img_0003",
                        "type": "image",
                        "asset": "assets/a.png",
                        "rect": {"x": 0, "y": 0, "w": "wide", "h": 1},
                        "src_px": [10, 10],
                        "z": 0,
                    }
                ],
            }
        ]
        with pytest.raises(ProjectFormatError) as exc:
            Project.from_dict(data)
        assert "pages[0].panels[0].children[0].rect.w" in str(exc.value)


class TestRepair:
    def test_小さすぎる採番値を直す(self):
        # 手で JSON を編集したあとなど。放置すると次の採番で ID が衝突する
        data = new_project().to_dict()
        data["pages"][0]["panels"] = [
            {
                "id": "panel_0500",
                "type": "panel",
                "shape": {"kind": "polygon", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]},
                "z": 0,
                "children": [],
            }
        ]
        data["next_id"] = 2

        project = Project.from_dict(data)

        assert project.next_id == 501
        assert any("next_id" in w for w in project.load_warnings)
        assert project.add_panel(project.pages[0], Rect(0.0, 0.0, 1.0, 1.0)).id == "panel_0501"

    def test_行き先の無い紐づけを外す(self):
        data = new_project().to_dict()
        data["pages"][0]["floating"] = [
            {
                "id": "bal_0002",
                "type": "balloon",
                "style": "ellipse",
                "rect": {"x": 0, "y": 0, "w": 10, "h": 10},
                "attached_panel_id": "panel_9999",
                "z": 10,
            }
        ]
        data["next_id"] = 100

        project = Project.from_dict(data)

        assert project.pages[0].floating[0].attached_panel_id is None
        assert any("panel_9999" in w for w in project.load_warnings)

    def test_問題がなければ警告は出ない(self, sample_project):
        assert Project.from_dict(sample_project.to_dict()).load_warnings == []


class TestAssetReferences:
    def test_参照している画像を集められる(self, sample_project):
        page = sample_project.pages[0]
        panel = sample_project.add_panel(page, Rect(0.0, 0.0, 10.0, 10.0))
        sample_project.add_image(panel, "assets/def456.png", Rect(0.0, 0.0, 5.0, 5.0), (10, 10))
        assert sample_project.referenced_assets() == {"assets/abc123.png", "assets/def456.png"}

    def test_画像だけを走査できる(self, sample_project):
        images = list(sample_project.iter_images())
        assert len(images) == 1
        assert isinstance(images[0], ImageObject)

    def test_コマの中まで_id_で探せる(self, sample_project):
        page = sample_project.pages[0]
        image_id = page.panels[0].children[0].id
        assert isinstance(page.find(image_id), ImageObject)
        assert isinstance(page.find(page.panels[0].id), Panel)
        assert page.find("存在しない") is None
