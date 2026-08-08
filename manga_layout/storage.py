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

import datetime
import json
import os
import pathlib
import re
import shutil
from dataclasses import dataclass

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
# 区切りは「空白」ではなく「生の改行を含む空白」に絞る。JSON 文字列の中には
# 生の改行が出てこない（`\n` の2文字にエスケープされる）ため、これなら
# 整形用の空白だけを狙い撃ちできる（→ `_compact_pairs` の docstring）
_PAIR_RE = re.compile(rf"\[\s*\n\s*({_NUMBER}),\s*\n\s*({_NUMBER})\s*\n\s*\]")


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

    **区切りに生の改行を必須にしてあるので、文字列の中身は壊さない。**
    JSON 文字列の中では改行が `\\n` の2文字にエスケープされ、生の改行は
    出てこない。区切りをただの空白（`\\s+`）にしていた版では、セリフや
    タイトルに `[ 12, 34 ]` のような**半角スペース**の並びが入ると、
    その中身まで `[12, 34]` に書き換わっていた（2026-08-08 に実機で確認・
    修正）。整形が使う空白は `json.dumps(indent=2)` が生の改行のあとに
    置くインデントなので、生の改行を要求しても整形の畳みには支障が無い。
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


def read_project_file(path: pathlib.Path | str) -> Project:
    """1つの JSON ファイルをプロジェクトとして読む。

    `project.json` そのものと `backup/` の中の世代（→ `load_backup`）の
    **両方がここを通る**。読めない・解釈できない場合の断り方を1箇所に
    まとめておかないと、同じ壊れ方でも入り口によって別の文面が出る。
    """
    path = pathlib.Path(path)
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
            f"{path.name} を解釈できません（{e.lineno} 行目 {e.colno} 文字目: {e.msg}）。"
            f"{BACKUP_DIRNAME}/ に直前の保存内容が残っている可能性があります。"
        ) from e

    return Project.from_dict(data)


def load_project(project_dir: pathlib.Path | str) -> Project:
    """プロジェクトを読み込む。

    形式が壊れていれば例外を投げる。ただし「存在しないコマへの紐づけ」など
    直せる範囲の乱れは直したうえで開き、内容を `project.load_warnings` に残す。
    """
    return read_project_file(pathlib.Path(project_dir) / PROJECT_FILENAME)


# -- バックアップからの復元（要件定義 6.6） ---------------------------------


# 一覧に出す種別の名前。**画面と同じ言葉を `storage` 側で持つ。**
# 2系列の区別（保存済み／作業中）は形式そのものの話（→ `AUTOSAVE_PREFIX` の
# 注記）で、画面の都合ではない
BACKUP_KIND_SAVED = "保存済み"
BACKUP_KIND_AUTOSAVE = "作業中"

_BACKUP_SERIES = (
    ("project", BACKUP_KIND_SAVED, BACKUP_GENERATIONS),
    (AUTOSAVE_PREFIX, BACKUP_KIND_AUTOSAVE, AUTOSAVE_GENERATIONS),
)


@dataclass(frozen=True)
class BackupEntry:
    """`backup/` に残っている世代1つ。復元の一覧に並べる。

    **中身の手がかり（ページ数・コマ数）まで持つ。** 日時と番号だけでは
    「どの作業をしていた頃か」を思い出せず、選べない（要件定義 10.1）。

    読めなかった世代も `pages` を None にして残す。一覧から黙って消すと、
    5世代あるはずのものが4つしか出ない理由が分からなくなる。
    """

    path: pathlib.Path
    kind: str
    generation: int
    saved_at: datetime.datetime
    pages: int | None = None
    panels: int | None = None

    @property
    def label(self) -> str:
        """一覧に出す1行。"""
        when = self.saved_at.strftime("%Y-%m-%d %H:%M")
        what = f"{self.kind}（{self.generation}つ前）"
        if self.pages is None:
            return f"{when}  {what}  読めません"
        return f"{when}  {what}  {self.pages}ページ・{self.panels}コマ"


def _backup_summary(path: pathlib.Path) -> tuple[int, int] | None:
    """世代1つのページ数とコマ数。読めなければ None。

    `Project` まで組み立てず辞書のまま数える。一覧を出すだけのために
    8件ぶんの検証を通すのは重く、しかも**直せる乱れのある世代が
    一覧から消えてしまう**（`from_dict` は直して読むが、ここで例外に
    なるものは弾かれる）。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pages = data["pages"]
        return len(pages), sum(len(p.get("panels", [])) for p in pages)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def list_backups(project_dir: pathlib.Path | str) -> list[BackupEntry]:
    """`backup/` に残っている世代を、**新しい順**に並べて返す。

    2系列を番号順に分けず、日時で混ぜて並べる。分けて並べると、
    保存済みと作業中のどちらが新しいのかが読み取れない。
    """
    backup_dir = pathlib.Path(project_dir) / BACKUP_DIRNAME
    if not backup_dir.is_dir():
        return []

    found: list[BackupEntry] = []
    for prefix, kind, generations in _BACKUP_SERIES:
        for n in range(1, generations + 1):
            path = backup_dir / f"{prefix}.{n}.json"
            if not path.is_file():
                continue
            summary = _backup_summary(path)
            found.append(
                BackupEntry(
                    path=path,
                    kind=kind,
                    generation=n,
                    saved_at=datetime.datetime.fromtimestamp(path.stat().st_mtime),
                    pages=None if summary is None else summary[0],
                    panels=None if summary is None else summary[1],
                )
            )
    found.sort(key=lambda e: e.saved_at, reverse=True)
    return found


def load_backup(path: pathlib.Path | str) -> Project:
    """`backup/` の中の世代を1つ読む。

    **読むだけで、`project.json` には触らない。** 戻したものを Undo で
    取り消せるようにするため、差し替えは履歴の上で行う
    （→ `History.replace`）。ディスクへ確定するのは利用者が保存を
    押したときだけ（要件定義 6.6 の「押していない保存を起こさない」）。
    """
    return read_project_file(path)


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
