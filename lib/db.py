"""Shared database helpers."""

import signal
import logging
from contextlib import contextmanager

import psycopg2

from lib.config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS

log = logging.getLogger("pogo-seeder")


def get_connection():
    """Create a new psycopg2 connection (autocommit=False)."""
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASS,
    )
    conn.autocommit = False
    return conn


@contextmanager
def safe_transaction():
    """Context manager: yields (conn, cur).

    - Commits on success.
    - Rolls back on exception or SIGINT (Ctrl+C).
    - Always closes connection.
    """
    conn = get_connection()
    _interrupted = False

    def _on_sigint(signum, frame):
        nonlocal _interrupted
        _interrupted = True
        log.warning("\n⚠ Ctrl+C received — rolling back...")
        raise KeyboardInterrupt

    prev_handler = signal.signal(signal.SIGINT, _on_sigint)

    try:
        with conn.cursor() as cur:
            yield conn, cur

        if _interrupted:
            conn.rollback()
            log.warning("  ↩ Transaction rolled back (interrupted)")
        else:
            conn.commit()
            log.info("\n✅ All done! Data committed.")

    except KeyboardInterrupt:
        conn.rollback()
        log.warning("  ↩ Transaction rolled back (Ctrl+C)")
        raise

    except Exception as e:
        conn.rollback()
        log.error("\n❌ Error: %s", e)
        raise

    finally:
        signal.signal(signal.SIGINT, prev_handler)
        conn.close()
