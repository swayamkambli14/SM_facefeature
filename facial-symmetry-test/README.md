# facial-symmetry-cv-engine

FastAPI Python backend for facial symmetry analysis using MediaPipe.
Built for Flutter + Supabase mobile app integration.

## Tech Stack

- Python 3.10
- FastAPI + uvicorn
- MediaPipe FaceMesh
- OpenCV (headless)
- Supabase (PostgreSQL + Storage + Auth)
- Docker
- Railway (deployment)

## API Endpoints

### GET /health

- Returns: `{"status": "ok"}`
- Auth: none

### POST /analyze

- Auth: Bearer token (Supabase JWT)
- Form fields:
  - `mode`: `"baseline"` or `"analyze"`
  - `image`: jpeg file upload
  - `fingerprint_data`: (analyze mode only) JSON string from previous baseline response
- Returns: JSON with scores, verdict, deviations, and storage paths

## Environment Variables

- `SUPABASE_URL` — your Supabase project URL
- `SUPABASE_SERVICE_KEY` — service role key (from Supabase → Settings → API)
- `SUPABASE_JWT_SECRET` — JWT secret (from Supabase → Settings → API)

## Supabase Setup

### 1. Storage bucket

- Go to Supabase dashboard → Storage → New bucket
- Name: `face-images`
- Public: NO (private)
- Add this RLS policy:

```sql
create policy "users manage own images"
on storage.objects for all
using (auth.uid()::text = (storage.foldername(name))[1]);
```

Storage structure:

```
face-images/
└── {user_id}/
    ├── baseline.jpg          ← always overwritten on new baseline
    └── scans/
        ├── 1710000000.jpg
        ├── 1710000100.jpg
        └── ...
```

### 2. Database tables

```sql
create table baselines (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users on delete cascade,
  fingerprint_json jsonb not null,
  image_path text,
  created_at timestamptz default now(),
  unique(user_id)
);

create table scans (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users on delete cascade,
  verdict text,
  zones jsonb,
  aggregate_deviation float,
  image_path text,
  created_at timestamptz default now()
);

alter table baselines enable row level security;
alter table scans enable row level security;

create policy "users see own baselines" on baselines
  for all using (auth.uid() = user_id);

create policy "users see own scans" on scans
  for all using (auth.uid() = user_id);
```

## Deploy to Railway

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
3. Select the `cv_engine/` subdirectory as the root
4. Add environment variables:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `SUPABASE_JWT_SECRET`
5. Railway detects Dockerfile and builds automatically
6. Copy the generated Railway URL — this is your Flutter app's API base URL
