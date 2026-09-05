"""ショートカットキーの一覧を Markdown で書き出す（要件定義 7章）。

**アプリが画面に出しているものと同じ材料から作る**（`ui/shortcuts.collect_groups`）。
「ヘルプ → ショートカットキーの一覧...」に出る内容と、書き出した文書は必ず一致する。

**手で書いた表を置かない。** 表を持つと、**キーを1つ変えた日から嘘をつき始める**
（`ui/shortcuts.py` の冒頭と同じ理由）。書き出し先も見張っている——
`tests/test_ui_shortcuts.py` が、この道具の出力と `docs/ショートカットキー一覧.md` が
一致することを確かめる。**中身を手で直すと、そのテストが落ちる。**

使い方（venv の python で）::

    ./venv/Scripts/python.exe tools/dump_shortcuts.py            # 画面に出す
    ./venv/Scripts/python.exe tools/dump_shortcuts.py --write    # 文書を書き換える
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "ショートカットキー一覧.md"

HEADER = """# ショートカットキーの一覧

**この文書は自動で作られる。手で書き換えない。**
中身は「ヘルプ → ショートカットキーの一覧...」に出るものと同じで、
`tools/dump_shortcuts.py` がアプリのメニューを辿って書き出している。

キーを変えたら、次のコマンドで作り直す。

```
./venv/Scripts/python.exe tools/dump_shortcuts.py --write
```

作り直し忘れは `tests/test_ui_shortcuts.py` が見つける。
"""

FOOTER = """
## 読み方

**1文字のキーは道具の切り替え。** 押すとその道具に持ち替わる。セリフを打っている
間は効かない（打った文字として入る）。

**「メニューに無いキー」は画面が直接拾う。** 連打して見た目を合わせる操作なので、
メニューを開き直さずに続けて押せるようにしてある。

**キーは変わることがある。** 覚え直しになる変更はしていないが、**探しにくい記号の
ものは移すことがある**（例: セリフの大きさは 2026-09-05 に `Ctrl+]` / `Ctrl+[` →
`Ctrl+.` / `Ctrl+,` → `Ctrl+>` / `Ctrl+<` と2度移した。角括弧は刻印を見ても何という
記号か分からず、句読点は点が小さすぎて見分けられなかった。**どちらも押しにくさ
ではなく、キーを目で見つけられるかの問題**）。
"""


def build() -> str:
    """一覧の Markdown を組み立てて返す。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from manga_layout.ui import EditorState, MainWindow
    from manga_layout.ui.shortcuts import collect_groups

    window = MainWindow(EditorState())
    try:
        groups = collect_groups(window)
    finally:
        window.state.history.mark_saved()
        window.close()
    del app

    lines = [HEADER]
    for group in groups:
        lines.append(f"## {group.title}\n")
        lines.append("| キー | 動作 |")
        lines.append("|---|---|")
        for row in group.rows:
            lines.append(f"| `{row.keys}` | {row.action} |")
        lines.append("")
    lines.append(FOOTER.strip())
    return "\n".join(lines) + "\n"


def main() -> int:
    text = build()
    if "--write" in sys.argv:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(text, encoding="utf-8", newline="\n")
        print(f"書き出しました: {OUTPUT}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
