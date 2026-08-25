# capman2 multi-user storage stack

Turns capman2's per-machine local SQLite capture into a **central, multi-user,
access-controlled** store using the reference architecture:

| Layer | Service | Purpose |
|-------|---------|---------|
| Access-controlled core | Postgres 17 | timeline, sessions, analyses, triples, playbooks — **Row-Level Security** isolates each user |
| Binary blobs | MinIO (S3) | screenshots, OCR text, full document content — bucket-per-user, IAM-policy-gated |
| Hot/ephemeral | Redis 7 | real-time session buffers, pub/sub, LLM-result cache |

> **Operational warning** — mirrors `docs/STORAGE_WORKLOAD.md`: the live capture
> path is write-heavy and must **not** be placed on SQLite-over-NFS/SMB (silent
> WAL corruption). This stack replaces `timeline.db` for multi-user. Keep raw
> on-disk capture local if you want a forensic copy; this is the shared store.

## Prerequisites

- Docker + Docker Compose v2
- `psql` (Postgres client) and `mc` (MinIO client) on the machine you run `bootstrap.sh` from
- Windows note: `bootstrap.sh` requires bash, `openssl`, `sha1sum` (Git Bash /
  WSL provides these). The `docker-compose.yml` itself is fully Windows-compatible.

## Start

```bash
cd deploy
cp .env.example .env      # set real passwords
docker compose up -d
docker compose ps         # wait for healthy
```

## Bootstrap a user

```bash
./bootstrap.sh add alice@example.com
```

This creates, end-to-end:

- **Postgres**: a `LOGIN` role `alice@example.com`, member of `capman_app`,
  granted read/write on the tenant tables. Credentials + the derived
  `capman.user_id` are appended to `.provisioned-users.tsv`.
- **MinIO:** IAM user `alice@example.com`, bucket `capman-alice@example.com`,
  and a policy scoped to that bucket only (Get/Put/List). Secrets in
  `.provisioned-minio.tsv`.

Connect as `alice@example.com`? Because Row-Level Security is `FORCE`d, the
**capman application must set the tenant key on every connection**:

```sql
SET capman.user_id = '<the user_id from .provisioned-users.tsv>';
```

Without it, the tenant tables return zero rows — no policy matches a NULL
session variable. The `capman` gateway/daemon sets this immediately after
connecting (see the SQL in `postgres/init/001_capman_multi_user.sql`).

## Access-control model

`001_capman_multi_user.sql` is the security boundary. Every tenant table
(`sessions`, `events`, `session_analyses`, `knowledge_triples`, `screenshots`,
`playbooks`, `knowledge_gaps`) is `FORCE ROW LEVEL SECURITY` with a
`tenant_isolation` policy comparing `user_id` to `current_setting('capman.user_id')`.

- Roles are `NOLOGIN` grouping roles; each human/user is a `LOGIN` role that is a member of `capman_app`. `capman_app` holds only the DML it needs — no DDL, no superuser.
- MinIO mirrors the boundary at the object layer: a user can only reach their own bucket prefix.

## Network & TLS

- All three services sit on a private `capman` bridge network.
- `ports:` are for the gateway container to reach them — do **not** publish the
  Postgres port to the public internet. Terminate remote access through the
  gateway (TLS), or SSH-tunnel, or run the gateway in the same network.
- For production, front MinIO with TLS and use a reverse proxy for Redis auth.

## Optional: StarRocks analytics add-on

Postgres is the access-controlled source of truth. If you later need heavy
cross-user analytics / real-time dashboards / large-scale hybrid (keyword +
vector) search, feed **StarRocks** from Postgres (external catalog or Stream
Load). Do **not** use StarRocks as the live capture or access-control store —
its row-isolation model is weaker than Postgres RLS and it targets analytical
queries, not per-user CRUD. Add it only when `capman2` reaches team/org query
scale.

## Files

```
deploy/
├── docker-compose.yml
├── .env.example
├── bootstrap.sh
├── postgres/init/001_capman_multi_user.sql
└── README.md
```