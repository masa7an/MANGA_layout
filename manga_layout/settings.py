"""アプリの設定（`settings.json`）。

**作品ではなく、この PC でのこのアプリの好み**を入れる。作品に属するもの
（ページの大きさ、綴じ方向）は `project.json` 側で、ここには入れない。
混ぜると、作品を別の PC へ渡したときに相手の好みを上書きしてしまう。

置き場所は**このリポジトリの `data/settings.json`**。`data/` は git 管理外
なので（`.gitignore` の `/data/`）、`F:` のような**片方の PC にしか無い
ドライブ**が書かれていても、もう1台へは同期されない。2台運用で困らない
という条件は、`%LOCALAPPDATA%` に置かなくても満たせる。

`%LOCALAPPDATA%` から移した理由は、**そこに置くと「誰が見ているファイル
なのか」が分からなくなった**ため。パッケージ版アプリの中から触ると
`%LOCALAPPDATA%` は `...\\Packages\\<アプリ>\\LocalCache\\Local\\` へ
転送されるが、**パス表示は元のまま変わらない**。同じパスなのに実体が別で、
片方では設定が入っていて片方では空、という状態になる（2026-08-03 に
実際に起きて、原因の特定に長くかかった）。作業フォルダの中なら実体が1つ
しかなく、この取り違えが起こらない。

**代わりに失うもの:** clone し直すと設定も消える。手で書き直す前提の
数行なので、取り違えの分かりにくさとは釣り合わないと判断した。

人が手で開いて書き換えることを前提にした形にしてある（項目を絞る、
知らない項目は捨てずに読み飛ばす、壊れていても起動を止めない）。
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

from .storage import atomic_write_text

# 利用者のデータを入れるフォルダ。git 管理外（`.gitignore` の `/data/`）
DATA_DIRNAME = "data"
SETTINGS_FILENAME = "settings.json"

# 形式を変えたときに、古い設定を読んでいると気づけるようにする
SETTINGS_VERSION = 1


def settings_dir() -> pathlib.Path:
    """設定を置くフォルダ。**このリポジトリの `data/`。**

    起動時の作業フォルダではなく、**このファイルの位置から**辿る。
    どこから起動しても同じ1個のファイルを指すようにするため
    （`run.bat` から、tools/ のスクリプトから、と入口が複数ある）。
    """
    # settings.py → manga_layout/ → リポジトリのルート
    return pathlib.Path(__file__).resolve().parent.parent / DATA_DIRNAME


def settings_path() -> pathlib.Path:
    return settings_dir() / SETTINGS_FILENAME


@dataclass
class AppSettings:
    """`settings.json` の中身。

    `default_parent_dir` は**ファイルの窓が始まる場所**。作品フォルダ
    そのものではなく、その1つ上を指す。`null` にするとドキュメント
    フォルダを使う。

    「名前を付けて保存」「作品を開く」「画像を選ぶ」で**共通**に使う。
    窓ごとに分けると、同じ作業の途中なのに始まる場所が変わり、
    そのたびに辿り直すことになる。
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
