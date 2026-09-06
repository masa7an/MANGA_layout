"""画面に本物のマウス操作を送る。**テストから使う道具で、本番には入らない。**

`PageView` の当たり判定・ドラッグは `mousePressEvent` から先でしか動かない
ので、そこを通すには本物の `QMouseEvent` が要る。作り方は4通りしかないが、
**押している側（`buttons`）と押した側（`button`）の組み合わせが種類ごとに
違う**ので、各テストファイルに書き写すと必ずどれかを取り違える。

| 種類 | 押した側 `button` | 押している側 `buttons` | 届く先 |
|---|---|---|---|
| `press` | 左 | 左 | `mousePressEvent` |
| `move` | 無し | 左 | `mouseMoveEvent` |
| `release` | 左 | 無し | `mouseReleaseEvent` |
| `double` | 左 | 左 | `mouseDoubleClickEvent` |

**座標はページの px で渡す**（`view` が画面の座標へ直す）。テストが表示倍率を
知らずに済むので、窓の大きさが変わっても書いた座標はそのまま効く。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QWidget

# 種類ごとの「押した側・押している側・届く先の名前」。
# **1か所にまとめてあるのはこの表のため。** 写すと取り違える
KINDS: dict[str, tuple[Qt.MouseButton, Qt.MouseButton, QMouseEvent.Type, str]] = {
    "press": (
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        QMouseEvent.Type.MouseButtonPress,
        "mousePressEvent",
    ),
    "move": (
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        QMouseEvent.Type.MouseMove,
        "mouseMoveEvent",
    ),
    "release": (
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        QMouseEvent.Type.MouseButtonRelease,
        "mouseReleaseEvent",
    ),
    "double": (
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        QMouseEvent.Type.MouseButtonDblClick,
        "mouseDoubleClickEvent",
    ),
}


def modifiers_of(shift: bool) -> Qt.KeyboardModifier:
    return (
        Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    )


def event_at(
    view, kind: str, x: float, y: float, *, shift: bool = False
) -> QMouseEvent:
    """ページの px を画面の座標へ直した、1つ分の出来事。**送りはしない。**

    送らずに欲しい場面がある（→ `test_ui_wand`）ので、作るのと送るのを
    分けてある。
    """
    button, buttons, event_type, _ = KINDS[kind]
    position = QPointF(view.mapFromScene(QPointF(x, y)))
    return QMouseEvent(
        event_type,
        position,
        view.viewport().mapToGlobal(position),
        button,
        buttons,
        modifiers_of(shift),
    )


def send(view, kind: str, x: float, y: float, *, shift: bool = False) -> None:
    """作って、その種類の受け口へ渡す。"""
    getattr(view, KINDS[kind][3])(event_at(view, kind, x, y, shift=shift))


def press(view, x: float, y: float, *, shift: bool = False) -> None:
    """左ボタンの押下。**ダブルクリックの手前には必ずこれが入る。**"""
    send(view, "press", x, y, shift=shift)


def move_to(view, x: float, y: float, *, shift: bool = False) -> None:
    """左ボタンを押したままの移動（→ `press` と対）。"""
    send(view, "move", x, y, shift=shift)


def release(view, x: float, y: float, *, shift: bool = False) -> None:
    """左ボタンの離し（→ `press` と対）。"""
    send(view, "release", x, y, shift=shift)


def double_click(view, x: float, y: float, *, shift: bool = False) -> None:
    """左ボタンのダブルクリック。

    **利用者から見た「ダブルクリック1回」は、押下との組で送る**
    （→ `test_pick_cycle.click_pair`）。Qt はダブルクリックの前に必ず
    押下を配るので、これ1つだけでは本物にならない。
    """
    send(view, "double", x, y, shift=shift)


def click(view, x: float, y: float, *, shift: bool = False) -> None:
    """押して、その場で離す。**動かさない。**"""
    press(view, x, y, shift=shift)
    release(view, x, y, shift=shift)


def drag(
    view, x1: float, y1: float, x2: float, y2: float, *, shift: bool = False
) -> None:
    """押して、引いて、離す。

    `shift` は3つとも押しっぱなしにする。刻みや等比は**離すときにも
    見ている**ので、途中だけ押しても本物と同じにならない。
    """
    press(view, x1, y1, shift=shift)
    move_to(view, x2, y2, shift=shift)
    release(view, x2, y2, shift=shift)


def press_widget(widget: QWidget, point: QPointF | None = None) -> None:
    """部品そのものを押す。**座標は部品の中での位置**（既定は中央）。

    `view` に送る上の道具とは座標系が違う。押した先で信号を出すだけの
    部品（ページ番号の欄など）を、本物のクリックで確かめるために使う。
    """
    where = QPointF(widget.rect().center()) if point is None else point
    widget.mousePressEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            where,
            widget.mapToGlobal(where),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
