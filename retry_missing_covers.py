#!/usr/bin/env python3
"""
retry_missing_covers.py
-----------------------
Retries cover art for all titles listed in missing_covers.txt using
aggressive fuzzy matching and additional sources beyond IGDB/Steam:
  1. IGDB worker (many fuzzy variants)
  2. Steam (fuzzy)
  3. Backloggd (scrapes the game page for its cover image)
  4. itch.io (searches for the game and grabs the cover)

After a successful download, patches index.html LOCAL_GAME_COVERS.
Titles still not found remain in missing_covers.txt.

Usage:
    python retry_missing_covers.py
    python retry_missing_covers.py --missing-file path/to/missing_covers.txt
"""

import sys, re, json, time, unicodedata, argparse
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
    print("⚠  Pillow not installed — will save original format")

# ── Config ────────────────────────────────────────────────────────────────────
IGDB_WORKER  = "https://medialogtwitch.rudacpbaptista.workers.dev"
GAMES_DIR    = Path("covers/games")
MISSING_FILE = Path("missing_covers.txt")
HTML_FILE    = Path("index.html")
DELAY        = 0.8
HEADERS      = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

# ── Text utils ────────────────────────────────────────────────────────────────
def normalise(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    return re.sub(r"[^a-z0-9]", "", s.lower())

def slugify(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = s.encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", s.lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "--", s)
    return s

def fuzzy_variants(title: str) -> list[str]:
    """Generate many search variants of a title for fuzzy matching."""
    variants = []

    def add(v):
        v = v.strip()
        if v and v not in variants:
            variants.append(v)

    add(title)

    # All-caps → title case
    if title == title.upper() and re.search(r'[A-Z]', title):
        add(title.title())
        add(title.capitalize())

    # Strip parenthetical year: "Dead Space (2008)" → "Dead Space"
    no_year = re.sub(r'\s*\(\d{4}\)\s*$', '', title).strip()
    add(no_year)

    # Strip brackets entirely: "Foo [bar]" → "Foo"
    no_bracket = re.sub(r'\s*[\(\[{][^\)\]]*[\)\]}]\s*', ' ', title).strip()
    add(no_bracket)

    # Short form before colon / dash
    if re.search(r'[:\u2014]|\s+-\s+', title):
        short = re.split(r'[:\u2014]|\s+-\s+', title)[0].strip()
        add(short)

    # Strip leading article
    no_art = re.sub(r'^(the|a|an)\s+', '', title, flags=re.IGNORECASE).strip()
    add(no_art)

    # Strip edition suffixes
    clean = re.sub(
        r"\s*([-–:]\s*)?(remastered|definitive edition|game of the year.*|"
        r"director.?s cut|enhanced edition|special edition|hd|remake|"
        r"ultimate edition|complete edition|deluxe edition)\s*$",
        "", title, flags=re.IGNORECASE
    ).strip()
    add(clean)

    # Numbered sequel variants: "Dead Space 2" → "Dead Space II" etc.
    roman = {'2':'II','3':'III','4':'IV','5':'V','6':'VI','7':'VII','8':'VIII'}
    arabic = {v:k for k,v in roman.items()}
    for ar, ro in roman.items():
        if re.search(r'\b' + ar + r'\b', title):
            add(re.sub(r'\b' + ar + r'\b', ro, title))
    for ro, ar in arabic.items():
        if re.search(r'\b' + ro + r'\b', title):
            add(re.sub(r'\b' + ro + r'\b', ar, title))

    # Ampersand ↔ "and"
    if '&' in title:
        add(title.replace('&', 'and'))
    if ' and ' in title.lower():
        add(re.sub(r'\band\b', '&', title, flags=re.IGNORECASE))

    # Remove punctuation entirely for a clean slug search
    no_punct = re.sub(r"[^\w\s]", " ", title).strip()
    no_punct = re.sub(r'\s+', ' ', no_punct)
    add(no_punct)

    # Ellipsis removal: "Hello Hell...o?" → "Hello Hello"
    no_ellipsis = re.sub(r'\.{2,}', '', title).strip()
    add(no_ellipsis)

    return variants


# ── Image utils ───────────────────────────────────────────────────────────────
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

def save_as_webp(data: bytes, path: Path) -> bool:
    try:
        if HAS_PIL:
            img = Image.open(BytesIO(data)).convert("RGB")
            img.save(path.with_suffix(".webp"), "WEBP", quality=85)
        else:
            path.write_bytes(data)
        return True
    except Exception as e:
        print(f"      save error: {e}")
        return False


# ── Source: IGDB ──────────────────────────────────────────────────────────────
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


# ── Source: Steam ─────────────────────────────────────────────────────────────
_steam_index: dict | None = None

def steam_index() -> dict:
    global _steam_index
    if _steam_index is not None:
        return _steam_index
    print("  [Steam] Fetching app list (one-time)...")
    try:
        r = requests.get(
            "https://api.steampowered.com/ISteamApps/GetAppList/v2/",
            timeout=30
        )
        apps = r.json()["applist"]["apps"]
        _steam_index = {normalise(a["name"]): a["appid"] for a in apps}
        print(f"  [Steam] {len(_steam_index)} apps indexed.")
    except Exception as e:
        print(f"  [Steam] Failed: {e}")
        _steam_index = {}
    return _steam_index

def try_steam(title: str) -> str | None:
    idx = steam_index()
    for variant in fuzzy_variants(title):
        appid = idx.get(normalise(variant))
        if appid:
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


# ── Source: Backloggd ─────────────────────────────────────────────────────────
def _backloggd_slug(title: str) -> str:
    """Convert a title to a backloggd URL slug."""
    s = title.lower()
    s = re.sub(r"[''`]", "", s)          # remove apostrophes
    s = re.sub(r"[^\w\s-]", " ", s)      # non-alphanum → space
    s = re.sub(r"\s+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s

def try_backloggd(title: str) -> str | None:
    """Scrape backloggd game page for cover image."""
    for variant in fuzzy_variants(title):
        slug = _backloggd_slug(variant)
        url = f"https://www.backloggd.com/games/{slug}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            # Look for cover image in og:image or the cover img tag
            og = re.search(r'<meta property="og:image"\s+content="([^"]+)"', r.text)
            if og:
                img_url = og.group(1)
                # Filter out default/placeholder images
                if "default" not in img_url.lower() and "placeholder" not in img_url.lower():
                    print(f"      Backloggd ✓  ({variant!r})")
                    return img_url
            # Fallback: look for cover img directly
            cover = re.search(
                r'<img[^>]+class="[^"]*cover[^"]*"[^>]+src="([^"]+)"', r.text
            )
            if cover:
                img_url = cover.group(1)
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                if "default" not in img_url.lower():
                    print(f"      Backloggd ✓  ({variant!r})")
                    return img_url
        except Exception:
            pass
        time.sleep(DELAY)
    return None


# ── Source: itch.io ───────────────────────────────────────────────────────────
def try_itch(title: str) -> str | None:
    """Search itch.io and grab the cover from the first result."""
    search_variants = fuzzy_variants(title)[:4]  # limit itch searches
    for variant in search_variants:
        try:
            r = requests.get(
                "https://itch.io/games/tag-",
                params={"q": variant},
                headers=HEADERS,
                timeout=12
            )
            # itch.io search URL is actually:
        except Exception:
            pass

    # Use the proper itch search endpoint
    for variant in search_variants:
        try:
            search_url = f"https://itch.io/search?q={requests.utils.quote(variant)}&type=games"
            r = requests.get(search_url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            # Find game cells with cover images
            # Pattern: <div class="game_cell" ...> ... <img ... src="...">
            cells = re.findall(
                r'<div class="game_cell[^"]*"[^>]*>.*?</div>\s*</div>',
                r.text, re.DOTALL
            )
            for cell in cells[:3]:
                # Extract title from the cell and check similarity
                cell_title_m = re.search(r'class="[^"]*title[^"]*"[^>]*>([^<]+)<', cell)
                if not cell_title_m:
                    continue
                cell_title = cell_title_m.group(1).strip()
                # Simple similarity: check normalised strings
                if normalise(cell_title)[:8] == normalise(variant)[:8] or \
                   normalise(variant) in normalise(cell_title) or \
                   normalise(cell_title) in normalise(variant):
                    # Get cover image
                    img_m = re.search(r'<img[^>]+src="(https://img\.itch\.zone/[^"]+)"', cell)
                    if not img_m:
                        img_m = re.search(r'data-lazy_src="(https://img\.itch\.zone/[^"]+)"', cell)
                    if img_m:
                        print(f"      itch.io ✓  ({variant!r} → {cell_title!r})")
                        return img_m.group(1)
        except Exception:
            pass
        time.sleep(DELAY)
    return None


# ── HTML patcher ──────────────────────────────────────────────────────────────
def patch_html(cover_map: dict):
    if not HTML_FILE.exists():
        print(f"⚠  {HTML_FILE} not found — skipping patch.")
        return
    html = HTML_FILE.read_text(encoding="utf-8")
    pattern = re.compile(
        r'(const LOCAL_GAME_COVERS\s*=\s*\{)(.*?)(\};?)',
        re.DOTALL
    )
    m = pattern.search(html)
    if not m:
        print("⚠  Could not find LOCAL_GAME_COVERS in HTML.")
        return
    existing = {}
    for em in re.finditer(r'"(\w+)":\s*"([^"]+)"', m.group(2)):
        existing[em.group(1)] = em.group(2)
    merged = {**existing, **cover_map}
    lines = [f'  "{k}": "{v}"' for k, v in sorted(merged.items())]
    new_block = m.group(1) + "\n" + ",\n".join(lines) + "\n" + m.group(3)
    html = html[:m.start()] + new_block + html[m.end():]
    HTML_FILE.write_text(html, encoding="utf-8")
    print(f"  ✓  index.html patched with {len(cover_map)} new entries.")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Retry missing game covers with fuzzy matching.")
    parser.add_argument("--missing-file", default=str(MISSING_FILE))
    args = parser.parse_args()

    mf = Path(args.missing_file)
    if not mf.exists():
        sys.exit(f"ERROR: {mf} not found.")

    lines = [l.strip() for l in mf.read_text(encoding="utf-8").splitlines() if l.strip()]
    # Parse "[game] Title" or "[book] Title" lines — only process games here
    games = []
    other_lines = []
    for line in lines:
        m = re.match(r'^\[game\]\s+(.+)$', line)
        if m:
            games.append(m.group(1))
        else:
            other_lines.append(line)  # preserve book lines etc.

    if not games:
        print("No [game] entries found in missing_covers.txt.")
        return

    GAMES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{'═'*60}")
    print(f"  RETRY  ({len(games)} missing games)")
    print(f"  Sources: IGDB → Steam → Backloggd → itch.io")
    print(f"{'═'*60}\n")

    still_missing = []
    cover_map = {}

    for idx, title in enumerate(games, 1):
        slug = slugify(title)
        norm = normalise(title)
        out_path = GAMES_DIR / f"{slug}.webp"

        print(f"[{idx}/{len(games)}] {title}")

        cover_url = None
        source = None

        # 1. IGDB — try all fuzzy variants
        variants = fuzzy_variants(title)
        print(f"      Trying {len(variants)} variants: {variants[:4]}{'...' if len(variants)>4 else ''}")
        for variant in variants:
            cover_url = try_igdb(variant)
            if cover_url:
                source = f"IGDB ({variant!r})"
                break
            time.sleep(DELAY)

        # 2. Steam
        if not cover_url:
            cover_url = try_steam(title)
            if cover_url:
                source = "Steam"
            time.sleep(DELAY)

        # 3. Backloggd
        if not cover_url:
            cover_url = try_backloggd(title)
            if cover_url:
                source = "Backloggd"

        # 4. itch.io
        if not cover_url:
            cover_url = try_itch(title)
            if cover_url:
                source = "itch.io"

        if cover_url:
            data = fetch(cover_url)
            if data:
                ok = save_as_webp(data, out_path)
                if ok:
                    cover_map[norm] = f"covers/games/{slug}.webp"
                    print(f"      ✓  {source} → saved {out_path}")
                else:
                    print(f"      ✗  save failed")
                    still_missing.append(f"[game] {title}")
            else:
                print(f"      ✗  fetch failed ({source})")
                still_missing.append(f"[game] {title}")
        else:
            print(f"      ✗  not found on any source")
            still_missing.append(f"[game] {title}")

    # Update missing_covers.txt with only what's still missing
    remaining = still_missing + other_lines
    if remaining:
        mf.write_text("\n".join(remaining), encoding="utf-8")
        print(f"\n⚠  {len(still_missing)} still missing → {mf}")
    else:
        mf.write_text("", encoding="utf-8")
        print(f"\n✓  All previously missing covers found!")

    # Patch HTML
    if cover_map:
        patch_html(cover_map)
    else:
        print("\nNo new covers found — index.html unchanged.")

    print(f"\nSummary: {len(games)} retried, "
          f"{len(games) - len(still_missing)} found, "
          f"{len(still_missing)} still missing.")


if __name__ == "__main__":
    main()
