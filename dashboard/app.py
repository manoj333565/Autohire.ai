"""
dashboard/app.py — Autonomous Job Agent Monitoring SaaS Dashboard Backend
========================================================================
Flask REST API and real-time Server-Sent Events (SSE) server for CareerPilot AI / Autohire.ai.
Features:
- Session-based password authentication with constant-time validation
- Real-time application metrics, 14-day activity trends, and portal breakdown
- Interactive Human Approval Queue (one-click Approve / Reject, Bulk Decisions)
- Job Feed matching reference SaaS layout with LLM Match Scores (98%, 94%, etc.)
- 1,831 PDF HR Contacts Outreach & Drip tracker
- Dynamic Candidate Profile & 3-Tier Verified Skills Management for Manoj Kumar Mandal
- Background Agent Execution trigger with live Server-Sent Events (SSE) log streaming
- Configurable settings and API connectivity health inspection
"""

import os
from statistics import mode
import sys
import json
import time
import hmac
import hashlib
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps
from typing import Dict, Any, List, Optional

from flask import (
    Flask,
    render_template,
    jsonify,
    request,
    Response,
    session,
    redirect,
    url_for
)
from flask_cors import CORS
from flask_wtf.csrf import CSRFProtect

from tests.test_database import db

# Add workspace root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from services.profile_service import ProfileService

logger = logging.getLogger("CareerPilot.Dashboard")

# ── Flask Application Setup ──────────────────────────────────────────────────
app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static")
)
# Local dashboard: restrict cross-origin access
CORS(app, resources={
    r"/api/*": {
        "origins": ["http://127.0.0.1:5000", "http://localhost:5000"]
    }
})

app.config.update(
    SECRET_KEY=cfg.DASHBOARD_SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_SAMESITE="Lax",
)
# Enable CSRF protection for all state-changing requests
csrf = CSRFProtect(app)

# ── Security & Rate Limiting Hardening (Milestone 14) ─────────────────────────
class InMemoryRateLimiter:
    """Thread-safe sliding-window rate limiter for sensitive endpoints."""
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.history: Dict[str, List[float]] = {}
        self.lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            records = self.history.get(key, [])
            records = [t for t in records if now - t < self.window_seconds]
            if len(records) >= self.max_requests:
                self.history[key] = records
                return False
            records.append(now)
            self.history[key] = records
            return True

    def reset(self, key: Optional[str] = None):
        with self.lock:
            if key:
                self.history.pop(key, None)
            else:
                self.history.clear()


login_limiter = InMemoryRateLimiter(max_requests=10, window_seconds=60)
agent_run_limiter = InMemoryRateLimiter(max_requests=5, window_seconds=60)


@app.after_request
def add_security_headers(response):
    """Inject hardened HTTP security headers to protect against clickjacking, sniffing, and XSS."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Server"] = "CareerPilot-Secure-Engine"
    return response


# ── Agent Execution & Log State ──────────────────────────────────────────────
agent_running = False
agent_lock = threading.Lock()
agent_log_buffer: List[str] = []
MAX_BUFFER_LINES = 300



_DB_INSTANCE: Optional[DatabaseManager] = None
_DB_INSTANCE_PATH: Optional[str] = None


def get_db() -> DatabaseManager:
    """Acquire thread-safe database manager singleton."""
    global _DB_INSTANCE, _DB_INSTANCE_PATH
    if _DB_INSTANCE is None or _DB_INSTANCE_PATH != cfg.DB_PATH:
        _DB_INSTANCE = DatabaseManager(cfg.DB_PATH)
        _DB_INSTANCE_PATH = cfg.DB_PATH
    return _DB_INSTANCE


def get_profile_service() -> ProfileService:
    """Acquire candidate profile service."""
    return ProfileService(get_db())


# ── Authentication & Security ────────────────────────────────────────────────
def verify_password(provided: str) -> bool:
    """Constant-time password verification against DASHBOARD_PASSWORD."""
    if not provided or not cfg.DASHBOARD_PASSWORD:
        return False
    provided_hash = hashlib.sha256(provided.strip().encode("utf-8")).hexdigest()
    expected_hash = hashlib.sha256(cfg.DASHBOARD_PASSWORD.strip().encode("utf-8")).hexdigest()
    return hmac.compare_digest(provided_hash, expected_hash)



def login_required(f):
    """Protect dashboard views and APIs using session authentication only."""
    @wraps(f)
    def decorated_function(*args, **kwargs):

        if session.get("authenticated") is not True:
            if request.path.startswith("/api/"):
                return jsonify({
                    "status": "error",
                    "error": "Unauthorized",
                    "message": "Authentication required. Please log in."
                }), 401

            return redirect(url_for("login_page"))

        return f(*args, **kwargs)

    return decorated_function



# ── Frontend Page Views ──────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    """Render the primary CareerPilot SaaS Dashboard interface."""
    return render_template(
        "index.html",
        candidate_name=cfg.CANDIDATE_NAME,
        candidate_email=cfg.PERSONAL_EMAIL,
        dry_run=cfg.DRY_RUN,
        require_human_approval=cfg.REQUIRE_HUMAN_APPROVAL,
        timezone=cfg.TIMEZONE
    )


@app.route("/login", methods=["GET", "POST"])
def login_page():
    """Render and process the secure SaaS authentication page with brute-force rate limiting."""
    if request.method == "GET":
        if session.get("authenticated") is True:
            return redirect(url_for("index"))
        return render_template("login.html", candidate_name=cfg.CANDIDATE_NAME)

    # Brute-force rate limiting check
    client_ip = request.remote_addr or "127.0.0.1"
    if not login_limiter.is_allowed(client_ip):
        logger.warning(f"Rate limit exceeded on /login for IP {client_ip}")
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({
                "status": "error",
                "error": "Too Many Requests",
                "message": "Too many failed login attempts. Please wait 60 seconds before retrying."
            }), 429
        return render_template(
            "login.html",
            error="Too many login attempts. Please wait 60 seconds before trying again.",
            candidate_name=cfg.CANDIDATE_NAME
        ), 429

    # POST handling: accepts form data or JSON
    data = request.get_json(silent=True) or request.form
    password = data.get("password", "")

    if verify_password(password):
        login_limiter.reset(client_ip)
        session["authenticated"] = True
        session["user"] = cfg.CANDIDATE_NAME
        session["login_time"] = datetime.now().isoformat()
        logger.info(f"Dashboard login successful for {cfg.CANDIDATE_NAME}")

        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({
                "status": "success",
                "message": f"Welcome back, {cfg.CANDIDATE_NAME}! Authenticated successfully.",
                "redirect": "/"
            })
        return redirect(url_for("index"))
    else:
        logger.warning("Failed dashboard authentication attempt.")
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({
                "status": "error",
                "message": "Invalid password. Access restricted to Manoj Kumar Mandal's administration."
            }), 401
        return render_template(
            "login.html",
            error="Invalid password. Please check your admin credentials.",
            candidate_name=cfg.CANDIDATE_NAME
        ), 401




@app.route("/logout", methods=["POST"])
@login_required
def logout():
    """Clear active session and redirect to login."""
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/health")
def health():
    """Public health check with minimal information disclosure."""
    return jsonify({
        "status": "ok",
        "service": "Autohire.ai",
        "timestamp": datetime.now().isoformat()
    }), 200


# ── Dashboard Analytics & Metrics APIs ───────────────────────────────────────

@app.route("/api/stats")
@login_required
def api_stats():
    """Fetch high-level aggregate and daily statistics."""
    try:
        db = get_db()
        all_time = db.get_all_time_stats()
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_stats = db.get_daily_stats(today_str)
        drip_stats = db.get_pdf_hr_stats()
        queue_counts = db.get_approval_queue_counts()

        # Calculate time until next scheduled run (09:00 IST)
        now_dt = datetime.now()
        next_run_dt = now_dt.replace(
            hour=cfg.SCHEDULE_HOUR,
            minute=cfg.SCHEDULE_MINUTE,
            second=0,
            microsecond=0
        )
        if now_dt >= next_run_dt:
            next_run_dt += timedelta(days=1)
        time_until_str = str(next_run_dt - now_dt).split(".")[0]

        return jsonify({
            "status": "success",
            "candidate": {
                "name": cfg.CANDIDATE_NAME,
                "email": cfg.PERSONAL_EMAIL,
                "role": "AI/ML & Agentic AI Engineer"
            },
            "all_time": {
                "total_jobs_seen": all_time.get("total_jobs_seen", 0),
                "total_applied": all_time.get("total_applied", 0),
                "total_emailed": all_time.get("total_emailed", 0),
                "total_hr_outreach_sent": all_time.get("total_hr_outreach_sent", 0),
                "total_failed": all_time.get("total_failed", 0),
                "total_failed_emails": all_time.get("total_failed_emails", 0),
                "unique_companies": all_time.get("unique_companies", 0),
                "avg_match_score": all_time.get("avg_match_score", 0.0),
            },
            "today": {
                "date": today_str,
                "scraped": today_stats.get("total_scraped", 0),
                "new": today_stats.get("total_new", 0),
                "applied": today_stats.get("total_applied", 0),
                "emailed": today_stats.get("total_emailed", 0),
            },
            "drip": {
                "total_contacts": drip_stats.get("total", 0),
                "sent": drip_stats.get("sent", 0),
                "sent_today": drip_stats.get("sent_today", 0),
                "daily_limit": cfg.DAILY_DRIP_LIMIT,
            },
            "approval_queue": queue_counts,
            "system": {
                "agent_running": agent_running,
                "dry_run": cfg.DRY_RUN,
                "require_human_approval": cfg.REQUIRE_HUMAN_APPROVAL,
                "current_time_ist": now_dt.strftime("%Y-%m-%d %H:%M:%S IST"),
                "next_run": next_run_dt.strftime(f"%Y-%m-%d {cfg.SCHEDULE_HOUR:02d}:{cfg.SCHEDULE_MINUTE:02d} IST"),
                "time_until_next_run": time_until_str,
            }
        })
    except Exception as e:
        logger.error(f"Error fetching stats: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/chart-data")
@login_required
def api_chart_data():
    """Fetch 14-day trend stats for Chart.js interactive rendering."""
    try:
        db = get_db()
        days = int(request.args.get("days", 14))
        trends = db.get_chart_stats(days=days)
        return jsonify({
            "status": "success",
            "days": days,
            "trends": trends
        })
    except Exception as e:
        logger.error(f"Error fetching chart data: {e}")
        return jsonify({"status": "error", "trends": []}), 500


@app.route("/api/portals")
@login_required
def api_portals():
    """Fetch job portal breakdown metrics."""
    try:
        db = get_db()
        portal_stats = db.get_portal_stats()
        return jsonify({
            "status": "success",
            "portals": portal_stats
        })
    except Exception as e:
        logger.error(f"Error fetching portal stats: {e}")
        return jsonify({"status": "error", "portals": []}), 500


# ── Job Feed & Applications APIs ─────────────────────────────────────────────

@app.route("/api/jobs")
@login_required
def api_jobs():
    """Fetch paginated job feed with LLM match scores and skill highlights (with input bounds validation)."""
    try:
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (ValueError, TypeError):
            page = 1

        try:
            per_page = min(100, max(1, int(request.args.get("per_page", 10))))
        except (ValueError, TypeError):
            per_page = 10

        portal = request.args.get("portal", "all").strip().lower()
        valid_portals = {"all", "remoteok", "weworkremotely", "internshala", "wellfound", "linkedin", "naukri", "ats", "direct"}
        if portal not in valid_portals:
            portal = "all"

        sort_by = request.args.get("sort_by", "score").strip().lower()
        if sort_by not in ("score", "recent", "company"):
            sort_by = "score"

        search = request.args.get("search", "").strip()[:100]

        db = get_db()
        feed = db.get_jobs_feed(
            page=page,
            per_page=per_page,
            portal=portal,
            sort_by=sort_by,
            search=search
        )
        return jsonify({
            "status": "success",
            **feed
        })
    except Exception as e:
        logger.error(f"Error loading jobs feed: {e}")
        return jsonify({"status": "error", "jobs": [], "total": 0}), 500


@app.route("/api/applications")
@login_required
def api_applications():
    """Fetch paginated historical applications with filters and bounded search."""
    try:
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (ValueError, TypeError):
            page = 1

        try:
            per_page = min(100, max(1, int(request.args.get("per_page", 20))))
        except (ValueError, TypeError):
            per_page = 20

        status_filter = request.args.get("status", "all").strip().lower()
        valid_statuses = {"all", "new", "applied", "manual_required", "interview", "rejected", "failed", "email_sent"}
        if status_filter not in valid_statuses:
            status_filter = "all"

        portal_filter = request.args.get("portal", "all").strip().lower()
        valid_portals = {"all", "remoteok", "weworkremotely", "internshala", "wellfound", "linkedin", "naukri", "ats", "direct"}
        if portal_filter not in valid_portals:
            portal_filter = "all"

        search = request.args.get("search", "").strip()[:100]

        db = get_db()
        data = db.get_applications(
            page=page,
            per_page=per_page,
            status=status_filter,
            portal=portal_filter,
            search=search
        )
        return jsonify({
            "status": "success",
            **data
        })
    except Exception as e:
        logger.error(f"Error fetching applications: {e}")
        return jsonify({"status": "error", "applications": [], "total": 0}), 500


@app.route("/api/applications/<job_hash>/status", methods=["POST"])
@login_required
def api_update_application_status(job_hash: str):
    """Manually update an application status with whitelist validation."""
    try:
        data = request.get_json() or {}
        new_status = data.get("status", "").strip().lower()
        notes = data.get("notes", "Updated via SaaS Dashboard").strip()

        valid_app_statuses = {"new", "applied", "manual_required", "interview", "rejected", "failed", "email_sent"}
        if new_status not in valid_app_statuses:
            return jsonify({
                "status": "error",
                "message": f"Invalid status '{new_status}'. Allowed values: {sorted(list(valid_app_statuses))}"
            }), 400

        
            db = get_db()

            updated = db.update_application_status(
                job_hash=job_hash,
                status=new_status,
                notes=notes
            )

            if not updated:
                return jsonify({
                "status": "error",
                "message": "Application not found."
            }), 404

        # Increment daily stats only after a confirmed database update
        if new_status == "applied":
            today_str = datetime.now().strftime("%Y-%m-%d")
            db.update_daily_stats(today_str, total_applied=1)


        return jsonify({
            "status": "success",
            "message": f"Application status updated to '{new_status}' successfully.",
            "job_hash": job_hash,
            "new_status": new_status
        })
    except Exception as e:

        logger.error(f"Error updating application status for {job_hash}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Human Approval Queue APIs (Safety Gate) ──────────────────────────────────

@app.route("/api/approval-queue")
@login_required
def api_approval_queue():
    """Retrieve pending actions awaiting human review."""
    try:
        status = request.args.get("status", "pending").strip().lower()
        limit = int(request.args.get("limit", 50))
        db = get_db()
        items = db.get_approval_queue(status=status, limit=limit)
        counts = db.get_approval_queue_counts()

        return jsonify({
            "status": "success",
            "items": items,
            "counts": counts,
            "total_pending": len(items) if status == "pending" else counts.get("pending", 0)
        })
    except Exception as e:
        logger.error(f"Error loading approval queue: {e}")
        return jsonify({"status": "error", "items": [], "counts": {}}), 500


@app.route("/api/approval-queue/<int:item_id>/decide", methods=["POST"])
@login_required
def api_decide_approval_item(item_id: int):
    """Approve or reject a specific pending approval item."""

    try:
        # Validate queue ID
        if item_id <= 0:
            return jsonify({
                "status": "error",
                "message": "A valid positive approval item ID is required."
            }), 400

        # Validate JSON request
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "status": "error",
                "message": "A valid JSON request is required."
            }), 400

        # Validate decision
        decision = data.get("decision")

        if not isinstance(decision, str):
            return jsonify({
                "status": "error",
                "message": "Decision must be a string."
            }), 400

        decision = decision.strip().lower()

        if decision not in ("approved", "rejected", "cancelled"):
            return jsonify({
                "status": "error",
                "message": "Decision must be approved, rejected, or cancelled."
            }), 400

        # Validate reviewer notes
        notes = data.get("notes", "Decided via Autohire.ai Dashboard")

        if not isinstance(notes, str):
            return jsonify({
                "status": "error",
                "message": "Reviewer notes must be a string."
            }), 400

        notes = notes.strip()

        if len(notes) > 2000:
            return jsonify({
                "status": "error",
                "message": "Reviewer notes cannot exceed 2000 characters."
            }), 400

        # Database method only updates items that are still pending.
        db = get_db()

        success = db.decide_approval(
            queue_id=item_id,
            decision=decision,
            notes=notes
        )

        if not success:
            return jsonify({
                "status": "error",
                "message": (
                    "Approval item was not found or has already been decided."
                )
            }), 409

        return jsonify({
            "status": "success",
            "item_id": item_id,
            "decision": decision,
            "message": f"Queue item {item_id} marked as {decision}."
        }), 200

    except ValueError as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 400

    except Exception:
        logger.exception(
            "Error deciding approval item ID %s",
            item_id
        )

        return jsonify({
            "status": "error",
            "message": "Unable to process approval decision."
        }), 500


@app.route("/api/approval-queue/bulk-decide", methods=["POST"])
@login_required
def api_bulk_decide_approval():
    """Bulk-decide only explicitly selected pending job applications."""
    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "status": "error",
                "message": "A valid JSON request is required."
            }), 400

        decision = data.get("decision")
        notes = data.get("notes", "")
        item_ids = data.get("item_ids")

        if not isinstance(decision, str):
            return jsonify({
                "status": "error",
                "message": "Decision must be a string."
            }), 400

        decision = decision.strip().lower()

        if decision not in ("approved", "rejected"):
            return jsonify({
                "status": "error",
                "message": "Decision must be 'approved' or 'rejected'."
            }), 400

        if not isinstance(notes, str):
            return jsonify({
                "status": "error",
                "message": "Notes must be a string."
            }), 400

        if not isinstance(item_ids, list) or not item_ids:
            return jsonify({
                "status": "error",
                "message": "Select at least one job application."
            }), 400

        if len(item_ids) > 100:
            return jsonify({
                "status": "error",
                "message": "A maximum of 100 items can be selected."
            }), 400

        if any(
            not isinstance(item_id, int)
            or isinstance(item_id, bool)
            or item_id <= 0
            for item_id in item_ids
        ):
            return jsonify({
                "status": "error",
                "message": "All selected IDs must be positive integers."
            }), 400

        if len(item_ids) != len(set(item_ids)):
            return jsonify({
                "status": "error",
                "message": "Duplicate item IDs are not allowed."
            }), 400

        db = get_db()
        count = db.bulk_decide_approval(
            decision=decision,
            notes=notes,
            item_ids=item_ids
        )

        return jsonify({
            "status": "success",
            "decision": decision,
            "updated_count": count,
            "message": f"Updated {count} selected job application(s)."
        }), 200

    except ValueError as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 400

    except Exception:
        logger.exception("Error executing bulk approval decision")
        return jsonify({
            "status": "error",
            "message": "Unable to process bulk approval decision."
        }), 500


# ── PDF HR Outreach & Drip APIs ──────────────────────────────────────────────

@app.route("/api/drip-stats")
@login_required
def api_drip_stats():
    """Fetch status for the verified HR contacts drip outreach campaign."""
    try:
        db = get_db()
        stats = db.get_pdf_hr_stats()
        total = stats.get("total", 0)
        sent = stats.get("sent", 0)
        remaining = max(0, total - sent)
        percent = round((sent / total * 100), 1) if total > 0 else 0.0

        return jsonify({
            "status": "success",
            "total_contacts": total,
            "sent": sent,
            "sent_today": stats.get("sent_today", 0),
            "remaining": remaining,
            "percentage_complete": percent,
            "daily_quota": cfg.DAILY_DRIP_LIMIT,
        })
    except Exception as e:
        logger.error(f"Error fetching drip stats: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Candidate Profile & Verified Skills APIs ─────────────────────────────────

@app.route("/api/profile", methods=["GET", "POST"])
@login_required
def api_profile():
    """Retrieve or update candidate profile details and preferences."""
    profile_service = get_profile_service()
    if request.method == "GET":
        try:
            profile = profile_service.get_full_profile(profile_id=1)
            return jsonify({
                "status": "success",
                "profile": profile
            })
        except Exception as e:
            logger.error(f"Error loading profile: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    # POST: Update personal details
    try:
        data = request.get_json() or {}
        success = profile_service.update_personal_details(profile_id=1, updates=data)
        return jsonify({
            "status": "success" if success else "error",
            "message": "Candidate profile updated successfully." if success else "No updates applied."
        })
    except Exception as e:
        logger.error(f"Error updating profile: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/skills", methods=["POST"])
@login_required
def api_add_skill():
    """Add or modify a candidate skill declaration across the 3 tiers."""
    try:
        data = request.get_json() or {}
        skill_name = data.get("skill_name", "").strip()
        proficiency = data.get("proficiency", "Proficient").strip().capitalize()
        category = data.get("category", "Technical").strip()

        if not skill_name:
            return jsonify({"status": "error", "message": "Missing 'skill_name'."}), 400

        profile_service = get_profile_service()
        profile_service.add_skill(
            profile_id=1,
            skill_name=skill_name,
            proficiency=proficiency,
            category=category
        )
        return jsonify({
            "status": "success",
            "message": f"Skill '{skill_name}' declared as '{proficiency}'."
        })
    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        logger.error(f"Error adding skill: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/skills/<int:skill_id>", methods=["DELETE"])
@login_required
def api_delete_skill(skill_id: int):
    """Delete a skill declaration from the candidate profile."""
    try:
        profile_service = get_profile_service()
        success = profile_service.remove_skill(skill_id=skill_id)
        if not success:
            return jsonify({"status": "error", "message": f"Skill {skill_id} not found."}), 404
        return jsonify({
            "status": "success",
            "message": f"Skill {skill_id} removed successfully."
        })
    except Exception as e:
        logger.error(f"Error deleting skill {skill_id}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/profile/education", methods=["POST"])
@login_required
def api_update_education():
    """Update candidate degree, college, year, and CGPA with audit logging."""
    try:
        data = request.get_json() or {}
        degree = data.get("degree", "").strip()
        college = data.get("college", "").strip()
        year = str(data.get("year", "")).strip()
        cgpa = str(data.get("cgpa", "")).strip()

        if not degree or not college:
            return jsonify({"status": "error", "message": "Degree and college are required."}), 400

        profile_service = get_profile_service()
        profile_service.update_education(profile_id=1, degree=degree, college=college, year=year, cgpa=cgpa)
        return jsonify({
            "status": "success",
            "message": "Education credentials updated successfully."
        })
    except Exception as e:
        logger.error(f"Error updating education: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/profile/preferences", methods=["POST"])
@login_required
def api_update_preferences():
    """Update job search target preferences and match threshold."""
    try:
        data = request.get_json() or {}
        profile_service = get_profile_service()
        profile_service.update_preferences(profile_id=1, preferences_dict=data)
        return jsonify({
            "status": "success",
            "message": "Career preferences updated successfully."
        })
    except Exception as e:
        logger.error(f"Error updating preferences: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/profile/reprocess-resume", methods=["POST"])
@login_required
def api_reprocess_resume():
    """
    Reprocess resume PDF.
    When confirm=False: returns diff for user confirmation.
    When confirm=True: applies changes to database and logs audit history.
    """
    try:
        data = request.get_json() or {}
        confirm_overwrite = bool(data.get("confirm", False))
        profile_service = get_profile_service()
        result = profile_service.sync_profile_with_resume(
            profile_id=1,
            confirm_overwrite=confirm_overwrite
        )
        return jsonify({
            "status": "success",
            "result": result
        })
    except Exception as e:
        logger.error(f"Error reprocessing resume: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/skills/verify", methods=["POST"])
@login_required
def api_verify_skill():
    """Promote or categorize a pending review skill into a verified proficiency tier."""
    try:
        data = request.get_json() or {}
        skill_name = data.get("skill_name", "").strip()
        proficiency = data.get("proficiency", "Intermediate").strip().capitalize()
        category = data.get("category", "Technical").strip()

        if not skill_name:
            return jsonify({"status": "error", "message": "Missing 'skill_name'."}), 400

        profile_service = get_profile_service()
        profile_service.confirm_skill_proficiency(
            profile_id=1,
            skill_name=skill_name,
            proficiency=proficiency,
            category=category
        )
        return jsonify({
            "status": "success",
            "message": f"Skill '{skill_name}' verified into '{proficiency}' tier."
        })
    except Exception as e:
        logger.error(f"Error verifying skill: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ── System Settings & Connectivity APIs ──────────────────────────────────────

@app.route("/api/settings", methods=["GET", "POST"])
@login_required
def api_settings():
    """Inspect and modify runtime configuration settings."""
    if request.method == "GET":
        return jsonify({
            "status": "success",
            "settings": {
                "dry_run": cfg.DRY_RUN,
                "require_human_approval": cfg.REQUIRE_HUMAN_APPROVAL,
                "match_score_threshold": cfg.MATCH_SCORE_THRESHOLD,
                "max_apply_per_day": cfg.MAX_APPLY_PER_DAY,
                "max_email_per_day": cfg.MAX_EMAIL_PER_DAY,
                "daily_drip_limit": cfg.DAILY_DRIP_LIMIT,
                "timezone": cfg.TIMEZONE,
                "schedule_hour": cfg.SCHEDULE_HOUR,
                "schedule_minute": cfg.SCHEDULE_MINUTE,
                "embedding_scoring_enabled": getattr(cfg, "EMBEDDING_SCORING_ENABLED", True),
                "embedding_model_name": getattr(cfg, "EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2"),
                "embedding_weight": getattr(cfg, "EMBEDDING_WEIGHT", 0.3),
                "llm_weight": getattr(cfg, "LLM_WEIGHT", 0.7),
            },
            "connectivity": {
                "gemini_configured": bool(cfg.GEMINI_API_KEY),
                "sentence_transformers_configured": True,
                "gmail_configured": bool(cfg.GMAIL_ADDRESS and cfg.GMAIL_APP_PASSWORD),
                "tavily_configured": bool(cfg.TAVILY_API_KEY),
                "apollo_configured": bool(cfg.APOLLO_API_KEY),
                "snov_configured": bool(cfg.SNOV_CLIENT_ID and cfg.SNOV_CLIENT_SECRET),
                "mem0_configured": bool(cfg.MEM0_API_KEY),
                "sqlite_wal": True,
            }
        })

       # POST: Update runtime flags in memory
    try:
        data = request.get_json(silent=True) or {}

        # Enforce mandatory safety settings
        if "dry_run" in data and data["dry_run"] is not True:
            return jsonify({
                "status": "error",
                "message": "DRY_RUN cannot be disabled."
            }), 403

        if (
            "require_human_approval" in data
            and data["require_human_approval"] is not True
        ):
            return jsonify({
                "status": "error",
                "message": "Human approval cannot be disabled."
            }), 403

        # Always maintain safe execution mode
        cfg.DRY_RUN = True
        cfg.REQUIRE_HUMAN_APPROVAL = True

        # Update match score threshold if provided
        if "match_score_threshold" in data:
            try:
                val = float(data["match_score_threshold"])

                if not (0.0 <= val <= 10.0):
                    return jsonify({
                        "status": "error",
                        "message": "match_score_threshold must be between 0.0 and 10.0"
                    }), 400

                cfg.MATCH_SCORE_THRESHOLD = val

            except (ValueError, TypeError):
                return jsonify({
                    "status": "error",
                    "message": "Invalid numeric value for match_score_threshold"
                }), 400

        logger.info(
            f"Updated runtime settings: "
            f"DRY_RUN={cfg.DRY_RUN}, "
            f"REQUIRE_HUMAN_APPROVAL={cfg.REQUIRE_HUMAN_APPROVAL}"
        )

        return jsonify({
            "status": "success",
            "message": "Settings updated successfully.",
            "current": {
                "dry_run": cfg.DRY_RUN,
                "require_human_approval": cfg.REQUIRE_HUMAN_APPROVAL,
                "match_score_threshold": cfg.MATCH_SCORE_THRESHOLD
            }
        })

    except Exception as e:
        logger.error(f"Error saving settings: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ── Agent Orchestration & Real-time Live Log Streaming ───────────────────────

def _append_log(msg: str):
    """Add a message to the in-memory log buffer."""
    global agent_log_buffer
    ts = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{ts}] {msg}"
    agent_log_buffer.append(formatted)
    if len(agent_log_buffer) > MAX_BUFFER_LINES:
        agent_log_buffer.pop(0)


@app.route("/api/agent/run", methods=["POST"])
@login_required
def api_agent_run():
    """Trigger the LangGraph autonomous job agent in a background thread (rate limited)."""
    global agent_running

    client_ip = request.remote_addr or "127.0.0.1"
    if not agent_run_limiter.is_allowed(client_ip):
        return jsonify({
            "status": "error",
            "error": "Too Many Requests",
            "message": "Agent execution rate limit exceeded. Please wait 60 seconds."
        }), 429

    with agent_lock:
        if agent_running:
            return jsonify({
                "status": "error",
                "message": "Agent execution is already in progress. Please wait for current run to conclude."
            }), 400
        agent_running = True


    # Force safe execution; never accept a live-mode API override
    cfg.DRY_RUN = True
    cfg.REQUIRE_HUMAN_APPROVAL = True
    dry_run = True

    def _execute_agent_workflow():
        global agent_running
        _append_log(f"🚀 [AGENT TRIGGERED] Candidate: {cfg.CANDIDATE_NAME} | Mode: {'DRY-RUN SAFE' if dry_run else 'LIVE SUBMISSION'}")
        start_time = time.time()
        try:
            from agent.graph import build_job_agent_graph, get_initial_state

            _append_log("Initializing LangGraph state machine...")
            agent = build_job_agent_graph()
            initial_state = get_initial_state(dry_run=dry_run)

            _append_log("Executing agent pipeline across 6 portals & enrichment nodes...")
            final_state = agent.invoke(initial_state)

            duration = round(time.time() - start_time, 2)
            applied_cnt = final_state.get("total_applied", 0)
            emailed_cnt = final_state.get("total_emailed", 0)
            staged_cnt = len(final_state.get("approval_items", []))

            _append_log(f"✅ [RUN COMPLETE] Duration: {duration}s | Staged for Approval: {staged_cnt} | Applied: {applied_cnt} | Emailed: {emailed_cnt}")
        except Exception as e:
            logger.error(f"Error in background agent run: {e}", exc_info=True)
            _append_log(f"❌ [AGENT ERROR] Pipeline encountered exception: {str(e)}")
        finally:
            with agent_lock:
                agent_running = False

    thread = threading.Thread(target=_execute_agent_workflow, daemon=True)
    thread.start()

    return jsonify({
        "status": "started",
        "message": f"Autonomous Job Agent started in background thread (Dry-Run: {dry_run}).",
        "dry_run": dry_run
    })


@app.route("/api/agent/status")
@login_required
def api_agent_status():
    """Retrieve agent operational state and recent buffered logs."""
    return jsonify({
        "status": "success",
        "running": agent_running,
        "logs": agent_log_buffer[-60:],
        "buffer_size": len(agent_log_buffer),
        "timestamp": datetime.now().isoformat()
    })


@app.route("/api/agent/stream-logs")
@app.route("/api/logs/stream")
@login_required
def api_logs_stream():
    """Server-Sent Events (SSE) endpoint for real-time live log tailing."""
    def generate():
        last_index = 0
        yield f"data: {json.dumps({'line': '⚡ Connected to CareerPilot AI Real-Time Log Stream'})}\n\n"

        # Stream existing buffer lines first
        current_buffer = list(agent_log_buffer)
        for line in current_buffer[-50:]:
            yield f"data: {json.dumps({'line': line})}\n\n"
        last_index = len(agent_log_buffer)

        # Loop and yield new entries
        while True:
            current_len = len(agent_log_buffer)
            if current_len > last_index:
                for idx in range(last_index, current_len):
                    line = agent_log_buffer[idx]
                    yield f"data: {json.dumps({'line': line})}\n\n"
                last_index = current_len

            # Emit periodic heartbeat with status
            yield f"data: {json.dumps({'heartbeat': True, 'running': agent_running})}\n\n"
            time.sleep(1.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )


# ── System Hardening & Maintenance API (Milestone 14) ─────────────────────────

@app.route("/api/admin/maintenance", methods=["POST"])
@login_required
def api_admin_maintenance():
    """Trigger SQLite WAL checkpoint and query planner optimization."""
    try:
        data = request.get_json(silent=True) or {}
        mode = str(data.get("mode", "PASSIVE")).upper()
        allowed_modes = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}

        if mode not in allowed_modes:
             return jsonify({
                "status": "error",
                "message": "Invalid maintenance mode."
            }), 400

        db = get_db()
        wal_res = db.checkpoint_wal(mode=mode)
        opt_res = db.optimize_database()
        return jsonify({
            "status": "success",
            "wal_checkpoint": wal_res,
            "database_optimization": opt_res,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error executing maintenance: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Global Defensive Error Handlers ──────────────────────────────────────────

@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith("/api/"):
        return jsonify({
            "status": "error",
            "error": "Not Found",
            "message": "The requested API endpoint does not exist."
        }), 404
    return render_template("login.html", error="Page not found.", candidate_name=cfg.CANDIDATE_NAME), 404


@app.errorhandler(429)
def handle_429(e):
    return jsonify({
        "status": "error",
        "error": "Too Many Requests",
        "message": "Rate limit exceeded. Please wait before retrying."
    }), 429


@app.errorhandler(500)
def handle_500(e):
    logger.error(f"Unhandled 500 error on {request.path}: {e}")
    if request.path.startswith("/api/"):
        return jsonify({
            "status": "error",
            "error": "Internal Server Error",
            "message": "An internal error occurred."
        }), 500
    return render_template("login.html", error="An internal error occurred.", candidate_name=cfg.CANDIDATE_NAME), 500


# ── Standalone Server Execution ──────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", cfg.DASHBOARD_PORT))
    print("\n" + "=" * 65)
    print("  🚀 CAREERPILOT AI — AUTONOMOUS SAAS DASHBOARD")
    print(f"  Candidate:  {cfg.CANDIDATE_NAME} ({cfg.PERSONAL_EMAIL})")
    print(f"  Local URL:  http://localhost:{port}")
    print(f"  Safety:     DRY_RUN={cfg.DRY_RUN} | APPROVAL={cfg.REQUIRE_HUMAN_APPROVAL}")
    print("=" * 65 + "\n")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
