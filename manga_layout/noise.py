"""形のばらつきに使う擬似乱数。**Qt もモデルも知らない。**

集中線（`focus.py` → 要件定義 6.16）と流線（`flow.py` → 6.26）の両方が
使う。**2つが共有しているのはここだけ**で、線の形も長さの基準も別々に
持っている。

`focus.py` に置いたまま `flow.py` から借りることもできたが、そうすると
「流線は集中線の一部」という関係が import に現れる。集中線を消したら
流線まで動かなくなるので、どちらにも属さない場所へ出した。
"""

from __future__ import annotations

import random

# 種として受け付ける上限。JSON に読めない大きさの数字が残らない程度に取る
SEED_MAX = 1 << 31


class Noise:
    """種から 0.0〜1.0 の数を順に取り出す。**線形合同法。**

    **Python の `random` を使わない。** `random` の出す並びは処理系に
    属するもので、将来それが変われば**同じ種から違う形が出る**。
    種を保存する目的（開くたびに形が変わらない、書き出した PNG と画面が
    一致する）がそこで崩れる。数行で済むので自前で持つ
    （要件定義 6.16）。

    **種は掛け算1回では混ざらない。** 線形合同法は下位の桁の質が悪く、
    隣り合う種から始めると1歩進めても近い値のままになる（実測: 種 100 と
    101 で最初の値が 0.02 しか違わず、1本目と2本目の向きが揃って見えた）。
    シフトと掛け算を交互にかけて、1ビットの違いを全体へ散らしてから回す。
    """

    _A = 1664525
    _C = 1013904223
    _M = 1 << 32

    def __init__(self, seed: int) -> None:
        self._value = self._scramble(int(seed) % self._M)

    @classmethod
    def _scramble(cls, value: int) -> int:
        """1ビットの違いを 32 ビット全体へ散らす。"""
        mask = cls._M - 1
        value ^= value >> 16
        value = (value * 0x7FEB352D) & mask
        value ^= value >> 15
        value = (value * 0x846CA68B) & mask
        return value ^ (value >> 16)

    def unit(self) -> float:
        """0.0 以上 1.0 未満。"""
        self._value = (self._value * self._A + self._C) % self._M
        return self._value / self._M

    def signed(self) -> float:
        """-1.0 以上 1.0 未満。"""
        return self.unit() * 2.0 - 1.0


def seed_from_text(text: str) -> int:
    """文字列から種を作る。**同じ文字列なら必ず同じ値。**

    雲_フキダシのゆらぎ（→ `layout.cloud_points`）が使う。フキダシの ID を
    そのまま種にすれば、**保存する項目を増やさずに**開き直しても同じ形へ
    戻せる。

    **Python の `hash()` を使わない。** あれは実行のたびに値が変わるので
    （PYTHONHASHSEED）、開くたびにフキダシの形が変わってしまう。
    FNV-1a を数行で持つ。
    """
    value = 0x811C9DC5
    mask = (1 << 32) - 1
    for byte in text.encode("utf-8"):
        value = ((value ^ byte) * 0x01000193) & mask
    return value


def new_seed() -> int:
    """新しい形のための種。

    **ここだけは `random` を使ってよい。** 選んだ値はそのまま保存され、
    以後の形はその値だけから決まる（→ `Noise`）。並びが処理系に属して
    いても困らないのは、二度と同じ列を求めないため。
    """
    return random.randrange(SEED_MAX)
