"""絵まわりの操作（読み込み・切り抜き・ラフ）。

**この3つは同じ素材を扱う。** 絵は `AssetStore` に本体を置き、作品側は
参照（`ref`）だけを持つ。切り抜きのマスクもラフの下敷きも同じ入れ物に
入るので、読み書き・使い回しの判定・後始末を1か所にまとめてある。
"""

from __future__ import annotations

import dataclasses

from PySide6.QtGui import QImage

from ..assets import AssetStore
from ..errors import (
    AssetError,
    MaskSizeError,
)
from ..geometry import Rect
from ..image_masks import (
    decode_mask,
    safe_masked_preview,
)
from ..images import (
    Preview,
    bake_key,
    decode,
    file_px,
    preview_from_bytes,
    readable_file,
    size_px,
    to_png_bytes,
    toned,
)
from ..layout import contain_rect_in
from ..model import (
    ImageObject,
    PageRough,
)
from ..wand import (
    GrayImage,
    intersected,
    removed,
    select_in,
    to_gray,
)

# 切り抜き（自動領域選択）の許容差で動かせる範囲と、1回ぶん。
#
# **上限を 64 で止める。** 実測では、陰影のある絵を 64 で押すと画面の半分が
# 選ばれた（→ `data/` の検討メモ）。そこから先は「区画を選ぶ」ではなくなる
WAND_TOLERANCE_MIN = 0
WAND_TOLERANCE_MAX = 64
WAND_TOLERANCE_STEP = 4

# 切り抜きを1回押したときの言い方3つ。
# **（履歴に積む名前、済んだときの案内、何も変わらなかったときの案内）**
#
# **履歴の名前を案内に流用しない。** 履歴は辞書形で並べるもの（→ Undo の一覧）
# なので、そのまま「ました」を足すと**「押した所を消すました」**になる。
# 2026-09-05 まで、押すたびに実際にそう出ていた——`label` 1つで兼ねようとして
# 無理が出た形なので、**兼ねるのをやめて3つ持つ**
WAND_ERASE_WORDS = (
    "押した所を消す",
    "押した所を消しました",
    "そこはもう消えています",
)
WAND_KEEP_WORDS = (
    "押した所だけ残す",
    "押した所だけ残しました",
    "もう、そこだけが残っています",
)


class ImageMixin:
    """絵の読み込み・切り抜き・ラフの操作。**`EditorState` に混ぜて使う。**

    単体では動かない。`self.edit_page` / `self.message` / `self.pending_assets`
    などは**混ぜた先が持っている**（→ `state.py`）。
    """

    # -- 画像 --------------------------------------------------------------

    def read_asset(self, ref: str) -> bytes | None:
        """画像の実体。まだ保存していないものは預かり所から取る。"""
        data = self.pending_assets.get(ref)
        if data is not None:
            return data
        if self.project_dir is None:
            return None
        store = AssetStore(self.project_dir)
        return store.read(ref) if store.exists(ref) else None

    def has_asset(self, ref: str) -> bool:
        """その画像が使えるか（実体があり、画像として開ける形か）。

        **展開はしない**（→ `preview` との違い）。全ページ分を巡回する
        処理から呼ぶので、まだ画面に出しておらずキャッシュに乗っていない
        画像があると、`preview()` 経由では展開待ちで固まる（2026-08-08 発見）。
        ヘッダーだけを見る `images.readable_file` で足りる。

        **「無い」と「開けない」を分けない。** 書き出した結果はどちらも
        同じ——そこが白く抜ける——で、利用者に伝えたいこともそこだから。
        分けていた頃は、点検（`check.inspect_project`）が実体の有無だけを
        見て「問題なし」と答える一方、書き出し前の警告
        （`ui.export.missing_assets_in`）は展開できるかを見ていたため、
        **壊れた画像1枚で2つの機能が違う答えを返していた**
        （2026-08-09 に発見。両方をここへ集約した）。
        """
        if ref in self.pending_assets:
            # 預かり分は取り込み時に `images.decode` を通っている（→ 5章）。
            # 開けることが確認済みなので、読み直さない
            return True
        if self.project_dir is None:
            return False
        store = AssetStore(self.project_dir)
        try:
            path = store.resolve(ref)
        except AssetError:
            # 参照の形自体が壊れている（`assets/` の外を指すなど）
            return False
        return path.is_file() and readable_file(path)

    def asset_px(self, ref: str) -> tuple[int, int] | None:
        """その画像の画素寸法。使えない画像なら None。**展開はしない。**

        `has_asset` と同じ1回の読みで分かることを、寸法まで返すだけ
        （→ `images.file_px`）。点検（`check.inspect_project`）が、切り抜きの
        マスクが絵と同じ寸法かを見るのに使う。
        """
        data = self.pending_assets.get(ref)
        if data is not None:
            # 預かり分はまだディスクに無い。取り込み時に展開済みなので、
            # 縮小版の持っている原寸をそのまま答える（→ `import_bytes`）
            preview = self.image_cache.get(ref, lambda: data)
            return None if preview is None else preview.source_px
        if self.project_dir is None:
            return None
        try:
            path = AssetStore(self.project_dir).resolve(ref)
        except AssetError:
            return None
        return file_px(path) if path.is_file() else None

    def preview(self, ref: str) -> Preview | None:
        """画面に描くための1枚。無い・壊れているときは None。

        **トーンは掛かっていない。** ラフのように「画像そのもの」が要る
        経路が使う。コマの中の画像を描くときは `image_preview` を通す。
        """
        return self.image_cache.get(ref, lambda: self.read_asset(ref))

    def image_preview(self, image) -> Preview | None:
        """画面に描くための1枚。**トーンと切り抜きが入っていれば焼いたほうを返す。**

        受け取るのが参照文字列ではなく画像そのものなのは、同じ `asset` でも
        トーンの設定や切り抜き次第で別の絵になるため（→ 要件定義 10.1・10.3）。
        マーク（`StickerObject`）もここを通るが、あちらはどちらも持たない。

        **切り抜きのある画像だけ、原寸から作り直す。** マスクは元画像の
        ピクセル座標に結び付いているので、`image_cache` が持っている縮小版へは
        掛けられない（→ `image_masks.masked_preview`）。無い画像は今までどおり
        縮小版から焼くので、この機能を使っていない作品では何も変わらない。
        """
        tone = getattr(image, "tone", None)
        mask_ref = getattr(image, "mask_asset", "")
        if tone is None and not mask_ref:
            return self.preview(image.asset)
        key = bake_key(image)
        if not mask_ref:
            return self.baked_cache.get(
                key, lambda: toned(self.preview(image.asset), tone)
            )
        return self.baked_cache.get(key, lambda: self._masked_preview(image, tone))

    def _masked_preview(self, image, tone) -> Preview | None:
        """切り抜きを焼いた画面用の1枚。実体が欠けていれば None。

        **マスクだけが欠けているときは、切り抜き無しの絵を返す**（→ 計画 段階1〜3）。
        開けなくする・何も描かない、はしない——欠けた絵1枚で作品が読めなく
        なるのは割に合わない、という `ImageCache` の考え方と揃えてある。
        マスクの欠けは点検（`check.KIND_MISSING_MASK`）が拾う。
        """
        baked = safe_masked_preview(
            self.read_asset(image.asset),
            self.read_asset(image.mask_asset),
            tone,
            reduced=True,
        )
        if baked is not None:
            return baked
        plain = self.preview(image.asset)
        return plain if tone is None else toned(plain, tone)

    def forget_if_unused(self, ref: str) -> None:
        """その参照が作品のどこからも使われていなければ、覚えを手放す。

        削除・差し替えで使われなくなった絵の縮小版・トーン焼き版が
        キャッシュに残り続けていた（`ImageCache.forget` /
        `ToneCache.forget` 自体はあったが、呼ぶ場所が無かった。
        2026-08-08 に発見）。**まだ使われている参照は手放さない。**
        同じ絵を複数箇所で使い回している場合に、片方を消しただけで
        もう片方まで展開し直しになるのを避ける。

        実体（assets/）は消さない削除・差し替え（→ `window.delete_image`）
        と同じ考え方で、ここも Undo で戻ったときに困らないよう**キャッシュ
        だけ**を対象にする。手放しても、次に描くときに読み直すだけで
        正しい絵に戻る。
        """
        if ref in self.project.referenced_assets():
            return
        self.image_cache.forget(ref)
        self.baked_cache.forget(ref)
        self.reduced_cache.forget(ref)
        if self.wand_scan is not None and self.wand_scan[0] == ref:
            self.forget_wand_gray()

    def import_bytes(self, data: bytes) -> tuple[str, tuple[int, int]]:
        """画像を取り込み、参照と原寸のピクセル寸法を返す。

        **展開できるか先に確かめる。** `assets.py` はバイト列しか見ないため、
        署名だけ正しい壊れたファイルを通してしまう。内容ハッシュが名前なので、
        一度入れてしまうと人が見分けられなくなる。
        """
        preview = preview_from_bytes(data)  # 壊れていればここで例外
        if self.project_dir is None:
            ref = self.pending_assets.add(data)
        else:
            ref = AssetStore(self.project_dir).add_bytes(data)
        self.image_cache.put(ref, preview)
        return ref, preview.source_px

    def place_image(self, panel_id: str, data: bytes) -> ImageObject:
        """コマに画像を1枚置く。置いた画像を選択状態にする。

        最初は**コマに収まる大きさ**（全体が見える）にする。埋めたいときは
        「コマにフィット」を使う。いきなり埋めると、絵のどこが切れているのか
        分からないまま進んでしまう。
        """
        ref, px = self.import_bytes(data)
        page = self.page
        rect = contain_rect_in(page.panel(panel_id).shape.bounds(), px)

        with self.edit("画像の配置") as project:
            target = project.pages[self._page_index].panel(panel_id)
            image = project.add_image(target, ref, rect, px)
        self.select(image.id)
        return image

    def replace_image(self, image_id: str, data: bytes) -> ImageObject | None:
        """コマの中の画像を、別のファイルの絵に入れ替える。無い画像なら None。

        **重なり順（z）は元の画像から引き継ぐ。** 背景の上にキャラを重ねる
        使い方があり、ただ置き直す形（末尾に足す）にすると、背景を差し替え
        ただけで**キャラの手前へ出てしまう**。

        大きさは置いたときと同じ「コマに収まる」から始める（→ `place_image`）。
        元の矩形をそのまま使い回すと、縦横比の違う絵で歪む。

        取り込みが先で削除が後。壊れたファイルを選んだときは `import_bytes`
        が例外を出し、**元の画像が消えないまま**呼び出し側へ戻る。
        """
        panel = self.page.panel_of_image(image_id)
        if panel is None:
            return None
        panel_id = panel.id
        old = next(c for c in panel.children if c.id == image_id)
        old_z, old_ref = old.z, old.asset

        ref, px = self.import_bytes(data)
        rect = contain_rect_in(panel.shape.bounds(), px)

        with self.edit("画像の差し替え") as project:
            target = project.pages[self._page_index].panel(panel_id)
            image = project.add_image(target, ref, rect, px)
            image.z = old_z
            target.children = [c for c in target.children if c.id != image_id]
        self.select(image.id)
        self.forget_if_unused(old_ref)
        return image

    # -- 切り抜き（AI で作ったマスク → 要件定義 10.3） ---------------------
    #
    # **作品が変わるのは適用の1回だけ。** 候補を見比べている間は何も変えない
    # ので、推論の失敗や見比べが未保存の変更や Undo の履歴を汚さない
    # （→ `SAM3実装計画.md` 段階5）。

    def import_mask_bytes(self, data: bytes) -> str:
        """マスクを取り込んで参照を返す。展開できなければ例外。

        **画像用の入れ物（`image_cache`）には入れない。** マスクは描く相手
        ではなく、絵に掛ける材料。混ぜると、引く側が「これは絵か、マスクか」を
        判断できなくなる（→ `ImageCache` の注記）。
        """
        decode_mask(data)  # 壊れていればここで例外
        return self._keep_mask(data)

    def _keep_mask(self, data: bytes) -> str:
        """**展開して確かめ済みの**マスクを `assets/` へ入れて、参照を返す。

        確認を済ませた側（→ `apply_image_mask` は寸法を見るために展開する）が
        もう一度展開しないための入口。**同じ PNG を2回展開していた**
        （11KB のマスクで 8ms ずつ。2026-09-05 発見）。
        """
        if self.project_dir is None:
            return self.pending_assets.add(data)
        return AssetStore(self.project_dir).add_bytes(data)

    def apply_image_mask(
        self, image_id: str, data: bytes, *, label: str = "切り抜きの適用"
    ) -> bool:
        """表示中のページの画像に切り抜きを掛ける。**作品が変わったら True。**

        False は「作品は何も変わっていない」——画像が見つからないときと、
        **既に同じ切り抜きが掛かっているとき**の2つ。呼ぶ側がすることは
        どちらも同じ（作品には触らず、案内だけ出す）なので分けていない。

        `label` は履歴に積む名前。**押した所を消す操作（→ `erase_region_at`）は
        別の名前で積む**——Undo の一覧で「切り抜きの適用」が並ぶだけだと、
        どれがどの操作か分からない。

        **寸法が合わなければ断る**（`MaskSizeError`）。縮めて合わせることは
        しない——合わせると、ずれた組み合わせが「輪郭がわずかにずれた絵」に
        なるだけで人が気づけない（→ `image_masks.apply_mask`）。

        既に切り抜いてある画像へ掛け直すのも同じ1手。前のマスクの実体は
        消さず、使われなくなった実体は「未使用ファイルを整理」が拾う
        （→ 要件定義 5章。差し替え・削除と同じ扱い）。
        """
        image = self.page.find(image_id)
        if not isinstance(image, ImageObject):
            return False

        mask_px = size_px(decode_mask(data))
        if mask_px != image.src_px:
            raise MaskSizeError(
                f"切り抜きの大きさが画像と違います"
                f"（画像 {image.src_px[0]:,} × {image.src_px[1]:,} 画素、"
                f"切り抜き {mask_px[0]:,} × {mask_px[1]:,} 画素）"
            )

        old_ref = image.mask_asset
        # 展開はすぐ上の寸法検査で済ませてある（→ `_keep_mask`）
        ref = self._keep_mask(data)
        if ref == old_ref:
            # **同じ内容のマスクは同じ参照になる**（`assets/` は内容ハッシュが
            # 名前 → `assets.ref_for`）。作品は1文字も変わらないので、履歴にも
            # 積まないし、呼ぶ側には「変わらなかった」と答える。
            # ここで True を返していた頃は、既に消えている所を押しても
            # 「消しました」と出ていた（2026-09-05 発見）
            return False
        with self.edit_page(label) as page:
            target = page.find(image_id)
            if isinstance(target, ImageObject):
                target.mask_asset = ref
        if old_ref:
            self.forget_if_unused(old_ref)
        return True

    def clear_image_mask(self, image_id: str) -> bool:
        """切り抜きを外して元の絵に戻す。外したら True。

        **実体（assets/）は消さない。** Undo で戻せる操作なので、ここで
        消すと戻したときに切り抜きだけが失われる（ラフを外すのと同じ扱い）。
        """
        image = self.page.find(image_id)
        if not isinstance(image, ImageObject) or not image.mask_asset:
            return False

        old_ref = image.mask_asset
        with self.edit_page("切り抜きを外す") as page:
            target = page.find(image_id)
            if isinstance(target, ImageObject):
                target.mask_asset = ""
        self.forget_if_unused(old_ref)
        return True

    def region_mask_at(self, image: ImageObject, seed: tuple[int, int]):
        """その画像の、指した1点を含む区画（→ `manga_layout.wand`）。

        **原寸に対して選ぶ。** マスクは元画像と同じ寸法でなければ適用できない
        ので、画面用の縮小版（`image_cache`）は使えない。

        **使えない絵では None を返す**（→ `wand_gray`）。
        """
        gray = self.wand_gray(image.asset)
        if gray is None:
            return None
        return select_in(gray, seed, tolerance=self.wand_tolerance)

    def wand_gray(self, ref: str) -> GrayImage | None:
        """濃淡に直した1枚。**押している絵1つぶんだけ覚える。** 使えない絵では None。

        **続けて押すのがこの道具の使い方**（→ 要件定義 10.3「続けて押せば
        足せる」）なので、押すたびに PNG を展開して濃淡に直すと、2回目から
        まるごと無駄になる。2048×2048 で展開 33ms・濃淡 45ms（2026-09-06 実測）。

        **覚えるのは1枚だけ。** 入れ物にすると手放し忘れが増える
        （→ `BakedCache` の注記）。参照は内容ハッシュ（→ `assets.ref_for`）
        なので、**参照が同じなら中身も同じ**——古い絵を使い回す心配が無い。

        **使えない絵では None を返す。** 実体が無い場合だけでなく、**実体は
        あるが展開できない**場合も同じ（→ `has_asset`「無い」と「開けない」を
        分けない）。ここで例外を素通しすると、押した瞬間の処理から漏れて
        コンソールへ落ちる——**アプリは落ちないので**（PySide6 は traceback を
        出して先へ進む）、`run.bat` から起動した利用者には**押したのに何も
        起きない**だけが残る。描画側（`safe_masked_preview`）は同じ例外を
        握って描き進めているので、こちらだけ素通しにする理由が無い
        （2026-09-05 発見）。
        """
        if self.wand_scan is not None and self.wand_scan[0] == ref:
            return self.wand_scan[1]
        data = self.read_asset(ref)
        if data is None:
            return None
        try:
            full = decode(data)
        except AssetError:
            return None
        gray = to_gray(full)
        self.wand_scan = (ref, gray)
        return gray

    def forget_wand_gray(self) -> None:
        """覚えている濃淡の1枚を手放す。**原寸ぶん（4MB前後）を抱え続けない。**"""
        self.wand_scan = None

    def erase_region_at(
        self, image_id: str, seed: tuple[int, int], *, keep_only: bool = False
    ) -> bool:
        """指した区画を消す。消したら True（→ 要件定義 10.3）。

        **1回押すと1手。** 選んでから確かめて確定する、という段取りを置かない。
        違えば Undo で戻し、続けて押せば足せる——**戻せる操作なら、確認より
        やり直しのほうが手数が少ない**（本人の判断 2026-08-27）。

        `keep_only` なら逆に「そこだけ残す」。背景が何区画にも割れている絵で、
        消す側を何度も押すより速い。

        既に切り抜いてある絵では、**今のマスクから引く**（掛け直しではない）。
        押すたびに前回の結果が消えると、区画ごとに消していけない。
        """
        image = self.page.find(image_id)
        if not isinstance(image, ImageObject):
            return False

        chosen = self.region_mask_at(image, seed)
        if chosen is None:
            # **「無い」と「壊れている」を分けない**（→ `has_asset`、
            # 点検の「使えない画像」と同じ言い分け）。どちらでも切り抜けない
            self.message.emit("この絵は使えません（実体が無いか、壊れています）")
            return False
        if chosen.empty:
            return False

        # **記録と実物の食い違いは、ここで止める。** `src_px`（project.json に
        # 書いてある寸法）と実体の寸法がずれていると、この先の組み合わせが
        # `MaskSizeError` を投げ、押した瞬間の処理から漏れてコンソールへ落ちる。
        # **アプリは落ちないので**、利用者には「押しても何も起きない」だけが
        # 残る（→ `region_mask_at` と同じ形。2026-09-05 発見）
        actual_px = size_px(chosen.mask)
        if actual_px != image.src_px:
            self.message.emit(
                f"絵の大きさが記録と違うため切り抜けません"
                f"（実物 {actual_px[0]:,} × {actual_px[1]:,}、"
                f"記録 {image.src_px[0]:,} × {image.src_px[1]:,} 画素）。"
                "絵を差し替えると直ります"
            )
            return False

        current = self.image_mask_or_full(image)
        if current is None:
            return False
        updated = (
            intersected(current, chosen.mask)
            if keep_only
            else removed(current, chosen.mask)
        )

        label, done, unchanged = WAND_KEEP_WORDS if keep_only else WAND_ERASE_WORDS
        if not self.apply_image_mask(image_id, to_png_bytes(updated), label=label):
            # **変わっていないなら、変わったとは言わない。** 既に消えている所を
            # 押したときで、履歴にも積まれていない（→ `apply_image_mask`）
            self.message.emit(unchanged)
            return False

        if chosen.leaked and not keep_only:
            self.message.emit(
                f"{done}（絵の {chosen.ratio:.0%}）。線に隙間があるかもしれません"
            )
        else:
            self.message.emit(f"{done}（押した区画は絵の {chosen.ratio:.0%}）")
        return True

    def mask_hides(self, image: ImageObject, seed: tuple[int, int]) -> bool:
        """その絵の、その画素が切り抜きで消えているか（→ 要件定義 10.3）。

        **効いていないマスクは「消えていない」と答える。** 実体が欠けている・
        壊れている・寸法が違うのどれでも、画面には切り抜き前の絵が出ている
        （→ `_masked_preview`、`image_masks.safe_masked_preview`）。ここだけ
        「消えている」と答えると、**見えている絵が押せない**という食い違いが出る。

        中間の濃さは消えた扱いにしない。透けているだけの所を素通りされると、
        押した絵と消える絵が食い違う（いま作れるのは 0 と 255 だけ → `wand`）。
        """
        if not image.mask_asset:
            return False
        data = self.read_asset(image.mask_asset)
        if data is None:
            return False
        try:
            mask = decode_mask(data)
        except AssetError:
            return False
        x, y = seed
        if size_px(mask) != image.src_px or not (
            0 <= x < mask.width() and 0 <= y < mask.height()
        ):
            return False
        return mask.pixelColor(x, y).value() == 0

    def image_mask_or_full(self, image: ImageObject):
        """その画像の今のマスク。掛かっていなければ**全面が残った**マスク。

        **無い状態を「全部残す」に読み替える。** こうすると、1枚目を押すときと
        2枚目以降を押すときで処理が分かれない。

        **効いていないマスクも「無い」に読み替える。** 壊れているときだけで
        なく、寸法が記録と違うときも同じ（→ `mask_hides`、
        `image_masks.safe_masked_preview`）。画面には切り抜き前の絵が出て
        いるので、引き算の元にすると**見えている絵と食い違う**うえ、そこで
        大きさが合わずに例外になる。
        """
        px = image.src_px
        if image.mask_asset:
            data = self.read_asset(image.mask_asset)
            if data is not None:
                try:
                    mask = decode_mask(data)
                except AssetError:
                    pass  # 壊れている。全面から引き直す（描画と同じ考え方）
                else:
                    if size_px(mask) == px:
                        return mask
        full = QImage(px[0], px[1], QImage.Format.Format_Grayscale8)
        if full.isNull():
            return None
        full.fill(255)
        return full

    def step_wand_tolerance(self, steps: int) -> bool:
        """許容差を増減する。**端で止める。** 変わったら True。

        トーンの増減（→ `_step_tone`）と同じ流儀。数字そのものは覚えなくて
        よいように、状態表示に段を出す。
        """
        value = max(
            WAND_TOLERANCE_MIN,
            min(WAND_TOLERANCE_MAX, self.wand_tolerance + steps * WAND_TOLERANCE_STEP),
        )
        if value == self.wand_tolerance:
            return False
        self.wand_tolerance = value
        self.message.emit(f"切り抜きの許容差: {value}")
        return True

    # -- ラフ（下敷き） ----------------------------------------------------

    def rough_preview(self, ref: str, faded: bool) -> Preview | None:
        """ラフを描くための1枚。無い・壊れているときは None。

        **青く染めるかどうかで入れ物を分ける。** 染めていないほうは普通の
        画像と同じものなので、画像用の入れ物をそのまま使い回せる。
        """
        cache = self.rough_cache if faded else self.image_cache
        return cache.get(ref, lambda: self.read_asset(ref))

    def place_rough(self, data: bytes) -> PageRough:
        """表示中のページにラフを敷く（→ 要件定義 6.23）。

        最初は**ページに収まる大きさ**（全体が見える）で置く。写真の縦横比が
        用紙と違っても切れないので、まず全体を見てから調整できる。
        既に敷いてあれば置き換える（1ページに1枚）。
        """
        ref, px = self.import_bytes(data)
        rect = contain_rect_in(Rect(0.0, 0.0, self.page.size.w, self.page.size.h), px)
        rough = PageRough(asset=ref, rect=rect, src_px=px)
        with self.edit_page("ラフの読み込み") as page:
            page.rough = rough
        return rough

    def remove_rough(self) -> None:
        """表示中のページのラフを外す。**実体（assets/）は消さない。**

        Undo で戻せる操作なので、ここで実体まで消すと戻したときに×印だけが
        残る。使われなくなった実体は「未使用ファイルを整理」が拾う（→ 5章）。
        """
        with self.edit_page("ラフを外す") as page:
            page.rough = None

    def set_rough_faded(self, faded: bool) -> None:
        """ラフを青く淡くするか、元の絵のまま出すかを切り替える。"""
        label = "ラフを青く淡く" if faded else "ラフを元の色に"
        with self.edit_page(label) as page:
            if page.rough is not None:
                page.rough = dataclasses.replace(page.rough, faded=faded)

    def set_rough_rect(self, rect: Rect, label: str) -> None:
        """ラフの位置・大きさを差し替える。1回のドラッグで1手。"""
        with self.edit_page(label) as page:
            if page.rough is not None:
                page.rough = dataclasses.replace(page.rough, rect=rect)
