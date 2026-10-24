"""Collapse our own card on an outside click, preserving its saved mode.

Windows tool windows can remain inactive, so deactivation alone misses clicks
in another application. Poll only the left button state and Qt cursor position;
no global hook, key logging, saved mouse history, or intercepted input.
"""
import ctypes
import sys
from PySide6.QtCore import QObject,QEvent,QTimer,Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication


class OutsideClickWatcher(QObject):
    def __init__(self,bubble):
        super().__init__(bubble);self.bubble=bubble;self.down=False;self.key_state=None
        app=QApplication.instance();app.installEventFilter(self)
        if sys.platform=='win32' and app.platformName()=='windows':
            user=ctypes.WinDLL('user32',use_last_error=True)
            self.key_state=user.GetAsyncKeyState
            self.key_state.argtypes=[ctypes.c_int];self.key_state.restype=ctypes.c_short
            self.down=bool(self.key_state(1)&0x8000)
            self.timer=QTimer(self);self.timer.setInterval(40);self.timer.timeout.connect(self.poll);self.timer.start()

    def contains(self,point):
        b=self.bubble
        if b.frameGeometry().contains(point):return True
        app=QApplication.instance()
        for widget in (b.report,app.activePopupWidget(),app.activeModalWidget()):
            if widget is not None and widget.isVisible() and widget.frameGeometry().contains(point):return True
        return False

    def click_at(self,point):
        b=self.bubble
        if not b.isVisible() or b.is_compact() or b._card_press is not None:return False
        if self.contains(point):return False
        # Run after the current input event and leave that click untouched.
        QTimer.singleShot(0,b,b.collapse_from_outside)
        return True

    def poll(self):
        pressed=bool(self.key_state(1)&0x8000)
        if pressed and not self.down:self.click_at(QCursor.pos())
        self.down=pressed

    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
            self.click_at(event.globalPosition().toPoint())
        return False
