#!/usr/bin/env python3
"""Apply the session-persistence patch to upstream LibreCrawl's main.py.

Without this patch LibreCrawl reads `session_id` BEFORE `get_or_create_crawler()`
creates it, so `crawl_id` is always null and crawl results are never written to
the SQLite DB — every audit comes back empty. This moves the `session_id` read to
AFTER the crawler (and its session) is initialised.

Idempotent: safe to run more than once. Usage: python3 patch-librecrawl.py main.py
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "main.py"
content = open(path, encoding="utf-8").read()

old = """    user_id = session.get('user_id')
    session_id = session.get('session_id')
    tier = session.get('tier', 'guest')"""

new = """    user_id = session.get('user_id')
    tier = session.get('tier', 'guest')"""

old2 = """    # Get or create crawler for this session
    crawler = get_or_create_crawler()"""

new2 = """    # Get or create crawler for this session (also initialises session_id)
    crawler = get_or_create_crawler()
    session_id = session.get('session_id')  # Must read AFTER get_or_create_crawler sets it"""

if old not in content:
    print("Session persistence patch already applied or not needed — skipping")
else:
    content = content.replace(old, new, 1)
    content = content.replace(old2, new2, 1)
    open(path, "w", encoding="utf-8").write(content)
    print("Session persistence patch applied")
