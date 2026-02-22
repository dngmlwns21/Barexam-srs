"""
crawler.py — Async scraper for 법학전문대학원협의회 모의시험 (akls.kr)

Target: https://akls.kr/exam/board.php?mNum=4&sNum=2&boardid=exam

Usage:
    python -m data_pipeline.crawler --out data/mock_raw.json
    python -m data_pipeline.crawler --out data/mock_raw.json --idx-range 50 100
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from .models import OX_LETTERS, RawQuestion, Source, SUBJECT_ALIASES

log = logging.getLogger(__name__)

BASE_URL  = "https://akls.kr/exam/board.php"
BOARD_PARAMS = "boardid=exam"
LIST_URL  = f"{BASE_URL}?{BOARD_PARAMS}&mode=list"
VIEW_URL  = f"{BASE_URL}?{BOARD_PARAMS}&mode=view&idx="

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://akls.kr/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# Circled numbers → int
CIRCLED = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}
CIRCLED_RE = re.compile(r"[①②③④⑤]")

# Question number pattern: "1.", "문 1.", "제1문", etc.
Q_NUM_RE = re.compile(
    r"(?:^|\n)\s*(?:문\s*)?(\d{1,2})\s*[.\)]\s+", re.MULTILINE
)

# Subject keywords for auto-detection
SUBJECT_KEYWORDS = {
    "헌법": ["헌법", "기본권", "위헌", "헌법재판"],
    "민법": ["민법", "채무불이행", "불법행위", "물권", "채권"],
    "민사소송법": ["민사소송", "소송법", "집행법", "민소"],
    "형법": ["형법", "범죄", "형사책임", "공범", "죄형"],
    "형사소송법": ["형사소송", "수사", "공소", "증거"],
    "상법": ["상법", "회사법", "어음", "보험"],
    "행정법": ["행정법", "행정행위", "행정소송"],
    "가족법": ["가족", "친족", "상속", "이혼"],
}


# ── HTML parsing helpers ──────────────────────────────────────────────────────

def _detect_subject(text: str) -> str:
    """Best-effort subject detection from raw text."""
    for subj, keywords in SUBJECT_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return SUBJECT_ALIASES.get(subj, subj)
    return "기타"


def _parse_title_meta(title: str) -> Dict:
    """Extract year/month/subject from post title like '2024년 9월 모의고사 형법'."""
    meta: Dict = {}
    year_m = re.search(r"(20\d{2})", title)
    month_m = re.search(r"(\d{1,2})\s*월", title)
    if year_m:
        meta["year"] = int(year_m.group(1))
    if month_m:
        meta["month"] = int(month_m.group(1))

    # Subject from title
    for subj in SUBJECT_ALIASES:
        if subj in title:
            meta["subject"] = SUBJECT_ALIASES[subj]
            break
    return meta


def _parse_questions_from_text(
    text: str,
    meta: Dict,
    source_idx: int,
) -> List[RawQuestion]:
    """
    Parse raw Korean exam text into RawQuestion list.

    Handles formats:
      • "1. [stem] ① choice1 ② choice2 ..."
      • "문 1. [stem] ① ..."
    """
    questions: List[RawQuestion] = []
    subject = meta.get("subject", "기타")
    year    = meta.get("year")
    month   = meta.get("month")

    # Split by question numbers
    blocks = re.split(r"\n\s*(?:문\s*)?(\d{1,2})\s*[.\)]\s+", "\n" + text)
    # blocks: ["", "1", "first q text", "2", "second q text", ...]

    i = 1
    while i + 1 < len(blocks):
        q_num_str = blocks[i].strip()
        q_body    = blocks[i + 1].strip()
        i += 2

        if not q_num_str.isdigit():
            continue
        q_num = int(q_num_str)

        # Split stem and choices on first circled number
        circ_pos = CIRCLED_RE.search(q_body)
        if not circ_pos:
            continue  # no choices found — skip

        stem      = q_body[: circ_pos.start()].strip()
        choice_text = q_body[circ_pos.start():]

        # Parse choices
        parts = re.split(r"([①②③④⑤])", choice_text)
        choices: Dict[int, str] = {}
        cur_num: Optional[int] = None
        for part in parts:
            if part in CIRCLED:
                cur_num = CIRCLED[part]
            elif cur_num is not None:
                choices[cur_num] = part.strip()

        if len(choices) < 2:
            continue  # malformed

        raw_id = (
            f"mock_{source_idx}_{year or 'X'}_{month or 'X'}"
            f"_{subject}_{q_num:03d}"
        )
        questions.append(
            RawQuestion(
                source=Source.MOCK_EXAM,
                raw_id=raw_id,
                year=year,
                month=month,
                subject=subject,
                question_number=q_num,
                stem=stem,
                choices=choices,
                correct_choice=1,  # placeholder — answer key parsed separately
                tags=[subject],
                source_file=f"akls_idx_{source_idx}",
            )
        )

    return questions


def _extract_answer_key(text: str) -> Dict[int, int]:
    """
    Extract answer key from text like:
      정답: 1-③ 2-① 3-⑤ …
      or a table with columns 1,2,3… / ③,①,⑤…
    """
    answer_map: Dict[int, int] = {}

    # Pattern: "1-③" or "1. ③" or "1 ③"
    pairs = re.findall(r"(\d{1,2})\s*[-.\s]\s*([①②③④⑤])", text)
    for qn, circ in pairs:
        answer_map[int(qn)] = CIRCLED[circ]

    # Fallback: "정답\s+\d+\s+\d+…" table headers then row of circles
    if not answer_map:
        header = re.search(r"문\s*번\s*((?:\d+\s+)+)", text)
        answers = re.search(r"정\s*답\s*((?:[①②③④⑤]\s+)+)", text)
        if header and answers:
            nums = list(map(int, header.group(1).split()))
            ans  = [CIRCLED[c] for c in re.findall(r"[①②③④⑤]", answers.group(1))]
            for n, a in zip(nums, ans):
                answer_map[n] = a

    return answer_map


# ── Crawler class ─────────────────────────────────────────────────────────────

class AklsCrawler:
    """Async crawler for akls.kr exam board."""

    def __init__(
        self,
        concurrency: int = 5,
        idx_range: Tuple[int, int] = (1, 200),
        timeout: int = 30,
    ) -> None:
        self.concurrency = concurrency
        self.idx_min, self.idx_max = idx_range
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(
            headers=HEADERS, timeout=self.timeout
        )
        return self

    async def __aexit__(self, *args):
        if self._session:
            await self._session.close()

    # ── Board list ────────────────────────────────────────────────────────────

    async def get_post_list(self, max_pages: int = 50) -> List[Tuple[int, str, str]]:
        """Return [(idx, title, date), ...] for all exam posts."""
        posts: List[Tuple[int, str, str]] = []
        seen_ids: set = set()
        offset = 0
        page_size = 20  # akls.kr shows 20 items per page

        for _ in range(max_pages):
            url = f"{LIST_URL}&offset={offset}"
            try:
                async with self._session.get(url) as r:
                    html = await r.text(encoding="utf-8", errors="replace")
            except Exception as exc:
                log.warning("List offset=%d fetch failed: %s", offset, exc)
                break

            soup = BeautifulSoup(html, "lxml")
            # Find links to individual posts (contain mode=view&idx=)
            links = soup.find_all("a", href=re.compile(r"mode=view&idx=\d+"))

            found_new = False
            for link in links:
                idx_m = re.search(r"idx=(\d+)", link["href"])
                if not idx_m:
                    continue
                idx = int(idx_m.group(1))
                if idx in seen_ids:
                    continue
                seen_ids.add(idx)
                title = link.get_text(strip=True)
                # Date: look in parent row's last td
                row  = link.find_parent("tr")
                date = ""
                if row:
                    cells = row.find_all("td")
                    date  = cells[-1].get_text(strip=True) if cells else ""
                posts.append((idx, title, date))
                found_new = True

            if not found_new:
                break
            offset += page_size
            await asyncio.sleep(0.3)

        log.info("Found %d posts on board", len(posts))
        return posts

    # ── Single post ───────────────────────────────────────────────────────────

    async def fetch_post(self, idx: int) -> Optional[str]:
        """Fetch raw HTML for a single post."""
        url = f"{VIEW_URL}{idx}"
        try:
            async with self._session.get(url) as r:
                if r.status == 404:
                    return None
                return await r.text(encoding="utf-8", errors="replace")
        except Exception as exc:
            log.warning("Post idx=%d fetch failed: %s", idx, exc)
            return None

    def _extract_attachment_urls(self, html: str, post_idx: int) -> List[Tuple[str, str]]:
        """
        Return [(url, filename), ...] for downloadable attachments.

        akls.kr uses javascript:download(boardid, b_idx, file_id, mime) links.
        Download URL: /module/board/download.php?boardid=BOARD&b_idx=POST_IDX&idx=FILE_ID

        - HWP  mime → skip (no usable text extractor)
        - PDF  mime → download
        - ''   mime → download (old posts: ZIP containing PDFs)
        - ZIP  mime → download
        """
        soup = BeautifulSoup(html, "lxml")
        results: List[Tuple[str, str]] = []

        js_re = re.compile(
            r"javascript:download\('([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']*)'\)"
        )

        for a in soup.find_all("a", href=True):
            href  = a["href"]
            fname = a.get_text(strip=True)

            m = js_re.match(href)
            if m:
                boardid, b_idx, file_id, mime = m.groups()
                if "hwp" in mime.lower():
                    log.debug("HWP (skipped): %s", fname)
                    continue
                dl_url = (
                    f"https://akls.kr/module/board/download.php"
                    f"?boardid={boardid}&b_idx={b_idx}&idx={file_id}"
                )
                results.append((dl_url, fname))
                continue

            # Direct file links (.pdf / .zip) — skip site-wide PDFs like 개인정보처리방침
            lower = (href + fname).lower()
            if any(lower.endswith(ext) for ext in (".pdf", ".zip")):
                if "처리방침" in fname or "privacy" in lower:
                    continue
                full_url = href if href.startswith("http") else urljoin("https://akls.kr", href)
                results.append((full_url, fname))

        return results

    async def _download_bytes(self, url: str) -> Optional[bytes]:
        """Download a file and return raw bytes."""
        try:
            async with self._session.get(url) as r:
                if r.status != 200:
                    return None
                return await r.read()
        except Exception as exc:
            log.warning("Download failed %s: %s", url, exc)
            return None

    def _extract_questions_from_pdf_bytes(
        self, pdf_bytes: bytes, meta: Dict, idx: int, fname: str
    ) -> List[RawQuestion]:
        """Extract questions from in-memory PDF bytes using pdfplumber."""
        try:
            import pdfplumber, io
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                pages = [
                    p.extract_text(x_tolerance=3, y_tolerance=3) or ""
                    for p in pdf.pages
                ]
            text = "\n".join(pages)
            if not text.strip():
                return []
            # Update meta from PDF filename if subject not yet set
            if "subject" not in meta:
                for alias, canonical in SUBJECT_ALIASES.items():
                    if alias in fname:
                        meta["subject"] = canonical
                        break
            if "subject" not in meta:
                meta["subject"] = _detect_subject(text)
            return _parse_questions_from_text(text, meta, idx)
        except Exception as exc:
            log.warning("PDF extraction failed (idx=%d, %s): %s", idx, fname, exc)
            return []

    def _extract_questions_from_zip_bytes(
        self, zip_bytes: bytes, meta: Dict, idx: int
    ) -> List[RawQuestion]:
        """Extract questions from in-memory ZIP (containing PDFs)."""
        import io, zipfile
        questions: List[RawQuestion] = []
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for info in zf.infolist():
                    # Korean ZIPs encode filenames in CP949; Python reads them as CP437 bytes
                    try:
                        name = info.filename.encode("cp437").decode("cp949")
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        name = info.filename
                    if name.lower().endswith(".pdf"):
                        pdf_bytes = zf.read(info.filename)
                        qs = self._extract_questions_from_pdf_bytes(
                            pdf_bytes, dict(meta), idx, name
                        )
                        questions.extend(qs)
                    else:
                        log.debug("ZIP member skipped: %s", name)
        except Exception as exc:
            log.warning("ZIP extraction failed (idx=%d): %s", idx, exc)
        return questions

    async def parse_post(self, html: str, idx: int) -> List[RawQuestion]:
        """
        Parse a single exam post.
        Downloads PDF/ZIP attachments and extracts questions from them.
        """
        soup = BeautifulSoup(html, "lxml")

        # Title
        title_el = soup.find("h3") or soup.find("h2") or soup.find(".subject")
        title = title_el.get_text(strip=True) if title_el else ""
        meta  = _parse_title_meta(title)

        # Try inline text first (older posts)
        content_el = (
            soup.find("div", class_=re.compile(r"content|view|bbs_con", re.I))
            or soup.find("td", class_=re.compile(r"content|view", re.I))
            or soup.find("div", id=re.compile(r"content|view", re.I))
            or soup.find("body")
        )
        inline_text = content_el.get_text(separator="\n") if content_el else ""
        if "subject" not in meta:
            meta["subject"] = _detect_subject(inline_text)

        inline_qs = _parse_questions_from_text(inline_text, meta, idx)
        if inline_qs:
            answer_key = _extract_answer_key(inline_text)
            for q in inline_qs:
                if q.question_number in answer_key:
                    q.correct_choice = answer_key[q.question_number]
            return inline_qs

        # Fall back: download PDF/ZIP attachments
        attachment_urls = self._extract_attachment_urls(html, idx)
        if not attachment_urls:
            log.debug("idx=%d: no inline questions, no PDF/ZIP attachments", idx)
            return []

        all_questions: List[RawQuestion] = []
        answer_key: Dict[int, int] = {}

        for url, fname in attachment_urls:
            log.debug("  Downloading: %s", fname)
            data = await self._download_bytes(url)
            if not data:
                continue

            # Detect file type by magic bytes first, fall back to extension
            if data[:4] == b'PK\x03\x04':        # ZIP magic
                qs = self._extract_questions_from_zip_bytes(data, dict(meta), idx)
            elif data[:4] == b'%PDF':             # PDF magic
                qs = self._extract_questions_from_pdf_bytes(data, dict(meta), idx, fname)
            else:
                fname_lower = fname.lower()
                if fname_lower.endswith(".zip"):
                    qs = self._extract_questions_from_zip_bytes(data, dict(meta), idx)
                elif fname_lower.endswith(".pdf"):
                    qs = self._extract_questions_from_pdf_bytes(data, dict(meta), idx, fname)
                else:
                    log.debug("Unknown file type (skipped): %s", fname)
                    continue

            # Separate question PDFs from answer key PDFs
            if any(kw in fname for kw in ("답", "정답", "answer", "가답")):
                # Likely answer key — extract key from this text
                import io, pdfplumber
                try:
                    with pdfplumber.open(io.BytesIO(data)) as pdf:
                        ak_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
                    answer_key.update(_extract_answer_key(ak_text))
                except Exception:
                    pass
            else:
                all_questions.extend(qs)

        # Apply answer key
        for q in all_questions:
            if q.question_number in answer_key:
                q.correct_choice = answer_key[q.question_number]

        return all_questions

    # ── Crawl all ─────────────────────────────────────────────────────────────

    async def crawl_all(
        self,
        idx_override: Optional[List[int]] = None,
    ) -> List[RawQuestion]:
        """Crawl all posts and return aggregated RawQuestion list."""
        if idx_override:
            indices = idx_override
        else:
            try:
                posts   = await self.get_post_list()
                indices = [p[0] for p in posts
                           if self.idx_min <= p[0] <= self.idx_max]
            except Exception:
                # Fallback: scan sequential indices
                indices = list(range(self.idx_min, self.idx_max + 1))

        sem   = asyncio.Semaphore(self.concurrency)
        all_q: List[RawQuestion] = []

        async def fetch_one(idx: int) -> List[RawQuestion]:
            async with sem:
                html = await self.fetch_post(idx)
                await asyncio.sleep(0.2)
                if not html:
                    return []
                qs = await self.parse_post(html, idx)
                log.info("  idx=%d → %d questions", idx, len(qs))
                return qs

        results = await asyncio.gather(*[fetch_one(i) for i in indices])
        for batch in results:
            all_q.extend(batch)

        log.info("Total questions crawled: %d", len(all_q))
        return all_q


# ── CLI entry point ───────────────────────────────────────────────────────────

async def _main(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    idx_range = (args.idx_min, args.idx_max)
    idx_list  = list(range(args.idx_min, args.idx_max + 1))

    async with AklsCrawler(
        concurrency=args.concurrency,
        idx_range=idx_range,
    ) as crawler:
        questions = await crawler.crawl_all(idx_override=idx_list)

    out = [q.model_dump(mode="json") for q in questions]
    Path(args.out).write_text(
        json.dumps({"questions": out}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved {len(questions)} questions → {args.out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl akls.kr mock exams")
    parser.add_argument("--out",         default="data/mock_raw.json")
    parser.add_argument("--idx-min",     type=int, default=1)
    parser.add_argument("--idx-max",     type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=5)
    asyncio.run(_main(parser.parse_args()))
