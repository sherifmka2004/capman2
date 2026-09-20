#!/usr/bin/env bash
# Provision a capman2 user end-to-end across the stack:
#   Postgres: LOGIN role (member of capman_app) granted the tenant tables.
#   MinIO:    IAM user + per-user bucket + policy scoped to that bucket.
# Product tenants (web behavior) are Postgres-only: user_id = product:<key>.
# Run from deploy/ after `docker compose up -d`.
#
# Usage:  ./bootstrap.sh add <email>
#         ./bootstrap.sh add-product <key>     # key: [a-z0-9_]+
#         ./bootstrap.sh list
set -euo pipefail

cd "$(dirname "$0")"
[ -f .env ] || { echo ".env missing — cp .env.example .env" >&2; exit 2; }
set -a; source .env; set +a

PGHOST="${POSTGRES_HOST:-localhost}"
PGPORT="${POSTGRES_PORT:-5432}"
PGDB="${POSTGRES_DB:-capman}"

MHOST="${MINIO_HOST:-localhost}"
MPORT="${MINIO_API_PORT:-9000}"

pg() {
  PGPASSWORD="${POSTGRES_ADMIN_PASSWORD}" psql \
    -h "$PGHOST" -p "$PGPORT" -U "${POSTGRES_ADMIN_USER:-capman_admin}" -d "$PGDB" \
    -v ON_ERROR_STOP=1 "$@"
}

hash_id() { printf '%s' "$1" | sha1sum | cut -c1-12; }

gen_secret() { openssl rand -hex 16; }

provision_pg() {
  local user="$1" id="$2" pass
  pass="$(gen_secret)"
  pg >/dev/null <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${user}') THEN
    CREATE ROLE ${user} WITH LOGIN PASSWORD '${pass}';
  END IF;
END \$\$;
GRANT capman_app TO ${user};
SQL
  printf '%s\t%s\t%s\n' "$user" "$pass" "$id" >> .provisioned-users.tsv
  echo "  postgres: role=${user} user_id=${id}"
}

provision_minio() {
  local user="$1"
  local bucket="capman-${user}"
  local secret
  secret="$(gen_secret)"
  mc config host add capman "http://${MHOST}:${MPORT}" "${MINIO_ROOT_USER:-capman_root}" "${MINIO_ROOT_PASSWORD}" >/dev/null
  mc mb "capman/${bucket}" >/dev/null 2>&1 || true
  mc admin user add "capman" "$user" "$secret" || true
  mc admin policy create "capman" "capman-${user}" /dev/stdin <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject","s3:PutObject","s3:ListBucket"],
      "Resource": ["arn:aws:s3:::${bucket}/*","arn:aws:s3:::${bucket}"]
    }
  ]
}
JSON
  mc admin policy attach "capman" "capman-${user}" --user "$user"
  printf '%s\t%s\t%s\n' "$user" "$secret" "$bucket" >> .provisioned-minio.tsv
  echo "  minio:  bucket=${bucket} user=${user}"
}

cmd_add() {
  local user id
  user="${1:?usage: ./bootstrap.sh add <email>}"
  id="$(hash_id "$user")"
  provision_pg "$user" "$id"
  provision_minio "$user"
  echo "provisioned ${user}  capman.user_id=${id}"
}

cmd_add_product() {
  local key role id
  key="${1:?usage: ./bootstrap.sh add-product <key>   # key: [a-z0-9_]+}"
  if ! printf '%s' "$key" | grep -Eq '^[a-z0-9_]+$'; then
    echo "invalid product key '${key}' — must match ^[a-z0-9_]+$" >&2
    exit 2
  fi
  # Postgres role names cannot contain ':', so the role is cw_<key>
  # while the RLS tenant id stays the literal product:<key>.
  role="cw_${key}"
  id="product:${key}"
  provision_pg "$role" "$id"
  echo "  minio:  skipped — product tenants store no blobs"
  echo "provisioned product ${key}  capman.user_id=${id}"
}

cmd_list() {
  if [ -f .provisioned-users.tsv ]; then
    echo "=== postgres ==="
    column -t -s$'\t' .provisioned-users.tsv
  fi
  if [ -f .provisioned-minio.tsv ]; then
    echo "=== minio ==="
    column -t -s$'\t' .provisioned-minio.tsv
  fi
}

case "${1:-}" in
  add)         cmd_add "${2:-}" ;;
  add-product) cmd_add_product "${2:-}" ;;
  list)        cmd_list ;;
  *)           echo "usage: $0 add <email> | add-product <key> | list" >&2; exit 2 ;;
esac