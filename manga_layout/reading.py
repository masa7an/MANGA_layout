"""4コマ2列のページかどうかの判定。**2つの読み順が共有する唯一の部分。**

このアプリには読み順が2つある（→ 要件定義 10.5）。

    提案      `next_panel.reading_order`      段は**縦に重なるコマ**で切る
    書き出し  `ui/psd_export.reading_order`   段は**上端の差が隙間より小さい**で切る

**段の切り方は違ったままにしてある。** 揃えると既に書き出した作品を再度
書き出したときフォルダ名の順が動く（約3割のページで。→ 要件定義 10.5）。

**共有するのは「このページは列優先で読むか」の判定だけ。** ここだけを1本に
したのは、**4コマ2列のページで、AI や利用者が「2コマ目」と言う相手が
ずれていたため**（`describe` の番号は書き出し側の読み順を使う）。番号が
指示の宛先になる以上、「番号はラベルにすぎない」では済まなくなった
（2026-09-06。→ 要件定義 10.5）。

**写して2本持たない。** 片方だけ直って、また食い違う——`next_panel.supported`
が同じ轍を踏んでいる（「同じ線引き」と書きながら中身が違い、4つの形で答えが
割れていた）。

**Qt を知らない。** 矩形の左右上下しか見ない。
"""

from __future__ import annotations

from .model import Panel

# 「縦に並ぶ列」とみなす最小のコマ数。**4コマ用の特殊な読み方なので、3段には
# 広げない。** 3段で左右の列がぴったり揃ったページは、ふつうの漫画のページと
# して行優先で読む。
MIN_PER_COLUMN = 4


def columns(panels: list[Panel]) -> list[list[Panel]]:
    """**ページを縦に切れる位置**で分ける。切れ目が1つも無ければ1つのまま。

    「横に重なるコマを集める」ではなく、**ページを貫く縦の切れ目**で分ける。
    集める側にすると、段ごとに幅が違うだけのふつうのページまで列に見えてしまう。

    各列は**上から下**に並べて返す。
    """
    if not panels:
        return []
    ordered = sorted(panels, key=lambda p: p.bounds().x)
    groups: list[list[Panel]] = [[ordered[0]]]
    edge = ordered[0].bounds().right
    for panel in ordered[1:]:
        box = panel.bounds()
        if box.x > edge:            # ここで縦に切れる
            groups.append([panel])
            edge = box.right
        else:
            groups[-1].append(panel)
            edge = max(edge, box.right)
    return [sorted(g, key=lambda p: p.bounds().y) for g in groups]


def column_first(panels: list[Panel]) -> list[Panel] | None:
    """列優先で読むページなら、その順に並べた一覧。**違うなら `None`。**

    **ページが縦に切れて、どの列も4個以上**のときだけ列優先（右列を上から下、
    続いて左列）。4コマ2列の8コマページの読み方で、慣習で決まっており迷う
    余地は無い。

    `None` を返した分は、呼んだ側がそれぞれの段の切り方で行優先に並べる。
    **段の切り方は共有していない**（→ このファイルの冒頭）。
    """
    if not panels:
        return None
    found = columns(panels)
    if len(found) < 2 or any(len(c) < MIN_PER_COLUMN for c in found):
        return None
    ordered: list[Panel] = []
    for column in sorted(found, key=lambda c: -c[0].bounds().right):
        ordered.extend(column)
    return ordered
