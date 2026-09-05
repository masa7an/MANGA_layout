"""コマや画像に**足す効果**の操作（集中線・流線・トーン）。

3つとも**独立したオブジェクトではなく、コマや画像の属性**として持つ
（→ 要件定義 6.16、6.26、10.1）。選択・削除・複製の経路には出てこないので、
触れるのはここにある操作だけ。この共通点で1つのファイルにまとめてある。

**形を作る計算はここに無い。** `manga_layout.focus` / `.flow` / `.tone` に
あり、ここが持つのは**押されたときの振る舞い**だけ。
"""

from __future__ import annotations

import contextlib

from ..flow import (
    default_flow,
    stepped_count as flow_stepped_count,
    stepped_length as flow_stepped_length,
    stepped_width as flow_stepped_width,
)
from ..focus import default_focus, new_seed, stepped_count, stepped_width
from ..geometry import Rect, normalize_angle
from ..model import FlowLines, FocusLines, ImageObject, Panel, Tone
from ..tone import (
    ANGLE_STEP as TONE_ANGLE_STEP,
    KIND_LABELS as TONE_KIND_LABELS,
    default_tone,
    level_label as tone_level_label,
    stepped_density as tone_stepped_density,
    stepped_pitch as tone_stepped_pitch,
    stepped_thin as tone_stepped_thin,
    stepped_threshold as tone_stepped_threshold,
)


class EffectsMixin:
    """集中線・流線・トーンの操作。**`EditorState` に混ぜて使う。**

    単体では動かない。`self.edit_page` / `self.message` / `self.selected_panel`
    などは**混ぜた先が持っている**（→ `state.py`）。
    """

    # -- 集中線 ------------------------------------------------------------
    #
    # 独立したオブジェクトではなくコマの属性なので（→ 要件定義 6.16）、
    # 選択・削除・複製の経路には出てこない。触れるのはここにある操作だけ。

    @property
    def selected_focus(self) -> FocusLines | None:
        """選択中のコマに入っている集中線。無ければ None。"""
        panel = self.selected_panel
        return None if panel is None else panel.focus_lines

    def _edit_focus(
        self, panel_id: str, label: str
    ) -> contextlib.AbstractContextManager[Panel]:
        """集中線の入ったコマを引き直して触る（→ `_edit_found`）。"""
        return self._edit_found(
            panel_id, label, Panel, "集中線の入ったコマ", having="focus_lines"
        )

    def add_focus_lines(self) -> bool:
        """選択中のコマに集中線を入れる。入れたら True。

        **1コマに1つまで。** 既に入っているコマでは何もしない
        （メニュー側は「消す」に変わっているので、ここへは来ない）。
        """
        panel = self.selected_panel
        if panel is None:
            self.message.emit("集中線を入れるコマを選んでください")
            return False
        if panel.focus_lines is not None:
            return False

        panel_id = panel.id
        with self.edit_page("集中線を入れる") as page:
            page.panel(panel_id).focus_lines = default_focus(self.focus_settings)
        self.message.emit(
            "集中線を入れました。十字のつまみで中心、四角のつまみで内側の空きを変えられます"
        )
        return True

    def remove_focus_lines(self) -> bool:
        """選択中のコマから集中線を消す。消したら True。"""
        panel = self.selected_panel
        if panel is None or panel.focus_lines is None:
            return False

        panel_id = panel.id
        with self.edit_page("集中線を消す") as page:
            page.panel(panel_id).focus_lines = None
        return True

    def set_focus_shape(
        self,
        panel_id: str,
        *,
        center: tuple[float, float] | None = None,
        hole: float | None = None,
    ) -> None:
        """中心・内側の空きを差し替える。

        1回のドラッグで1手。ドラッグ中は画面側が下見を描くだけで、ここへは
        離した時点で1度だけ来る（斜めの境界と同じ流儀 → 要件定義 6.10）。
        """
        label = "集中線の中心" if center is not None else "集中線の内側"
        with self._edit_focus(panel_id, label) as panel:
            if center is not None:
                panel.focus_lines.center = center
            if hole is not None:
                panel.focus_lines.hole = hole

    def step_focus_count(self, steps: int) -> bool:
        """線の本数を増減する。変わったら True。"""
        return self._step_focus(
            "count", stepped_count, steps, lambda n: f"集中線: {n} 本"
        )

    def step_focus_width(self, steps: int) -> bool:
        """線の太さを増減する。変わったら True。"""
        return self._step_focus(
            "width",
            stepped_width,
            steps,
            lambda w: f"集中線の太さ: {w * 100:.1f}%（コマの短辺に対する割合）",
        )

    def _step_focus(self, field: str, step, steps: int, label) -> bool:
        """本数と太さの増減は、値の名前と刻み方だけが違う（→ `_step_value`）。"""
        panel = self.selected_panel
        if panel is None:
            return False
        panel_id = panel.id

        def apply(value) -> None:
            with self._edit_focus(panel_id, "集中線の調整") as target:
                setattr(target.focus_lines, field, value)

        return self._step_value(panel.focus_lines, field, step, steps, label, apply)

    def reseed_focus(self) -> bool:
        """ばらつきだけを作り直す。中心・本数・太さは変えない。

        **1手として積む。** Undo で前の形に戻せないと、気に入っていた形を
        振り直しで失うことになる（→ 要件定義 6.16）。
        """
        panel = self.selected_panel
        if panel is None or panel.focus_lines is None:
            return False

        panel_id = panel.id
        with self._edit_focus(panel_id, "集中線の形を振り直す") as target:
            target.focus_lines.seed = new_seed()
        return True

    def toggle_focus_color(self) -> bool:
        """線の色を黒⇄白で切り替える。**単純な色違い**（要件定義 6.19）。

        形（本数・太さ・空き・中心）には触らない。押すたびに必ず変わる
        操作なので、`_step_value` の端で止まるガードは要らない。
        """
        panel = self.selected_panel
        if panel is None or panel.focus_lines is None:
            return False

        panel_id = panel.id
        with self._edit_focus(panel_id, "集中線の色") as target:
            target.focus_lines.white = not target.focus_lines.white
        return True

    # -- 流線 --------------------------------------------------------------
    #
    # 集中線と同じく、独立したオブジェクトではなくコマの属性
    # （→ 要件定義 6.26）。選択・削除・複製の経路には出てこない。
    #
    # **集中線の操作と1つにまとめていない。** 値の名前だけでなく、決まる
    # ものが違う（中心と空き ⇄ 向きと長さ）ので、まとめると分岐だらけの
    # 1本になる。**増減だけは形が同じ**なので、そこは `_step_value` で
    # 集中線・トーンともまとめてある。

    @property
    def selected_flow(self) -> FlowLines | None:
        """選択中のコマに入っている流線。無ければ None。"""
        panel = self.selected_panel
        return None if panel is None else panel.flow_lines

    def _edit_flow(
        self, panel_id: str, label: str
    ) -> contextlib.AbstractContextManager[Panel]:
        """流線の入ったコマを引き直して触る（→ `_edit_found`）。"""
        return self._edit_found(
            panel_id, label, Panel, "流線の入ったコマ", having="flow_lines"
        )

    def add_flow_lines(self) -> bool:
        """選択中のコマに流線を入れる。入れたら True。

        **1コマに1つまで。** 集中線が入っていても構わない（別の項目なので
        両方持てる → 要件定義 6.26）。
        """
        panel = self.selected_panel
        if panel is None:
            self.message.emit("流線を入れるコマを選んでください")
            return False
        if panel.flow_lines is not None:
            return False

        panel_id = panel.id
        with self.edit_page("流線を入れる") as page:
            page.panel(panel_id).flow_lines = default_flow(self.flow_settings)
        self.message.emit("流線を入れました。丸のつまみをドラッグすると向きが変わります")
        return True

    def remove_flow_lines(self) -> bool:
        """選択中のコマから流線を消す。消したら True。"""
        panel = self.selected_panel
        if panel is None or panel.flow_lines is None:
            return False

        panel_id = panel.id
        with self.edit_page("流線を消す") as page:
            page.panel(panel_id).flow_lines = None
        return True

    def set_flow_angle(self, panel_id: str, angle: float) -> None:
        """向きを差し替える。1回のドラッグで1手（集中線の中心と同じ流儀）。"""
        with self._edit_flow(panel_id, "流線の向き") as panel:
            panel.flow_lines.angle = angle

    def step_flow_count(self, steps: int) -> bool:
        """線の本数を増減する。変わったら True。"""
        return self._step_flow(
            "count", flow_stepped_count, steps, lambda n: f"流線: {n} 本"
        )

    def step_flow_width(self, steps: int) -> bool:
        """線の太さを増減する。変わったら True。"""
        return self._step_flow(
            "width",
            flow_stepped_width,
            steps,
            lambda w: f"流線の太さ: {w * 100:.1f}%（コマの短辺に対する割合）",
        )

    def step_flow_length(self, steps: int) -> bool:
        """線の長さを増減する。変わったら True。"""
        return self._step_flow(
            "length",
            flow_stepped_length,
            steps,
            lambda v: f"流線の長さ: コマの対角線の {v * 100:.0f}%",
        )

    def _step_flow(self, field: str, step, steps: int, label) -> bool:
        """本数・太さ・長さの増減は、値の名前と刻み方だけが違う
        （→ `_step_value`）。
        """
        panel = self.selected_panel
        if panel is None:
            return False
        panel_id = panel.id

        def apply(value) -> None:
            with self._edit_flow(panel_id, "流線の調整") as target:
                setattr(target.flow_lines, field, value)

        return self._step_value(panel.flow_lines, field, step, steps, label, apply)

    def reseed_flow(self) -> bool:
        """ばらつきだけを作り直す。向き・本数・太さ・長さは変えない。"""
        panel = self.selected_panel
        if panel is None or panel.flow_lines is None:
            return False

        panel_id = panel.id
        with self._edit_flow(panel_id, "流線の形を振り直す") as target:
            target.flow_lines.seed = new_seed()
        return True

    def toggle_flow_color(self) -> bool:
        """線の色を黒⇄白で切り替える（集中線と同じ形 → 6.19）。"""
        panel = self.selected_panel
        if panel is None or panel.flow_lines is None:
            return False

        panel_id = panel.id
        with self._edit_flow(panel_id, "流線の色") as target:
            target.flow_lines.white = not target.flow_lines.white
        return True

    # -- トーン（画像の黒の置き換え → 要件定義 10.1） ------------------------

    @property
    def tone_image(self) -> ImageObject | None:
        """トーンの操作が効く画像。無ければ None。

        **絵を選んでいなくても、コマを選んでいれば効く。** トーンの持ち主は
        画像だが（→ 要件定義 6.27）、絵を選ぶにはコマをダブルクリックして
        中へ入る必要がある。そこを要求すると、集中線・流線と同じつもりで
        コマを選んだ利用者には**ただグレーの項目**に見える（本人談 2026-08-06）。

        **絵が2枚以上あるコマでは決まらない**ので None を返す。背景の上に
        キャラを重ねる使い方があり、どちらに掛けるかは黙って選べない
        （→ `tone_ambiguous`）。
        """
        image = self.selected_image
        if image is not None:
            return image
        images = self.panel_images
        return images[0] if len(images) == 1 else None

    @property
    def panel_images(self) -> list[ImageObject]:
        """選択中のコマに入っている絵。コマを選んでいなければ空。"""
        panel = self.selected_panel
        if panel is None:
            return []
        return [c for c in panel.children if isinstance(c, ImageObject)]

    @property
    def tone_ambiguous(self) -> bool:
        """コマを選んでいて、絵が2枚以上ある。どれに掛けるか決まらない。

        **項目はグレーにせず、押したら状態表示で断る。** グレーにすると
        「使えない」は伝わるが理由が伝わらない（斜めのコマを複製できない
        と伝えるときと同じ線引き → 6.15）。
        """
        return self.selected_image is None and len(self.panel_images) > 1

    @property
    def selected_tone(self) -> Tone | None:
        """操作が効く画像に入っているトーン。無ければ None。"""
        image = self.tone_image
        return None if image is None else image.tone

    def _edit_tone(
        self, image_id: str, label: str, *, merge_key: str | None = None
    ) -> contextlib.AbstractContextManager[ImageObject]:
        """トーンの入った画像を引き直して触る（→ `_edit_found`）。

        `merge_key` を渡すと、連打ぶんが履歴の1手にまとまる（→ `History.commit`）。
        """
        return self._edit_found(
            image_id,
            label,
            ImageObject,
            "トーンの入った画像",
            having="tone",
            merge_key=merge_key,
        )

    def add_tone(self) -> bool:
        """選択中の画像にトーンを入れる。入れたら True。

        **範囲は絞らない状態で入る。** 絞るのは足りなかったときの手当てで、
        まず全体に掛けて様子を見るほうが手数が少ない（→ 要件定義 10.1）。
        """
        if self.tone_ambiguous:
            self.message.emit(
                "このコマには絵が複数あります。"
                "ダブルクリックで絵を選んでから入れてください"
            )
            return False
        image = self.tone_image
        if image is None:
            self.message.emit("トーンを入れる絵かコマを選んでください")
            return False
        if image.tone is not None:
            return False

        image_id = image.id
        with self.edit_page("トーンを入れる") as page:
            target = page.find(image_id)
            if isinstance(target, ImageObject):
                target.tone = default_tone()
        self.message.emit(
            "黒ベタをトーンにしました。範囲を絞るには道具の「トーン範囲を調整」へ"
        )
        return True

    def remove_tone(self) -> bool:
        """選択中の画像からトーンを消す。消したら True。"""
        image = self.tone_image
        if image is None or image.tone is None:
            return False

        image_id = image.id
        with self.edit_page("トーンを消す") as page:
            target = page.find(image_id)
            if isinstance(target, ImageObject):
                target.tone = None
        return True

    # 押した直後の状態表示は、**割合ではなく「何段目か」で出す**
    # （→ 要件定義 6.27）。`0.3% 未満` の形だと「設定値を1つ決めた」ように
    # 読め、**同じ項目をもう一度押してよいことが伝わらない**（本人談
    # 2026-08-06。「細い線を残す」を3回押してちょうどよくなった）。
    # メニューの項目名にも同じ数字が出る（→ `ToneMenu`）。

    def step_tone_threshold(self, steps: int) -> bool:
        """どこまでを黒と見るかを増減する。変わったら True。"""
        return self._step_tone(
            "threshold",
            tone_stepped_threshold,
            steps,
            # ここだけ元の値も添える。**絵ごとに合わせる値**で、
            # 前に上手くいった絵と見比べられるようにしておく（→ 6.27）
            lambda n: f"拾う黒: {tone_level_label('threshold', n)}（明るさ {n} 以下）",
        )

    def step_tone_pitch(self, steps: int) -> bool:
        """斜線の間隔を増減する。変わったら True。"""
        return self._step_tone(
            "pitch",
            tone_stepped_pitch,
            steps,
            lambda v: f"斜線の間隔: {tone_level_label('pitch', v)}",
        )

    def step_tone_density(self, steps: int) -> bool:
        """濃さ（線の太さ）を増減する。変わったら True。"""
        return self._step_tone(
            "density",
            tone_stepped_density,
            steps,
            lambda v: f"トーンの濃さ: {tone_level_label('density', v)}",
        )

    def step_tone_thin(self, steps: int) -> bool:
        """どこまでを細いと見るかを増減する。変わったら True。"""
        return self._step_tone(
            "thin",
            tone_stepped_thin,
            steps,
            lambda v: (
                # 0 は「細さで選り分けない」という別の状態。段数だけだと
                # 端に着いたことしか分からないので、言葉でも断る
                f"細い線を残す: {tone_level_label('thin', v)}"
                + ("（細さで選り分けない）" if v <= 0.0 else "")
            ),
        )

    def set_tone_kind(self, kind: str) -> bool:
        """トーンの見た目（斜線・灰色・白抜き）を切り替える。変わったら True。

        **効かなくなる値を消さない。** 灰色にすると `angle` と `pitch` は
        絵に出なくなるが、持ったままにしておけば斜線へ戻したときに前の
        調整がそのまま返る（メニュー側はグレーにして「今は効かない」と
        示す → `ToneMenu.refresh`）。

        **連打をまとめない。** 3つを行き来して見比べる操作なので、1手ずつ
        積んで Undo で1つずつ戻れるほうがよい（増減の連打とは別 → `_step_tone`）。
        """
        image = self.tone_image
        if image is None or image.tone is None or image.tone.kind == kind:
            return False

        image_id = image.id
        with self._edit_tone(image_id, "トーンの種類") as target:
            target.tone.kind = kind
        self.message.emit(f"トーンの種類: {TONE_KIND_LABELS[kind]}")
        return True

    def step_tone_angle(self, steps: int) -> bool:
        """斜線の向きを 15 度ずつ回す。**つまみは作らない**（→ 要件定義 10.1）。"""
        image = self.tone_image
        if image is None or image.tone is None:
            return False

        angle = normalize_angle(image.tone.angle + steps * TONE_ANGLE_STEP)
        image_id = image.id
        with self._edit_tone(image_id, "斜線の向き") as target:
            target.tone.angle = angle
        self.message.emit(f"斜線の向き: {angle:.0f}°")
        return True

    def _step_tone(self, field: str, step, steps: int, label) -> bool:
        """しきい値・間隔・濃さ・細さの増減は、値の名前と刻み方だけが違う
        （→ `_step_value`）。

        **連打ぶんは履歴の1手にまとめる**（`merge_key`）。1回ずつ積むと、
        20回押した調整を戻すのに Undo を20回押すことになる（セリフの入力と
        同じ扱い → `History.commit`）。**鍵に項目の名前を混ぜる**ので、
        濃さを触ってからしきい値を触っても別の手として積まれる。
        """
        image = self.tone_image
        if image is None:
            return False
        image_id = image.id

        def apply(value) -> None:
            with self._edit_tone(
                image_id, "トーンの調整", merge_key=f"tone:{image_id}:{field}"
            ) as target:
                setattr(target.tone, field, value)

        return self._step_value(image.tone, field, step, steps, label, apply)

    def set_tone_area(self, image_id: str, area: Rect | None) -> None:
        """絞る矩形を差し替える。1回のドラッグで1手（流線の向きと同じ流儀）。

        `area` は**画像に対する割合**。`None` で絞らない状態に戻す。
        **はみ出していても直さない**——0〜1 の外は絵が無いだけで、画像の縁で
        自然に切れる（→ 要件定義 10.1）。
        """
        with self._edit_tone(image_id, "トーンの範囲") as target:
            target.tone.area = area

    def clear_tone_area(self) -> bool:
        """絞りを外して画像全体に戻す。戻したら True。"""
        image = self.tone_image
        if image is None or image.tone is None or image.tone.area is None:
            return False
        self.set_tone_area(image.id, None)
        self.message.emit("トーンの範囲を画像全体に戻しました")
        return True
