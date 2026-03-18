#!/usr/bin/env python3
"""
download_covers.py
------------------
Downloads cover art for ALL games and books in your media log.
- Games:  IGDB worker → Steam → skip
- Books:  Open Library → Google Books → skip
- Saves as .webp (requires Pillow), falls back to .jpg otherwise
- Writes covers/games/<slug>.webp and covers/books/<slug>.webp
- After running, also updates index.html so LOCAL_GAME_COVERS and
  LOCAL_BOOK_COVERS point to the local files instead of remote URLs.

Usage:
    pip install requests pillow
    python download_covers.py
    python download_covers.py --games-only
    python download_covers.py --books-only
    python download_covers.py --resume     # skip titles whose file already exists

Outputs:
    covers/games/*.webp
    covers/books/*.webp
    missing_covers.txt    - titles with no cover found (check manually)
"""

import sys, os, re, json, time, unicodedata, argparse
from pathlib import Path
from io import BytesIO

try:
    import requests
except ImportError:
    sys.exit("Missing: pip install requests")

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("⚠  Pillow not installed — saving as original format (not always .webp)")
    print("   Run: pip install pillow\n")

# ── Config ──────────────────────────────────────────────────────────────────
IGDB_WORKER  = "https://medialogtwitch.rudacpbaptista.workers.dev"
GAMES_DIR    = Path("covers/games")
BOOKS_DIR    = Path("covers/books")
MISSING_FILE = Path("missing_covers.txt")
HTML_FILE    = Path("index.html")
DELAY        = 0.7   # seconds between network requests
HEADERS      = {"User-Agent": "Mozilla/5.0 Chrome/120.0"}

# ── Text utils ───────────────────────────────────────────────────────────────
def normalise(s: str) -> str:
    """Mirror JS normalise(): strip everything except a-z0-9."""
    s = unicodedata.normalize("NFD", s)
    return re.sub(r"[^a-z0-9]", "", s.lower())

def slugify(s: str) -> str:
    """Mirror JS slug(): kebab-case ASCII slug."""
    s = unicodedata.normalize("NFD", s)
    s = s.encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", s.lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    # Collapse multiple dashes
    s = re.sub(r"-{2,}", "--", s)
    return s

def strip_edition(title: str) -> str:
    """Remove common edition suffixes for fuzzy matching."""
    return re.sub(
        r"\s*([-–:]\s*)?(remastered|definitive edition|game of the year.*|"
        r"director.?s cut|enhanced edition|special edition|hd|remake|"
        r"ultimate edition|complete edition)\s*$",
        "", title, flags=re.IGNORECASE
    ).strip()

def igdb_variants(title: str) -> list[str]:
    # If title is all-caps (like ASYLUM, GYLT), also try title-case version
    working = title
    if title == title.upper() and len(title) > 1 and re.search(r'[A-Z]', title):
        title_cased = title.title()
        variants = [title, title_cased]
    else:
        variants = [title]

    # Strip parenthetical year suffix e.g. "Dead Space (2008)" → "Dead Space"
    no_year = re.sub(r'\s*\(\d{4}\)\s*$', '', working).strip()
    if no_year and no_year not in variants:
        variants.append(no_year)

    # Short form before colon / em-dash / spaced hyphen
    if re.search(r"[:\u2014]|\s+-\s+", working):
        short = re.split(r"[:\u2014]|\s+-\s+", working)[0].strip()
        if short not in variants:
            variants.append(short)
    # Strip article
    no_art = re.sub(r"^(the|a|an)\s+", "", working, flags=re.IGNORECASE).strip()
    if no_art not in variants:
        variants.append(no_art)
    # Strip edition
    clean = strip_edition(working)
    if clean and clean not in variants:
        variants.append(clean)
    return variants

# ── Image saving ─────────────────────────────────────────────────────────────
def save_as_webp(data: bytes, path: Path) -> bool:
    try:
        if HAS_PIL:
            img = Image.open(BytesIO(data)).convert("RGB")
            img.save(path.with_suffix(".webp"), "WEBP", quality=85)
            # ensure path has .webp suffix
            if path.suffix != ".webp":
                path = path.with_suffix(".webp")
        else:
            path.write_bytes(data)
        return True
    except Exception as e:
        print(f"      save error: {e}")
        return False

def fetch(url: str) -> bytes | None:
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200 and len(r.content) > 500:
                return r.content
        except Exception:
            pass
        time.sleep(1)
    return None

# ── IGDB via worker ───────────────────────────────────────────────────────────
def try_igdb(query: str) -> str | None:
    try:
        r = requests.get(IGDB_WORKER, params={"game": query}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            url = data.get("cover")
            if url:
                return url.replace("t_cover_big", "t_cover_big_2x").replace(".jpg", ".webp")
    except Exception:
        pass
    return None

# ── Steam fallback ────────────────────────────────────────────────────────────
_steam_index: dict | None = None

def steam_index() -> dict:
    global _steam_index
    if _steam_index is not None:
        return _steam_index
    print("  [Steam] Fetching app list (one-time, ~4 MB)...")
    try:
        r = requests.get(
            "https://api.steampowered.com/ISteamApps/GetAppList/v2/",
            timeout=30
        )
        apps = r.json()["applist"]["apps"]
        _steam_index = {normalise(a["name"]): a["appid"] for a in apps}
        print(f"  [Steam] {len(_steam_index)} apps indexed.")
    except Exception as e:
        print(f"  [Steam] Failed to load index: {e}")
        _steam_index = {}
    return _steam_index

def try_steam(title: str) -> str | None:
    idx = steam_index()
    for variant in igdb_variants(title):
        appid = idx.get(normalise(variant))
        if appid:
            # Prefer portrait cover
            for url in [
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900.jpg",
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
            ]:
                try:
                    r = requests.head(url, headers=HEADERS, timeout=8)
                    if r.status_code == 200:
                        return url
                except Exception:
                    pass
    return None

# ── Open Library (books) ──────────────────────────────────────────────────────
def try_openlibrary(title: str, author: str) -> str | None:
    q = title + (" " + author if author else "")
    try:
        r = requests.get(
            "https://openlibrary.org/search.json",
            params={"q": q, "limit": 5, "fields": "cover_i,title,author_name"},
            timeout=12
        )
        for doc in r.json().get("docs", []):
            cid = doc.get("cover_i")
            if cid:
                return f"https://covers.openlibrary.org/b/id/{cid}-L.jpg"
    except Exception:
        pass
    return None

def try_google_books(title: str, author: str) -> str | None:
    q = f'intitle:"{title}"' + (f' inauthor:"{author}"' if author else "")
    try:
        r = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": q, "maxResults": 3},
            timeout=12
        )
        for item in r.json().get("items", []):
            img = item.get("volumeInfo", {}).get("imageLinks", {})
            url = img.get("thumbnail") or img.get("smallThumbnail")
            if url:
                # Upgrade to larger size
                url = url.replace("zoom=1", "zoom=3").replace("&edge=curl", "")
                return url
    except Exception:
        pass
    return None

# ── Live missing file writer ──────────────────────────────────────────────────
def _write_missing(missing: list):
    """Write missing list to file immediately after each miss."""
    MISSING_FILE.write_text("\n".join(missing), encoding="utf-8")

# ── Core download function ────────────────────────────────────────────────────
def download_all(games: list[str], books: list[tuple], resume: bool):
    GAMES_DIR.mkdir(parents=True, exist_ok=True)
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)

    missing = []
    # Maps normalise(title) -> relative path for HTML patching
    cover_map_games = {}
    cover_map_books = {}

    # ── Games ────────────────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"  GAMES  ({len(games)} titles)")
    print(f"{'═'*60}\n")

    for idx, title in enumerate(games, 1):
        slug = slugify(title)
        norm = normalise(title)
        out_path = GAMES_DIR / f"{slug}.webp"

        print(f"[{idx}/{len(games)}] {title}")

        if resume and out_path.exists():
            print(f"      ↩ resuming — already exists")
            cover_map_games[norm] = f"covers/games/{slug}.webp"
            continue

        cover_url = None

        # Try IGDB variants
        for variant in igdb_variants(title):
            cover_url = try_igdb(variant)
            if cover_url:
                print(f"      IGDB ✓  ({variant!r})")
                break
            time.sleep(DELAY)

        # Steam fallback
        if not cover_url:
            cover_url = try_steam(title)
            if cover_url:
                print(f"      Steam ✓")
            time.sleep(DELAY)

        if cover_url:
            data = fetch(cover_url)
            if data:
                ok = save_as_webp(data, out_path)
                if ok:
                    cover_map_games[norm] = f"covers/games/{slug}.webp"
                    print(f"      ✓  saved → {out_path}")
                else:
                    missing.append(f"[game] {title}")
                    _write_missing(missing)
            else:
                print(f"      ✗  fetch failed")
                missing.append(f"[game] {title}")
                _write_missing(missing)
        else:
            print(f"      ✗  not found")
            missing.append(f"[game] {title}")
            _write_missing(missing)

    # ── Books ────────────────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"  BOOKS  ({len(books)} titles)")
    print(f"{'═'*60}\n")

    for idx, (title, author) in enumerate(books, 1):
        slug = slugify(title)
        norm = normalise(title)
        out_path = BOOKS_DIR / f"{slug}.webp"

        print(f"[{idx}/{len(books)}] {title}  /  {author}")

        if resume and out_path.exists():
            print(f"      ↩ resuming — already exists")
            cover_map_books[norm] = f"covers/books/{slug}.webp"
            continue

        cover_url = try_openlibrary(title, author)
        if cover_url:
            print(f"      OpenLibrary ✓")
        else:
            cover_url = try_google_books(title, author)
            if cover_url:
                print(f"      Google Books ✓")
        time.sleep(DELAY)

        if cover_url:
            data = fetch(cover_url)
            if data:
                ok = save_as_webp(data, out_path)
                if ok:
                    cover_map_books[norm] = f"covers/books/{slug}.webp"
                    print(f"      ✓  saved → {out_path}")
                else:
                    missing.append(f"[book] {title}")
                    _write_missing(missing)
            else:
                print(f"      ✗  fetch failed")
                missing.append(f"[book] {title}")
                _write_missing(missing)
        else:
            print(f"      ✗  not found")
            missing.append(f"[book] {title}")
            _write_missing(missing)

    # ── Write missing list ────────────────────────────────────────────────────
    if missing:
        MISSING_FILE.write_text("\n".join(missing), encoding="utf-8")
        print(f"\n⚠  {len(missing)} covers not found → {MISSING_FILE}")
    else:
        print(f"\n✓  All covers downloaded successfully!")

    # ── Patch index.html ──────────────────────────────────────────────────────
    if HTML_FILE.exists() and (cover_map_games or cover_map_books):
        patch_html(cover_map_games, cover_map_books)
    else:
        print(f"\n⚠  {HTML_FILE} not found — skipping HTML patch.")
        print("   Add entries manually from cover_map_games / cover_map_books.")

    return missing


def patch_html(cover_map_games: dict, cover_map_books: dict):
    """
    Rebuilds LOCAL_GAME_COVERS and LOCAL_BOOK_COVERS in index.html
    so every title points to its local file.
    """
    print(f"\nPatching {HTML_FILE}...")
    html = HTML_FILE.read_text(encoding="utf-8")

    def rebuild_covers_block(html: str, var_name: str, new_entries: dict) -> str:
        # Find existing block
        pattern = re.compile(
            rf'(const {re.escape(var_name)}\s*=\s*\{{)(.*?)(\}};?)',
            re.DOTALL
        )
        m = pattern.search(html)
        if not m:
            print(f"  ⚠  Could not find {var_name} in HTML — skipping.")
            return html

        # Parse existing entries so we don't lose anything
        existing_str = m.group(2)
        existing = {}
        for em in re.finditer(r'"(\w+)":\s*"([^"]+)"', existing_str):
            existing[em.group(1)] = em.group(2)

        # Merge: new entries override existing ones
        merged = {**existing, **new_entries}

        # Rebuild block
        lines = [f'  "{k}": "{v}"' for k, v in sorted(merged.items())]
        new_block = m.group(1) + "\n" + ",\n".join(lines) + "\n" + m.group(3)
        return html[:m.start()] + new_block + html[m.end():]

    html = rebuild_covers_block(html, "LOCAL_GAME_COVERS", cover_map_games)
    html = rebuild_covers_block(html, "LOCAL_BOOK_COVERS", cover_map_books)

    HTML_FILE.write_text(html, encoding="utf-8")
    print(f"  ✓  index.html updated with {len(cover_map_games)} game + {len(cover_map_books)} book entries.")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download all cover art for your media log.")
    parser.add_argument("--games-only", action="store_true")
    parser.add_argument("--books-only", action="store_true")
    parser.add_argument("--resume",     action="store_true", help="Skip titles whose file already exists")
    args = parser.parse_args()

    # Load title lists — generated by extract_titles.py or placed manually
    games, books = [], []

    if not args.books_only:
        gf = Path("all_games.json")
        if not gf.exists():
            sys.exit(f"ERROR: {gf} not found. Run extract_titles.py first.")
        games = json.loads(gf.read_text(encoding="utf-8"))

    if not args.games_only:
        bf = Path("all_books.json")
        if not bf.exists():
            sys.exit(f"ERROR: {bf} not found. Run extract_titles.py first.")
        raw = json.loads(bf.read_text(encoding="utf-8"))
        # Support both [title, author] pairs and plain strings
        books = [(e[0], e[1]) if isinstance(e, list) else (e, "") for e in raw]

    download_all(games, books, resume=args.resume)
