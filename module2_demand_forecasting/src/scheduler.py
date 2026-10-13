"""
src/scheduler.py
----------------
Module 2 — Weekly Forecast Scheduler (dedicated process).

Run as a SEPARATE process from FastAPI:
  python -m src.scheduler

Features:
  - APScheduler BackgroundScheduler with cron trigger.
  - PostgreSQL advisory lock prevents concurrent execution across processes.
  - forecast_run table records run status persistently.
  - On startup: detects interrupted runs (stuck in 'running') and marks them 'interrupted'.
  - Catch-up after missed runs is MANUAL (POST /api/forecast/trigger) — not automatic.
  - Training is NOT scheduled; run `python src/train.py` manually.

Local deployment note:
  The scheduler process requires the computer to remain running.
  Scheduled forecasts will not execute while the machine is off or in sleep mode.
  For reliable weekly scheduling, deploy on an always-on host (cloud VM, server).
"""

import sys
import logging
import time
import signal
from pathlib import Path
from datetime import datetime, timezone

import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    SCHEDULER_TIMEZONE, SCHEDULER_DAY_OF_WEEK, SCHEDULER_HOUR, SCHEDULER_MINUTE,
    INTERRUPTED_RUN_THRESHOLD_HOURS, LOGS_DIR
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [scheduler] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOGS_DIR / "scheduler.log"), encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


def get_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )


def check_interrupted_runs():
    """
    On scheduler startup: mark any 'running' rows older than threshold as 'interrupted'.
    Does NOT rerun them — manual recovery via POST /api/forecast/trigger.
    """
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """UPDATE forecast_run
               SET status = 'interrupted',
                   run_completed_at = NOW(),
                   error_detail = 'Scheduler restarted while this run was in progress.'
               WHERE status = 'running'
                 AND run_started_at < NOW() - INTERVAL '%s hours'
               RETURNING forecast_run_id, run_started_at""",
            (INTERRUPTED_RUN_THRESHOLD_HOURS,)
        )
        interrupted = cur.fetchall()
        conn.commit()
        for run_id, started_at in interrupted:
            logger.warning(
                f"Interrupted run marked: {run_id} (started {started_at}). "
                "Use POST /api/forecast/trigger to rerun manually."
            )
    finally:
        cur.close()
        conn.close()


def scheduled_forecast_job():
    """
    The weekly scheduled forecast job.
    Delegates to the pipeline's run_forecast_pipeline() which handles
    advisory locking and run-record management.
    """
    logger.info("Scheduled forecast job triggered.")
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from pipeline import run_forecast_pipeline
        run_id = run_forecast_pipeline(triggered_by="scheduler")
        if run_id:
            logger.info(f"Scheduled forecast completed. run_id={run_id}")
        else:
            logger.info("Scheduled forecast skipped (lock not acquired or no-op).")
    except Exception as e:
        logger.error(f"Scheduled forecast failed: {e}", exc_info=True)


def run_manual_forecast():
    """
    Safe manual trigger for development / catch-up.
    Called from CLI: python src/scheduler.py --run-now
    """
    logger.info("Manual forecast trigger invoked.")
    sys.path.insert(0, str(Path(__file__).parent))
    from pipeline import run_forecast_pipeline
    run_id = run_forecast_pipeline(triggered_by="manual")
    return run_id


def start_scheduler():
    """Start the APScheduler with weekly cron and graceful shutdown on SIGINT/SIGTERM."""
    logger.info("Module 2 Scheduler starting...")
    logger.info(
        f"  Schedule: {SCHEDULER_DAY_OF_WEEK} at {SCHEDULER_HOUR:02d}:{SCHEDULER_MINUTE:02d} "
        f"({SCHEDULER_TIMEZONE})"
    )
    logger.info("  Training is NOT scheduled. Run `python src/train.py` manually.")
    logger.info(
        "  IMPORTANT: This scheduler requires the computer to remain running. "
        "Deploy on an always-on host for production use."
    )

    # Startup check
    check_interrupted_runs()

    scheduler = BackgroundScheduler(timezone=SCHEDULER_TIMEZONE)
    scheduler.add_job(
        scheduled_forecast_job,
        trigger=CronTrigger(
            day_of_week=SCHEDULER_DAY_OF_WEEK,
            hour=SCHEDULER_HOUR,
            minute=SCHEDULER_MINUTE,
            timezone=SCHEDULER_TIMEZONE,
        ),
        id="weekly_forecast",
        name="Weekly Demand Forecast",
        max_instances=1,      # prevents overlap within this process
        misfire_grace_time=3600,  # 1 hour: if fire time is missed, still run within grace
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started. Waiting for scheduled runs...")

    # Graceful shutdown
    def _shutdown(signum, frame):
        logger.info("Shutdown signal received. Stopping scheduler...")
        scheduler.shutdown(wait=True)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        time.sleep(60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Module 2 Scheduler")
    parser.add_argument("--run-now", action="store_true", help="Run forecast immediately and exit")
    args = parser.parse_args()

    if args.run_now:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        run_id = run_manual_forecast()
        print(f"Manual forecast complete. run_id={run_id}")
    else:
        start_scheduler()
