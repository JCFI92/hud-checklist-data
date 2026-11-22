"""Build docs/freq.json (airport frequencies) from the FAA NASR 28-day subscription.

Runs in GitHub Actions. Finds the current NASR cycle, downloads the CSV package,
and writes a compact file the HUD Checklist app downloads when it has internet.
"""
import csv, datetime as dt, io, json, re, sys, urllib.request, zipfile

ANCHOR = dt.date(2026, 10, 29)  # a known NASR effective date; cycles repeat every 28 days
UA = {"User-Agent": "hud-checklist-data (github.com/JCFI92/hud-checklist-data)"}


def cycles(today):
    n = (today - ANCHOR).days // 28
    cur = ANCHOR + dt.timedelta(days=28 * n)
    return [cur, cur - dt.timedelta(days=28)]


def fetch(date):
    url = f"https://nfdc.faa.gov/webContent/28DaySub/extra/{date.day:02d}_{date:%b}_{date.year}_CSV.zip"
    print("GET", url)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
        return r.read()


def find_csv(z, name):
    """Return rows of `name` from the zip, looking inside nested zips too."""
    for info in z.infolist():
        base = info.filename.rsplit("/", 1)[-1].upper()
        if base == name:
            return list(csv.DictReader(io.TextIOWrapper(z.open(info), encoding="latin-1")))
        if base.endswith(".ZIP"):
            rows = find_csv(zipfile.ZipFile(io.BytesIO(z.read(info))), name)
            if rows is not None:
                return rows
    return None


# Frequency use text in NASR -> label shown in the app (first match wins).
RULES = [
    ("ATIS", r"ATIS"),
    ("APP/DEP", r"(?=.*\bAPCH\b|.*\bAPP\b)(?=.*\bDEP\b)"),
    ("CLEARANCE", r"\bCD\b|CLNC|CLEARANCE|\bCLD\b"),
    ("GROUND", r"\bGND\b|GROUND"),
    ("TOWER", r"\bLCL\b|\bTWR\b|TOWER"),
    ("DEPARTURE", r"\bDEP\b|DEPARTURE"),
    ("APPROACH", r"\bAPCH\b|\bAPP\b|APPROACH"),
    ("CTAF", r"CTAF"),
    ("UNICOM", r"UNICOM|\bUNIC\b"),
    ("AWOS", r"AWOS|ASOS"),
]
ORDER = [r[0] for r in RULES]
DEBUG = {"KSNA", "KAJO", "KCNO", "KSMO"}


def label(text):
    t = (text or "").upper()
    for name, rx in RULES:
        if re.search(rx, t):
            return name
    return None


def clean_freq(f):
    m = re.search(r"\d{3}\.\d{1,3}", f or "")
    if not m:
        return None
    v = float(m.group())
    return m.group() if 108.0 <= v < 137.0 else None  # VHF airband only


def main():
    data = None
    for c in cycles(dt.date.today()):
        try:
            data, cycle = fetch(c), c
            break
        except Exception as e:
            print("  not available:", e)
    if data is None:
        sys.exit("No NASR package found")
    z = zipfile.ZipFile(io.BytesIO(data))
    apt = find_csv(z, "APT_BASE.CSV")
    frq = find_csv(z, "FRQ.CSV")
    if not apt or not frq:
        print([i.filename for i in z.infolist()][:50])
        sys.exit("APT_BASE.csv or FRQ.csv missing")
    print("APT_BASE columns:", list(apt[0].keys()))
    print("FRQ columns:", list(frq[0].keys()))

    # Airport id -> ICAO id when it has one (KSNA), otherwise the FAA id (L35).
    key = {}
    out = {}
    ctaf_cols = [c for c in apt[0] if "CTAF" in c.upper()]
    unicom_cols = [c for c in apt[0] if "UNICOM" in c.upper()]
    for a in apt:
        lid = (a.get("ARPT_ID") or "").strip()
        icao = (a.get("ICAO_ID") or "").strip()
        if not lid:
            continue
        k = icao or lid
        key[lid] = k
        rows = out.setdefault(k, [])
        for col, name in [(c, "CTAF") for c in ctaf_cols] + [(c, "UNICOM") for c in unicom_cols]:
            f = clean_freq(a.get(col))
            if f:
                rows.append([name, f])

    svc_col = next((c for c in frq[0] if c.upper() in ("SERVICED_FACILITY", "SERVICED_FAC")), None)
    use_col = next((c for c in frq[0] if c.upper() == "FREQ_USE"), None)
    freq_col = next((c for c in frq[0] if c.upper() == "FREQ"), None)
    type_col = next((c for c in frq[0] if c.upper() == "FACILITY_TYPE"), None)
    print("using", svc_col, use_col, freq_col, type_col)
    for r in frq:
        lid = (r.get(svc_col) or "").strip()
        if lid not in key:
            continue
        f = clean_freq(r.get(freq_col))
        name = label(r.get(use_col)) or label(r.get(type_col))
        if key[lid] in DEBUG:
            print("RAW", key[lid], r.get(type_col), "|", r.get(use_col), "|", r.get(freq_col), "|", r.get("SECTORIZATION"), "->", name)
        if f and name:
            out[key[lid]].append([name, f])

    final = {}
    for k, rows in out.items():
        seen, keep = set(), []
        for name, f in sorted(rows, key=lambda x: ORDER.index(x[0])):
            if (name, f) in seen:
                continue
            seen.add((name, f))
            keep.append([name, f])
        if keep:
            final[k] = keep

    doc = {"source": "FAA NASR", "cycle": cycle.isoformat(), "built": dt.date.today().isoformat(), "airports": final}
    with open("docs/freq.json", "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    with open("docs/cycle.txt", "w") as fh:
        fh.write(cycle.isoformat() + "\n")
    print(f"cycle {cycle} airports {len(final)}")
    for a in ["KAJO", "KSNA", "KCNO", "KVNY", "KLGB", "KSMO", "KFUL", "L35"]:
        print(a, final.get(a))


if __name__ == "__main__":
    main()
