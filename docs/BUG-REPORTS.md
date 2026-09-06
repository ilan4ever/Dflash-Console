# Bug reports and diagnostics

DFlash Console separates **public bug reports** from **private diagnostic logs**.

## User flow (in-app)

1. Open **About** → **Report a bug**.
2. Describe the problem and reproduction steps.
3. Click **Copy report & open GitHub**.
4. A redacted diagnostic report is copied to the clipboard.
5. GitHub opens the bug report template — paste only what you are comfortable sharing.

Full logs are **not** posted to GitHub automatically.

## What is collected locally

| File | Purpose |
|------|---------|
| `logs/support-journal.log` | Rotating journal (max ~2500 lines) — boots, loads, errors, active client |
| `logs/support-meta.json` | First run, first server start, model activity history |
| `logs/*.log` | Engine, console API, startup, API access (existing) |

The support journal trims oldest lines when the file grows too large.

## API

| Endpoint | Purpose |
|----------|---------|
| `GET /api/diagnostics/bundle` | Redacted JSON or `format=text` report |
| `POST /api/diagnostics/report` | Build report, optional private upload, return clipboard text + GitHub URL |
| `GET /api/console/logs` | Aggregated logs for the Developer logs panel |

## Private upload (developer / website)

Set in `config.json`:

```json
"support_upload_url": "https://onevoiceai.in/wp-json/dflash/v1/report",
"support_report_token": "<ingest-token>"
```

Or `DFLASH_SUPPORT_UPLOAD_URL` and `DFLASH_REPORT_TOKEN` environment variables.

Header on upload: `X-DFlash-Report-Token`. Server token file: `dflash-console-private/.report-token`.

When configured, users can opt in to **Send full diagnostic log privately** in the bug report dialog. The POST body is JSON:

```json
{
  "report_id": "...",
  "version": "0.3.143",
  "boot_id": "...",
  "user_note": "...",
  "reproduction": "...",
  "bundle_text": "...",
  "bundle_b64": "..."
}
```

Your website should store this server-side and email or queue it — **never** expose raw user logs in a public GitHub issue.

## GitHub vs website

| Channel | Content | Visibility |
|---------|---------|------------|
| GitHub Issues | User-written summary + optional pasted excerpt | Public |
| Website upload | Full diagnostic bundle | Private (your backend) |

Recommended: keep GitHub for triage and discussion; use the website endpoint for full logs.

## Redaction

Diagnostic bundles redact config keys containing `token`, `password`, `secret`, or `api_key`. Users should still review the clipboard before pasting into GitHub.

## CLI

```powershell
dflash report          # machine snapshot (existing)
curl http://127.0.0.1:8900/api/diagnostics/bundle?format=text
```
