"""キャンバスへの画像ドラッグ&ドロップの検証。

エクスプローラーから絵を放り込む導線は、メニューを一度も開かずに使える
ぶん**壊れても気づきにくい**。落ちない・置かれる、だけでなく次の3つを
ここで押さえる。

1. **`dragMoveEvent` も受けること。** Windows では入った瞬間だけ許可して
   動かした途端に拒否へ変わり、カーソルが禁止マークのまま落とせなくなる。
   見た目にはドラッグできているので、テストが無いと再発しても分からない
2. **落とし先が無いときに黙らないこと。** コマの外に落とすと何も起きない。
   案内が出ないと「対応していないファイルだった」と誤解される
3. **壊れた1枚で残りを巻き添えにしないこと。** まとめて落とすのが普通の
   使い方なので、1枚で全部止まると原因が分からない
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent

from manga_layout.ui import EditorState, MainWindow


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    # 未保存のまま閉じると確認ダイアログが応答待ちで止まる
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def window_with_panel(window):
    """全面コマが1つ。落とし先がある状態。"""
    window.add_full_page_panel()
    return window


@pytest.fixture
def messages(window):
    seen: list[str] = []
    window.state.message.connect(seen.append)
    return seen


def panel_images(window) -> list:
    return window.state.page.panels[0].children


def inside(window) -> tuple[float, float]:
    """コマの中の点（場面座標＝ページのミリ）。"""
    bounds = window.state.page.panels[0].shape.bounds()
    return (bounds.x + bounds.w / 2.0, bounds.y + bounds.h / 2.0)


# ページの外。場面には余白があるので座標としては有効だが、コマは無い
OUTSIDE = (-5.0, -5.0)


def mime_for(*paths) -> QMimeData:
    """ファイルを掴んだドラッグの中身。

    **戻り値を持ち続けること。** イベント側は借りているだけなので、
    先に回収されると落ちる
    """
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return mime


def _viewport_pos(window, scene_point: tuple[float, float]) -> QPointF:
    return QPointF(window.view.mapFromScene(*scene_point))


def send_drop(window, mime: QMimeData, scene_point: tuple[float, float]) -> QDropEvent:
    event = QDropEvent(
        _viewport_pos(window, scene_point),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    event.ignore()  # 既定値に頼らず、受けたかどうかを自分で見分ける
    window.view.dropEvent(event)
    return event


def send_drag(window, mime: QMimeData, kind: str = "move") -> QDragMoveEvent:
    cls = QDragEnterEvent if kind == "enter" else QDragMoveEvent
    event = cls(
        _viewport_pos(window, inside(window)).toPoint(),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    event.ignore()
    if kind == "enter":
        window.view.dragEnterEvent(event)
    else:
        window.view.dragMoveEvent(event)
    return event


class TestReceivable:
    """何を画像として受け取るか。ここは中身を読まず、名前だけで決める。"""

    def test_画像だけを拾う(self, window, tmp_path):
        mime = mime_for(tmp_path / "a.png", tmp_path / "memo.txt", tmp_path / "b.jpg")
        got = window.view._dropped_images(mime)
        assert [p.name for p in got] == ["a.png", "b.jpg"]

    @pytest.mark.parametrize("name", ["a.PNG", "a.Jpeg", "a.WEBP"])
    def test_大文字の拡張子でも拾う(self, window, tmp_path, name):
        """保存元によっては拡張子が大文字になる。見分けを小文字に寄せている"""
        assert window.view._dropped_images(mime_for(tmp_path / name))

    def test_知らない拡張子は拾わない(self, window, tmp_path):
        """PSD や TIFF は `assets.sniff_format` が見分けられない"""
        mime = mime_for(tmp_path / "a.psd", tmp_path / "b.tiff")
        assert window.view._dropped_images(mime) == []

    def test_ネット上の絵は拾わない(self, window):
        """ブラウザからの直接ドラッグは今は対象外。取りに行かず素通しする"""
        mime = QMimeData()
        mime.setUrls([QUrl("https://example.com/a.png")])
        assert window.view._dropped_images(mime) == []

    def test_ファイル以外のドラッグは拾わない(self, window):
        mime = QMimeData()
        mime.setText("ただの文字列")
        assert window.view._dropped_images(mime) == []


class TestAccept:
    """落とせる状態に見えること。カーソルの形はこれで決まる。"""

    def test_入った時点で受ける(self, window_with_panel, fixture_dir):
        mime = mime_for(fixture_dir / "rgb_opaque.png")
        assert send_drag(window_with_panel, mime, "enter").isAccepted()

    def test_動かしている間も受け続ける(self, window_with_panel, fixture_dir):
        """Windows で落とせなくなる不具合の回帰テスト（→ モジュールの説明）"""
        mime = mime_for(fixture_dir / "rgb_opaque.png")
        assert send_drag(window_with_panel, mime, "move").isAccepted()

    def test_画像でなければ受けない(self, window_with_panel, tmp_path):
        mime = mime_for(tmp_path / "memo.txt")
        assert not send_drag(window_with_panel, mime, "move").isAccepted()


class TestDropOnPanel:
    def test_コマの上に落とすと置かれる(self, window_with_panel, fixture_dir):
        window = window_with_panel
        mime = mime_for(fixture_dir / "rgb_opaque.png")

        send_drop(window, mime, inside(window))

        assert len(panel_images(window)) == 1

    def test_置いた画像が選ばれる(self, window_with_panel, fixture_dir):
        """落としたあとすぐ動かせるように。選ばれていないと一度掴み直しになる"""
        window = window_with_panel
        mime = mime_for(fixture_dir / "rgb_opaque.png")

        send_drop(window, mime, inside(window))

        assert window.state.selected_id == panel_images(window)[0].id

    def test_コマに収まる大きさで入る(self, window_with_panel, fixture_dir):
        """いきなり埋めない。絵のどこが切れているか分からなくなる（→ place_image）"""
        window = window_with_panel
        mime = mime_for(fixture_dir / "rgb_opaque.png")

        send_drop(window, mime, inside(window))

        bounds = window.state.page.panels[0].shape.bounds()
        rect = panel_images(window)[0].rect
        assert rect.x >= bounds.x and rect.y >= bounds.y
        assert rect.x + rect.w <= bounds.x + bounds.w
        assert rect.y + rect.h <= bounds.y + bounds.h

    def test_まとめて落とせる(self, window_with_panel, fixture_dir):
        window = window_with_panel
        mime = mime_for(
            fixture_dir / "rgb_opaque.png", fixture_dir / "rgba_transparent.png"
        )

        send_drop(window, mime, inside(window))

        assert len(panel_images(window)) == 2

    def test_枚数を伝える(self, window_with_panel, fixture_dir, messages):
        window = window_with_panel
        mime = mime_for(
            fixture_dir / "rgb_opaque.png", fixture_dir / "rgba_transparent.png"
        )

        send_drop(window, mime, inside(window))

        assert any("2 枚を置きました" in m for m in messages)

    def test_受けたことをイベントに返す(self, window_with_panel, fixture_dir):
        """返さないと、落とした側（エクスプローラー）が失敗として扱う"""
        window = window_with_panel
        mime = mime_for(fixture_dir / "rgb_opaque.png")

        assert send_drop(window, mime, inside(window)).isAccepted()

    def test_元に戻せる(self, window_with_panel, fixture_dir):
        """履歴に積まれていること。積み忘れると戻せないまま増える"""
        window = window_with_panel
        mime = mime_for(fixture_dir / "rgb_opaque.png")
        send_drop(window, mime, inside(window))

        window.state.undo()

        assert panel_images(window) == []

    def test_1枚ずつ戻せる(self, window_with_panel, fixture_dir):
        """まとめて落としても、履歴は1枚に1つ。まとめると戻しすぎる"""
        window = window_with_panel
        mime = mime_for(
            fixture_dir / "rgb_opaque.png", fixture_dir / "rgba_transparent.png"
        )
        send_drop(window, mime, inside(window))

        window.state.undo()

        assert len(panel_images(window)) == 1


class TestDropOutsidePanel:
    def test_コマが無ければ置かれない(self, window_with_panel, fixture_dir):
        window = window_with_panel
        mime = mime_for(fixture_dir / "rgb_opaque.png")

        send_drop(window, mime, OUTSIDE)

        assert panel_images(window) == []

    def test_どうすればよいか伝える(self, window_with_panel, fixture_dir, messages):
        window = window_with_panel
        mime = mime_for(fixture_dir / "rgb_opaque.png")

        send_drop(window, mime, OUTSIDE)

        assert any("コマの上に落として" in m for m in messages)

    def test_コマが1つも無いページでも落ちない(self, window, fixture_dir, messages):
        """ページの中でもコマが無ければ同じ扱い。起動直後がこの状態"""
        mime = mime_for(fixture_dir / "rgb_opaque.png")

        send_drop(window, mime, (50.0, 50.0))

        assert window.state.page.panels == []
        assert any("コマの上に落として" in m for m in messages)


class TestBrokenFile:
    """壊れたファイルで巻き添えを出さない。"""

    def test_壊れた1枚では何も置かれない(self, window_with_panel, fixture_dir):
        window = window_with_panel
        mime = mime_for(fixture_dir / "broken.png")

        send_drop(window, mime, inside(window))

        assert panel_images(window) == []

    def test_ファイル名を添えて知らせる(self, window_with_panel, fixture_dir, messages):
        """どれが駄目だったか分からないと、まとめて落としたときに探せない"""
        window = window_with_panel
        mime = mime_for(fixture_dir / "broken.png")

        send_drop(window, mime, inside(window))

        assert any("broken.png" in m for m in messages)

    def test_残りは置かれる(self, window_with_panel, fixture_dir):
        window = window_with_panel
        mime = mime_for(
            fixture_dir / "broken.png",
            fixture_dir / "rgb_opaque.png",
            fixture_dir / "rgba_transparent.png",
        )

        send_drop(window, mime, inside(window))

        assert len(panel_images(window)) == 2

    def test_無いファイルでも落ちない(self, window_with_panel, tmp_path, messages):
        """ドラッグ中に元が消える・切り取られる場合がある"""
        window = window_with_panel
        mime = mime_for(tmp_path / "どこにも無い.png")

        send_drop(window, mime, inside(window))

        assert panel_images(window) == []
        assert messages
