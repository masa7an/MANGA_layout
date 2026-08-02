"""「名前を付けて保存」の窓。

作品は1個のファイルではなく**フォルダ**（`project.json` + `assets/`）なので、
普通の「ファイルを保存」の窓は使えない。かといって「既にあるフォルダを選ぶ」
窓（`QFileDialog.getExistingDirectory`）にすると、**名前を打つ欄が無い**。
選んだ瞬間にそのフォルダへ書き込まれ、作品に名前を付けられない。

そこで「置き場所」と「作品名」を分けて受け取り、**出来上がるフォルダの
場所をそのまま見せる**。フォルダを作る操作は結果が目に見えないので、
押す前に「どこに何ができるか」を1行で確かめられるようにしてある。
"""

from __future__ import annotations

import pathlib

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..storage import is_project_dir

DEFAULT_PROJECT_NAME = "無題"

# 断る理由と「上書きになる」を出すときの色
WARN_COLOR = "#C0392B"

# Windows がフォルダ名に許さない文字
INVALID_NAME_CHARS = '\\/:*?"<>|'

# Windows が装置の名前として押さえていて、フォルダにできない語。
# 拡張子を付けても（`CON.txt`）同じなので、点より前だけを見る
RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{n}" for n in range(1, 10)]
    + [f"LPT{n}" for n in range(1, 10)]
)


def name_problem(name: str) -> str | None:
    """作品名として使えない理由。使えるなら None。

    作ってから OS に断られると、何が悪かったのか分からないまま
    やり直すことになる。押す前に理由を出す。
    """
    name = name.strip()
    if not name:
        return "作品名を入れてください"

    bad = sorted({c for c in name if c in INVALID_NAME_CHARS})
    if bad:
        return "フォルダ名に使えない文字が入っています: " + " ".join(bad)
    if name.endswith("."):
        return "末尾のピリオドはフォルダ名に使えません"
    if name.split(".")[0].upper() in RESERVED_NAMES:
        return f"「{name}」は Windows が装置の名前として使っているため、フォルダにできません"
    return None


def target_problem(path: pathlib.Path) -> str | None:
    """その場所に作品フォルダを置けない理由。置けるなら None。

    **中身のある「作品でないフォルダ」には書かない。** 作品として開けない
    ものに `project.json` と `assets/` を足すと、元から入っていたファイルと
    混ざり、あとからどちらの持ち物か分からなくなる。
    """
    if path.is_file():
        return "同じ名前のファイルが既にあります"
    if path.is_dir() and not is_project_dir(path) and any(path.iterdir()):
        return "同じ名前の、作品ではないフォルダが既にあります"
    return None


def target_note(path: pathlib.Path) -> str:
    """押したら何が起きるかの1行。"""
    problem = target_problem(path)
    if problem is not None:
        return problem
    if is_project_dir(path):
        return f"既にある作品に上書きします: {path}"
    return f"新しく作られます: {path}"


def default_parent(
    project_dir: pathlib.Path | None, configured: str | None = None
) -> pathlib.Path:
    """置き場所の初期値。次の順に、**実在する最初のもの**を使う。

    1. 開いている作品の親フォルダ。2作目は1作目の隣に作ることが多い
    2. `settings.json` の `default_parent_dir`（新しい作品のとき）
    3. ドキュメントフォルダ

    実在を毎回確かめるのは、設定に `F:` のような外付けドライブが
    書かれていて、**その PC では繋がっていない**ことがあるため。
    無い場所を出しても選び直す手間が増えるだけになる。
    """
    if project_dir is not None:
        parent = pathlib.Path(project_dir).parent
        if parent.is_dir():
            return parent
    if configured:
        path = pathlib.Path(configured)
        if path.is_dir():
            return path
    documents = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    return pathlib.Path(documents) if documents else pathlib.Path.home()


class SaveAsDialog(QDialog):
    """置き場所と作品名を決める。"""

    def __init__(
        self,
        parent_dir: pathlib.Path,
        name: str = "",
        parent: QWidget | None = None,
        settings_file: pathlib.Path | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("名前を付けて保存")

        self.parent_dir = QLineEdit(str(parent_dir), self)
        browse = QPushButton("参照...", self)
        browse.clicked.connect(self._choose_parent)

        place = QHBoxLayout()
        place.addWidget(self.parent_dir)
        place.addWidget(browse)

        self.name = QLineEdit(name or DEFAULT_PROJECT_NAME, self)
        # 開いた直後に打ち始められるよう、初期値を選択しておく
        self.name.selectAll()

        form = QFormLayout()
        form.addRow("置き場所", place)
        form.addRow("作品名", self.name)

        self.note = QLabel(self)
        self.note.setWordWrap(True)

        hint = QLabel(
            "作品は1個のファイルではなくフォルダです。"
            "上の名前でフォルダが作られ、その中に project.json と assets/ が入ります。",
            self,
        )
        hint.setWordWrap(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(self.note)
        if settings_file is not None:
            # 「なぜここから始まるのか」はこの窓でしか気にならない。
            # 直し方を同じ場所に書いておく
            where = QLabel(f"置き場所の初期値は {settings_file} で変えられます。", self)
            where.setWordWrap(True)
            where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(where)
        layout.addWidget(self.buttons)

        self.parent_dir.textChanged.connect(self._refresh)
        self.name.textChanged.connect(self._refresh)
        self._refresh()

        # 決めたいのは名前なので、開いた直後にそこへ入れる。既定では
        # 上から順に「置き場所」へ入り、打ち始めると置き場所が書き換わる
        self.name.setFocus()

    def _choose_parent(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "置き場所を選ぶ", self.parent_dir.text()
        )
        if folder:
            self.parent_dir.setText(str(pathlib.Path(folder)))

    def _refresh(self) -> None:
        """行き先の説明を出し、作れないうちは OK を押させない。

        押せてしまうと、窓が閉じたあとにエラーの窓が出ることになる。
        直す場所（名前の欄）が既に消えているので、打ち直しが遠くなる。
        """
        problem = self.problem()
        self.note.setText(problem or target_note(self.chosen_path()))
        # 断る理由と「上書きになる」は目立たせる。行き先の説明と同じ見た目だと、
        # 読み飛ばして そのまま押される
        warn = problem is not None or self.overwrites_project()
        self.note.setStyleSheet(f"color: {WARN_COLOR};" if warn else "")

        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(problem is None)

    def problem(self) -> str | None:
        parent = self.parent_dir.text().strip()
        if not parent:
            return "置き場所を選んでください"
        if not pathlib.Path(parent).is_dir():
            return f"置き場所が見つかりません: {parent}"
        return name_problem(self.name.text()) or target_problem(self.chosen_path())

    def chosen_path(self) -> pathlib.Path:
        return pathlib.Path(self.parent_dir.text().strip()) / self.name.text().strip()

    def overwrites_project(self) -> bool:
        """既にある作品を上書きするか。呼ぶ側が確認を出す。"""
        return is_project_dir(self.chosen_path())
