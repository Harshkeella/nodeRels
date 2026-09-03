# Deployment

## The architecture, and why it is smaller than you expect

```
                        Users
                          |
                       HTTPS
                          |
              +-----------+-----------+
              |                       |
       Next.js (Vercel)        Supabase Auth
       login / dashboard       email+password, Google OAuth
              |                       |
              |  Bearer <access_token>|
              +-----------+-----------+
                          |
                 FastAPI  (one container, persistent disk)
                          |
             get_current_user -> workspace = user id
                          |
        +-----------------+------------------+
        |         |          |        |      |
     Qdrant   NetworkX   DuckDB   SQLite  scraped
   (embedded  (per-ws     (per-   (per-    cache
    or Cloud)  graphml)   user)    user)
                          |
                    LLM gateway
             Groq -> OpenRouter -> Ollama
        global TPM bucket + per-user RPM/TPD
```

**What this deliberately does not have, and why.** The master plan called for
PostgreSQL, object storage, Redis and Celery workers. For 10–50 users they were
dropped:

* You need a box with a persistent disk regardless. MiniLM (embeddings), the
  cross-encoder (reranking) and GLiNER (extraction) are ~500 MB of local torch
  models loaded into the process. That rules out every serverless runtime, so
  a long-lived container with a volume is a *given*, not a choice.
* Once that box exists, PostgreSQL replaces per-user SQLite files that are
  already isolated and already on the same durable volume — buying horizontal
  scale you do not have, at the price of a fourth managed service.
* Object storage would hold original uploads, but ingestion keeps only
  *extracted text*; the manifest already records size and hash. Re-uploading
  is the recovery path either way (see `docs/SECURITY.md`).
* Redis + Celery buy cross-process job durability. One process, one volume, and
  jobs recorded in the user's own database get the same durability with one
  moving part.

Each of those becomes right at a different scale. The migration is a swap of
the four storage openers listed in `docs/MULTI_TENANCY.md`, not a rewrite,
because nothing above them knows which store it is talking to.

## Providers

| Piece | Choice | Cost at 10–50 users |
|---|---|---|
| Frontend | Vercel Hobby | $0 |
| Auth | Supabase free tier | $0 |
| Backend + worker | Fly.io / Render, 2 GB RAM + volume | ~$10–20/mo |
| Vectors | Embedded Qdrant on that volume | $0 |
| LLM | Groq free -> OpenRouter free -> Ollama | $0 |

Roughly **$10–20/month**, and the volume is the only thing that must not be
ephemeral. Set `QDRANT_URL` to move vectors to Qdrant Cloud without another
code change when the volume gets tight.

> The disk is the product. A platform whose filesystem resets on deploy will
> silently destroy every user's graph. Verify the volume before the first user.

## Setting it up

### 1. Supabase (auth only — no application data lives there)

1. Create a project.
2. Authentication -> Providers -> **Email**: on. Turn "Confirm email" on.
3. Authentication -> Providers -> **Google**: on. In Google Cloud Console
   create an OAuth 2.0 Web Client, set the authorised redirect URI to
   `https://<project-ref>.supabase.co/auth/v1/callback`, and paste the client
   ID and secret into Supabase. **The secret stays there** — it never enters
   this repository, the frontend bundle, or the extension.
4. Authentication -> URL Configuration -> Redirect URLs: add
   `https://<your-app>/auth/callback` and `https://<your-app>/reset-password`.
5. Copy **Project URL** and **anon key** (public, for the frontend) and the
   **JWT Secret** (secret, for the backend).

### 2. Backend

```bash
cp backend/.env.example backend/.env      # then fill it in
AUTH_DISABLED=false
SUPABASE_JWT_SECRET=<the JWT secret>
CORS_ORIGINS=https://<your-app>
STORAGE_DIR=/data                          # the mounted volume
```

```bash
docker build -t noderels-api -f backend/Dockerfile .
docker run -p 8000:8000 --env-file backend/.env \
  -v noderels-data:/data -v noderels-models:/models noderels-api
```

`HF_HOME=/models` keeps the model downloads on a volume instead of in the
image: baking ~500 MB of weights into every build is slow to push and slower
to roll back.

### 3. Frontend

```bash
NEXT_PUBLIC_API_BASE_URL=https://<your-api>
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key>
```

`npm run build` && deploy. Both values are public by design.

### 4. Extension

Settings (⚙) -> backend URL, Supabase project URL, anon key -> Save. The
browser asks once for access to those origins. Sign in with email and password.

## Verifying a deployment

```bash
curl https://<your-api>/health
# {"status":"ok","checks":{"qdrant":"ok","storage":"ok","auth":"ok"}}

curl -i https://<your-api>/api/v1/knowledge-base
# 401 -- every application endpoint is authenticated

curl -i -H "Authorization: Bearer $TOKEN" https://<your-api>/api/v1/me/usage
# {"used_bytes":0,"quota_bytes":5368709120,...}
```

`auth: "error: no JWT secret"` means the backend cannot verify anything and
will reject every request — fix it before pointing users at it.

## Rollback

Nothing in this change is destructive, which is what makes the rollback one
line:

```bash
AUTH_DISABLED=true    # and restart
```

The local user's workspace is `""` and its storage paths are the pre-existing
flat ones, so the app returns to exactly the single-user install it was.
Per-user data under `storage/users/` is untouched and comes back when auth is
turned on again. To roll back the code as well, `git revert` — no schema
migration ran, so no schema has to be undone.

## Backups

Back up the **volume**. What is on it, by how much it costs to lose:

| Data | Rebuildable? |
|---|---|
| `users/*/manifest.sqlite3` | **No.** The inventory of what exists. |
| `users/*/chat.sqlite3` | **No.** Conversations and their evidence chains. |
| `users/*/spreadsheets.duckdb` | **No.** The rows themselves. |
| `kb/<workspace>/*.graphml` | Only by re-ingesting: hours of extraction. |
| `qdrant/` | Yes, by re-ingesting — but only if the source text still exists. |
| `scraped_articles/` | Yes, by re-fetching. |

`sqlite3 file ".backup out"` and a volume snapshot are enough. Restore order:
volume -> start the API -> `POST /api/v1/knowledge-base/reprocess` for anything
left mid-pipeline.

Note the gap honestly: **original uploaded bytes are not kept.** Ingestion
stores extracted text, so a restored knowledge base is searchable but a user
cannot re-download the PDF they uploaded. Add object storage if that matters.
