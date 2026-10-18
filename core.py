"""
core.py - everything that is NOT GUI: data store, hand-written sorting
algorithms (with comparison / swap counters), search algorithms, composite
filters, CSV helpers and GSMArena page parsers.

No built-in sort()/sorted() is used for the application's sorting.
"""
import csv
import math
import re
import time

# =====================================================================
#  Value helpers
# =====================================================================

def to_num(v):
    """Convert '$1,299', '8 GB', 5.5, '' ... to float or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[,$£€\s]", "", str(v))
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        m = re.fullmatch(r"(-?\d+(?:\.\d+)?)[A-Za-z%]*", s)   # '8GB', '5000mAh'
        return float(m.group(1)) if m else None


def fmt(v):
    """Value -> text for table / CSV."""
    if v is None:
        return ""
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def infer_types(columns, rows):
    """Detect numeric columns (>=90% of non-blank cells parse as numbers)
    and convert them. Returns (typed_rows, numeric_flags)."""
    ncol = len(columns)
    numeric = []
    for c in range(ncol):
        nonblank = [r[c] for r in rows if r[c] is not None and str(r[c]).strip() != ""]
        ok = sum(1 for v in nonblank if to_num(v) is not None)
        numeric.append(bool(nonblank) and ok / len(nonblank) >= 0.9)
    typed = []
    for r in rows:
        typed.append([to_num(r[c]) if numeric[c] else ("" if r[c] is None else str(r[c]))
                      for c in range(ncol)])
    return typed, numeric


# =====================================================================
#  Sorting algorithms
#  Every algorithm sorts a list of (key, row_index) pairs IN PLACE,
#  comparing only pair[0], and returns (comparisons, swaps).
#  "swaps" = element exchanges; for insertion / merge / shell style
#  algorithms it counts element moves (shifts / writes).
# =====================================================================

class Rev:
    """Wrapper that reverses ordering -> descending order inside tuple keys."""
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v

    def __lt__(self, o):
        return o.v < self.v

    def __eq__(self, o):
        return self.v == o.v

    __hash__ = None


def bubble_sort(a):
    c = s = 0
    n = len(a)
    for i in range(n - 1):
        swapped = False
        for j in range(n - 1 - i):
            c += 1
            if a[j + 1][0] < a[j][0]:
                a[j], a[j + 1] = a[j + 1], a[j]
                s += 1
                swapped = True
        if not swapped:
            break
    return c, s


def selection_sort(a):
    c = s = 0
    n = len(a)
    for i in range(n - 1):
        m = i
        for j in range(i + 1, n):
            c += 1
            if a[j][0] < a[m][0]:
                m = j
        if m != i:
            a[i], a[m] = a[m], a[i]
            s += 1
    return c, s


def _insertion_range(a, lo, hi):
    """Insertion sort on a[lo:hi]."""
    c = s = 0
    for i in range(lo + 1, hi):
        x = a[i]
        j = i - 1
        while j >= lo:
            c += 1
            if x[0] < a[j][0]:
                a[j + 1] = a[j]
                s += 1
                j -= 1
            else:
                break
        a[j + 1] = x
    return c, s


def insertion_sort(a):
    return _insertion_range(a, 0, len(a))


def _bottom_up_merge(a, width):
    """Iterative merge passes starting from runs of length `width`."""
    n = len(a)
    c = s = 0
    src, buf = a, a[:]
    while width < n:
        for lo in range(0, n, 2 * width):
            mid = min(lo + width, n)
            hi = min(lo + 2 * width, n)
            i, j, k = lo, mid, lo
            while i < mid and j < hi:
                c += 1
                if src[j][0] < src[i][0]:      # strict '<' keeps it stable
                    buf[k] = src[j]
                    j += 1
                else:
                    buf[k] = src[i]
                    i += 1
                k += 1
                s += 1
            while i < mid:
                buf[k] = src[i]; i += 1; k += 1; s += 1
            while j < hi:
                buf[k] = src[j]; j += 1; k += 1; s += 1
        src, buf = buf, src
        width *= 2
    if src is not a:
        a[:] = src
    return c, s


def merge_sort(a):
    return _bottom_up_merge(a, 1)


def quick_sort(a):
    """Iterative 3-way quick sort, median-of-three pivot.
    (3-way partition keeps it fast on columns with many duplicates such
    as RAM or Year, where a plain Lomuto partition degrades to O(n^2).)"""
    c = s = 0
    n = len(a)
    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        while lo < hi:
            mid = (lo + hi) // 2
            x, y, z = a[lo][0], a[mid][0], a[hi][0]
            c += 3
            if x < y:
                pv = y if y < z else (z if x < z else x)
            else:
                pv = x if x < z else (z if y < z else y)
            lt, i, gt = lo, lo, hi
            while i <= gt:
                c += 1
                if a[i][0] < pv:
                    a[lt], a[i] = a[i], a[lt]
                    lt += 1; i += 1; s += 1
                else:
                    c += 1
                    if pv < a[i][0]:
                        a[i], a[gt] = a[gt], a[i]
                        gt -= 1; s += 1
                    else:
                        i += 1
            if lt - lo < hi - gt:
                stack.append((gt + 1, hi)); hi = lt - 1
            else:
                stack.append((lo, lt - 1)); lo = gt + 1
    return c, s


def heap_sort(a):
    n = len(a)
    c = s = 0

    def sift(i, end):
        nonlocal c, s
        while True:
            l = 2 * i + 1
            if l >= end:
                return
            m = l
            if l + 1 < end:
                c += 1
                if a[l][0] < a[l + 1][0]:
                    m = l + 1
            c += 1
            if a[i][0] < a[m][0]:
                a[i], a[m] = a[m], a[i]
                s += 1
                i = m
            else:
                return

    for i in range(n // 2 - 1, -1, -1):
        sift(i, n)
    for end in range(n - 1, 0, -1):
        a[0], a[end] = a[end], a[0]
        s += 1
        sift(0, end)
    return c, s


def shell_sort(a):
    c = s = 0
    n = len(a)
    gap = 1
    while gap < n // 3:
        gap = gap * 3 + 1                      # Knuth sequence
    while gap >= 1:
        for i in range(gap, n):
            x = a[i]
            j = i
            while j >= gap:
                c += 1
                if x[0] < a[j - gap][0]:
                    a[j] = a[j - gap]
                    s += 1
                    j -= gap
                else:
                    break
            a[j] = x
        gap //= 3
    return c, s


def cocktail_sort(a):
    c = s = 0
    lo, hi = 0, len(a) - 1
    while lo < hi:
        swapped = False
        for j in range(lo, hi):
            c += 1
            if a[j + 1][0] < a[j][0]:
                a[j], a[j + 1] = a[j + 1], a[j]
                s += 1
                swapped = True
        hi -= 1
        if not swapped:
            break
        swapped = False
        for j in range(hi, lo, -1):
            c += 1
            if a[j][0] < a[j - 1][0]:
                a[j], a[j - 1] = a[j - 1], a[j]
                s += 1
                swapped = True
        lo += 1
        if not swapped:
            break
    return c, s


def comb_sort(a):
    c = s = 0
    n = len(a)
    gap, swapped = n, True
    while gap > 1 or swapped:
        gap = max(1, int(gap / 1.3))
        swapped = False
        for i in range(n - gap):
            c += 1
            if a[i + gap][0] < a[i][0]:
                a[i], a[i + gap] = a[i + gap], a[i]
                s += 1
                swapped = True
    return c, s


def tim_sort(a):
    """Simplified Tim Sort: insertion-sort runs of 32, then merge runs."""
    RUN = 32
    c = s = 0
    n = len(a)
    for lo in range(0, n, RUN):
        dc, ds = _insertion_range(a, lo, min(lo + RUN, n))
        c += dc; s += ds
    dc, ds = _bottom_up_merge(a, RUN)
    return c + dc, s + ds


# name -> info.  'quadratic' is used to warn before sorting huge datasets.
SORTERS = {
    "Bubble Sort":        dict(fn=bubble_sort,    big_o="O(n²)",              stable=True,  quadratic=True),
    "Selection Sort":     dict(fn=selection_sort, big_o="O(n²)",              stable=False, quadratic=True),
    "Insertion Sort":     dict(fn=insertion_sort, big_o="O(n²)",              stable=True,  quadratic=True),
    "Merge Sort":         dict(fn=merge_sort,     big_o="O(n log n)",         stable=True,  quadratic=False),
    "Quick Sort":         dict(fn=quick_sort,     big_o="O(n log n) avg, O(n²) worst", stable=False, quadratic=False),
    "Heap Sort":          dict(fn=heap_sort,      big_o="O(n log n)",         stable=False, quadratic=False),
    "Shell Sort (extra)": dict(fn=shell_sort,     big_o="≈O(n^1.5)",          stable=False, quadratic=False),
    "Cocktail Sort (extra)": dict(fn=cocktail_sort, big_o="O(n²)",            stable=True,  quadratic=True),
    "Comb Sort (extra)":  dict(fn=comb_sort,      big_o="O(n²) worst, ≈O(n log n) avg", stable=False, quadratic=False),
    "Tim Sort (extra)":   dict(fn=tim_sort,       big_o="O(n log n)",         stable=True,  quadratic=False),
}


# =====================================================================
#  Search algorithms (exact match). Binary / Jump need sorted data.
# =====================================================================

def linear_search(values, target):
    res = [i for i, v in enumerate(values) if v == target]
    return res, len(values)


def binary_search(sorted_pairs, target):
    lo, hi, c = 0, len(sorted_pairs), 0
    while lo < hi:                          # lower bound
        mid = (lo + hi) // 2
        c += 1
        if sorted_pairs[mid][0] < target:
            lo = mid + 1
        else:
            hi = mid
    res = []
    while lo < len(sorted_pairs) and sorted_pairs[lo][0] == target:
        res.append(sorted_pairs[lo][1]); lo += 1; c += 1
    return res, c


def jump_search(sorted_pairs, target):
    n = len(sorted_pairs)
    step = max(1, int(math.sqrt(n)))
    prev, cur, c = 0, step, 0
    while prev < n and sorted_pairs[min(cur, n) - 1][0] < target:
        c += 1
        prev, cur = cur, cur + step
    i = prev
    while i < n and sorted_pairs[i][0] < target:
        c += 1
        i += 1
    res = []
    while i < n and sorted_pairs[i][0] == target:
        res.append(sorted_pairs[i][1]); i += 1; c += 1
    return res, c


SEARCHERS = ["Linear Search", "Binary Search", "Jump Search"]

STR_OPS = ["contains", "starts with", "ends with", "equals"]
NUM_OPS = ["=", "!=", "<", "<=", ">", ">=", "between"]
CONNECTORS_FIRST = ["(none)", "NOT"]
CONNECTORS = ["AND", "OR", "AND NOT", "OR NOT"]


# =====================================================================
#  Data store
# =====================================================================

class DataStore:
    def __init__(self, columns, rows):
        self.columns = list(columns)
        self.rows, self.numeric = infer_types(columns, rows)
        self._norm, self._sortv, self._sorted = {}, {}, {}

    def __len__(self):
        return len(self.rows)

    def norm(self, col):
        """Values used for filtering: floats / lower-cased strings."""
        if col not in self._norm:
            if self.numeric[col]:
                self._norm[col] = [r[col] for r in self.rows]
            else:
                self._norm[col] = [r[col].lower() for r in self.rows]
        return self._norm[col]

    def sort_vals(self, col):
        """Values used for sorting (None -> -inf so blanks come first)."""
        if col not in self._sortv:
            if self.numeric[col]:
                self._sortv[col] = [(-math.inf if r[col] is None else r[col]) for r in self.rows]
            else:
                self._sortv[col] = [r[col].lower() for r in self.rows]
        return self._sortv[col]

    def sorted_pairs(self, col):
        """(value, row) pairs sorted by value - needed by binary/jump search.
        Built once per column with our own merge sort, then cached."""
        if col not in self._sorted:
            pairs = [(v, i) for i, v in enumerate(self.norm(col)) if v is not None]
            merge_sort(pairs)
            self._sorted[col] = pairs
        return self._sorted[col]


# =====================================================================
#  Sorting front-end (single & multi-level)
# =====================================================================

def make_pairs(store, indices, levels):
    """levels = [(col, descending), ...]  ->  [(key_tuple, row_index), ...]"""
    cols = [(store.sort_vals(c), d) for c, d in levels]
    pairs = []
    for i in indices:
        pairs.append((tuple(Rev(v[i]) if d else v[i] for v, d in cols), i))
    return pairs


def run_sort(store, indices, levels, algo):
    """Sort `indices` by `levels` with algorithm `algo`.
    Returns (ordered_indices, milliseconds, comparisons, swaps).
    Only the algorithm itself is timed (not key building)."""
    pairs = make_pairs(store, indices, levels)
    t0 = time.perf_counter()
    c, s = SORTERS[algo]["fn"](pairs)
    ms = (time.perf_counter() - t0) * 1000.0
    return [p[1] for p in pairs], ms, c, s


def benchmark(store, indices, levels, progress=None):
    """Run every algorithm on the same data. Returns list of dict rows."""
    base = make_pairs(store, indices, levels)
    out = []
    for k, (name, info) in enumerate(SORTERS.items()):
        if progress:
            progress(f"Benchmarking {name} ({k + 1}/{len(SORTERS)}) ...")
        data = list(base)
        t0 = time.perf_counter()
        c, s = info["fn"](data)
        ms = (time.perf_counter() - t0) * 1000.0
        out.append(dict(name=name, big_o=info["big_o"], stable=info["stable"],
                        ms=ms, comparisons=c, swaps=s, rows=len(base)))
    return out


# =====================================================================
#  Filtering: single conditions + AND / OR / NOT composition
# =====================================================================

def _evaluate_one(store, cond):
    col, op, raw, algo = cond["col"], cond["op"], cond["value"].strip(), cond["algo"]
    vals = store.norm(col)
    n = len(vals)
    if raw == "":
        raise ValueError("Please enter a value for every filter condition.")

    if store.numeric[col]:
        def num(txt):
            v = to_num(txt)
            if v is None:
                raise ValueError(f"'{txt}' is not a number (column '{store.columns[col]}').")
            return v
        if op == "between":
            parts = raw.replace("-", ",").split(",") if "," not in raw else raw.split(",")
            if len(parts) != 2:
                raise ValueError("For 'between' type two numbers, e.g.  200,500")
            lo, hi = num(parts[0]), num(parts[1])
            return {i for i, v in enumerate(vals) if v is not None and lo <= v <= hi}, n
        t = num(raw)
        if op == "=":
            target = t
        else:
            preds = {
                "!=": lambda v: v is None or v != t,
                "<":  lambda v: v is not None and v < t,
                "<=": lambda v: v is not None and v <= t,
                ">":  lambda v: v is not None and v > t,
                ">=": lambda v: v is not None and v >= t,
            }
            p = preds[op]
            return {i for i, v in enumerate(vals) if p(v)}, n
    else:
        t = raw.lower()
        if op == "equals":
            target = t
        else:
            preds = {
                "contains":    lambda v: t in v,
                "starts with": lambda v: v.startswith(t),
                "ends with":   lambda v: v.endswith(t),
            }
            p = preds[op]
            return {i for i, v in enumerate(vals) if p(v)}, n

    # exact match -> user-selected search algorithm
    if algo == "Binary Search":
        res, c = binary_search(store.sorted_pairs(col), target)
    elif algo == "Jump Search":
        res, c = jump_search(store.sorted_pairs(col), target)
    else:
        res, c = linear_search(vals, target)
    return set(res), c


def evaluate_filters(store, conds):
    """conds: list of dicts(connector, col, op, value, algo).
    Evaluated strictly left-to-right.  Returns (set_of_rows, comparisons, ms)."""
    t0 = time.perf_counter()
    universe = set(range(len(store)))
    result, total_c = None, 0
    for k, cond in enumerate(conds):
        s, c = _evaluate_one(store, cond)
        total_c += c
        conn = cond["connector"]
        if k == 0:
            result = (universe - s) if conn == "NOT" else s
        elif conn == "AND":
            result = result & s
        elif conn == "OR":
            result = result | s
        elif conn == "AND NOT":
            result = result - s
        elif conn == "OR NOT":
            result = result | (universe - s)
    return result, total_c, (time.perf_counter() - t0) * 1000.0


# =====================================================================
#  CSV
# =====================================================================

def save_csv(path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(columns)
        for r in rows:
            w.writerow([fmt(v) for v in r])


def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        data = list(csv.reader(f))
    if len(data) < 2:
        raise ValueError("CSV has no data rows.")
    cols = data[0]
    rows = [(r + [""] * len(cols))[:len(cols)] for r in data[1:]]
    return cols, rows


# =====================================================================
#  GSMArena parsing (pure functions - no network, so they can be tested)
#
#  Brand listing pages show, for every device, an <img title="..."> tooltip such as
#    "Samsung Galaxy A54 Android smartphone. Announced Mar 2023. Features 6.4" display,
#     Exynos 1380 chipset, 5000 mAh battery, 256 GB storage, 8 GB RAM, ..."
#  so ~40 devices (9+ attributes each) come from ONE request.
# =====================================================================

GSM_BASE = "https://www.gsmarena.com/"
GSM_MAKERS = GSM_BASE + "makers.php3"

GSM_COLUMNS = ["ID", "Brand", "Model", "Device Type", "OS", "Year", "Month (1-12)",
               "Screen (in)", "Chipset", "Battery (mAh)", "Storage (GB)", "RAM (GB)", "URL"]

_OS = (r"Android|iOS|iPadOS|watchOS|HarmonyOS|Windows Phone|Windows Mobile|Windows|Symbian|"
       r"BlackBerry OS|BlackBerry|Tizen|Wear OS|KaiOS|Bada|webOS|Sailfish OS|Firefox OS|Palm OS")
_TYPE_RE = re.compile(r"\s(?:(" + _OS + r")\s+)?(smartphone|tablet|watch|feature phone|phone)\s*$", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_DATE_RE = re.compile(r"(?:Announced|Released)\s+(?:([A-Z][a-z]{2})[a-z]*\s+)?(\d{4})")
_SCREEN_RE = re.compile(r"([\d.]+)\s*[\u2033\u201d\"']*\s*display")
_BAT_RE = re.compile(r"(\d[\d,]*)\s*mAh")
_STO_RE = re.compile(r"([\d.]+)\s*(TB|GB|MB)\s+storage", re.I)
_RAM_RE = re.compile(r"([\d.]+)\s*(GB|MB)\s+RAM", re.I)


def _to_gb(num, unit):
    v = float(num)
    u = unit.upper()
    return v * 1024 if u == "TB" else (v / 1024 if u == "MB" else v)


def parse_tooltip(title):
    """Tooltip text -> dict of attributes (missing ones are None)."""
    out = dict(type=None, os=None, year=None, month=None, screen=None,
               chipset=None, battery=None, storage=None, ram=None)
    first = title.split(". ")[0].strip().rstrip(".")
    m = _TYPE_RE.search(first)
    if m:
        out["os"] = m.group(1)
        out["type"] = m.group(2).capitalize() if m.group(2).lower() != "feature phone" else "Feature phone"
    m = _DATE_RE.search(title)
    if m:
        out["month"] = _MONTHS.get((m.group(1) or "").lower())
        out["year"] = int(m.group(2))
    m = _SCREEN_RE.search(title)
    if m:
        out["screen"] = float(m.group(1).rstrip("."))
    m = re.search(r"display,\s*([^,]+?)\s+chipset", title) or re.search(r"Features\s+([^,]+?)\s+chipset", title)
    if m:
        out["chipset"] = m.group(1).strip()
    m = _BAT_RE.search(title)
    if m:
        out["battery"] = float(m.group(1).replace(",", ""))
    m = _STO_RE.search(title)
    if m:
        out["storage"] = round(_to_gb(*m.groups()), 3)
    m = _RAM_RE.search(title)
    if m:
        out["ram"] = round(_to_gb(*m.groups()), 3)
    return out


def parse_brands(html):
    """makers.php3 -> list of (brand_name, first_page_url, device_count)."""
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        if not re.search(r"-phones-\d+\.php$", a["href"]):
            continue
        parts = a.get_text("|", strip=True).split("|")
        name = parts[0].strip()
        cnt = re.search(r"(\d[\d,]*)\s+devices?", a.get_text(" ", strip=True))
        url = urljoin(GSM_BASE, a["href"])
        if name and url not in seen:
            seen.add(url)
            out.append((name, url, int(cnt.group(1).replace(",", "")) if cnt else 0))
    return out


def parse_listing(html, brand, page_url):
    """Brand listing page -> (rows, next_page_url or None)."""
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for a in soup.find_all("a", href=True):
        img, strong = a.find("img"), a.find("strong")
        idm = re.search(r"-(\d+)\.php$", a["href"])
        if not (img and strong and img.get("title") and idm):
            continue
        t = parse_tooltip(img["title"])
        rows.append([int(idm.group(1)), brand, strong.get_text(" ", strip=True),
                     t["type"], t["os"], t["year"], t["month"], t["screen"], t["chipset"],
                     t["battery"], t["storage"], t["ram"], urljoin(page_url, a["href"])])
    nxt = soup.find("a", attrs={"title": "Next page"})
    next_url = urljoin(page_url, nxt["href"]) if nxt and nxt.get("href") and nxt["href"] != "#" else None
    return rows, next_url