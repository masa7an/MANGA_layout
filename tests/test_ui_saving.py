"""「名前を付けて保存」の検証。

作品は1個のファイルではなくフォルダなので、普通の「ファイルを保存」の窓が
使えない。かといって「既にあるフォルダを選ぶ」窓にすると、名前を打つ欄が
無く、選んだ瞬間にそこへ書き込まれる。ここで押さえたいのは3つ。

1. **名前を打てること。** 打った名前でフォルダが作られる
2. **作れないものを押させないこと。** OS に断られてから理由を出しても、
   直す場所（名前の欄）は既に閉じている
3. **他人の作品を黙って潰さないこと。** 同名の作品があれば必ず確認する
"""

from __future__ import annotations

import pathlib

import pytest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox

from manga_layout import Rect, is_project_dir
from manga_layout.recent_project import load_recent_project
from manga_layout.settings import AppSettings, save_settings
from manga_layout.storage import project_dir_of
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.saving import (
    DEFAULT_PROJECT_NAME,
    INITIAL_WIDTH,
    MIN_WIDTH,
    SaveAsDialog,
    default_parent,
    name_problem,
    target_note,
    target_problem,
)


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.state.history.mark_saved()
    win.close()


def ok_enabled(dialog: SaveAsDialog) -> bool:
    return dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


class Test作品名の検査:
    """作ってから OS に断られる前に、理由を出す。"""

    def test_普通の名前は通る(self):
        assert name_problem("私のネーム") is None
        assert name_problem("第1話 出会い") is None

    def test_空は断る(self):
        assert name_problem("") is not None
        assert name_problem("   ") is not None

    @pytest.mark.parametrize("name", ["a/b", "a\\b", "a:b", "a?b", 'a"b', "a<b", "a|b"])
    def test_フォルダ名に使えない文字を断る(self, name):
        problem = name_problem(name)
        assert problem is not None
        assert "使えない文字" in problem

    def test_末尾のピリオドを断る(self):
        # Windows は黙って落とすため、打った名前と違うフォルダができる
        assert "ピリオド" in name_problem("第1話.")

    @pytest.mark.parametrize("name", ["CON", "nul", "COM1", "AUX.txt"])
    def test_Windowsが押さえている名前を断る(self, name):
        problem = name_problem(name)
        assert problem is not None
        assert "装置の名前" in problem

    def test_前後の空白は数えない(self):
        assert name_problem("  作品  ") is None


class Test行き先の検査:
    """そこに作品フォルダを置けるか。"""

    def test_何も無ければ置ける(self, tmp_path):
        assert target_problem(tmp_path / "新しい作品") is None

    def test_同名のファイルがあれば断る(self, tmp_path):
        (tmp_path / "作品").write_text("", encoding="utf-8")
        assert "ファイル" in target_problem(tmp_path / "作品")

    def test_空のフォルダなら置ける(self, tmp_path):
        (tmp_path / "作品").mkdir()
        assert target_problem(tmp_path / "作品") is None

    def test_中身のある別物のフォルダは断る(self, tmp_path):
        """元から入っていたファイルと混ざると、持ち主が分からなくなる。"""
        folder = tmp_path / "写真"
        folder.mkdir()
        (folder / "a.png").write_bytes(b"")
        assert "作品ではない" in target_problem(folder)

    def test_既にある作品は上書きとして通す(self, qapp, tmp_path):
        state = EditorState()
        state.save(tmp_path / "作品")

        assert target_problem(tmp_path / "作品") is None
        assert "上書き" in target_note(tmp_path / "作品")

    def test_新規なら新しく作ると出る(self, tmp_path):
        assert "新しく作られます" in target_note(tmp_path / "まだ無い")


class Test置き場所の初期値:
    """設定 → 開いている作品の隣 → ドキュメント、の順に実在するものを使う。"""

    def test_開いている作品の隣を出す(self, qapp, tmp_path):
        state = EditorState()
        state.save(tmp_path / "1作目")
        assert default_parent(state.project_dir) == tmp_path

    def test_保存前でも実在するフォルダを返す(self):
        assert default_parent(None).is_dir()

    def test_設定した場所を使う(self, tmp_path):
        assert default_parent(None, str(tmp_path)) == tmp_path

    def test_設定した場所が無ければ諦める(self):
        """外付けドライブが繋がっていない PC で開いた場合。

        無い場所を出しても、選び直す手間が増えるだけになる。
        """
        fallback = default_parent(None, r"Z:\繋がっていないドライブ\作品")
        assert fallback.is_dir()

    def test_開いている作品のほうが強い(self, qapp, tmp_path):
        """既にある作品の「名前を付けて保存」は、その隣から始める。"""
        state = EditorState()
        state.save(tmp_path / "1作目")
        other = tmp_path / "設定の場所"
        other.mkdir()

        assert default_parent(state.project_dir, str(other)) == tmp_path


class Test窓:
    """打った名前がそのまま行き先になること。"""

    def test_置き場所と名前をつなぐ(self, qapp, tmp_path):
        dialog = SaveAsDialog(tmp_path, "私のネーム")
        assert dialog.chosen_path() == tmp_path / "私のネーム"

    def test_名前が無ければ既定を入れておく(self, qapp, tmp_path):
        dialog = SaveAsDialog(tmp_path, "")
        assert dialog.name.text() == DEFAULT_PROJECT_NAME
        assert ok_enabled(dialog)

    def test_使えない名前ではOKを押せない(self, qapp, tmp_path):
        dialog = SaveAsDialog(tmp_path, "私のネーム")
        dialog.name.setText("a/b")

        assert not ok_enabled(dialog)
        assert "使えない文字" in dialog.note.text()

    def test_名前を直せばまた押せる(self, qapp, tmp_path):
        dialog = SaveAsDialog(tmp_path, "私のネーム")
        dialog.name.setText("")
        assert not ok_enabled(dialog)

        dialog.name.setText("直した名前")
        assert ok_enabled(dialog)

    def test_置き場所が無ければ押せない(self, qapp, tmp_path):
        dialog = SaveAsDialog(tmp_path, "私のネーム")
        dialog.parent_dir.setText(str(tmp_path / "存在しない"))

        assert not ok_enabled(dialog)
        assert "見つかりません" in dialog.note.text()

    def test_パスが収まる幅で開く(self, qapp, tmp_path):
        """この窓の中身はほとんどがパス。狭いと行き先を確かめられない。"""
        dialog = SaveAsDialog(tmp_path, "私のネーム")
        assert dialog.width() >= INITIAL_WIDTH

    def test_長いパスでも切らない(self, qapp, tmp_path):
        deep = tmp_path / ("とても長いフォルダ名" * 6)
        deep.mkdir()
        dialog = SaveAsDialog(deep, "私のネーム")
        assert dialog.width() >= dialog.sizeHint().width()

    def test_縮められる下限がある(self, qapp, tmp_path):
        dialog = SaveAsDialog(tmp_path, "私のネーム")
        assert dialog.minimumWidth() == MIN_WIDTH

    def test_上書きになることを見せる(self, qapp, tmp_path):
        state = EditorState()
        state.save(tmp_path / "先客")

        dialog = SaveAsDialog(tmp_path, "先客")

        assert dialog.overwrites_project()
        assert ok_enabled(dialog)  # 断りはしない。確認は呼ぶ側が出す
        assert "上書き" in dialog.note.text()


class Test画面からの保存:
    """メニューから押したときの流れ。"""

    def test_打った名前でフォルダができる(self, window, tmp_path, monkeypatch):
        _accept(monkeypatch, tmp_path / "私のネーム")
        window.add_full_page_panel()

        assert window.save_project_as()

        assert is_project_dir(tmp_path / "私のネーム")
        assert window.state.project_dir == tmp_path / "私のネーム"

    def test_深い場所でも親ごと作る(self, window, tmp_path, monkeypatch):
        _accept(monkeypatch, tmp_path / "新しい作品")

        assert window.save_project_as()
        assert (tmp_path / "新しい作品" / "assets").is_dir()

    def test_取り消せば何も作らない(self, window, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "manga_layout.ui.window.SaveAsDialog.exec",
            lambda self: QDialog.DialogCode.Rejected,
        )

        assert not window.save_project_as()
        assert list(tmp_path.iterdir()) == []
        assert window.state.project_dir is None

    def test_保存していない作品でも呼べる(self, window, tmp_path, monkeypatch):
        """Ctrl+S は保存先が無ければここへ来る。"""
        _accept(monkeypatch, tmp_path / "初めての保存")
        window.state.add_text(Rect(20.0, 20.0, 40.0, 20.0), "あ")

        assert window.save_project()
        assert is_project_dir(tmp_path / "初めての保存")

    def test_既存の作品への上書きは確認する(self, window, tmp_path, monkeypatch):
        other = EditorState()
        other.save(tmp_path / "先客")
        before = (tmp_path / "先客" / "project.json").read_bytes()

        _accept(monkeypatch, tmp_path / "先客")
        monkeypatch.setattr(
            QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
        )

        assert not window.save_project_as()
        assert (tmp_path / "先客" / "project.json").read_bytes() == before
        assert window.state.project_dir is None

    def test_承知すれば上書きする(self, window, tmp_path, monkeypatch):
        other = EditorState()
        other.save(tmp_path / "先客")
        before = (tmp_path / "先客" / "project.json").read_bytes()

        window.add_full_page_panel()
        _accept(monkeypatch, tmp_path / "先客")
        monkeypatch.setattr(
            QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Ok
        )

        assert window.save_project_as()
        assert (tmp_path / "先客" / "project.json").read_bytes() != before


class Test開く:
    """作品はフォルダ単位だが、選ばせるのは `project.json`。

    フォルダを選ぶ窓にすると、目当ての `project.json` が一覧に出てこない。
    選べないのか場所を間違えたのかが分からず、開けないと思ってしまう。
    """

    def test_選ばれたファイルから作品フォルダを割り出す(self, tmp_path):
        (tmp_path / "作品").mkdir()
        json = tmp_path / "作品" / "project.json"
        json.write_text("{}", encoding="utf-8")

        assert project_dir_of(json) == tmp_path / "作品"

    def test_フォルダを渡してもそのまま通す(self, tmp_path):
        """内部の経路がどちらを受け取っても壊れないようにしておく。"""
        assert project_dir_of(tmp_path) == tmp_path

    def test_projectjsonを選ぶと開ける(self, window, tmp_path, monkeypatch):
        other = EditorState()
        other.save(tmp_path / "先に作った作品")

        _choose_file(monkeypatch, tmp_path / "先に作った作品" / "project.json")
        window.open_project()

        assert window.state.project_dir == tmp_path / "先に作った作品"

    def test_取り消せば開かない(self, window, monkeypatch):
        before = window.state.project_dir
        _choose_file(monkeypatch, None)

        window.open_project()

        assert window.state.project_dir == before

    def test_作品でないファイルは断る(self, window, tmp_path, monkeypatch):
        stray = tmp_path / "ただのファイル.json"
        stray.write_text("{}", encoding="utf-8")
        _choose_file(monkeypatch, stray)
        shown = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a, **k: shown.append(a[1])
        )

        window.open_project()

        assert shown == ["開けません"]
        assert window.state.project_dir is None


class Test窓が始まる場所:
    """開く・保存・画像を選ぶで**同じ場所**から始める。

    窓ごとに別々だと、同じ作業の途中なのに始まる場所が変わり、そのたびに
    辿り直すことになる。特に「画像を選ぶ」は場所を渡していなかったため、
    アプリを起動したフォルダから始まっていた。
    """

    def test_作品を開くは設定の場所から始まる(self, window, tmp_path, monkeypatch):
        _configure(window, tmp_path, tmp_path)
        started = _record_start_dir(monkeypatch)

        window.open_project()

        assert started == [str(tmp_path)]

    def test_画像を選ぶも同じ場所から始まる(self, window, tmp_path, monkeypatch):
        _configure(window, tmp_path, tmp_path)
        window.add_full_page_panel()
        started = _record_start_dir(monkeypatch)

        window.open_image_file()

        assert started == [str(tmp_path)]

    def test_保存も同じ場所から始まる(self, window, tmp_path):
        _configure(window, tmp_path, tmp_path)

        assert window._default_parent() == tmp_path

    def test_作品を開いていればその隣から始まる(self, window, tmp_path, monkeypatch):
        """設定より、今いる場所のほうが強い。"""
        other = tmp_path / "設定の場所"
        other.mkdir()
        _configure(window, tmp_path, other)
        window.state.save(tmp_path / "1作目")
        window.add_full_page_panel()
        started = _record_start_dir(monkeypatch)

        window.open_image_file()

        assert started == [str(tmp_path)]

    def test_書き換えた設定は開き直さずに効く(self, window, tmp_path):
        """`settings.json` は手で書き換える前提のファイル。

        起動時に一度読むだけだと、書き換えてもアプリを開き直すまで
        効かない。しかも効かない理由は画面に出ないので、設定の書き方を
        間違えたのかと疑うことになる（2026-08-03 に実際に起きた）。
        """
        最初 = tmp_path / "最初の場所"
        あとで = tmp_path / "あとで書いた場所"
        最初.mkdir()
        あとで.mkdir()
        _configure(window, tmp_path, 最初)
        assert window._default_parent() == 最初

        # アプリを開いたまま、設定だけを書き換える
        save_settings(AppSettings(default_parent_dir=str(あとで)), window.settings_file)

        assert window._default_parent() == あとで


class Test前回のファイルを開く:
    """『ファイル』→『開く』の下にある、前回の行き先へのショートカット。

    `settings.json`（手で書き換える前提）とは別に、開く・保存するたびに
    黙って上書きする記録を見て、選ぶ手間なしで前回の作品を開く。
    """

    def test_起動直後は無効(self, window):
        assert not window.recent_project_action.isEnabled()

    def test_保存すると記録される(self, window, tmp_path, monkeypatch):
        _accept(monkeypatch, tmp_path / "私のネーム")
        window.add_full_page_panel()

        window.save_project_as()

        assert load_recent_project() == tmp_path / "私のネーム"
        assert window.recent_project_action.isEnabled()
        assert "私のネーム" in window.recent_project_action.text()

    def test_開くと記録される(self, window, tmp_path, monkeypatch):
        other = EditorState()
        other.save(tmp_path / "先に作った作品")
        _choose_file(monkeypatch, tmp_path / "先に作った作品" / "project.json")

        window.open_project()

        assert load_recent_project() == tmp_path / "先に作った作品"
        assert window.recent_project_action.isEnabled()

    def test_選ぶ手間なしで開ける(self, window, tmp_path, monkeypatch):
        other = EditorState()
        other.save(tmp_path / "前回の作品")
        _choose_file(monkeypatch, tmp_path / "前回の作品" / "project.json")
        window.open_project()
        window.new_project()
        assert window.state.project_dir is None

        window.open_recent_project()

        assert window.state.project_dir == tmp_path / "前回の作品"

    def test_記録が無ければ何もしない(self, window):
        before = window.state.project_dir
        window.open_recent_project()
        assert window.state.project_dir == before

    def test_移動されていれば断る(self, window, tmp_path, monkeypatch):
        other = EditorState()
        other.save(tmp_path / "動かす作品")
        _choose_file(monkeypatch, tmp_path / "動かす作品" / "project.json")
        window.open_project()
        window.new_project()

        import shutil

        shutil.rmtree(tmp_path / "動かす作品")
        shown = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a, **k: shown.append(a[1])
        )

        window.open_recent_project()

        assert shown == ["開けません"]


def _configure(window, tmp_path: pathlib.Path, parent_dir: pathlib.Path) -> None:
    """設定を差し替える。**本物の `settings.json` は触らない。**

    利用者の設定を読みに行くと、その PC に何が書いてあるかで結果が
    変わってしまう（`F:` のような外付けドライブが書かれていることがある）。
    """
    window.settings_file = tmp_path / "settings.json"
    save_settings(AppSettings(default_parent_dir=str(parent_dir)), window.settings_file)


def _record_start_dir(monkeypatch) -> list[str]:
    """窓を出さずに、渡された「始まる場所」だけ控える。"""
    started: list[str] = []

    def fake(parent, caption, directory="", *args, **kwargs):
        started.append(directory)
        return "", ""

    monkeypatch.setattr("manga_layout.ui.window.QFileDialog.getOpenFileName", fake)
    return started


def _choose_file(monkeypatch, path: pathlib.Path | None) -> None:
    """ファイル選択の窓を出さずに、選んだことにして進める。"""
    monkeypatch.setattr(
        "manga_layout.ui.window.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(path) if path else "", ""),
    )


def _accept(monkeypatch, path: pathlib.Path) -> None:
    """窓を出さずに、その行き先を選んだことにして進める。"""
    monkeypatch.setattr(
        "manga_layout.ui.window.SaveAsDialog.exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        "manga_layout.ui.window.SaveAsDialog.chosen_path", lambda self: path
    )
    monkeypatch.setattr(
        "manga_layout.ui.window.SaveAsDialog.overwrites_project",
        lambda self: is_project_dir(path),
    )
