# SmartClip AI

SmartClip AI is a FastAPI video-processing app that supports:

- video upload
- supported video URLs through yt-dlp
- FFmpeg compression
- Whisper transcription
- transcript-based summaries
- ranked highlights
- generated MP4 clips
- MySQL persistence
- cookie-based authentication

## 1. Local MySQL / SQLyog

Create the database and tables by running `database.sql` in SQLyog.

Then copy `.env.example` to `.env` and set the **actual MySQL root password used by SQLyog**:

```env
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=YOUR_REAL_SQLYOG_PASSWORD
DB_NAME=smartclip
```

Do not assume the password is `root` unless SQLyog successfully connects with that password.

Start locally:

```powershell
python -m uvicorn main:app --reload
```

Health checks:

- `/health`
- `/health/db`

## 2. GitHub

Do not commit `.env`. It is intentionally ignored.

```powershell
git add .
git commit -m "Prepare SmartClip AI for Railway"
git push origin main
```

## 3. Railway

Create a Railway project and add:

1. a GitHub service connected to this repository
2. a MySQL service in the same Railway project

Railway's MySQL service provides:

- `MYSQLHOST`
- `MYSQLPORT`
- `MYSQLUSER`
- `MYSQLPASSWORD`
- `MYSQLDATABASE`
- `MYSQL_URL`

The app automatically prefers these Railway variables over local `DB_*` variables.

Add only this application secret to the SmartClip service:

```env
SMARTCLIP_AUTH_SECRET=<long-random-secret>
WHISPER_MODEL=base
COOKIE_SECURE=true
```

Do not add `DB_HOST=localhost` to Railway.

Railway detects the Dockerfile and the container uses the Railway `PORT` variable automatically.

After deployment, open:

```text
https://YOUR-RAILWAY-DOMAIN/health
https://YOUR-RAILWAY-DOMAIN/health/db
```

`/health/db` should report `"status": "ok"`.

## Important

Railway service storage is ephemeral unless persistent storage is configured. Generated videos and clips stored in `uploads/` can disappear after redeploy/restart. For production, move video assets to object storage before treating the service as a permanent media store.
