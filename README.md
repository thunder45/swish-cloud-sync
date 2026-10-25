# swish-cloud-sync

Copies a GoPro Cloud library to Google Photos once, with no duplicates and with the real
capture date, time and place, then removes the older reduced copies (GoPro files the Quik
app saved on the phone, which the phone backed up to Google Photos).

Everything runs locally on a Mac. See **[photos_migration/README.md](photos_migration/README.md)**
for setup, commands, where data and secrets live, and how capture times are decided.

```
python3.12 -m venv .venv && .venv/bin/pip install -r photos_migration/requirements.txt
.venv/bin/python -m photos_migration status
```

## History: the AWS design is retired

The project started as an AWS CDK app (EventBridge → Step Functions → Lambda → S3 Glacier
Deep Archive). That stack and its buckets were **deleted on 2026-10-20** and must **not** be
redeployed: it ran in a work AWS account. Its code is only in git history.
