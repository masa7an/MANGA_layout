"""Undo / Redo（要件定義 6.8）。

**スナップショット方式**。1手ごとにプロジェクト全体の状態を保存形式に変換して
積む。操作ごとに「何をどう戻すか」を書く方式（コマンド方式）に比べて、
操作を1つ足すたびに元に戻す処理も書く必要がなく、書き忘れによる
「戻したはずなのに戻っていない」が起きない。

状態は保存形式（`Project.to_dict()`）を経由して持つ。つまり
**保存されない項目は Undo でも戻らない**。この2つを意図的に一致させてある
ので、項目を足したときに `to_dict()` への追加を忘れれば Undo のテストでも
落ち、抜けに気づける。

`History` がプロジェクトの唯一の持ち主になる。Undo すると `Project` の実体は
別のものへ差し替わるため、**画面側は必ず `history.project` を読み直すこと**。
古い `Page` や `Panel` を掴んだままにすると、戻した後の編集が反映されない。
"""

from __future__ import annotations

import contextlib
import json
import zlib
from collections.abc import Iterator
from dataclasses import dataclass

from .model import Project

# 要件定義 6.8 のとおり 50 手
DEFAULT_LIMIT = 50

# zlib の圧縮レベル。1 は「速さ優先」。
# 30ページ規模でも 1手あたり数ミリ秒で、操作の体感には出ない
_COMPRESS_LEVEL = 1


def _encode(project: Project) -> bytes:
    """現在の状態を、履歴に積める形へ変換する。

    辞書のまま持つと 30ページ規模で 1手あたり数 MB になり、50手で
    数百 MB に達する。JSON 文字列にしてから圧縮すると 2 桁小さくなる。
    """
    text = json.dumps(project.to_dict(), ensure_ascii=False, separators=(",", ":"))
    return zlib.compress(text.encode("utf-8"), _COMPRESS_LEVEL)


def _decode(blob: bytes) -> Project:
    return Project.from_dict(json.loads(zlib.decompress(blob).decode("utf-8")))


@dataclass(frozen=True)
class Step:
    """履歴の1手。`label` は「元に戻す: コマの移動」のように画面へ出す。"""

    label: str
    state: bytes


class History:
    """プロジェクトと、その編集履歴。

    使い方は次のどちらか。`edit()` のほうが安全（途中で例外が出ても
    中途半端に変更された状態が残らない）。

        with history.edit("コマの移動") as project:
            project.pages[0].move_panel(panel_id, 10.0, 0.0)

        # あるいは
        history.project.pages[0].move_panel(panel_id, 10.0, 0.0)
        history.commit("コマの移動")
    """

    def __init__(self, project: Project, limit: int = DEFAULT_LIMIT) -> None:
        if limit < 1:
            raise ValueError(f"履歴の上限は 1 以上が必要です（{limit}）")
        self._project = project
        self._limit = limit
        # 直前に確定した状態。ここからの差分があるかどうかで「変化したか」を見る
        self._baseline = _encode(project)
        self._undo: list[Step] = []
        self._redo: list[Step] = []
        self._merge_key: str | None = None
        self._saved = self._baseline
        # 自動バックアップは「保存した」印を動かせないので、印を別に持つ
        # （理由は `is_autosave_pending`）
        self._autosaved = self._baseline

    # -- 現在の状態 --------------------------------------------------------

    @property
    def project(self) -> Project:
        """今のプロジェクト。Undo で差し替わるので、毎回ここから読むこと。"""
        return self._project

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str | None:
        """次に元に戻す操作の名前。メニューの表示に使う。"""
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    @property
    def depth(self) -> int:
        """積まれている手数。"""
        return len(self._undo)

    # -- 保存状態 ----------------------------------------------------------

    @property
    def is_dirty(self) -> bool:
        """保存していない変更があるか。閉じるときの確認に使う。

        保存した地点まで Undo で戻れば、正しく「変更なし」に戻る。
        """
        return self._baseline != self._saved

    def mark_saved(self) -> None:
        """保存が完了した時点を記録する。

        自動バックアップの印も一緒に進める。保存した内容は project.json に
        そのまま入っているので、同じものを `backup/` へ写しても増えない。
        """
        self._saved = self._baseline
        self._autosaved = self._baseline

    @property
    def is_autosave_pending(self) -> bool:
        """前回の自動バックアップ以降に変化があるか。

        **`is_dirty` では代用できない。** 自動バックアップでは
        `mark_saved()` を呼べない（呼ぶと未保存の印が消え、閉じるときに
        本体が保存されないまま黙って終わる）ため、`is_dirty` は退避した
        後も真のまま残る。それを目印にすると、1文字も変えていなくても
        タイマーが回るたびに書くことになる。

        比較は保存形式そのものなので、「変えて元に戻した」場合も正しく
        「変化なし」になる（`is_dirty` と同じ仕組み）。
        """
        return self._baseline != self._autosaved

    def mark_autosaved(self) -> None:
        """自動バックアップを書いた時点を記録する。

        **`mark_saved()` は呼ばない。** 未保存の印は利用者が保存するまで
        残す（→ `is_autosave_pending`）。
        """
        self._autosaved = self._baseline

    # -- 編集 --------------------------------------------------------------

    def commit(self, label: str, *, merge_key: str | None = None) -> bool:
        """直前の確定以降の変更を、1手として確定する。

        変化が無ければ何もせず False を返す。ドラッグしたが元の位置に
        戻した場合など、履歴を無意味に埋めないため。

        `merge_key` を渡すと、直前の1手が同じ鍵で確定されていた場合に
        そこへ吸収する。セリフの入力を1文字ずつ積んで、Undo 1回で
        1文字しか戻らない状態を避けるためのもの。
        入力欄から離れたときなどに `break_merge()` で区切る。
        """
        current = _encode(self._project)
        if current == self._baseline:
            return False

        merging = (
            merge_key is not None and merge_key == self._merge_key and bool(self._undo)
        )
        if not merging:
            self._undo.append(Step(label, self._baseline))
            if len(self._undo) > self._limit:
                del self._undo[0]

        self._baseline = current
        self._merge_key = merge_key
        self._redo.clear()
        return True

    def break_merge(self) -> None:
        """まとめ扱いを打ち切る。次の `commit` は必ず新しい1手になる。"""
        self._merge_key = None

    @contextlib.contextmanager
    def edit(self, label: str, *, merge_key: str | None = None) -> Iterator[Project]:
        """1手ぶんの編集をまとめる。

        途中で例外が出た場合は直前の確定状態へ戻してから送出する。
        半分だけ適用された状態が残らないので、失敗した操作のあとでも
        安心して編集を続けられる。
        """
        try:
            yield self._project
        except Exception:
            self.rollback()
            raise
        self.commit(label, merge_key=merge_key)

    def rollback(self) -> None:
        """確定していない変更を捨てて、直前の確定状態へ戻す。履歴には積まない。"""
        self._project = _decode(self._baseline)

    def replace(self, project: Project, label: str) -> bool:
        """作品まるごとを別のものへ差し替え、**1手として積む**。

        バックアップからの復元（要件定義 6.6）で使う。**スナップショット
        方式なので、まるごとの差し替えも普通の1手と何も変わらない。**
        履歴を作り直す必要はなく、復元後の姿が今の履歴の上に乗るだけなので、
        Undo 1回で復元前の作業に戻れる。

        **作品を開くとき（`EditorState.reset`）とは別物。** あちらは
        `History` ごと作り直すので、通すと履歴が消えて戻れなくなる。

        中身が今と同じなら何もせず False を返す（`commit` と同じ約束）。
        同じ内容の世代を選んだだけで履歴が1手埋まることがない。
        """
        previous = self._project
        self._project = project
        if self.commit(label):
            return True
        self._project = previous
        return False

    # -- 元に戻す / やり直す ------------------------------------------------

    def undo(self) -> str | None:
        """1手戻す。戻した操作の名前を返す。戻せなければ None。"""
        if not self._undo:
            return None
        step = self._undo.pop()
        self._redo.append(Step(step.label, self._baseline))
        self._baseline = step.state
        self._project = _decode(step.state)
        self._merge_key = None
        return step.label

    def redo(self) -> str | None:
        """1手やり直す。やり直した操作の名前を返す。やり直せなければ None。"""
        if not self._redo:
            return None
        step = self._redo.pop()
        self._undo.append(Step(step.label, self._baseline))
        self._baseline = step.state
        self._project = _decode(step.state)
        self._merge_key = None
        return step.label

    def clear(self) -> None:
        """履歴を捨てる。別のプロジェクトを開いたときに呼ぶ。"""
        self._undo.clear()
        self._redo.clear()
        self._merge_key = None

    # -- 診断 --------------------------------------------------------------

    def memory_bytes(self) -> int:
        """履歴が抱えている状態の合計サイズ。上限の妥当性を測るのに使う。"""
        return sum(len(s.state) for s in self._undo) + sum(len(s.state) for s in self._redo)
