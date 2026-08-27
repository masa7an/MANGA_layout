"""このアプリが投げる例外。

利用者にそのまま見せられる日本語のメッセージを持たせる。
「どのファイルの、どの項目が」まで書くこと。
"""


class MangaLayoutError(Exception):
    """このアプリ由来の例外すべての親。"""


class ProjectFormatError(MangaLayoutError):
    """project.json の内容が期待した形になっていない。"""


class ProjectNotFoundError(MangaLayoutError):
    """指定された場所にプロジェクトが無い。"""


class UnsupportedVersionError(ProjectFormatError):
    """このアプリより新しい形式で保存されたプロジェクト。

    古いアプリで新しいファイルを開いて、知らない項目を捨てたまま
    保存し直す事故を防ぐために、読み込み自体を断る。
    """


class ExportError(MangaLayoutError):
    """PNG の書き出しに失敗した。

    保存先が決まっていない、確保できない大きさを指定した、書き込めない、
    のいずれか。どれも利用者が対処できる話なので、そのまま見せる。
    """


class ImageFetchError(MangaLayoutError):
    """ネット上の画像を取ってこられなかった。

    ブラウザから絵を直接ドラッグしたときの経路（→ `fetch.py`）。
    つながらない・断られた・大きすぎる・時間切れをまとめて表す。
    どれも利用者が落とし直すか諦めるかで済む話なので、区別しない。
    """


class MaskSizeError(MangaLayoutError):
    """マスクの大きさが、掛ける相手の画像と合っていない（→ 要件定義 10.3）。

    合わないまま縮めて合わせることはしない。ずれた組み合わせが「輪郭が
    わずかにずれた絵」として通ってしまい、人が見て気づけないため。
    """


class AssetError(MangaLayoutError):
    """assets/ の操作に失敗した。"""


class UnknownImageFormatError(AssetError):
    """画像として認識できないデータを取り込もうとした。"""


class BrokenImageError(AssetError):
    """形式は分かるが、画像として展開できなかった。

    署名だけ正しく中身が壊れているファイルがこれ。`assets.py` は
    バイト列しか見ないため見抜けず、実際に展開して初めて分かる。
    """
