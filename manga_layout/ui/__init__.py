"""画面まわり。PySide6 に依存するのはこの階層だけ。

モデル側（`manga_layout` 直下）は Qt を知らないので、画面なしでテストできる。
"""

from .state import EditorState
from .window import MainWindow

__all__ = ["EditorState", "MainWindow"]
