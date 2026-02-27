"""
legal_retriever.py — Fetch real legal texts for RAG using law.go.kr API with
BeautifulSoup fallback scraping from casenote.kr / lbox.kr.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class LegalRetriever:
    """Retrieves legal texts from official APIs or fallback web scraping."""

    def __init__(self, http_client: httpx.AsyncClient):
        self.client = http_client
        self.law_api_key = os.getenv("LAW_GO_KR_API_KEY", "")

    # ── Public API ────────────────────────────────────────────────────────────

    async def fetch_precedent(self, case_number: str) -> Optional[str]:
        """
        Fetch full text of a Supreme Court precedent.
        1st: law.go.kr official API (if key configured)
        2nd: casenote.kr scraping
        3rd: lbox.kr scraping
        """
        if self.law_api_key:
            result = await self._fetch_law_go_kr(case_number)
            if result:
                return result

        result = await self._scrape_casenote(case_number)
        if result:
            return result

        return await self._scrape_lbox(case_number)

    async def fetch_statute(self, statute_name: str, article: str = "") -> Optional[str]:
        """Fetch statute text from law.go.kr."""
        if not self.law_api_key:
            return None
        try:
            params = {
                "OC": self.law_api_key,
                "target": "law",
                "query": statute_name,
                "type": "JSON",
            }
            resp = await self.client.get(
                "https://www.law.go.kr/DRF/lawSearch.do", params=params, timeout=8.0
            )
            resp.raise_for_status()
            data = resp.json()
            laws = data.get("LawSearch", {}).get("law", [])
            if not laws:
                return None
            # Take first match, fetch full text
            law_id = laws[0].get("법령ID")
            if not law_id:
                return None
            detail_resp = await self.client.get(
                "https://www.law.go.kr/DRF/lawService.do",
                params={"OC": self.law_api_key, "target": "law", "ID": law_id, "type": "JSON"},
                timeout=8.0,
            )
            detail_resp.raise_for_status()
            detail = detail_resp.json()
            content = detail.get("법령", {}).get("조문", "")
            if article:
                # Try to extract specific article
                pattern = rf"제\s*{article}\s*조[^제]*"
                match = re.search(pattern, content)
                if match:
                    return match.group(0)[:800]
            return str(content)[:1200] if content else None
        except Exception as exc:
            log.warning("law.go.kr statute fetch failed for '%s': %s", statute_name, exc)
            return None

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_law_go_kr(self, case_number: str) -> Optional[str]:
        """Official law.go.kr precedent API."""
        try:
            resp = await self.client.get(
                "https://www.law.go.kr/DRF/lawService.do",
                params={
                    "OC": self.law_api_key,
                    "target": "prec",
                    "prec_no": case_number,
                    "type": "JSON",
                },
                timeout=8.0,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("판결요지") or data.get("판시사항") or data.get("판결이유", "")
            if content and len(content) > 30:
                log.info("law.go.kr API: fetched precedent %s", case_number)
                return str(content)[:2000]
        except Exception as exc:
            log.warning("law.go.kr API failed for %s: %s", case_number, exc)
        return None

    async def _scrape_casenote(self, case_number: str) -> Optional[str]:
        """Scrape precedent summary from casenote.kr."""
        try:
            search_url = f"https://casenote.kr/search/?q={case_number}"
            resp = await self.client.get(
                search_url,
                headers={"User-Agent": _USER_AGENT},
                timeout=10.0,
                follow_redirects=True,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # Find first result link
            result_link = soup.select_one("a.case-link, .search-result a, .result-item a")
            if not result_link:
                result_link = soup.find("a", href=re.compile(r"/\d{4}[가-힣]"))
            if not result_link:
                return None

            case_url = result_link.get("href", "")
            if not case_url.startswith("http"):
                case_url = "https://casenote.kr" + case_url

            case_resp = await self.client.get(
                case_url,
                headers={"User-Agent": _USER_AGENT},
                timeout=10.0,
                follow_redirects=True,
            )
            case_resp.raise_for_status()
            case_soup = BeautifulSoup(case_resp.text, "lxml")

            # Extract verdict summary
            for selector in [
                ".verdict-summary", ".case-summary", ".판결요지",
                "section.summary", ".holding", "#summary",
            ]:
                el = case_soup.select_one(selector)
                if el:
                    text = el.get_text(separator="\n", strip=True)
                    if len(text) > 50:
                        log.info("casenote.kr: scraped %s (%d chars)", case_number, len(text))
                        return text[:2000]

            # Fallback: largest <p> block
            paragraphs = case_soup.find_all("p")
            if paragraphs:
                best = max(paragraphs, key=lambda p: len(p.get_text()))
                text = best.get_text(separator=" ", strip=True)
                if len(text) > 80:
                    log.info("casenote.kr fallback paragraph for %s", case_number)
                    return text[:2000]
        except Exception as exc:
            log.warning("casenote.kr scraping failed for %s: %s", case_number, exc)
        return None

    async def _scrape_lbox(self, case_number: str) -> Optional[str]:
        """Scrape precedent summary from lbox.kr as final fallback."""
        try:
            search_url = f"https://lbox.kr/case?q={case_number}"
            resp = await self.client.get(
                search_url,
                headers={"User-Agent": _USER_AGENT},
                timeout=10.0,
                follow_redirects=True,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            for selector in [
                ".case-holding", ".판결요지", ".holding-text",
                ".summary-text", "article p",
            ]:
                el = soup.select_one(selector)
                if el:
                    text = el.get_text(separator="\n", strip=True)
                    if len(text) > 50:
                        log.info("lbox.kr: scraped %s (%d chars)", case_number, len(text))
                        return text[:2000]
        except Exception as exc:
            log.warning("lbox.kr scraping failed for %s: %s", case_number, exc)
        return None
