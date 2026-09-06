"""作品の出し入れ（開く・保存・自動保存・バックアップからの復元）と、点検の印。

**点検の印も同じ場所に置く。** 印は作品を1文字も変えないので保存の対象に
ならず、開いたり保存したりのたびに落とすものでもある（→ 要件定義 10.1）。
消す場所と保存する場所が離れていると、片方だけ直る。
"""

from __future__ import annotations

import contextlib
import pathlib

from ..assets import (
    AssetStore,
    PendingAssets,
)
from ..errors import AssetError
from ..history import History
from ..model import Project
from ..storage import (
    BackupEntry,
    list_backups,
    load_backup,
    load_project,
    save_project,
    write_autosave,
)


class FileMixin:
    """開く・保存・復元と、点検の印。**`EditorState` に混ぜて使う。**

    単体では動かない。`self.history` / `self.message` / `self.project_dir`
    などは**混ぜた先が持っている**（→ `state.py`）。
    """

    # -- 点検の印（要件定義 10.1） -------------------------------------------

    @property
    def check_marks(self) -> set[str]:
        """紫の印が付いているページの id。**作品には保存されない。**"""
        return set(self._check_marks)

    def set_check_marks(self, page_ids: set[str]) -> None:
        """点検の結果で印を付け直す。**前の結果は必ず捨てる。**

        足し込むと、直したものの印が残り続けて嘘になる。押すたびに数え直す
        （→ 要件定義 10.1）以上、印も毎回まっさらから付け直す。
        """
        if page_ids == self._check_marks:
            return
        self._check_marks = set(page_ids)
        self.check_changed.emit()

    def clear_check_marks(self) -> None:
        self.set_check_marks(set())

    # -- ファイル ----------------------------------------------------------

    def reset(self, project: Project, project_dir: pathlib.Path | None) -> None:
        """別の作品に入れ替える。履歴も作り直す。"""
        self.history = History(project)
        self.project_dir = project_dir
        self._page_index = 0
        self._selected_id = None
        # 前の作品の画像を抱えたままにしない。参照が同じでも中身は別物
        self.pending_assets = PendingAssets()
        self.image_cache.clear()
        self.rough_cache.clear()
        self.baked_cache.clear()
        self.reduced_cache.clear()
        self.forget_wand_gray()
        # 点検の印は前の作品のもの。ページの id ごと別系列になるので、
        # 残しても付きようがないが、消さないと数だけ残る（→ 要件定義 10.1）
        self.clear_check_marks()
        self.changed.emit()
        self.selection_changed.emit()
        self.page_changed.emit()

    def load(self, project_dir: pathlib.Path) -> list[str]:
        """作品を開く。読み込み時に直した内容があれば返す。"""
        project = load_project(project_dir)
        warnings = list(project.load_warnings)
        self.reset(project, project_dir)
        return warnings

    def save(self, project_dir: pathlib.Path | None = None) -> pathlib.Path:
        target = project_dir or self.project_dir
        if target is None:
            raise ValueError("保存先が決まっていません")

        # **画像の実体を先に書く。** 逆順だと、途中で落ちたときに
        # 実体の無い参照を持つ project.json が残る。この順なら、
        # 最悪でも参照されない画像が余るだけで済む
        store = AssetStore(target)
        self._carry_assets_to(store)
        self.pending_assets.flush_to(store)

        # **控えを手放すのは project.json の書き込みが終わってから。**
        # 実体は書けたのにここで例外が飛ぶと（ロック・ディスク満杯など）、
        # 保存は失敗として返る。そこで控えを先に空にしていると、続けて
        # 別の場所へ保存し直したときに実体が書かれず、参照だけが残った
        # project.json ができてしまう（2026-08-08 に発見）
        path = save_project(self.project, target)
        self.pending_assets.clear()
        self.project_dir = target
        self.history.mark_saved()
        return path

    def _carry_assets_to(self, store: AssetStore) -> None:
        """今の保存先にある画像の実体を、新しい保存先へ写す。

        「名前を付けて保存」で保存先が変わる経路のためにある。
        `pending_assets` が持っているのは**保存先が決まる前に貼った分**だけ
        なので、一度保存した作品を別名保存すると、それだけでは
        `assets/` が空のまま project.json だけが増える（＝参照が全部切れる）。

        写すのは **project.json から参照されている画像だけ。** 参照の無い
        ものまで運ぶと、「未使用ファイルを整理」で片付けたはずのものが
        別名保存のたびに戻ってくる。

        **1枚読めなくても保存は止めない。** 元の `assets/` が既に欠けている
        作品を別名保存したいことはある。そこで保存ごと失敗させると、
        欠けていない残りまで写せずに終わってしまう。欠けたままの参照は
        「ファイル → 抜けチェック」で見つけられる。
        """
        if self.project_dir is None:
            return

        source = AssetStore(self.project_dir)
        for ref in sorted(self.project.referenced_assets()):
            # 名前が中身のハッシュなので、同名が既にあれば中身も同じ。
            # 保存先が変わっていない普段の保存は、ここで全部素通りする
            if store.exists(ref) or ref in self.pending_assets:
                continue
            try:
                store.add_bytes(source.read(ref))
            except AssetError:
                # 読めない・画像として通らない1枚を飛ばすだけ。書き込みに
                # 失敗した場合（OSError）はここでは捕まえず、保存を止める。
                # 実体を書けないまま project.json を書くと参照が切れる
                continue

    def autosave(self) -> pathlib.Path | None:
        """作業中の内容を `backup/` へ退避する。書いたらそのパスを返す。

        タイマーから一定間隔で呼ばれる（要件定義 6.6）。**次の2つの場合は
        何もせず None を返す。**

        **保存先が決まっていない**（一度も保存していない作品）。退避先の
        フォルダが無いうえ、その状態で貼った画像は**まだディスクに無い**
        （→ `import_bytes` の `pending_assets`）ため、JSON だけ書いても
        参照先の無い退避になる。保存先が決まっていれば画像は貼った時点で
        `assets/` に入るので、この問題は起きない。

        **前回の退避から変化が無い。** 判定は保存形式そのものの比較なので
        （→ `History.is_autosave_pending`）、「変えて元に戻した」場合も
        正しく何もしない。

        **保存した扱いにはしない。** 本体（project.json）を書き換えていない
        以上、未保存の印は利用者が保存するまで残す。
        """
        if self.project_dir is None:
            return None
        if not self.history.is_autosave_pending:
            return None

        path = write_autosave(self.project, self.project_dir)
        self.history.mark_autosaved()
        return path

    # -- バックアップからの復元（要件定義 6.6） ------------------------------

    def backups(self) -> list[BackupEntry]:
        """戻せる世代の一覧。保存先が決まっていなければ空。"""
        if self.project_dir is None:
            return []
        return list_backups(self.project_dir)

    def restore_backup(self, path: pathlib.Path) -> list[str]:
        """`backup/` の世代1つを画面へ戻す。直した箇所があれば返す。

        **`project.json` は書き換えない。** 戻した結果は履歴の上に1手として
        乗るだけなので、Undo 1回で復元前の作業に戻れる。ディスクへ確定する
        のは利用者が保存を押したときで、そのとき今の `project.json` は
        自動で `backup/project.1.json` へ退避される（→ `_rotate_backups`）。

        **順序が要**。選んだ世代を**先に読み切ってから**、今の内容を退避する。
        逆にすると `write_autosave` が世代を1つずつ繰り下げるので、
        `autosave.2.json` を選んだつもりが別の中身になり、`autosave.3.json`
        に至っては最古として消えたあとを読むことになる。

        **`reset()` は通さない。** あちらは `History` を作り直すので、
        通した瞬間に「元に戻す」で戻れなくなる。
        """
        project = load_backup(path)
        warnings = list(project.load_warnings)

        # 今の内容を1つ退避しておく。戻す操作自体で今の作業を失わせない
        # （要件定義 10.1）。**読み終えた後**に行う（上の「順序が要」）
        with contextlib.suppress(OSError):
            self.autosave()

        if not self.history.replace(project, "バックアップから復元"):
            return warnings

        self._after_restore()
        return warnings

    def _after_restore(self) -> None:
        """復元で入れ替わった後の後始末。

        選択は**必ず解除する**。戻した作品の ID は今選んでいるものと
        別系列なので、残すと「選んでいるはずのものが見当たらない」状態に
        なる。ページ番号を丸めるのは Undo と同じ理由（→ `_after_history_move`）。

        **画像の覚え書き（`image_cache`）は捨てない。** `reset()` は捨てるが、
        あちらは別の作品へ移る操作。ここでは同じ作品フォルダの中に留まり、
        参照は中身から作った名前（SHA1）なので、同じ参照なら中身も必ず同じ。
        捨てると復元のたびに全部の画像を展開し直すことになる（Undo が
        捨てていないのと同じ理由）。
        """
        self._selected_id = None
        self._page_index = max(0, min(self._page_index, self.page_count - 1))
        self._leave_rough_tool_if_gone()
        # `_after_history_move` と同じ理由（→ そちら）。選択を必ず外すので
        # トーンの範囲を直す道具も持ったままにしない（2026-08-08 発見。
        # 以前はここだけ呼び忘れていた）
        self._leave_tone_tool_if_gone()
        self.changed.emit()
        self.selection_changed.emit()
        self.page_changed.emit()

    @property
    def is_dirty(self) -> bool:
        return self.history.is_dirty
