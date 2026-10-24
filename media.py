"""사진·동영상 비교(색인 단계 media): 파일 바이트가 달라도 보이는 내용이 같은 사진·동영상을 짝(media_pair)으로 남긴다.
- 사진: 9×8 회색으로 줄여 이웃 칸의 밝기 차이로 만든 64비트 그림 지문(dHash). 크기만 바꿨거나 다시 저장한 사진은 지문이 거의 같다.
- 동영상: video_interval초(기본 2분)마다 한 장면을 뽑아 같은 그림 지문을 만든다(짧은 영상은 길이를 MIN_FRAMES등분).
  길이가 LENGTH_GAP 안쪽으로 비슷한 영상끼리 같은 시각의 장면(앞뒤 한 장까지 밀어 봄)을 맞춰, 같은 장면의 비율이 video_similar% 이상이면 짝.
- 거의 한 가지 색인 장면(검은 화면 등)은 서로 다른 영상끼리도 같아 보여 빼고 센다.
장면은 ffmpeg(imageio-ffmpeg에 든 것)로, 사진은 Pillow(HEIC는 pillow-heif)로 읽는다. 이 단계가 돌 때만 불러온다."""
import os
import re
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import filetime
import index
import settings

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv", ".m4v", ".ts"}
IMAGE_BITS = 6  # 사진: 지문이 이만큼(64비트 중) 이하로 다르면 같은 그림
FRAME_BITS = 10  # 동영상의 한 장면: 이만큼 이하로 다르면 같은 장면
BANDS = 8  # 사진 지문을 8비트씩 8조각으로 나눈다. IMAGE_BITS(6)비트 이하로 다르면 다른 비트가 든 조각은 많아야 6개라,
SAME_BANDS = BANDS - IMAGE_BITS  # 적어도 이만큼(2조각)은 같다. 그런 사진만 비교해도 짝을 하나도 놓치지 않는다.
MIN_FRAMES = 5
MIN_COMPARED = 3  # 이보다 적은 장면만 비교할 수 있으면 판단하지 않는다.
LENGTH_GAP = 0.1  # 동영상 길이가 서로 10% 넘게 다르면 비교하지 않는다.
BLANK = 8  # 줄인 장면에서 가장 밝은 칸과 가장 어두운 칸의 차이가 이보다 작으면 한 가지 색으로 본다.
NO_WINDOW = 0x08000000  # ffmpeg 창을 띄우지 않는다.
# 장면을 뽑는 ffmpeg를 동시에 이만큼 띄운다. 10분 영상 20장면: 하나씩 2.85초, 4개씩 1.22초, 8개씩 0.97초(뽑은 장면은 같다).
# 하드 디스크에서는 너무 많이 띄우면 오히려 느려질 수 있어 4로 둔다(ffmpeg 하나에 장면을 모두 넣는 방법은 결과가 달라 쓰지 않는다).
WORKERS = 4


def dhash(gray):
    """9×8 회색 72바이트 → 64비트 지문. 한 가지 색이면 None."""
    if len(gray) != 72 or max(gray) - min(gray) < BLANK:
        return None
    value = 0
    for y in range(8):
        for x in range(8):
            value = value << 1 | (gray[y * 9 + x] < gray[y * 9 + x + 1])
    return value


def distance(a, b):
    return (a ^ b).bit_count()


def image_hash(path):
    """사진의 그림 지문(돌려 찍은 정보는 반영한다). 열지 못하면 None."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if os.path.splitext(path)[1].lower() in (".heic", ".heif"):
        import pillow_heif

        pillow_heif.register_heif_opener()
    try:
        with Image.open(path) as img:
            img.draft("L", (64, 64))  # JPEG는 줄여서 읽어 빠르다.
            small = ImageOps.exif_transpose(img).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            return dhash(small.tobytes())
    except (OSError, UnidentifiedImageError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError):  # 깨진 EXIF·끊긴 HEIC
        return None


def ffmpeg():
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def video_length(path):
    """동영상 길이(초). 모르면 None."""
    result = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True, creationflags=NO_WINDOW, timeout=60)
    found = re.search(rb"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    return int(found[1]) * 3600 + int(found[2]) * 60 + float(found[3]) if found else None


def grab(path, seconds, options):
    """seconds의 시각마다 장면 하나를 options(-vf 등)대로 바꾼 rawvideo 바이트, 순서대로(뽑지 못하면 None).
    ffmpeg를 WORKERS개까지 동시에 띄운다. 사진·동영상 비교와 인식이 함께 쓴다."""
    def one(second):
        result = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{second:.2f}", "-i", path, "-frames:v", "1", *options, "-f", "rawvideo", "-"],
                                capture_output=True, creationflags=NO_WINDOW, timeout=120)
        return result.stdout if result.returncode == 0 else None

    with ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(one, seconds))


def frame_hashes(path, seconds):
    """seconds마다 장면 지문(한 가지 색이거나 뽑지 못하면 None)."""
    return [dhash(raw) if raw is not None else None for raw in grab(path, seconds, ["-vf", "scale=9:8,format=gray"])]


def frame_times(length, interval):
    """장면을 뽑을 시각들: interval초마다(각 칸의 가운데). 그러면 MIN_FRAMES장이 안 되는 짧은 영상은 길이를 MIN_FRAMES등분."""
    if length >= interval * MIN_FRAMES:
        return [interval * (k + 0.5) for k in range(int(length // interval))]
    return [length * (k + 0.5) / MIN_FRAMES for k in range(MIN_FRAMES)]


def encode(hashes):
    return ",".join("-" if h is None else f"{h:016x}" for h in hashes)


def decode(text):
    return [None if part == "-" else int(part, 16) for part in text.split(",")] if text else []


def similarity(a, b):
    """두 동영상의 장면 지문 목록 → 같은 장면의 비율(%). 비교한 장면이 MIN_COMPARED보다 적으면 None.
    같은 차례의 장면을 앞뒤 한 장까지 밀어 보고 가장 가까운 것을 쓴다(앞부분이 조금 잘린 영상도 맞게)."""
    if len(a) > len(b):
        a, b = b, a
    compared = same = 0
    for i, h in enumerate(a):
        near = [x for x in b[max(0, i - 1):i + 2] if x is not None]
        if h is None or not near:
            continue
        compared += 1
        same += min(distance(h, x) for x in near) <= FRAME_BITS
    return round(100 * same / compared) if compared >= MIN_COMPARED else None


def todo(db, interval):
    """지문을 (다시) 만들 파일: 없거나, 그 뒤에 바뀌었거나, 동영상인데 장면 간격 설정이 바뀐 것."""
    exts = sorted(IMAGE_EXTS | VIDEO_EXTS)
    return db.execute(f"""select f.id, f.path, f.ext, f.stamp from files f left join media m on m.id = f.id
                          where f.ext in ({', '.join('?' * len(exts))}) and f.size > 0
                            and (m.id is null or m.made <> f.stamp or (m.kind = 'video' and m.interval <> ?))""", exts + [interval]).fetchall()


def fingerprint(db, row, interval):
    """파일 하나의 지문을 만들어 media에 넣는다. 읽은 뒤 연 시각은 되돌린다."""
    st = os.stat(row["path"])
    if st.st_mtime_ns != row["stamp"]:  # 목록을 훑은 뒤에 바뀌었다: 다음 색인 때 한다.
        return
    for table, column in (("media", "id"), ("media_todo", "id"), ("media_pair", "a"), ("media_pair", "b")):
        db.execute(f"delete from {table} where {column} = ?", (row["id"],))
    if row["ext"] in IMAGE_EXTS:
        h = image_hash(row["path"])
        db.execute("insert into media(id, kind, duration, frames, made, interval) values(?, 'image', null, ?, ?, null)",
                   (row["id"], encode([h]) if h is not None else "", row["stamp"]))
        if h is not None:
            db.execute("insert or ignore into media_todo(id) values(?)", (row["id"],))
    else:
        length = video_length(row["path"])
        frames = frame_hashes(row["path"], frame_times(length, interval)) if length else []
        db.execute("insert into media(id, kind, duration, frames, made, interval) values(?, 'video', ?, ?, ?, ?)",
                   (row["id"], length, encode(frames), row["stamp"], interval))
        db.execute("delete from meta where name = 'video_similar'")  # 동영상 짝을 다시 맞추게 한다
    filetime.restore_access(row["path"], st)


def pair(db, a, b, score):
    db.execute("insert or replace into media_pair(a, b, score) values(?, ?, ?)", (min(a, b), max(a, b), score))


def match_images(db):
    """아직 짝을 맞추지 않은 사진(media_todo)마다, 같은 조각이 SAME_BANDS개 이상인 사진과만 비교해 IMAGE_BITS 안쪽이면 짝으로 남긴다.
    사진 지문과 조각 표는 메모리에 한 번 만든다(후보마다 DB에 묻거나, 조각 하나만 같은 것까지 비교하면 사진 수십만 장에서 수십억 번이 된다).
    멈추면(index.stop) 남은 사진은 media_todo에 남아 다음 색인 때 이어서 한다."""
    ids = [i for (i,) in db.execute("select id from media_todo")]
    if not ids:
        return
    hashes = {}
    for r in db.execute("select id, frames from media where kind = 'image'"):
        frames = decode(r["frames"])
        if frames and frames[0] is not None:
            hashes[r["id"]] = frames[0]
    bands = defaultdict(list)
    for i, h in hashes.items():
        for n in range(BANDS):
            bands[n, h >> (8 * n) & 255].append(i)
    index.status.update(step="사진 짝 맞추기", done=0, total=len(ids))
    for me in ids:
        if index.stop.is_set():
            return
        h = hashes.get(me)
        if h is not None:
            same = Counter(other for n in range(BANDS) for other in bands[n, h >> (8 * n) & 255] if other != me)
            for other in (o for o, c in same.items() if c >= SAME_BANDS):
                d = distance(h, hashes[other])
                if d <= IMAGE_BITS:
                    pair(db, me, other, round(100 * (1 - d / 64)))
        db.execute("delete from media_todo where id = ?", (me,))
        index.status["done"] += 1
        index.save_now(db)


def match_videos(db, similar):
    """동영상 짝을 모두 다시 정한다(유사도 기준을 바꿔도 맞게). 길이 순으로 놓고 LENGTH_GAP 안쪽의 영상끼리만 비교한다."""
    videos = [(r["id"], r["duration"], decode(r["frames"])) for r in db.execute(
        "select id, duration, frames from media where kind = 'video' and duration > 0 order by duration")]
    db.execute("delete from media_pair where a in (select id from media where kind = 'video')")
    for i, (a, length, frames) in enumerate(videos):
        if index.stop.is_set():  # 멈추면 표시(meta)를 남기지 않아 다음에 처음부터 다시 맞춘다
            return
        for b, other_length, other_frames in videos[i + 1:]:
            if other_length > length * (1 + LENGTH_GAP):
                break
            score = similarity(frames, other_frames)
            if score is not None and score >= similar:
                pair(db, a, b, score)


def run(db):
    """색인 단계: 남은 사진·동영상의 지문을 만들고 짝을 맞춘다. 멈추면(index.stop) 그때까지 한 것은 남긴다."""
    config = settings.load()
    rows = todo(db, config["video_interval"])
    index.status.update(step="사진·동영상 비교", done=0, total=len(rows))
    for row in rows:
        if index.stop.is_set():
            break
        index.status["current"] = row["path"]
        try:
            fingerprint(db, row, config["video_interval"])
        except (OSError, subprocess.SubprocessError):
            continue
        index.status["done"] += 1
        index.save_now(db)
    match_images(db)
    last = db.execute("select value from meta where name = 'video_similar'").fetchone()
    if not index.stop.is_set() and (last is None or last[0] != config["video_similar"]):  # 새 동영상 지문은 이 표시를 지운다. 지운 동영상의 짝은 drop_ids가 함께 지운다.
        match_videos(db, config["video_similar"])
        if not index.stop.is_set():
            db.execute("insert or replace into meta(name, value) values('video_similar', ?)", (config["video_similar"],))
    index.save_now(db, force=True)
