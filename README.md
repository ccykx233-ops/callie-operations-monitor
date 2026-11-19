# Callie Operations Monitor

Independent, read-only business health checks for the Callie customer-service stack.

## Safety boundary

- Reads selected operational tables.
- Writes only the dedicated `callie_monitor` PostgreSQL schema.
- Cannot send customer messages, call Dify, claim order tasks, or change business state.
- DingTalk is disabled unless repository variable `MONITOR_MODE` is exactly `active`.
- Secrets belong in GitHub Actions Secrets and must never be committed.

## Schedule

The workflow starts every ten minutes. Worker health is checked on every run. Workbench synchronization and order lookup are audited once per completed natural hour through idempotent schedule keys. A concise DingTalk digest is sent once after 18:00 Asia/Shanghai, including a no-problem result. Database daily summaries are generated after 00:15 Asia/Shanghai.

Scheduled GitHub Actions can be delayed. A delayed run still audits the last complete hour; duplicate starts cannot create duplicate checks or notifications.

## Required GitHub configuration

Secrets:

- `MONITOR_DATABASE_URL`: restricted PostgreSQL role; SELECT on required business tables and write access only to `callie_monitor`.
- `DINGTALK_WEBHOOK_URL`: required only when active notification is approved.
- `DINGTALK_SECRET`: optional DingTalk signing secret.

Repository variables:

- `MONITOR_MODE`: keep `dry_run` during observation; use `active` only after approval.
- `MONITOR_WORKER_ID`
- `MONITOR_WORKER_INSTANCE_ID`
- `MONITOR_WORKER_CABINET_ID`
- `MONITOR_INTEGRATION_IDS`: exactly 18 unique comma-separated channel IDs.

The Windows host runs two small one-shot exporters once per hour. They do not decide health or send DingTalk messages: the workbench exporter writes only an aggregate receipt count, and the order exporter reads the existing A7 state files and writes only runtime status. Their restricted monitor database credential is configured locally and is not committed.

On hosts where Task Scheduler registration is restricted, `scripts/run-callie-monitor-daemon.ps1` is the independent login-started scheduler. It invokes the workbench exporter after minute 02 and the order exporter after minute 04, retries failures after five minutes, and writes only a local heartbeat/status file. `scripts/run-callie-monitor-reporter.ps1` is the one-shot wrapper used by the daemon. Neither script changes business tables or customer state.

## Database migration

The migration is explicit and never runs during import or monitor startup:

```powershell
$env:DATABASE_URL = '<restricted migration connection supplied out of band>'
python scripts/migrate_monitoring.py --apply
```

Always inspect and back up the target before applying. The migration only creates the `callie_monitor` schema and its five monitoring tables.
