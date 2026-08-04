#!/bin/sh
set -eu

INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-21600}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
DEST=/backups

mkdir -p "$DEST"

while true; do
  ts=$(date +%Y%m%d_%H%M%S)
  file="$DEST/tegrakobot_${ts}.sql.gz"
  if PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h db -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$file.tmp"; then
    mv "$file.tmp" "$file"
    echo "$(date -Iseconds) backup ok: $file"
  else
    rm -f "$file.tmp"
    echo "$(date -Iseconds) backup FAILED" >&2
  fi
  find "$DEST" -name 'tegrakobot_*.sql.gz' -mtime "+${RETENTION_DAYS}" -delete
  sleep "$INTERVAL_SECONDS"
done
