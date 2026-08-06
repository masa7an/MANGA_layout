"""バックアップからの復元（要件定義 6.6）。

確かめる筋は3つ。

1. `backup/` に何が残っているかを読み取れる（`storage.list_backups`）
2. 作品まるごとの差し替えが**履歴の1手**になる（`History.replace`）
3. 戻したあと「元に戻す」で復元前の作業に戻れる（`EditorState`）

3 が本題。1・2 はそこへ至る部品なので、壊れたときにどの段で壊れたかが
分かるよう別々に見ている。
"""

from __future__ import annotations

import json
import os

import pytest

from manga_layout import (
    History,
    Rect,
    list_backups,
    load_backup,
    new_project,
    save_project,
)
from manga_layout.errors import ProjectFormatError
from manga_layout.storage import (
    BACKUP_DIRNAME,
    BACKUP_KIND_AUTOSAVE,
    BACKUP_KIND_SAVED,
    write_autosave,
)
from manga_layout.ui import EditorState


def コマ数(project) -> int:
    return sum(len(page.panels) for page in project.pages)


@pytest.fixture
def 保存済みの作品(tmp_path, sample_project):
    """1回保存し、`backup/` に世代が1つある状態。"""
    save_project(sample_project, tmp_path)  # backup 無し（project.json だけ）
    save_project(sample_project, tmp_path)  # ここで project.1.json ができる
    return tmp_path


class Test世代の一覧:
    def test_保存済みと作業中を両方拾う(self, tmp_path, sample_project):
        save_project(sample_project, tmp_path)
        save_project(sample_project, tmp_path)
        write_autosave(sample_project, tmp_path)

        kinds = [e.kind for e in list_backups(tmp_path)]
        assert BACKUP_KIND_SAVED in kinds
        assert BACKUP_KIND_AUTOSAVE in kinds

    def test_新しい順に並ぶ(self, tmp_path, sample_project):
        # 保存済みと作業中を混ぜて日時順にする。系列ごとに分けて並べると
        # どちらが新しいのか読み取れない
        save_project(sample_project, tmp_path)
        save_project(sample_project, tmp_path)
        backup = tmp_path / BACKUP_DIRNAME
        write_autosave(sample_project, tmp_path)

        # 保存済みのほうを明確に古くする
        古い = backup / "project.1.json"
        os.utime(古い, (1_600_000_000, 1_600_000_000))

        entries = list_backups(tmp_path)
        assert [e.saved_at for e in entries] == sorted(
            (e.saved_at for e in entries), reverse=True
        )
        assert entries[-1].path == 古い

    def test_中身の手がかりを持つ(self, tmp_path, sample_project):
        save_project(sample_project, tmp_path)
        save_project(sample_project, tmp_path)
        entry = list_backups(tmp_path)[0]
        assert entry.pages == len(sample_project.pages)
        assert entry.panels == コマ数(sample_project)

    def test_壊れた世代も一覧に残る(self, tmp_path, sample_project):
        # 黙って消すと、5世代あるはずのものが4つしか出ない理由が分からない
        save_project(sample_project, tmp_path)
        save_project(sample_project, tmp_path)
        (tmp_path / BACKUP_DIRNAME / "project.1.json").write_text("{壊", encoding="utf-8")

        entries = list_backups(tmp_path)
        assert len(entries) == 1
        assert entries[0].pages is None
        assert "読めません" in entries[0].label

    def test_backupが無ければ空(self, tmp_path, sample_project):
        save_project(sample_project, tmp_path, backup=False)
        assert list_backups(tmp_path) == []


class Test世代を読む:
    def test_読み戻すと一致する(self, 保存済みの作品, sample_project):
        path = 保存済みの作品 / BACKUP_DIRNAME / "project.1.json"
        assert load_backup(path).to_dict() == sample_project.to_dict()

    def test_壊れていれば断る(self, tmp_path):
        path = tmp_path / "壊れた.json"
        path.write_text("{", encoding="utf-8")
        with pytest.raises(ProjectFormatError):
            load_backup(path)


class Test履歴への差し替え:
    def test_1手として積まれる(self, sample_project):
        history = History(new_project(title="今の作品"))
        assert history.replace(sample_project, "バックアップから復元") is True
        assert history.depth == 1
        assert history.undo_label == "バックアップから復元"
        assert history.project.title == "テスト作品"

    def test_元に戻すと復元前へ帰る(self, sample_project):
        history = History(new_project(title="今の作品"))
        history.replace(sample_project, "バックアップから復元")
        history.undo()
        assert history.project.title == "今の作品"

    def test_中身が同じなら積まない(self, sample_project):
        # 同じ内容の世代を選んだだけで履歴が1手埋まらないこと
        history = History(sample_project)
        assert history.replace(sample_project, "バックアップから復元") is False
        assert history.depth == 0

    def test_履歴は作り直されない(self, sample_project):
        # `EditorState.reset` と違い、それまでの手が残ること
        history = History(new_project())
        with history.edit("コマの追加") as project:
            project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
        history.replace(sample_project, "バックアップから復元")
        assert history.depth == 2


class Test画面からの復元:
    def _状態(self, 保存済みの作品):
        state = EditorState()
        state.load(保存済みの作品)
        return state

    def test_戻すと中身が入れ替わる(self, 保存済みの作品):
        state = self._状態(保存済みの作品)
        元のコマ数 = コマ数(state.project)
        with state.edit("コマの追加") as project:
            project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
        assert コマ数(state.project) == 元のコマ数 + 1

        entry = state.backups()[0]
        state.restore_backup(entry.path)
        assert コマ数(state.project) == 元のコマ数

    def test_元に戻すと復元前の作業が帰る(self, 保存済みの作品):
        # この機能の本題
        state = self._状態(保存済みの作品)
        with state.edit("コマの追加") as project:
            project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
        作業後 = コマ数(state.project)

        state.restore_backup(state.backups()[0].path)
        state.undo()
        assert コマ数(state.project) == 作業後

    def test_project_jsonは書き換わらない(self, 保存済みの作品):
        state = self._状態(保存済みの作品)
        with state.edit("コマの追加") as project:
            project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
        state.save()
        保存直後 = (保存済みの作品 / "project.json").read_text(encoding="utf-8")

        state.restore_backup(state.backups()[0].path)
        assert (保存済みの作品 / "project.json").read_text(encoding="utf-8") == 保存直後

    def test_戻すと未保存になる(self, 保存済みの作品):
        state = self._状態(保存済みの作品)
        with state.edit("コマの追加") as project:
            project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
        state.save()
        assert state.is_dirty is False

        state.restore_backup(state.backups()[0].path)
        assert state.is_dirty is True

    def test_選択は解除される(self, 保存済みの作品):
        # **中身が変わる状況を作ってから戻す。** 同じ内容の世代を選んだ
        # 場合は1手が積まれず後始末も走らない（→ `test_中身が同じなら積まない`）
        state = self._状態(保存済みの作品)
        with state.edit("コマの追加") as project:
            project.add_panel(project.pages[0], Rect(0.0, 0.0, 10.0, 10.0))
        state.select(state.page.panels[0].id)

        state.restore_backup(state.backups()[0].path)
        assert state.selected_id is None

    def test_ページ番号は範囲に収まる(self, 保存済みの作品):
        state = self._状態(保存済みの作品)
        with state.edit("ページの追加") as project:
            project.add_page()
            project.add_page()
        state.set_page_index(2)

        state.restore_backup(state.backups()[0].path)
        assert state.page_index < state.page_count

    def test_一覧の窓から選べる(self, 保存済みの作品, qapp):
        from manga_layout.ui.restore import RestoreDialog

        state = self._状態(保存済みの作品)
        entries = state.backups()
        dialog = RestoreDialog(entries)
        # 一番新しいものが選ばれている。戻したい先はたいてい直前
        assert dialog.chosen() is entries[0]

    def test_読めない世代は選べない(self, 保存済みの作品, qapp):
        from manga_layout.ui.restore import RestoreDialog

        (保存済みの作品 / BACKUP_DIRNAME / "project.1.json").write_text(
            "{壊", encoding="utf-8"
        )
        state = self._状態(保存済みの作品)
        dialog = RestoreDialog(state.backups())
        assert dialog.chosen() is None
        assert not dialog.buttons.button(
            dialog.buttons.StandardButton.Ok
        ).isEnabled()

    def test_窓を開けている間は自動バックアップを止める(self, 保存済みの作品, qapp):
        """一覧を出してから戻すまでの間に世代が繰り下がらないこと。

        窓は modal だが Qt のタイマーは止まらないので、開けたままにすると
        裏で `autosave.N.json` が繰り下がり、**一覧に出ていた行とは別の
        中身**が入る（実測で「15コマ」の行を選んで 18 コマが入った）。
        """
        from PySide6.QtWidgets import QDialog

        from manga_layout.ui import MainWindow
        from manga_layout.ui.restore import RestoreDialog

        window = MainWindow(EditorState())
        window.state.load(保存済みの作品)
        assert window.files.autosave_timer.isActive()

        止まっていたか: list[bool] = []
        RestoreDialog.exec = lambda self: (
            止まっていたか.append(not window.files.autosave_timer.isActive()),
            QDialog.DialogCode.Rejected,
        )[1]
        try:
            window.files.restore_backup()
        finally:
            del RestoreDialog.exec

        assert 止まっていたか == [True]
        # 閉じたら元どおり動き出すこと。止めたままにすると、以降まったく
        # 退避されなくなる
        assert window.files.autosave_timer.isActive()

    def test_選んだ世代が退避で押し出されない(self, 保存済みの作品, sample_project):
        """**順序の検証。** 復元前の退避を先にやると世代が繰り下がり、
        一番古い作業中の世代は最古として消える。読むのが先であること。
        """
        # 作業中の世代を上限（3つ）まで埋める。一番古いものを目印付きにする
        目印 = new_project(title="いちばん古い作業中")
        write_autosave(目印, 保存済みの作品)
        write_autosave(sample_project, 保存済みの作品)
        write_autosave(sample_project, 保存済みの作品)

        state = self._状態(保存済みの作品)
        古い = 保存済みの作品 / BACKUP_DIRNAME / "autosave.3.json"
        assert json.loads(古い.read_text(encoding="utf-8"))["title"] == "いちばん古い作業中"

        state.restore_backup(古い)
        assert state.project.title == "いちばん古い作業中"
