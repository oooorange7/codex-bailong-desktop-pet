"""Windows UI fonts with pixel-aligned rendering for small desktop text."""
import os
import sys
from functools import lru_cache
from pathlib import Path
from PySide6.QtGui import QFont,QFontDatabase,QGuiApplication

LATIN_FAMILY='Microsoft YaHei UI'
FONT_FAMILIES=[LATIN_FAMILY,'Segoe UI','Microsoft YaHei','sans-serif']
FONT_CSS='font-family:'+','.join('"'+name+'"' for name in FONT_FAMILIES)+';font-weight:400;'
ROOT=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
FONT_FILE=ROOT/'app/fonts/ChakraPetch-Regular.ttf'
_font_id=None
_font_loaded=False


def register_font():
    global _font_id,_font_loaded
    if _font_id is None and QGuiApplication.instance() is not None:
        # Explicit registration also keeps Chinese fallback available in Qt's
        # offscreen renderer, where system font discovery is unavailable.
        system_fonts=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
        for name in ('msyh.ttc','msyhbd.ttc'):
            if (system_fonts/name).is_file():QFontDatabase.addApplicationFont(str(system_fonts/name))
        _font_id=QFontDatabase.addApplicationFont(str(FONT_FILE))
        _font_loaded=LATIN_FAMILY in QFontDatabase.families()
    return _font_loaded


def ui_font(px,bold=False):
    register_font()
    return QFont(_font_template(px,bold))


@lru_cache(maxsize=128)
def _font_template(px,bold):
    value=QFont();value.setFamilies(FONT_FAMILIES);value.setPixelSize(px)
    value.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    # Let Windows use the font's screen-optimized outlines and snap stems to
    # physical pixels at both integral and fractional display scale factors.
    value.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    value.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return value


def install_ui_font(app):
    register_font()
    value=ui_font(13)
    app.setFont(value)
