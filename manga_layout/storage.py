"""project.json の読み書き。

保存形式は要件定義 5章のフォルダ形式:

    作品フォルダ/
        project.json
        assets/
            <sha1>.png
        backup/
            project.1.json    ← 直前の保存内容（新しいほど番号が小さい）
            project.2.json
            autosave.1.json   ← タイマーで退避した、保存していない作業中の内容

保存で最も気を遣うのは**上書きの瞬間**。作業中のファイルを直接書き換えると、
書いている途中で落ちたときに元も新しい内容も両方失う。ここでは
「別名で全部書き切ってから、名前を入れ替える」方式にしている。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil

from .assets import ASSETS_DIRNAME, AssetStore
from .errors import ProjectFormatError, ProjectNotFoundError
from .model import Project

PROJECT_FILENAME = "project.json"
BACKUP_DIRNAME = "backup"

# 残す世代数。1作品ぶんの JSON は数十 KB なので、多めに持っても負担にならない
BACKUP_GENERATIONS = 5

# 自動バックアップ（→ `write_autosave`）。**別の系列にする。**
#
# 保存で退避する `project.N.json` は「過去の**保存済み**の姿」だが、
# こちらは「まだ**保存していない**今の姿」で、中身の性質が違う。混ぜると
# 復元しようとしたときにどちらか見分けられないうえ、世代が数個しか
# 無いので、タイマーが回るたびに保存済みの世代が押し出されて消える。
AUTOSAVE_PREFIX = "autosave"
AUTOSAVE_GENERATIONS = 3


_NUMBER = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_PAIR_RE = re.compile(rf"\[\s+({_NUMBER}),\s+({_NUMBER})\s+\]")


def is_project_dir(path: pathlib.Path | str) -> bool:
    return (pathlib.Path(path) / PROJECT_FILENAME).is_file()


def project_dir_of(path: pathlib.Path | str) -> pathlib.Path:
    """選ばれたパスから作品フォルダを割り出す。

    作品はフォルダ単位だが、利用者が指すのは `project.json` のほうが自然。
    **どちらを渡しても同じ場所に行き着く**ようにして、開く側が
    「ファイルとフォルダのどちらを受け取ったか」を気にせずに済ませる。
    """
    path = pathlib.Path(path)
    return path.parent if path.is_file() else path


def _compact_pairs(text: str) -> str:
    """`[x, y]` の 2 要素を 1 行に畳む。

    整形して書くと座標が 1 個ずつ改行され、コマが数百あるファイルでは
    人が追えない長さになる。頂点・しっぽの先端・元画像の寸法がこれに当たる。

    文字列の中身を壊す心配はない。JSON では改行が `\\n` の 2 文字に
    エスケープされるため、生の改行は整形用の空白としてしか現れない。
    """
    return _PAIR_RE.sub(r"[\1, \2]", text)


def atomic_write_text(path: pathlib.Path, text: str) -> None:
    """一時ファイルへ書き切ってから置き換える。

    `os.replace` は Windows でも同一ドライブ内なら不可分な操作なので、
    「古い内容」か「新しい内容」のどちらかしか残らない。
    中途半端な JSON が project.json として残る事態を防げる。

    `fsync` は、OS の書き込みキャッシュに載っただけの状態で
    名前を入れ替えてしまわないために挟んでいる。
    """
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _shift_generations(backup_dir: pathlib.Path, prefix: str, generations: int) -> None:
    """`<prefix>.1.json` … を1つずつ古い番号へ繰り下げ、1番を空ける。

    上限を超えた世代は消える。古いほうから動かすのは、先に新しいほうを
    動かすと繰り下げ先が既にあって上書きになるため。
    """
    oldest = backup_dir / f"{prefix}.{generations}.json"
    if oldest.exists():
        oldest.unlink()
    for n in range(generations - 1, 0, -1):
        src = backup_dir / f"{prefix}.{n}.json"
        if src.exists():
            os.replace(src, backup_dir / f"{prefix}.{n + 1}.json")


def _rotate_backups(project_dir: pathlib.Path) -> None:
    """既存の project.json を backup/ へ退避し、古い世代を繰り下げる。

    退避は移動ではなく複製。移動にすると、複製と書き込みの合間に落ちた場合に
    project.json が消えたままになる。
    """
    current = project_dir / PROJECT_FILENAME
    if not current.is_file():
        return

    backup_dir = project_dir / BACKUP_DIRNAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    _shift_generations(backup_dir, "project", BACKUP_GENERATIONS)
    shutil.copy2(current, backup_dir / "project.1.json")


def _project_text(project: Project) -> str:
    """project.json に書く中身。"""
    return _compact_pairs(json.dumps(project.to_dict(), ensure_ascii=False, indent=2)) + "\n"


def write_autosave(project: Project, project_dir: pathlib.Path | str) -> pathlib.Path:
    """作業中の内容を `backup/autosave.1.json` へ退避し、そのパスを返す。

    **`project.json` は触らない。** 押していない保存が起きると、
    「保存していない変更があります」の確認と食い違い、Undo で戻せる範囲と
    ディスクの中身がずれる（要件定義 6.6）。

    **画像の実体には触らない。** 呼ぶ側は保存先が決まっている作品に限る
    ことになっており、その場合、画像は貼った時点で `assets/` に入っている
    （→ `EditorState.autosave`）。
    """
    project_dir = pathlib.Path(project_dir)
    backup_dir = project_dir / BACKUP_DIRNAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    _shift_generations(backup_dir, AUTOSAVE_PREFIX, AUTOSAVE_GENERATIONS)

    path = backup_dir / f"{AUTOSAVE_PREFIX}.1.json"
    atomic_write_text(path, _project_text(project))
    return path


def save_project(
    project: Project,
    project_dir: pathlib.Path | str,
    *,
    backup: bool = True,
) -> pathlib.Path:
    """プロジェクトを保存し、書き込んだ project.json のパスを返す。

    未使用の画像整理は**ここでは行わない**。Undo で戻したときに参照が
    切れるため、`prune_unused_assets()` を利用者の操作で呼ぶ形にしている。
    """
    project_dir = pathlib.Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ASSETS_DIRNAME).mkdir(exist_ok=True)

    if backup:
        _rotate_backups(project_dir)

    path = project_dir / PROJECT_FILENAME
    atomic_write_text(path, _project_text(project))
    return path


def load_project(project_dir: pathlib.Path | str) -> Project:
    """プロジェクトを読み込む。

    形式が壊れていれば例外を投げる。ただし「存在しないコマへの紐づけ」など
    直せる範囲の乱れは直したうえで開き、内容を `project.load_warnings` に残す。
    """
    project_dir = pathlib.Path(project_dir)
    path = project_dir / PROJECT_FILENAME
    if not path.is_file():
        raise ProjectNotFoundError(f"プロジェクトが見つかりません: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ProjectFormatError(f"{path} を読めませんでした（{e}）") from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProjectFormatError(
            f"{PROJECT_FILENAME} を解釈できません（{e.lineno} 行目 {e.colno} 文字目: {e.msg}）。"
            f"{BACKUP_DIRNAME}/ に直前の保存内容が残っている可能性があります。"
        ) from e

    return Project.from_dict(data)


def find_missing_assets(project: Project, project_dir: pathlib.Path | str) -> list[str]:
    """参照されているのに実体が無い画像の一覧。

    見つかっても読み込みは止めない。1枚欠けただけで作品全体が開けないのは
    割に合わないので、開いたうえで該当箇所を示す方針。
    """
    store = AssetStore(project_dir)
    return sorted(ref for ref in project.referenced_assets() if not store.exists(ref))


def prune_unused_assets(project: Project, project_dir: pathlib.Path | str) -> list[str]:
    """どこからも参照されていない画像を `assets/_unused/` へ移す。

    利用者が明示的に選んだときだけ呼ぶこと（理由は `AssetStore.collect_unused`）。
    """
    return AssetStore(project_dir).collect_unused(project.referenced_assets())
