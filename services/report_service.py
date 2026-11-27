"""
services/report_service.py — Executive Daily HTML Report & Analytics Engine (F9)
================================================================================
Generates comprehensive daily intelligence briefings for Manoj Kumar Mandal:
- Executive KPI Metric Cards (Daily & All-Time Applications, Cold Outreach, PDF Drip)
- Multi-Stage Job Search Pipeline Funnel (Scraped -> Filtered -> Scored -> Staged -> Applied)
- 7-Day Performance & Velocity Comparison with Day-over-Day Trends
- 3-Layer Career Inbox Intelligence (Recruiter Interview Replies vs Bounce-backs vs Marketing)
- 1,831 Verified HR Contacts Drip Campaign Progress (50 emails/day cap)
- Pending Human Review Queue Alerts
- Mobile-responsive HTML email template with Royal Blue (#1A56DB) SaaS branding
- Persistent local artifact generation (`logs/reports/daily_report_YYYY-MM-DD.html`)
- Audit logging into `email_history` and optional Gmail SMTP dispatch
"""

import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

import config as cfg
from database.db_manager import DatabaseManager
from tools.inbox_monitor import InboxMonitor
from tools.email_sender import EmailSender

logger = logging.getLogger("CareerPilot.ReportService")


class ReportService:
    """Enterprise Daily Reporting and Executive Intelligence Service."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        dry_run: bool = True
    ):
        self.db = db_manager or DatabaseManager(cfg.DB_PATH)
        self.dry_run = dry_run
        self.reports_dir = Path(cfg.BASE_DIR) / "logs" / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # METRICS & PIPELINE FUNNEL COMPILATION
    # =========================================================================

    def compile_daily_metrics(
        self,
        state: Optional[Dict[str, Any]] = None,
        date_str: Optional[str] = None
    ) -> Dict[str, Any]:
        """Compile today's pipeline execution counts, all-time totals, and conversion rates."""
        date_key = date_str or datetime.now().strftime("%Y-%m-%d")
        db_daily = self.db.get_daily_stats(date_key)
        all_time = self.db.get_all_time_stats()
        drip_stats = self.db.get_pdf_hr_stats()
        approval_counts = self.db.get_approval_queue_counts()

        # Extract counts from LangGraph state or fallback to database
        if state:
            scraped = len(state.get("raw_jobs", [])) or db_daily.get("total_scraped", 0)
            filtered = len(state.get("filtered_jobs", [])) or db_daily.get("total_new", 0)
            scored = len(state.get("scored_jobs", []))
            staged = len(state.get("approval_items", [])) or len(state.get("staged_approvals", []))
            applied = state.get("total_applied", len(state.get("applied_jobs", []))) or db_daily.get("total_applied", 0)
            emailed = state.get("total_emailed", len(state.get("emailed_jobs", []))) or db_daily.get("total_emailed", 0)
            pdf_drip_sent = len(state.get("pdf_drip_sent", [])) or drip_stats.get("sent_today", 0)
            errors_cnt = len(state.get("errors", []))
            duration = state.get("duration_seconds", db_daily.get("run_time_seconds", 0.0))
        else:
            scraped = db_daily.get("total_scraped", 0)
            filtered = db_daily.get("total_new", 0)
            scored = filtered
            staged = approval_counts.get("pending", 0)
            applied = db_daily.get("total_applied", 0)
            emailed = db_daily.get("total_emailed", 0)
            pdf_drip_sent = drip_stats.get("sent_today", 0)
            errors_cnt = db_daily.get("total_failed", 0)
            duration = db_daily.get("run_time_seconds", 0.0)

        # Pipeline Funnel Conversion Rates
        filter_rate = round((filtered / scraped * 100), 1) if scraped > 0 else 0.0
        scoring_rate = round((scored / filtered * 100), 1) if filtered > 0 else 0.0
        stage_rate = round((staged / scored * 100), 1) if scored > 0 else 0.0
        apply_rate = round((applied / staged * 100), 1) if staged > 0 else (100.0 if applied > 0 else 0.0)

        # Drip progress
        total_drip = drip_stats.get("total", 1831)
        sent_drip = drip_stats.get("sent", 0)
        remaining_drip = max(0, total_drip - sent_drip)
        drip_percent = round((sent_drip / total_drip * 100), 1) if total_drip > 0 else 0.0

        return {
            "date": date_key,
            "duration_seconds": round(duration, 1),
            "today": {
                "scraped": scraped,
                "filtered": filtered,
                "scored": scored,
                "staged": staged,
                "applied": applied,
                "emailed": emailed,
                "pdf_drip_sent": pdf_drip_sent,
                "errors": errors_cnt
            },
            "conversion_rates": {
                "filter_pass_pct": filter_rate,
                "scoring_pass_pct": scoring_rate,
                "approval_stage_pct": stage_rate,
                "apply_conversion_pct": apply_rate
            },
            "all_time": {
                "total_jobs_seen": all_time.get("total_jobs_seen", 0),
                "total_applied": all_time.get("total_applied", 0),
                "total_emailed": all_time.get("total_emailed", 0),
                "total_failed": all_time.get("total_failed", 0),
                "unique_companies": all_time.get("unique_companies", 0)
            },
            "drip_campaign": {
                "total_contacts": total_drip,
                "sent_total": sent_drip,
                "sent_today": pdf_drip_sent,
                "remaining": remaining_drip,
                "percentage_complete": drip_percent,
                "daily_limit": cfg.DAILY_DRIP_LIMIT
            },
            "approval_queue": approval_counts
        }

    # =========================================================================
    # 7-DAY METRIC COMPARISON
    # =========================================================================

    def compile_7day_comparison(self, days: int = 7) -> Dict[str, Any]:
        """Retrieve and compare performance trends across the last 7 calendar days."""
        trends = self.db.get_chart_stats(days=days)

        # Ensure we have data structure even if db has fewer days
        daily_records = []
        for r in trends:
            daily_records.append({
                "date": r.get("date", ""),
                "applied": r.get("total_applied", 0),
                "emailed": r.get("total_emailed", 0),
                "scraped": r.get("total_scraped", 0)
            })

        total_applied_7d = sum(r["applied"] for r in daily_records)
        total_emailed_7d = sum(r["emailed"] for r in daily_records)
        total_scraped_7d = sum(r["scraped"] for r in daily_records)
        num_days = max(1, len(daily_records))

        avg_applied_daily = round(total_applied_7d / num_days, 1)
        avg_emailed_daily = round(total_emailed_7d / num_days, 1)

        # Day-over-Day calculation (today vs yesterday)
        dod_applied_pct = 0.0
        if len(daily_records) >= 2:
            yesterday_val = daily_records[-2]["applied"]
            today_val = daily_records[-1]["applied"]
            if yesterday_val > 0:
                dod_applied_pct = round(((today_val - yesterday_val) / yesterday_val * 100), 1)
            elif today_val > 0:
                dod_applied_pct = 100.0

        return {
            "period_days": num_days,
            "daily_records": daily_records,
            "totals": {
                "applied": total_applied_7d,
                "emailed": total_emailed_7d,
                "scraped": total_scraped_7d
            },
            "averages": {
                "applied_per_day": avg_applied_daily,
                "emailed_per_day": avg_emailed_daily
            },
            "dod_applied_growth_pct": dod_applied_pct
        }

    # =========================================================================
    # INBOX INTELLIGENCE & RECRUITER REPLIES
    # =========================================================================

    def compile_inbox_intelligence(self, days_back: int = 3) -> Dict[str, Any]:
        """Scan inbox via IMAP monitor and categorize recruiter replies vs bounces."""
        monitor = InboxMonitor(dry_run=self.dry_run)
        items = monitor.fetch_inbox_activity(days_back=days_back)

        bounce_count = 0
        auto_replies_count = 0
        rejections_count = 0
        positive_replies = []
        general_count = 0

        for item in items:
            cat = item.get("classification", {}).get("category", "")
            if cat == "hard_bounce":
                bounce_count += 1
            elif cat == "auto_reply":
                auto_replies_count += 1
            elif cat == "rejection":
                rejections_count += 1
            elif cat == "positive_reply":
                positive_replies.append({
                    "sender": item.get("sender", "Recruiter"),
                    "subject": item.get("subject", "Interview Follow-Up"),
                    "date": item.get("date", ""),
                    "snippet": item.get("snippet", "")
                })
            else:
                general_count += 1

        return {
            "total_inspected": len(items),
            "bounce_count": bounce_count,
            "auto_replies_count": auto_replies_count,
            "rejections_count": rejections_count,
            "positive_replies": positive_replies,
            "general_count": general_count
        }

    # =========================================================================
    # RESPONSIVE HTML EMAIL TEMPLATE RENDERING
    # =========================================================================

    def render_html_report(self, report_data: Dict[str, Any]) -> str:
        """Render a polished, executive HTML report formatted for email and browser inspection."""
        metrics = report_data.get("metrics", {})
        today = metrics.get("today", {})
        all_time = metrics.get("all_time", {})
        drip = metrics.get("drip_campaign", {})
        conv = metrics.get("conversion_rates", {})
        trend_7d = report_data.get("trend_7d", {})
        inbox = report_data.get("inbox", {})
        queue = metrics.get("approval_queue", {})

        date_str = metrics.get("date", datetime.now().strftime("%Y-%m-%d"))
        dry_run = report_data.get("dry_run", True)
        mode_badge = (
            '<span style="background:#FEF3C7; color:#92400E; padding:3px 8px; border-radius:9999px; font-weight:700; font-size:11px;">SAFE DRY-RUN MODE</span>'
            if dry_run else
            '<span style="background:#ECFDF5; color:#065F46; padding:3px 8px; border-radius:9999px; font-weight:700; font-size:11px;">LIVE DISPATCH MODE</span>'
        )

        # ── Recruiter Replies HTML ───────────────────────────────────────────
        pos_replies = inbox.get("positive_replies", [])
        if pos_replies:
            replies_cards = ""
            for r in pos_replies:
                replies_cards += f"""
                <div style="background:#FFFFFF; border:1px solid #FCD34D; border-left:4px solid #F59E0B; border-radius:6px; padding:12px; margin-bottom:10px;">
                  <div style="font-size:13px; font-weight:700; color:#0F172A;">{r.get('sender')}</div>
                  <div style="font-size:12px; color:#64748B; margin-top:2px;">Subject: <i>{r.get('subject')}</i> · {r.get('date')}</div>
                  <div style="font-size:12px; color:#1E293B; background:#FFFBEB; padding:8px; border-radius:4px; margin-top:6px;">
                    "{r.get('snippet')}"
                  </div>
                </div>
                """
            inbox_html = f"""
            <div style="background:#FFFDF5; border:1px solid #FDE68A; border-radius:8px; padding:16px; margin-bottom:24px;">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                <h3 style="margin:0; font-size:15px; color:#92400E;">🎯 Recruiter Interview Responses ({len(pos_replies)})</h3>
                <span style="font-size:11px; font-weight:700; background:#FDE68A; color:#78350F; padding:2px 8px; border-radius:9999px;">Action Required</span>
              </div>
              {replies_cards}
            </div>
            """
        else:
            inbox_html = """
            <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:14px; margin-bottom:24px; color:#64748B; font-size:12.5px; text-align:center;">
              <i>📭 No direct recruiter interview replies detected in the last scan. Follow-ups in progress.</i>
            </div>
            """

        # Bounces Warning
        bounce_count = inbox.get("bounce_count", 0)
        bounce_html = ""
        if bounce_count > 0:
            bounce_html = f"""
            <div style="background:#FEF2F2; border-left:4px solid #EF4444; border-radius:6px; padding:10px 14px; margin-bottom:20px; font-size:12px; color:#991B1B;">
              ⚠️ <strong>{bounce_count} email bounces</strong> intercepted and quarantined to safeguard sender reputation.
            </div>
            """

        # ── 7-Day Trend Rows ─────────────────────────────────────────────────
        trend_rows = ""
        for rec in trend_7d.get("daily_records", []):
            trend_rows += f"""
            <tr style="border-bottom:1px solid #F1F5F9;">
              <td style="padding:8px 12px; font-size:12px; color:#334155;">{rec.get('date')}</td>
              <td style="padding:8px 12px; font-size:12px; font-weight:600; color:#1A56DB;">{rec.get('applied', 0)}</td>
              <td style="padding:8px 12px; font-size:12px; color:#8B5CF6;">{rec.get('emailed', 0)}</td>
              <td style="padding:8px 12px; font-size:12px; color:#64748B;">{rec.get('scraped', 0)}</td>
            </tr>
            """

        # ── Pending Approvals Alert ──────────────────────────────────────────
        pending_cnt = queue.get("pending", 0)
        pending_alert = ""
        if pending_cnt > 0:
            pending_alert = f"""
            <div style="background:#EFF6FF; border:1px solid #BFDBFE; border-radius:8px; padding:14px 18px; margin-bottom:24px; display:flex; justify-content:space-between; align-items:center;">
              <div>
                <div style="font-weight:700; color:#1E40AF; font-size:13.5px;">⚠️ Human Approval Required ({pending_cnt} Pending Actions)</div>
                <div style="font-size:12px; color:#3B82F6; margin-top:2px;">Applications and cold outreach drafts await your review before dispatch.</div>
              </div>
              <a href="http://localhost:{cfg.DASHBOARD_PORT}/#approvals" style="background:#1A56DB; color:#FFFFFF; padding:8px 14px; border-radius:6px; font-size:12px; font-weight:700; text-decoration:none;">Review Queue &rarr;</a>
            </div>
            """

        # ── Assemble Master Responsive HTML Document ─────────────────────────
        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CareerPilot AI Daily Report — {date_str}</title>
</head>
<body style="font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color:#F8FAFC; color:#0F172A; line-height:1.5; margin:0; padding:24px 12px;">
  <div style="max-width:680px; margin:0 auto; background:#FFFFFF; border:1px solid #E2E8F0; border-radius:12px; overflow:hidden; box-shadow:0 4px 6px -1px rgba(0, 0, 0, 0.05);">

    <!-- Header Banner -->
    <div style="background:linear-gradient(135deg, #1A56DB 0%, #1E40AF 100%); padding:28px 24px; color:#FFFFFF;">
      <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <div>
          <div style="font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:1px; opacity:0.85; margin-bottom:4px;">
            Autohire.ai · CareerPilot Intelligence Briefing
          </div>
          <h1 style="margin:0; font-size:22px; font-weight:800; letter-spacing:-0.5px;">Daily Executive Run Report</h1>
          <div style="font-size:13px; opacity:0.9; margin-top:4px;">
            Candidate: <strong>{cfg.CANDIDATE_NAME}</strong> · {date_str}
          </div>
        </div>
        <div>
          {mode_badge}
        </div>
      </div>
    </div>

    <div style="padding:24px;">

      {pending_alert}

      <!-- 4 Executive KPI Metric Cards -->
      <table style="width:100%; border-collapse:separate; border-spacing:8px; margin-left:-8px; margin-right:-8px; margin-bottom:20px;">
        <tr>
          <!-- KPI 1: Applied -->
          <td style="width:25%; background:#FAFCFF; border:1px solid #DBEAFE; border-radius:8px; padding:14px 10px; text-align:center;">
            <div style="font-size:22px; font-weight:800; color:#1A56DB;">{today.get('applied', 0)}</div>
            <div style="font-size:11px; font-weight:600; color:#475569; margin-top:2px;">Applied Today</div>
            <div style="font-size:10px; color:#94A3B8; margin-top:2px;">Total: {all_time.get('total_applied', 0)}</div>
          </td>
          <!-- KPI 2: Cold Emails -->
          <td style="width:25%; background:#FAFCFF; border:1px solid #E9D5FF; border-radius:8px; padding:14px 10px; text-align:center;">
            <div style="font-size:22px; font-weight:800; color:#8B5CF6;">{today.get('emailed', 0)}</div>
            <div style="font-size:11px; font-weight:600; color:#475569; margin-top:2px;">Cold Emails</div>
            <div style="font-size:10px; color:#94A3B8; margin-top:2px;">Total: {all_time.get('total_emailed', 0)}</div>
          </td>
          <!-- KPI 3: PDF HR Drip -->
          <td style="width:25%; background:#FAFCFF; border:1px solid #A7F3D0; border-radius:8px; padding:14px 10px; text-align:center;">
            <div style="font-size:22px; font-weight:800; color:#10B981;">{drip.get('sent_today', 0)}</div>
            <div style="font-size:11px; font-weight:600; color:#475569; margin-top:2px;">HR Drip Today</div>
            <div style="font-size:10px; color:#94A3B8; margin-top:2px;">{drip.get('sent_total', 0)}/{drip.get('total_contacts', 1831)}</div>
          </td>
          <!-- KPI 4: Pending Review -->
          <td style="width:25%; background:#FAFCFF; border:1px solid #FDE68A; border-radius:8px; padding:14px 10px; text-align:center;">
            <div style="font-size:22px; font-weight:800; color:#D97706;">{queue.get('pending', 0)}</div>
            <div style="font-size:11px; font-weight:600; color:#475569; margin-top:2px;">Approval Queue</div>
            <div style="font-size:10px; color:#94A3B8; margin-top:2px;">Safety Gate</div>
          </td>
        </tr>
      </table>

      <!-- Pipeline Funnel Table -->
      <div style="border:1px solid #E2E8F0; border-radius:8px; overflow:hidden; margin-bottom:24px;">
        <div style="background:#F8FAFC; padding:12px 16px; border-bottom:1px solid #E2E8F0; font-size:13px; font-weight:700; color:#0F172A;">
          ⚡ Daily Multi-Stage Pipeline Funnel
        </div>
        <table style="width:100%; border-collapse:collapse; text-align:left;">
          <thead>
            <tr style="background:#FAFCFF; font-size:11px; color:#64748B; text-transform:uppercase; letter-spacing:0.5px;">
              <th style="padding:10px 14px; border-bottom:1px solid #E2E8F0;">Pipeline Stage</th>
              <th style="padding:10px 14px; border-bottom:1px solid #E2E8F0;">Processed Count</th>
              <th style="padding:10px 14px; border-bottom:1px solid #E2E8F0;">Stage Pass Rate</th>
              <th style="padding:10px 14px; border-bottom:1px solid #E2E8F0;">Status</th>
            </tr>
          </thead>
          <tbody>
            <tr style="border-bottom:1px solid #F1F5F9; font-size:12.5px;">
              <td style="padding:10px 14px;"><strong>1. Multi-Portal Scrape</strong> (6 portals)</td>
              <td style="padding:10px 14px; font-weight:600;">{today.get('scraped', 0)}</td>
              <td style="padding:10px 14px;">100.0%</td>
              <td style="padding:10px 14px; color:#10B981; font-weight:600;">Completed</td>
            </tr>
            <tr style="border-bottom:1px solid #F1F5F9; font-size:12.5px;">
              <td style="padding:10px 14px;"><strong>2. 7-Stage Filter & Deduplication</strong></td>
              <td style="padding:10px 14px; font-weight:600;">{today.get('filtered', 0)}</td>
              <td style="padding:10px 14px;">{conv.get('filter_pass_pct', 0.0)}%</td>
              <td style="padding:10px 14px; color:#10B981; font-weight:600;">Cleaned</td>
            </tr>
            <tr style="border-bottom:1px solid #F1F5F9; font-size:12.5px;">
              <td style="padding:10px 14px;"><strong>3. LLM Scoring & Skills Match</strong></td>
              <td style="padding:10px 14px; font-weight:600;">{today.get('scored', 0)}</td>
              <td style="padding:10px 14px;">{conv.get('scoring_pass_pct', 0.0)}%</td>
              <td style="padding:10px 14px; color:#10B981; font-weight:600;">Vetted</td>
            </tr>
            <tr style="border-bottom:1px solid #F1F5F9; font-size:12.5px;">
              <td style="padding:10px 14px;"><strong>4. Approval Queue Staging</strong></td>
              <td style="padding:10px 14px; font-weight:600;">{today.get('staged', 0)}</td>
              <td style="padding:10px 14px;">{conv.get('approval_stage_pct', 0.0)}%</td>
              <td style="padding:10px 14px; color:#D97706; font-weight:600;">Review Gate</td>
            </tr>
            <tr style="font-size:12.5px;">
              <td style="padding:10px 14px;"><strong>5. Application & Outreach Dispatch</strong></td>
              <td style="padding:10px 14px; font-weight:600; color:#1A56DB;">{today.get('applied', 0)}</td>
              <td style="padding:10px 14px;">{conv.get('apply_conversion_pct', 0.0)}%</td>
              <td style="padding:10px 14px; color:#1A56DB; font-weight:600;">Submitted</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Recruiter Replies & Bounces Section -->
      {bounce_html}
      {inbox_html}

      <!-- 7-Day Performance Trends Table -->
      <div style="border:1px solid #E2E8F0; border-radius:8px; overflow:hidden; margin-bottom:24px;">
        <div style="background:#F8FAFC; padding:12px 16px; border-bottom:1px solid #E2E8F0; display:flex; justify-content:space-between; align-items:center;">
          <span style="font-size:13px; font-weight:700; color:#0F172A;">📊 7-Day Performance & Activity Trends</span>
          <span style="font-size:11px; color:#64748B;">Avg: <strong>{trend_7d.get('averages', {}).get('applied_per_day', 0)}</strong> apps/day</span>
        </div>
        <table style="width:100%; border-collapse:collapse; text-align:left;">
          <thead>
            <tr style="background:#FAFCFF; font-size:11px; color:#64748B; text-transform:uppercase;">
              <th style="padding:8px 12px; border-bottom:1px solid #E2E8F0;">Date</th>
              <th style="padding:8px 12px; border-bottom:1px solid #E2E8F0;">Applied</th>
              <th style="padding:8px 12px; border-bottom:1px solid #E2E8F0;">Cold Emails</th>
              <th style="padding:8px 12px; border-bottom:1px solid #E2E8F0;">Jobs Scraped</th>
            </tr>
          </thead>
          <tbody>
            {trend_rows if trend_rows else '<tr><td colspan="4" style="padding:14px; text-align:center; color:#94A3B8; font-size:12px;">No historical records found.</td></tr>'}
          </tbody>
        </table>
      </div>

      <!-- 1,831 PDF HR Drip Progress Bar -->
      <div style="background:#FAFCFF; border:1px solid #DBEAFE; border-radius:8px; padding:16px; margin-bottom:20px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
          <div style="font-size:13px; font-weight:700; color:#1E40AF;">📬 1,831 Verified Recruiter Drip Pipeline</div>
          <div style="font-size:12px; font-weight:700; color:#1A56DB;">{drip.get('percentage_complete', 0)}% Completed</div>
        </div>
        <div style="height:8px; background:#E2E8F0; border-radius:9999px; overflow:hidden; margin-bottom:8px;">
          <div style="height:100%; width:{min(100, drip.get('percentage_complete', 0))}%; background:linear-gradient(90deg, #1A56DB 0%, #2563EB 100%);"></div>
        </div>
        <div style="display:flex; justify-content:space-between; font-size:11px; color:#64748B;">
          <span>Sent: <strong>{drip.get('sent_total', 0)}</strong> of {drip.get('total_contacts', 1831)} recruiters</span>
          <span>Daily Quota: <strong>50 emails/day</strong></span>
          <span>Remaining: <strong>{drip.get('remaining', 1831)}</strong></span>
        </div>
      </div>

      <!-- Dashboard Quick Access -->
      <div style="text-align:center; margin:24px 0 12px 0;">
        <a href="http://localhost:{cfg.DASHBOARD_PORT}/" style="display:inline-block; background:#1A56DB; color:#FFFFFF; padding:10px 24px; border-radius:8px; font-size:13px; font-weight:700; text-decoration:none; box-shadow:0 2px 4px rgba(26, 86, 219, 0.2);">
          Open CareerPilot SaaS Dashboard &rarr;
        </a>
      </div>

    </div>

    <!-- Email Footer -->
    <div style="background:#F8FAFC; border-top:1px solid #E2E8F0; padding:16px 24px; text-align:center; font-size:11.5px; color:#94A3B8;">
      <div>Autohire.ai · CareerPilot AI Enterprise Autonomous Application System</div>
      <div style="margin-top:2px;">Configured for: <strong>{cfg.CANDIDATE_NAME}</strong> ({cfg.PERSONAL_EMAIL}) · Asia/Kolkata</div>
    </div>

  </div>
</body>
</html>"""
        return html

    # =========================================================================
    # GENERATION & PERSISTENCE ORCHESTRATION
    # =========================================================================

    def generate_daily_report(
        self,
        state: Optional[Dict[str, Any]] = None,
        date_str: Optional[str] = None,
        send_email: bool = False,
        dry_run: Optional[bool] = None
    ) -> Dict[str, Any]:
        """
        Orchestrate the end-to-end report compilation:
        1. Aggregate daily and all-time metrics
        2. Compile 7-day trend history
        3. Scan and classify career inbox
        4. Render HTML template and text summary
        5. Persist artifact to logs/reports/
        6. Record in email_history table
        7. Optionally dispatch via EmailSender
        """
        is_dry = self.dry_run if dry_run is None else dry_run
        today_str = date_str or datetime.now().strftime("%Y-%m-%d")

        logger.info(f"[ReportService] Generating executive daily report for {today_str} (Dry-Run: {is_dry})...")

        # 1. Gather all report components
        metrics = self.compile_daily_metrics(state=state, date_str=today_str)
        trend_7d = self.compile_7day_comparison(days=7)
        inbox = self.compile_inbox_intelligence(days_back=3)

        report_payload = {
            "candidate": {
                "name": cfg.CANDIDATE_NAME,
                "email": cfg.PERSONAL_EMAIL,
                "role": "AI/ML & Agentic AI Engineer"
            },
            "date": today_str,
            "generated_at": datetime.now().isoformat(),
            "dry_run": is_dry,
            "metrics": metrics,
            "trend_7d": trend_7d,
            "inbox": inbox
        }

        # 2. Render HTML
        html_content = self.render_html_report(report_payload)

        # 3. Save artifacts to disk
        html_file = self.reports_dir / f"daily_report_{today_str}.html"
        json_file = self.reports_dir / f"daily_report_{today_str}.json"

        try:
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(html_content)
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(report_payload, f, indent=2)
            logger.info(f"✅ Daily report artifact written to: {html_file}")
        except Exception as e:
            logger.warning(f"Could not write report files to disk: {e}")

       
        # 4. Generate the report email subject.
        subject_line = (
            f"[Autohire.ai] Daily Briefing {today_str} | "
            f"Applied: {metrics['today']['applied']} | "
            f"Emailed: {metrics['today']['emailed']} | "
            f"HR Drip: {metrics['drip_campaign']['sent_today']}/50 | "
            f"Replies: {len(inbox.get('positive_replies', []))}"
        )

        # 5. Optional live email dispatch.
        # Dry-run must never contact SMTP.
        email_sent_ok = False

        if send_email and not is_dry:
            try:
                sender = EmailSender(dry_run=False)

                email_sent_ok = sender.send_summary_email(
                    to_email=cfg.PERSONAL_EMAIL,
                    subject=subject_line,
                    html_body=html_content,
                    dry_run=False,
                )

            except Exception:
                logger.exception(
                    "[ReportService] Error dispatching report email."
                )

        # 6. Record history only after confirmed live delivery.
        if email_sent_ok:
            try:
                with self.db._get_conn() as conn:
                    conn.execute(
                        """
                        INSERT INTO email_history (
                            job_hash,
                            recipient_email,
                            recipient_name,
                            company,
                            subject,
                            body,
                            email_type,
                            status,
                            sent_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, 'daily_report', 'sent', ?)
                        """,
                        (
                            f"report_{today_str}",
                            cfg.PERSONAL_EMAIL,
                            cfg.CANDIDATE_NAME,
                            "Autohire.ai",
                            subject_line,
                            f"Daily Executive HTML Report for {today_str}",
                            datetime.now().isoformat(),
                        ),
                    )

            except Exception:
                logger.exception(
                    "[ReportService] Could not record confirmed report email."
                )

        return {
            "status": "success",
            "date": today_str,
            "subject": subject_line,
            "html_path": str(html_file),
            "json_path": str(json_file),
            "html_content": html_content,
            "metrics": metrics,
            "trend_7d": trend_7d,
            "inbox": inbox,
            "email_dispatched": email_sent_ok,
            "dry_run": is_dry
        }
