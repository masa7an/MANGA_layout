"""次のコマの位置を、定石と照合して提案する。

**このファイルは画面もモデルも知らない。** 入るのは正規化した矩形の並びだけで、
出るのも矩形。ページとの間は `next_panel.py` が取り持つ。切り離してあるのは、
コマ割りの当たり外れを**画面を起動せずに**確かめられるようにするため
（`layout.py` を `ui/` から切り離してあるのと同じ理由）。

**コマは読み順で渡す前提**（見開きの定石が「最後のコマ」を見る）。
ここが出すのは**幾何として成立する候補だけ**で、「どれが物語に合うか」は判断しない。
座標はページ幅・高さを 1.0 とした正規化値で扱う（ページの寸法に依存させないため）。

定数に付けた実測値は、**実際の漫画ページ6枚を測ったもの**。手で置いた値と、
材料から決めた値を、コメントで区別してある。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class NBox:
    """正規化座標のコマ。x,y は左上、w,h は幅と高さ。すべて 0.0〜1.0。"""
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def mirrored(self) -> NBox:
        """左右反転。ページ幅を 1.0 としているので引き算だけで済む。"""
        return NBox(1.0 - self.right, self.y, self.w, self.h)

    def to_bbox(self, width: int, height: int) -> list[int]:
        return [round(self.x * width), round(self.y * height),
                round(self.right * width), round(self.bottom * height)]

    def rounded(self, digits: int = 3) -> NBox:
        return NBox(*(round(v, digits) for v in (self.x, self.y, self.w, self.h)))


def normalize(bboxes: Sequence[Sequence[int]], image_size: Sequence[int]) -> list[NBox]:
    """段階1の [x1,y1,x2,y2]（画素）を正規化座標へ直す。"""
    width, height = image_size
    out = []
    for x1, y1, x2, y2 in bboxes:
        out.append(NBox(x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height))
    return out


def intersection_area(a: NBox, b: NBox) -> float:
    w = min(a.right, b.right) - max(a.x, b.x)
    h = min(a.bottom, b.bottom) - max(a.y, b.y)
    return w * h if w > 0 and h > 0 else 0.0


# ページ端にこれだけ近ければ「断ち切り」とみなす。
# 実測（5ページ・コマ20個の「最も近い端までの距離」）は2つに分かれる:
#   0.000〜0.007 が13件（断ち切り）／ 0.014 以上が7件（枠のあるコマ）
# **0.01 はその隙間にある。**（確認日: 2026-09-04）
BLEED_MARGIN = 0.01


def is_bleed(box: NBox, margin: float = BLEED_MARGIN) -> bool:
    """断ち切り（ページ端まで抜けているコマ）かどうか。"""
    return (box.x <= margin or box.y <= margin
            or box.right >= 1.0 - margin or box.bottom >= 1.0 - margin)


def covered_area(boxes: Sequence[NBox]) -> float:
    """コマが覆っているページの割合。**重なりを二重に数えない。**

    定石の判定には使っていない（参考として画面に出すだけ）。
    実測した漫画ページ6枚では 66〜96%（枠の無い余白が広いページほど低い）。
    """
    if not boxes:
        return 0.0
    xs = sorted({v for b in boxes for v in (max(0.0, b.x), min(1.0, b.right))})
    ys = sorted({v for b in boxes for v in (max(0.0, b.y), min(1.0, b.bottom))})
    total = 0.0
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            cx, cy = (xs[i] + xs[i + 1]) / 2.0, (ys[j] + ys[j + 1]) / 2.0
            if any(b.x <= cx <= b.right and b.y <= cy <= b.bottom for b in boxes):
                total += (xs[i + 1] - xs[i]) * (ys[j + 1] - ys[j])
    return total


def _spread(values: Sequence[float]) -> float:
    """ばらつき。(最大 - 最小) / 平均。全部同じなら 0。"""
    mean = sum(values) / len(values)
    return (max(values) - min(values)) / mean if mean else 0.0


def _soft(measured: float, tolerance: float) -> float:
    """許容差に対する近さを 0〜1 で返す。ぴったりなら 1、許容差ちょうどで 0。"""
    return max(0.0, 1.0 - measured / tolerance) if tolerance else 0.0


@dataclass(frozen=True)
class PreviousPage:
    """直前のページ。**コマは読み順で持つ。**"""
    number: int | None = None
    boxes: Sequence[NBox] = ()


@dataclass(frozen=True)
class PageContext:
    """ページそのものについて、座標から分からないこと。

    見開きの定石はページ番号の偶奇と直前のページで決まるので、
    **このページのコマの座標だけでは判定できない。**
    """
    number: int | None = None      # ページ番号。分からなければ None
    previous: PreviousPage | None = None
    # コマを置いてよい範囲（基本枠）。**空白ページで置き場所を決めるのに要る。**
    # 既にコマがあるページなら、余白はそのコマから借りられる（→ `borrow_frame`）が、
    # 1枚も無いページには手本が無い。分からなければ None
    frame: NBox | None = None
    # コマとコマの隙間。**横と縦で別に持つ。** px の隙間を正規化すると縦横で値が変わる
    # （基本枠と同じ理由）。分からなければ None で、既定値を使う
    gutter_x: float | None = None
    gutter_y: float | None = None


# 何も分からないときのページの文脈。**中身が無く、書き換えられない**ので使い回してよい
# （既定の引数で `PageContext()` を呼ぶと ruff の B008 に触れる）
NO_CONTEXT = PageContext()


@dataclass
class Candidate:
    """提案する1コマ。"""
    box: NBox
    order: int          # 既存コマの続きとしての読み順
    joseki: str
    reason: str


@dataclass
class Plan:
    """1つの提案。**コマ1個以上でひとまとまり。**

    定石1は4コマで1つの提案。逆に、同じ定石が**別々の案**を複数出すこともある
    （下段の右コマの幅 1/3・1/2・2/3 など）。**案どうしは重なるので、同時に描かない。**
    """
    candidates: list[Candidate]
    label: str = ""       # 同じ定石の中で案を見分ける名前（例: 幅 1/3）


@dataclass
class Check:
    """条件1つの判定結果。**実測値を必ず持たせる**（外したとき理由を追うため）。

    `passed` は3値。**None は「判定できない」**（比べる材料が足りない）。
    材料が無いときに黙って通すと、**中身の無い「o」が並ぶ。**
    """
    name: str
    passed: bool | None
    measured: str

    @property
    def mark(self) -> str:
        return "o" if self.passed else ("?" if self.passed is None else "x")


def _sameness_check(name: str, values: Sequence[float], tolerance: float) -> Check:
    """「ほぼ同じか」を見る。**2個以上ないと比べようがない。**"""
    if len(values) < 2:
        return Check(name, None, f"値が{len(values)}個では比べられない")
    spread = _spread(values)
    return Check(name, spread <= tolerance,
                 f"ばらつき {spread:.1%} / 許容 {tolerance:.0%}")


def _constancy_check(name: str, gaps: Sequence[float], tolerance: float) -> Check:
    """「一定か」を見る。**間隔が2個以上ないと、一定かどうか言えない。**

    間隔1個だと差は必ず 0 になり、**何を入れても通ってしまう。**
    """
    if len(gaps) < 2:
        return Check(name, None, f"間隔が{len(gaps)}個では一定かどうか言えない")
    diff = max(gaps) - min(gaps)
    return Check(name, diff <= tolerance,
                 f"差 {diff:.3f} / 許容 {tolerance:.2f}")


@dataclass
class Match:
    joseki: str
    title: str
    matched: bool
    # 条件をどれだけ余裕をもって満たしたか（0〜1）。**順位には使わない。**
    # 測っているものが定石ごとに違うので、定石をまたいで比べられない
    # （ばらつきの小ささ・空きの大きさ・細さ・固定値が混ざっている）。
    score: float
    priority: int = 1                  # 大きいほど特殊。出す順はこれと定石表の並びで決まる
    checks: list[Check] = field(default_factory=list)
    plans: list[Plan] = field(default_factory=list)

    def add_plan(self, candidates: Sequence[Candidate], label: str = "") -> None:
        self.plans.append(Plan(candidates=list(candidates), label=label))

    def report(self) -> str:
        head = "[{}] {} 優先{} / 余裕度 {:.2f} / {}".format(
            "一致" if self.matched else "不一致", self.title,
            self.priority, self.score, self.joseki)
        lines = [head]
        for c in self.checks:
            lines.append(f"    {c.mark} {c.name} （{c.measured}）")
        for plan in self.plans:
            head = "    -> " + (plan.label + ": " if plan.label else "")
            for i, cand in enumerate(plan.candidates):
                b = cand.box.rounded()
                lines.append("{}コマ{}: x={} y={} w={} h={}  {}".format(
                    head if i == 0 else "       ",
                    cand.order, b.x, b.y, b.w, b.h, cand.reason))
        return "\n".join(lines)


# --- 定石1: 縦コマ列の左右反復 -------------------------------------------------
#
#   条件  右半分に3個以上のコマがある / 幅がほぼ同じ / 高さがほぼ同じ /
#         縦の間隔がほぼ一定 / 左半分が空いている
#   提案  各コマを左右反転して左半分へ置く。読み順は右列を上から下、続いて左列を上から下
#
# 「四コマ漫画」とは呼ばない。**右側だけを見て作品の形式を断定する必要は無い。**
# 4コマとは限らないので、コマ数は 3 以上を通す。

# 幅・高さのばらつき（平均に対する比）。**この2つだけ材料が無い。**
# 縦4コマ列のページが手元に1枚も無いので、実測で確かめようがない。
WIDTH_TOLERANCE = 0.15
HEIGHT_TOLERANCE = 0.20
# 縦の間隔の差。実測では**ページ内の段の間隔の差は 0.003 以下**で、この値は10倍ゆるい。
# **ゆるいまま残してある。** 締めても落ちるページが実測の中に1枚も無く、効き目を
# 確かめられないため（数字が動かない条件を締めるのは、根拠の無い変更になる）。
GAP_TOLERANCE = 0.03
# 「右半分」の判定にどれだけ食い込みを許すか。実測では**中央をまたぐコマが 20個中12個**
# あり、またぎの深さは中央 0.116。**またぐのが普通**なので、この条件は強い制約になる
# （縦4コマ列を狙う定石なので、それでよい）。（確認日: 2026-09-04）
HALF_MARGIN = 0.03
# 左半分の面積のうち、埋まっていてよい割合。0.02 では厳しすぎた:
# 実測に、中央を 0.052 またぐ右上コマがあり、占有 4.0% になる。
# **中央をわずかにまたぐコマは珍しくない**ので 0.05 まで通す。**手で置いた値。**
LEFT_EMPTY_TOLERANCE = 0.05
MIN_PANELS = 3


def match_vertical_strip_repeat(boxes: Sequence[NBox], ctx: PageContext = NO_CONTEXT) -> Match:
    m = Match(joseki="vertical_strip_repeat", title="縦コマ列の左右反復",
              matched=False, score=0.0, priority=4)
    if not boxes:
        m.checks.append(Check("コマが検出されている", False, "0個"))
        return m

    column = sorted(boxes, key=lambda b: b.y)

    enough = len(column) >= MIN_PANELS
    m.checks.append(Check(f"コマ数が{MIN_PANELS}個以上", enough,
                          f"{len(column)}個"))

    # 断ち切りコマがあるページは、この定石の対象から外す（2026-09-04 決定）。
    # ページ端まで抜けたコマは正規化すると x=0 になり、右半分の判定が意味を失う。
    bleeding = [b for b in column if is_bleed(b)]
    m.checks.append(Check("断ち切りコマが無い", not bleeding,
                          f"断ち切り {len(bleeding)}個"))

    leftmost = min(b.x for b in column)
    in_right = leftmost >= 0.5 - HALF_MARGIN
    m.checks.append(Check("全コマが右半分に収まる", in_right,
                          f"左端 x={leftmost:.3f}"))

    left_filled = sum(intersection_area(b, NBox(0.0, 0.0, 0.5, 1.0)) for b in column) / 0.5
    left_empty = left_filled <= LEFT_EMPTY_TOLERANCE
    m.checks.append(Check("左半分が空いている", left_empty,
                          f"占有 {left_filled:.1%}"))

    m.checks.append(_sameness_check("幅がほぼ同じ", [b.w for b in column], WIDTH_TOLERANCE))
    m.checks.append(_sameness_check("高さがほぼ同じ", [b.h for b in column], HEIGHT_TOLERANCE))
    gaps = [column[i + 1].y - column[i].bottom for i in range(len(column) - 1)]
    m.checks.append(_constancy_check("縦の間隔がほぼ一定", gaps, GAP_TOLERANCE))

    # **「判定できない」は通さない。** 材料が足りないまま成立させない
    m.matched = all(c.passed is True for c in m.checks)
    if not m.matched:
        return m

    w_spread = _spread([b.w for b in column])
    h_spread = _spread([b.h for b in column])
    gap_diff = max(gaps) - min(gaps)

    # 一致度は「ばらつきの小ささ」だけで決める。通っただけの条件は満点扱いにしない。
    m.score = (_soft(w_spread, WIDTH_TOLERANCE)
               + _soft(h_spread, HEIGHT_TOLERANCE)
               + _soft(gap_diff, GAP_TOLERANCE)) / 3.0

    next_order = len(column) + 1
    plan = []
    for i, b in enumerate(column, start=1):
        plan.append(Candidate(box=b.mirrored(), order=next_order, joseki=m.joseki,
                              reason=f"右列 第{i}段を左右反転した位置"))
        next_order += 1
    m.add_plan(plan)
    return m


# --- 定石2: 左半分を上下2等分 -------------------------------------------------
#
#   条件  左半分が空いている
#   提案  左半分に、上下の等間隔な2コマを置く
#
# **定石1に当たらなかったページの受け皿。断ち切りコマがあってもよい。**
# 断ち切りのページを定石1から外した以上、行き場が要る（2026-09-04 決定）。
# 定石1より一般的なので優先度は低い（両方に当たったら定石1を採る）。

# コマ間の余白。既存コマから測れないときだけ使う。
# 実測（5ページ・段と段の縦の間 6件）: 最小 0.011 / 中央 0.018 / 最大 0.022。
# **中央値をそのまま使う。**（確認日: 2026-09-04）
DEFAULT_GUTTER = 0.018

# これ未満の幅・高さしか取れないなら提案しない。
# 実測（5ページ・コマ20個）: 幅は最小 0.236、高さは最小 0.206。
# **以前の 0.05 は実物の最小コマの 1/4 で、どんな細切れも通していた。**
# 実測の最小より下に、少し余裕を見て置く。（確認日: 2026-09-04）
MIN_ROOM = 0.15


def _median(values: Sequence[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


@dataclass(frozen=True)
class Frame:
    """既存コマから借りた、ページの余白とコマ間。**新しい余白を発明しないための道具。**

    左の余白は右と同じ、下の余白は上と同じとみなす。断ち切りのページでは
    上の余白が 0 なので、**下端も自動でページ端まで伸びる。**
    """
    left: float
    right: float
    top: float
    bottom: float
    gutter: float


def borrow_frame(boxes: Sequence[NBox]) -> Frame:
    column = sorted(boxes, key=lambda b: b.y)
    # **横に並んだコマ同士の「縦の間隔」は負になる。** 上下に離れている組だけを数える
    # （数えないと、横並びの2コマから -0.368 のような余白が出る）。
    gaps = [g for g in (column[i + 1].y - column[i].bottom
                        for i in range(len(column) - 1)) if g > 0]
    gutter = _median(gaps) if gaps else DEFAULT_GUTTER
    right = max(b.right for b in boxes)
    top = min(b.y for b in boxes)
    return Frame(left=max(0.0, 1.0 - right), right=right,
                 top=top, bottom=min(1.0, 1.0 - top), gutter=gutter)


def top_band(boxes: Sequence[NBox]) -> list[NBox]:
    """最も上の段。**最初のコマと縦に重なるコマの集まり。**

    しきい値を持たない——同じ段のコマは必ず縦に重なるので、重なりだけで決まる。
    """
    first = min(boxes, key=lambda b: b.y)
    return [b for b in boxes if b.y < first.bottom and b.bottom > first.y]


def bottom_band(boxes: Sequence[NBox]) -> list[NBox]:
    """最も下の段。**最後のコマと縦に重なるコマの集まり。** `top_band` の裏返し。"""
    last = max(boxes, key=lambda b: b.y)
    return [b for b in boxes if b.y < last.bottom and b.bottom > last.y]


def remaining_ratio(f: Frame, lowest: float) -> float:
    """使える高さのうち、まだ空いている割合。**余裕度の説明に使うだけ。**"""
    usable = f.bottom - f.top
    return max(0.0, (f.bottom - lowest) / usable) if usable > 0 else 0.0


def _overlapping(proposed: Sequence[NBox], boxes: Sequence[NBox]) -> list[NBox]:
    return [c for c in proposed
            if any(intersection_area(c, b) > 1e-6 for b in boxes)]


def match_left_half_two_even(boxes: Sequence[NBox], ctx: PageContext = NO_CONTEXT) -> Match:
    m = Match(joseki="left_half_two_even", title="左半分を上下2等分",
              matched=False, score=0.0, priority=1)
    if not boxes:
        m.checks.append(Check("コマが検出されている", False, "0個"))
        return m

    left_filled = sum(intersection_area(b, NBox(0.0, 0.0, 0.5, 1.0)) for b in boxes) / 0.5
    left_empty = left_filled <= LEFT_EMPTY_TOLERANCE
    m.checks.append(Check("左半分が空いている", left_empty,
                          f"占有 {left_filled:.1%} / 許容 {LEFT_EMPTY_TOLERANCE:.0%}"))
    if not left_empty:
        return m

    # **余白を発明せず、既存コマから借りる。**
    #   左の余白  = 右の余白と同じとみなす
    #   下の余白  = 上の余白と同じとみなす
    #   コマ間    = 既存の縦の間隔の中央値。測れないときだけ既定値
    f = borrow_frame(boxes)
    gutter, top = f.gutter, f.top
    x = f.left
    width = (min(b.x for b in boxes) - gutter) - x
    height = (f.bottom - top - gutter) / 2.0

    room = width >= MIN_ROOM and height >= MIN_ROOM
    m.checks.append(Check("2コマ分の幅と高さが取れる", room,
                          f"幅 {width:.3f} 高さ {height:.3f} / 余白 {gutter:.3f}"))
    if not room:
        return m

    proposed = [NBox(x, top, width, height),
                NBox(x, top + height + gutter, width, height)]
    overlapping = _overlapping(proposed, boxes)
    m.checks.append(Check("既存コマと重ならない", not overlapping,
                          f"重なり {len(overlapping)}個"))
    if overlapping:
        return m

    m.matched = True
    m.score = _soft(left_filled, LEFT_EMPTY_TOLERANCE)
    next_order = len(boxes) + 1
    plan = []
    for i, b in enumerate(proposed, start=1):
        plan.append(Candidate(box=b, order=next_order, joseki=m.joseki,
                              reason=f"左半分の上下2等分 第{i}段（余白は既存コマから借用）"))
        next_order += 1
    m.add_plan(plan)
    return m


# --- 定石3: 三段構造の中段右 ---------------------------------------------------
#
#   条件  断ち切りコマが無い / 描かれているのが1段だけ / 下に空きがある
#   提案  上・中・下の三段構造と予想し、**中段の右コマ**を1つ置く
#
# 上から描き進めるページでは、空くのは左ではなく下。
# 断ち切りのあるページは定石4が扱うので、ここでは除く（定石1と同じ線引き）。
#
# **「下に 2/3 以上の空きがある」という条件は置かない。** 実測すると、三段のページの
# 1段目でも空きは 61.7% しかなく、**三段のページですら通らない条件**になる。
# さらに、**上段からは二段か三段かを見分けられない**——二段のページは上段が使える高さの
# 36.9%・残り 1.71段分、三段のページは 37.0%・1.70段分で、ほぼ同じ数字で結果が違う。
# **見分けようとせず、三段の解釈（この定石）と二段の解釈（定石8）を両方出す。**
# 代わりに置いたのは**しきい値ではなく構造の条件**（描かれているのが1段だけか）。


# 帯の右コマの幅。**1つに決めない。**
#
# 実測した3ページで、最下段の右コマが使える幅に占める割合は **33.7% / 55.3% / 67.9%**。
# **3枚とも違う。** 1枚だけを見て 1/3 に決めていたが、材料が増えたら合わなくなった。
# **どれが正しいかは幾何では決まらない**ので、3つとも案として並べる。
RIGHT_WIDTH_SHARES = ((1, 3), (1, 2), (2, 3))


def _width_label(num: int, den: int) -> str:
    """幅の案の名前。**3か所で同じ形にするため、ここでだけ作る。**"""
    return "幅 いっぱい" if num == den else f"幅 {num}/{den}"


def _right_panel(f: Frame, share: float) -> tuple[float, float]:
    """帯を左右に割ったときの、右コマの x と幅。share は使える幅に対する割合。"""
    width = (f.right - f.left - f.gutter) * share
    return f.right - width, width


def _propose_band_right(m: Match, boxes: Sequence[NBox], f: Frame,
                        top: float, height: float, reason: str) -> None:
    """帯の右コマを、**幅ちがいで並べて**提案する。定石3・4・8で共有する。

    **高さの確認をここで持つ。** 呼ぶ側に任せると、埋まったページで
    高さが負のコマを提案してしまう（実際に一度そうなった）。
    """
    if height < MIN_ROOM:
        return
    order = len(boxes) + 1
    for num, den in RIGHT_WIDTH_SHARES:
        x, width = _right_panel(f, num / den)
        box = NBox(x, top, width, height)
        if width < MIN_ROOM or _overlapping([box], boxes):
            continue
        m.add_plan([Candidate(box=box, order=order, joseki=m.joseki, reason=reason)],
                   label=_width_label(num, den))


def _propose_bottom_right(m: Match, boxes: Sequence[NBox], lowest: float) -> None:
    """残った下の領域の右 1/3 に1コマ置く。**定石4と定石8で中身を共有する。**

    2つは提案の作り方がまったく同じで、**断ち切りの有無だけが違う。**
    別々に書くと、片方だけ直して食い違う。
    """
    f = borrow_frame(boxes)
    top = lowest + f.gutter
    height = f.bottom - top
    _propose_band_right(m, boxes, f, top, height, "下の帯の右コマ")

    m.checks.append(Check("下の帯に置ける案がある", bool(m.plans),
                          f"高さ {height:.3f} / 案 {len(m.plans)}件 / 余白 {f.gutter:.3f}"))
    m.matched = bool(m.plans)
    m.score = remaining_ratio(f, lowest) if m.matched else 0.0


def match_three_band_middle_right(boxes: Sequence[NBox], ctx: PageContext = NO_CONTEXT) -> Match:
    m = Match(joseki="three_band_middle_right", title="三段構造の中段右",
              matched=False, score=0.0, priority=2)
    if not boxes:
        m.checks.append(Check("コマが検出されている", False, "0個"))
        return m

    bleeding = [b for b in boxes if is_bleed(b)]
    m.checks.append(Check("断ち切りコマが無い", not bleeding,
                          f"断ち切り {len(bleeding)}個"))

    # **描かれているのが1段だけのときにだけ、三段構造を予想する。**
    # 2段目まで描かれたページで「残りを2段に割る」のは、この定石の意図ではない。
    band = top_band(boxes)
    only_one_band = len(band) == len(boxes)
    m.checks.append(Check("描かれているのが1段だけ", only_one_band,
                          f"{1 if only_one_band else 2}段目まで描かれている"))

    f = borrow_frame(boxes)
    lowest = max(b.bottom for b in boxes)
    remaining = remaining_ratio(f, lowest)
    m.checks.append(Check("下に空きがある", remaining > 0, f"残り {remaining:.1%}"))
    if bleeding or not only_one_band or remaining <= 0:
        return m

    band_top = lowest + f.gutter
    height = (f.bottom - band_top - f.gutter) / 2.0   # 中段と下段で2等分
    _propose_band_right(m, boxes, f, band_top, height,
                        "上・中・下の三段と予想した中段の右コマ")

    m.checks.append(Check("中段に置ける案がある", bool(m.plans),
                          f"高さ {height:.3f} / 案 {len(m.plans)}件 / 余白 {f.gutter:.3f}"))
    m.matched = bool(m.plans)
    m.score = remaining if m.matched else 0.0
    return m


# --- 定石4: 下段の右（断ち切りページ）-----------------------------
#
#   条件  断ち切りコマがある / 下に空きがある
#   提案  残った下の領域の**右 1/3** にコマを1つ置く
#
# **「すべてのコマが上半分に収まる」という条件は置かない。** 下端 0.5 という絶対値は
# 実物と合わず、二段まで描いたページ（下端 0.605）が落ちてしまう。
# 置けるかどうかは「幅と高さが取れる」で見れば足りる。
#
# 断ち切りのページには余白の手本が無い。Frame は上の余白（＝0）を下にも当てるので、
# **領域が自動でページ端まで伸びる。**


def match_bleed_top_bottom_right(boxes: Sequence[NBox], ctx: PageContext = NO_CONTEXT) -> Match:
    m = Match(joseki="bleed_top_bottom_right", title="下段の右（断ち切りページ）",
              matched=False, score=0.0, priority=2)
    if not boxes:
        m.checks.append(Check("コマが検出されている", False, "0個"))
        return m

    bleeding = [b for b in boxes if is_bleed(b)]
    m.checks.append(Check("断ち切りコマがある", bool(bleeding),
                          f"断ち切り {len(bleeding)}個"))

    lowest = max(b.bottom for b in boxes)
    m.checks.append(Check("下に空きがある", True,
                          f"残り {remaining_ratio(borrow_frame(boxes), lowest):.1%}"))
    if not bleeding:
        return m

    _propose_bottom_right(m, boxes, lowest)
    return m


# --- 定石5: 細い右上コマの左隣 -------------------------------------------------
#
#   条件  下と左の両方に空きがある / 右上のコマの幅がページの 1/3 以下
#   提案  同じ幅・同じ高さのコマを、その左隣に置く
#
# **細い右上コマは、横に並べる意図の現れ**とみなす（2026-09-04 追加）。
# 下にも空いているが、**下ではなく左を先に埋める**ので、定石3・4より優先度を高くする。
# 幅が 1/3 を超えるなら当てはまらず、下へ落ちる（定石3・4）。

# 「細い」とみなす幅の上限。実測（5ページ・コマ20個）の幅は
# 最小 0.236 / 中央 0.446 / 最大 1.000。**1/3（0.333）は中央より明確に狭い側。**
# （確認日: 2026-09-04）
NARROW_TOP_RATIO = 1.0 / 3.0


def match_narrow_top_beside_left(boxes: Sequence[NBox],
                                 ctx: PageContext = NO_CONTEXT) -> Match:
    m = Match(joseki="narrow_top_beside_left", title="細い右上コマの左隣",
              matched=False, score=0.0, priority=3)
    if not boxes:
        m.checks.append(Check("コマが検出されている", False, "0個"))
        return m

    top_right = min(boxes, key=lambda b: (b.y, -b.right))
    narrow = top_right.w <= NARROW_TOP_RATIO
    m.checks.append(Check("右上のコマが幅 1/3 以下", narrow,
                          f"幅 {top_right.w:.3f} / 上限 {NARROW_TOP_RATIO:.3f}"))

    empty_below = 1.0 - max(b.bottom for b in boxes)
    below_ok = empty_below >= MIN_ROOM
    m.checks.append(Check("下に空きがある", below_ok, f"空き {empty_below:.1%}"))

    f = borrow_frame(boxes)
    x = top_right.x - f.gutter - top_right.w
    proposed = [NBox(x, top_right.y, top_right.w, top_right.h)]
    fits = x >= f.left - 1e-6
    m.checks.append(Check("左に同じ幅が入る", fits,
                          f"左端 {x:.3f} / ページ左余白 {f.left:.3f}"))
    if not (narrow and below_ok and fits):
        return m

    overlapping = _overlapping(proposed, boxes)
    m.checks.append(Check("左が空いている", not overlapping,
                          f"重なり {len(overlapping)}個"))
    if overlapping:
        return m

    m.matched = True
    m.score = _soft(top_right.w, NARROW_TOP_RATIO)
    m.add_plan([Candidate(box=proposed[0], order=len(boxes) + 1, joseki=m.joseki,
                          reason="細い右上コマと同じ幅・高さで、その左隣")])
    return m


# --- 定石6: 見開きドン ---------------------------------------------------------
#
#   条件  1. このページが空白の新規ページである
#         2. 直前のページが存在し、奇数ページである
#         3. 【提案】が押された
#   提案  このページの最初のコマとして、上部を横切る大きめの断ち切りコマ
#
# **条件3は、この道具が呼ばれたこと自体。** 判定の対象にならないので、下の checks には
# 現れない。**呼ばれていないのに提案を出さないことは、組み込む側の約束事**になる
# （設計文書「実装への申し送り」）。
#
# **条件はこの2つだけ。** 「直前のページが埋め切られている」「最後のコマが左下にあり、
# 小さい」も試したが、**どちらも置かない。ページを次へ進めたこと自体が、直前を
# 描き終えた証拠**とみなす。条件を減らしたことで、実物のページに当たるようになった。

# 断ち切りコマの高さ。**ページ全面ではない**（全面だと次のコマを置く余地が残らない）。
# 実測した4ページの1段目の高さは 0.294〜0.378 で、**中央値 0.369。そこに合わせる。**
# 1件だけを見て 0.4 にしていたが、材料を増やして直した。
OPENING_BAND_HEIGHT = 0.37


def match_spread_opening_bleed(boxes: Sequence[NBox],
                               ctx: PageContext = NO_CONTEXT) -> Match:
    m = Match(joseki="spread_opening_bleed", title="見開きドン",
              matched=False, score=0.0, priority=3)

    blank = not boxes
    m.checks.append(Check("このページが空白", blank, f"コマ {len(boxes)}個"))

    prev = ctx.previous
    has_prev = prev is not None and bool(prev.boxes)
    m.checks.append(Check("直前のページがある", has_prev,
                          "ページ {}".format(prev.number if prev else "無し")))

    odd = has_prev and prev.number is not None and prev.number % 2 == 1
    m.checks.append(Check("直前が奇数ページ", odd,
                          "ページ {}".format(
                              prev.number if prev and prev.number is not None else "不明")))
    if not (blank and has_prev and odd):
        return m

    m.matched = True
    m.score = 1.0   # 条件はすべて成立か不成立か。**中間が無いので惜しさも無い**
    m.add_plan([Candidate(
        box=NBox(0.0, 0.0, 1.0, OPENING_BAND_HEIGHT), order=1, joseki=m.joseki,
        reason="奇数ページ（{}p）の次を開くコマ。上部を横切る断ち切り".format(
            prev.number if prev.number is not None else "?"))])
    return m


# --- 定石7: 空白ページの描き出し -----------------------------------------------
#
#   条件  このページが空白の新規ページである
#   提案  幅 1/3・高さ 1/3 のコマを右上に置く（三段構成の右上コマ）
#
# **これは定石と呼べるほどのものではない**（2026-09-04 追加）。
# 【提案】を押したときの**一パターン**にすぎず、「こう描くべき」という型ではない。
# **理想は、押すたびに複数のパターンを順繰りに出すこと。今はそこまでやらない。**
# そのため優先度は最も低くし、定石6（見開きドン）が成り立つ場面ではそちらを採る。
#
# 空白ページには**余白の手本が無い**ので、既定の余白を使うしかない。

OPENING_PANEL_RATIO = 1.0 / 3.0   # 高さは置いてよい範囲の 1/3（三段構成の1段ぶん）

# 最初のコマの幅。**1つに決めない**（帯の右コマと同じ考え方）。
# 空白ページには手本が何も無く、**どれが良いかを決める材料がそもそも無い。**
# 「幅いっぱい」は、横帯で始める描き出し
OPENING_WIDTH_SHARES = ((1, 3), (1, 2), (2, 3), (1, 1))

# 上を断ち切る描き出し。**ページの端を越えて伸ばす。**
# 端ちょうどで止めると、断裁のずれで白い筋が出る。越えた分は切り落とされる。
# 幅は 1/2（半分ほどの大きさが、断ち切りの見せ場として収まりがよい）。
# **越える量は手で置いた値。** この道具は仕上がり寸法しか持たないので、借りる手本が無い
OPENING_BLEED_SHARE = (1, 2)
OPENING_BLEED_OVERFLOW = 0.02

# 4コマ×2列の雛形。**8コマまとめて1つの案。**
# 読む順は右列を上から下、続いて左列を上から下（→ 縦コマ列の左右反復と同じ）
OPENING_GRID_ROWS = 4
OPENING_GRID_COLUMNS = 2
# 空白ページの余白。**手本が無いので、材料の真ん中に置くしかない。**
# 断ち切りの無いページの余白は実測で二分している（0.006〜0.031 と 0.088〜0.120）。
# **中間の値。材料が二分しているので、決め手が無い。**
DEFAULT_MARGIN = 0.03


def match_blank_page_opening(boxes: Sequence[NBox],
                             ctx: PageContext = NO_CONTEXT) -> Match:
    m = Match(joseki="blank_page_opening", title="空白ページの描き出し",
              matched=False, score=0.0, priority=1)

    blank = not boxes
    m.checks.append(Check("このページが空白", blank, f"コマ {len(boxes)}個"))
    if not blank:
        return m

    # **基本枠が分かっているなら、その中に置く。** 分からないときだけ既定の余白を使う。
    # 枠を無視すると、右上が枠からはみ出したコマを置いてしまう
    area = ctx.frame or NBox(
        DEFAULT_MARGIN, DEFAULT_MARGIN, 1.0 - DEFAULT_MARGIN * 2, 1.0 - DEFAULT_MARGIN * 2
    )
    height = area.h * OPENING_PANEL_RATIO
    m.matched = True
    m.score = 1.0
    for num, den in OPENING_WIDTH_SHARES:
        width = area.w * num / den
        m.add_plan(
            [Candidate(
                box=NBox(area.right - width, area.y, width, height),
                order=1, joseki=m.joseki,
                reason="三段構成の右上コマ（置いてよい範囲の右上に、高さは枠の 1/3）")],
            label=_width_label(num, den),
        )
    m.add_plan(_opening_bleed(area, height, m.joseki), label="上を断ち切り（幅 1/2）")
    # **雛形は最後に置く。** 8コマまとめて敷く案なので、1コマずつの案を見てから
    m.add_plan(_opening_grid(area, ctx, m.joseki), label="4コマ×2列")
    return m


def _opening_bleed(area: NBox, height: float, joseki: str) -> list[Candidate]:
    """上をページの外まで伸ばした、断ち切りの描き出し。

    **下辺は他の案と同じ位置。** 上だけが伸びるので、他の案と見比べやすい。
    """
    num, den = OPENING_BLEED_SHARE
    width = area.w * num / den
    bottom = area.y + height
    top = -OPENING_BLEED_OVERFLOW
    return [Candidate(
        box=NBox(area.right - width, top, width, bottom - top),
        order=1, joseki=joseki,
        reason="上をページの外まで伸ばした断ち切りコマ（下辺は他の案と同じ）")]


def _opening_grid(area: NBox, ctx: PageContext, joseki: str) -> list[Candidate]:
    """4コマ×2列の8コマを、置いてよい範囲いっぱいに敷く。

    **読む順は右列を上から下、続いて左列を上から下。** 4コマのページはそう読む
    （慣習で決まっている）ので、番号もその順で振る。
    """
    gutter_x = ctx.gutter_x if ctx.gutter_x is not None else DEFAULT_GUTTER
    gutter_y = ctx.gutter_y if ctx.gutter_y is not None else DEFAULT_GUTTER
    width = (area.w - gutter_x * (OPENING_GRID_COLUMNS - 1)) / OPENING_GRID_COLUMNS
    height = (area.h - gutter_y * (OPENING_GRID_ROWS - 1)) / OPENING_GRID_ROWS

    plan = []
    order = 1
    for column in range(OPENING_GRID_COLUMNS):          # 右の列から左の列へ
        x = area.right - width - column * (width + gutter_x)
        for row in range(OPENING_GRID_ROWS):            # 上から下へ
            plan.append(Candidate(
                box=NBox(x, area.y + row * (height + gutter_y), width, height),
                order=order, joseki=joseki,
                reason=f"4コマ×2列の雛形（{column + 1}列目 {row + 1}段目）"))
            order += 1
    return plan


# 定石表。**9つ目まで実装済み。** 残りは名前だけ置いてある（設計文書と対応）。
# --- 定石8: 下段の右（断ち切り無し）---------------------------------------------------
#
#   条件  断ち切りコマが無い / 下に空きがある
#   提案  残った下の領域の右 1/3 にコマを1つ置く（定石4と同じ提案）
#
# **定石4の断ち切り抜き版。** 定石4とは断ち切りの有無だけが違い、提案の作り方は共有する。
#
# **二段構成は珍しくない**（実測した3ページのうち2枚がそれ）。ところが定石4は断ち切りを
# 条件にしているので、**断ち切りの無い二段ページには当たる定石が1つも無かった。**


def match_two_tier_bottom_right(boxes: Sequence[NBox],
                                ctx: PageContext = NO_CONTEXT) -> Match:
    m = Match(joseki="two_tier_bottom_right", title="下段の右（断ち切り無し）",
              matched=False, score=0.0, priority=2)
    if not boxes:
        m.checks.append(Check("コマが検出されている", False, "0個"))
        return m

    bleeding = [b for b in boxes if is_bleed(b)]
    m.checks.append(Check("断ち切りコマが無い", not bleeding,
                          f"断ち切り {len(bleeding)}個"))

    lowest = max(b.bottom for b in boxes)
    m.checks.append(Check("下に空きがある", True,
                          f"残り {remaining_ratio(borrow_frame(boxes), lowest):.1%}"))
    if bleeding:
        return m

    _propose_bottom_right(m, boxes, lowest)
    return m


# --- 定石9: 同じ段の左を埋める -------------------------------------------------
#
#   条件  最も下の段に、まだ左の空きがある / そこにコマを置ける
#   提案  その段の左隣に1コマ。幅は「残り全部」「3/4」「1/2」の3案
#
# **段の途中で止まっている状態を見る定石。**
# 帯の定石（3・4・8）は**最も下の端より下**しか見ないので、
# **右コマだけ置かれた段の左の空きは、どの定石の視野にも入らない。**
# その状態では**1つ飛ばして次の段を提案してしまう。**
#
# 高さと上端は、**その段の右コマに合わせる。** 実測では、同じ段の左コマの高さと上端は
# 右コマと一致していた（2ページで確認）。**そこは推測が要らない。**
#
# 幅は決め打ちにしない。**右端を段の右コマの隣に固定し、左へどこまで伸ばすかを変える。**
# 実測では、残り幅に対して「ほぼ全部」の例と「ほぼ 3/4」の例があった。
LEFT_WIDTH_SHARES = ((1, 1), (3, 4), (1, 2))

# **その段のコマの半分より狭い空きは、余白とみなして埋めない。**
# 絶対値ではなく、**そのページ自身のコマを物差しにする。**
# 段どうしで左端が違うページ（実測で 0.047 と 0.117）は、**完成していても 0.053 の
# 空きが残る。** 歯止めが無いと、完成したページの余白に細いコマを提案してしまう。
MIN_BAND_SHARE = 0.5


def match_fill_band_left(boxes: Sequence[NBox],
                         ctx: PageContext = NO_CONTEXT) -> Match:
    m = Match(joseki="fill_band_left", title="同じ段の左を埋める",
              matched=False, score=0.0, priority=3)
    if not boxes:
        m.checks.append(Check("コマが検出されている", False, "0個"))
        return m

    f = borrow_frame(boxes)
    band = bottom_band(boxes)
    top = min(b.y for b in band)
    height = max(b.bottom for b in band) - top
    right_edge = min(b.x for b in band) - f.gutter
    available = right_edge - f.left

    narrowest = min(b.w for b in band)
    enough = available >= MIN_ROOM and available >= narrowest * MIN_BAND_SHARE
    m.checks.append(Check("最も下の段に左の空きがある", enough,
                          f"空き幅 {available:.3f} / 段の最も狭いコマ {narrowest:.3f} の半分以上か"))
    if not enough:
        return m

    order = len(boxes) + 1
    for num, den in LEFT_WIDTH_SHARES:
        width = available * num / den
        box = NBox(right_edge - width, top, width, height)
        if width < MIN_ROOM or _overlapping([box], boxes):
            continue
        m.add_plan([Candidate(box=box, order=order, joseki=m.joseki,
                              reason="同じ段の左隣（高さは段に合わせる）")],
                   label=_width_label(num, den))

    m.checks.append(Check("置ける案がある", bool(m.plans), f"案 {len(m.plans)}件"))
    m.matched = bool(m.plans)
    m.score = available / (f.right - f.left) if m.matched else 0.0
    return m


# **並びは出す順そのもの**（特殊なものから順に。優先度が同じならこの並びのまま出る）。
JOSEKI: list[Callable[..., Match]] = [
    match_vertical_strip_repeat,      # 優先4
    match_narrow_top_beside_left,     # 優先3
    match_fill_band_left,             # 優先3
    match_spread_opening_bleed,       # 優先3
    match_three_band_middle_right,    # 優先2
    match_bleed_top_bottom_right,     # 優先2
    match_two_tier_bottom_right,      # 優先2
    match_left_half_two_even,         # 優先1
    match_blank_page_opening,         # 優先1
]

PLANNED = [
    ("two_column_even", "左右2列の均等配置"),
    ("horizontal_band_repeat", "横長コマの縦反復"),
    ("big_top_split_bottom", "上部に大コマ＋下部を2分割"),
    ("closing_panel_bottom", "ページ下部に大きな締めコマ"),
    ("same_width_fill", "既存コマと同じ幅で残り空間を均等分割"),
]


def match_all(boxes: Sequence[NBox],
              ctx: PageContext = NO_CONTEXT) -> list[Match]:
    """定石表を総当たりし、**出す順**に返す（一致しなかったものも含む）。

    順は「当たったものが先 → 特殊なものが先 → 定石表の並び」。
    **余裕度は順に関与しない**（定石ごとに測っているものが違うため）。
    並べ替えは安定なので、優先度が同じなら定石表に書いた順のままになる。
    """
    return sorted((f(boxes, ctx) for f in JOSEKI),
                  key=lambda m: (m.matched, m.priority), reverse=True)


def proposals(matches: Sequence[Match]) -> list[tuple[Match, Plan]]:
    """当たった定石の案を、出す順に**全部**並べる。**1件＝定石1つの、案1つ。**

    **1つに絞らない。** 【提案】を押すたびに、この並びを順繰りに見せる想定。
    どれが物語に合うかは幾何では決まらないので、**ここでは決めない。**
    """
    return [(m, plan) for m in matches if m.matched for plan in m.plans]
