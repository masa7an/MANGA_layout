"""コマの上に置くもの（吹き出し・マーク・セリフ）の操作。

3つとも**コマの中ではなくページに浮いて置かれる**（`Page.floating`）ので、
重なり順も紐づけも同じ仕組みで動く。セリフは吹き出しへ、マークはコマへ
紐づけられる（→ 要件定義 6.13、6.14）。
"""

from __future__ import annotations

import contextlib
import dataclasses

from ..geometry import Rect
from ..layout import (
    attach_target,
    balloon_at,
    contain_rect_in,
    default_sticker_rect,
    default_tail_tip,
    tail_tip_turned_to,
)
from ..model import (
    BALLOON_STYLES_WITH_BUBBLE_TAIL,
    BALLOON_STYLES_WITHOUT_TAIL,
    TAIL_SHAPE_BUBBLES,
    TAIL_SHAPE_TRIANGLE,
    BalloonObject,
    StickerObject,
    Tail,
    TextObject,
)
from ..stickers import (
    STICKER_EXCLAIM,
    STICKER_EXCLAIM_QUESTION,
    read_sticker,
)

# マークの種類の呼び名。**フキダシと同じ形**（→ `BALLOON_STYLE_LABELS`）。
# 左側は保存形式に書かれる `kind` なので変えない。
#
# **表に無い値も来る。** `kind` は選択肢を固定せずに読むので（→ 5章）、
# 素材が増えたあとの作品を古いアプリで開くと知らない値が入る。
# 引くときは必ず既定を添える（`STICKER_KIND_LABELS.get(kind, "マーク")`）
STICKER_KIND_LABELS = {
    STICKER_EXCLAIM: "ビックリマーク",
    STICKER_EXCLAIM_QUESTION: "ビックリはてなマーク",
}


def _font_changes(
    family: str | None, size_px: float | None, bold: bool | None
) -> dict[str, object]:
    """指定された項目だけを集める（`dataclasses.replace` へ渡す形）。

    セリフの書式と「次に作るセリフの書式」の両方が同じ形で受け取るので、
    組み立ては1か所にしてある（→ `EditorState.set_text_font`）。
    """
    return {
        key: value
        for key, value in (("family", family), ("size_px", size_px), ("bold", bold))
        if value is not None
    }


class TextMixin:
    """吹き出し・マーク・セリフの操作。**`EditorState` に混ぜて使う。**

    単体では動かない。`self.edit` / `self.message` / `self.select` などは
    **混ぜた先が持っている**（→ `state.py`）。
    """

    # -- 吹き出し ----------------------------------------------------------

    def add_balloon(self, rect: Rect, style: str = "ellipse") -> BalloonObject:
        """吹き出しを1つ置く。置いたものを選択状態にする。

        重なっているコマに自動で紐づける（要件定義 6.4）。紐づけておくと、
        あとでコマを動かしたときに吹き出しが付いて回る。外したいときは
        あとから解除できる。

        **四角はしっぽを消した状態で置く**（`BALLOON_STYLES_WITHOUT_TAIL`）。
        ナレーション・地の文に使うもので、指す相手がいない。先端の位置は
        他の種類と同じに入れておく——あとで「しっぽを出す」を選んだときに、
        向きの決まらないしっぽが出ないようにするため。

        **雲は丸い飛びしっぽで置く**（`BALLOON_STYLES_WITH_BUBBLE_TAIL`）。
        どちらも心の声を表すので、三角と組み合わせることがまず無い。
        どちらも**置いたときの既定**で、あとから切り替えられる。
        """
        page = self.page
        attached = attach_target(page, rect)
        tip = default_tail_tip(rect)

        with self.edit("フキダシの追加") as project:
            balloon = project.add_balloon(
                project.pages[self._page_index], rect, style, attached
            )
            balloon.tail = Tail(
                enabled=style not in BALLOON_STYLES_WITHOUT_TAIL,
                tip=tip,
                width=self.balloon_settings.tail_width,
                shape=(
                    TAIL_SHAPE_BUBBLES
                    if style in BALLOON_STYLES_WITH_BUBBLE_TAIL
                    else TAIL_SHAPE_TRIANGLE
                ),
            )
        self.select(balloon.id)
        return balloon

    # -- マーク ------------------------------------------------------------

    def add_sticker(
        self, kind: str, x: float, y: float, box: Rect | None = None
    ) -> StickerObject:
        """マークを1つ置く。置いたものを選択状態にする（要件定義 6.14）。

        `box` はドラッグで囲った範囲。渡されたら**その中に縦横比を保って
        収める**。渡さなければ (x, y) を中心に既定の大きさで置く。

        重なっているコマに自動で紐づける（フキダシと同じ → 6.4）。
        紐づけておくと、あとでコマを動かしたときに付いて回る。**切り抜かれ
        ない**のはページ直下に置いているため。

        素材は組み込みだが、**実体は既存の経路で `assets/` に入る**。
        こうしておくと作品が自己完結し、あとで素材を差し替えても既にある
        作品の見た目は変わらない。
        """
        ref, px = self.import_bytes(read_sticker(kind))
        page = self.page
        rect = (
            contain_rect_in(box, px)
            if box is not None and box.w > 0.0 and box.h > 0.0
            else default_sticker_rect(page, x, y, px)
        )
        attached = attach_target(page, rect)

        label = STICKER_KIND_LABELS.get(kind, "マーク")
        with self.edit(f"{label}の追加") as project:
            sticker = project.add_sticker(
                project.pages[self._page_index], kind, ref, rect, px, attached
            )
        self.select(sticker.id)
        return sticker

    def set_sticker_attachment(self, sticker_id: str, panel_id: str | None) -> None:
        """コマへの紐づけを付け替える。None で解除。"""
        label = "コマへの紐づけ" if panel_id else "紐づけの解除"
        with self.edit_page(label) as page:
            target = page.find(sticker_id)
            if not isinstance(target, StickerObject):
                raise KeyError(f"マークが見つかりません: {sticker_id}")
            target.attached_panel_id = panel_id

    # -- セリフ ------------------------------------------------------------

    def add_text(self, rect: Rect, content: str = "") -> TextObject:
        """セリフを1つ置く。置いたものを選択状態にする。

        **吹き出しの上なら、その吹き出しに紐づける**（要件定義 6.5）。
        吹き出しはコマの中で単独に動かせるので、コマだけに紐づけると
        吹き出しを動かしたときにセリフが取り残される。

        **書式と向きは `next_text_font` / `next_text_direction`（最後に
        指定したもの）を使う。** 作品の中でセリフの書体と大きさが揃って
        いるのが普通なので、1つ選ぶたびに既定へ戻ると、置くたびに選び直す
        ことになる。**向きも同じ**で、横書きの箇条書きを作っている最中に
        1つ置くたび縦書きへ戻ると、そのつど直す操作が要る。
        """
        page = self.page
        balloon = balloon_at(page, *rect.center)
        panel_id = None if balloon is not None else attach_target(page, rect)

        with self.edit("セリフの追加") as project:
            text = project.add_text(
                project.pages[self._page_index], content, rect, panel_id
            )
            text.font = dataclasses.replace(self.next_text_font)
            text.direction = self.next_text_direction
            text.attached_balloon_id = balloon.id if balloon is not None else None
        self.select(text.id)
        return text

    def _edit_text(
        self, text_id: str, label: str
    ) -> contextlib.AbstractContextManager[TextObject]:
        """セリフを引き直して触る（→ `_edit_found`）。"""
        return self._edit_found(text_id, label, TextObject, "セリフ")

    def set_text_content(self, text_id: str, content: str) -> None:
        with self._edit_text(text_id, "セリフの入力") as text:
            text.content = content

    def set_text_align(self, text_id: str, align: str) -> None:
        with self._edit_text(text_id, "セリフの整列") as text:
            text.align = align

    def set_text_direction(self, text_id: str, direction: str) -> None:
        """縦書き・横書きを切り替え、**次に作るセリフにも写す**。

        `align` は持ち替えない。横書きの左右の寄せが、縦書きでは上下の
        寄せとして読み替えられる（→ `manga_layout.vertical`）。別々に
        持つと、向きを往復したときにどちらの値を使うのか決められなくなる。

        写すのは書式（→ `set_text_font`）と同じ理由。向きだけ引き継がずに
        既定の縦書きへ戻ると、横書きの箇条書きを作っている最中は1つ置く
        たびに直すことになる。
        """
        with self._edit_text(text_id, "セリフの向き") as text:
            text.direction = direction
        self.set_next_text_direction(direction)

    def set_next_text_direction(self, direction: str) -> None:
        """**次に作るセリフの向きだけ**を決める。今あるセリフには触らない。

        作品を変えないので履歴には積まない（→ `set_next_text_font`）。
        """
        self.next_text_direction = direction

    def set_text_font(
        self,
        text_id: str,
        *,
        family: str | None = None,
        size_px: float | None = None,
        bold: bool | None = None,
    ) -> None:
        """選んでいるセリフの書式を変え、**次に作るセリフにも写す**。

        写すのをここでやるのは、書式を指定する操作（種類を選ぶ・大きさを
        1段階ずつ・太字）が全部ここを通るため。呼び出し側でやると、
        3か所のうち1つを直し忘れたときに「この操作だけ引き継がれない」
        という説明の付かない差ができる（→ `next_text_font`）。
        """
        with self._edit_text(text_id, "セリフの書式") as text:
            text.font = dataclasses.replace(
                text.font, **_font_changes(family, size_px, bold)
            )
        self.set_next_text_font(family=family, size_px=size_px, bold=bold)

    def scale_text(self, text_id: str, rect: Rect, size_px: float) -> None:
        """セリフを枠ごと拡大縮小する（四隅のドラッグ → 要件定義 6.5）。

        **枠と文字の大きさを1手で変える。** 別々に積むと Undo が2回要り、
        1回目では枠だけが戻った中途半端な形が出る。

        **大きさは次に作るセリフへも写す**（`set_text_font` と同じ）。
        引いて決めた大きさが引き継がれないと、作品の中でセリフの大きさを
        揃えるのに、置くたびにキーで合わせ直すことになる。
        """
        with self._edit_text(text_id, "セリフの拡大縮小") as text:
            text.rect = rect
            text.font = dataclasses.replace(text.font, size_px=size_px)
        self.set_next_text_font(size_px=size_px)

    def set_next_text_font(
        self,
        *,
        family: str | None = None,
        size_px: float | None = None,
        bold: bool | None = None,
    ) -> None:
        """**次に作るセリフの書式だけ**を決める。今あるセリフには触らない。

        セリフを1つも選んでいないときにフォントの窓から使う（→
        `MainWindow.choose_font`）。選んでいるときしか書式を指定できないと、
        **次の書式を決めるためだけに、要らないセリフを1つ置く**ことになる。

        作品を変えないので履歴には積まない（→ `next_text_font`）。
        """
        self.next_text_font = dataclasses.replace(
            self.next_text_font, **_font_changes(family, size_px, bold)
        )

    def _edit_balloon(
        self, balloon_id: str, label: str
    ) -> contextlib.AbstractContextManager[BalloonObject]:
        """吹き出しを引き直して触る（→ `_edit_found`）。"""
        return self._edit_found(balloon_id, label, BalloonObject, "吹き出し")

    def set_balloon_style(self, balloon_id: str, style: str) -> None:
        with self._edit_balloon(balloon_id, "フキダシの種類変更") as balloon:
            balloon.style = style

    def set_tail_tip(self, balloon_id: str, tip: tuple[float, float]) -> None:
        with self._edit_balloon(balloon_id, "しっぽの向き") as balloon:
            balloon.tail = dataclasses.replace(balloon.tail, tip=tip, enabled=True)

    def set_tail_root(self, balloon_id: str, root_y: float | None) -> None:
        """しっぽの付け根の縦位置。None で先端の向きに合わせる（自動）。

        **先端は動かさない。** ここは付け根を左右へ寄せる微調整で、
        しゃべっている相手を指したまま生え際だけを変えるためのもの。
        大きく向きを変えるのは `turn_tail`（→ 6.4）。
        """
        if root_y is not None:
            root_y = min(max(root_y, -1.0), 1.0)
        with self._edit_balloon(balloon_id, "しっぽの付け根") as balloon:
            balloon.tail = dataclasses.replace(balloon.tail, root_y=root_y)

    def turn_tail(self, balloon_id: str, direction: str) -> None:
        """しっぽを `direction`（→ `TAIL_DIRECTIONS`）へ**先端ごと**回す。

        付け根だけを動かすと、先端と反対側では本体に隠れて針になる。
        メニューから「上へ」を選ぶのは向きを変えたいときなので、
        先端も連れて回すほうが指示どおりの絵になる（→ 6.4）。

        回した先では付け根の高さと先端の向きが一致するので、`root_y` は
        自動（None）へ戻す。ここに値を残すと、あとで先端だけ動かした
        ときに古い指定が効いて、また針に痩せる。
        """
        with self._edit_balloon(balloon_id, "しっぽの向き") as balloon:
            tip = tail_tip_turned_to(balloon, direction)
            if tip is None:
                return  # 先端が中心に重なっている。いまの向きが決まらない
            balloon.tail = dataclasses.replace(balloon.tail, tip=tip, root_y=None)

    def set_tail_enabled(self, balloon_id: str, enabled: bool) -> None:
        label = "しっぽを出す" if enabled else "しっぽを消す"
        with self._edit_balloon(balloon_id, label) as balloon:
            balloon.tail = dataclasses.replace(balloon.tail, enabled=enabled)

    def set_tail_shape(self, balloon_id: str, shape: str) -> None:
        """しっぽを三角にするか、丸い飛びしっぽにするか（→ 要件定義 10.1）。

        **先端も付け根も動かさない。** 形だけを差し替えるので、指している
        相手と生え際はそのまま残る。
        """
        with self._edit_balloon(balloon_id, "しっぽの形") as balloon:
            balloon.tail = dataclasses.replace(balloon.tail, shape=shape)

    def set_attachment(self, balloon_id: str, panel_id: str | None) -> None:
        """コマへの紐づけを付け替える。None で解除。"""
        label = "コマへの紐づけ" if panel_id else "紐づけの解除"
        with self._edit_balloon(balloon_id, label) as balloon:
            balloon.attached_panel_id = panel_id
