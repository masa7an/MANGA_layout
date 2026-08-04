"""自動バックアップの記録（`data/autosave.log`）。

タイマーは黙って回るので、**動いていないときと、動いていて何もしないときの
区別が画面から付かない**。2026-08-05 に「5分待っても何も起きない」という
報告があり、原因が「タイマーが回っていない」なのか「回っているが退避する
理由が無い」なのかを切り分けられなかった。その差を残すための記録。

**毎回は書かない。** 同じ内容が続く間は最初の1行だけにする。保存先が
決まっていない作品を1日開きっぱなしにしても1行で済み、記録を読むときも
「変わった時点」だけが並ぶ。ただし**退避できたとき**は毎回書く。いつの
時点の内容が `backup/` に入っているかは、記録の目的そのものなので落とせない。

置き場所は `data/`（git 管理外）。作品ではなくこの PC での動きの記録なので、
作品フォルダにも設定ファイルにも入れない。
"""

from __future__ import annotations

import datetime
import pathlib

from .settings import settings_dir

LOG_FILENAME = "autosave.log"


def log_path() -> pathlib.Path:
    return settings_dir() / LOG_FILENAME


class AutosaveLog:
    """1行ずつ追記する。**書けなくても作業は止めない。**"""

    def __init__(self, path: pathlib.Path | None = None) -> None:
        self.path = path or log_path()
        self._last: str | None = None

    def record(self, message: str, *, repeat: bool = False) -> bool:
        """1行残す。書いたら True。

        `repeat` を立てない限り、**直前と同じ内容なら書かない**。
        5分ごとに同じ行が積み上がるのを防ぐ。
        """
        if not repeat and message == self._last:
            return False
        self._last = message

        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(f"{stamp}\t{message}\n")
        except OSError:
            # 記録は補助。書けないことを画面に出しても、利用者にできる
            # ことがない（自動バックアップ本体の失敗は別に知らせている）
            return False
        return True
