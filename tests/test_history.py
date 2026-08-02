"""Undo / Redo の検証。"""

from __future__ import annotations

import pytest

from manga_layout import BalloonObject, History, Rect, TextObject, new_project
from manga_layout.history import DEFAULT_LIMIT


def _panel_x(history: History) -> float:
    return history.project.pages[0].panels[0].shape.bounds().x


class TestBasics:
    def test_最初は戻せない(self, sample_project):
        history = History(sample_project)
        assert not history.can_undo
        assert not history.can_redo
        assert history.undo() is None
        assert history.redo() is None

    def test_1手戻せる(self, sample_project):
        history = History(sample_project)
        before = _panel_x(history)

        with history.edit("コマの移動") as project:
            page = project.pages[0]
            page.move_panel(page.panels[0].id, 30.0, 0.0)

        assert _panel_x(history) == pytest.approx(before + 30.0)

        assert history.undo() == "コマの移動"
        assert _panel_x(history) == pytest.approx(before)

    def test_やり直せる(self, sample_project):
        history = History(sample_project)
        before = _panel_x(history)

        with history.edit("コマの移動") as project:
            page = project.pages[0]
            page.move_panel(page.panels[0].id, 30.0, 0.0)

        history.undo()
        assert history.redo() == "コマの移動"
        assert _panel_x(history) == pytest.approx(before + 30.0)

    def test_操作の名前が取り出せる(self, sample_project):
        # メニューに「元に戻す: コマの追加」と出すため
        history = History(sample_project)
        with history.edit("コマの追加") as project:
            project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))

        assert history.undo_label == "コマの追加"
        assert history.redo_label is None
        history.undo()
        assert history.undo_label is None
        assert history.redo_label == "コマの追加"

    def test_何手でも戻れる(self, sample_project):
        history = History(sample_project)
        for n in range(10):
            with history.edit(f"{n}手目") as project:
                project.add_panel(project.pages[0], Rect(float(n), 0.0, 10.0, 10.0))

        assert len(history.project.pages[0].panels) == 11
        for _ in range(10):
            history.undo()
        assert len(history.project.pages[0].panels) == 1


class TestSnapshotFidelity:
    def test_全項目が戻る(self, sample_project):
        # 保存形式を経由しているので、保存できるものは必ず戻る
        history = History(sample_project)
        page = history.project.pages[0]
        balloon = next(f for f in page.floating if isinstance(f, BalloonObject))
        text = next(f for f in page.floating if isinstance(f, TextObject))
        before = (
            balloon.tail.tip,
            balloon.style,
            text.content,
            text.font.size_px,
            page.panels[0].border.width,
        )

        with history.edit("まとめて変更") as project:
            p = project.pages[0]
            b = next(f for f in p.floating if isinstance(f, BalloonObject))
            t = next(f for f in p.floating if isinstance(f, TextObject))
            b.tail = b.tail.translated(100.0, 100.0)
            b.style = "jagged"
            t.content = "書き換えた"
            t.font.size_px = 54.0
            p.panels[0].border.width = 2.0

        history.undo()

        page = history.project.pages[0]
        balloon = next(f for f in page.floating if isinstance(f, BalloonObject))
        text = next(f for f in page.floating if isinstance(f, TextObject))
        assert (
            balloon.tail.tip,
            balloon.style,
            text.content,
            text.font.size_px,
            page.panels[0].border.width,
        ) == before

    def test_採番の続きも戻る(self, sample_project):
        # 戻さないと、Undo 後に作ったオブジェクトが消したものと同じ ID を持つ
        history = History(sample_project)
        next_before = history.project.next_id

        with history.edit("コマの追加") as project:
            project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
        history.undo()

        assert history.project.next_id == next_before

    def test_ページの並べ替えも戻る(self, sample_project):
        history = History(sample_project)
        sample_project.add_page()
        history.commit("ページの追加")
        order_before = [p.id for p in history.project.pages]

        with history.edit("ページの並べ替え") as project:
            project.move_page(1, 0)
        assert [p.id for p in history.project.pages] != order_before

        history.undo()
        assert [p.id for p in history.project.pages] == order_before

    def test_戻した後の状態は独立している(self, sample_project):
        history = History(sample_project)
        with history.edit("移動") as project:
            page = project.pages[0]
            page.move_panel(page.panels[0].id, 30.0, 0.0)
        history.undo()

        # 戻した状態をさらに編集しても、履歴に積まれた状態は汚れない
        page = history.project.pages[0]
        page.move_panel(page.panels[0].id, 5.0, 0.0)
        history.commit("別の移動")
        history.undo()

        assert _panel_x(history) == pytest.approx(10.0)


class TestNoChange:
    def test_変化がなければ積まない(self, sample_project):
        # ドラッグして元の位置に戻した場合など
        history = History(sample_project)
        assert history.commit("何もしていない") is False
        assert not history.can_undo

    def test_やり直しは新しい操作で消える(self, sample_project):
        history = History(sample_project)
        with history.edit("1つ目") as project:
            project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
        history.undo()
        assert history.can_redo

        with history.edit("2つ目") as project:
            project.add_panel(project.pages[0], Rect(50.0, 0.0, 10.0, 10.0))

        assert not history.can_redo


class TestMerge:
    def test_同じ鍵の入力はまとまる(self, sample_project):
        # セリフを1文字ずつ積むと、Undo 1回で1文字しか戻らない
        history = History(sample_project)
        text = next(f for f in history.project.pages[0].floating if isinstance(f, TextObject))
        key = f"text:{text.id}"

        for word in ("こ", "こん", "こんに", "こんにち", "こんにちは"):
            obj = next(
                f for f in history.project.pages[0].floating if isinstance(f, TextObject)
            )
            obj.content = word
            history.commit("セリフの入力", merge_key=key)

        assert history.depth == 1
        history.undo()
        restored = next(
            f for f in history.project.pages[0].floating if isinstance(f, TextObject)
        )
        assert restored.content == "テスト\nセリフ"

    def test_鍵を区切れば別の手になる(self, sample_project):
        history = History(sample_project)
        text = next(f for f in history.project.pages[0].floating if isinstance(f, TextObject))
        key = f"text:{text.id}"

        text.content = "あ"
        history.commit("セリフの入力", merge_key=key)
        history.break_merge()

        obj = next(f for f in history.project.pages[0].floating if isinstance(f, TextObject))
        obj.content = "あい"
        history.commit("セリフの入力", merge_key=key)

        assert history.depth == 2
        history.undo()
        restored = next(
            f for f in history.project.pages[0].floating if isinstance(f, TextObject)
        )
        assert restored.content == "あ"

    def test_別の操作が挟まればまとまらない(self, sample_project):
        history = History(sample_project)
        text = next(f for f in history.project.pages[0].floating if isinstance(f, TextObject))
        key = f"text:{text.id}"

        text.content = "あ"
        history.commit("セリフの入力", merge_key=key)

        history.project.add_panel(history.project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
        history.commit("コマの追加")

        obj = next(f for f in history.project.pages[0].floating if isinstance(f, TextObject))
        obj.content = "あい"
        history.commit("セリフの入力", merge_key=key)

        assert history.depth == 3

    def test_戻したあとはまとまらない(self, sample_project):
        history = History(sample_project)
        text = next(f for f in history.project.pages[0].floating if isinstance(f, TextObject))
        key = f"text:{text.id}"

        text.content = "あ"
        history.commit("セリフの入力", merge_key=key)
        history.undo()

        obj = next(f for f in history.project.pages[0].floating if isinstance(f, TextObject))
        obj.content = "い"
        history.commit("セリフの入力", merge_key=key)

        assert history.depth == 1
        history.undo()
        restored = next(
            f for f in history.project.pages[0].floating if isinstance(f, TextObject)
        )
        assert restored.content == "テスト\nセリフ"


class TestFailedEdit:
    def test_途中で失敗しても半端な状態が残らない(self, sample_project):
        history = History(sample_project)
        before = len(history.project.pages[0].panels)

        with pytest.raises(KeyError):
            with history.edit("失敗する操作") as project:
                project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
                project.pages[0].move_panel("panel_9999", 10.0, 0.0)  # 無いコマ

        assert len(history.project.pages[0].panels) == before
        assert not history.can_undo

    def test_失敗した操作は履歴に残らない(self, sample_project):
        history = History(sample_project)
        with history.edit("成功する操作") as project:
            project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))

        with pytest.raises(KeyError):
            with history.edit("失敗する操作") as project:
                project.pages[0].move_panel("panel_9999", 10.0, 0.0)

        assert history.depth == 1
        assert history.undo_label == "成功する操作"


class TestLimit:
    def test_上限を超えたら古い手から捨てる(self, sample_project):
        history = History(sample_project, limit=5)
        for n in range(8):
            with history.edit(f"{n}手目") as project:
                project.add_panel(project.pages[0], Rect(float(n), 0.0, 10.0, 10.0))

        assert history.depth == 5
        # 直近5手だけ戻れる
        assert history.undo_label == "7手目"
        for _ in range(5):
            history.undo()
        assert not history.can_undo
        # 捨てられた3手ぶんは残ったまま
        assert len(history.project.pages[0].panels) == 4

    def test_既定は50手(self):
        assert DEFAULT_LIMIT == 50

    def test_上限0は作れない(self, sample_project):
        with pytest.raises(ValueError):
            History(sample_project, limit=0)


class TestDirty:
    def test_変更すると未保存になる(self, sample_project):
        history = History(sample_project)
        assert not history.is_dirty

        with history.edit("コマの追加") as project:
            project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
        assert history.is_dirty

        history.mark_saved()
        assert not history.is_dirty

    def test_保存した地点へ戻れば未保存が解ける(self, sample_project):
        history = History(sample_project)
        with history.edit("1つ目") as project:
            project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
        history.mark_saved()

        with history.edit("2つ目") as project:
            project.add_panel(project.pages[0], Rect(50.0, 0.0, 10.0, 10.0))
        assert history.is_dirty

        history.undo()
        assert not history.is_dirty


class TestMemory:
    def test_30ページの作品でも履歴が軽い(self):
        """要件定義が想定する規模で、50手ぶんの履歴が現実的な大きさに収まるか。

        辞書のまま持つと 1手あたり数 MB になり 50手で数百 MB に達する。
        保存形式の文字列にして圧縮することで 2 桁小さくしている。
        """
        project = new_project(title="30ページの読み切り")
        for _ in range(29):
            project.add_page()
        for page in project.pages:
            for row in range(4):
                panel = project.add_panel(page, Rect(15.0, 15.0 + row * 68.0, 180.0, 64.0))
                project.add_balloon(page, Rect(20.0, 20.0, 42.0, 24.0), attached_panel_id=panel.id)
                project.add_text(
                    page, "テストのセリフです", Rect(23.0, 23.0, 36.0, 18.0),
                    attached_panel_id=panel.id,
                )

        history = History(project)
        for n in range(DEFAULT_LIMIT):
            with history.edit(f"{n}手目") as p:
                page = p.pages[n % len(p.pages)]
                page.move_panel(page.panels[0].id, 1.0, 0.0)

        assert history.depth == DEFAULT_LIMIT
        # 50手ぶんで 20MB を超えるようなら方式を見直す
        assert history.memory_bytes() < 20 * 1024 * 1024
