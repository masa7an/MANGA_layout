"""アプリの設定（`settings.json`）の検証。

手で書き換える前提のファイルなので、押さえたいのは**壊れた設定で起動を
止めないこと**。打ち間違いや古い項目が混ざるのは前提で、1個の綴り間違いで
アプリが開かなくなるほうが困る。
"""

from __future__ import annotations

import json

import pytest

from manga_layout.settings import (
    SETTINGS_FILENAME,
    SETTINGS_VERSION,
    AppSettings,
    ensure_settings_file,
    load_settings,
    save_settings,
    settings_path,
)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "設定" / SETTINGS_FILENAME


class Test読み書き:
    def test_書いたものが読める(self, path):
        save_settings(AppSettings(default_parent_dir=r"F:\作品置き場"), path)
        assert load_settings(path).default_parent_dir == r"F:\作品置き場"

    def test_親フォルダごと作る(self, path):
        save_settings(AppSettings(), path)
        assert path.is_file()

    def test_人が読める形で書く(self, path):
        save_settings(AppSettings(default_parent_dir=r"F:\漫画用"), path)
        text = path.read_text(encoding="utf-8")

        # 手で開いて直すファイルなので、1行に詰めない・日本語を \u で潰さない
        assert "\n" in text.strip()
        assert "漫画用" in text

    def test_形式の版を書き込む(self, path):
        save_settings(AppSettings(), path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["format_version"] == SETTINGS_VERSION


class Test壊れた設定:
    """どれも既定値で通す。起動を止めない。"""

    def test_ファイルが無い(self, path):
        assert load_settings(path) == AppSettings()

    def test_JSONとして壊れている(self, path):
        path.parent.mkdir(parents=True)
        path.write_text("{ default_parent_dir: ", encoding="utf-8")
        assert load_settings(path) == AppSettings()

    def test_辞書ではない(self, path):
        path.parent.mkdir(parents=True)
        path.write_text("[1, 2, 3]", encoding="utf-8")
        assert load_settings(path) == AppSettings()

    def test_値の型が違う(self, path):
        path.parent.mkdir(parents=True)
        path.write_text('{"default_parent_dir": 123}', encoding="utf-8")
        assert load_settings(path).default_parent_dir is None

    def test_空文字は指定なしとして扱う(self, path):
        path.parent.mkdir(parents=True)
        path.write_text('{"default_parent_dir": ""}', encoding="utf-8")
        assert load_settings(path).default_parent_dir is None

    def test_知らない項目は読み飛ばす(self, path):
        path.parent.mkdir(parents=True)
        path.write_text(
            '{"default_parent_dir": "F:\\\\作品", "むかしの項目": true}',
            encoding="utf-8",
        )
        assert load_settings(path).default_parent_dir == r"F:\作品"


class Test雛形の用意:
    def test_無ければ作る(self, path):
        ensure_settings_file(path)
        assert path.is_file()
        assert load_settings(path) == AppSettings()

    def test_あるものには触らない(self, path):
        save_settings(AppSettings(default_parent_dir=r"F:\大事な設定"), path)
        before = path.read_bytes()

        ensure_settings_file(path)

        assert path.read_bytes() == before


class Test置き場所:
    def test_LOCALAPPDATAの下に置く(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert settings_path() == tmp_path / "MANGA_layout" / SETTINGS_FILENAME

    def test_環境変数が無くても場所は決まる(self, monkeypatch):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        assert settings_path().name == SETTINGS_FILENAME
