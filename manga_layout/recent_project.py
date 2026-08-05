"""直前に開いていた作品の場所（`data/recent_project.txt`）。

`settings.json` は**人が手で書き換える前提**のファイル（→ `settings.py`）。
ここに入れる値はその逆で、**開く・保存するたびにアプリが黙って上書きする**
セッションの記録なので、混ぜずに別ファイルに分ける。混ぜると、手で書いた
値がアプリの動作で気づかないうちに上書きされることになる。

中身は作品フォルダへの1行のパスだけ。読み書きとも失敗しても作業は止めない
（→ `autosave_log.py` と同じ考え方）。
"""

from __future__ import annotations

import pathlib

from .settings import settings_dir
from .storage import atomic_write_text

RECENT_PROJECT_FILENAME = "recent_project.txt"


def recent_project_path() -> pathlib.Path:
    return settings_dir() / RECENT_PROJECT_FILENAME


def load_recent_project(path: pathlib.Path | None = None) -> pathlib.Path | None:
    path = path or recent_project_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return pathlib.Path(text) if text else None


def save_recent_project(project_dir: pathlib.Path, path: pathlib.Path | None = None) -> None:
    path = path or recent_project_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, str(project_dir))
    except OSError:
        # 記録は補助。書けなくても開く・保存する操作自体は成功しているので、
        # そちらを失敗扱いにしない
        pass
