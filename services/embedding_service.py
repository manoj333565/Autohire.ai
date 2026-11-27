"""
services/embedding_service.py — Embedding-Based Semantic Similarity Scoring (F3 Bonus)
========================================================================================
Implements:
  - Dense semantic vector embedding of candidate profile & job descriptions
  - Cosine similarity matching normalized to [0.0, 10.0] scale
  - SHA-256 profile text hashing and embedding caching to avoid redundant computation
  - Weighted combination with LLM relevance score
  - Resilient offline loading with local file prioritization
  - Graceful degradation: preserves 100% LLM scoring if embedding model is unavailable
"""

import os
import logging
import hashlib
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import config as cfg

logger = logging.getLogger("CareerPilot.EmbeddingService")


class EmbeddingService:
    """
    Modular Semantic Similarity Scoring Engine using Sentence-Transformers.
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        enabled: Optional[bool] = None,
        embedding_weight: Optional[float] = None,
        llm_weight: Optional[float] = None
    ):
        self.model_name = (model_name or getattr(cfg, "EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")).strip()
        self.enabled = getattr(cfg, "EMBEDDING_SCORING_ENABLED", True) if enabled is None else bool(enabled)
        self.embedding_weight = getattr(cfg, "EMBEDDING_WEIGHT", 0.3) if embedding_weight is None else float(embedding_weight)
        self.llm_weight = getattr(cfg, "LLM_WEIGHT", 0.7) if llm_weight is None else float(llm_weight)

        # Caching structures for candidate profile embedding
        self._cached_profile_hash: Optional[str] = None
        self._cached_profile_embedding: Optional[np.ndarray] = None

        # Model instance (lazy loaded)
        self._model = None
        self._model_initialized = False

    def _ensure_model(self) -> bool:
        """
        Lazy load SentenceTransformer model with offline fallbacks.
        Returns True if model is loaded and ready, False otherwise.
        """
        if not self.enabled:
            return False

        if self._model_initialized:
            return self._model is not None

        self._model_initialized = True

        # Enforce offline mode in sandboxed/restricted environments
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        try:
            from sentence_transformers import SentenceTransformer
            try:
                # Prefer local cached weights first to avoid network checks
                self._model = SentenceTransformer(
                    self.model_name,
                    model_kwargs={"local_files_only": True}
                )
            except Exception:
                # Fallback to standard initialization if local_files_only kwarg is unsupported
                self._model = SentenceTransformer(self.model_name)

            logger.info(
                f"[EmbeddingService] Successfully loaded SentenceTransformer model '{self.model_name}' "
                f"(Weights: LLM={self.llm_weight}, Emb={self.embedding_weight})"
            )
            return True
        except Exception as e:
            logger.warning(
                f"[EmbeddingService] Could not load SentenceTransformer model '{self.model_name}': {e}. "
                f"Gracefully disabling embedding scoring (LLM score will be used 100%)."
            )
            self._model = None
            return False

    def encode_text(self, text: str) -> Optional[np.ndarray]:
        """
        Encode arbitrary text into a normalized dense embedding vector.
        Ensures the model is loaded before encoding.
        """
        if not self._ensure_model():
            return None
        try:
            return self._model.encode(
                text.strip(),
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False
            )
        except Exception as e:
            logger.warning(f"[EmbeddingService] Failed to encode text: {e}")
            return None

    # -------------------------------------------------------------------------
    # Text Serialization Helpers
    # -------------------------------------------------------------------------

    def build_candidate_profile_text(self, candidate_context: Dict[str, Any]) -> str:
        """
        Construct a concise, semantically dense representation of the candidate profile.
        Includes verified skills, education, experience level, target roles, and key projects.
        """
        identity = candidate_context.get("candidate_identity", {})
        verified = candidate_context.get("verified_skills_by_proficiency", {})
        proficient = verified.get("Proficient", [])
        intermediate = verified.get("Intermediate", [])
        learning = verified.get("Learning", [])

        target_prefs = candidate_context.get("target_preferences", {})
        target_roles = target_prefs.get("target_roles", [])
        locations = target_prefs.get("preferred_locations", [])

        projects = candidate_context.get("projects", [])
        proj_texts = []
        for p in projects:
            if isinstance(p, dict):
                proj_texts.append(f"{p.get('name')}: {p.get('tech')}")

        parts = [
            f"Candidate: {identity.get('name', 'Manoj Kumar Mandal')}",
            f"Degree: {identity.get('degree') or identity.get('education', 'B.Tech Computer Science and Engineering')}",
            f"Experience: Entry-Level / Fresher (0-2 years hands-on production project experience)",
            f"Target Roles: {', '.join(target_roles)}",
            f"Proficient Skills: {', '.join(proficient)}",
            f"Intermediate Skills: {', '.join(intermediate)}",
            f"Learning Skills: {', '.join(learning)}",
            f"Preferred Locations: {', '.join(locations)}",
        ]
        if proj_texts:
            parts.append(f"Key Projects: {'; '.join(proj_texts)}")

        return "\n".join(parts)

    def build_job_text(self, job: Dict[str, Any]) -> str:
        """
        Construct normalized job text representation for embedding generation.
        """
        title = (job.get("title") or job.get("job_title") or "").strip()
        company = (job.get("company") or "").strip()
        location = (job.get("location") or "").strip()
        experience = (job.get("experience") or "").strip()
        desc = (job.get("description") or job.get("job_description") or "")[:1500].strip()

        return (
            f"Job Title: {title}\n"
            f"Company: {company}\n"
            f"Experience Required: {experience}\n"
            f"Location: {location}\n"
            f"Description: {desc}"
        )

    # -------------------------------------------------------------------------
    # Candidate Embedding with SHA-256 In-Memory Caching
    # -------------------------------------------------------------------------

    def get_candidate_embedding(self, candidate_context: Dict[str, Any]) -> Optional[np.ndarray]:
        """
        Retrieve or compute candidate profile embedding vector with SHA-256 caching.
        Returns None if embedding model is unavailable.
        """
        if not self._ensure_model():
            return None

        profile_text = self.build_candidate_profile_text(candidate_context)
        profile_hash = hashlib.sha256(profile_text.encode("utf-8")).hexdigest()

        # Cache Hit
        if self._cached_profile_hash == profile_hash and self._cached_profile_embedding is not None:
            logger.debug("[EmbeddingService] Profile cache hit. Reusing cached candidate embedding.")
            return self._cached_profile_embedding

        # Cache Miss: Generate and cache
        try:
            embedding = self._model.encode(
                profile_text,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False
            )
            self._cached_profile_hash = profile_hash
            self._cached_profile_embedding = embedding
            logger.info(f"[EmbeddingService] Generated and cached new candidate profile embedding (Hash: {profile_hash[:8]}).")
            return embedding
        except Exception as e:
            logger.warning(f"[EmbeddingService] Failed to encode candidate profile: {e}")
            return None

    # -------------------------------------------------------------------------
    # Batch Semantic Similarity Scoring
    # -------------------------------------------------------------------------

    def compute_similarity_scores(
        self,
        jobs: List[Dict[str, Any]],
        candidate_context: Dict[str, Any]
    ) -> List[Optional[float]]:
        """
        Compute normalized cosine similarity scores [0.0, 10.0] for a batch of jobs.
        Returns a list of float scores (or None for failures/disabled state) matching input order.
        """
        if not jobs:
            return []

        if not self.enabled:
            return [None] * len(jobs)

        cand_emb = self.get_candidate_embedding(candidate_context)
        if cand_emb is None:
            return [None] * len(jobs)

        try:
            job_texts = [self.build_job_text(j) for j in jobs]
            job_embeddings = self._model.encode(
                job_texts,
                batch_size=32,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False
            )

            # Cosine similarity between normalized vectors is dot product
            # cand_emb shape: (D,), job_embeddings shape: (N, D)
            sims = np.dot(job_embeddings, cand_emb)

            scores = []
            for sim in sims:
                # Raw cosine similarity typically in [-1.0, 1.0], mostly [0.0, 0.9] for text
                raw_sim = float(sim)
                # Clamp to [0.0, 1.0] and scale to 10.0
                clamped_sim = max(0.0, min(1.0, raw_sim))
                norm_score = round(clamped_sim * 10.0, 1)
                scores.append(norm_score)

            return scores
        except Exception as e:
            logger.warning(f"[EmbeddingService] Batch job embedding failed: {e}. Returning None scores.")
            return [None] * len(jobs)

    def compute_single_similarity(
        self,
        job: Dict[str, Any],
        candidate_context: Dict[str, Any]
    ) -> Optional[float]:
        """Compute normalized semantic score for a single job."""
        res = self.compute_similarity_scores([job], candidate_context)
        return res[0] if res else None

    # -------------------------------------------------------------------------
    # Score Combination
    # -------------------------------------------------------------------------

    def combine_scores(
        self,
        llm_score: float,
        embedding_score: Optional[float],
        llm_weight: Optional[float] = None,
        embedding_weight: Optional[float] = None
    ) -> float:
        """
        Combine LLM relevance score and embedding similarity score using configurable weights.
        Returns combined score clamped to [0.0, 10.0].
        """
        llm_sc = float(llm_score)

        if embedding_score is None:
            return max(0.0, min(10.0, round(llm_sc, 1)))

        emb_sc = float(embedding_score)

        w_llm = self.llm_weight if llm_weight is None else float(llm_weight)
        w_emb = self.embedding_weight if embedding_weight is None else float(embedding_weight)

        total_weight = w_llm + w_emb
        if total_weight <= 0.0:
            total_weight = 1.0
            w_llm = 1.0
            w_emb = 0.0

        combined = (w_llm * llm_sc + w_emb * emb_sc) / total_weight
        return max(0.0, min(10.0, round(combined, 1)))
