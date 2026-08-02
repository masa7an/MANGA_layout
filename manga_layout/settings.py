"""アプリの設定（`settings.json`）。

**作品ではなく、この PC でのこのアプリの好み**を入れる。作品に属するもの
（ページの大きさ、綴じ方向）は `project.json` 側で、ここには入れない。
混ぜると、作品を別の PC へ渡したときに相手の好みを上書きしてしまう。

置き場所は `%LOCALAPPDATA%\\MANGA_layout\\settings.json`。リポジトリの中に
置かない理由は2つある。

- このプロジェクトは**2台の PC で git 同期している**。保存先には `F:` の
  ような、片方の PC にしか無いドライブが入る
- リポジトリを clone し直しても設定が残る

人が手で開いて書き換えることを前提にした形にしてある（項目を絞る、
知らない項目は捨てずに読み飛ばす、壊れていても起動を止めない）。
"""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass

from .storage import atomic_write_text

APP_DIRNAME = "MANGA_layout"
SETTINGS_FILENAME = "settings.json"

# 形式を変えたときに、古い設定を読んでいると気づけるようにする
SETTINGS_VERSION = 1


def settings_dir() -> pathlib.Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return pathlib.Path(base) / APP_DIRNAME
    # Windows 以外や、環境変数が無い場合の逃げ道
    return pathlib.Path.home() / f".{APP_DIRNAME.lower()}"


def settings_path() -> pathlib.Path:
    return settings_dir() / SETTINGS_FILENAME


@dataclass
class AppSettings:
    """`settings.json` の中身。

    `default_parent_dir` は「名前を付けて保存」の窓に最初から入っている
    **置き場所**。作品フォルダそのものではなく、その1つ上を指す。
    `null` にするとドキュメントフォルダを使う。
    """

    default_parent_dir: str | None = None

    def to_dict(self) -> dict:
        return {
            "format_version": SETTINGS_VERSION,
            "default_parent_dir": self.default_parent_dir,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        """辞書から作る。**知らない項目は黙って読み飛ばす。**

        設定は人が手で書き換えるものなので、打ち間違いや古い項目が
        混ざる。1個の綴り間違いで起動しなくなるほうが困る。
        """
        value = data.get("default_parent_dir")
        return cls(default_parent_dir=value if isinstance(value, str) and value else None)


def load_settings(path: pathlib.Path | None = None) -> AppSettings:
    """設定を読む。**無い・壊れているときは既定値を返す。**

    起動を止めない。設定はあくまで好みで、無くても作業はできる。
    """
    path = path or settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    if not isinstance(data, dict):
        return AppSettings()
    return AppSettings.from_dict(data)


def save_settings(settings: AppSettings, path: pathlib.Path | None = None) -> pathlib.Path:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, text)
    return path


def ensure_settings_file(path: pathlib.Path | None = None) -> pathlib.Path:
    """無ければ既定値で作る。**あるものには触らない。**

    手で書き換える前提のファイルなので、実物が無いと「どこに何を書けば
    いいのか」が分からない。起動時に一度呼んで、空の雛形を置いておく。
    """
    path = path or settings_path()
    if not path.is_file():
        save_settings(load_settings(path), path)
    return path
