"""動作確認用のサンプルプロジェクトを作る。

    ./venv/Scripts/python.exe tools/make_sample.py

`samples/dev/project.json` を書き出す。漫画としてありそうなコマ割りを
入れてあるので、画面まわりの「読み込ませる対象」に使える。

**`samples/basic` へは書き出さない。** そちらは version 1（mm 座標系）の
まま凍結してあるサンプルで、旧形式を px へ移行する処理の実地確認に使って
いる（tests/test_migration.py、tests/test_ui.py）。ここで上書きすると
その確認が効かなくなる（`test_サンプルは旧形式のまま置いてある` が検出する）。

画像は含めない（`assets/` は .gitignore の対象）。コマ・吹き出し・セリフの
配置と紐づけだけを持つ、そのまま git に載せられる大きさのデータ。
"""

from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from manga_layout import (  # noqa: E402
    Page,
    Project,
    Rect,
    Tail,
    new_project,
    save_project,
)
from manga_layout.layout import DEFAULT_SETTINGS  # noqa: E402
from manga_layout.model import MM_TO_PX  # noqa: E402

OUT_DIR = REPO_ROOT / "samples" / "dev"

# ページの余白と、コマとコマの隙間（ガター）。座標系は px（要件定義 3章）。
# コマ割りの既定設定（layout.DEFAULT_SETTINGS）にそのまま揃えておく
MARGIN = DEFAULT_SETTINGS.margin
GUTTER = DEFAULT_SETTINGS.gutter


def mm(value: float) -> float:
    """コマの寸法を mm 感覚のまま書けるようにする換算。

    このファイルのレイアウトはもともと mm で決めていた（座標系が px に
    変わる前の名残）。px の生の数字を直書きすると紙の上での大きさが
    読み取れなくなるので、ここでだけ mm → px を挟む。
    """
    return round(value * MM_TO_PX, 1)


def add_panel_with_speech(
    project: Project,
    page: Page,
    rect: Rect,
    speech: str,
    tail_offset: tuple[float, float],
) -> None:
    """コマを1つ作り、そこに紐づいた吹き出しとセリフを添える。

    吹き出しはコマの子ではなく `attached_panel_id` で紐づく。
    コマを動かせば追随するが、コマ枠で切り抜かれないので枠外へはみ出せる。
    """
    panel = project.add_panel(page, rect)

    balloon_rect = Rect(rect.x + mm(5.0), rect.y + mm(5.0), mm(42.0), mm(24.0))
    balloon = project.add_balloon(page, balloon_rect, attached_panel_id=panel.id)
    balloon.tail = Tail(
        enabled=True,
        tip=(balloon_rect.center[0] + tail_offset[0], balloon_rect.bottom + tail_offset[1]),
        width=mm(6.0),
    )

    project.add_text(
        page,
        speech,
        Rect(
            balloon_rect.x + mm(3.0),
            balloon_rect.y + mm(3.0),
            balloon_rect.w - mm(6.0),
            balloon_rect.h - mm(6.0),
        ),
        attached_panel_id=panel.id,
    )


def build() -> Project:
    project = new_project(title="サンプル作品")

    # --- 1ページ目: 上下に大ゴマ、中段を左右に割る典型的な構成 ---
    page1 = project.pages[0]
    width = page1.size.w - MARGIN * 2
    half = (width - GUTTER) / 2

    add_panel_with_speech(
        project, page1, Rect(MARGIN, MARGIN, width, mm(80.0)), "ここが1コマ目だ", (mm(8.0), mm(12.0))
    )
    add_panel_with_speech(
        project, page1, Rect(MARGIN, MARGIN + mm(86.0), half, mm(80.0)), "左のコマ", (mm(-6.0), mm(10.0))
    )
    add_panel_with_speech(
        project,
        page1,
        Rect(MARGIN + half + GUTTER, MARGIN + mm(86.0), half, mm(80.0)),
        "右のコマ",
        (mm(6.0), mm(10.0)),
    )
    add_panel_with_speech(
        project, page1, Rect(MARGIN, MARGIN + mm(172.0), width, mm(95.0)), "最後の大ゴマ", (0.0, mm(14.0))
    )

    # --- 2ページ目: コマだけ。セリフを入れる前の状態の見本 ---
    page2 = project.add_page()
    for row in range(3):
        project.add_panel(page2, Rect(MARGIN, MARGIN + row * mm(90.0), width, mm(84.0)))

    # コマに紐づかない、ページに直接置いた吹き出し（枠外のナレーション想定）
    project.add_balloon(
        page2, Rect(MARGIN, page2.size.h - mm(28.0), mm(60.0), mm(18.0)), style="jagged"
    )

    return project


def main() -> int:
    project = build()
    path = save_project(project, OUT_DIR, backup=False)

    print(f"書き出しました: {path.relative_to(REPO_ROOT)}")
    print(f"  ページ数    : {len(project.pages)}")
    print(f"  コマ数      : {sum(len(p.panels) for p in project.pages)}")
    print(f"  浮遊要素数  : {sum(len(p.floating) for p in project.pages)}")
    print(f"  次の採番    : {project.next_id}")
    print(f"  大きさ      : {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
