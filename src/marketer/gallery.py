"""Gallery Image Pool — HTTP client, eligibility filter, Stage 1 metadata scorer.

This module is standalone: it has no dependency on the normalizer, reasoner,
or any LLM module. It can be imported and tested independently.

Spec ref: OpenSpec/11-gallery-image-pool.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from marketer.schemas.internal_context import GalleryPool, GalleryPoolItem

logger = logging.getLogger(__name__)

# Weights for Stage 1 metadata scoring
_HIGH = 3.0
_MEDIUM = 2.0
_LOW = 1.0

# Signals that indicate a person-centric brief (triggers people-presence bonus)
_PERSON_SIGNALS = frozenset(
    {"founder", "fundador", "team", "equipo", "persona", "person", "staff", "people"}
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def is_eligible(item: dict[str, Any], now: datetime) -> bool:
    """Return True iff item passes all eligibility criteria (§3.1).

    Criteria: type == 'img', used_at is null, locked_until is null or past.
    """
    if item.get("type") != "img":
        return False
    if item.get("used_at") is not None:
        return False
    locked_until = _parse_dt(item.get("locked_until"))
    if locked_until is not None and locked_until >= now:
        return False
    return True


def score_image(item: dict[str, Any], task_context: dict[str, Any]) -> float:
    """Deterministic metadata relevance score. No LLM. Returns 0.0 for empty metadata.

    Scoring signals and weights (§3.2):
    - Tag overlap with brief keywords + user request tokens: HIGH (3.0 per hit)
    - Owner description keyword match vs user request: HIGH (3.0 per hit)
    - Subject relevance vs user request: MEDIUM (2.0 per hit)
    - Mood/tone alignment vs brief tone: MEDIUM (2.0 per hit)
    - People presence (bonus when brief is person-centric): MEDIUM (2.0)
    - Category match vs task context: LOW (1.0)
    - Style alignment vs brief design style: LOW (1.0)
    """
    metadata = item.get("metadata") or {}
    if not metadata:
        return 0.0

    user_request = (task_context.get("user_request") or "").lower()
    brief_keywords = [k.lower() for k in (task_context.get("brief_keywords") or [])]
    brief_tone = (task_context.get("brief_tone") or "").lower()
    brief_design_style = (task_context.get("brief_design_style") or "").lower()

    request_tokens = {t for t in user_request.split() if len(t) > 2}
    keyword_tokens = set(brief_keywords)
    scoring_tokens = request_tokens | keyword_tokens

    score = 0.0

    # HIGH: tag overlap (intersection with scoring tokens)
    tags = [t.lower() for t in (metadata.get("tags") or []) if isinstance(t, str)]
    for tag in tags:
        if tag in scoring_tokens or any(tag in tok for tok in scoring_tokens):
            score += _HIGH

    # HIGH: owner description keyword presence
    description = (item.get("description") or "").lower()
    if description and request_tokens:
        desc_hits = sum(1 for tok in request_tokens if tok in description)
        score += desc_hits * _HIGH

    # MEDIUM: subject relevance
    subject = (metadata.get("subject") or "").lower()
    if subject and request_tokens:
        subj_hits = sum(1 for tok in request_tokens if tok in subject)
        score += subj_hits * _MEDIUM

    # MEDIUM: mood/tone alignment
    mood = (metadata.get("mood") or "").lower()
    if mood and brief_tone:
        tone_tokens = {t for t in brief_tone.split() if len(t) > 2}
        mood_tokens = {t for t in mood.split() if len(t) > 2}
        tone_hits = len(tone_tokens & mood_tokens)
        score += tone_hits * _MEDIUM

    # MEDIUM: people presence bonus (when brief is person-centric)
    people = (metadata.get("people") or "").lower()
    if people:
        all_context = user_request + " " + " ".join(brief_keywords)
        if any(sig in all_context for sig in _PERSON_SIGNALS):
            score += _MEDIUM

    # LOW: category match
    category = (item.get("category") or "").lower()
    if category and scoring_tokens:
        cat_tokens = {t for t in category.split() if len(t) > 2}
        if cat_tokens & scoring_tokens:
            score += _LOW

    # LOW: style alignment
    style = (metadata.get("style") or "").lower()
    if style and brief_design_style:
        style_tokens = {t for t in style.split() if len(t) > 2}
        design_tokens = {t for t in brief_design_style.split() if len(t) > 2}
        if style_tokens & design_tokens:
            score += _LOW

    return score


def _build_shortlist(
    raw_items: list[dict[str, Any]],
    task_context: dict[str, Any],
    vision_candidates: int,
) -> tuple[list[GalleryPoolItem], int]:
    """Filter, score, sort, and cap the eligible image pool.

    Returns (shortlist, total_eligible).
    """
    now = _now_utc()
    eligible = [item for item in raw_items if is_eligible(item, now)]
    total_eligible = len(eligible)

    if not eligible:
        return [], 0

    scored: list[tuple[float, dict[str, Any]]] = [
        (score_image(item, task_context), item) for item in eligible
    ]
    scored.sort(key=lambda x: x[0], reverse=True)

    shortlist: list[GalleryPoolItem] = []
    for sc, item in scored[:vision_candidates]:
        shortlist.append(
            GalleryPoolItem(
                uuid=item.get("uuid", ""),
                content_url=item.get("content", ""),
                category=item.get("category") or "",
                description=item.get("description"),
                used_at=item.get("used_at"),
                metadata=item.get("metadata") or {},
                score=sc,
            )
        )

    return shortlist, total_eligible


async def fetch_gallery_pool(
    account_uuid: str,
    base_url: str,
    api_key: str,
    task_context: dict[str, Any],
    vision_candidates: int = 5,
    page_size: int = 50,
    timeout: float = 5.0,
) -> tuple[GalleryPool | None, str | None]:
    """Fetch, filter, score, and shortlist gallery images for the given account.

    Returns (pool, warning_code):
    - pool is None on any fetch error; caller should fall back to ROUTER-gate gallery.
    - warning_code is set when a non-ideal condition occurred (including None = all good).
    """
    url = f"{base_url}/accounts/{account_uuid}/gallery"
    params = {"page": 1, "size": page_size}
    headers = {"X-API-KEY": api_key, "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params, headers=headers)
    except httpx.TimeoutException:
        logger.warning('"event=gallery_api_timeout account_uuid=%s"', account_uuid)
        return None, "gallery_api_unavailable"
    except Exception:
        logger.warning(
            '"event=gallery_api_request_failed account_uuid=%s"',
            account_uuid,
            exc_info=True,
        )
        return None, "gallery_api_unavailable"

    if resp.status_code == 404:
        logger.info('"event=gallery_api_not_found account_uuid=%s"', account_uuid)
        return None, "gallery_api_not_found"

    if resp.status_code >= 400:
        logger.warning(
            '"event=gallery_api_http_error status=%s account_uuid=%s"',
            resp.status_code,
            account_uuid,
        )
        return None, "gallery_api_unavailable"

    try:
        body = resp.json()
    except Exception:
        logger.warning('"event=gallery_api_parse_error account_uuid=%s"', account_uuid)
        return None, "gallery_api_unavailable"

    # Resolve items — API may return a bare list or a paginated wrapper object.
    # Observed shape: {"total_items": N, "total_pages": M, "results": [...], ...}
    api_total_items: int | None = None
    if isinstance(body, list):
        raw_items: list[dict[str, Any]] = body
    elif isinstance(body, dict):
        raw_items = body.get("results") or []
        api_total_items = body.get("total_items")
        if not isinstance(raw_items, list):
            logger.warning(
                '"event=gallery_api_unexpected_shape account_uuid=%s"', account_uuid
            )
            raw_items = []
    else:
        logger.warning(
            '"event=gallery_api_unexpected_shape account_uuid=%s"', account_uuid
        )
        raw_items = []

    total_fetched = len(raw_items)

    # Truncation: either the API reports more items than we fetched, or we got a
    # full page (exact page_size) which suggests more pages may exist.
    if api_total_items is not None:
        truncated = api_total_items > total_fetched
    else:
        truncated = total_fetched >= page_size
    if truncated:
        logger.info(
            '"event=gallery_pool_truncated total_fetched=%d account_uuid=%s"',
            total_fetched,
            account_uuid,
        )

    shortlist, total_eligible = _build_shortlist(raw_items, task_context, vision_candidates)

    warning_code: str | None = None
    if truncated:
        warning_code = "gallery_pool_truncated"
    elif total_eligible == 0:
        warning_code = "gallery_pool_empty"

    return GalleryPool(
        shortlist=shortlist,
        total_fetched=total_fetched,
        total_eligible=total_eligible,
        truncated=truncated,
        source="gallery_api",
    ), warning_code
