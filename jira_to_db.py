#!/usr/bin/env python3
"""
jira_to_db.py — Pull Jira issues via the REST API (using curl + HTTP Basic auth
with your real username + password, i.e. Jira Server / Data Center) and store
them in a local SQLite database for later HTML chart generation.

Credentials live in a separate, non-public file (default: jira_secrets.ini)
that you should keep out of version control and lock down with `chmod 600`.

Usage:
    python3 jira_to_db.py                       # uses jira_secrets.ini -> jira.db
    python3 jira_to_db.py --config path.ini --db out.db
    python3 jira_to_db.py --jql "project = ABC AND status != Done"

The credential (username:password) is passed to curl through a --config file on
stdin, so it never appears in the process argument list (`ps` / shell history).
"""

import argparse
import configparser
import json
import os
import sqlite3
import stat
import subprocess
import sys
from datetime import datetime, timezone

# Classic Jira Server / Data Center search endpoint (startAt pagination).
SEARCH_PATH = "/rest/api/2/search"

# Fields we fetch and the chart-friendly columns we flatten them into.
FIELDS = [
    "summary", "status", "issuetype", "priority",
    "assignee", "reporter", "created", "updated",
    "resolutiondate", "project", "labels",
]


def load_config(path):
    if not os.path.exists(path):
        sys.exit(f"error: credentials file not found: {path}\n"
                 f"       copy the template and fill it in, then `chmod 600 {path}`")

    # Warn (don't hard-fail) if the secrets file is world/group readable.
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        print(f"warning: {path} is readable by others (mode {oct(mode)}); "
              f"run `chmod 600 {path}`", file=sys.stderr)

    cfg = configparser.ConfigParser()
    cfg.read(path)
    try:
        j = cfg["jira"]
        conf = {
            "base_url": j["base_url"].rstrip("/"),
            "username": j["username"].strip(),
            "password": j["password"].strip(),
            "jql": j.get("jql", "ORDER BY created DESC").strip(),
        }
    except KeyError as e:
        sys.exit(f"error: missing key {e} in [jira] section of {path}")

    if "YOURSITE" in conf["base_url"] or "PASTE_YOUR" in conf["password"]:
        sys.exit(f"error: {path} still contains template placeholders — fill it in first")
    return conf


def curl_search(conf, jql, start_at):
    """One POST to the Jira search endpoint via curl. Returns parsed JSON."""
    url = conf["base_url"] + SEARCH_PATH
    body = {"jql": jql, "fields": FIELDS, "startAt": start_at, "maxResults": 100}

    # Credentials go in a curl config read from stdin -> not visible in argv.
    curl_config = f'user = "{conf["username"]}:{conf["password"]}"\n'

    proc = subprocess.run(
        [
            "curl", "-s", "--fail-with-body", "--config", "-",
            "-X", "POST", url,
            "-H", "Accept: application/json",
            "-H", "Content-Type: application/json",
            "--data", json.dumps(body),
        ],
        input=curl_config,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"error: curl failed (exit {proc.returncode}): "
                 f"{proc.stdout.strip() or proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.exit(f"error: could not parse Jira response:\n{proc.stdout[:500]}")


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS issues (
            key             TEXT PRIMARY KEY,
            project         TEXT,
            summary         TEXT,
            issue_type      TEXT,
            status          TEXT,
            status_category TEXT,
            priority        TEXT,
            assignee        TEXT,
            reporter        TEXT,
            created         TEXT,
            updated         TEXT,
            resolved        TEXT,
            labels          TEXT
        );
        CREATE TABLE IF NOT EXISTS fetch_runs (
            run_at      TEXT,
            jql         TEXT,
            issue_count INTEGER
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.commit()


def flatten(issue):
    f = issue.get("fields", {}) or {}

    def name(obj):
        return (obj or {}).get("name")

    status = f.get("status") or {}
    return (
        issue.get("key"),
        (f.get("project") or {}).get("key"),
        f.get("summary"),
        name(f.get("issuetype")),
        name(status),
        (status.get("statusCategory") or {}).get("name"),
        name(f.get("priority")),
        (f.get("assignee") or {}).get("displayName"),
        (f.get("reporter") or {}).get("displayName"),
        f.get("created"),
        f.get("updated"),
        f.get("resolutiondate"),
        ",".join(f.get("labels") or []),
    )


def main():
    ap = argparse.ArgumentParser(description="Fetch Jira issues into a SQLite DB.")
    ap.add_argument("--config", default="jira_secrets.ini", help="credentials .ini file")
    ap.add_argument("--db", default="jira.db", help="output SQLite database")
    ap.add_argument("--jql", help="override the JQL from the config file")
    args = ap.parse_args()

    conf = load_config(args.config)
    jql = args.jql or conf["jql"]

    conn = sqlite3.connect(args.db)
    init_db(conn)

    # Remember the base URL so chart.py can build issue links without the creds.
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('base_url', ?)",
                 (conf["base_url"],))
    conn.commit()

    fetched = 0
    page = 0
    start_at = 0
    while True:
        data = curl_search(conf, jql, start_at)
        issues = data.get("issues", [])
        rows = [flatten(i) for i in issues]
        conn.executemany(
            "INSERT OR REPLACE INTO issues "
            "(key, project, summary, issue_type, status, status_category, "
            " priority, assignee, reporter, created, updated, resolved, labels) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        fetched += len(rows)
        page += 1
        available = data.get("total", fetched)
        print(f"page {page}: +{len(rows)} issues (fetched {fetched}/{available})")

        if not issues or fetched >= available:
            break
        start_at += len(issues)
    total = fetched

    conn.execute(
        "INSERT INTO fetch_runs (run_at, jql, issue_count) VALUES (?,?,?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), jql, total),
    )
    conn.commit()
    conn.close()
    print(f"done: {total} issues written to {args.db}")


if __name__ == "__main__":
    main()
