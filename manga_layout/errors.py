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


class AssetError(MangaLayoutError):
    """assets/ の操作に失敗した。"""


class UnknownImageFormatError(AssetError):
    """画像として認識できないデータを取り込もうとした。"""
