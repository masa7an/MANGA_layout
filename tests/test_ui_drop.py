"""キャンバスへの画像ドラッグ&ドロップの検証。

落とされ方は3通りあり、**この順に見る**（→ `canvas.PageView._dropped_sources`）。

1. 手元のファイル（エクスプローラーから）
2. 絵そのもの（画像を持たせて渡してくるアプリ）
3. 住所だけ（ブラウザから。取りに行かないと絵が手に入らない）

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
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QGuiApplication,
    QImage,
)

from manga_layout.errors import ImageFetchError
from manga_layout.ui import EditorState, MainWindow

REMOTE_URL = "https://example.com/img/photo.png"


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


def mime_with_image(width: int = 7, height: int = 11) -> QMimeData:
    """絵そのものを持たせたドラッグ。寸法で他と見分けられるようにしてある。"""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.red)
    mime = QMimeData()
    mime.setImageData(image)
    return mime


def mime_with_url(*urls: str) -> QMimeData:
    """住所だけのドラッグ（ブラウザから絵を引っ張ったとき）。"""
    mime = QMimeData()
    mime.setUrls([QUrl(u) for u in urls])
    return mime


@pytest.fixture
def fetched(monkeypatch, fixture_dir):
    """取りに行った住所を記録し、既定では基準画像を返す。

    **本物の通信はしない。** 外へ出ると、テストが回線と相手の都合で落ちる。
    取ってくること自体の検証は `test_fetch.py` に置いてある
    """

    class Recorder:
        def __init__(self):
            self.urls: list[str] = []
            self.reply = lambda: (fixture_dir / "rgb_opaque.png").read_bytes()

        def __call__(self, url: str) -> bytes:
            self.urls.append(url)
            return self.reply()

    recorder = Recorder()
    monkeypatch.setattr("manga_layout.ui.canvas.fetch_bytes", recorder)
    return recorder


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

    def test_ネット上の絵はファイルとしては拾わない(self, window):
        """住所は別の経路で扱う（→ `TestDropUrl`）。ファイルとしては読めない"""
        assert window.view._dropped_images(mime_with_url(REMOTE_URL)) == []

    def test_ファイル以外のドラッグは拾わない(self, window):
        mime = QMimeData()
        mime.setText("ただの文字列")
        assert window.view._dropped_images(mime) == []


class TestFetchableUrls:
    """住所として取りに行くもの。中身ではなく種類だけで決める。"""

    def test_httpは取りに行く(self, window):
        assert window.view._dropped_urls(mime_with_url(REMOTE_URL)) == [REMOTE_URL]

    def test_拡張子では絞らない(self, window):
        """配信元が拡張子を付けないことは普通にある。絞ると落とせる絵が減る"""
        url = "https://example.com/photo?id=1"
        assert window.view._dropped_urls(mime_with_url(url)) == [url]

    @pytest.mark.parametrize(
        "url", ["ftp://example.com/a.png", "data:image/png;base64,AAAA"]
    )
    def test_http以外は取りに行かない(self, window, url):
        assert window.view._dropped_urls(mime_with_url(url)) == []

    def test_手元のファイルは含めない(self, window, tmp_path):
        """住所としても読めてしまうので、二重に置かないよう外す"""
        assert window.view._dropped_urls(mime_for(tmp_path / "a.png")) == []


class TestAccept:
    """落とせる状態に見えること。カーソルの形はこれで決まる。"""

    def test_入った時点で受ける(self, window_with_panel, fixture_dir):
        mime = mime_for(fixture_dir / "rgb_opaque.png")
        assert send_drag(window_with_panel, mime, "enter").isAccepted()

    def test_動かしている間も受け続ける(self, window_with_panel, fixture_dir):
        """Windows で落とせなくなる不具合の回帰テスト（→ モジュールの説明）"""
        mime = mime_for(fixture_dir / "rgb_opaque.png")
        assert send_drag(window_with_panel, mime, "move").isAccepted()

    def test_絵そのものでも受ける(self, window_with_panel):
        assert send_drag(window_with_panel, mime_with_image(), "move").isAccepted()

    def test_住所だけでも受ける(self, window_with_panel):
        """ブラウザから引いたときに禁止マークのままだと、試す気にならない"""
        mime = mime_with_url(REMOTE_URL)
        assert send_drag(window_with_panel, mime, "move").isAccepted()

    def test_画像でなければ受けない(self, window_with_panel, tmp_path):
        mime = mime_for(tmp_path / "memo.txt")
        assert not send_drag(window_with_panel, mime, "move").isAccepted()

    def test_取りに行けない住所なら受けない(self, window_with_panel):
        mime = mime_with_url("ftp://example.com/a.png")
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


class TestDropImageData:
    """絵そのものを持たせて渡してくるアプリからのドロップ。"""

    def test_絵そのものを落とすと置かれる(self, window_with_panel):
        window = window_with_panel
        mime = mime_with_image(7, 11)

        send_drop(window, mime, inside(window))

        assert [i.src_px for i in panel_images(window)] == [(7, 11)]

    def test_ファイルがあればそちらを使う(self, window_with_panel, fixture_dir):
        """ブラウザは両方を渡してくる。**手元にあるものを優先する**"""
        window = window_with_panel
        mime = mime_for(fixture_dir / "rgb_opaque.png")
        mime.setImageData(QImage(7, 11, QImage.Format.Format_ARGB32))

        send_drop(window, mime, inside(window))

        assert len(panel_images(window)) == 1
        assert panel_images(window)[0].src_px != (7, 11)

    def test_空の絵は置かず黙らない(self, window_with_panel, messages):
        """中身の無い `QImage` を持たせてくる相手がいる。

        受けると見せた以上、何も起きないまま終わらせない
        （ドラッグ中の判定は軽さを取って中身まで見ていない）
        """
        window = window_with_panel
        mime = QMimeData()
        mime.setImageData(QImage())

        send_drop(window, mime, inside(window))

        assert panel_images(window) == []
        assert any("取り出せませんでした" in m for m in messages)


class TestDropUrl:
    """ブラウザから絵を直接ドラッグしたとき（住所だけが渡される）。"""

    def test_取りに行って置く(self, window_with_panel, fetched):
        window = window_with_panel

        send_drop(window, mime_with_url(REMOTE_URL), inside(window))

        assert fetched.urls == [REMOTE_URL]
        assert len(panel_images(window)) == 1

    def test_複数の住所を順に取る(self, window_with_panel, fetched):
        window = window_with_panel
        second = "https://example.com/img/other.png"

        send_drop(window, mime_with_url(REMOTE_URL, second), inside(window))

        assert fetched.urls == [REMOTE_URL, second]

    def test_取り込み中だと伝える(self, window_with_panel, fetched, messages):
        """取り終わるまで画面が止まる。何も出ないと固まったように見える"""
        window = window_with_panel

        send_drop(window, mime_with_url(REMOTE_URL), inside(window))

        assert any("取り込んでいます" in m for m in messages)

    def test_コマの外なら取りに行かない(self, window_with_panel, fetched):
        """**落とし先が決まってから取りに行く。**

        先に取ってしまうと、置き場所を外して断られるたびに通信が走り、
        やり直すほど待たされる
        """
        send_drop(window_with_panel, mime_with_url(REMOTE_URL), OUTSIDE)

        assert fetched.urls == []

    def test_絵そのものがあれば取りに行かない(self, window_with_panel, fetched):
        """ブラウザは住所と絵の両方を渡してくる。手元にあるなら使う"""
        window = window_with_panel
        mime = mime_with_url(REMOTE_URL)
        mime.setImageData(mime_with_image(7, 11).imageData())

        send_drop(window, mime, inside(window))

        assert fetched.urls == []
        assert [i.src_px for i in panel_images(window)] == [(7, 11)]

    def test_取れなければ住所の名前を添えて知らせる(
        self, window_with_panel, fetched, messages
    ):
        window = window_with_panel

        def boom():
            raise ImageFetchError("つながりませんでした（timed out）")

        fetched.reply = boom

        send_drop(window, mime_with_url(REMOTE_URL), inside(window))

        assert panel_images(window) == []
        assert any("photo.png" in m and "timed out" in m for m in messages)

    def test_取れた1枚は残る(self, window_with_panel, fetched):
        """1つ失敗しても残りを続ける（まとめて引くことがある）"""
        window = window_with_panel
        replies = [ImageFetchError("駄目でした"), fetched.reply()]

        def next_reply():
            value = replies.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        fetched.reply = next_reply

        send_drop(
            window,
            mime_with_url(REMOTE_URL, "https://example.com/img/other.png"),
            inside(window),
        )

        assert len(panel_images(window)) == 1

    def test_取ってきた中身が画像でなければ置かない(
        self, window_with_panel, fetched, messages
    ):
        """住所を拡張子で絞っていないので、絵でないものも届く（→ `_dropped_urls`）"""
        window = window_with_panel
        fetched.reply = lambda: b"<html>not an image</html>"

        send_drop(window, mime_with_url(REMOTE_URL), inside(window))

        assert panel_images(window) == []
        assert any("photo.png" in m for m in messages)

    def test_砂時計を残さない(self, window_with_panel, fetched):
        """戻し忘れると、以後ずっと砂時計のまま操作することになる"""
        window = window_with_panel
        fetched.reply = lambda: (_ for _ in ()).throw(ImageFetchError("駄目でした"))

        send_drop(window, mime_with_url(REMOTE_URL), inside(window))

        assert QGuiApplication.overrideCursor() is None


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
