"""
services/memory_service.py — Cross-Session Memory Management Engine (F6 / Assignment Page 7)
=============================================================================================
Maintains contextual, persistent memory across autonomous agent sessions:
  1. Persistent Daily Summaries: Reflects actual confirmed SQLite operations and counts.
  2. Startup Context Synthesis: Injects previous summaries, applied hashes, and interaction notes
     into AgentState to prevent repeating actions while keeping DB constraints authoritative.
  3. Deterministic API Response Caching: Integrated via ApiCacheService to eliminate duplicate calls.
  4. Bonus Semantic Memory Retrieval: Embedding-based similarity search using Sentence-Transformers,
     with graceful offline fallback to token-overlap ranking when the embedding model is unavailable.
  5. Bonus Memory Retention & Pruning: Safely prunes expired cache and old summaries, while strictly
     preserving application history, approval records, audit logs, and candidate insights.
"""

import os
import json
import re
import logging
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

import numpy as np

import config as cfg
from database.db_manager import DatabaseManager
from services.api_cache_service import ApiCacheService

logger = logging.getLogger("CareerPilot.MemoryService")


class MemoryService:
    """
    Unified Cross-Session Memory Engine with Semantic Search, Caching, and Safe Pruning.
    """

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        cache_path: Optional[str] = None,
        embedding_service: Optional[Any] = None,
    ):
        self.db = db_manager or DatabaseManager(cfg.DB_PATH)
        data_dir = Path(cfg.DATA_DIR)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = Path(cache_path or (data_dir / "mem0_cache.json"))
        self.api_cache = ApiCacheService(self.db)
        self._init_cache_file()

        # Embedding model instance (lazy loaded)
        self._embedding_service = embedding_service
        self._embedding_initialized = False

    def _init_cache_file(self):
        """Ensure local memory cache file exists."""
        if not self.cache_path.exists():
            try:
                with open(self.cache_path, "w", encoding="utf-8") as f:
                    json.dump({"created_at": datetime.now().isoformat(), "entries": {}}, f, indent=2)
            except Exception as e:
                logger.warning(f"[MemoryService] Could not initialize cache file: {e}")

    def _get_embedding_service(self) -> Optional[Any]:
        """Lazy load SentenceTransformer embedding service with offline fallback."""
        if self._embedding_initialized:
            return self._embedding_service

        self._embedding_initialized = True
        try:
            from services.embedding_service import EmbeddingService
            svc = EmbeddingService()
            if svc._ensure_model():
                self._embedding_service = svc
                logger.info("[MemoryService] Semantic embedding service successfully initialized.")
            else:
                self._embedding_service = None
                logger.info("[MemoryService] Embedding model unavailable. Will use keyword fallback.")
        except Exception as e:
            logger.debug(f"[MemoryService] Embedding service load notice: {e}")
            self._embedding_service = None

        return self._embedding_service

    # ------------------------------------------------------------------
    # Core Memory CRUD Operations
    # ------------------------------------------------------------------

    def remember(
        self,
        key: str,
        content: str,
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Store or update a cross-session memory entry in SQLite and cache.
        Automatically indexes embeddings for semantic retrieval when available.
        """
        clean_key = str(key).strip().lower()
        meta = metadata or {}
        meta["updated_at"] = datetime.now().isoformat()

        # 1. Persist to SQLite memory_store
        try:
            self.db.store_memory(
                memory_key=clean_key,
                category=category,
                content=content,
                metadata=meta
            )
            logger.info(f"[MemoryService] Remembered key '{clean_key}' under category '{category}'")
        except Exception as e:
            logger.error(f"[MemoryService] Failed to store memory in SQLite: {e}")
            return False

        # 2. Update local JSON cache
        self._update_local_cache(clean_key, content, category, meta)

        # 3. Optional semantic embedding indexing
        self._embed_memory_if_eligible(clean_key, content, category)

        # 4. Optional Mem0 cloud sync (if enabled)
        if cfg.USE_MEM0:
            self._sync_to_mem0_cloud(clean_key, content, category, meta)

        return True

    def recall(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific memory item by key.
        """
        clean_key = str(key).strip().lower()
        # Check SQLite first
        try:
            mem = self.db.get_memory(clean_key)
            if mem:
                return mem
        except Exception as e:
            logger.debug(f"[MemoryService] SQLite recall exception: {e}")

        # Fallback to local cache file
        return self._get_from_local_cache(clean_key)

    def recall_by_category(self, category: str) -> List[Dict[str, Any]]:
        """
        Retrieve all memories belonging to a specific category.
        """
        results = []
        try:
            with self.db._get_conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM memory_store WHERE category = ? ORDER BY updated_at DESC",
                    (category,)
                ).fetchall()
                for r in rows:
                    item = dict(r)
                    item["metadata"] = json.loads(item.get("metadata") or "{}")
                    results.append(item)
        except Exception as e:
            logger.error(f"[MemoryService] Error querying category '{category}': {e}")

        return results

    # ------------------------------------------------------------------
    # PHASE 1: Daily Memory Summaries
    # ------------------------------------------------------------------

    def save_daily_summary(
        self,
        run_id: str,
        date_str: str,
        metrics: Dict[str, Any],
        errors: Optional[List[str]] = None,
        completed_actions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Save persistent daily summary reflecting actual database records and confirmed operations.
        Counts reflect verified records rather than mere in-memory attempts.
        """
        err_list = errors or []
        act_list = completed_actions or []

        # Fetch actual verified database counts to guarantee data accuracy
        actual_db_counts = self.db.get_actual_workflow_counts(run_id=run_id)

        scraped = int(metrics.get("scraped", metrics.get("total_scraped", 0)))
        filtered = int(metrics.get("passed_filters", metrics.get("filtered_passed", 0)))
        scored = int(metrics.get("scored", metrics.get("scored_jobs", 0)))

        # Actual confirmed applications and sent emails directly from SQLite
        applied = actual_db_counts["confirmed_applications"]
        emailed = actual_db_counts["confirmed_emails_sent"]
        pending = actual_db_counts["pending_approvals"]
        failed = len(err_list) + actual_db_counts["failed_applications"] + actual_db_counts["failed_emails"]

        summary_text = (
            f"Daily Run {run_id} on {date_str}: {scraped} scraped, {filtered} filtered, "
            f"{scored} scored | Confirmed Applied: {applied}, Confirmed Emailed: {emailed}, "
            f"Pending Approvals: {pending}, Failed: {failed}."
        )

        record = {
            "run_id": run_id,
            "date": date_str,
            "total_scraped": scraped,
            "total_filtered": filtered,
            "total_scored": scored,
            "total_applications": applied,
            "total_emails_sent": emailed,
            "pending_approvals": pending,
            "failed_operations": failed,
            "important_errors": err_list,
            "completed_actions": act_list,
            "summary_text": summary_text,
        }

        # 1. Persist to daily_memory_summaries table
        self.db.save_daily_memory_summary(record)

        # 2. Store in memory_store for category indexing
        self.remember(
            key=f"daily_summary_{date_str}_{run_id}",
            content=summary_text,
            category="daily_summary",
            metadata=record,
        )

        # 3. Update pointer to latest summary
        self.remember(
            key="latest_daily_summary",
            content=summary_text,
            category="daily_summary",
            metadata=record,
        )

        logger.info(f"[MemoryService] Persistent daily summary recorded for run {run_id}")
        return record

    def get_recent_daily_summaries(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Retrieve recent daily memory summaries."""
        return self.db.get_daily_memory_summaries(limit=limit)

    # ------------------------------------------------------------------
    # PHASE 2: Load Previous Context & Startup Synthesis
    # ------------------------------------------------------------------

    def synthesize_startup_context(self) -> Dict[str, Any]:
        """
        Synthesize cross-session context at workflow initialization:
        - Retrieves latest daily summaries
        - Identifies completed applications (job hashes)
        - Identifies sent email recipients
        - Identifies pending approval queue items
        - Retrieves candidate insights and past company interaction notes
        """
        latest_summary = self.db.get_latest_daily_memory_summary()
        recent_summaries = self.db.get_daily_memory_summaries(limit=5)

        # Authoritative protection sets
        applied_hashes = list(self.db.get_applied_job_hashes())
        sent_recipients = list(self.db.get_sent_email_recipients())
        pending_approvals = self.db.get_pending_approval_refs()

        # Category memories
        candidate_insights = self.recall_by_category("candidate_insights")
        company_interactions = self.recall_by_category("company_interactions")

        logger.info(
            f"[MemoryService] Startup context synthesized: {len(applied_hashes)} previous applications, "
            f"{len(sent_recipients)} sent recipients, {len(pending_approvals)} pending approvals, "
            f"{len(recent_summaries)} historical summaries."
        )

        return {
            "latest_daily_summary": latest_summary,
            "recent_daily_summaries": recent_summaries,
            "previously_applied_hashes": applied_hashes,
            "previously_emailed_recipients": sent_recipients,
            "pending_approvals": pending_approvals,
            "candidate_insights": candidate_insights,
            "company_interactions": company_interactions,
            "startup_timestamp": datetime.now().isoformat(),
        }

    # ------------------------------------------------------------------
    # PHASE 4: Bonus — Semantic Memory Retrieval
    # ------------------------------------------------------------------

    def _embed_memory_if_eligible(self, memory_key: str, content: str, category: str):
        """Generate and save vector embedding for eligible memory categories."""
        # Index high-value memory categories
        eligible_categories = {"candidate_insights", "company_interactions", "daily_summary", "general"}
        if category not in eligible_categories:
            return

        emb_service = self._get_embedding_service()
        if not emb_service or not emb_service._ensure_model():
            return

        try:
            mem_row = self.db.get_memory(memory_key)
            if not mem_row or not mem_row.get("id"):
                return

            mem_id = mem_row["id"]
            if hasattr(emb_service, "encode_text"):
                vector = emb_service.encode_text(content)
            else:
                vector = emb_service._model.encode(content, show_progress_bar=False)

            if vector is None:
                return

            self.db.save_memory_embedding(
                memory_id=mem_id,
                memory_key=memory_key,
                embedding=vector.tolist() if hasattr(vector, "tolist") else list(vector),
                model_name=getattr(emb_service, "model_name", "all-MiniLM-L6-v2"),
            )
            logger.debug(f"[MemoryService] Indexed vector embedding for memory #{mem_id} ('{memory_key}')")
        except Exception as e:
            logger.debug(f"[MemoryService] Could not index embedding for '{memory_key}': {e}")

    def semantic_search(
        self,
        query: str,
        limit: int = 5,
        min_similarity: float = 0.10,
    ) -> List[Dict[str, Any]]:
        """
        Embedding-based semantic similarity search across stored memories.
        If embedding model is unavailable or throws, gracefully degrades to keyword ranking.
        """
        if not query or not query.strip():
            return []

        emb_service = self._get_embedding_service()

        # Fallback to keyword search if embedding model is unavailable
        if not emb_service or not emb_service._ensure_model():
            logger.info("[MemoryService] Embedding model offline/unavailable. Falling back to keyword search.")
            keyword_results = self.search_memory(query, limit=limit)
            for r in keyword_results:
                r["retrieval_mode"] = "keyword_fallback"
            return keyword_results

        try:
            # Dense query vector
            if hasattr(emb_service, "encode_text"):
                query_vec = emb_service.encode_text(query.strip())
            else:
                query_vec = emb_service._model.encode(query.strip(), show_progress_bar=False)

            if query_vec is None:
                fallback = self.search_memory(query, limit=limit)
                for r in fallback:
                    r["retrieval_mode"] = "keyword_fallback"
                return fallback

            norm_q = np.linalg.norm(query_vec)
            if norm_q == 0:
                fallback = self.search_memory(query, limit=limit)
                for r in fallback:
                    r["retrieval_mode"] = "keyword_fallback"
                return fallback

            rows = self.db.get_memory_embeddings()
            if not rows:
                # No embeddings stored yet; fallback to keyword search
                fallback = self.search_memory(query, limit=limit)
                for r in fallback:
                    r["retrieval_mode"] = "keyword_fallback"
                return fallback

            scored_candidates = []
            for r in rows:
                emb = np.array(r["embedding"], dtype=float)
                norm_e = np.linalg.norm(emb)
                if norm_e == 0:
                    continue

                cosine_sim = float(np.dot(query_vec, emb) / (norm_q * norm_e))
                if cosine_sim >= min_similarity:
                    scored_candidates.append({
                        "id": r["memory_id"],
                        "memory_key": r["memory_key"],
                        "category": r.get("category"),
                        "content": r.get("content"),
                        "metadata": r.get("metadata", {}),
                        "similarity_score": round(cosine_sim, 4),
                        "retrieval_mode": "semantic",
                    })

            scored_candidates.sort(key=lambda x: x["similarity_score"], reverse=True)
            return scored_candidates[:limit]

        except Exception as e:
            logger.warning(f"[MemoryService] Semantic search exception: {e}. Executing keyword fallback.")
            fallback = self.search_memory(query, limit=limit)
            for r in fallback:
                r["retrieval_mode"] = "keyword_fallback"
            return fallback

    # ------------------------------------------------------------------
    # Keyword Search (Fallback & Multi-Strategy)
    # ------------------------------------------------------------------

    def search_memory(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Search memories across all categories using keyword and token-overlap ranking.
        """
        if not query:
            return []

        query_tokens = set(re.findall(r"\w+", query.lower()))
        all_memories = []

        try:
            with self.db._get_conn() as conn:
                rows = conn.execute("SELECT * FROM memory_store").fetchall()
                for r in rows:
                    item = dict(r)
                    item["metadata"] = json.loads(item.get("metadata") or "{}")
                    all_memories.append(item)
        except Exception as e:
            logger.error(f"[MemoryService] Search error: {e}")
            return []

        scored_results = []
        for mem in all_memories:
            text = f"{mem.get('memory_key', '')} {mem.get('content', '')} {mem.get('category', '')}".lower()
            mem_tokens = set(re.findall(r"\w+", text))
            overlap = len(query_tokens.intersection(mem_tokens))

            # Exact phrase match boost
            boost = 3.0 if query.lower() in text else 0.0
            relevance = overlap + boost

            if relevance > 0:
                mem_copy = dict(mem)
                mem_copy["relevance_score"] = relevance
                mem_copy["retrieval_mode"] = "keyword"
                scored_results.append((relevance, mem_copy))

        scored_results.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored_results[:limit]]

    # ------------------------------------------------------------------
    # PHASE 5: Bonus — Memory Pruning & Safe Retention Strategy
    # ------------------------------------------------------------------

    def prune_memory(
        self,
        retention_days: int = 30,
        create_backup: bool = True,
        preserve_minimum_summaries: int = 5,
    ) -> Dict[str, Any]:
        """
        Safely prune eligible historical daily summaries and expired cache entries.
        Guarantees:
          - Never deletes applications, email_history, approval_queue, or execution claims.
          - Never deletes candidate_insights or company interaction records.
          - Preserves minimum recent daily summaries regardless of age.
          - Creates reversible JSON backup file prior to deletion.
        """
        backup_file_path = None
        cutoff_date = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")

        # 1. Reversible Backup Creation
        if create_backup:
            try:
                backup_dir = Path(cfg.DATABASE_DIR) / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_file = backup_dir / f"memory_prune_backup_{timestamp_str}.json"

                # Capture candidate records to be pruned
                with self.db._get_conn() as conn:
                    summaries_to_prune = conn.execute("""
                        SELECT * FROM daily_memory_summaries
                        WHERE date < ?
                    """, (cutoff_date,)).fetchall()

                    expired_cache = conn.execute("""
                        SELECT * FROM api_cache
                        WHERE is_valid = 0 OR (expires_at IS NOT NULL AND expires_at <= datetime('now'))
                    """).fetchall()

                backup_payload = {
                    "backup_created_at": datetime.now().isoformat(),
                    "retention_days": retention_days,
                    "cutoff_date": cutoff_date,
                    "pruned_summaries": [dict(r) for r in summaries_to_prune],
                    "pruned_cache_entries": [dict(r) for r in expired_cache],
                }

                with open(backup_file, "w", encoding="utf-8") as f:
                    json.dump(backup_payload, f, indent=2)

                backup_file_path = str(backup_file)
                logger.info(f"[MemoryService] Prune backup created at: {backup_file_path}")
            except Exception as e:
                logger.warning(f"[MemoryService] Warning creating prune backup: {e}")

        # 2. Execute safe pruning in SQLite
        prune_result = self.db.prune_historical_memory(
            retention_days=retention_days,
            preserve_minimum_summaries=preserve_minimum_summaries,
        )

        prune_result["backup_file"] = backup_file_path

        # 3. Log audit entry into memory_store
        audit_content = (
            f"Memory Pruning on {datetime.now().isoformat()}: Pruned {prune_result.get('summaries_pruned', 0)} "
            f"summaries older than {cutoff_date} ({retention_days} days) and {prune_result.get('cache_pruned', 0)} "
            f"expired cache entries. Backup: {backup_file_path or 'none'}."
        )
        self.remember(
            key=f"prune_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            content=audit_content,
            category="memory_maintenance",
            metadata=prune_result,
        )

        logger.info(f"[MemoryService] Memory pruning complete: {prune_result}")
        return prune_result

    # ------------------------------------------------------------------
    # Company Interaction History Tracking
    # ------------------------------------------------------------------

    def record_company_interaction(
        self,
        company: str,
        action: str,
        outcome: str = "success",
        notes: str = ""
    ) -> bool:
        """
        Record a company-specific interaction (application, email, interview, rejection).
        """
        clean_co = re.sub(r"[^\w\s]", "", company).strip().lower()
        key = f"company_interaction_{clean_co}"

        existing = self.recall(key)
        history = existing.get("metadata", {}).get("history", []) if existing else []

        record = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "outcome": outcome,
            "notes": notes
        }
        history.append(record)

        content = (
            f"Company {company}: Last action was '{action}' with outcome '{outcome}'. "
            f"Total interactions: {len(history)}."
        )

        return self.remember(
            key=key,
            content=content,
            category="company_interactions",
            metadata={
                "company": company,
                "clean_company": clean_co,
                "last_outcome": outcome,
                "history": history
            }
        )

    def get_company_interaction(self, company: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve past interactions for a target company.
        """
        clean_co = re.sub(r"[^\w\s]", "", company).strip().lower()
        return self.recall(f"company_interaction_{clean_co}")

    # ------------------------------------------------------------------
    # System Context Summary for Agent Invocations
    # ------------------------------------------------------------------

    def get_system_context(self) -> Dict[str, Any]:
        """
        Synthesize cross-session metrics and memories for agent initialization.
        """
        all_time = self.db.get_all_time_stats()
        today_applied = self.db.get_today_applied_count()
        recent_insights = self.recall_by_category("candidate_insights")

        return {
            "all_time_stats": all_time,
            "today_applied": today_applied,
            "daily_quota_remaining": max(0, cfg.MAX_APPLY_PER_DAY - today_applied),
            "insights_count": len(recent_insights),
            "cached_memory_keys": list(self._get_all_cache_keys()),
            "last_run_summary": self.recall("last_run_summary"),
            "latest_daily_summary": self.db.get_latest_daily_memory_summary(),
        }

    # ------------------------------------------------------------------
    # Local Cache & Mem0 Cloud Helpers
    # ------------------------------------------------------------------

    def _update_local_cache(self, key: str, content: str, category: str, meta: Dict[str, Any]):
        """Update the JSON cache on disk."""
        try:
            cache_data = {"entries": {}}
            if self.cache_path.exists():
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)

            if "entries" not in cache_data:
                cache_data["entries"] = {}

            cache_data["entries"][key] = {
                "content": content,
                "category": category,
                "metadata": meta,
                "updated_at": datetime.now().isoformat()
            }
            cache_data["last_modified"] = datetime.now().isoformat()

            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2)
        except Exception as e:
            logger.debug(f"[MemoryService] Cache write exception: {e}")

    def _get_from_local_cache(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve entry from the JSON cache."""
        try:
            if self.cache_path.exists():
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                entry = cache_data.get("entries", {}).get(key)
                if entry:
                    return {
                        "memory_key": key,
                        "category": entry.get("category", "general"),
                        "content": entry.get("content", ""),
                        "metadata": entry.get("metadata", {}),
                        "updated_at": entry.get("updated_at")
                    }
        except Exception:
            pass
        return None

    def _get_all_cache_keys(self) -> List[str]:
        """List all keys stored in the local cache."""
        try:
            if self.cache_path.exists():
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return list(data.get("entries", {}).keys())
        except Exception:
            pass
        return []

    def _sync_to_mem0_cloud(self, key: str, content: str, category: str, meta: Dict[str, Any]):
        """Optional Mem0 cloud synchronization."""
        try:
            from mem0 import MemoryClient
            client = MemoryClient(api_key=cfg.MEM0_API_KEY)
            client.add(
                content,
                user_id=cfg.MEM0_USER_ID,
                metadata={"key": key, "category": category, **meta}
            )
            logger.info(f"[MemoryService] Synced memory '{key}' to Mem0 cloud.")
        except Exception as e:
            logger.debug(f"[MemoryService] Mem0 cloud sync skipped/failed: {e}")
