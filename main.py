import os,re,json,uuid,shutil,hashlib,secrets,subprocess    
from pathlib import Path
from datetime import datetime,timedelta,timezone    
from urllib.parse import urlparse
from typing import Optional
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Form,
    HTTPException,
    Depends,
    Cookie,
    BackgroundTasks,
)
from fastapi.responses import HTMLResponse,FileResponse,RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Load local .env when developing on Windows.
# Railway injects its own environment variables at runtime.
load_dotenv(override=False)

os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMEXPR_NUM_THREADS', '1')
try: import whisper
except ImportError: whisper=None
try: import yt_dlp
except ImportError: yt_dlp=None

BASE_DIR=Path(__file__).resolve().parent
UPLOAD_DIR=BASE_DIR/'uploads'; CLIPS_DIR=UPLOAD_DIR/'clips'
UPLOAD_DIR.mkdir(parents=True,exist_ok=True); CLIPS_DIR.mkdir(parents=True,exist_ok=True)

def env(*names,default=None):
    for n in names:
        v=os.getenv(n)
        if v and str(v).strip(): return str(v).strip()
    return default

# Railway MySQL exposes MYSQLHOST/MYSQLPORT/MYSQLUSER/MYSQLPASSWORD/
# MYSQLDATABASE to connected services. Prefer those over local DB_* values.
# This prevents a local .env from accidentally forcing Railway to localhost.
def first_env(*names, default=None):
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip() != '':
            return str(value).strip()
    return default

DB_HOST = first_env('MYSQLHOST', 'DB_HOST')
DB_PORT = first_env('MYSQLPORT', 'DB_PORT', default='3306')
DB_USER = first_env('MYSQLUSER', 'DB_USER')
DB_PASSWORD = first_env('MYSQLPASSWORD', 'DB_PASSWORD')
DB_NAME = first_env('MYSQLDATABASE', 'DB_NAME')

MYSQL_HOST = DB_HOST
MYSQL_PORT = int(DB_PORT or 3306)
MYSQL_USER = DB_USER
MYSQL_PASSWORD = DB_PASSWORD
MYSQL_DATABASE = DB_NAME

# Local HTTP should use a non-secure cookie; Railway HTTPS should use Secure.
COOKIE_SECURE = (
    os.getenv('COOKIE_SECURE', '').strip().lower() == 'true'
    or bool(os.getenv('RAILWAY_PUBLIC_DOMAIN'))
)

AUTH_SECRET=env('SMARTCLIP_AUTH_SECRET',default='CHANGE-ME')
WHISPER_MODEL=env('WHISPER_MODEL',default='tiny')
ALLOWED={'.mp4','.mov','.avi','.mkv','.webm'}
whisper_model=None

app=FastAPI(title='SmartClip AI',version='3.0.0')
# ============================================================
# BACKGROUND PROCESSING JOBS
# ============================================================
jobs = {}
app.mount('/static',StaticFiles(directory=str(BASE_DIR/'static')),name='static')

class RegisterRequest(BaseModel): username:str; email:str; password:str
class LoginRequest(BaseModel): email:str; password:str
class URLRequest(BaseModel): url:str

def clean(v): return None if v is None else ''.join(c for c in str(v) if ord(c)<=0xFFFF)
def password_hash(p):
    s=secrets.token_bytes(16); d=hashlib.pbkdf2_hmac('sha256',p.encode(),s,200000); return s.hex()+'$'+d.hex()
def password_ok(p,stored):
    try:
        sh,dh=stored.split('$',1); s=bytes.fromhex(sh); d=hashlib.pbkdf2_hmac('sha256',p.encode(),s,200000); return secrets.compare_digest(d.hex(),dh)
    except Exception:return False

def token(uid):
    exp=int((datetime.now(timezone.utc)+timedelta(days=7)).timestamp()); payload=f'{uid}.{exp}'
    sig=hashlib.sha256((payload+AUTH_SECRET).encode()).hexdigest(); return f'{payload}.{sig}'
def verify(t):
    try:
        uid,exp,sig=t.split('.',2); payload=f'{uid}.{exp}'
        good=hashlib.sha256((payload+AUTH_SECRET).encode()).hexdigest()
        if not secrets.compare_digest(sig,good) or int(exp)<int(datetime.now(timezone.utc).timestamp()): return None
        return int(uid)
    except Exception:return None

def current_user(smartclip_token:str|None=Cookie(default=None)):
    if not smartclip_token: raise HTTPException(401,'Please login first.')
    uid=verify(smartclip_token)
    if uid is None: raise HTTPException(401,'Login expired. Please login again.')
    return uid

def db():
    if not DB_HOST or not DB_USER or not DB_NAME:
        raise HTTPException(
            status_code=500,
            detail=(
                'MySQL is not configured. Set MYSQLHOST, MYSQLPORT, '
                'MYSQLUSER, MYSQLPASSWORD and MYSQLDATABASE on Railway, '
                'or DB_HOST, DB_PORT, DB_USER, DB_PASSWORD and DB_NAME locally.'
            )
        )

    try:
        return mysql.connector.connect(
            host=DB_HOST,
            port=int(DB_PORT or 3306),
            user=DB_USER,
            password=DB_PASSWORD or '',
            database=DB_NAME,
            charset='utf8mb4',
            use_unicode=True,
            connection_timeout=10,
        )
    except Error as e:
        raise HTTPException(
            status_code=500,
            detail=f'MySQL connection failed: {e}'
        )
def cmd(c):
    try:
        r = subprocess.run(
            c,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
        )
    except FileNotFoundError:
        raise RuntimeError(
            'FFmpeg/FFprobe is not installed or not in PATH.'
        )

    if r.returncode != 0:
        command_text = ' '.join(str(x) for x in c)
        error_text = (r.stderr.strip() or r.stdout.strip() or 'Command failed')
        raise RuntimeError(
            f'FFmpeg command failed.\nCommand: {command_text}\nError: {error_text}'
        )

    return r
def video_path(name):
    p=UPLOAD_DIR/os.path.basename(name)
    if not p.exists() or not p.is_file(): raise HTTPException(404,'Video file not found.')
    return p
def compression_level(x):
    x=(x or 'medium').lower().strip()
    if x not in {'original','low','medium','high'}: raise HTTPException(400,'Invalid compression level.')
    return x
def compress(p,level):
    level=compression_level(level)
    if level=='original': return p
    out=UPLOAD_DIR/f'{p.stem}_{level}.mp4'
    cmd = [
        'ffmpeg',
        '-y',
        '-i', str(p),
        '-vf', 'scale=1280:-2',
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-crf', '28',
        '-threads', '2',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-movflags', '+faststart',
        str(out),
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            'FFmpeg compression timed out after 10 minutes.'
        )
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or '').strip()[-4000:]
        raise RuntimeError(
            'FFmpeg compression failed:\n' + detail
        )
    if not out.exists(): raise RuntimeError('Compressed file was not created')
    return out

def init_db():
    c=d=dbconn=None
    try:
        dbconn=db(); c=dbconn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS users(user_id INT AUTO_INCREMENT PRIMARY KEY,username VARCHAR(100) NOT NULL,email VARCHAR(255) NOT NULL UNIQUE,password_hash VARCHAR(500) NOT NULL,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP) DEFAULT CHARACTER SET utf8")
        c.execute("CREATE TABLE IF NOT EXISTS videos(video_id INT AUTO_INCREMENT PRIMARY KEY,user_id INT,filename VARCHAR(255) NOT NULL,original_url VARCHAR(1000),title VARCHAR(500),language VARCHAR(50),width INT,height INT,fps DOUBLE,duration_seconds DOUBLE,prompt MEDIUMTEXT,compression_level VARCHAR(30),link_type VARCHAR(50),created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP) DEFAULT CHARACTER SET utf8")
        c.execute("CREATE TABLE IF NOT EXISTS transcripts(transcript_id INT AUTO_INCREMENT PRIMARY KEY,video_id INT NOT NULL,transcript MEDIUMTEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS transcript_segments(segment_id INT AUTO_INCREMENT PRIMARY KEY,video_id INT NOT NULL,text MEDIUMTEXT,start_time DOUBLE,end_time DOUBLE,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS summaries(summary_id INT AUTO_INCREMENT PRIMARY KEY,video_id INT NOT NULL,summary MEDIUMTEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS highlights(highlight_id INT AUTO_INCREMENT PRIMARY KEY,video_id INT NOT NULL,text MEDIUMTEXT,start_time DOUBLE,end_time DOUBLE,score INT,reason MEDIUMTEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        dbconn.commit()
    except Exception as e: print('DB init failed:',e)
    finally:
        if c:c.close()
        if dbconn:dbconn.close()
@app.on_event('startup')
def startup(): init_db()

@app.get('/',response_class=HTMLResponse)
def root(): return RedirectResponse('/input')
@app.get('/input',response_class=HTMLResponse)
def input_page(): return HTMLResponse((BASE_DIR/'templates/input.html').read_text(encoding='utf8'))
@app.get('/login',response_class=HTMLResponse)
def login_page(): return HTMLResponse((BASE_DIR/'templates/htmllogin.html').read_text(encoding='utf8'))
@app.get('/register',response_class=HTMLResponse)
def reg_page(): return HTMLResponse((BASE_DIR/'templates/htmlreg.html').read_text(encoding='utf8'))

@app.post('/auth/register')
def register(x:RegisterRequest):
    u=x.username.strip(); e=x.email.strip().lower()
    if len(u)<2: raise HTTPException(400,'Username is too short.')
    if len(x.password)<6: raise HTTPException(400,'Password must contain at least 6 characters.')
    d=db(); c=d.cursor()
    try:
        c.execute('SELECT user_id FROM users WHERE email=%s',(e,))
        if c.fetchone(): raise HTTPException(409,'Email already registered.')
        c.execute('INSERT INTO users(username,email,password_hash) VALUES(%s,%s,%s)',(clean(u),clean(e),password_hash(x.password))); uid=c.lastrowid; d.commit()
        return {'message':'Registration successful.','user_id':uid,'username':u,'email':e}
    except HTTPException: raise
    except Error as er: d.rollback(); raise HTTPException(500,f'Registration failed: {er}')
    finally:c.close();d.close()

@app.post('/auth/login')
def login(x:LoginRequest):
    d=db(); c=d.cursor(dictionary=True)
    try:
        c.execute('SELECT user_id,username,email,password_hash FROM users WHERE email=%s LIMIT 1',(x.email.strip().lower(),)); u=c.fetchone()
        if not u or not password_ok(x.password,u['password_hash']): raise HTTPException(401,'Invalid email or password.')
        response=HTMLResponse(json.dumps({'message':'Login successful.','user':{'user_id':u['user_id'],'username':u['username'],'email':u['email']}}),media_type='application/json')
        response.set_cookie('smartclip_token',token(u['user_id']),httponly=True,samesite='lax',secure=COOKIE_SECURE,max_age=604800,path='/')
        return response
    finally:c.close();d.close()
@app.post('/auth/logout')
def logout():
    r=RedirectResponse('/login',status_code=303); r.delete_cookie('smartclip_token',path='/'); return r
@app.get('/auth/me')
def me(uid:int=Depends(current_user)):
    d=db();c=d.cursor(dictionary=True)
    try:c.execute('SELECT user_id,username,email,created_at FROM users WHERE user_id=%s',(uid,));u=c.fetchone();
    finally:c.close();d.close()
    if not u: raise HTTPException(404,'User not found.')
    return u

@app.post('/video/url/validate')
def validate(x:URLRequest):
    url=x.url.strip()
    if not re.match(r'^https?://',url,re.I): return {'valid':False,'supported':False,'message':'Invalid URL.'}
    p=urlparse(url); domain=p.netloc.lower().split(':')[0].replace('www.','')
    source='youtube' if domain in {'youtube.com','youtu.be','m.youtube.com'} else ('direct_video' if p.path.lower().endswith(tuple(ALLOWED)) else 'unknown')
    return {'valid':True,'supported':source!='unknown','source':source,'domain':domain,'url':url}

# ============================================================
# START ONE-CLICK PROCESSING
# ============================================================

@app.post('/process/one-click')
async def one_click_start(
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(None),
    url: str | None = Form(None),
    prompt: str = Form('Find the best moments'),
    compression: str = Form('medium'),
    uid: int = Depends(current_user),
):
    compression = compression_level(compression)

    clean_url = (url or '').strip()

    if file is None and not clean_url:
        raise HTTPException(
            status_code=400,
            detail='Upload a video or provide a URL.'
        )

    job_id = uuid.uuid4().hex

    jobs[job_id] = {
        'status': 'starting',
        'progress': 0,
        'message': 'Preparing video...',
        'result': None,
        'error': None,
        'user_id': uid,
    }

    original_name = None
    original_url = clean_url or None
    title = None
    link_type_value = 'upload'

    # --------------------------------------------------------
    # UPLOAD: save the file immediately, then process async
    # --------------------------------------------------------
    if file is not None:
        filename = file.filename or ''

        ext = Path(filename).suffix.lower()

        if ext not in ALLOWED:
            raise HTTPException(
                status_code=400,
                detail=f'Unsupported video format: {ext}'
            )

        stem = (
            re.sub(
                r'[^A-Za-z0-9_-]+',
                '_',
                Path(filename).stem
            ).strip('_')
            or 'video'
        )

        saved_path = (
            UPLOAD_DIR
            / f'{stem}_{uuid.uuid4().hex[:10]}{ext}'
        )

        try:
            with saved_path.open('wb') as output:
                while True:
                    chunk = await file.read(1024 * 1024)

                    if not chunk:
                        break

                    output.write(chunk)

            original_name = saved_path.name
            title = filename

        except Exception as e:
            jobs[job_id]['status'] = 'failed'
            jobs[job_id]['message'] = 'Upload failed.'
            jobs[job_id]['error'] = str(e)

            raise HTTPException(
                status_code=500,
                detail=f'Upload failed: {e}'
            )

    # --------------------------------------------------------
    # URL: download inside background job
    # --------------------------------------------------------
    background_tasks.add_task(
        run_one_click_job,
        job_id,
        original_name,
        original_url,
        title,
        prompt,
        compression,
        link_type_value,
        uid,
    )

    return {
        'status': 'started',
        'job_id': job_id,
        'message': 'Processing started.'
    }

# ============================================================
# BACKGROUND ONE-CLICK PROCESSOR
# ============================================================

def run_one_click_job(
    job_id,
    original_name,
    original_url,
    title,
    prompt,
    compression,
    link_type_value,
    uid,
):
    try:
        job = jobs.get(job_id)

        if job is None:
            raise RuntimeError('Processing job no longer exists.')

        job.update({
            'status': 'processing',
            'progress': 2,
            'message': 'Preparing video...'
        })

        # ----------------------------------------------------
        # URL INPUT
        # ----------------------------------------------------
        if original_name is None:
            if not original_url:
                raise RuntimeError(
                    'No uploaded file or video URL was provided.'
                )

            if yt_dlp is None:
                raise RuntimeError(
                    'yt-dlp is not installed.'
                )

            job.update({
                'progress': 5,
                'message': 'Downloading video...'
            })

            ident = uuid.uuid4().hex[:10]
            template = str(UPLOAD_DIR / f'download_{ident}.%(ext)s')

            opts = {
                'outtmpl': template,
                'format': (
                    'bestvideo[ext=mp4]+'
                    'bestaudio[ext=m4a]/'
                    'best[ext=mp4]/'
                    'best'
                ),
                'merge_output_format': 'mp4',
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
            }

            try:
                with yt_dlp.YoutubeDL(opts) as y:
                    info = y.extract_info(original_url, download=True)

            except Exception as e:
                error_text = str(e)

                if "Sign in to confirm you’re not a bot" in error_text:
                    raise RuntimeError(
                        "YouTube is currently blocking this download. "
                        "Please upload the video file instead."
                    )

                raise RuntimeError(
                    f"YouTube download failed: {error_text}"
                )

            candidates = [
                p for p in UPLOAD_DIR.glob(f'download_{ident}.*')
                if p.suffix.lower() in ALLOWED
            ]

            if not candidates:
                raise RuntimeError('Downloaded video was not found.')

            original = candidates[0]
            title = clean(info.get('title', 'Downloaded video'))
            link_type_value = 'youtube'

        # ----------------------------------------------------
        # UPLOADED FILE
        # ----------------------------------------------------
        else:
            original = UPLOAD_DIR / os.path.basename(original_name)

            if not original.exists():
                raise RuntimeError('Uploaded video was not found.')

        # ----------------------------------------------------
        # COMPRESS
        # ----------------------------------------------------
        job.update({
            'progress': 12,
            'message': 'Preparing video...'
        })

        final = compress(original, compression)

        if final != original:
            try:
                original.unlink()
            except Exception:
                pass

        job.update({
            'progress': 18,
            'message': 'Analyzing video...'
        })

        d = db()
        c = d.cursor()

        try:
            c.execute(
                '''
                INSERT INTO videos
                (
                    user_id,
                    filename,
                    original_url,
                    title,
                    prompt,
                    compression_level,
                    link_type
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s)
                ''',
                (
                    uid,
                    final.name,
                    clean(original_url),
                    clean(title),
                    clean(prompt),
                    compression,
                    link_type_value,
                )
            )

            vid = c.lastrowid
            d.commit()

        except Exception:
            d.rollback()
            raise

        finally:
            c.close()
            d.close()

        jobs[job_id].update({
            'progress': 25,
            'message': 'Reading video information...'
        })

        a = cmd([
            'ffprobe',
            '-v',
            'error',
            '-show_entries',
            'format=duration',
            '-show_entries',
            'stream=width,height,r_frame_rate,codec_name',
            '-of',
            'json',
            str(final)
        ])

        info = json.loads(a.stdout)

        vs = next(
            (
                s
                for s in info.get('streams', [])
                if s.get('width') is not None
            ),
            {}
        )

        fps = None

        if '/' in str(
            vs.get('r_frame_rate', '')
        ):

            n, den = vs[
                'r_frame_rate'
            ].split('/', 1)

            if float(den):
                fps = round(
                    float(n) / float(den),
                    2
                )

        duration = round(
            float(
                info.get(
                    'format',
                    {}
                ).get(
                    'duration',
                    0
                )
            ),
            2
        )

        jobs[job_id].update({
            'progress': 30,
            'message': 'Transcribing audio...'
        })

        global whisper_model

        if whisper is None:
            raise RuntimeError(
                'Whisper is not installed.'
            )

        if whisper_model is None:
            jobs[job_id].update({
                'progress': 35,
                'message': 'Loading Whisper model...'
            })

            whisper_model = whisper.load_model(
                WHISPER_MODEL,
                device='cpu'
            )

        wav = UPLOAD_DIR / f'{final.stem}.wav'

        # --------------------------------------------------------
        # Extract audio as mono 16 kHz WAV
        # --------------------------------------------------------
        cmd([
            'ffmpeg',
            '-y',
            '-i',
            str(final),
            '-vn',
            '-ac',
            '1',
            '-ar',
            '16000',
            '-acodec',
            'pcm_s16le',
            str(wav)
        ])

        print('=== AUDIO DEBUG ===')
        print('WAV path:', wav)
        print('WAV exists:', wav.exists())
        print('WAV size:', wav.stat().st_size if wav.exists() else 0)

        jobs[job_id].update({
            'progress': 50,
            'message': 'Running speech recognition...'
        })

        # --------------------------------------------------------
        # Whisper transcription
        # --------------------------------------------------------
        try:
            transcription_result = whisper_model.transcribe(
                str(wav),
                word_timestamps=True,
                fp16=False,
                temperature=0,
                condition_on_previous_text=False,
                verbose=True,
            )

        except Exception as e:
            raise RuntimeError(
                f'Whisper transcription failed: {e}'
            )

        # finally:
        #     # Always remove temporary WAV
        #     try:
        #         wav.unlink()
        #     except Exception:
        #         pass

        if not isinstance(transcription_result, dict):
            raise RuntimeError(
                'Whisper returned an invalid transcription result.'
            )

        transcript = (
            transcription_result.get('text') or ''
        ).strip()

        language = (
            transcription_result.get('language')
            or 'unknown'
        )

        segments = []

        for segment in transcription_result.get(
            'segments',
            []
        ):
            text = (
                segment.get('text') or ''
            ).strip()

            start = float(
                segment.get('start', 0)
            )

            end = float(
                segment.get('end', 0)
            )

            if text and end > start:
                segments.append({
                    'text': text,
                    'start': round(start, 2),
                    'end': round(end, 2),
                })

        sentences = [
            s.strip()
            for s in re.split(
                r'(?<=[.!?])\s+',
                transcript
            )
            if s.strip()
        ]

        summary = (
            ' '.join(
                sentences[:6]
            )
            or transcript[:1200]
        )

        jobs[job_id].update({
            'progress': 60,
            'message': 'Finding best moments...'
        })

        prompt_words = set(
            re.findall(
                r'\w+',
                prompt.lower()
            )
        )

        keywords = {
            'important',
            'best',
            'amazing',
            'beautiful',
            'secret',
            'problem',
            'solution',
            'never',
            'always',
            'remember',
            'why',
            'how',
            'life',
            'happy',
            'sad',
            'success',
            'failure',
            'dream',
            'truth',
            'funny',
            'emotional',
            'interesting',
            'wow',
            'love'
        }

        candidates = []

        for s in segments:

            text = s['text']

            dur = (
                s['end']
                - s['start']
            )

            score = 50

            reasons = []

            low = text.lower()

            for w in prompt_words:

                if (
                    len(w) >= 3
                    and w in low
                ):

                    score += 8

                    reasons.append(
                        'prompt:' + w
                    )

            for w in keywords:

                if w in low:

                    score += 5

                    reasons.append(
                        'keyword:' + w
                    )

            if len(text) >= 20:
                score += 5

            if len(text) >= 50:
                score += 5

            if '?' in text:

                score += 8

                reasons.append(
                    'question'
                )

            if '!' in text:

                score += 8

                reasons.append(
                    'exclamation'
                )

            if 2 <= dur <= 8:

                score += 10

                reasons.append(
                    'ideal-length'
                )

            elif 8 < dur <= 12:

                score += 5

            elif dur > 15:

                score -= 20

            candidates.append({
                'text': text,
                'start': s['start'],
                'end': s['end'],
                'score': max(
                    0,
                    min(
                        100,
                        score
                    )
                ),
                'reason':
                    ', '.join(reasons)
                    or
                    'Smart transcript scoring'
            })

        candidates.sort(
            key=lambda x: x['score'],
            reverse=True
        )

        selected = []

        for x in candidates:

            if any(
                x['start'] < y['end']
                and x['end'] > y['start']
                for y in selected
            ):
                continue

            selected.append(x)

            if len(selected) >= 5:
                break

        jobs[job_id].update({
            'progress': 70,
            'message': 'Saving results...'
        })

        d = db()
        c = d.cursor()

        try:
            c.execute(
                '''
                UPDATE videos
                SET
                    language=%s,
                    width=%s,
                    height=%s,
                    fps=%s,
                    duration_seconds=%s
                WHERE video_id=%s
                ''',
                (
                    clean(language),
                    vs.get('width'),
                    vs.get('height'),
                    fps,
                    duration,
                    vid
                )
            )

            c.execute(
                '''
                INSERT INTO transcripts
                (
                    video_id,
                    transcript
                )
                VALUES(%s,%s)
                ''',
                (
                    vid,
                    clean(transcript)
                )
            )

            c.execute(
                '''
                INSERT INTO summaries
                (
                    video_id,
                    summary
                )
                VALUES(%s,%s)
                ''',
                (
                    vid,
                    clean(summary)
                )
            )

            for s in segments:
                c.execute(
                    '''
                    INSERT INTO transcript_segments
                    (
                        video_id,
                        text,
                        start_time,
                        end_time
                    )
                    VALUES(%s,%s,%s,%s)
                    ''',
                    (
                        vid,
                        clean(s['text']),
                        s['start'],
                        s['end']
                    )
                )

            for h in selected:
                c.execute(
                    '''
                    INSERT INTO highlights
                    (
                        video_id,
                        text,
                        start_time,
                        end_time,
                        score,
                        reason
                    )
                    VALUES(%s,%s,%s,%s,%s,%s)
                    ''',
                    (
                        vid,
                        clean(h['text']),
                        h['start'],
                        h['end'],
                        h['score'],
                        clean(h['reason'])
                    )
                )

            d.commit()

        except Exception:
            d.rollback()
            raise

        finally:
            c.close()
            d.close()

        jobs[job_id].update({
            'progress': 80,
            'message': 'Generating clips...'
        })

        clips = []

        for i, h in enumerate(
            selected,
            1
        ):

            cf = (
                f'{final.stem}'
                f'_clip_{i}.mp4'
            )

            cp = (
                CLIPS_DIR / cf
            )

            # Add context around each detected highlight
            clip_start = max(0, h['start'] - 5)
            clip_end = min(duration, h['end'] + 5)
            clip_duration = clip_end - clip_start

            if clip_duration <= 0:
                continue

            cmd([
                'ffmpeg',
                '-y',
                '-ss',
                str(clip_start),
                '-i',
                str(final),
                '-t',
                str(clip_duration),
                '-c:v',
                'libx264',
                '-preset',
                'ultrafast',
                '-crf',
                '28',
                '-pix_fmt',
                'yuv420p',
                '-c:a',
                'aac',
                '-b:a',
                '96k',
                '-movflags',
                '+faststart',
                str(cp)
            ])

            clips.append({
                'clip_number': i,
                'filename': cf,
                'url':
                    f'/video/clip/{cf}',
                'start': h['start'],
                'end': h['end'],
                'clip_start': round(clip_start, 2),
                'clip_end': round(clip_end, 2),
                'duration': round(
                    clip_duration,
                    2
                ),
                'score': h['score'],
                'text': h['text'],
                'reason': h['reason']
            })

        result = {
            'message':
                'One-click SmartClip processing completed.',
            'video_id': vid,
            'filename': final.name,
            'title': title,
            'compression': compression,
            'language': language,
            'analysis': {
                'width': vs.get('width'),
                'height': vs.get('height'),
                'fps': fps,
                'duration_seconds': duration,
                'video_codec':
                    vs.get('codec_name')
            },
            'transcript': transcript,
            'summary': summary,
            'highlights': selected,
            'clips': clips,
            'video_url':
                f'/video/file/{final.name}'
        }

        jobs[job_id].update({
            'status': 'completed',
            'progress': 100,
            'message': 'Processing completed.',
            'result': result,
            'error': None
        })

        print(
            f'JOB {job_id} COMPLETED'
        )

    except Exception as e:

        print(
            f'JOB {job_id} ERROR:',
            repr(e)
        )

        jobs[job_id].update({
            'status': 'failed',
            'progress': 100,
            'message': 'Processing failed.',
            'error': str(e)
        })

# ============================================================
# ONE-CLICK JOB STATUS
# ============================================================

@app.get('/process/status/{job_id}')
def one_click_status(
    job_id: str,
    uid: int = Depends(current_user)
):

    job = jobs.get(job_id)

    if job is None:
        raise HTTPException(
            404,
            'Processing job not found.'
        )

    if job['user_id'] != uid:
        raise HTTPException(
            403,
            'You do not have access to this job.'
        )

    return {
        'job_id': job_id,
        'status': job['status'],
        'progress': job['progress'],
        'message': job['message'],
        'result': job['result'],
        'error': job['error']
    }

@app.get('/video/file/{filename}')
def serve_video(filename:str,uid:int=Depends(current_user)):
    p=video_path(filename); return FileResponse(str(p),media_type='video/mp4',filename=p.name)
@app.get('/video/clip/{filename}')
def serve_clip(filename:str,uid:int=Depends(current_user)):
    p=CLIPS_DIR/os.path.basename(filename)
    if not p.exists():raise HTTPException(404,'Clip not found.')
    return FileResponse(str(p),media_type='video/mp4',filename=p.name)
@app.get('/user/videos')
def user_videos(uid:int=Depends(current_user)):
    d=db();c=d.cursor(dictionary=True);c.execute('SELECT * FROM videos WHERE user_id=%s ORDER BY video_id DESC',(uid,));rows=c.fetchall();c.close();d.close();return {'count':len(rows),'videos':rows}
@app.get('/health/db')
def health_db():
    configured = bool(DB_HOST and DB_USER and DB_NAME)
    if not configured:
        return {
            'status': 'error',
            'database_configured': False,
            'host': DB_HOST,
            'user': DB_USER,
            'database': DB_NAME,
        }

    try:
        connection = db()
        connection.close()
        return {
            'status': 'ok',
            'database_configured': True,
            'host': DB_HOST,
            'port': int(DB_PORT or 3306),
            'user': DB_USER,
            'database': DB_NAME,
        }
    except HTTPException as e:
        return {
            'status': 'error',
            'database_configured': True,
            'host': DB_HOST,
            'port': int(DB_PORT or 3306),
            'user': DB_USER,
            'database': DB_NAME,
            'error': e.detail,
        }


@app.get('/health')
def health():
    return {
        'status': 'healthy',
        'service': 'SmartClip AI',
        'version': '3.0.0',
        'database': MYSQL_DATABASE,
        'database_configured': bool(DB_HOST and DB_USER and DB_NAME),
    }
