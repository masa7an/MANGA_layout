"""点検（抜けチェック → 要件定義 10.1）の検証。

ここで押さえたいのは4つ。

1. **何も直さないこと。** 点検は見つけて返すだけで、コマもセリフも動かない
2. **判定を厳しくしすぎないこと。** 一覧が常に埋まると読まれなくなる。
   フキダシからのはみ出しは、字が明らかに外へ出たときだけ拾う
3. **重さの違いが消えないこと。**「実体の無い画像」と「付箋が残っている」を
   同じ並びに置かない
4. **フォント無しで確かめられること。** 検証環境には書体が1つも無い（→ 6.5）。
   はみ出し判定は字送りの計算だけでできているので、ここでテストできる
"""

from __future__ import annotations

import dataclasses

import pytest

from manga_layout import Rect, new_project
from manga_layout.check import (
    GROUP_FIX,
    GROUP_LEFTOVER,
    KIND_BLANK_PAGE,
    KIND_EMPTY_PANEL,
    KIND_EMPTY_TEXT,
    KIND_MISSING_ASSET,
    KIND_NOTE_LEFT,
    KIND_TEXT_OVERFLOW,
    headline,
    inspect_project,
    marked_page_ids,
    summary_lines,
)
from manga_layout.focus import default_focus
from manga_layout.layout import outside_page
from manga_layout.model import PageNote, PageRough

# フキダシは用紙の真ん中あたりに置く。半径 200px の円
BALLOON_RECT = Rect(300.0, 300.0, 400.0, 400.0)


def kinds(findings) -> list[str]:
    return [f.kind for f in findings]


def project_with_balloon_text(content: str, rect: Rect, direction: str = "vertical"):
    """フキダシ1つと、その上に乗せたセリフ1つ。"""
    project = new_project()
    page = project.pages[0]
    balloon = project.add_balloon(page, BALLOON_RECT)
    text = project.add_text(page, content, rect)
    text.attached_balloon_id = balloon.id
    text.direction = direction
    return project, balloon, text


class Test何も置いていないページ:
    """空のコマ1つは拾うのに白紙は素通り、では判定が逆立ちしている。"""

    def test_作った直後の白紙を拾う(self):
        project = new_project()
        found = inspect_project(project)
        assert kinds(found) == [KIND_BLANK_PAGE]
        assert found[0].object_id is None

    def test_コマが1つでもあれば白紙ではない(self):
        project = new_project()
        project.add_panel(project.pages[0], Rect(100.0, 100.0, 400.0, 400.0))
        assert KIND_BLANK_PAGE not in kinds(inspect_project(project))

    def test_フキダシだけでも白紙ではない(self):
        project = new_project()
        project.add_balloon(project.pages[0], BALLOON_RECT)
        assert KIND_BLANK_PAGE not in kinds(inspect_project(project))

    def test_ラフだけのページは白紙(self):
        """ラフは書き出しでは切られる（→ 6.23）ので、出るのは白紙のまま。"""
        project = new_project()
        project.pages[0].rough = PageRough(
            asset="assets/rough.png", rect=Rect(0.0, 0.0, 1240.0, 1754.0), src_px=(620, 877)
        )
        assert kinds(inspect_project(project)) == [KIND_BLANK_PAGE]

    def test_付箋を貼っただけでは中身にならない(self):
        project = new_project()
        project.pages[0].note = PageNote(color="blue")
        assert set(kinds(inspect_project(project))) == {KIND_BLANK_PAGE, KIND_NOTE_LEFT}


class Test何も無ければ何も出ない:
    def test_絵もセリフも入ったページで何も出ない(self):
        project = new_project()
        page = project.pages[0]
        panel = project.add_panel(page, Rect(100.0, 100.0, 600.0, 600.0))
        project.add_image(panel, "assets/a.png", Rect(110.0, 110.0, 500.0, 500.0), (100, 100))
        balloon = project.add_balloon(page, BALLOON_RECT)
        text = project.add_text(page, "あい", Rect(430.0, 430.0, 140.0, 140.0))
        text.attached_balloon_id = balloon.id

        assert inspect_project(project, lambda ref: True) == []


class Test空のままのセリフ:
    def test_空なら拾う(self):
        project, _, text = project_with_balloon_text("", Rect(430.0, 430.0, 140.0, 140.0))
        found = inspect_project(project)
        assert kinds(found) == [KIND_EMPTY_TEXT]
        assert found[0].object_id == text.id

    def test_空白だけでも拾う(self):
        project, _, _ = project_with_balloon_text("   \n ", Rect(430.0, 430.0, 140.0, 140.0))
        assert kinds(inspect_project(project)) == [KIND_EMPTY_TEXT]

    def test_空のセリフをはみ出しとして二重に数えない(self):
        """空のセリフは枠そのものが帯になる（→ `layout.text_ink_bands`）。

        枠はフキダシより大きく取られることがあり、そのまま判定に回すと
        1つのセリフが2件として並ぶ。
        """
        project, _, _ = project_with_balloon_text("", Rect(0.0, 0.0, 1200.0, 1700.0))
        assert kinds(inspect_project(project)).count(KIND_TEXT_OVERFLOW) == 0


class Testフキダシからのはみ出し:
    """**厳しくしすぎない。** 一覧が常に埋まると読まれなくなる（→ 10.1）。"""

    def test_中に収まっていれば拾わない(self):
        project, _, _ = project_with_balloon_text("あい", Rect(430.0, 430.0, 140.0, 140.0))
        assert inspect_project(project) == []

    def test_縁ぎりぎりでも拾わない(self):
        """縦書きの帯は列送りぶんの幅を持つので、角が少し出るのが普通。

        字の並びは円の中に収まっているが、帯の角は円の外に出る位置。
        ここを拾うと、普通に使っているだけで一覧が埋まる。
        """
        # 円の右端寄り。字（42px）の中心は円の内側にある
        project, _, _ = project_with_balloon_text("あ", Rect(600.0, 470.0, 60.0, 60.0))
        assert inspect_project(project) == []

    def test_大きく外に出ていれば拾う(self):
        project, _, text = project_with_balloon_text("あい", Rect(800.0, 430.0, 140.0, 140.0))
        found = inspect_project(project)
        assert kinds(found) == [KIND_TEXT_OVERFLOW]
        assert found[0].object_id == text.id

    def test_フキダシに紐づいていないセリフは対象外(self):
        """コマの外へ直接置くナレーションは、正常な使い方（→ 6.5）。"""
        project, _, text = project_with_balloon_text("あい", Rect(800.0, 430.0, 140.0, 140.0))
        text.attached_balloon_id = None
        assert inspect_project(project) == []

    def test_消えたフキダシを指していても落ちない(self):
        project, balloon, _ = project_with_balloon_text(
            "あい", Rect(800.0, 430.0, 140.0, 140.0)
        )
        page = project.pages[0]
        page.floating = [f for f in page.floating if f.id != balloon.id]
        assert inspect_project(project) == []

    def test_横書きは左右で判定しない(self):
        """横書きは Qt が行を組むので**字送りが分からない**（→ `text_ink_bands`）。

        帯の幅が枠のままなので、左右で判定すると短いセリフまで全部
        はみ出しになる。
        """
        # 枠はフキダシより横に広いが、行の帯（上下）は中に収まっている
        project, _, _ = project_with_balloon_text(
            "あい", Rect(150.0, 460.0, 700.0, 80.0), direction="horizontal"
        )
        assert inspect_project(project) == []

    def test_横書きでも上下に出ていれば拾う(self):
        project, _, _ = project_with_balloon_text(
            "あい", Rect(400.0, 900.0, 200.0, 80.0), direction="horizontal"
        )
        assert kinds(inspect_project(project)) == [KIND_TEXT_OVERFLOW]

    def test_四角いフキダシは四隅まで中(self):
        """丸い種類と違い、四角は角にも中身がある（→ `balloon_contains`）。"""
        project, balloon, _ = project_with_balloon_text(
            "あ", Rect(320.0, 320.0, 60.0, 60.0)
        )
        balloon.style = "rect"
        assert inspect_project(project) == []


class Test用紙の外は拾わない:
    """紙の端まで絵を出すコマ（断ち切り）は**普通の使い方**（2026-08-06）。

    拾うと窓が常に埋まり、埋まった窓は読まれなくなる。
    """

    def test_はみ出したコマを拾わない(self):
        project = new_project()
        page = project.pages[0]
        panel = project.add_panel(page, Rect(-100.0, 100.0, 400.0, 400.0))
        project.add_image(panel, "assets/a.png", Rect(0.0, 100.0, 300.0, 300.0), (10, 10))

        assert inspect_project(project, lambda ref: True) == []

    def test_はみ出したフキダシとセリフも拾わない(self):
        project = new_project()
        page = project.pages[0]
        balloon = project.add_balloon(page, Rect(-200.0, 1600.0, 400.0, 400.0))
        text = project.add_text(page, "あい", Rect(-150.0, 1650.0, 140.0, 140.0))
        text.attached_balloon_id = balloon.id

        assert inspect_project(project) == []

    def test_ページの大きさを変えたときの数え上げは残す(self):
        """点検から外しただけで、`layout.outside_page` は消していない。

        あちらは**その操作で外へ出た**ことをその場で知らせるもの（→ 6.1）で、
        用途が違う。
        """
        project = new_project()
        panel = project.add_panel(project.pages[0], Rect(-100.0, 100.0, 400.0, 400.0))
        assert [o.id for o in outside_page(project.pages[0])] == [panel.id]


class Test絵の入っていないコマ:
    def test_空のコマを拾う(self):
        project = new_project()
        panel = project.add_panel(project.pages[0], Rect(100.0, 100.0, 400.0, 400.0))
        found = inspect_project(project)
        assert kinds(found) == [KIND_EMPTY_PANEL]
        assert found[0].object_id == panel.id

    def test_集中線だけのコマは空ではない(self):
        """絵は入っていなくても、描くものがある。"""
        project = new_project()
        panel = project.add_panel(project.pages[0], Rect(100.0, 100.0, 400.0, 400.0))
        panel.focus_lines = default_focus()
        assert inspect_project(project) == []


class Test実体の見つからない画像:
    @pytest.fixture
    def project(self):
        project = new_project()
        page = project.pages[0]
        panel = project.add_panel(page, Rect(100.0, 100.0, 400.0, 400.0))
        project.add_image(panel, "assets/ok.png", Rect(110.0, 110.0, 300.0, 300.0), (10, 10))
        project.add_image(panel, "assets/gone.png", Rect(120.0, 120.0, 300.0, 300.0), (10, 10))
        return project

    def test_渡さなければ見ない(self, project):
        """実体を確かめられるのは画面の側だけ。

        勝手に「無事だった」と答えると、確かめた場合と区別が付かない。
        """
        assert inspect_project(project) == []

    def test_渡せば拾う(self, project):
        found = inspect_project(project, lambda ref: ref == "assets/ok.png")
        assert kinds(found) == [KIND_MISSING_ASSET]

    def test_マークも数える(self, project):
        """マークはコマの子ではないが、実体が無ければ同じように白く抜ける。"""
        project.add_sticker(
            project.pages[0], "exclamation", "assets/gone2.png", Rect(10, 10, 40, 40), (10, 10)
        )
        found = inspect_project(project, lambda ref: ref == "assets/ok.png")
        assert kinds(found) == [KIND_MISSING_ASSET, KIND_MISSING_ASSET]

    def test_書き出しの数え方と同じ関数を使う(self):
        """「実体の無い画像」の数え方が check.py と ui/export.py で
        別々に定義され、中身も一字一句同じだった。片方だけ直る危険を
        避けるため `layout.page_assets` へ集約した（2026-08-08 に発見）。
        """
        from manga_layout.layout import page_assets as layout_page_assets
        from manga_layout.ui.export import page_assets as export_page_assets

        assert export_page_assets is layout_page_assets


class Test付箋の残り:
    def test_貼ってあれば拾う(self):
        project = new_project()
        # 白紙として拾われないよう、中身を1つ置いておく
        project.add_balloon(project.pages[0], BALLOON_RECT)
        project.pages[0].note = PageNote(color="yellow", text="あとで直す")
        found = inspect_project(project)
        assert kinds(found) == [KIND_NOTE_LEFT]
        assert found[0].object_id is None


class Test並びと印:
    @pytest.fixture
    def mixed(self):
        """1ページ目に軽いもの、2ページ目に重いものを置く。"""
        project = new_project()
        first = project.pages[0]
        project.add_balloon(first, BALLOON_RECT)  # 白紙にはしない
        first.note = PageNote(color="blue")

        second = project.add_page()
        panel = project.add_panel(second, Rect(100.0, 100.0, 400.0, 400.0))
        project.add_image(panel, "assets/gone.png", Rect(110.0, 110.0, 300.0, 300.0), (10, 10))
        return project

    def test_重い順に並ぶ(self, mixed):
        """ページ番号の順ではなく、**重い順**（→ 10.1「重さの違い」）。"""
        found = inspect_project(mixed, lambda ref: False)
        assert kinds(found) == [KIND_MISSING_ASSET, KIND_NOTE_LEFT]

    def test_印はページの_id_で返す(self, mixed):
        """番号だと、点検のあとに並べ替えたとき印が別のページに残る。"""
        found = inspect_project(mixed, lambda ref: False)
        assert marked_page_ids(found) == {p.id for p in mixed.pages}

    def test_印は種類で分けない(self, mixed):
        """印は1色。分けると色の意味を覚えることになる（→ 6.18）。"""
        assert len(marked_page_ids(inspect_project(mixed, lambda ref: False))) == 2


class Test読むための文:
    def test_見つからなければそう言う(self):
        assert "見つかりませんでした" in headline([])
        assert summary_lines([]) == ["直し忘れは見つかりませんでした。"]

    def test_件数とページ数を出す(self):
        project = new_project()
        project.pages[0].note = PageNote(color="blue")
        project.add_panel(project.pages[0], Rect(100.0, 100.0, 400.0, 400.0))
        found = inspect_project(project)
        assert headline(found) == "1 ページで 2 件 見つかりました"

    def test_直し忘れと消し忘れを分けて出す(self):
        """「実体の無い画像」と「付箋が残っている」を同じ並びに置かない。"""
        project = new_project()
        project.pages[0].note = PageNote(color="blue")
        project.add_panel(project.pages[0], Rect(100.0, 100.0, 400.0, 400.0))

        lines = summary_lines(inspect_project(project))
        text = "\n".join(lines)
        assert f"■ {GROUP_FIX}" in text
        assert f"■ {GROUP_LEFTOVER}" in text
        # 直し忘れが先。重い方を上に出す
        assert text.index(f"■ {GROUP_FIX}") < text.index(f"■ {GROUP_LEFTOVER}")

    def test_同じページに複数あれば数を添える(self):
        project = new_project()
        for _ in range(3):
            project.add_panel(project.pages[0], Rect(100.0, 100.0, 400.0, 400.0))
        text = "\n".join(summary_lines(inspect_project(project)))
        assert "1ページ ×3" in text

    def test_ページ番号は_1_始まり(self):
        project = new_project()
        project.add_page()
        project.add_panel(project.pages[1], Rect(100.0, 100.0, 400.0, 400.0))
        text = "\n".join(summary_lines(inspect_project(project)))
        assert "2ページ" in text


class Test作品を変えない:
    def test_点検しても保存形式が1文字も変わらない(self):
        """**直さない**（→ 10.1）。数えるだけで、モデルには触らない。"""
        project = new_project()
        page = project.pages[0]
        page.note = PageNote(color="pink")
        panel = project.add_panel(page, Rect(-50.0, 100.0, 400.0, 400.0))
        project.add_image(panel, "assets/gone.png", Rect(0.0, 110.0, 300.0, 300.0), (10, 10))
        project.add_text(page, "", Rect(430.0, 430.0, 140.0, 140.0))

        before = project.to_dict()
        inspect_project(project, lambda ref: False)
        assert project.to_dict() == before

    def test_返ってくるものは書き換えられない(self):
        """`Finding` は凍らせてある。受け取った側が黙って書き換えられない。"""
        project = new_project()
        project.add_panel(project.pages[0], Rect(100.0, 100.0, 400.0, 400.0))
        found = inspect_project(project)
        with pytest.raises(dataclasses.FrozenInstanceError):
            found[0].kind = KIND_NOTE_LEFT
