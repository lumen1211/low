import os, sys, types, asyncio
from pathlib import Path

# ensure project root on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

def _stub_gui_deps():
    """Provide minimal PySide6 and other dependencies for importing src.gui."""
    if 'PySide6' in sys.modules:
        return

    widgets = types.ModuleType('PySide6.QtWidgets')
    core = types.ModuleType('PySide6.QtCore')

    class QMainWindow:
        def __init__(self):
            pass
        def setWindowTitle(self, *a):
            pass
        def resize(self, *a):
            pass
        def setCentralWidget(self, w):
            pass
    class QWidget:
        def __init__(self, *a, **kw):
            pass
    class QVBoxLayout:
        def __init__(self, parent=None):
            pass
        def addWidget(self, w):
            pass
        def addLayout(self, l):
            pass
    class QHBoxLayout:
        def __init__(self):
            pass
        def addWidget(self, w):
            pass
        def addStretch(self, s):
            pass
    class QLabel:
        def __init__(self, text=""):
            self._text = text
        def setText(self, t):
            self._text = t
        def text(self):
            return self._text
    class QPushButton:
        def __init__(self, text=""):
            self.clicked = types.SimpleNamespace(connect=lambda fn: None)
    class QTableWidgetItem:
        def __init__(self, text=""):
            self._text = str(text)
        def setText(self, t):
            self._text = str(t)
        def text(self):
            return self._text
    class QHeaderView:
        Stretch = 0
        def setSectionResizeMode(self, *a):
            pass
    class QTableWidget:
        def __init__(self, rows, cols):
            self.cols = cols
            self._data = [[QTableWidgetItem("") for _ in range(cols)] for _ in range(rows)]
        def setHorizontalHeaderLabels(self, labels):
            pass
        def horizontalHeader(self):
            return QHeaderView()
        def setRowCount(self, n):
            if n == 0:
                self._data = []
        def rowCount(self):
            return len(self._data)
        def insertRow(self, r):
            self._data.insert(r, [QTableWidgetItem("") for _ in range(self.cols)])
        def setItem(self, r, c, item):
            self._data[r][c] = item
        def item(self, r, c):
            return self._data[r][c]
        def removeRow(self, r):
            del self._data[r]
    class QTextEdit:
        def __init__(self):
            self.lines = []
        def setReadOnly(self, flag):
            pass
        def append(self, s):
            self.lines.append(s)
        def toPlainText(self):
            return "\n".join(self.lines)
    widgets.QMainWindow = QMainWindow
    widgets.QWidget = QWidget
    widgets.QVBoxLayout = QVBoxLayout
    widgets.QHBoxLayout = QHBoxLayout
    widgets.QLabel = QLabel
    widgets.QPushButton = QPushButton
    widgets.QTableWidget = QTableWidget
    widgets.QTableWidgetItem = QTableWidgetItem
    widgets.QHeaderView = QHeaderView
    widgets.QTextEdit = QTextEdit

    class QTimer:
        def __init__(self, parent=None):
            self.timeout = types.SimpleNamespace(connect=lambda fn: None)
        def setInterval(self, i):
            pass
        def start(self):
            pass
    core.QTimer = QTimer

    sys.modules['PySide6'] = types.ModuleType('PySide6')
    sys.modules['PySide6.QtWidgets'] = widgets
    sys.modules['PySide6.QtCore'] = core

    # Stub requests module used by gui.py
    sys.modules.setdefault('requests', types.ModuleType('requests'))

    # Stub onboarding_webview to avoid heavy deps
    onb = types.ModuleType('src.onboarding_webview')
    class WebOnboarding:
        pass
    class Account:
        pass
    onb.WebOnboarding = WebOnboarding
    onb.Account = Account
    sys.modules['src.onboarding_webview'] = onb

_stub_gui_deps()

from src.gui import MainWindow


def _run_feeder_once(mw, message):
    async def _inner():
        await mw.queue.put(message)
        await asyncio.sleep(0.01)
    mw.loop.run_until_complete(_inner())


def test_feeder_updates_progress_and_claim(tmp_path):
    mw = MainWindow(Path('accounts.csv'))
    logs = []
    mw.log_line = lambda s: logs.append(s)

    _run_feeder_once(mw, ("user1", "progress", {"pct": 33.3, "remain": 5}))
    row = mw.row_of("user1")
    assert mw.tbl.item(row, 5).text() == "33%"
    assert mw.tbl.item(row, 6).text() == "5"

    _run_feeder_once(mw, ("user1", "claimed", {"at": "time", "drop": "Drop"}))
    assert mw.tbl.item(row, 7).text() == "time"
    assert mw.metrics["claimed"] == 1
    assert logs[-1] == "[user1] Claimed Drop"

    # cancel background feeder task to avoid warnings
    mw._feeder_task.cancel()
    try:
        mw.loop.run_until_complete(mw._feeder_task)
    except BaseException:
        pass
