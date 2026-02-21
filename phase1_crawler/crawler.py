"""
Phase 1: Web Crawler for Korean Bar Exam Past Papers
Target: https://www.moj.go.kr/moj/405/subview.do
Downloads: PDF, HWP, ZIP exam files
"""

import re
import time
import logging
from pathlib import Path
from typing import List, Dict, Set
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ── Config ─────────────────────────────────────────────────────────────────
BASE_URL = "https://www.moj.go.kr"
LIST_URL = f"{BASE_URL}/moj/405/subview.do"
DOWNLOAD_DIR = Path(__file__).resolve().parent.parent / "downloads"
REQUEST_DELAY = 1.5  # seconds between requests (be polite)
TARGET_EXTENSIONS = {".pdf", ".hwp", ".hwpx", ".zip"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": BASE_URL,
}


# ── Helpers ────────────────────────────────────────────────────────────────
def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """GET with retry + polite delay."""
    for attempt in range(3):
        try:
            resp = session.get(url, headers=HEADERS, timeout=30, **kwargs)
            resp.raise_for_status()
            time.sleep(REQUEST_DELAY)
            return resp
        except requests.RequestException as exc:
            log.warning("Attempt %d failed for %s: %s", attempt + 1, url, exc)
            time.sleep(REQUEST_DELAY * 2)
    raise RuntimeError(f"Failed to fetch: {url}")


def safe_filename(name: str) -> str:
    """Strip characters invalid in Windows filenames."""
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


# ── Crawler logic ──────────────────────────────────────────────────────────
def get_total_pages(session: requests.Session) -> int:
    """Parse total page count from the first list page."""
    resp = get(session, LIST_URL)
    soup = BeautifulSoup(resp.text, "html.parser")
    # Try every <a> and <strong> in pagination for page numbers
    pag = soup.find("div", class_="pagination") or soup.find("div", class_="paging")
    if not pag:
        return 1
    nums = []
    for tag in pag.find_all(["a", "strong"]):
        text = tag.get_text(strip=True)
        href = tag.get("href", "")
        # from href: javascript:page_link('N') or ?page=N
        for src in (href, text):
            m = re.search(r"(\d+)", src)
            if m:
                nums.append(int(m.group(1)))
    return max(nums) if nums else 1


def get_article_links(session: requests.Session, page: int) -> List[Dict]:
    """Return list of {title, url} from one list page."""
    resp = get(session, LIST_URL, params={"page": page})
    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []
    for a in soup.select("table.artclTable tbody tr td a[href*='artclView.do']"):
        href = a.get("href", "")
        if not href:
            continue
        articles.append(
            {
                "title": a.get_text(strip=True),
                "url": urljoin(BASE_URL, href),
            }
        )
    return articles


def get_download_links(session: requests.Session, article_url: str) -> List[Dict]:
    """Return list of {filename, url} from an article page."""
    resp = get(session, article_url)
    soup = BeautifulSoup(resp.text, "html.parser")
    files = []
    for a in soup.select("a[href*='download.do']"):
        filename = a.get_text(strip=True)
        ext = Path(filename).suffix.lower()
        if ext not in TARGET_EXTENSIONS:
            continue
        files.append(
            {
                "filename": filename,
                "url": urljoin(BASE_URL, a["href"]),
            }
        )
    return files


def download_file(session: requests.Session, url: str, dest: Path) -> None:
    """Stream-download a file to dest (skip if already exists)."""
    if dest.exists():
        log.info("  SKIP (exists): %s", dest.name)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("  DL  %s", dest.name)
    with session.get(url, headers=HEADERS, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    time.sleep(REQUEST_DELAY)


# ── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    log.info("Fetching page count…")
    total_pages = get_total_pages(session)
    log.info("Detected pages: %d (will also stop early if page is empty)", total_pages)

    seen_articles: Set[str] = set()
    total_downloaded = 0
    MAX_PAGES = max(total_pages, 20)  # never infinite-loop

    for page in range(1, MAX_PAGES + 1):
        log.info("── Page %d ──", page)
        articles = get_article_links(session, page)
        if not articles:
            log.info("No articles on page %d — stopping.", page)
            break

        for art in articles:
            if art["url"] in seen_articles:
                continue
            seen_articles.add(art["url"])

            log.info("Article: %s", art["title"][:60])
            folder_name = safe_filename(art["title"])[:80]
            article_dir = DOWNLOAD_DIR / folder_name

            try:
                file_links = get_download_links(session, art["url"])
            except Exception as exc:
                log.error("  Could not parse article: %s", exc)
                continue

            if not file_links:
                log.info("  No target files.")
                continue

            for fl in file_links:
                fname = safe_filename(fl["filename"])
                dest = article_dir / fname
                try:
                    download_file(session, fl["url"], dest)
                    total_downloaded += 1
                except Exception as exc:
                    log.error("  Download failed: %s", exc)

    log.info("Done. %d file(s) downloaded to: %s", total_downloaded, DOWNLOAD_DIR)


if __name__ == "__main__":
    main()
