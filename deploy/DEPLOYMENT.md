# FMM Assistant deployment runbook

Target platform: Linux server with PostgreSQL 16 + pgvector, Python 3.11 and
systemd. The service is database-first: PostgreSQL holds documents, pages,
chunks, knowledge, chat history, feedback and audit records.

## 1. Prerequisites

- PostgreSQL 16 with the `vector` extension available.
- Python 3.11 (`python3.11 -m venv venv`).
- A service account (`fmm`) and a deployment root (`/opt/fmm-assistant`).
- Disk space for source documents, rendered pages and the BGE-M3 ONNX model.

## 2. Install

```bash
sudo useradd --system --home /opt/fmm-assistant --shell /usr/sbin/nologin fmm
sudo -u fmm python3.11 -m venv /opt/fmm-assistant/venv
sudo -u fmm /opt/fmm-assistant/venv/bin/pip install -r /opt/fmm-assistant/requirements.txt
sudo -u fmm /opt/fmm-assistant/venv/bin/pip install -r /opt/fmm-assistant/requirements-ml.txt
sudo -u fmm /opt/fmm-assistant/venv/bin/pip install -r /opt/fmm-assistant/requirements-dev.txt
```

Model weights are downloaded once and never at runtime:

```bash
sudo -u fmm HF_HUB_OFFLINE=0 /opt/fmm-assistant/venv/bin/python scripts/download_models.py --backend onnx
```

## 3. Configuration

```bash
sudo install -d -m 0750 -o fmm -g fmm /etc/fmm-assistant
sudo -u fmm cp config/.env.example /etc/fmm-assistant/fmm.env
sudo chmod 0640 /etc/fmm-assistant/fmm.env
sudo chown fmm:fmm /etc/fmm-assistant/fmm.env
sudo -u fmm vim /etc/fmm-assistant/fmm.env
```

Set at least:

- `DATABASE_URL` - production PostgreSQL 16 with pgvector.
- `JWT_SECRET` - generated with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
- Exactly one vision provider credential (`GEMINI_API_KEY` + `GEMINI_VISION_MODEL`,
  or the Qwen/Databricks values) and one chat provider (`CHAT_PROVIDER`).
- `HF_HUB_OFFLINE=1` so no model is downloaded at runtime.
- `API_BASE_URL` is not needed on the server; the UI unit sets it to
  `http://127.0.0.1:8000`.

Secrets live only in this file. They are never stored in PostgreSQL, returned by
the API or written to logs.

## 4. Database

```bash
sudo -u fmm /opt/fmm-assistant/venv/bin/python -m app.db.migrate
sudo -u fmm /opt/fmm-assistant/venv/bin/python -m app.db.migrate --status
sudo -u fmm /opt/fmm-assistant/venv/bin/python scripts/create_admin.py --username admin
```

Migrations are plain SQL in `app/db/migrations`, tracked in `schema_migrations`
with SHA-256 checksums. Never edit an applied migration; add a new file.

## 5. Services

```bash
sudo cp deploy/systemd/fmm-api.service /etc/systemd/system/
sudo cp deploy/systemd/fmm-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fmm-api.service fmm-ui.service
sudo systemctl status fmm-api.service fmm-ui.service
journalctl -u fmm-api.service -n 100 --no-pager
```

Both units bind to `127.0.0.1`. Put nginx (or an internal reverse proxy) in
front of them for user access, and terminate TLS there.

Microphone dictation in the Chat page uses the browser recording API, which
browsers only expose in a secure context. Serve the UI over HTTPS, or use it
from `localhost`; on a plain-HTTP remote origin the browser blocks the
microphone and the question box stays type-only.

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 300s;
    proxy_buffering off;   # required for /chat/stream
}
location / {
    proxy_pass http://127.0.0.1:8501/;
    proxy_set_header Host $host;
}
```

## 6. Content operations

```bash
sudo -u fmm /opt/fmm-assistant/venv/bin/python scripts/ingest_document.py \
    --file data/documents/guide.pdf --approve
sudo -u fmm /opt/fmm-assistant/venv/bin/python scripts/build_knowledge.py --approve
```

Markdown and DOCX sources are ingested through the same pipeline; vision
analysis only runs for PDF pages.

## 7. Backup and restore

```bash
sudo -u fmm DATABASE_URL="$DATABASE_URL" BACKUP_DIR=/var/backups/fmm \
    /opt/fmm-assistant/scripts/backup_database.sh
sudo -u fmm DATABASE_URL="$DATABASE_URL" \
    /opt/fmm-assistant/scripts/restore_database.sh /var/backups/fmm/fmm_<stamp>.dump
```

Daily cron example (`/etc/cron.d/fmm-backup`):

```
15 2 * * * fmm DATABASE_URL=postgresql://... BACKUP_DIR=/var/backups/fmm /opt/fmm-assistant/scripts/backup_database.sh >> /var/log/fmm-backup.log 2>&1
```

Back up `data/` (source documents and rendered pages) alongside the database.
Verify a restore quarterly in a staging database and record the result.

## 8. Health and monitoring

```bash
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
```

As an administrator, `GET /admin/operations/health` returns counters for
documents, ready versions, running and failed jobs, missing embeddings, open
conflicts, open questions and request errors in the last hour.

`GET /chat/voice/capability` reports whether microphone dictation is usable.
The first transcription downloads the faster-whisper model, so the request
takes longer; the model is cached afterwards. The Chat page shows a warning
and disables the microphone when the capability is unavailable.

Alert on:

- `/health/ready` failing for more than two minutes.
- `failed_jobs` greater than zero for more than fifteen minutes.
- `missing_embeddings` greater than zero.
- `errors_last_hour` above the agreed threshold.

## 9. Upgrade

```bash
sudo -u fmm /opt/fmm-assistant/scripts/backup_database.sh
sudo systemctl stop fmm-ui.service fmm-api.service
sudo -u fmm /opt/fmm-assistant/venv/bin/pip install -r /opt/fmm-assistant/requirements.txt
sudo -u fmm /opt/fmm-assistant/venv/bin/python -m app.db.migrate
sudo systemctl start fmm-api.service fmm-ui.service
curl -fsS http://127.0.0.1:8000/health/ready
```

## 10. Rollback

Stop the services, restore the database dump taken before the upgrade, and
deploy the previous code revision. Migrations are forward-only; restoring the
pre-upgrade dump is the supported rollback path.
