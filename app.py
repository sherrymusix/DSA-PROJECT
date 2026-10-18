"""
Mobile Phone Scraper & Sorting Visualizer  (CS200 - Project 1)
Run:  python app.py        (needs: PyQt6 or PyQt5, requests, beautifulsoup4)
"""
import os
import sys
import threading
import time
from collections import deque

try:
    from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex, QThread, pyqtSignal
    from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                                 QGridLayout, QTabWidget, QPushButton, QComboBox, QLabel,
                                 QLineEdit, QSpinBox, QDoubleSpinBox, QProgressBar, QTableView, QHeaderView,
                                 QAbstractItemView, QMessageBox, QFileDialog, QDialog,
                                 QTableWidget, QTableWidgetItem, QGroupBox)
except ImportError:                                    # fall back to PyQt5
    from PyQt5.QtCore import Qt, QAbstractTableModel, QModelIndex, QThread, pyqtSignal
    from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                                 QGridLayout, QTabWidget, QPushButton, QComboBox, QLabel,
                                 QLineEdit, QSpinBox, QDoubleSpinBox, QProgressBar, QTableView, QHeaderView,
                                 QAbstractItemView, QMessageBox, QFileDialog, QDialog,
                                 QTableWidget, QTableWidgetItem, QGroupBox)

import core
from core import (DataStore, SORTERS, SEARCHERS, STR_OPS, NUM_OPS, CONNECTORS,
                  CONNECTORS_FIRST, GSM_COLUMNS, GSM_MAKERS, fmt)

AUTO_CSV = "mobile_phones.csv"
YES = QMessageBox.StandardButton.Yes
NO = QMessageBox.StandardButton.No


# =====================================================================
#  Background threads
# =====================================================================
class ScrapeThread(QThread):
    """Real web scraping with pause / resume / stop.
    mode 'gsm' : GSMArena - every brand's listing pages (round-robin over brands)
    mode 'url' : any page(s) containing an HTML table (optional {page} in the URL)"""
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished_ok = pyqtSignal(list, list, bool)      # columns, rows, was_stopped
    failed = pyqtSignal(str)

    HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 CS200-student-project",
               "Accept-Language": "en"}

    def __init__(self, mode, amount, url="", delay=1.5):
        super().__init__()
        self.mode, self.amount, self.url, self.delay = mode, amount, url, delay
        self.note = ""
        self._run = threading.Event()
        self._run.set()
        self._stop = False
        self._rows, self._columns = [], None

    def pause(self):
        self._run.clear()

    def resume(self):
        self._run.set()

    def stop(self):
        self._stop = True
        self._run.set()

    def _checkpoint(self):
        self._run.wait()                 # blocks while paused
        return not self._stop

    def _sleep(self, secs):
        end = time.time() + secs
        while time.time() < end:
            if not self._checkpoint():
                return False
            time.sleep(0.2)
        return True

    def _get(self, session, url):
        """GET with polite back-off when the server says 'slow down'."""
        for attempt in range(6):
            if not self._checkpoint():
                return None
            r = session.get(url, timeout=25)
            if r.status_code in (403, 429, 503):
                wait = min(300, 30 * 2 ** attempt)
                self.status.emit(f"Server asked us to slow down (HTTP {r.status_code}) - "
                                 f"waiting {wait}s then retrying ...")
                if not self._sleep(wait):
                    return None
                continue
            r.raise_for_status()
            return r.content.decode("utf-8", "replace")
        raise RuntimeError("The website keeps blocking requests (HTTP 403/429). "
                           "Try again later or increase the delay.")

    def run(self):
        try:
            if self.mode == "gsm":
                self._run_gsm()
            else:
                self._run_url()
        except Exception as e:           # network errors, bad HTML ...
            msg = f"{type(e).__name__}: {e}"
            if self._rows:               # keep what we already have
                self.note = f"Scraping interrupted ({msg}). Partial data kept."
                self.finished_ok.emit(self._columns, self._rows, True)
            else:
                self.failed.emit(msg)
            return
        if not self._rows:
            self.failed.emit("No data could be scraped from that source.")
        else:
            self.finished_ok.emit(self._columns, self._rows, self._stop)

    def _run_gsm(self):
        import requests
        self._columns = GSM_COLUMNS
        s = requests.Session()
        s.headers.update(self.HEADERS)
        self.status.emit("Downloading brand list ...")
        html = self._get(s, GSM_MAKERS)
        if html is None:
            return
        brands = core.parse_brands(html)
        if not brands:
            raise RuntimeError("Could not find any brands on makers.php3 (site layout changed?).")
        queue = deque((name, url) for name, url, _ in brands)    # round-robin => mixed brands
        seen = set()
        while queue and len(self._rows) < self.amount:
            if not self._sleep(self.delay):
                break
            brand, url = queue.popleft()
            self.status.emit(f"Scraping {brand}  ({len(self._rows):,} phones so far, "
                             f"{len(queue) + 1} brand queues left)")
            html = self._get(s, url)
            if html is None:
                break
            rows, nxt = core.parse_listing(html, brand, url)
            for r in rows:
                if r[0] not in seen and len(self._rows) < self.amount:
                    seen.add(r[0])
                    self._rows.append(r)
            if nxt:
                queue.append((brand, nxt))
            self.progress.emit(len(self._rows))

    def _run_url(self):
        import requests
        from bs4 import BeautifulSoup
        s = requests.Session()
        s.headers.update(self.HEADERS)
        page, paged = 1, "{page}" in self.url
        while len(self._rows) < self.amount:
            u = self.url.replace("{page}", str(page))
            self.status.emit(f"Fetching {u}")
            html = self._get(s, u)
            if html is None:
                break
            soup = BeautifulSoup(html, "html.parser")
            tables = soup.find_all("table")
            if not tables:
                break
            table = max(tables, key=lambda t: len(t.find_all("tr")))
            new = 0
            for tr in table.find_all("tr"):
                ths, tds = tr.find_all("th"), tr.find_all("td")
                if ths and not tds:
                    if self._columns is None:
                        self._columns = [t.get_text(" ", strip=True) or f"Column {i + 1}"
                                         for i, t in enumerate(ths)]
                    continue
                if not tds:
                    continue
                cells = [td.get_text(" ", strip=True) for td in tds]
                if self._columns is None:
                    self._columns = [f"Column {i + 1}" for i in range(len(cells))]
                self._rows.append((cells + [""] * len(self._columns))[:len(self._columns)])
                new += 1
                if len(self._rows) >= self.amount:
                    break
            self.progress.emit(len(self._rows))
            if new == 0 or not paged:
                break
            page += 1
            if not self._sleep(self.delay):
                break


class TaskThread(QThread):
    """Runs fn(progress_callback) off the GUI thread (sorting / benchmark)."""
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.progress.emit))
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


# =====================================================================
#  Table model (fast even with 15k+ rows)
# =====================================================================
class PhoneModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.store, self.view, self.marker = None, [], None

    def set_data(self, store, view):
        self.beginResetModel()
        self.store, self.view, self.marker = store, view, None
        self.endResetModel()

    def set_view(self, view, marker=None):
        self.beginResetModel()
        self.view = view
        self.marker = marker
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self.view)

    def columnCount(self, parent=QModelIndex()):
        return len(self.store.columns) if self.store else 0

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return fmt(self.store.rows[self.view[index.row()]][index.column()])
        if role == Qt.ItemDataRole.TextAlignmentRole and self.store.numeric[index.column()]:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            name = self.store.columns[section]
            if self.marker and self.marker[0] == section:
                name += "  ▼" if self.marker[1] else "  ▲"
            return name
        return str(section + 1)


# =====================================================================
#  One filter condition row:  [AND/OR/NOT] [column] [operator] [value] [search algo]
# =====================================================================
class ConditionRow(QWidget):
    def __init__(self, first, columns, numeric):
        super().__init__()
        self.numeric = numeric
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.conn = QComboBox()
        self.conn.addItems(CONNECTORS_FIRST if first else CONNECTORS)
        self.col = QComboBox()
        self.col.addItems(columns)
        self.op = QComboBox()
        self.value = QLineEdit()
        self.value.setPlaceholderText("value  (between: 200,500)")
        self.algo = QComboBox()
        self.algo.addItems(SEARCHERS)
        self.algo.setToolTip("Search algorithm - used for exact-match conditions (= / equals)")
        for w, s in ((self.conn, 0), (self.col, 1), (self.op, 0), (self.value, 2), (self.algo, 0)):
            lay.addWidget(w, s)
        self.col.currentIndexChanged.connect(self._fill_ops)
        self.op.currentTextChanged.connect(self._algo_state)
        self._fill_ops()

    def _fill_ops(self):
        self.op.clear()
        self.op.addItems(NUM_OPS if self.numeric[self.col.currentIndex()] else STR_OPS)

    def _algo_state(self, text):
        self.algo.setEnabled(text in ("=", "equals"))

    def condition(self):
        conn = self.conn.currentText()
        return dict(connector="" if conn == "(none)" else conn, col=self.col.currentIndex(),
                    op=self.op.currentText(), value=self.value.text(),
                    algo=self.algo.currentText())


# =====================================================================
#  Benchmark result window
# =====================================================================
class BenchmarkDialog(QDialog):
    def __init__(self, parent, results, column_name):
        super().__init__(parent)
        self.setWindowTitle("Sorting Benchmark")
        self.resize(900, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"All algorithms on column '{column_name}' - "
                             f"{results[0]['rows']:,} rows (same data for each)"))
        heads = ["Algorithm", "Time complexity", "Stable?", "Time (ms)", "Comparisons", "Swaps / moves"]
        t = QTableWidget(len(results), len(heads))
        t.setHorizontalHeaderLabels(heads)
        fastest = min(r["ms"] for r in results)
        for i, r in enumerate(results):
            vals = [r["name"], r["big_o"], "Stable" if r["stable"] else "Unstable",
                    f"{r['ms']:.3f}", f"{r['comparisons']:,}", f"{r['swaps']:,}"]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if j >= 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if r["ms"] == fastest:
                    f = item.font(); f.setBold(True); item.setFont(f)
                t.setItem(i, j, item)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(t)
        lay.addWidget(QLabel("Bold row = fastest."))
        b = QPushButton("Close")
        b.clicked.connect(self.accept)
        lay.addWidget(b)


# =====================================================================
#  Main window
# =====================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mobile Phone Scraper & Sorting Visualizer")
        self.resize(1250, 800)
        self.store = None
        self.order, self.view, self.fs = [], [], None   # base order, shown rows, filter set
        self.scraper, self.task = None, None
        self.cond_rows = []
        self.model = PhoneModel()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_scrape_tab(), "1. Scrape / Data")
        self.tabs.addTab(self._build_sort_tab(), "2. Sorting")
        self.tabs.addTab(self._build_filter_tab(), "3. Search && Filter")
        self.tabs.setMaximumHeight(235)
        root.addWidget(self.tabs)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(24)
        hh = self.table.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.sectionClicked.connect(self.on_header_click)
        root.addWidget(self.table, 1)

        self.metrics = QLabel("Metrics: -")
        self.metrics.setStyleSheet("font-weight:bold; padding:4px;")
        root.addWidget(self.metrics)
        self.statusBar().showMessage("Ready - choose how many devices to scrape and press 'Start Scraping' (or load a CSV).")
        self.algo_info()

    # ------------------------------------------------------------ tabs
    def _build_scrape_tab(self):
        w = QWidget()
        g = QGridLayout(w)
        self.source = QComboBox()
        self.source.addItems(["GSMArena.com - all brands (real website)", "Custom URL (any page with an HTML table)"])
        self.source.currentIndexChanged.connect(
            lambda i: self.url.setEnabled(i == 1))
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://example.com/phones?page={page}   ({page} is optional)")
        self.url.setEnabled(False)
        self.amount = QSpinBox()
        self.amount.setRange(1, 100000)
        self.amount.setValue(15000)
        self.amount.setSingleStep(1000)
        self.amount.setGroupSeparatorShown(True)

        self.delay = QDoubleSpinBox()
        self.delay.setRange(0.5, 30.0)
        self.delay.setSingleStep(0.5)
        self.delay.setValue(1.5)
        self.delay.setSuffix(" s")
        self.delay.setToolTip("Pause between requests - be polite to the website")

        self.btn_start = QPushButton("▶ Start Scraping")
        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_stop = QPushButton("⏹ Stop")
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self.start_scrape)
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_stop.clicked.connect(self.stop_scrape)

        self.progress = QProgressBar()
        self.progress.setFormat("%v / %m devices  (%p%)")
        self.btn_load = QPushButton("📂 Load CSV")
        self.btn_export = QPushButton("💾 Export current table to CSV")
        self.btn_load.clicked.connect(self.load_csv)
        self.btn_export.clicked.connect(self.export_csv)

        g.addWidget(QLabel("Source:"), 0, 0); g.addWidget(self.source, 0, 1)
        g.addWidget(QLabel("Number of phones:"), 0, 2); g.addWidget(self.amount, 0, 3)
        g.addWidget(QLabel("URL (custom mode):"), 1, 0); g.addWidget(self.url, 1, 1)
        g.addWidget(QLabel("Delay between requests:"), 1, 2); g.addWidget(self.delay, 1, 3)
        row = QHBoxLayout()
        for b in (self.btn_start, self.btn_pause, self.btn_stop):
            row.addWidget(b)
        g.addLayout(row, 2, 0, 1, 4)
        g.addWidget(self.progress, 3, 0, 1, 4)
        row2 = QHBoxLayout()
        row2.addWidget(self.btn_load); row2.addWidget(self.btn_export)
        g.addLayout(row2, 4, 0, 1, 4)
        g.setColumnStretch(1, 1)
        return w

    def _build_sort_tab(self):
        w = QWidget()
        g = QGridLayout(w)
        self.lvl_col, self.lvl_dir = [], []
        for k in range(3):
            c, d = QComboBox(), QComboBox()
            d.addItems(["Ascending ▲", "Descending ▼"])
            self.lvl_col.append(c); self.lvl_dir.append(d)
            g.addWidget(QLabel(f"{'Sort by' if k == 0 else 'Then by'} (level {k + 1}):"), k, 0)
            g.addWidget(c, k, 1); g.addWidget(d, k, 2)

        self.algo = QComboBox()
        self.algo.addItems(list(SORTERS))
        self.algo.setCurrentText("Merge Sort")
        self.algo.currentTextChanged.connect(self.algo_info)
        self.algo_label = QLabel()
        self.scope = QComboBox()
        self.scope.addItems(["Full dataset", "Only currently filtered results"])
        self.bench_n = QSpinBox()
        self.bench_n.setRange(100, 100000)
        self.bench_n.setValue(3000)
        self.bench_n.setSingleStep(500)
        self.bench_n.setToolTip("Max rows used by the benchmark (slow O(n²) algorithms need a limit)")

        g.addWidget(QLabel("Algorithm:"), 0, 3); g.addWidget(self.algo, 0, 4)
        g.addWidget(self.algo_label, 1, 3, 1, 2)
        g.addWidget(QLabel("Sort scope:"), 2, 3); g.addWidget(self.scope, 2, 4)

        self.btn_sort = QPushButton("⇅ Sort")
        self.btn_reset = QPushButton("Reset order")
        self.btn_bench = QPushButton("📊 Benchmark all algorithms")
        self.btn_sort.clicked.connect(lambda: self.do_sort(self.get_levels()))
        self.btn_reset.clicked.connect(self.reset_order)
        self.btn_bench.clicked.connect(self.do_benchmark)
        g.addWidget(self.btn_sort, 3, 0, 1, 2)
        g.addWidget(self.btn_reset, 3, 2)
        g.addWidget(self.btn_bench, 3, 3)
        row = QHBoxLayout(); row.addWidget(QLabel("max rows:")); row.addWidget(self.bench_n)
        g.addLayout(row, 3, 4)
        g.setColumnStretch(1, 1); g.setColumnStretch(4, 1)
        return w

    def _build_filter_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.cond_box = QVBoxLayout()
        lay.addLayout(self.cond_box)
        row = QHBoxLayout()
        self.btn_add = QPushButton("+ Add condition")
        self.btn_rem = QPushButton("− Remove last")
        self.btn_apply = QPushButton("🔍 Apply filter")
        self.btn_clear = QPushButton("Clear filter")
        self.btn_add.clicked.connect(self.add_condition)
        self.btn_rem.clicked.connect(self.remove_condition)
        self.btn_apply.clicked.connect(self.apply_filter)
        self.btn_clear.clicked.connect(self.clear_filter)
        for b in (self.btn_add, self.btn_rem, self.btn_apply, self.btn_clear):
            row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(QLabel("Conditions are evaluated left-to-right: "
                             "((c1 AND c2) OR NOT c3) ...  Search algorithm applies to exact matches."))
        lay.addStretch()
        return w

    # ------------------------------------------------------------ helpers
    def algo_info(self, *_):
        info = SORTERS[self.algo.currentText()]
        self.algo_label.setText(f"{info['big_o']}  ·  {'Stable' if info['stable'] else 'Unstable'}")

    def set_scraping_ui(self, scraping):
        self.btn_start.setEnabled(not scraping)
        self.btn_pause.setEnabled(scraping)
        self.btn_stop.setEnabled(scraping)
        self.btn_load.setEnabled(not scraping)
        self.btn_export.setEnabled(not scraping)
        self.source.setEnabled(not scraping)
        self.amount.setEnabled(not scraping)
        self.delay.setEnabled(not scraping)
        self.tabs.setTabEnabled(1, not scraping)
        self.tabs.setTabEnabled(2, not scraping)
        if not scraping:
            self.btn_pause.setText("⏸ Pause")

    def set_busy(self, busy):
        for i in range(3):
            self.tabs.setTabEnabled(i, not busy)

    def warn(self, text):
        QMessageBox.warning(self, "Notice", text)

    # ------------------------------------------------------------ scraping
    def start_scrape(self):
        mode = "gsm" if self.source.currentIndex() == 0 else "url"
        url = self.url.text().strip()
        if mode == "url" and not url.startswith("http"):
            return self.warn("Enter a full URL starting with http:// or https://")
        n = self.amount.value()
        self.progress.setMaximum(n)
        self.progress.setValue(0)
        self.scraper = ScrapeThread(mode, n, url, self.delay.value())
        self.scraper.progress.connect(self.progress.setValue)
        self.scraper.status.connect(self.statusBar().showMessage)
        self.scraper.finished_ok.connect(self.scrape_done)
        self.scraper.failed.connect(self.scrape_failed)
        self.set_scraping_ui(True)
        self.statusBar().showMessage("Scraping ...")
        self.scraper.start()

    def toggle_pause(self):
        if self.btn_pause.text().startswith("⏸"):
            self.scraper.pause()
            self.btn_pause.setText("▶ Resume")
            self.statusBar().showMessage("Paused.")
        else:
            self.scraper.resume()
            self.btn_pause.setText("⏸ Pause")
            self.statusBar().showMessage("Scraping ...")

    def stop_scrape(self):
        if self.scraper:
            self.scraper.stop()

    def scrape_failed(self, msg):
        self.set_scraping_ui(False)
        self.statusBar().showMessage("Scraping failed.")
        self.warn(msg)

    def scrape_done(self, columns, rows, stopped):
        self.set_scraping_ui(False)
        if not rows:
            self.statusBar().showMessage("Stopped - nothing was scraped.")
            return
        self.set_dataset(columns, rows)
        try:
            core.save_csv(AUTO_CSV, self.store.columns, self.store.rows)
            saved = f"  Saved to {os.path.abspath(AUTO_CSV)}"
        except OSError as e:
            saved = f"  (could not auto-save CSV: {e})"
        self.statusBar().showMessage(
            f"{'Stopped early' if stopped else 'Done'}: {len(rows):,} devices loaded.{saved}")
        if self.scraper and self.scraper.note:
            self.warn(self.scraper.note)

    def load_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV files (*.csv)")
        if not path:
            return
        try:
            cols, rows = core.load_csv(path)
        except Exception as e:
            return self.warn(f"Could not read CSV:\n{e}")
        self.set_dataset(cols, rows)
        self.progress.setMaximum(max(1, len(rows)))
        self.progress.setValue(len(rows))
        self.statusBar().showMessage(f"Loaded {len(rows):,} rows from {path}")

    def export_csv(self):
        if not self.store:
            return self.warn("No data to export yet.")
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "phones_view.csv", "CSV files (*.csv)")
        if path:
            core.save_csv(path, self.store.columns, (self.store.rows[i] for i in self.view))
            self.statusBar().showMessage(f"Exported {len(self.view):,} rows (current order/filter) to {path}")

    # ------------------------------------------------------------ dataset
    def set_dataset(self, columns, rows):
        self.store = DataStore(columns, rows)
        self.order = list(range(len(self.store)))
        self.fs = None
        self.view = self.order[:]
        self.model.set_data(self.store, self.view)
        self.table.resizeColumnsToContents()
        for k, c in enumerate(self.lvl_col):
            c.clear()
            if k > 0:
                c.addItem("(none)")
            c.addItems(self.store.columns)
        for r in self.cond_rows:
            r.setParent(None)
        self.cond_rows = []
        self.add_condition()
        self.metrics.setText(f"Metrics: - ({len(rows):,} rows loaded)")

    def refresh_view(self, marker=None):
        if self.fs is None:
            self.view = self.order[:]
        else:
            fs = self.fs
            self.view = [i for i in self.order if i in fs]
        self.model.set_view(self.view, marker)

    # ------------------------------------------------------------ sorting
    def get_levels(self):
        levels = []
        for k in range(3):
            idx = self.lvl_col[k].currentIndex()
            if k > 0:
                idx -= 1                                   # '(none)' offset
            if idx < 0 or any(idx == c for c, _ in levels):
                continue
            levels.append((idx, self.lvl_dir[k].currentIndex() == 1))
        return levels

    def on_header_click(self, section):
        if not self.store or not self.tabs.isTabEnabled(1):
            return
        desc = False
        m = self.model.marker
        if m and m[0] == section:
            desc = not m[1]
        self.lvl_col[0].setCurrentIndex(section)
        self.lvl_dir[0].setCurrentIndex(1 if desc else 0)
        self.do_sort([(section, desc)])

    def do_sort(self, levels):
        if not self.store:
            return self.warn("Scrape or load some data first.")
        if not levels:
            return self.warn("Choose a column to sort by.")
        algo = self.algo.currentText()
        filtered_only = self.scope.currentIndex() == 1 and self.fs is not None
        indices = list(self.view if filtered_only else self.order)
        if SORTERS[algo]["quadratic"] and len(indices) > 5000:
            ans = QMessageBox.question(
                self, "Slow algorithm",
                f"{algo} is {SORTERS[algo]['big_o']} and you are sorting {len(indices):,} rows.\n"
                f"This may take a while (the window stays responsive). Continue?", YES | NO)
            if ans != YES:
                return
        store = self.store
        self.task = TaskThread(lambda progress: core.run_sort(store, indices, levels, algo))
        self.task.progress.connect(self.statusBar().showMessage)
        self.task.done.connect(lambda res: self.sort_done(res, levels, algo, filtered_only))
        self.task.failed.connect(self.task_failed)
        self.set_busy(True)
        self.statusBar().showMessage(f"Sorting {len(indices):,} rows with {algo} ...")
        self.task.start()

    def sort_done(self, res, levels, algo, filtered_only):
        ordered, ms, comps, swaps = res
        self.set_busy(False)
        marker = (levels[0][0], levels[0][1]) if len(levels) == 1 else None
        if filtered_only:
            self.view = ordered
            self.model.set_view(self.view, marker)
        else:
            self.order = ordered
            self.refresh_view(marker)
        info = SORTERS[algo]
        desc = " → ".join(f"{self.store.columns[c]} {'▼' if d else '▲'}" for c, d in levels)
        self.metrics.setText(
            f"{algo}  |  {desc}  |  {len(ordered):,} rows  |  ⏱ {ms:.3f} ms  |  "
            f"comparisons: {comps:,}  |  swaps/moves: {swaps:,}  |  "
            f"{info['big_o']}, {'stable' if info['stable'] else 'unstable'}")
        self.statusBar().showMessage("Sort finished.")

    def reset_order(self):
        if self.store:
            self.order = list(range(len(self.store)))
            self.refresh_view()
            self.metrics.setText("Metrics: original order restored")

    def do_benchmark(self):
        if not self.store:
            return self.warn("Scrape or load some data first.")
        levels = self.get_levels()[:1] or [(0, False)]
        filtered_only = self.scope.currentIndex() == 1 and self.fs is not None
        indices = list(self.view if filtered_only else self.order)[:self.bench_n.value()]
        store, colname = self.store, self.store.columns[levels[0][0]]
        self.task = TaskThread(lambda progress: core.benchmark(store, indices, levels, progress))
        self.task.progress.connect(self.statusBar().showMessage)
        self.task.done.connect(lambda r: self.bench_done(r, colname))
        self.task.failed.connect(self.task_failed)
        self.set_busy(True)
        self.task.start()

    def bench_done(self, results, colname):
        self.set_busy(False)
        self.statusBar().showMessage("Benchmark finished.")
        BenchmarkDialog(self, results, colname).exec()

    def task_failed(self, msg):
        self.set_busy(False)
        self.warn(msg)

    # ------------------------------------------------------------ filtering
    def add_condition(self):
        if not self.store:
            return
        row = ConditionRow(len(self.cond_rows) == 0, self.store.columns, self.store.numeric)
        self.cond_rows.append(row)
        self.cond_box.addWidget(row)

    def remove_condition(self):
        if len(self.cond_rows) > 1:
            self.cond_rows.pop().setParent(None)

    def apply_filter(self):
        if not self.store:
            return self.warn("Scrape or load some data first.")
        try:
            result, comps, ms = core.evaluate_filters(self.store, [r.condition() for r in self.cond_rows])
        except ValueError as e:
            return self.warn(str(e))
        self.fs = result
        self.refresh_view(self.model.marker)
        self.metrics.setText(f"Filter: {len(self.view):,} of {len(self.store):,} rows match  |  "
                             f"⏱ {ms:.3f} ms  |  comparisons: {comps:,}")

    def clear_filter(self):
        if self.store:
            self.fs = None
            self.refresh_view(self.model.marker)
            self.metrics.setText("Filter cleared.")


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()