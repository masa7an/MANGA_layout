"""アプリの設定（`settings.json`）の検証。

手で書き換える前提のファイルなので、押さえたいのは**壊れた設定で起動を
止めないこと**。打ち間違いや古い項目が混ざるのは前提で、1個の綴り間違いで
アプリが開かなくなるほうが困る。
"""

from __future__ import annotations

import json
import pathlib

import pytest

import manga_layout
from manga_layout.model import DEFAULT_FONT_FAMILY
from manga_layout.settings import (
    JPG_QUALITY_DEFAULT,
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


class Testよく使う書体:
    """3枠の並び（→ 要件定義 6.5）。**枠の番号は覚えるものなので、詰めない。**"""

    def test_既定は1枠目だけ埋まる(self):
        """空で始めると、道具箱にボタンが1つも出ず機能が見えない。"""
        assert AppSettings().favorite_fonts == [DEFAULT_FONT_FAMILY, "", ""]

    def test_書いたものが読める(self, path):
        save_settings(AppSettings(favorite_fonts=["メイリオ", "游明朝", ""]), path)
        assert load_settings(path).favorite_fonts == ["メイリオ", "游明朝", ""]

    def test_文字列でない値はその枠だけ空になる(self, path):
        """**繰り上げない。** 2枠目が消えたときに3枠目が繰り上がると、
        覚えた番号と押した結果が黙ってずれる。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"favorite_fonts": ["A", 3, "C"]}), encoding="utf-8")
        assert load_settings(path).favorite_fonts == ["A", "", "C"]

    def test_多い分は切る(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"favorite_fonts": ["A", "B", "C", "D"]}), encoding="utf-8"
        )
        assert load_settings(path).favorite_fonts == ["A", "B", "C"]

    def test_足りない分は空で埋める(self, path):
        """読む側が長さを確かめずに済むよう、常に3つの並びにする。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"favorite_fonts": ["A"]}), encoding="utf-8")
        assert load_settings(path).favorite_fonts == ["A", "", ""]

    def test_全部消したときは空のまま(self, path):
        """空の配列は打ち間違いではなく「1つも要らない」という指定。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"favorite_fonts": []}), encoding="utf-8")
        assert load_settings(path).registered_fonts == []

    def test_登録されたものだけを並べる(self):
        settings = AppSettings(favorite_fonts=["A", "", "C"])
        assert settings.registered_fonts == ["A", "C"]


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


class Test自動バックアップの間隔:
    """短くして動きを確かめられること。**打ち間違いで壊れないこと。**"""

    def _書く(self, path, value: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'{{"autosave_interval_sec": {value}}}', encoding="utf-8")

    def test_既定は5分(self, path):
        assert load_settings(path).autosave_interval_sec == 300

    def test_短くできる(self, path):
        # 5分待たずに動きを確かめるための逃げ道
        self._書く(path, "30")
        assert load_settings(path).autosave_interval_sec == 30

    def test_短すぎる値は既定に落とす(self, path):
        self._書く(path, "0")
        assert load_settings(path).autosave_interval_sec == 300

    def test_長すぎる値は既定に落とす(self, path):
        self._書く(path, "99999")
        assert load_settings(path).autosave_interval_sec == 300

    def test_数でない値は既定に落とす(self, path):
        self._書く(path, '"30秒"')
        assert load_settings(path).autosave_interval_sec == 300

    def test_真偽値は数として通さない(self, path):
        # Python では True が 1 として通り、1秒間隔になってしまう
        self._書く(path, "true")
        assert load_settings(path).autosave_interval_sec == 300

    def test_書いたものが読み戻せる(self, path):
        save_settings(AppSettings(autosave_interval_sec=60), path)
        assert load_settings(path).autosave_interval_sec == 60


class TestJPG品質:
    """既定は90。**打ち間違いで壊れないこと。**（→ 要件定義 6.7）"""

    def _書く(self, path, value: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'{{"jpg_quality": {value}}}', encoding="utf-8")

    def test_既定は90(self, path):
        assert load_settings(path).jpg_quality == JPG_QUALITY_DEFAULT

    def test_変えられる(self, path):
        self._書く(path, "70")
        assert load_settings(path).jpg_quality == 70

    def test_0以下は既定に落とす(self, path):
        self._書く(path, "0")
        assert load_settings(path).jpg_quality == JPG_QUALITY_DEFAULT

    def test_100を超える値は既定に落とす(self, path):
        self._書く(path, "101")
        assert load_settings(path).jpg_quality == JPG_QUALITY_DEFAULT

    def test_数でない値は既定に落とす(self, path):
        self._書く(path, '"高画質"')
        assert load_settings(path).jpg_quality == JPG_QUALITY_DEFAULT

    def test_真偽値は数として通さない(self, path):
        self._書く(path, "true")
        assert load_settings(path).jpg_quality == JPG_QUALITY_DEFAULT

    def test_書いたものが読み戻せる(self, path):
        save_settings(AppSettings(jpg_quality=100), path)
        assert load_settings(path).jpg_quality == 100


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

    def test_書き込みに失敗しても起動は止めない(self, path, monkeypatch):
        """ディスク容量不足・権限エラーなどで雛形が置けなくても、
        起動時の未処理例外にはしない（2026-08-08 発見）。
        """
        import manga_layout.settings as settings_module

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(settings_module, "atomic_write_text", _boom)

        ensure_settings_file(path)  # 例外を出さずに戻ってくること

        assert not path.is_file()


class Test置き場所:
    """作業フォルダの `data/` に置く。

    `%LOCALAPPDATA%` に置いていた頃は、パッケージ版アプリの中から触ると
    別フォルダへ転送されるのに**パス表示が変わらず**、同じパスなのに実体が
    別という状態になった。作業フォルダの中なら実体が1つしかない。
    """

    def test_リポジトリのdataの下に置く(self):
        repo_root = pathlib.Path(manga_layout.__file__).resolve().parent.parent
        assert settings_path() == repo_root / "data" / SETTINGS_FILENAME

    def test_どこから起動しても同じ場所を指す(self, monkeypatch, tmp_path):
        """起動時の作業フォルダに左右されない。

        `run.bat` から、tools/ のスクリプトから、と入口が複数あるので、
        相対で決めると入口ごとに別のファイルを見ることになる。
        """
        before = settings_path()
        monkeypatch.chdir(tmp_path)
        assert settings_path() == before

    def test_環境変数に左右されない(self, monkeypatch):
        """`%LOCALAPPDATA%` を見ていた頃の名残が残っていないこと。"""
        before = settings_path()
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        assert settings_path() == before

    def test_git管理外の場所にある(self):
        """`F:` のような片方の PC にしか無いドライブが同期で持ち込まれない。"""
        repo_root = pathlib.Path(manga_layout.__file__).resolve().parent.parent
        ignored = (repo_root / ".gitignore").read_text(encoding="utf-8")
        assert "/data/" in ignored
