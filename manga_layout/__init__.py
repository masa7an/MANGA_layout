"""漫画レイアウタ（MANGA_layout）のデータモデルと保存処理。

この層は Qt に依存しない。画面まわりを持ち込まないことで、
テストが画面なしで動き、あとから UI を差し替えても影響が及ばない。
"""

from .assets import AssetStore, sniff_format
from .errors import (
    AssetError,
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
    "Font",
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
