"""face-widget: Halden's face as a desktop widget.

A frameless, always-on-top, genuinely transparent window that shows an
ai-visualizer face over the desktop. It is not a second visualizer: it
loads a face from the running ai-visualizer server, so it rides exactly
the same signal bus the browser faces ride and idles, listens, thinks
and speaks in lockstep with the voice line.

Why PySide6 rather than something lighter. WebView2 is already on this
machine and pywebview can host it in three lines, but its Windows host
is a WinForms form that paints an opaque background behind the browser,
and that background cannot be removed: TransparencyKey is ignored
because WebView2 composites through DirectComposition. Measured, not
assumed, on 2026-08-27. Qt renders the page into a surface it owns, so
WA_TranslucentBackground has something to act on and the transparency
is real per-pixel alpha. The cost is that QtWebEngine carries its own
Chromium.

There is no title bar, so the ways out are the tray icon and a
right-click on the widget itself.
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import (Qt, QEvent, QLockFile, QPoint,
                            QTimer, QUrl)
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QMenu, QMessageBox,
                               QSystemTrayIcon, QVBoxLayout, QWidget)
from PySide6.QtWebEngineWidgets import QWebEngineView

HERE = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("FACE_WIDGET_CONFIG")
                   or (HERE / "face-widget.json"))
LOCK_PATH = HERE / ".widget.lock"
LOG_PATH = HERE / "widget.log"


def log(msg):
    """run.bat launches this under pythonw, which has no console, so a
    print goes nowhere. Everything worth reading afterwards goes here."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def install_crash_handler():
    """Without a console an unhandled exception is a window that simply
    never appears and no explanation anywhere. Write the traceback down,
    then say so out loud if Qt is still healthy enough to manage it."""
    import traceback

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log("CRASH\n" + text)
        try:
            QMessageBox.critical(None, "face-widget",
                                 f"The face widget stopped with an error.\n\n"
                                 f"{exc_type.__name__}: {exc}\n\n"
                                 f"Full details: {LOG_PATH}")
        except Exception:
            pass

    sys.excepthook = hook


DEFAULTS = {
    # The ai-visualizer checkout that serves the faces. Its server.py is
    # standard library only, so this venv's python can run it.
    "visualizer_dir": str((HERE.parent / "ai-visualizer").resolve()),
    # Which face to float. Must be a folder under the visualizer's faces/.
    # radial-widget is the background-stripped variant; the stock faces
    # all paint a full-bleed backdrop and will show as a solid rectangle.
    "face": "radial-widget",
    "port": 8790,
    # Logical pixels, square. Qt works in logical pixels and Windows in
    # physical ones; at 125% scaling these differ by a quarter, which is
    # a good way to lose an afternoon.
    "size": 280,
    # null centres it near the bottom right on first run. Saved on move.
    # Negative values are legitimate: a monitor to the left of the primary.
    "pos_x": None,
    "pos_y": None,
    "always_on_top": True,
    # Start the visualizer server if nothing answers on the port.
    "autostart_server": True,
    # Whole-widget opacity, 0.15 to 1.0. Separate from the face's own
    # alpha: this dims the entire thing, glow included.
    "opacity": 1.0,
}

MIN_SIZE, MAX_SIZE = 120, 900


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except ValueError as e:
        log(f"[config] face-widget.json is not valid JSON ({e}), "
            f"using defaults")
    return cfg


def save_config(updates):
    """Read-modify-write, so hand-added keys survive. A file that fails to
    parse is left alone rather than replaced with a stub."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as e:
        log(f"[config] not writable/parsable, session only: {e}")
        return False
    data.update(updates)
    try:
        CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n",
                               encoding="utf-8")
    except OSError as e:
        log(f"[config] write failed: {e}")
        return False
    return True


def port_open(port, host="127.0.0.1", timeout=0.4):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def ensure_server(cfg):
    """Returns (ok, message). Starts the visualizer if nothing answers."""
    port = int(cfg["port"])
    if port_open(port):
        return True, "already running"
    if not cfg.get("autostart_server", True):
        return False, (f"nothing is listening on port {port} and autostart "
                       f"is turned off")
    vdir = Path(cfg["visualizer_dir"])
    server = vdir / "server.py"
    if not server.is_file():
        return False, f"no server.py at {server}"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen([sys.executable, str(server), "--no-open"],
                         cwd=str(vdir), creationflags=flags,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError as e:
        return False, f"could not start server.py: {e}"
    for _ in range(40):                      # up to about 8 seconds
        if port_open(port):
            return True, "started"
        time.sleep(0.2)
    return False, "server.py was started but never answered on the port"


_lock = None


def single_instance():
    """True if we are the only copy. Two widgets on one bus is not
    harmful, just confusing, and there is no taskbar entry to tell them
    apart.

    QLockFile rather than a hand-rolled pid file. The obvious version of
    this, reading a pid and probing it with os.kill(pid, 0), is actively
    destructive on Windows: CPython maps every signal except the two
    console events onto TerminateProcess, so the "probe" kills the very
    widget it was meant to detect, silently, with exit code 0. QLockFile
    does stale-lock detection with the right platform calls."""
    global _lock
    _lock = QLockFile(str(LOCK_PATH))
    _lock.setStaleLockTime(0)                # judge staleness by the pid
    return _lock.tryLock(100)


def ring_icon():
    """A tray icon drawn rather than shipped, so there is no asset file to
    lose track of."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(74, 240, 122), 6))
    p.drawEllipse(8, 8, 48, 48)
    p.setPen(QPen(QColor(160, 255, 195), 3))
    p.drawEllipse(20, 20, 24, 24)
    p.end()
    return QIcon(pm)


class FaceWidget(QWidget):
    def __init__(self, cfg):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if cfg.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.cfg = cfg
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(float(cfg.get("opacity", 1.0)))

        self.view = QWebEngineView(self)
        self.view.page().setBackgroundColor(Qt.transparent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)

        self._drag = None
        self._save = QTimer(self)
        self._save.setSingleShot(True)
        self._save.setInterval(800)          # debounce: dragging fires a lot
        self._save.timeout.connect(self._persist)

        size = max(MIN_SIZE, min(MAX_SIZE, int(cfg.get("size", 280))))
        # cfg["size"] is the INTENT: the size that was actually asked for,
        # by the config file, the wheel or the menu. Never Qt's current
        # opinion of the window, which changes on its own. See _persist.
        cfg["size"] = size
        self.resize(size, size)
        self._restore_position(size)
        self.load_face()

        # QWebEngineView keeps a private child widget that takes the mouse
        # first, so the drag has to be caught on that child, not on self.
        QTimer.singleShot(0, self._grab_mouse_events)

    # ---------------------------------------------------------------- setup
    def load_face(self):
        url = (f"http://127.0.0.1:{int(self.cfg['port'])}"
               f"/faces/{self.cfg['face']}/")
        self.view.load(QUrl(url))

    def _grab_mouse_events(self):
        proxy = self.view.focusProxy()
        if proxy is None:                    # not realised yet, try again
            QTimer.singleShot(50, self._grab_mouse_events)
            return
        proxy.installEventFilter(self)

    def _restore_position(self, size):
        x, y = self.cfg.get("pos_x"), self.cfg.get("pos_y")
        if x is None or y is None or not self._on_a_screen(int(x), int(y),
                                                           size):
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.right() - size - 40
            y = screen.bottom() - size - 40
        self.move(int(x), int(y))

    @staticmethod
    def _on_a_screen(x, y, size):
        """A saved position is only good if a screen still covers it.
        Unplug the left-hand monitor and yesterday's negative coordinates
        would park the widget somewhere unreachable."""
        probe = QPoint(int(x + size / 2), int(y + size / 2))
        return any(s.availableGeometry().contains(probe)
                   for s in QApplication.screens())

    # ---------------------------------------------------------------- input
    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            self._drag = (ev.globalPosition().toPoint()
                          - self.frameGeometry().topLeft())
            return True
        if t == QEvent.MouseMove and self._drag is not None:
            self.move(ev.globalPosition().toPoint() - self._drag)
            self._save.start()
            return True
        if t == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton:
            self._drag = None
            return True
        if t == QEvent.ContextMenu:
            self.show_menu(ev.globalPos())
            return True
        if t == QEvent.Wheel:
            # no frame to grab, so the wheel is the only way to resize
            step = 20 if ev.angleDelta().y() > 0 else -20
            self.apply_size(self.width() + step)
            return True
        return super().eventFilter(obj, ev)

    def apply_size(self, size):
        size = max(MIN_SIZE, min(MAX_SIZE, int(size)))
        self.cfg["size"] = size              # the intent, see _persist
        centre = self.geometry().center()
        self.resize(size, size)
        self.move(centre.x() - size // 2, centre.y() - size // 2)
        self._save.start()

    # ----------------------------------------------------------------- menu
    def show_menu(self, at):
        m = QMenu()
        top = QAction("Always on top", m, checkable=True)
        top.setChecked(bool(self.cfg.get("always_on_top", True)))
        top.triggered.connect(self.toggle_on_top)
        m.addAction(top)

        sizes = m.addMenu("Size")
        for label, px in (("Small (200)", 200), ("Medium (280)", 280),
                          ("Large (380)", 380), ("Extra large (500)", 500)):
            act = QAction(label, sizes)
            act.triggered.connect(lambda _=False, p=px: self.apply_size(p))
            sizes.addAction(act)

        fade = m.addMenu("Opacity")
        for label, o in (("100%", 1.0), ("75%", 0.75), ("50%", 0.5),
                         ("30%", 0.3)):
            act = QAction(label, fade)
            act.triggered.connect(lambda _=False, v=o: self.apply_opacity(v))
            fade.addAction(act)

        m.addSeparator()
        reset = QAction("Reset position", m)
        reset.triggered.connect(self.reset_position)
        m.addAction(reset)
        again = QAction("Reload face", m)
        again.triggered.connect(self.load_face)
        m.addAction(again)
        m.addSeparator()
        bye = QAction("Quit", m)
        bye.triggered.connect(QApplication.quit)
        m.addAction(bye)
        m.exec(at)

    def toggle_on_top(self, on):
        self.cfg["always_on_top"] = bool(on)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(on))
        self.show()                          # flag changes need a re-show
        self._save.start()

    def apply_opacity(self, value):
        self.cfg["opacity"] = float(value)
        self.setWindowOpacity(float(value))
        self._save.start()

    def reset_position(self):
        self.cfg["pos_x"] = self.cfg["pos_y"] = None
        self._restore_position(self.width())
        self._save.start()

    # ------------------------------------------------------------ lifecycle
    def _persist(self):
        # Clamp on the way out as well as on the way in. main() already
        # bounds the size when it loads, so a bad value never rendered as
        # anything worse than a maximum-size widget, but it stayed in the
        # file and came back every launch. A width of 12479 was found here
        # on 2026-08-27 and the geometry call that produced it was never
        # identified, so the defence is to refuse the value rather than to
        # trust the source. The log line exists to catch the culprit if it
        # happens again: nothing recorded it the first time.
        raw = int(self.cfg.get("size", 280))
        size = max(MIN_SIZE, min(MAX_SIZE, raw))
        if size != raw:
            log(f"geometry out of range on save: intent={raw}, "
                f"clamped to {size}")
        # Deliberately NOT self.width(). Qt reports width in logical
        # pixels for the screen the window is currently on, so dragging
        # between monitors at different device pixel ratios rescales it:
        # 1.25 -> 1.5 multiplies by .833, the other way by 1.2. Saving
        # that number made every screen crossing compound into the file.
        # Measured 2026-08-27: 450 -> 375 -> 312 -> 261 across three
        # crossings, and the 12479 found earlier is 280 grown by 1.2
        # about twenty times. The window changing size on a different
        # display is correct, it keeps the same physical size. Recording
        # that as the new intent was not.
        save_config({"pos_x": self.x(), "pos_y": self.y(),
                     "size": size,
                     "always_on_top": bool(self.cfg.get("always_on_top",
                                                        True)),
                     "opacity": float(self.cfg.get("opacity", 1.0))})

    def closeEvent(self, ev):
        self._persist()
        super().closeEvent(ev)


def main():
    cfg = load_config()
    for i, arg in enumerate(sys.argv):
        if arg == "--face" and i + 1 < len(sys.argv):
            cfg["face"] = sys.argv[i + 1]

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)     # the tray outlives the window
    install_crash_handler()
    log(f"starting: face={cfg['face']} port={cfg['port']} "
        f"size={cfg['size']}")

    if not single_instance():
        # --quiet is for callers that cannot know whether the widget is
        # already up: start.bat launches it on every voice-line start, and a
        # modal dialog there would be noise every single time. Launched by
        # hand the dialog still earns its place, because there is no window
        # to point at, only a tray icon.
        if "--quiet" in sys.argv:
            log("already running; nothing to do")
        else:
            QMessageBox.information(None, "face-widget",
                                    "The face widget is already running.\n"
                                    "Look for the ring in the system tray.")
        return 0

    ok, msg = ensure_server(cfg)
    log(f"visualizer: {msg}")
    if not ok:
        QMessageBox.critical(None, "face-widget",
                             f"Could not reach the visualizer.\n\n{msg}")
        return 1

    w = FaceWidget(cfg)
    w.show()

    tray = QSystemTrayIcon(ring_icon())
    tray.setToolTip("Halden face")
    tmenu = QMenu()
    act_show = QAction("Show", tmenu)
    act_show.triggered.connect(w.show)
    tmenu.addAction(act_show)
    act_hide = QAction("Hide", tmenu)
    act_hide.triggered.connect(w.hide)
    tmenu.addAction(act_hide)
    tmenu.addSeparator()
    act_quit = QAction("Quit", tmenu)
    act_quit.triggered.connect(QApplication.quit)
    tmenu.addAction(act_quit)
    tray.setContextMenu(tmenu)
    tray.activated.connect(
        lambda reason: w.setVisible(not w.isVisible())
        if reason == QSystemTrayIcon.Trigger else None)
    tray.show()

    app.aboutToQuit.connect(w._persist)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
