"""사진·동영상 인식(색인 단계 recognize). 모델과 무거운 라이브러리(onnxruntime·OpenCV·numpy)는 이 단계를 시작할 때 올리고 끝나면 내린다.
- 종류(CLIP): 글로 적은 분류(GROUPS) 중 가장 가까운 것, 동물·음식이면 그 안의 종류(ANIMALS·FOODS)까지. tags(kind 'group'·'animal'·'food').
- 내 분류 모델: 사용자가 고른 이미지 분류 ONNX(옆의 config.json·preprocessor_config.json). 분류마다 점수(%)를 tags(kind 'custom', label 번호)에.
  화면에는 모델 속 분류 이름을 쓰지 않고 사용자가 설정에서 붙인 이름만 쓴다(custom_labels).
- 얼굴(사진만): YuNet으로 찾고 SFace 지문으로 같은 사람끼리 묶는다(faces, person 번호). 얼굴 정보는 이 PC의 색인에만 둔다.
동영상은 video_interval 간격 장면 중 MAX_FRAMES장까지: 종류는 장면 지문의 평균, 내 분류 모델은 장면 중 가장 높은 점수.
이 단계는 따로 띄운 프로세스(같은 프로그램을 --recognize로)에서 돈다. onnxruntime 등은 한 번 불러오면 프로세스가 끝날 때까지
메모리(약 400MB)에 남기 때문이다. 끝나면 그 프로세스가 끝나 모두 돌아간다. 진행은 한 줄씩 JSON으로 받고, 멈추기는 표시 파일(STOP)로 알린다.
표준 입력으로 알리지 않는 까닭: Windows에서 한 스레드가 파이프를 읽고 기다리면, 라이브러리를 불러올 때 그 파이프의 종류를 묻는 일(GetFileType)이 멈춰 버린다."""
import gc
import json
import os
import subprocess
import sys
import threading

import filetime
import index
import media
import models
import settings

GROUPS = [("animal", "동물", "a photo of an animal"), ("food", "음식", "a photo of food"),
          ("landscape", "풍경", "a photo of a landscape or scenery"), ("people", "사람", "a photo of a person or people"),
          ("document", "문서·스크린샷", "a screenshot or a photo of a document with text"), ("other", "기타 물건", "a photo of an object")]
ANIMALS = [("dog", "강아지"), ("cat", "고양이"), ("bird", "새"), ("fish", "물고기"), ("rabbit", "토끼"), ("hamster", "햄스터"),
           ("horse", "말"), ("cow", "소"), ("pig", "돼지"), ("sheep", "양"), ("chicken", "닭"), ("duck", "오리"), ("turtle", "거북이"),
           ("frog", "개구리"), ("snake", "뱀"), ("lizard", "도마뱀"), ("insect", "곤충"), ("butterfly", "나비"), ("bear", "곰"),
           ("tiger", "호랑이"), ("lion", "사자"), ("elephant", "코끼리"), ("monkey", "원숭이"), ("deer", "사슴"), ("squirrel", "다람쥐"),
           ("fox", "여우"), ("penguin", "펭귄"), ("dolphin", "돌고래")]
FOODS = [("pizza", "피자"), ("hamburger", "햄버거"), ("sushi", "초밥"), ("noodles", "면 요리"), ("rice dish", "밥 요리"),
         ("fried chicken", "치킨"), ("grilled meat", "고기 구이"), ("steak", "스테이크"), ("salad", "샐러드"), ("soup or stew", "국·찌개"),
         ("kimchi", "김치"), ("bread", "빵"), ("cake", "케이크"), ("dessert", "디저트"), ("fruit", "과일"), ("coffee", "커피"),
         ("drink", "음료"), ("tteokbokki", "떡볶이"), ("dumplings", "만두"), ("seafood", "해산물"), ("sandwich", "샌드위치"),
         ("pasta", "파스타"), ("curry", "카레")]
LABELS = {"group": {k: v for k, v, _ in GROUPS}, "animal": dict(ANIMALS), "food": dict(FOODS)}  # 화면 이름(서버가 내려준다)
MEAN, STD = (0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711)  # CLIP 입력 정규화
EOS = 49407  # CLIP 글 토막의 끝(모자란 자리도 이것으로 채운다)
MAX_FRAMES = 12
FACE_SCORE = 0.8  # 얼굴로 볼 최소 점수(YuNet)
FACE_MIN = 40  # 이보다 작은 얼굴(px)은 같은 사람 판단이 흔들려 쓰지 않는다.
SAME_PERSON = 0.42  # SFace 지문의 코사인 유사도가 이 이상이면 같은 사람(OpenCV 권장 0.363보다 조금 엄하게: 남을 한 사람으로 덜 묶게)
THREADS = 2  # 색인하는 동안에도 PC가 느려지지 않게
STOP = "recognize.stop"  # 설정 폴더에 이 파일이 생기면 인식 프로세스가 지금 파일까지만 하고 멈춘다.


def custom_signature(config):
    """내 분류 모델이 바뀌었는지 알 표시(경로·크기·고친 시각). 없으면 ''."""
    path = config.get("custom_model_path") or ""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    return f"{path}|{st.st_size}|{st.st_mtime_ns}"


def custom_count(path):
    """내 분류 모델의 분류 수(옆의 config.json). 모르면 0."""
    try:
        return len(json.load(open(os.path.join(os.path.dirname(path), "config.json"), encoding="utf-8"))["id2label"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


class Engine:
    """이 단계 동안만 쓰는 모델들. 만들 때 올리고 close()로 내린다."""

    def __init__(self, config):
        import numpy as np
        import onnxruntime as ort

        self.np = np
        options = ort.SessionOptions()
        options.intra_op_num_threads = THREADS
        self.session = lambda file: ort.InferenceSession(file, options, providers=["CPUExecutionProvider"])
        self.vision = self.session(models.path("clip/vision.onnx"))
        self.prompts = self.text_embeddings()
        self.custom, self.custom_input = None, None
        if custom_signature(config):
            self.custom = self.session(config["custom_model_path"])
            self.custom_input = self.custom_preprocess(config["custom_model_path"])
        self.faces = None
        if config.get("face_on", True) and models.ready("face"):
            import cv2

            self.cv2 = cv2
            self.detector = cv2.FaceDetectorYN.create(models.path("face/yunet.onnx"), "", (320, 320), FACE_SCORE)
            self.recognizer = cv2.FaceRecognizerSF.create(models.path("face/sface.onnx"), "")
            self.faces = True

    def close(self):
        self.vision = self.custom = self.detector = self.recognizer = None
        gc.collect()

    def text_embeddings(self):
        """분류 글들의 CLIP 지문(정규화). 글 모델은 여기서만 쓰고 바로 내린다."""
        from tokenizers import Tokenizer

        np = self.np
        tokenizer = Tokenizer.from_file(models.path("clip/tokenizer.json"))
        text = self.session(models.path("clip/text.onnx"))
        sets = {"group": [p for _, _, p in GROUPS], "animal": [f"a photo of a {n}, a type of animal" for n, _ in ANIMALS],
                "food": [f"a photo of {n}, a type of food" for n, _ in FOODS]}
        out = {}
        for name, prompts in sets.items():
            ids = [tokenizer.encode(p).ids for p in prompts]
            width = max(map(len, ids))
            vectors = text.run(None, {"input_ids": np.array([i + [EOS] * (width - len(i)) for i in ids], dtype=np.int64)})[0]
            out[name] = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        del text
        return out

    def clip_input(self, image):
        """PIL 그림 → CLIP 입력(짧은 변 224로 줄여 가운데 224×224, 정규화)."""
        from PIL import Image

        from PIL import ImageOps

        np = self.np
        image = ImageOps.fit(image.convert("RGB"), (224, 224), Image.Resampling.BICUBIC)  # 가운데를 먼저 잘라 줄인다(아주 가늘고 긴 그림도 메모리가 작다)
        pixels = (np.asarray(image, dtype=np.float32) / 255 - MEAN) / STD
        return pixels.transpose(2, 0, 1)[None].astype(np.float32)

    def custom_preprocess(self, path):
        """내 분류 모델의 입력 규칙(옆의 preprocessor_config.json: 크기·평균·표준편차). 없으면 224×224, 0.5/0.5."""
        try:
            rule = json.load(open(os.path.join(os.path.dirname(path), "preprocessor_config.json"), encoding="utf-8"))
        except (OSError, ValueError):
            rule = {}
        size = rule.get("size") or {}
        side = size.get("height") or size.get("shortest_edge") or 224
        return {"size": (size.get("width") or side, side), "mean": rule.get("image_mean") or (0.5, 0.5, 0.5),
                "std": rule.get("image_std") or (0.5, 0.5, 0.5)}

    def image_vector(self, image):
        vector = self.vision.run(None, {"pixel_values": self.clip_input(image)})[0][0]
        return vector / self.np.linalg.norm(vector)

    def custom_scores(self, image):
        """내 분류 모델의 분류별 점수(0~100)."""
        from PIL import Image

        np = self.np
        rule = self.custom_input
        pixels = (np.asarray(image.convert("RGB").resize(rule["size"], Image.Resampling.BILINEAR), dtype=np.float32) / 255 - rule["mean"]) / rule["std"]
        name = self.custom.get_inputs()[0].name
        logits = self.custom.run(None, {name: pixels.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
        e = np.exp(logits - logits.max())
        return [round(float(x) * 100) for x in e / e.sum()]

    def kinds(self, vector):
        """CLIP 지문 → [(kind, label, 점수)]: 분류 하나와, 동물·음식이면 그 안의 종류 하나."""
        np = self.np

        def best(name):
            p = np.exp(100 * self.prompts[name] @ vector)
            p /= p.sum()
            i = int(p.argmax())
            return i, round(float(p[i]) * 100)

        i, score = best("group")
        group = GROUPS[i][0]
        found = [("group", group, score)]
        if group in ("animal", "food"):
            j, sub = best(group)
            found.append((group, (ANIMALS if group == "animal" else FOODS)[j][0], sub))
        return found

    def face_vectors(self, image):
        """사진 속 얼굴들의 SFace 지문(정규화). 너무 큰 사진은 줄여서 찾는다."""
        np = self.np
        image = image.convert("RGB")
        image.thumbnail((1280, 1280))
        bgr = np.ascontiguousarray(np.asarray(image)[:, :, ::-1])
        self.detector.setInputSize((bgr.shape[1], bgr.shape[0]))
        _, found = self.detector.detect(bgr)
        vectors = []
        for face in found if found is not None else []:
            if min(face[2], face[3]) < FACE_MIN:
                continue
            vector = self.recognizer.feature(self.recognizer.alignCrop(bgr, face))[0]
            vectors.append(vector / np.linalg.norm(vector))
        return vectors


def open_image(path):
    from PIL import Image, ImageOps

    if os.path.splitext(path)[1].lower() in (".heic", ".heif"):
        import pillow_heif

        pillow_heif.register_heif_opener()
    try:
        image = Image.open(path)
        image.draft("RGB", (1280, 1280))
        return ImageOps.exif_transpose(image)  # 여기서 읽는다
    except RuntimeError as e:  # 깨진 HEIC(pillow_heif): 모델 오류(RuntimeError, 위로 올린다)와 나눠 파일 문제로
        raise ValueError(str(e)) from e


def video_frames(path, interval):
    """동영상의 장면들(PIL, 짧은 변 224 이상). 길이를 모르면 빈 목록."""
    from PIL import Image

    length = media.video_length(path)
    if not length:
        return []
    times = media.frame_times(length, interval)
    step = max(1, len(times) // MAX_FRAMES)
    raws = media.grab(path, times[::step][:MAX_FRAMES], ["-vf", "scale=224:224:force_original_aspect_ratio=increase,crop=224:224", "-pix_fmt", "rgb24"])
    return [Image.frombytes("RGB", (224, 224), raw) for raw in raws if raw is not None and len(raw) == 224 * 224 * 3]


def people(db):
    """사람 번호 → 그 사람 얼굴 지문의 평균(정규화)."""
    import numpy as np

    sums = {}
    for person, vec in db.execute("select person, vec from faces"):
        v = np.frombuffer(vec, dtype=np.float32)
        sums[person] = sums.get(person, 0) + v
    return People({p: v / np.linalg.norm(v) for p, v in sums.items()})


class People:
    """사람 번호 → 얼굴 지문의 평균(정규화)을 행렬 하나(rows의 앞 size줄)로 든다. 얼굴마다 모든 사람과 한 번에 곱해 견준다.
    한 명씩 견주면 사람 2,000명에 얼굴 500개가 1.6초였다. 줄이 모자라면 두 배로 늘린다(사람이 늘 때마다 행렬을 베끼지 않게)."""

    def __init__(self, centers=None):
        import numpy as np

        self.np = np
        self.ids = list(centers or {})
        self.rows = np.stack([centers[p] for p in self.ids]).astype(np.float32) if self.ids else None
        self.size = len(self.ids)
        self.next = max(self.ids, default=0) + 1  # 새 사람의 번호(지금까지의 가장 큰 번호 + 1)

    def closest(self, vector):
        """(줄 번호, 사람 번호) 중 SAME_PERSON 이상으로 가장 가까운 것, 없으면 (None, None). 점수가 같으면 나중에 생긴 사람."""
        if not self.size:
            return None, None
        scores = self.rows[:self.size] @ vector
        i = self.size - 1 - int(self.np.argmax(scores[::-1]))
        return (i, self.ids[i]) if scores[i] >= SAME_PERSON else (None, None)

    def add(self, vector):
        """새 사람을 더하고 그 번호를 돌려준다."""
        if self.rows is None:
            self.rows = self.np.empty((16, len(vector)), dtype=self.np.float32)
        elif self.size == len(self.rows):
            self.rows = self.np.concatenate([self.rows, self.np.empty_like(self.rows)])
        self.rows[self.size] = vector
        self.ids.append(self.next)
        self.next += 1
        self.size += 1
        return self.ids[-1]

    def mix(self, i, vector):
        """i줄의 사람 평균에 새 얼굴을 조금 섞는다(평균을 따라가게)."""
        mixed = self.rows[i] + vector * 0.2
        self.rows[i] = mixed / self.np.linalg.norm(mixed)


def assign(db, file_id, vectors, people):
    """얼굴마다 가장 가까운 사람(SAME_PERSON 이상)에, 없으면 새 사람으로 넣는다. people(People)은 그 자리에서 고친다."""
    for n, vector in enumerate(vectors):
        i, best = people.closest(vector)
        if best is None:
            best = people.add(vector)
        else:
            people.mix(i, vector)
        db.execute("insert into faces(id, n, person, vec) values(?, ?, ?, ?)", (file_id, n, best, vector.astype(people.np.float32).tobytes()))


def todo(db, signature, faces_on):
    exts = sorted(media.IMAGE_EXTS | media.VIDEO_EXTS)
    return db.execute(f"""select f.id, f.path, f.ext, f.stamp from files f left join recognized r on r.id = f.id
                          where f.ext in ({', '.join('?' * len(exts))}) and f.size > 0
                            and (r.id is null or r.made <> f.stamp or r.custom <> ? or r.faces <> ?)""", exts + [signature, int(faces_on)]).fetchall()


def local(path):
    """이 PC에 내용이 있는 파일인지(클라우드 전용이거나 사라졌으면 False)."""
    try:
        return not index.cloud_only(os.stat(path))
    except OSError:
        return False


def recognize_one(db, engine, row, config, centers):
    """파일 하나를 인식해 tags·faces·recognized에 넣는다. 열지 못하면 빈 결과로 표시만 한다(다시 시도하지 않게)."""
    from PIL import Image

    np = engine.np
    st = os.stat(row["path"])
    if index.stamp(st) != row["stamp"] or index.cloud_only(st):  # 클라우드 전용: 받은 뒤에 한다
        return False
    readable = media.openable(row["path"])
    tags, faces = [], []  # 다 읽은 뒤에 쓴다(읽는 동안 쓰기 잠금을 잡지 않게: index.release)
    try:
        if not readable:
            pass  # 늘 열 수 없다(권한 없음 등): 빈 결과로
        elif row["ext"] in media.IMAGE_EXTS:
            with open_image(row["path"]) as image:
                tags += engine.kinds(engine.image_vector(image))
                if engine.custom is not None:
                    tags += [("custom", str(i), s) for i, s in enumerate(engine.custom_scores(image))]
                if engine.faces:
                    faces = engine.face_vectors(image)
        else:
            frames = video_frames(row["path"], config["video_interval"])
            if frames:
                mean = np.mean([engine.image_vector(f) for f in frames], axis=0)
                tags += engine.kinds(mean / np.linalg.norm(mean))
                if engine.custom is not None:
                    scores = [engine.custom_scores(f) for f in frames]
                    tags += [("custom", str(i), max(s[i] for s in scores)) for i in range(len(scores[0]))]
    except OSError as e:
        if e.errno is not None and row["ext"] in media.IMAGE_EXTS and media.again(e, row["path"]):  # 사진을 읽다가 잠시 막혔다: 기록하지 않고 다음에 다시
            raise
        tags, faces = [], []
    except (ValueError, SyntaxError, EOFError, MemoryError, subprocess.SubprocessError, Image.DecompressionBombError):
        tags, faces = [], []  # 깨진 EXIF·끊긴 HEIC·아주 큰 사진 등 파일 문제. 모델 오류는 위로 올려 단계를 멈추고 알린다.
    for table in ("tags", "faces", "recognized"):
        db.execute(f"delete from {table} where id = ?", (row["id"],))
    if faces:
        assign(db, row["id"], faces, centers)
    db.executemany("insert into tags(id, kind, label, score) values(?, ?, ?, ?)", [(row["id"], *t) for t in tags])
    db.execute("insert into recognized(id, made, custom, faces) values(?, ?, ?, ?)",
               (row["id"], row["stamp"], custom_signature(config), int(bool(engine.faces))))
    filetime.restore_access(row["path"], st)
    return True


def child_command():
    """인식 프로세스를 띄우는 명령. exe면 같은 exe, 소스면 파이썬으로 desktop.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--recognize"]
    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop.py"), "--recognize"]


running = [None]  # 지금(또는 마지막으로) 띄운 인식 프로세스. 창을 닫을 때 남아 있으면 desktop.py가 끝낸다.


def end():
    """인식 프로세스가 아직 돌면 끝낸다(모델을 올리는 동안은 멈추라는 표시를 못 본다)."""
    if running[0] is not None and running[0].poll() is None:
        running[0].kill()


def run(db):
    """색인 단계(본 프로그램 쪽): 인식 프로세스를 띄워 진행을 index.status에 옮기고, index.stop이 켜지면 멈추라고 알린다."""
    if not models.ready("clip"):
        raise ValueError("사진·동영상 인식 모델이 없습니다. 설정 → 사진·동영상 인식에서 [모델 받기]를 누르세요.")
    index.save_now(db, force=True)  # 앞 단계에서 쓴 것을 저장해 두어야 인식 프로세스가 본다.
    index.status.update(step="사진·동영상 인식", done=0, total=0, current="모델을 올리는 중")
    stop_file = settings.folder() / STOP
    stop_file.unlink(missing_ok=True)  # 지난번의 표시 파일(이 앱이 만든 것)
    child = running[0] = subprocess.Popen(child_command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, encoding="utf-8",
                                          creationflags=media.NO_WINDOW)

    def tell_stop():  # 인식 프로세스가 끝나면 이 스레드도 끝난다.
        while child.poll() is None:
            if index.stop.wait(0.5):
                stop_file.touch()
                return

    threading.Thread(target=tell_stop, daemon=True).start()
    error = ""
    for line in child.stdout:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "error" in message:
            error = message["error"]
        else:
            index.status.update(message)
    child.wait()
    stop_file.unlink(missing_ok=True)
    if error or child.returncode:
        raise ValueError(error or f"사진·동영상 인식이 끝나지 못했습니다(코드 {child.returncode}).")


def child_main():
    """인식 프로세스(desktop.py --recognize): 표시 파일(STOP)이 생기는지 보며 work를 하고, 진행을 한 줄씩 JSON으로 알린다."""
    sys.stdout.reconfigure(encoding="utf-8")
    stop_file = settings.folder() / STOP

    def watch():
        while not index.stop.wait(0.5):
            if stop_file.exists():
                index.stop.set()

    threading.Thread(target=watch, daemon=True).start()

    def report(**changes):
        print(json.dumps(changes, ensure_ascii=False), flush=True)

    db = index.connect()
    try:
        work(db, report)
    except Exception as e:  # 본 프로그램이 화면에 알린다.
        report(error=str(e))
    finally:
        db.close()


def work(db, report):
    """아직 인식하지 않았거나, 그 뒤에 바뀌었거나, 내 분류 모델·얼굴 설정이 바뀐 사진·동영상을 인식한다. report(진행 바뀐 것)."""
    if not models.ready("clip"):
        raise ValueError("사진·동영상 인식 모델이 없습니다. 설정 → 사진·동영상 인식에서 [모델 받기]를 누르세요.")
    config = settings.load()
    faces_on = bool(config.get("face_on", True)) and models.ready("face")
    # 클라우드 전용만 남았으면 모델(약 400MB)을 올리지 않는다. 이 PC에 있는 것을 하나 찾을 때까지만 본다
    # (모두 미리 stat하면 사진 20만 장에 38초였다). 나머지 클라우드 전용·사라진 파일은 recognize_one이 건너뛴다.
    rows = todo(db, custom_signature(config), faces_on)
    here = any(not index.stop.is_set() and local(row["path"]) for row in rows)
    report(total=len(rows) if here else 0, done=0)
    if not here:
        return
    engine = Engine(config)
    try:
        centers = people(db) if engine.faces else None  # People
        for n, row in enumerate(rows):
            if index.stop.is_set():
                break
            report(current=row["path"], done=n)
            index.release(db)
            try:
                recognize_one(db, engine, row, config, centers)
            except OSError:
                continue
        report(done=len(rows), current="")
    finally:
        engine.close()
        del engine
        gc.collect()
    index.save_now(db, force=True)


def forget_faces(db):
    """얼굴 정보를 모두 지운다(설정에서 얼굴 찾기를 끌 때). 다시 켜면 다음 인식 때 새로 만든다."""
    db.execute("delete from faces")
    db.execute("update recognized set faces = 0")
