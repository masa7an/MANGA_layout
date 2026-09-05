"""コマそのものを扱う操作（斜め・ロック・次の提案・重なり順）。

**コマを作る・動かす・割るはここに無い。** それらは `layout.py` の計算を
`canvas.py` が呼ぶ形になっていて、ここにあるのは**コマを選んだあとに
効くもの**だけ。
"""

from __future__ import annotations

from .. import next_panel
from ..geometry import Rect
from ..model import Page
from ..slant import (
    flip_slant_pair,
    set_slant_pair_rect,
    slide_slant_pair,
)

# 次のコマの提案（→ 要件定義 10.5）。**1手の名前と、履歴のまとめ鍵を兼ねる。**
# 「直前の1手の続きか」は `history.merge_key` で見る（→ `suggest_next_panel`）
SUGGEST_LABEL = "次のコマを提案"


class PanelMixin:
    """斜めのコマ・ロック・次の提案・重なり順。**`EditorState` に混ぜて使う。**

    単体では動かない。`self.edit_page` / `self.message` / `self.selected_panel`
    などは**混ぜた先が持っている**（→ `state.py`）。
    """

    # -- 斜めのコマ --------------------------------------------------------
    #
    # 組を作り直す操作は3つ（向きの反転・境界の移動・大きさ変更）あり、
    # **どれも最後は `slant.rebuild_slant_pair` → `check_slant` を通る**。
    # 割れない大きさなら `ValueError` で断られるので、受け方も1か所に
    # まとめてある（→ `_edit_slant`）。

    def _edit_slant(self, label: str, apply) -> bool:
        """斜めの組を作り直す1手。作り直せたら True。

        `apply(page)` の中で `check_slant` が断ったら、**理由を状態表示に
        出して False を返す**（コマの分割と同じ扱い → `PageView._apply_split`）。

        **3つの操作で受け方を書き分けない。** 書き分けていた頃は大きさ変更
        にしか受けが無く、反転と境界の移動では `ValueError` が Qt の
        スロットを突き抜けていた。突き抜けても PySide6 は印字して続けるので、
        **画面には何も出ないまま操作だけが効かない**（2026-08-09 に発見）。

        普段はここへ来ない。**ドラッグの下見が先に押し戻す**ので
        （→ `slant.clamp_slant_rect` / `clamp_slant_ratio`）、断りが要るのは
        手で書き換えた project.json のように、元から割れない大きさの組だけ。
        """
        try:
            with self.edit_page(label) as page:
                apply(page)
        except ValueError as e:
            self.message.emit(str(e))
            return False
        return True

    def flip_slant(self) -> bool:
        """選択中の斜めの組の向きを反転する。反転できたら True。

        利用者がつつけるのはここだけ。位置と角度は分割したときに決まり、
        あとは外側の矩形にぶら下がって動く。
        """
        panel = self.selected_panel
        if panel is None or self.page.slant_pair_of(panel.id) is None:
            self.message.emit("斜めに割ったコマを選んでください")
            return False

        panel_id = panel.id
        return self._edit_slant(
            "斜めの向きを反転",
            lambda page: flip_slant_pair(
                page, page.slant_pair_of(panel_id), self.settings
            ),
        )

    def slide_slant(self, panel_id: str, ratio: float) -> bool:
        """斜めの境界を左右にずらす。ずらせたら True。

        1回のドラッグで1手。ドラッグ中は画面側が下見を描くだけで、
        ここへは離した時点で1度だけ来る（しっぽの付け根と同じ流儀）。
        """

        def apply(page: Page) -> None:
            pair = page.slant_pair_of(panel_id)
            if pair is not None:
                slide_slant_pair(page, pair, ratio, self.settings)

        return self._edit_slant("斜めの境界を移動", apply)

    def set_slant_rect(self, panel_id: str, rect: Rect) -> bool:
        """斜めの組の外側の矩形を差し替える（大きさ変更）。変えられたら True。

        1枚ずつ変形せず組ごと作り直す。片方ずつ動かすと、傾きと隙間が
        左右で食い違う（→ `slant.set_slant_pair_rect`）。
        """

        def apply(page: Page) -> None:
            pair = page.slant_pair_of(panel_id)
            if pair is not None:
                set_slant_pair_rect(page, pair, rect, self.settings)

        return self._edit_slant("斜めのコマの大きさ変更", apply)

    # -- コマのロック --------------------------------------------------------
    #
    # 完成したコマを誤って動かさないためのもの（→ 要件定義 6.17）。
    # 止めるのは移動・大きさ変更・分割・削除だけで、中の画像や紐づいた
    # 吹き出し・セリフ・集中線には触らない。

    def set_panel_locked(self, panel_id: str, locked: bool) -> None:
        """1枚をロック／解除する。斜めの組なら**両方**に効かせる。

        片方だけ解いても動かせないままでは意味が無い（→ `is_panel_locked`
        が「片方でもロックなら組ごと動かせない」で見ているのと対）。
        """
        label = "コマをロック" if locked else "コマのロックを解除"
        with self.edit_page(label) as page:
            pair = page.slant_pair_of(panel_id)
            targets = pair.members() if pair is not None else (panel_id,)
            for target_id in targets:
                page.panel(target_id).locked = locked

    def toggle_panel_lock(self) -> bool:
        """選択中のコマのロックを切り替える。変わったら True。"""
        panel = self.selected_panel
        if panel is None:
            return False
        self.set_panel_locked(panel.id, not self.is_locked_selection)
        return True

    def lock_all_panels(self) -> bool:
        """このページのコマをすべてロックする。変わったら True。

        既に全部ロック済みなら**何もしない**。押しても変化の無い操作で
        Undo の一手を使わせない（→ `_step_value` と同じ流儀）。
        """
        page = self.page
        if not page.panels or all(p.locked for p in page.panels):
            return False
        with self.edit_page("このページのコマをすべてロック") as page:
            for panel in page.panels:
                panel.locked = True
        return True

    def unlock_all_panels(self) -> bool:
        """このページのコマのロックをすべて解除する。変わったら True。"""
        if not any(p.locked for p in self.page.panels):
            return False
        with self.edit_page("このページのコマのロックをすべて解除") as page:
            for panel in page.panels:
                panel.locked = False
        return True

    # -- 次のコマの提案 ------------------------------------------------------
    #
    # 描きかけのページを見て、次に置くコマを提案する（→ 要件定義 10.5）。
    # 幾何の計算は `next_panel.py` にあり、ここは**押されたときの振る舞い**だけを持つ。
    #
    # **押すたびに、直前の提案を次の案へ差し替える。** 増やしていくのではない。
    # 案どうしは重なるので、並べて置くと読めない絵になる。
    #
    # **履歴は `merge_key` で1手にまとめる。** 5回押しても Undo 1回で消える。
    # 「気に入らなければ元に戻す」を、押した回数だけ繰り返させない。

    def suggest_next_panel(self) -> bool:
        """次のコマを提案し、そのとおりに置く。置けたら True。

        **成功のお知らせもここで出す。** 何番目の案かは呼ぶ側からは分からないため
        （他の操作は窓の側でお知らせを出しているが、ここだけ例外にしてある）。
        """
        page = self.page
        if not next_panel.supported(page):
            # **「斜め」だけではない。** 面積の無いコマや、頂点の順が崩れたコマも
            # 断る（→ `next_panel.supported`）。文言はその全部を指す言い方にしてある
            self.message.emit("四角でないコマがあるページでは提案できません")
            return False

        # 差し替えるかどうかは、**履歴がまとめている鍵**だけで決める。
        # これは次の `edit()` が直前の1手へ吸い込まれる条件そのものなので、
        # **「差し替えたのに履歴は別の1手」があり得なくなる。**
        #
        # `undo_label` で見てはいけない。あちらは**積まれた1手の名前**で、
        # コマを選ぶ・道具を持ち替える・ページを移るだけで `break_merge()` が
        # 走っても変わらない。差し替えたのに新しい1手が積まれ、**Undo 1回では
        # 戻らなくなる**（2026-09-05 実測。コマをクリックしてもう一度押すと再現）。
        #
        # 途中で別の操作をしたら、その提案は**確定したもの**として扱い、次は新しく足す
        replacing = self.history.merge_key == SUGGEST_LABEL
        ignore = self._suggested if replacing else ()

        found = next_panel.suggestions(
            self.project, page, ignore, self.settings.margin, self.settings.gutter
        )
        if not found:
            self._suggested = ()
            self.message.emit("提案できる形が見つかりません")
            return False

        index = (self._suggest_index + 1) % len(found) if replacing else 0
        with self.edit(SUGGEST_LABEL, merge_key=SUGGEST_LABEL) as project:
            edited = project.pages[self._page_index]
            if replacing:
                # **消すのは `remove_panel` を通す。** 一覧を直に書き替えると、
                # 紐づいたフキダシ・セリフの紐づけ解除と、斜めの組の解消を
                # 飛ばす。今は直前に自分が置いたコマしか消さないので、どちらも
                # 持たない——**だから今は無害だが、削除の作法がここだけ違う**
                # （2026-09-05 に揃えた）。
                #
                # 見つからないものは飛ばす。`remove_panel` は無いと例外を投げ、
                # `edit()` が巻き戻して画面にエラーが出る。**提案の押し直しで
                # 出してよいエラーではない。**
                for panel_id in ignore:
                    if any(p.id == panel_id for p in edited.panels):
                        edited.remove_panel(panel_id)
            added = next_panel.add_suggestion(project, edited, found[index])
        self._suggested = tuple(p.id for p in added)
        self._suggest_index = index
        self.message.emit(
            f"提案 {index + 1}/{len(found)}: {found[index].text()}"
            "（もう一度押すと次の案）"
        )
        return True

    # -- コマの重なり順 ------------------------------------------------------
    #
    # コマ同士が重なったとき、どちらを手前に描くかを `z` で決める。描く順
    # （`render.draw_page`）とクリック判定（`layout.panel_at`）はどちらも
    # 元から `z` を見ているので、ここで書き換えるだけで両方に効く。
    #
    # **「1つ手前へ」ではなく「最前面へ」にしてある。** 重なっていない
    # コマを1つ跨いでも見た目が変わらず、押しても何も起きないように
    # 見える。段階を数えさせないほうが確実に届く。

    @staticmethod
    def _panel_group(page: Page, panel_id: str) -> tuple[str, ...]:
        """一緒に動かすコマ。斜めの組なら**両方**、でなければ自分だけ。

        ロック（→ `set_panel_locked`）と同じ扱い。片割れだけ手前に出すと、
        1枚を割って作ったはずの2枚の間に別のコマが挟まる。
        """
        pair = page.slant_pair_of(panel_id)
        return pair.members() if pair is not None else (panel_id,)

    def _panel_group_at_edge(self, panel_id: str, *, front: bool) -> bool:
        """そのコマ（と相方）が既に一番手前／一番奥に居るか。"""
        page = self.page
        group = set(self._panel_group(page, panel_id))
        others = [p.z for p in page.panels if p.id not in group]
        if not others:
            return True
        mine = [page.panel(i).z for i in group]
        return min(mine) > max(others) if front else max(mine) < min(others)

    def can_raise_panel(self, panel_id: str) -> bool:
        return not self._panel_group_at_edge(panel_id, front=True)

    def can_lower_panel(self, panel_id: str) -> bool:
        return not self._panel_group_at_edge(panel_id, front=False)

    def raise_panel(self, panel_id: str) -> bool:
        """コマを最前面へ。変わったら True。

        既に一番手前なら**何もしない**。押しても変化の無い操作で Undo の
        一手を使わせない（→ `lock_all_panels` と同じ流儀）。
        """
        if not self.can_raise_panel(panel_id):
            return False
        with self.edit_page("コマを手前へ") as page:
            top = max(p.z for p in page.panels)
            for offset, target_id in enumerate(self._ordered_group(page, panel_id), 1):
                page.panel(target_id).z = top + offset
        return True

    def lower_panel(self, panel_id: str) -> bool:
        """コマを最背面へ。変わったら True。"""
        if not self.can_lower_panel(panel_id):
            return False
        with self.edit_page("コマを奥へ") as page:
            bottom = min(p.z for p in page.panels)
            group = self._ordered_group(page, panel_id)
            for offset, target_id in enumerate(reversed(group), 1):
                page.panel(target_id).z = bottom - offset
        return True

    @classmethod
    def _ordered_group(cls, page: Page, panel_id: str) -> list[str]:
        """一緒に動かすコマを、今の重なり順のまま並べたもの。

        斜めの組は互いに重ならないので見た目には出ないが、順を崩さずに
        運べば、あとで組に別の意味が付いたときに巻き込まれない。
        """
        return sorted(cls._panel_group(page, panel_id), key=lambda i: page.panel(i).z)
