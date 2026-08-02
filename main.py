"""漫画レイアウタの起動口。

    ./venv/Scripts/python.exe main.py [作品フォルダ]

作品フォルダを渡すとそれを開く。省略すると新規作成で始まる。
"""

from __future__ import annotations

import pathlib
import sys

from PySide6.QtWidgets import QApplication

from manga_layout.errors import MangaLayoutError
from manga_layout.storage import is_project_dir, load_project
from manga_layout.ui import EditorState, MainWindow


def main(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName("MANGA_layout")

    state = EditorState()

    if len(argv) > 1:
        path = pathlib.Path(argv[1])
        if not is_project_dir(path):
            print(f"作品フォルダではありません（project.json が無い）: {path}")
            return 1
        try:
            state.reset(load_project(path), path)
        except MangaLayoutError as e:
            print(f"開けませんでした: {e}")
            return 1

    window = MainWindow(state)
    window.show()
    window.view.fit_page()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
