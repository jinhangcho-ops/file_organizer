"""사진·동영상 인식에 쓰는 모델 파일: 받는 곳·크기·SHA-256과 받기. exe에는 넣지 않고, 사용자가 [모델 받기]를 누를 때 받는다.
자리는 %LOCALAPPDATA%\\file_organizer\\models(설정 폴더와 따로: 크고 다시 받을 수 있다). 내 분류 모델은 사용자가 고른 파일을 쓴다(recognize)."""
import hashlib
import os
import urllib.request
from pathlib import Path

HF = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
# 묶음 → [(자리, 받는 곳, 바이트, SHA-256)]. clip: 종류 인식(OpenAI CLIP ViT-B/32를 ONNX로 바꾼 것), face: 얼굴 찾기(YuNet)·같은 사람(SFace).
FILES = {
    "clip": [
        ("clip/vision.onnx", HF + "onnx/vision_model_quantized.onnx", 89117001, "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299"),
        ("clip/text.onnx", HF + "onnx/text_model_quantized.onnx", 64504507, "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a"),
        ("clip/tokenizer.json", HF + "tokenizer.json", 2224119, "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd"),
    ],
    "face": [
        ("face/yunet.onnx", ZOO + "face_detection_yunet/face_detection_yunet_2023mar.onnx", 232589,
         "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
        ("face/sface.onnx", ZOO + "face_recognition_sface/face_recognition_sface_2021dec.onnx", 38696353,
         "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
    ],
}
NAMES = {"clip": "종류 인식", "face": "같은 사람 찾기"}
CHUNK = 1024 * 1024


def folder():
    """모델 자리. 테스트는 FILE_ORGANIZER_MODELS로 임시 폴더를 준다."""
    return Path(os.environ.get("FILE_ORGANIZER_MODELS") or Path(os.environ.get("LOCALAPPDATA") or Path.home(), "file_organizer", "models"))


def path(rel):
    return str(folder() / rel)


def ready(group):
    """그 묶음의 파일이 모두 있고 크기가 맞는지(해시는 받을 때 확인했다)."""
    return all(os.path.isfile(path(rel)) and os.path.getsize(path(rel)) == size for rel, _, size, _ in FILES[group])


def status():
    """화면용: [{"name", "title", "ready", "bytes"}]"""
    return [{"name": group, "title": NAMES[group], "ready": ready(group), "bytes": sum(f[2] for f in files)} for group, files in FILES.items()]


def fetch(url, target, progress):
    """url을 target에 받는다(검사는 이것을 바꿔 끼운다). progress(받은 바이트)."""
    with urllib.request.urlopen(url, timeout=60) as answer, open(target, "wb") as out:
        done = 0
        while chunk := answer.read(CHUNK):
            out.write(chunk)
            done += len(chunk)
            progress(done)


def download(groups, progress=lambda *args: None):
    """groups의 빠진 파일을 받는다. 임시 이름으로 받아 크기·SHA-256이 맞을 때만 제자리로 옮긴다(틀리면 지우고 알린다).
    {"done": [자리], "failed": [{"from", "error"}]}"""
    todo = [f for group in groups for f in FILES[group] if not (os.path.isfile(path(f[0])) and os.path.getsize(path(f[0])) == f[2])]
    total = sum(f[2] for f in todo)
    done, failed, before = [], [], 0
    for rel, url, size, digest in todo:
        target = Path(path(rel))
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".part")
        try:
            fetch(url, temp, lambda got: progress(before + got, total, rel))
            if temp.stat().st_size != size or hashlib.sha256(temp.read_bytes()).hexdigest() != digest:
                raise OSError("받은 파일이 예상과 다릅니다(크기나 SHA-256).")
            os.replace(temp, target)
            done.append(rel)
        except OSError as e:
            failed.append({"from": rel, "error": getattr(e, "strerror", None) or str(e)})
        finally:
            if temp.exists():
                temp.unlink()  # 받다 만 임시 파일(앱이 받은 것, 사용자 파일 아님)
        before += size
    progress(total, total)
    return {"done": done, "failed": failed}
