"""
services/api_cache_service.py — Deterministic SQLite API Response Caching Service
==================================================================================
Provides persistent, local caching for external API interactions (Gemini, Apollo, Snov,
DNS MX records, Job APIs) to eliminate redundant network requests, reduce latency, and
protect API quotas.

Core Security & Reliability Guarantees:
1. Deterministic hashing: SHA-256 over normalized (endpoint, sorted_sanitized_params).
2. Credential Sanitization: Strips API keys, bearer tokens, secrets, and auth headers
   BEFORE computing cache keys and BEFORE persisting cached parameters.
3. Success-only caching: Only HTTP 200 / successful response payloads are cached.
   Failures, 4xx/5xx errors, and unauthorized responses are NEVER cached as successes.
4. Safe TTL expiration and explicit invalidation (by key, endpoint, or provider).
"""

import hashlib
import json
import logging
from typing import Dict, Any, Optional, Union
from datetime import datetime, timezone

import config as cfg
from database.db_manager import DatabaseManager

logger = logging.getLogger("CareerPilot.ApiCacheService")

# Sensitive parameter keys that MUST NEVER be cached or hashed with raw secrets
SENSITIVE_PARAM_KEYS = {
    "api_key", "apikey", "key", "token", "auth", "authorization",
    "password", "secret", "app_password", "client_secret", "access_token"
}


class ApiCacheService:
    """
    Persistent SQLite API Cache Manager with deterministic keys and credential scrubbing.
    """

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        default_ttl_seconds: int = 86400,
    ):
        self.db = db_manager or DatabaseManager(cfg.DB_PATH)
        self.default_ttl = int(default_ttl_seconds)

    @staticmethod
    def sanitize_parameters(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Deeply scrub credentials and sensitive tokens from request parameters.
        Returns a clean, safe dictionary suitable for hashing and persistence.
        """
        if not params:
            return {}

        clean = {}
        for k, v in params.items():
            if str(k).lower() in SENSITIVE_PARAM_KEYS:
                continue
            if isinstance(v, dict):
                clean[k] = ApiCacheService.sanitize_parameters(v)
            elif isinstance(v, (list, tuple)):
                clean[k] = [
                    ApiCacheService.sanitize_parameters(item) if isinstance(item, dict) else item
                    for item in v
                ]
            else:
                clean[k] = v
        return clean

    @classmethod
    def generate_cache_key(
        cls,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generate a deterministic, reproducible SHA-256 cache key.
        Endpoint and sanitized parameters are canonicalized with sorted keys.
        """
        clean_endpoint = str(endpoint or "").strip().lower()
        clean_params = cls.sanitize_parameters(params)
        canonical_payload = f"{clean_endpoint}:{json.dumps(clean_params, sort_keys=True, separators=(',', ':'))}"
        return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()

    def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        source: str = "generic",
    ) -> Optional[Any]:
        """
        Fetch valid, unexpired cached response payload.
        Returns None on cache miss, expiration, or invalidation.
        """
        key = self.generate_cache_key(endpoint, params)
        row = self.db.get_api_response_cache(key)

        if not row:
            logger.debug(f"[ApiCache] Cache MISS for {source} [{endpoint}] (Key: {key[:8]}...)")
            return None

        # Verify unexpired
        expires_at_str = row.get("expires_at")
        if expires_at_str:
            clean_exp = expires_at_str.replace("T", " ")
            try:
                exp_dt = datetime.strptime(clean_exp[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if exp_dt <= datetime.now(timezone.utc):
                    logger.debug(f"[ApiCache] Cache EXPIRED for {source} [{endpoint}] (Key: {key[:8]}...)")
                    return None
            except Exception:
                pass

        raw_response = row.get("response_data")
        if raw_response is None:
            return None

        logger.debug(f"[ApiCache] Cache HIT for {source} [{endpoint}] (Key: {key[:8]}...)")
        try:
            return json.loads(raw_response)
        except Exception:
            return raw_response

    def set(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]],
        response_data: Any,
        source: str = "generic",
        ttl_seconds: Optional[int] = None,
        status_code: int = 200,
    ) -> Optional[str]:
        """
        Store a successful API response in the persistent SQLite cache.
        Rejects non-200 responses, empty payloads, and handles param scrubbing.
        """
        # Guard: Never cache unsuccessful, unauthorized, or error responses
        if status_code != 200 or response_data is None:
            logger.debug(f"[ApiCache] Skipped caching non-success response (status: {status_code})")
            return None

        ttl = int(ttl_seconds if ttl_seconds is not None else self.default_ttl)
        key = self.generate_cache_key(endpoint, params)
        clean_params = self.sanitize_parameters(params)

        # Serialize response data safely
        if isinstance(response_data, (dict, list, tuple, bool, int, float)):
            serialized_response = json.dumps(response_data)
        else:
            serialized_response = str(response_data)

        self.db.set_api_response_cache(
            cache_key=key,
            endpoint=endpoint,
            source=source,
            request_params=json.dumps(clean_params),
            response_data=serialized_response,
            ttl_seconds=ttl,
            status_code=status_code,
        )

        logger.debug(f"[ApiCache] Cached {source} [{endpoint}] (TTL: {ttl}s, Key: {key[:8]}...)")
        return key

    def invalidate(
        self,
        cache_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        source: Optional[str] = None,
    ) -> int:
        """
        Explicitly invalidate cached items by key, endpoint, or provider.
        """
        count = self.db.invalidate_api_cache(
            cache_key=cache_key,
            endpoint=endpoint,
            source=source,
        )
        logger.info(f"[ApiCache] Invalidated {count} cache entries (Key={cache_key}, Endpoint={endpoint}, Source={source})")
        return count

    def prune_expired(self) -> int:
        """
        Purge all expired and invalidated entries from the SQLite cache table.
        """
        count = self.db.prune_expired_api_cache()
        logger.info(f"[ApiCache] Pruned {count} expired/invalidated cache entries")
        return count
