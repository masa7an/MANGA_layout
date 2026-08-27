"""画像の暗い部分をトーン（斜線・灰色・白）に置き換える（要件定義 6.27）。

**`images.py` と同じく Qt に依存する非 UI 層。** 画素を触るので `QImage`
から離れられない。分けてあるのは、`images.py` が「展開して持つ」係で、
こちらが「絵を作り替える」係だから。

外から使うのは `apply_tone`（焼いた1枚）と `tone_pieces`（PSD 用に3枚へ
分けたもの）。中は4段で、**どの段も画素を1つずつ
Python で触らない**——`bytes.translate` と `QImage.scaled` に任せる
（2048×2048 で合計 40ms 程度。要件定義 10.1 の実測）。

    1. 白地に載せてから灰色にする   … 透明を「白」に倒す
    2. しきい値で切る               … `bytes.translate`
    3. 細いものを落とす             … 縮小 → 切り直し → 拡大
    4. 矩形で絞る                   … `setClipRect`

**依存パッケージは1つも増やさない。** numpy も Pillow も OpenCV も要らない
ことは着手前に実測してある（要件定義 10.1）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from .geometry import Rect
from .model import (
    TONE_KIND_GRAY,
    TONE_KIND_STRIPES,
    TONE_KIND_WHITE,
    Tone,
)

# 種類の日本語名。**メニューの項目名と状態表示で同じ名前を使う**ので、
# 1か所に持つ（畳んだ親の名前を1か所に持つのと同じ理由 → 要件定義 6.27）
KIND_LABELS = {
    TONE_KIND_STRIPES: "斜線",
    TONE_KIND_GRAY: "灰色",
    TONE_KIND_WHITE: "白抜き",
}

# 斜線の色。白い紙の上の黒い線で、色は選べない。
# **集中線・流線と違い白は用意しない**——ここは黒ベタの置き換えなので、
# 白い線にすると「黒を白で塗る」ことになって元の絵の意味が消える
TONE_INK = QColor("#000000")
# 置き換えた先の地の色。黒ベタが「白地に黒い斜線」になる
TONE_PAPER = QColor("#FFFFFF")

# 細いものを落とすとき、縮めたあとどれだけ濃く残っていれば
# 「太い」と認めるか（0〜255）。200 は「8割方が黒」の意味。
#
# **設定にしていない。** `thin`（どの太さから細いと見るか）で足りており、
# 2つあると片方を動かしたときにもう片方の意味が変わって収拾が付かない
_KEEP = 200


@dataclass(frozen=True)
class ToneSettings:
    """トーンを入れるときの出発点。

    **入れた時点で画像の側へ焼き付ける。** あとからここを変えても、
    既に入っているトーンは変わらない（集中線・流線と同じ → 要件定義
    6.16、6.26）。
    """

    # ここより暗い画素をトーンにする（0〜255）。試作で 30 が良かった
    threshold: int = 30
    # 斜線の向き（度）。45 が右上がり
    angle: float = 45.0
    # 斜線の間隔。**画像の短辺に対する割合。**
    # px で持つと、画面用の縮小版と原寸で細かさが変わる（要件定義 10.1）
    pitch: float = 0.008
    # 線の太さを間隔に対する割合で。これが濃さになる
    density: float = 0.35
    # これより細いものはトーンにしない。**画像の短辺に対する割合。**
    # 0.002 は 2048px で 4px 相当で、試作で線画が守れた値
    thin: float = 0.002
    # 見た目（`model.TONE_KINDS`）。**入れた直後は斜線。**
    # 灰色・白抜きはクリスタで貼り直す前提の絵なので、出発点にはしない
    kind: str = TONE_KIND_STRIPES


DEFAULT_TONE_SETTINGS = ToneSettings()

# メニューで動かせる範囲。
#
# **保存形式として弾く範囲（`Tone.from_dict`）とは別もの。** あちらは
# 「読んでよい値か」で、こちらは「押し続けたときどこで止めるか」
# （集中線・流線と同じ線引き → `focus.py`、`flow.py`）
THRESHOLD_MIN = 4
THRESHOLD_MAX = 200
PITCH_MIN = 0.002
PITCH_MAX = 0.05
DENSITY_MIN = 0.05
DENSITY_MAX = 0.9
THIN_MIN = 0.0
THIN_MAX = 0.02

# メニューの1回ぶん
THRESHOLD_STEP = 10
PITCH_STEP = 0.002
DENSITY_STEP = 0.05
THIN_STEP = 0.001


# 斜線の向きをメニューで回すときの1回ぶん（度）。
# **つまみは作らない**（→ 要件定義 10.1。矩形のつまみだけで手一杯なので、
# 向きまで掴めるようにすると×と丸が同じ場所に並ぶ）
ANGLE_STEP = 15.0


def default_tone() -> Tone:
    """設定の値でトーンを1つ作る。範囲は画像全体（絞らない）。"""
    s = DEFAULT_TONE_SETTINGS
    return Tone(
        threshold=s.threshold,
        angle=s.angle,
        pitch=s.pitch,
        density=s.density,
        thin=s.thin,
        area=None,
        kind=s.kind,
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def stepped_threshold(threshold: int, steps: int) -> int:
    """どこまでを黒と見るかを増減する。範囲の端で止める。"""
    return int(_clamp(threshold + steps * THRESHOLD_STEP, THRESHOLD_MIN, THRESHOLD_MAX))


def stepped_pitch(pitch: float, steps: int) -> float:
    """斜線の間隔を増減する。範囲の端で止める。"""
    return _clamp(pitch + steps * PITCH_STEP, PITCH_MIN, PITCH_MAX)


def stepped_density(density: float, steps: int) -> float:
    """線の太さ＝濃さを増減する。範囲の端で止める。"""
    return _clamp(density + steps * DENSITY_STEP, DENSITY_MIN, DENSITY_MAX)


def stepped_thin(thin: float, steps: int) -> float:
    """どこまでを細いと見るかを増減する。範囲の端で止める。

    下限は 0（＝何も落とさない）。線画を持たない絵ではここを 0 にしたほうが
    輪郭が素直に出る。
    """
    return _clamp(thin + steps * THIN_STEP, THIN_MIN, THIN_MAX)


# -- 何段目か -------------------------------------------------------------

# 増減できる4つの値の、範囲と1回ぶんを1つの表にまとめる。
# **メニューに「今 何段目か」を出すため**（→ 要件定義 6.27）。
_RANGES = {
    "threshold": (THRESHOLD_MIN, THRESHOLD_MAX, THRESHOLD_STEP),
    "pitch": (PITCH_MIN, PITCH_MAX, PITCH_STEP),
    "density": (DENSITY_MIN, DENSITY_MAX, DENSITY_STEP),
    "thin": (THIN_MIN, THIN_MAX, THIN_STEP),
}


def level_max(field: str) -> int:
    """その値を端から端まで押したときの段数。"""
    low, high, step = _RANGES[field]
    return round((high - low) / step)


def level_of(field: str, value: float) -> int:
    """今の値が何段目か。

    **押した回数を数えるのではなく、値から逆算する。** 数えると、Undo で
    戻したとき・別の絵に移ったとき・保存して開き直したときに、数字だけが
    実際の値とずれる（入れた直後が既に 0 段目でないものもある。`thin` の
    既定 0.002 は 2 段目 → 要件定義 6.27）。

    手で書き換えた `project.json` のように**範囲の外**の値が来ても、
    0〜上限に収めて返す（表示のためのものなので、弾いて止めるより丸める）。
    """
    low, _high, step = _RANGES[field]
    return max(0, min(level_max(field), round((value - low) / step)))


def level_label(field: str, value: float) -> str:
    """メニューと状態表示に出す「2/20」の形。"""
    return f"{level_of(field, value)}/{level_max(field)}"


# -- 中の4段 --------------------------------------------------------------


def _flatten_on_white(image: QImage) -> QImage:
    """白い紙の上に載せた1枚。**透明を「白」に倒すためだけにやる。**

    `Format_Grayscale8` への変換はアルファを捨てるので、透明な部分の色が
    そのまま出る。背景が「透明かつ白」なら白として読まれて実害が無いが、
    **「透明かつ黒」で保存された画像では背景一面がトーンになる**。
    保存の仕方で結果が変わるのは事故のもとなので、先に倒しておく。
    """
    out = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(TONE_PAPER)
    painter = QPainter(out)
    painter.drawImage(0, 0, image)
    painter.end()
    return out


def _raw_of(image: QImage) -> tuple[bytes, int, int, int]:
    """**変換せずに**生バイト列を取り出す。`Format_Grayscale8` 専用。

    ここを `convertToFormat` にすると**マスクの意味が黙って壊れる**。
    `Format_Alpha8` を `Format_Grayscale8` へ変換すると全画素 255 になり、
    マスクが「画像全部」を指す（→ [PySide6の落とし穴.md](../PySide6の落とし穴.md) の 3）。
    """
    if image.format() != QImage.Format.Format_Grayscale8:
        raise ValueError(f"Grayscale8 ではありません（{image.format()}）")
    return (bytes(image.constBits()), image.width(), image.height(), image.bytesPerLine())


def _gray_image(raw: bytes, w: int, h: int, bpl: int) -> QImage:
    # `QImage` は渡したバイト列を**参照したまま持つ**ので、複製して
    # Python 側の寿命から切り離す。しないと解放後の領域を読む
    return QImage(raw, w, h, bpl, QImage.Format.Format_Grayscale8).copy()


def _cut(raw: bytes, keep_at_or_below: int | None = None, keep_at_or_above: int | None = None) -> bytes:
    """256 個の対応表で 0 / 255 に切り分ける。

    `bytes.translate` は C 側の1ループなので、419万画素で 4.6ms しか
    かからない（Python の for 文だと 205ms → 要件定義 10.1）。
    """
    if keep_at_or_below is not None:
        table = bytes(255 if i <= keep_at_or_below else 0 for i in range(256))
    else:
        assert keep_at_or_above is not None
        table = bytes(255 if i >= keep_at_or_above else 0 for i in range(256))
    return raw.translate(table)


def _drop_thin(mask: QImage, k: int) -> QImage:
    """細いものをマスクから落とす（収縮 → 膨張のかわり）。

    **専用の道具は使わない。** 縮小（`SmoothTransformation`）は周りの画素を
    混ぜるので、細い線は薄まり、大きなベタは濃いまま残る。そこをもう一度
    切り直して拡大すれば、収縮 → 膨張と同じ結果になる。

    `k` が**どこまでを細いと見るかの物差し**。4 なら、4px 未満の線が落ちる。
    """
    w, h = mask.width(), mask.height()
    small = mask.scaled(
        QSize(max(1, w // k), max(1, h // k)),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    raw, sw, sh, sbpl = _raw_of(small)
    cut = _gray_image(_cut(raw, keep_at_or_above=_KEEP), sw, sh, sbpl)
    return cut.scaled(
        QSize(w, h),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def area_px(area: Rect, w: int, h: int) -> Rect:
    """割合で持っている矩形を、画像の画素に直す。"""
    return Rect(area.x * w, area.y * h, area.w * w, area.h * h)


def _clip(mask: QImage, area: Rect) -> QImage:
    """矩形の外をマスクから外す（要件定義 10.1「矩形で範囲を絞る」）。

    **縁はぼかさない。** ぼかすと設定が1つ増えるうえ、「ここで切った」と
    分かるほうがネームでは扱いやすい。
    """
    box = area_px(area, mask.width(), mask.height())
    out = QImage(mask.size(), QImage.Format.Format_Grayscale8)
    out.fill(0)
    painter = QPainter(out)
    painter.setClipRect(round(box.x), round(box.y), round(box.w), round(box.h))
    painter.drawImage(0, 0, mask)
    painter.end()
    return out


def build_mask(image: QImage, tone: Tone) -> QImage:
    """トーンにする所を 255 にした1枚（`Format_Grayscale8`）。

    分けてあるのは、テストが「どこが選ばれたか」だけを見られるようにする
    ため。斜線の見た目は目で決めるもので、数では確かめられない。
    """
    gray = _flatten_on_white(image).convertToFormat(QImage.Format.Format_Grayscale8)
    raw, w, h, bpl = _raw_of(gray)
    mask = _gray_image(_cut(raw, keep_at_or_below=tone.threshold), w, h, bpl)

    short = min(w, h)
    k = round(short * tone.thin)
    if k >= 2:
        mask = _drop_thin(mask, k)
    if tone.area is not None:
        mask = _clip(mask, tone.area)
    return mask


def _stripes(size: QSize, tone: Tone) -> QImage:
    """白地に斜線を引いた1枚。画像と同じ大きさ。

    **タイルを敷き詰めるのではなく、直接引く。** 敷き詰めると、向きが
    45 度以外のときに継ぎ目が出る。線は多くても数百本で、引く手間は
    画素を触るのに比べれば無いに等しい。
    """
    w, h = size.width(), size.height()
    out = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(TONE_PAPER)

    pitch = max(1.0, min(w, h) * tone.pitch)
    width = max(1.0, pitch * tone.density)
    # 回した先でも端まで届くよう、対角線ぶんの正方形を塗るつもりで引く
    reach = math.hypot(w, h) / 2.0 + pitch

    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(w / 2.0, h / 2.0)
    painter.rotate(tone.angle)
    painter.setPen(QPen(TONE_INK, width))
    steps = int(reach / pitch) + 1
    for i in range(-steps, steps + 1):
        y = i * pitch
        painter.drawLine(-reach, y, reach, y)
    painter.end()
    return out


def _gray_of(density: float) -> QColor:
    """濃さから灰色を作る（要件定義 6.27）。

    **`density` を斜線と共用する。** 斜線では「線の太さ ÷ 間隔」がそのまま
    黒の占める割合になっているので、同じ値を灰色の濃さとして読み替えると
    **種類を切り替えても濃さの見た目が揃う**（0.35 なら 35% の黒）。

    項目を別に持つ手もあったが、そうすると「濃く」を押したときにどちらが
    動くかを種類ごとに覚えることになり、メニューにも2組並ぶ。
    """
    level = round(255 * (1.0 - _clamp(density, 0.0, 1.0)))
    return QColor(level, level, level)


def _pattern(size: QSize, tone: Tone) -> QImage:
    """置き換えた先に敷く1枚。画像と同じ大きさ。

    **どの種類でも「1枚敷いて、マスクで抜く」形は変わらない**（→ `apply_tone`）。
    違うのはここで何を描くかだけなので、種類が増えても合成の側は触らずに済む。
    """
    if tone.kind == TONE_KIND_STRIPES:
        return _stripes(size, tone)
    out = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    # 白抜きは「紙の色で塗り潰す」＝濃さ 0 の灰色にあたるが、`density` の
    # 下限（0.05）が 5% の灰なので**別の種類として持つ**。クリスタで
    # トーンを貼る前提なら、わずかな灰色も網点の隙間から見えてしまう
    out.fill(TONE_PAPER if tone.kind == TONE_KIND_WHITE else _gray_of(tone.density))
    return out


def as_alpha(mask: QImage) -> QImage:
    """マスクの明るさを透明度として読み替えた1枚。

    **切り抜き（→ 要件定義 10.3）もここを通る。** 明るさを透明度として
    読む所を2つ持つと、片方だけが下の落とし穴に落ちる。

    **`convertToFormat` でこれをやってはいけない。** `Format_Alpha8` へ
    変換すると全画素 255 になり、マスクが「画像全部」を指す。生バイト列を
    そのまま持ち直す（1画素1バイトで並びが同じ）→ `_raw_of` の注記。
    """
    raw, w, h, bpl = _raw_of(mask)
    return QImage(raw, w, h, bpl, QImage.Format.Format_Alpha8).copy()


def _cut_out(layer: QImage, mask: QImage) -> QImage:
    """`layer` をマスクの形に抜く（外を透明にする）。渡した1枚を書き換える。"""
    painter = QPainter(layer)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, as_alpha(mask))
    painter.end()
    return layer


def _solid(size: QSize, color: QColor) -> QImage:
    out = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(color)
    return out


def _fully_masked(mask: QImage) -> QImage:
    """**完全に選ばれている所だけ**を残したマスク。縁の中間の濃さを落とす。

    細さで落とす段（`_drop_thin`）が拡大を通るので、マスクの縁は 0 と 255 の
    あいだの値になる。**そこへ白ベタを敷くと、重ねた結果が焼き込んだ1枚と
    食い違う**——縁では「白を敷いてからトーンを敷く」と2回混ざるのに対し、
    焼き込んだほうは1回しか混ざらない（→ 要件定義 6.28）。

    縁を落とせば白ベタは中間の濃さを持たなくなり、重ねた結果が完全に一致
    する。落ちるのは絵の縁 1〜2px ぶんで、そこは元の絵がそのまま残る。
    """
    raw, w, h, bpl = _raw_of(mask)
    return _gray_image(_cut(raw, keep_at_or_above=255), w, h, bpl)


@dataclass(frozen=True)
class TonePieces:
    """トーンを PSD のレイヤー3枚に分けたもの（→ 要件定義 6.28）。

    **3枚とも1回のマスクから作る。** 焼くのは1枚 40ms 程度なので、別々に
    呼ぶと同じ計算を3回することになる。

    重ねる順は `fill` → `pattern`（`area` は非表示の目印なのでどこでもよい）。
    **等倍で重ねれば `apply_tone` が焼いた1枚と一致する**（`fill` が縁を
    持たないため → `_fully_masked`）。縮めて描く書き出しでは、絵とトーンが
    別々に縮むぶん**境目 1px だけずれる**（→ 要件定義 6.28）。
    """

    # トーン範囲。黒いシルエットで、クリスタの「レイヤーから選択範囲」に使う
    area: QImage
    # 白ベタ。**元の絵の黒ベタを隠すためだけにある。** これが無いと、
    # 利用者がトーンを貼り替えたときに下の黒ベタが網点の隙間から透ける
    fill: QImage
    # 敷いたトーンそのもの。**利用者はこれを消して自分のトーンに差し替える**
    pattern: QImage


def tone_pieces(image: QImage, tone: Tone) -> TonePieces:
    """トーンを3枚に分ける（→ `TonePieces`、要件定義 6.28）。

    **`apply_tone` と同じ `build_mask` を通る。** 別に作ると、しきい値や
    細さを動かしたときに絵とレイヤーがずれる——ずれても画面には出ないので、
    クリスタで貼ってから気づくことになる。

    3枚とも**絵と同じ場所・同じ大きさ**で返す。クリスタで位置を合わせ直す
    手間が要らないことが、この機能の中身。
    """
    mask = build_mask(image, tone)
    size = image.size()
    return TonePieces(
        area=_cut_out(_solid(size, TONE_INK), mask),
        fill=_cut_out(_solid(size, TONE_PAPER), _fully_masked(mask)),
        pattern=_cut_out(_pattern(size, tone), mask),
    )


def apply_tone(image: QImage, tone: Tone) -> QImage:
    """暗い所をトーン（斜線・灰色・白）に置き換えた1枚を返す。元の画像は変えない。

    **元のアルファは残る。** マスクが透明な所を外している（白地に載せて
    から判定する）ので、トーンは不透明な所にしか乗らない。
    """
    layer = _cut_out(_pattern(image.size(), tone), build_mask(image, tone))

    out = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(out)
    painter.drawImage(0, 0, layer)
    painter.end()
    return out
