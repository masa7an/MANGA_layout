"""一度出した操作のヒントの記録（`data/hints_seen.txt`）。

ヒントは**最初の1回だけ**出す（→ 要件定義 6.35）。2回目から出さないために、
出したヒントの名前を1行に1つずつ書いておく。

`recent_project.txt` と同じく、**アプリが黙って書き足す**記録なので
`settings.json`（人が手で書き換える前提）とは混ぜない（→ `recent_project.py`）。
読み書きとも失敗しても作業は止めない。読めなければ「まだ出していない」と
みなすので、最悪でもヒントがもう一度出るだけで済む。
"""

from __future__ import annotations

import pathlib

from .settings import settings_dir
from .storage import atomic_write_text

HINTS_SEEN_FILENAME = "hints_seen.txt"


def hints_seen_path() -> pathlib.Path:
    return settings_dir() / HINTS_SEEN_FILENAME


def load_hints_seen(path: pathlib.Path | None = None) -> set[str]:
    path = path or hints_seen_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {line.strip() for line in text.splitlines() if line.strip()}


def mark_hint_seen(hint_id: str, path: pathlib.Path | None = None) -> None:
    """`hint_id` を出したことにする。既に書いてあれば何もしない。"""
    path = path or hints_seen_path()
    seen = load_hints_seen(path)
    if hint_id in seen:
        return
    seen.add(hint_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, "".join(f"{name}\n" for name in sorted(seen)))
    except OSError:
        # 記録は補助。書けなければ次の起動でもう一度出るだけ
        pass


# 最後に出したヒントの名前（→ 「ヒントをもう一度見る」）。**出した順は
# `hints_seen.txt` からは分からない**——あちらは並べ替えて書くうえ、出す前に
# 使った人の分（出していないもの）も入る。なので別に1行だけ持つ
LAST_HINT_FILENAME = "last_hint.txt"


def last_hint_path() -> pathlib.Path:
    return settings_dir() / LAST_HINT_FILENAME


def load_last_hint(path: pathlib.Path | None = None) -> str | None:
    path = path or last_hint_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def save_last_hint(hint_id: str, path: pathlib.Path | None = None) -> None:
    path = path or last_hint_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, f"{hint_id}\n")
    except OSError:
        # 記録は補助。書けなければ、もう一度見るで出るものが古くなるだけ
        pass
