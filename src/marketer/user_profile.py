"""USP Memory Gateway GraphQL client.

Fetches client identity + insights from the User Profile service.
The query is a hardcoded static string — the LLM has no role in constructing it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_QUERY = """
query FetchUserProfile($accountUuid: String!) {
  identity(accountUuid: $accountUuid) {
    uuid
    accountUuid
    brand {
      colors
      communicationLang
      communicationStyle
      designStyle
      font
      hasMaterial
      keywords
      postContentStyle
      logoUrl
    }
    company {
      name
      category
      subcategory
      country
      businessPhone
      email
      websiteUrl
      historyAndFounder
      targetCustomer
      productServices
      storeType
      location
    }
    socialMedia {
      instagramUrl
      facebookUrl
      tiktokUrl
      linkedinUrl
    }
    status {
      isCompleted
    }
    timestamps {
      updatedAt
    }
  }
  insights(accountUuid: $accountUuid) {
    uuid
    key
    insight
    active
    confidence
    sourceIdentifier
    lastUpdateSource
    updatedAt
  }
}
"""


@dataclass
class UserInsight:
    key: str
    insight: str
    confidence: int | None
    source_identifier: str | None
    updated_at: str | None


@dataclass
class IdentityData:
    uuid: str | None
    account_uuid: str | None
    brand: dict[str, Any]
    company: dict[str, Any]
    social_media: dict[str, Any]


@dataclass
class UserProfile:
    identity: IdentityData | None
    insights: list[UserInsight]
    fetched_at: str

    def to_storage_dict(self) -> dict[str, Any]:
        return {
            "fetched_at": self.fetched_at,
            "identity": {
                "uuid": self.identity.uuid,
                "brand": self.identity.brand,
                "company": self.identity.company,
                "socialMedia": self.identity.social_media,
            }
            if self.identity
            else None,
            "insights": [
                {
                    "key": i.key,
                    "insight": i.insight,
                    "active": True,
                    "confidence": i.confidence,
                    "sourceIdentifier": i.source_identifier,
                    "updatedAt": i.updated_at,
                }
                for i in self.insights
            ],
        }


async def fetch_user_profile(
    account_uuid: str | None,
    endpoint: str,
    api_key: str,
    timeout: float = 5.0,
) -> UserProfile | None:
    """Fetch identity + insights from USP Memory Gateway.

    Returns None on any failure (network, auth, parse error).
    Caller should warn and continue with brief-only context.
    """
    if not endpoint or not api_key:
        logger.warning('"event=user_profile_skipped reason=not_configured"')
        return None
    if not account_uuid:
        logger.warning('"event=user_profile_skipped reason=no_account_uuid"')
        return None

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                endpoint,
                json={"query": _QUERY, "variables": {"accountUuid": account_uuid}},
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
            )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.TimeoutException:
        logger.warning(
            '"event=user_profile_unavailable reason=timeout account_uuid=%s"',
            account_uuid,
        )
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning(
            '"event=user_profile_unavailable reason=http_%s account_uuid=%s"',
            exc.response.status_code,
            account_uuid,
        )
        return None
    except Exception:
        logger.warning(
            '"event=user_profile_unavailable reason=request_failed account_uuid=%s"',
            account_uuid,
            exc_info=True,
        )
        return None

    errors = payload.get("errors")
    if errors:
        logger.warning(
            '"event=user_profile_unavailable reason=graphql_error errors=%s"', errors
        )

    data = payload.get("data") or {}
    identity_raw = data.get("identity")
    insights_raw = data.get("insights") or []
    fetched_at = datetime.now(timezone.utc).isoformat()

    if identity_raw is None:
        logger.info('"event=user_profile_not_found account_uuid=%s"', account_uuid)
        return UserProfile(identity=None, insights=[], fetched_at=fetched_at)

    identity = IdentityData(
        uuid=identity_raw.get("uuid"),
        account_uuid=identity_raw.get("accountUuid"),
        brand=identity_raw.get("brand") or {},
        company=identity_raw.get("company") or {},
        social_media=identity_raw.get("socialMedia") or {},
    )

    insights: list[UserInsight] = []
    for raw in insights_raw:
        if not isinstance(raw, dict) or not raw.get("active"):
            continue
        key = raw.get("key")
        insight_text = raw.get("insight")
        if not key or not insight_text:
            continue
        insights.append(
            UserInsight(
                key=key,
                insight=insight_text,
                confidence=raw.get("confidence"),
                source_identifier=raw.get("sourceIdentifier"),
                updated_at=raw.get("updatedAt"),
            )
        )
    insights.sort(key=lambda i: i.confidence or 0, reverse=True)

    return UserProfile(identity=identity, insights=insights, fetched_at=fetched_at)
