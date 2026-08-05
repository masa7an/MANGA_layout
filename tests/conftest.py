"""テスト共通の準備。"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

# 画面まわりのテストは表示装置なしで動かす。
# QApplication を作る前に決めておく必要がある
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"

# venv に入れずに実行された場合でも manga_layout を見つけられるようにする
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from manga_layout import Rect, Tail, new_project  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """画面まわりのテスト用。1つのプロセスに QApplication は1つだけ。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def fixture_dir() -> pathlib.Path:
    return FIXTURE_DIR


@pytest.fixture(autouse=True)
def 自動バックアップの記録を逃がす(tmp_path_factory, monkeypatch):
    """本物の `data/autosave.log` に書かせない。

    記録は「タイマーが動いていないのか、動いていて何もしないのか」を
    切り分けるためのもの（→ `manga_layout.autosave_log`）。テストで
    `MainWindow` を作るたびに起動の行が混ざると、利用者が読むときに
    本物の起動が埋もれて用をなさなくなる。

    **`tmp_path` の中には置かない。** あそこは「保存すると何ができるか」を
    数えているテストが見ている場所なので、関係の無いファイルを混ぜると
    そちらが落ちる（実際に落ちた）。
    """
    directory = tmp_path_factory.mktemp("autosave_log")
    monkeypatch.setattr(
        "manga_layout.autosave_log.log_path", lambda: directory / "autosave.log"
    )


@pytest.fixture(autouse=True)
def 前回開いた作品の記録を逃がす(tmp_path_factory, monkeypatch):
    """本物の `data/recent_project.txt` に書かせない。

    「前回のファイルを開く」（→ `manga_layout.recent_project`）は開く・保存
    するたびに黙って上書きする。テストで `MainWindow` を作るたびに本物へ
    書くと、テストで使った `tmp_path`（テストが終われば消える）が実物の
    記録に残り続けてしまう。
    """
    directory = tmp_path_factory.mktemp("recent_project")
    monkeypatch.setattr(
        "manga_layout.recent_project.recent_project_path",
        lambda: directory / "recent_project.txt",
    )


@pytest.fixture
def png_bytes() -> bytes:
    """透明度ありの基準画像。"""
    return (FIXTURE_DIR / "rgba_transparent.png").read_bytes()


@pytest.fixture
def sample_project():
    """1ページに、コマ・画像・吹き出し・セリフを1つずつ入れたプロジェクト。

    紐づけ（attached_panel_id）まで張ってあるので、これ1つで
    往復変換とコマ移動の追随を確かめられる。
    """
    project = new_project(title="テスト作品")
    page = project.pages[0]

    panel = project.add_panel(page, Rect(10.0, 10.0, 90.0, 60.0))
    project.add_image(panel, "assets/abc123.png", Rect(12.0, 12.0, 80.0, 55.0), (1200, 900))

    balloon = project.add_balloon(page, Rect(20.0, 15.0, 40.0, 25.0), attached_panel_id=panel.id)
    balloon.tail = Tail(enabled=True, tip=(55.0, 45.0), width=6.0)
    project.add_text(page, "テスト\nセリフ", Rect(22.0, 18.0, 36.0, 19.0), attached_panel_id=panel.id)

    return project
