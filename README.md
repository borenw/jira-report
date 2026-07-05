# Jira → SQLite → HTML report

Pull issues from a **Jira Server / Data Center** instance over the REST API
(using `curl` + your real username/password), store them in a local SQLite
database, then generate a single self-contained, interactive HTML report with
filters, bar charts, and a burndown trend line with best/likely/worst-case
completion-date forecasts.

```
jira_secrets.ini  ->  jira_to_db.py  ->  jira.db  ->  chart.py  ->  report.html
   (your creds)        (fetch)          (database)     (build)      (open in browser)
```

## Requirements

- Python 3 (standard library only — no `pip install` needed)
- `curl` on your PATH
- Network access to your Jira server
- A Jira account whose **username + password** work for HTTP Basic auth
  (this is a Server/Data Center feature; Atlassian **Cloud** does not accept
  passwords and would need an API token instead)

## Steps

1. **Fill in your credentials.** Copy the template, then edit it:
   ```
   cp jira_secrets.ini.example jira_secrets.ini
   ```
   Open `jira_secrets.ini` and set:
   - `base_url` — e.g. `https://jira.mycompany.com` (no trailing slash)
   - `username` — your Jira login name
   - `password` — your actual account password
   - `jql` — which issues to pull, e.g. `project = ABC AND created >= -180d ORDER BY created DESC`

2. **Lock down the secrets file** so other users on the host can't read it:
   ```
   chmod 600 jira_secrets.ini
   ```
   It is already listed in `.gitignore`, so it will never be committed.

3. **Build the database:**
   ```
   python3 jira_to_db.py
   ```
   This pages through every matching issue and writes `jira.db`. It prints
   progress like `page 1: +100 issues (fetched 100/342)`. Re-running is safe —
   issues are upserted by key, so existing rows are refreshed, not duplicated.

   Optional overrides:
   ```
   python3 jira_to_db.py --config jira_secrets.ini --db jira.db
   python3 jira_to_db.py --jql "project = XYZ AND status != Done"
   ```

4. **Generate the report:**
   ```
   python3 chart.py
   ```
   This reads `jira.db` and writes `report.html`.
   Optional overrides: `python3 chart.py --db jira.db --out report.html`

5. **Open the report.** Double-click `report.html`, or:
   ```
   xdg-open report.html      # Linux
   ```
   The page is fully self-contained (all data embedded, no internet needed).

6. **Refresh later.** Re-run steps 3 and 4 whenever you want up-to-date data.

## What's in the report

- **Filters (pull-downs):** Project, User (assignee), Status, State
  (Open / Resolved-Closed), and a forecast window. All charts update live and
  honour every filter together.
- **Summary tiles:** filtered totals for all / open / resolved.
- **Bar charts:** issues by status, by project, by assignee (top 15).
- **Trend line (x-axis = day):** cumulative *open* issues over time — a burndown
  built from created vs. resolved dates, recomputed for the current filter.
- **Forecast:** three dashed projections extrapolated from the recent
  resolution rate within the chosen window:
  - **Best case** (green) — fast burn-down rate (20th percentile of daily change)
  - **Likely** (blue) — average daily change
  - **Worst case** (red) — slow rate (80th percentile)

  Each line is labelled with the date it **crosses the x-axis** (open = 0, i.e.
  projected "everything done"). If the backlog isn't shrinking, that case is
  labelled *no completion — backlog not shrinking* instead. Projections are
  capped at 3× the historical span so they stay readable.

## Files

| File | Purpose | Commit to git? |
|------|---------|----------------|
| `jira_secrets.ini` | your credentials + JQL | **No** (git-ignored) |
| `jira_to_db.py`    | fetch issues via curl → `jira.db` | yes |
| `chart.py`         | build `report.html` from `jira.db` | yes |
| `jira.db`          | generated SQLite database | no (git-ignored) |
| `report.html`      | generated report | your call |

## Security notes

- Your password is passed to `curl` through a config file on **stdin**
  (`curl --config -`), so it never appears in the process list (`ps`) or your
  shell history.
- Keep `jira_secrets.ini` at mode `600`. `jira_to_db.py` warns if it isn't.
- If you get a `401`/`403` or an HTML login page back, the server may block
  Basic auth, require a CAPTCHA after failed logins, or sit behind SSO — check
  with your Jira admin.

## Database schema (`issues` table)

`key`, `project`, `summary`, `issue_type`, `status`, `status_category`,
`priority`, `assignee`, `reporter`, `created`, `updated`, `resolved`, `labels`
— plus a `fetch_runs` table logging each pull (timestamp, JQL, count).
