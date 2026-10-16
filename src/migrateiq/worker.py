import argparse
import time

from .config import get_settings
from .db import SessionLocal, init_db
from .jobs import claim_next_job, recover_interrupted_jobs, run_job


def work_once() -> bool:
    with SessionLocal() as session:
        job = claim_next_job(session)
        if not job:
            return False
        job_id = job.id
    with SessionLocal() as session:
        run_job(session, job_id)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="MigrateIQ database-backed migration worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    args = parser.parse_args()
    init_db()
    with SessionLocal() as session:
        recover_interrupted_jobs(session)
    if args.once:
        work_once()
        return
    while True:
        if not work_once():
            time.sleep(get_settings().worker_poll_seconds)


if __name__ == "__main__":
    main()
