"""前回開いた作品の記録（`data/recent_project.txt`）の検証。

`settings.json` とは違い、**人が手で書くものではなく、アプリが黙って
上書きする**ファイル。押さえたいのは、書いたものが読み戻せることと、
無い・読めないときに起動を止めないこと。
"""

from __future__ import annotations

from manga_layout.recent_project import (
    RECENT_PROJECT_FILENAME,
    load_recent_project,
    recent_project_path,
    save_recent_project,
)


class Test読み書き:
    def test_書いたものが読める(self, tmp_path):
        path = tmp_path / RECENT_PROJECT_FILENAME
        save_recent_project(tmp_path / "私の作品", path)
        assert load_recent_project(path) == tmp_path / "私の作品"

    def test_上書きできる(self, tmp_path):
        path = tmp_path / RECENT_PROJECT_FILENAME
        save_recent_project(tmp_path / "1作目", path)
        save_recent_project(tmp_path / "2作目", path)
        assert load_recent_project(path) == tmp_path / "2作目"

    def test_親フォルダごと作る(self, tmp_path):
        path = tmp_path / "記録" / RECENT_PROJECT_FILENAME
        save_recent_project(tmp_path / "作品", path)
        assert path.is_file()


class Test無い読めない:
    def test_ファイルが無ければNone(self, tmp_path):
        assert load_recent_project(tmp_path / RECENT_PROJECT_FILENAME) is None

    def test_中身が空ならNone(self, tmp_path):
        path = tmp_path / RECENT_PROJECT_FILENAME
        path.write_text("", encoding="utf-8")
        assert load_recent_project(path) is None


class Test置き場所:
    def test_settingsと同じフォルダ(self):
        from manga_layout.settings import settings_dir

        assert recent_project_path() == settings_dir() / RECENT_PROJECT_FILENAME
