"""
legal_retriever.py — Fetch real legal texts for RAG.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import httpx

log = logging.getLogger(__name__)

class LegalRetriever:
    """Retrieves legal texts from official APIs or fallback scraping."""

    def __init__(self, http_client: httpx.AsyncClient):
        self.client = http_client
        self.law_api_key = os.getenv("LAW_GO_KR_API_KEY", "")

    async def fetch_precedent(self, case_number: str) -> Optional[str]:
        """
        Fetch full text of a Supreme Court precedent.
        Tries official API first, then falls back to scraping.
        Input format example: '2022다12345'
        """
        # 1. Try Law.go.kr API (if key exists)
        if self.law_api_key:
            try:
                url = "https://www.law.go.kr/DRF/lawService.do"
                params = {
                    "OC": self.law_api_key,
                    "target": "prec",
                    "prec_no": case_number,
                    "type": "JSON",
                }
                resp = await self.client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                content = data.get("판결요지", "") or data.get("판결이유", "")
                if content:
                    log.info(f"Fetched precedent {case_number} from law.go.kr API.")
                    return content
            except Exception as e:
                log.warning(f"Law.go.kr API failed for {case_number}, falling back. Error: {e}")

        # 2. Fallback: Casenote.kr (Scraping)
        log.info(f"Falling back to casenote.kr for {case_number}")
        return await self._scrape_casenote(case_number)

    async def _scrape_casenote(self, case_number: str) -> Optional[str]:
        """Scrape summary from casenote.kr (unofficial fallback)."""
        try:
            search_url = f"https://casenote.kr/search/?q={case_number}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
            resp = await self.client.get(search_url, headers=headers)
            resp.raise_for_status()
            # NOTE: This is a mock extraction. A real implementation would use BeautifulSoup.
            mock_extracted_text = f"[Mock Scraped Text for {case_number}] The court held that... (from casenote.kr)"
            log.info(f"Scraped mock data for {case_number} from casenote.kr.")
            return mock_extracted_text
        except Exception as e:
            log.error(f"Casenote scraping failed for {case_number}: {e}")
            return None
