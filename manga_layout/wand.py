"""自動領域選択（塗りつぶし選択・マジックワンド）。

**指した1点から、似た濃さの続く範囲を広げて選ぶ。** 線画やべた塗りの絵では、
黒い線が壁になって「線で囲まれた1区画」がそのまま取れる。切り抜きたい対象を
言葉で言う必要が無いかわりに、**何を選ぶかは人が指す**。

**依存パッケージを増やさない。** OpenCV の `cv2.floodFill` を使う手もあるが、
このアプリは「numpy も Pillow も OpenCV も要らない」ことを実測して作ってある
（→ `tone.py` の冒頭）。ここでも画素を1つずつ Python で触らずに済ませる——
**行の中の連なりを探すのは `bytes.find` に任せる**（C 側の走査）ので、
Python の繰り返しは「連なりの数」ぶんしか回らない。

速さは足りている。2048×2048 の絵で背景をまるごと選んで **63〜71ms**、
1024×900 の絵で手を選んで **13ms**。うち 45ms 前後は「濃淡に直して対応表を作る」
固定費で、塗り自体はそれより小さい。**画素ごとに Python で辿る書き方では
1,220ms** だったので、19倍ほど速い。

（根拠: `select_at` を実画像に対して3回ずつ実行 / 確認日: 2026-08-27）

**その固定費は、続けて押すなら1回で済む**（→ `GrayImage`・`select_in`）。
同じ絵を押している間は濃淡に直した1枚を使い回せるように、選ぶ処理から
切り出してある。**許容差は後から掛ける**ので、許容差を変えても作り直さない。

得意・不得意がはっきりしている。**地が一様で、線が閉じていること**が前提で、
グラデーションで塗った絵では許容差をどう回しても収まらない。1画素の隙間が
あれば、その区画は外へつながる。**下見を出して人が見て気づける形が要る。**
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QImage

from .errors import MaskSizeError
from .tone import flatten_on_white

# 選んだ割合がこれを超えたら「漏れたかもしれない」と見る。
#
# **9割ではなく 0.97。** 背景をクリックして人物を切り抜く使い方では、
# 選ばれるのが画面の 84.5% になるのが普通（2048×2048 の実測）。
# ここを低くすると、正しい操作のたびに警告が出る
LEAK_RATIO = 0.97

# 許容差の既定。**線画の白地なら 1 でも足りる**（白は 255 に張り付いている）が、
# 少しだけ幅を持たせておくと、書き出しで軽く汚れた白でも1区画にまとまる
DEFAULT_TOLERANCE = 8


@dataclass(frozen=True)
class Selection:
    """選んだ範囲1つ。

    `mask` は元画像と同じ寸法の8bitグレースケール（0 か 255）で、そのまま
    `image_masks.apply_mask` に渡せる（→ 要件定義 10.3）。
    """

    mask: QImage
    # 選ばれた画素の数と、画像全体に対する割合
    count: int
    ratio: float

    @property
    def empty(self) -> bool:
        return self.count == 0

    @property
    def leaked(self) -> bool:
        """線の隙間から外へ漏れた疑いがあるか。

        **判定ではなく疑い。** 広い背景を選ぶ操作と見分けが付かないので、
        止めるのではなく画面が「線に隙間があるかもしれません」と言うために使う。
        """
        return self.ratio >= LEAK_RATIO


@dataclass(frozen=True)
class GrayImage:
    """濃淡だけにした1枚（行の詰め物を外した並び）。

    **選ぶ処理から切り出してあるのは、続けて押すときに使い回すため。**
    これを作るのが選択1回の固定費の大半で、2048×2048 で 45ms 前後
    （うち展開ぶんは別。→ `to_gray`）。**許容差は後から掛ける**ので、
    許容差を変えても作り直さなくてよい。
    """

    rows: bytes
    width: int
    height: int


def to_gray(image: QImage) -> GrayImage:
    """濃淡を「行の詰め物を外した」バイト列にする。

    **透明は白に倒す**（→ `tone.flatten_on_white`）。画面では紙の白の上に
    置かれるので、利用者が見ているとおりの濃さで判断することになる。
    """
    if image.isNull() or image.width() == 0 or image.height() == 0:
        # 空の1枚に絵を描くことはできない（`flatten_on_white` が
        # `QPainter` を通す）。**手で書き換えた project.json などで来る**
        return GrayImage(b"", max(0, image.width()), max(0, image.height()))
    gray = flatten_on_white(image).convertToFormat(QImage.Format.Format_Grayscale8)
    w, h, stride = gray.width(), gray.height(), gray.bytesPerLine()
    raw = bytes(gray.constBits())
    if stride != w:
        # 行の末尾の詰め物を落として、幅ちょうどに詰め直す
        raw = b"".join(raw[y * stride : y * stride + w] for y in range(h))
    return GrayImage(raw, w, h)


def _same_table(value: int, tolerance: int) -> bytes:
    """種の濃さ ± 許容差なら 1、それ以外は 0 にする 256 個の対応表。"""
    low, high = value - tolerance, value + tolerance
    return bytes(1 if low <= i <= high else 0 for i in range(256))


def _spread(same: bytes, w: int, h: int, seed: tuple[int, int]) -> tuple[bytearray, int]:
    """種から連なりを辿って広げる。戻り値は (選んだ所, 画素数)。

    **上下左右の4方向だけで広げる**（斜めを含めない）。斜めを含めると、
    線が斜めに交差している所の1画素の隙間をすり抜けて外へ出る。

    **線を指せば線が選ばれる。** 何を選ぶかは「指した所と似た濃さが続く範囲」
    でしかなく、線か中身かをここは知らない。

    塗るのは**その行の連なりまるごと**なので、「1画素でも選ばれている連なりは、
    全部選ばれている」が常に成り立つ。だから隣の行を見るときは、連なりの
    先頭1画素だけ確かめれば足りる。
    """
    x, y = seed
    chosen = bytearray(w * h)
    # **種そのものは必ず選ばれる。** 対応表は種の濃さから作っているので、
    # 「種が範囲の外」ということが起こらない（線を指せば線が選ばれる）
    stack = [(x, y)]
    count = 0
    while stack:
        cx, cy = stack.pop()
        base = cy * w
        if chosen[base + cx]:
            continue

        # その行の連なりの端を探す。**探すのは `bytes.find` の仕事**
        stop = same.rfind(0, base, base + cx)
        left = base if stop == -1 else stop + 1
        stop = same.find(0, base + cx, base + w)
        right = base + w - 1 if stop == -1 else stop - 1

        chosen[left : right + 1] = b"\x01" * (right - left + 1)
        count += right - left + 1

        for ny in (cy - 1, cy + 1):
            if not 0 <= ny < h:
                continue
            nbase = ny * w
            i, end = nbase + (left - base), nbase + (right - base)
            while i <= end:
                if same[i]:
                    if not chosen[i]:
                        stack.append((i - nbase, ny))
                    # この連なりの先は見なくてよい（まるごと同じ扱いになる）
                    stop = same.find(0, i, end + 1)
                    i = end + 1 if stop == -1 else stop + 1
                else:
                    stop = same.find(1, i, end + 1)
                    i = end + 1 if stop == -1 else stop
    return chosen, count


# 0/1 の並びを 0/255 のマスクへ（→ `tone._cut` と同じ手）
_TO_MASK = bytes(255 if i else 0 for i in range(256))


def _mask_image(chosen: bytes, w: int, h: int) -> QImage:
    raw = bytes(chosen).translate(_TO_MASK)
    # `QImage` は渡したバイト列を参照したまま持つので複製する（→ `tone._gray_image`）
    return QImage(raw, w, h, w, QImage.Format.Format_Grayscale8).copy()


def select_in(
    gray: GrayImage,
    seed: tuple[int, int],
    *,
    tolerance: int = DEFAULT_TOLERANCE,
) -> Selection:
    """濃淡に直した1枚から選ぶ。**同じ絵を続けて押す側はこちらを使う。**

    `seed` は元画像のピクセル座標。範囲の外を指されたら空の選択を返す
    （画面の側で押さえてはいるが、ここでも落ちないようにしておく）。
    """
    w, h, rows = gray.width, gray.height, gray.rows
    x, y = seed
    if w == 0 or h == 0 or not (0 <= x < w and 0 <= y < h):
        return Selection(mask=_mask_image(bytes(max(0, w * h)), w, h), count=0, ratio=0.0)

    same = rows.translate(_same_table(rows[y * w + x], max(0, tolerance)))
    chosen, count = _spread(same, w, h, (x, y))
    return Selection(mask=_mask_image(chosen, w, h), count=count, ratio=count / (w * h))


def select_at(
    image: QImage,
    seed: tuple[int, int],
    *,
    tolerance: int = DEFAULT_TOLERANCE,
) -> Selection:
    """指した1点から広げて選ぶ。**元画像は変えない。**

    濃淡に直す手間（→ `to_gray`）を毎回払う形。**1回きりならこちら**で、
    続けて押す側は `to_gray` の結果を持ち回って `select_in` を呼ぶ。
    """
    return select_in(to_gray(image), seed, tolerance=tolerance)


# -- 選んだ範囲どうしの組み合わせ ------------------------------------------
#
# **画素を1つずつ Python で触らない。** 0 と 255 しか入っていないので、
# バイト列をまるごと1つの整数として読めば、論理演算がそのまま画素ごとの
# 論理演算になる（4M画素で数ミリ秒）。

_INVERT = bytes(255 - i for i in range(256))


def _raw_of(mask: QImage) -> tuple[bytes, QImage]:
    gray = mask.convertToFormat(QImage.Format.Format_Grayscale8)
    return bytes(gray.constBits()), gray


def _image_like(raw: bytes, like: QImage) -> QImage:
    return QImage(
        raw, like.width(), like.height(), like.bytesPerLine(),
        QImage.Format.Format_Grayscale8,
    ).copy()


def inverted(mask: QImage) -> QImage:
    """選んだ所と選んでいない所を入れ替える。

    **切り抜きの定石はこれ。** 背景を選んでから反転すると、線で囲まれた
    中身（人物）が残る。線そのものは選ばれていないので、反転すると
    **線が中身の側に付いてくる**——輪郭が痩せないのはこのため。
    """
    raw, gray = _raw_of(mask)
    return _image_like(raw.translate(_INVERT), gray)


def _pair(a: QImage, b: QImage) -> tuple[bytes, bytes, QImage]:
    if a.size() != b.size():
        raise MaskSizeError(
            f"大きさの違う選択は組み合わせられません"
            f"（{a.width():,} × {a.height():,} と {b.width():,} × {b.height():,}）"
        )
    a_raw, a_gray = _raw_of(a)
    b_raw, _ = _raw_of(b)
    return a_raw, b_raw, a_gray


def combined(a: QImage, b: QImage) -> QImage:
    """どちらかに入っている所（足す）。"""
    a_raw, b_raw, like = _pair(a, b)
    n = len(a_raw)
    merged = int.from_bytes(a_raw, "big") | int.from_bytes(b_raw, "big")
    return _image_like(merged.to_bytes(n, "big"), like)


def removed(a: QImage, b: QImage) -> QImage:
    """`a` から `b` を取り除く（引く）。"""
    a_raw, b_raw, like = _pair(a, b)
    n = len(a_raw)
    kept = int.from_bytes(a_raw, "big") & int.from_bytes(b_raw.translate(_INVERT), "big")
    return _image_like(kept.to_bytes(n, "big"), like)


def intersected(a: QImage, b: QImage) -> QImage:
    """どちらにも入っている所だけ残す（そこだけ残す）。"""
    a_raw, b_raw, like = _pair(a, b)
    n = len(a_raw)
    kept = int.from_bytes(a_raw, "big") & int.from_bytes(b_raw, "big")
    return _image_like(kept.to_bytes(n, "big"), like)
