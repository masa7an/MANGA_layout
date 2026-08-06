"""設定を調整する道具の検証（→ 要件定義 6.28）。

この道具は**手で書き換える前提のファイルに、後から機械が触る**という
立場なので、押さえたいのは書き換えの正しさより「**触っていない所を
壊さないこと**」。

- 知らない項目を消さない（手で書き足したものが黙って消えない）
- 壊れた設定でも開ける。そのうえで、保存すると置き換わることを先に出す
- 範囲外の値はそもそも入力できない（保存してから既定に戻らない）
"""

from __future__ import annotations

import json
import pathlib

import pytest

from manga_layout.settings import (
    SETTINGS_FILENAME,
    SETTINGS_VERSION,
    AppSettings,
    load_settings,
    save_settings,
    update_settings_file,
)
from tools.settings_editor import SettingsEditor, dropped_fields, normalized_dir


@pytest.fixture
def path(tmp_path):
    return tmp_path / "設定" / SETTINGS_FILENAME


def 書く(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def 読む(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class Test知らない項目を消さない:
    """設定は手で書き換えるファイル。道具が触った途端に他が消えては困る。"""

    def test_知らない項目が残る(self, path):
        書く(path, '{"むかしの項目": "大事", "jpg_quality": 70}')

        update_settings_file(AppSettings(jpg_quality=80), path)

        assert 読む(path)["むかしの項目"] == "大事"

    def test_知っている項目は差し替わる(self, path):
        書く(path, '{"むかしの項目": "大事", "jpg_quality": 70}')

        update_settings_file(AppSettings(jpg_quality=80), path)

        assert 読む(path)["jpg_quality"] == 80

    def test_ファイルが無くても書ける(self, path):
        update_settings_file(AppSettings(), path)
        assert load_settings(path) == AppSettings()

    def test_壊れていても書ける(self, path):
        """読めない中身は諦めて置き換える。**開けなくなるほうが困る。**"""
        書く(path, "{ jpg_quality: ")

        update_settings_file(AppSettings(jpg_quality=80), path)

        assert load_settings(path).jpg_quality == 80

    def test_形式の版を書き込む(self, path):
        update_settings_file(AppSettings(), path)
        assert 読む(path)["format_version"] == SETTINGS_VERSION

    def test_人が読める形で書く(self, path):
        """道具で保存したあとも、手で開いて直せる形のままであること。"""
        update_settings_file(AppSettings(default_parent_dir=r"F:\漫画用"), path)
        text = path.read_text(encoding="utf-8")

        assert "\n" in text.strip()
        assert "漫画用" in text


class Testフォルダの整え方:
    def test_区切りを揃える(self):
        """ファイル窓は `/` で返す。設定ファイルの中で2通りに見えないようにする。"""
        assert normalized_dir("F:/2025_e/下書き") == str(pathlib.Path(r"F:\2025_e\下書き"))

    def test_引用符ごと貼っても通る(self):
        """エクスプローラーの「パスのコピー」は `"` を付けて渡してくる。"""
        assert normalized_dir('"F:\\2025_e\\下書き"') == str(pathlib.Path(r"F:\2025_e\下書き"))

    def test_空なら指定なし(self):
        assert normalized_dir("   ") is None

    def test_重ねる書き方は人が意識しない(self, path, qapp):
        """`\\` を2つ重ねるのは JSON の決まり。道具を通せば人は書かない。"""
        editor = SettingsEditor(path)
        editor.folder.setText(r"F:\2025_e\下書き")

        editor.save()

        assert load_settings(path).default_parent_dir == r"F:\2025_e\下書き"


class Test採用されなかった値を知らせる:
    """範囲外は既定に落ちる。落ちたこと自体は正しいが、黙っていると
    「書き換えたのに効かない」としか見えない。"""

    def test_範囲外は知らせる(self, path):
        書く(path, '{"autosave_interval_sec": 99999}')
        assert dropped_fields(読む(path), load_settings(path)) == ["自動バックアップの間隔"]

    def test_型違いも知らせる(self, path):
        書く(path, '{"jpg_quality": "高画質"}')
        assert dropped_fields(読む(path), load_settings(path)) == ["JPG の品質"]

    def test_真偽値も知らせる(self, path):
        """`true` は Python では 1 として通ってしまうので、値だけ見ると気づけない。"""
        書く(path, '{"autosave_interval_sec": true}')
        assert dropped_fields(読む(path), load_settings(path)) == ["自動バックアップの間隔"]

    def test_書いていない項目は知らせない(self, path):
        書く(path, "{}")
        assert dropped_fields(読む(path), load_settings(path)) == []

    def test_通った値は知らせない(self, path):
        書く(path, '{"jpg_quality": 70}')
        assert dropped_fields(読む(path), load_settings(path)) == []

    def test_指定なしと書いたのは知らせない(self, path):
        """`null` は「ドキュメントから始める」という意図の指定。"""
        書く(path, '{"default_parent_dir": null}')
        assert dropped_fields(読む(path), load_settings(path)) == []


class Test窓:
    def test_設定を読んで出す(self, path, qapp):
        save_settings(AppSettings(jpg_quality=70, autosave_interval_sec=30), path)

        editor = SettingsEditor(path)

        assert editor.quality.value() == 70
        assert editor.interval.value() == 30

    def test_範囲外の値はそもそも入力できない(self, path, qapp):
        """保存してから既定に戻る、が起きないようにする。"""
        editor = SettingsEditor(path)

        editor.interval.setValue(99999)
        editor.quality.setValue(0)

        assert editor.interval.value() == 3600
        assert editor.quality.value() == 1

    def test_保存すると書き戻る(self, path, qapp):
        editor = SettingsEditor(path)
        editor.quality.setValue(75)
        editor.opacity.setValue(0.6)

        editor.save()

        saved = load_settings(path)
        assert saved.jpg_quality == 75
        assert saved.rough_opacity == pytest.approx(0.6)

    def test_保存しなければ書き換わらない(self, path, qapp):
        save_settings(AppSettings(jpg_quality=70), path)

        editor = SettingsEditor(path)
        editor.quality.setValue(20)

        assert load_settings(path).jpg_quality == 70

    def test_設定が無くても開ける(self, path, qapp):
        """初めて使うとき、まだアプリを起動していない場合。"""
        editor = SettingsEditor(path)
        assert editor.quality.value() == AppSettings().jpg_quality
        assert not editor.broken

    def test_壊れていても開ける(self, path, qapp):
        書く(path, "{ jpg_quality: ")
        assert SettingsEditor(path).broken

    def test_壊れているときは置き換わることを出す(self, path, qapp):
        """黙って上書きすると、手で書いた中身が消えたことに気づけない。"""
        書く(path, "{ jpg_quality: ")

        editor = SettingsEditor(path)

        assert editor.top_note.isVisibleTo(editor)
        assert "置き換わります" in editor.top_note.text()

    def test_採用されなかった項目を窓の上に出す(self, path, qapp):
        書く(path, '{"autosave_interval_sec": 99999}')

        editor = SettingsEditor(path)

        assert editor.top_note.isVisibleTo(editor)
        assert "自動バックアップの間隔" in editor.top_note.text()

    def test_問題がなければ何も出さない(self, path, qapp):
        """雑音を出さない。出ているときだけ読めばよい状態にする。"""
        save_settings(AppSettings(jpg_quality=70), path)

        editor = SettingsEditor(path)

        assert not editor.top_note.isVisibleTo(editor)

    def test_実在しないフォルダでも保存できる(self, path, qapp, tmp_path):
        """外付けドライブが繋がっていない日にも、先に設定しておける。"""
        editor = SettingsEditor(path)
        editor.folder.setText(str(tmp_path / "つながっていないドライブ"))

        editor.save()

        assert "つながっていないドライブ" in (load_settings(path).default_parent_dir or "")

    def test_実在しないフォルダには注意を出す(self, path, qapp, tmp_path):
        editor = SettingsEditor(path)

        editor.folder.setText(str(tmp_path / "無い場所"))

        assert editor.folder_note.isVisibleTo(editor)

    def test_実在すれば注意を出さない(self, path, qapp, tmp_path):
        editor = SettingsEditor(path)

        editor.folder.setText(str(tmp_path))

        assert not editor.folder_note.isVisibleTo(editor)

    def test_空にできる(self, path, qapp):
        """ドキュメントから始める状態へ戻せること。"""
        save_settings(AppSettings(default_parent_dir=r"F:\作品"), path)

        editor = SettingsEditor(path)
        editor.folder.clear()
        editor.save()

        assert load_settings(path).default_parent_dir is None
