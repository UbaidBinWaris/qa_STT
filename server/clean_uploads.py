#!/usr/bin/env python3
"""Cleanup background task: retains only the newest 30 audio files in uploads/."""
import asyncio
import logging
import os
import sys
import time

# Ensure imports work regardless of working directory
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import db

logger = logging.getLogger("clean_uploads")

UPLOAD_DIR = os.path.join(SERVER_DIR, "uploads")
MAX_FILES = 30
CHECK_INTERVAL_SEC = 300  # 5 minutes minimum check interval


def run_cleanup(max_files: int = MAX_FILES):
    """Low-priority file prune: keeps max_files newest files and deletes older files and DB records."""
    if not os.path.exists(UPLOAD_DIR):
        return

    # Gather all non-hidden files in uploads/
    entries = []
    try:
        for entry in os.scandir(UPLOAD_DIR):
            if entry.is_file() and not entry.name.startswith("."):
                entries.append(entry)
    except OSError as err:
        logger.warning(f"Failed to scan upload directory: {err}")
        return

    if len(entries) <= max_files:
        return

    # Sort entries by mtime descending (newest first)
    entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)
    to_delete = entries[max_files:]

    conn = db.connect()
    deleted_count = 0

    for entry in to_delete:
        filepath = entry.path
        filename = entry.name

        # Yield execution (50ms sleep) to minimize CPU/disk IO load
        time.sleep(0.05)

        try:
            # Look up matching call record in database to delete associated metadata
            rows = conn.execute(
                "SELECT id FROM calls WHERE audio_path=? OR filename=?",
                (filepath, filename),
            ).fetchall()

            for row in rows:
                call_id = row["id"]
                db.delete_call(call_id)

            if os.path.exists(filepath):
                os.remove(filepath)
                deleted_count += 1
        except Exception as err:
            logger.error(f"Error deleting file {filename}: {err}")

    if deleted_count > 0:
        logger.info(f"Auto-cleanup pruned {deleted_count} old file(s). Retained top {max_files} latest files.")


async def start_cleaner_task():
    """Background asyncio daemon running periodic cleanup with low priority."""
    logger.info("Uploads auto-cleanup background task started.")
    loop = asyncio.get_event_loop()
    while True:
        try:
            # Execute cleanup in thread pool so disk I/O does not block event loop
            await loop.run_in_executor(None, run_cleanup, MAX_FILES)
        except Exception as err:
            logger.error(f"Error in cleanup task: {err}")
        await asyncio.sleep(CHECK_INTERVAL_SEC)


def main():
    """Standalone runner if invoked directly from CLI."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run_cleanup()


if __name__ == "__main__":
    main()
