#!/usr/bin/env python3
"""
librecrawl-mcp orphan watchdog.

Designed to run from cron (recommended every 15 min). Keeps the server
ephemeral by deleting any session whose client disconnected without
calling librecrawl_audit_zip(auto_cleanup=True).

Policy (override via env vars):
  TTL_DONE_S        = 3600  (1h)    — done sessions purged if older
  TTL_CRAWLING_S    = 14400 (4h)    — crawling sessions purged if abandoned
  TTL_QUEUED_S      = 1800  (30min) — queued sessions never started
  failed/cancelled                  — purged immediately

Cascades:
  - state.db rows (sessions, artifacts, events, chunks)
  - REPORTS_DIR files older than TTL_DONE_S
  - Upstream LibreCrawl DB crawl records (crawls, crawled_urls,
    crawl_links, crawl_issues)

Env vars (defaults shown):
  LIBRECRAWL_STATE_DB     = ~/librecrawl-state.db
  LIBRECRAWL_UPSTREAM_DB  = ~/webapps/librecrawl/data/users.db
  REPORTS_DIR             = ~/librecrawl-reports
  LIBRECRAWL_WATCHDOG_LOG = ~/librecrawl-watchdog.log

Install (cron):
  */15 * * * * /usr/bin/python3 /path/to/watchdog.py >> /path/to/log 2>&1
"""
import os, sys, time, glob, sqlite3

HOME = os.path.expanduser('~')
STATE_DB     = os.environ.get('LIBRECRAWL_STATE_DB',     f'{HOME}/librecrawl-state.db')
UPSTREAM_DB  = os.environ.get('LIBRECRAWL_UPSTREAM_DB',  f'{HOME}/webapps/librecrawl/data/users.db')
REPORTS_DIR  = os.environ.get('REPORTS_DIR',             f'{HOME}/librecrawl-reports')
LOG_PATH     = os.environ.get('LIBRECRAWL_WATCHDOG_LOG', f'{HOME}/librecrawl-watchdog.log')

TTL_DONE_S     = int(os.environ.get('TTL_DONE_S',     60 * 60))
TTL_CRAWLING_S = int(os.environ.get('TTL_CRAWLING_S', 4 * 60 * 60))
TTL_QUEUED_S   = int(os.environ.get('TTL_QUEUED_S',   30 * 60))

NOW = time.time()


def log(msg: str) -> None:
    line = f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {msg}'
    print(line)
    try:
        with open(LOG_PATH, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def main() -> int:
    if not os.path.exists(STATE_DB):
        log('watchdog: no state.db yet — nothing to do')
        return 0
    sdb = sqlite3.connect(STATE_DB)
    cur = sdb.cursor()
    rows = cur.execute(
        'SELECT id, status, started_at, upstream_crawl_id FROM sessions'
    ).fetchall()
    purge_ids: list[tuple[str, str]] = []
    purge_upstream: list[int] = []
    for sid, status, started_at, upstream in rows:
        age = NOW - (started_at or NOW)
        purge, reason = False, ''
        if status == 'done' and age > TTL_DONE_S:
            purge, reason = True, f'done >{age/3600:.1f}h'
        elif status == 'crawling' and age > TTL_CRAWLING_S:
            purge, reason = True, f'crawling >{age/3600:.1f}h (abandoned)'
        elif status == 'queued' and age > TTL_QUEUED_S:
            purge, reason = True, f'queued >{age/60:.0f}min'
        elif status in ('failed', 'cancelled'):
            purge, reason = True, status
        if purge:
            purge_ids.append((sid, reason))
            if upstream:
                purge_upstream.append(upstream)
    if not purge_ids:
        log(f'watchdog: scanned {len(rows)} sessions, 0 to purge')
        return 0
    for sid, reason in purge_ids:
        for table in ('events', 'artifacts', 'chunks', 'sessions'):
            cur.execute(f'DELETE FROM {table} WHERE session_id = ? OR id = ?', (sid, sid))
        log(f'watchdog: purged session {sid} ({reason})')
    sdb.commit()
    sdb.close()
    purged_files = 0
    purged_bytes = 0
    if os.path.isdir(REPORTS_DIR):
        for fp in glob.glob(f'{REPORTS_DIR}/*'):
            try:
                mtime = os.path.getmtime(fp)
                if NOW - mtime > TTL_DONE_S:
                    sz = os.path.getsize(fp)
                    os.unlink(fp)
                    purged_files += 1
                    purged_bytes += sz
            except Exception as e:
                log(f'watchdog: error deleting {fp}: {e}')
    if purged_files:
        log(f'watchdog: purged {purged_files} orphan files ({purged_bytes} bytes)')
    if purge_upstream and os.path.exists(UPSTREAM_DB):
        try:
            udb = sqlite3.connect(UPSTREAM_DB)
            ucur = udb.cursor()
            for cid in purge_upstream:
                for t in ('crawl_issues', 'crawl_links', 'crawled_urls', 'crawls'):
                    try:
                        ucur.execute(f'DELETE FROM {t} WHERE crawl_id = ? OR id = ?', (cid, cid))
                    except Exception as e:
                        log(f'watchdog: upstream {t} delete error: {e}')
            udb.commit()
            udb.close()
            log(f'watchdog: purged {len(purge_upstream)} upstream crawl records')
        except Exception as e:
            log(f'watchdog: upstream DB error: {e}')
    log(f'watchdog: cycle complete — purged {len(purge_ids)} sessions')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        log(f'watchdog: FATAL error: {e}')
        sys.exit(1)
