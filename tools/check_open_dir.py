"""ファイルの窓がどこから始まるかを、アプリと同じ経路で調べる。

「設定した場所にならない」と感じたときに、**どこで期待とずれたのか**を
1つずつ切り分けるためのもの。画面を見ただけでは、次のどれなのかが
分からない。

- `settings.json` が読めていない（JSON の書き間違い）
- 書いた場所が実在しない（外付けドライブが繋がっていない）
- 作品を開いているので、設定より「その隣」が優先されている
- 設定は効いているが、そのフォルダに作品が無くて空に見えている

使い方
------
    ./venv/Scripts/python.exe tools/check_open_dir.py

作品を開いた状態での動きを見たいときは、作品フォルダを渡す:

    ./venv/Scripts/python.exe tools/check_open_dir.py "F:\\...\\私のネーム"
"""

import datetime
import os
import pathlib
import sys

# 画面を出さずに調べる。窓を開くわけではないので表示装置は要らない
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# tools/ から実行しても manga_layout を見つけられるようにする
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from manga_layout.settings import settings_path  # noqa: E402
from manga_layout.storage import is_project_dir, load_project  # noqa: E402
from manga_layout.ui import EditorState, MainWindow  # noqa: E402

PROJECT_FILENAME = "project.json"


def main(argv: list[str]) -> int:
    app = QApplication([])  # noqa: F841  Qt の初期化に要る

    # **どちらの PC かを最初に出す。** 設定は PC ごとに別々に持つ作りなので
    # （`data/` は git 管理外）、片方で設定してももう片方には無い
    print(f"PC 名        : {os.environ.get('COMPUTERNAME', '(不明)')}")

    path = settings_path()
    print(f"設定ファイル : {path}")
    print(f"  実在       : {'あり' if path.is_file() else 'なし'}")
    if path.is_file():
        stamp = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        print(f"  更新       : {stamp:%Y-%m-%d %H:%M:%S}")
        print("  中身       :")
        for line in path.read_text(encoding="utf-8").splitlines():
            print(f"    {line}")

    state = EditorState()
    if len(argv) > 1:
        opened = pathlib.Path(argv[1])
        if not is_project_dir(opened):
            print(f"\n作品フォルダではありません（{PROJECT_FILENAME} が無い）: {opened}")
            return 1
        state.reset(load_project(opened), opened)

    window = MainWindow(state)

    configured = window.settings.default_parent_dir
    print(f"\n読めた設定   : {configured!r}")
    if configured:
        # 実在を確かめるのは、繋がっていない外付けドライブが
        # 書かれていることがあるため
        exists = pathlib.Path(configured).is_dir()
        print(f"  実在       : {'あり' if exists else 'なし（この場所は使われない）'}")

    print(f"開いている作品: {window.state.project_dir}")
    if window.state.project_dir is not None:
        print("  ※ 作品を開いている間は、設定より「その隣」が優先される")

    start = window.files.default_parent()
    print(f"\n窓が始まる場所: {start}")

    _report_contents(start)
    return 0


def _report_contents(start: pathlib.Path) -> None:
    """そこに何が見えるかまで出す。

    **場所が合っていても、作品が1つも無ければ空に見える。**「開く」は
    `project.json` だけを出す窓なので、画像しか入っていないフォルダでは
    何も並ばず、場所を間違えたように見える。
    """
    if not start.is_dir():
        print("  ※ この場所は実在しない")
        return

    children = sorted(start.iterdir())
    projects = [c for c in children if c.is_dir() and (c / PROJECT_FILENAME).is_file()]
    here = (start / PROJECT_FILENAME).is_file()

    print(f"  中身       : {len(children)} 件")
    print(f"  作品フォルダ: {len(projects)} 件 " + (", ".join(p.name for p in projects)))
    if here:
        print(f"  ※ この場所自体が作品フォルダ（直下に {PROJECT_FILENAME} がある）")
    if not projects and not here:
        print("  ※ 「開く」の窓には何も並ばない。場所は合っていても空に見える")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
