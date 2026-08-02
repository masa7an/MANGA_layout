"""漫画レイアウタ（MANGA_layout）のデータモデルと保存処理。

この層は Qt に依存しない。画面まわりを持ち込まないことで、
テストが画面なしで動き、あとから UI を差し替えても影響が及ばない。

**`manga_layout.images` はここから公開しない。** 画像の展開に Qt が要るため、
ここに載せると `import manga_layout` だけで PySide6 を引き込むことになる。
使う側が `from manga_layout.images import ...` と明示して取る。
"""

from .assets import AssetStore, PendingAssets, sniff_format
from .errors import (
    AssetError,
    BrokenImageError,
    MangaLayoutError,
    ProjectFormatError,
    ProjectNotFoundError,
    UnknownImageFormatError,
    UnsupportedVersionError,
)
from .geometry import Polygon, Rect, Size, fit_rect, fit_size
from .history import History
from .model import (
    APP_NAME,
    FORMAT_VERSION,
    PAGE_SIZES,
    BalloonObject,
    Border,
    Font,
    ImageObject,
    Page,
    Panel,
    Project,
    SlantPair,
    Tail,
    TextObject,
    new_project,
)
from .storage import (
    find_missing_assets,
    is_project_dir,
    load_project,
    prune_unused_assets,
    save_project,
)

__all__ = [
    "APP_NAME",
    "FORMAT_VERSION",
    "PAGE_SIZES",
    "AssetError",
    "AssetStore",
    "BalloonObject",
    "Border",
    "BrokenImageError",
    "Font",
    "PendingAssets",
    "History",
    "ImageObject",
    "MangaLayoutError",
    "Page",
    "Panel",
    "Polygon",
    "Project",
    "ProjectFormatError",
    "ProjectNotFoundError",
    "Rect",
    "SlantPair",
    "Size",
    "Tail",
    "TextObject",
    "UnknownImageFormatError",
    "UnsupportedVersionError",
    "find_missing_assets",
    "fit_rect",
    "fit_size",
    "is_project_dir",
    "load_project",
    "new_project",
    "prune_unused_assets",
    "save_project",
    "sniff_format",
]
