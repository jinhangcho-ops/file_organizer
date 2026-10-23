"""화면이 부르는 Flask 서버. desktop.py가 127.0.0.1의 빈 포트에 띄우고, 열쇠(KEY)를 아는 프로그램 창의 요청만 받는다."""
import os
import secrets
import subprocess

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import actions
import archive
import blacklist
import browse
import drives
import exclude
import fence
import index
import jobs
import kinds
import lock
import models
import organize
import protect
import recognize
import search
import settings
import tree

app = Flask(__name__, static_folder="static")
KEY = secrets.token_urlsafe(32)  # 실행할 때마다 새로 만든다. desktop.py가 창의 첫 주소에 넣는다.
window_control = None  # 창 동작(동작, 값 → 답). desktop.py가 채운다.
lock_target = ""  # 잠금 창이 다룰 폴더(본 창의 폴더 메뉴 [잠금…]으로 연 경우). 주소에 넣지 않고 여기 둔다.
# 제목 줄 없는 본 창의 버튼·크기 조절, lock: 잠금 창 열기(path: 다룰 폴더), refresh: 본 창의 목록·숫자 다시 읽기(잠금 창이 바꾼 뒤)
WINDOW_ACTIONS = {"minimize", "toggle", "close", "bounds", "place", "lock", "refresh"}
CSP = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:"


@app.before_request
def only_window():
    """창은 첫 주소의 key로 들어와 쿠키를 받고, 그 뒤로는 쿠키로 확인한다. 열쇠가 없으면 화면·파일·API 모두 403."""
    given = request.args.get("key") or request.cookies.get("key", "")
    if not secrets.compare_digest(given.encode(), KEY.encode()):
        abort(403, "프로그램 창에서만 열 수 있습니다.")


@app.after_request
def headers(response):
    response.headers["Content-Security-Policy"] = CSP
    return response


@app.errorhandler(Exception)
def on_error(e):
    if isinstance(e, HTTPException):
        return jsonify(error=e.description), e.code
    if isinstance(e, OSError):
        return jsonify(error=f"{e.strerror or e}: {e.filename or ''}".rstrip(": ")), 400
    if isinstance(e, ValueError):
        return jsonify(error=str(e)), 400
    raise e


@app.get("/")
def page():
    response = send_from_directory(app.static_folder, "index.html")
    response.set_cookie("key", KEY, httponly=True, samesite="Strict")
    return response


@app.get("/lock")
def lock_page():
    """잠금 창(화면 캡처·공유에서 빠지는 따로 뜨는 창)의 화면. 잠금 관련 화면은 모두 여기에만 있다."""
    response = send_from_directory(app.static_folder, "lock.html")
    response.set_cookie("key", KEY, httponly=True, samesite="Strict")
    return response

def body():
    return request.get_json(silent=True) or {}


@app.get("/api/state")
def state():
    return jsonify(settings=settings.load(), drives=drives.list_drives(), known=drives.known_folders(), kinds=kinds.NAMES,
                   last_access=drives.last_access_on(), undo=len(organize.last()), index=index.status, counts=search.counts(),
                   places=tree.places(), exclude=exclude.load(), blacklist=blacklist.load(), protected=protect.load(), admin=blacklist.is_admin(),
                   panes=settings.PANES,
                   steps=settings.STEPS, step_list=settings.step_list(settings.load()), models=models.status(), labels=recognize.LABELS,
                   custom_count=recognize.custom_count(settings.load()["custom_model_path"]), numbers={name: {"min": low, "max": high} for name, (low, high, _) in settings.NUMBERS.items()})


@app.post("/api/settings")
def save_settings():
    return jsonify(settings=settings.save(body()))


@app.get("/api/browse")
def browse_folder():
    """앱 안의 폴더·파일 고르기 창: path 바로 아래(없으면 디스크·알려진 폴더). ext가 있으면 그 확장자의 파일도."""
    return jsonify(browse.listing(request.args.get("path", ""), request.args.get("ext") or None))


@app.post("/api/mkdir")
def make_folder():
    """앱 안의 고르기 창의 [새 폴더]."""
    data = body()
    return jsonify(path=browse.make_folder(data.get("path"), data.get("name")))


@app.post("/api/organize/preview")
def preview():
    data = body()
    settings.save({"source": data.get("source"), "dest": data.get("dest")})  # 고른 곳을 기억한다(검사도 여기서).
    return jsonify(moves=organize.plan(data["source"], data["dest"]))


@app.post("/api/organize/run")
def organize_run():
    data = body()
    return jsonify(jobs.start("정리", lambda progress: organize.run(data.get("source"), data.get("dest"), progress)))


@app.post("/api/organize/undo")
def organize_undo():
    return jsonify(jobs.start("되돌리기", organize.undo))


@app.get("/api/job")
def job():
    """뒤에서 도는 작업(정리, 옮기기, 휴지통, 보관)의 진행. 화면이 끝날 때까지 읽는다."""
    return jsonify(jobs.status)


@app.post("/api/chip")
def chip():
    """종류 칩을 누른 횟수를 센다(많이 누른 칩을 왼쪽에)."""
    return jsonify(settings=settings.count_chip(body().get("kind")))


@app.post("/api/archive/preview")
def archive_preview():
    data = body()
    config = settings.save({"archive_dest": data.get("dest"), "cold_days": data.get("days")})
    return jsonify(archive.preview(config["cold_days"], config["archive_dest"]))


@app.post("/api/archive/run")
def archive_run():
    data = body()
    if data.get("confirm") is not True:
        raise ValueError("확인한 뒤에만 보관합니다.")
    config = settings.save({"archive_dest": data.get("dest"), "cold_days": data.get("days")})
    return jsonify(jobs.start("보관", lambda progress: archive.run(config["archive_dest"], config["cold_days"], progress)))


@app.route("/api/index", methods=["GET", "POST"])
def build_index():
    """POST: 색인을 시작한다. only가 있으면 그 단계 하나만(설정의 [이것만 하기]) 한다."""
    if request.method == "POST":
        only = body().get("only")
        if only is not None and only not in settings.STEPS:
            raise ValueError(f"모르는 색인 단계입니다: {only}")
        paths = index.roots(settings.load())
        if not paths:
            raise ValueError("색인할 폴더가 없습니다.")
        if not index.start(paths, only):
            raise ValueError("이미 색인하는 중입니다.")
    return jsonify(index.status)


@app.post("/api/index/stop")
def stop_index():
    """돌고 있는 색인을 지금 파일까지만 하고 멈추게 한다. 돌고 있지 않으면 아무 일도 하지 않는다."""
    index.stop.set()
    return jsonify(index.status)


@app.post("/api/files")
def files():
    return jsonify(search.listing(body()))


@app.get("/api/chain")
def chain():
    """파일(folder=1이면 그 폴더 자신)까지의 폴더들."""
    return jsonify(folders=tree.chain(request.args["path"], request.args.get("folder") == "1"))


@app.get("/api/same")
def same():
    """상세에 보여 줄 내용이 같은 파일과 이름이 같은 파일."""
    return jsonify(search.same(checked(request.args.get("path", ""))))


@app.get("/api/preview")
def file_preview():
    """상세의 내용 미리보기(색인이 읽어 둔 글자의 앞부분)."""
    return jsonify(search.preview(checked(request.args.get("path", ""))))


@app.get("/api/recognized")
def recognized():
    """상세의 추정 종류와 같은 사람(사진·동영상 인식의 결과)."""
    return jsonify(search.recognized(checked(request.args.get("path", ""))))


@app.post("/api/models/download")
def download_models():
    """인식 모델을 받는다(groups: models.FILES의 이름들). 뒤의 작업으로 돌리고 진행은 /api/job으로 본다."""
    groups = body().get("groups")
    if not isinstance(groups, list) or not groups or any(g not in models.FILES for g in groups):
        raise ValueError("받을 모델을 고르세요.")
    return jsonify(job=jobs.start("모델 받기", lambda progress: models.download(groups, progress)))


@app.post("/api/custom_model")
def custom_model():
    """내 분류 모델 파일을 정하거나(path) 뺀다(""). 분류 수에 맞춰 분류 설정을 늘리거나 줄인다(있던 이름·기준은 둔다)."""
    path = body().get("path")
    config = settings.save({"custom_model_path": path if isinstance(path, str) else None})
    count = recognize.custom_count(config["custom_model_path"]) if config["custom_model_path"] else 0
    labels = config["custom_labels"][:count]
    labels += [{"name": f"분류 {i + 1}", "threshold": 80, "show": False} for i in range(len(labels), count)]
    return jsonify(settings=settings.save({"custom_labels": labels}), custom_count=count)


@app.post("/api/faces/forget")
def forget_faces():
    """얼굴 찾기를 끄고 모아 둔 얼굴 정보를 모두 지운다(색인 파일 안의 것만, 사진은 그대로)."""
    not_busy()
    db = index.connect()
    try:
        recognize.forget_faces(db)
        db.commit()
    finally:
        db.close()
    return jsonify(settings=settings.save({"face_on": False}))


@app.post("/api/window")
def window():
    """화면이 그린 제목 줄의 버튼(최소화·최대화·닫기)과 가장자리 크기 조절. bounds는 지금 창의 위치·크기, place는 위치·크기 바꾸기."""
    data = body()
    action = data.get("action")
    if action not in WINDOW_ACTIONS:
        raise ValueError(f"모르는 창 동작입니다: {action}")
    if window_control is None:
        raise ValueError("프로그램 창에서만 됩니다.")
    return jsonify(window_control(action, data) or {})


@app.get("/api/tree")
def folder_tree():
    """폴더의 바로 아래 폴더들. 왼쪽 폴더 나무가 펼칠 때 부른다."""
    return jsonify(folders=tree.children(request.args["path"]))


@app.post("/api/exclude")
def change_exclude():
    data = body()
    return jsonify(exclude=exclude.remove(data["path"]) if data.get("remove") else exclude.add(data["path"]))


def checked(path, found=None, fences=None):
    """색인에 있고 지금도 있는 파일·폴더만 받는다(화면이 다루는 파일은 모두 색인에서 왔다). 차단·잠금 폴더 안은 거부한다.
    여러 개를 볼 때는 index.look의 결과(found)와 fence.keys()를 한 번만 만들어 넘긴다."""
    if not isinstance(path, str) or not (found or index.look([path]))[path][0] or not os.path.exists(path):
        raise ValueError(f"색인에 없는 경로입니다. 색인을 갱신하세요: {path}")
    return fence.refuse(path, fences)


def indexed_path():
    return checked(body().get("path") or "")


def indexed_paths(movable=True):
    """요청의 paths(여러 개). movable이면 색인한 맨 위 폴더는 받지 않는다(통째로 옮기거나 지우지 않는다)."""
    paths = body().get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
        raise ValueError("고른 파일이 없습니다.")
    found, fences = index.look(paths), fence.keys()  # 연결 하나, 차단·잠금 목록 한 번으로 모두 본다.
    for path in paths:
        checked(path, found, fences)
        if movable and found[path][1]:
            raise ValueError(f"색인한 맨 위 폴더는 옮기거나 지울 수 없습니다: {path}")
    return list(dict.fromkeys(paths))


@app.post("/api/move")
def move():
    """파일·폴더들을 folder(나무에 끌어 놓은 폴더나 앱 안의 고르기 창으로 고른 폴더)로 옮긴다."""
    paths = indexed_paths()
    folder = settings.folder_text(body().get("folder"), "옮길 곳")  # 있는 폴더이고 차단·잠금 폴더가 아니어야 한다.
    for path in paths:  # 잠금 폴더를 품은 폴더는 같은 디스크 안에서만 옮긴다(위치는 lock.moved가 고친다).
        fence.refuse_holding(path, drives.same_drive(path, folder), moving=True)
        lock.refuse_clash(path, os.path.join(folder, os.path.basename(path)))
    return jsonify(job=jobs.start("옮기기", lambda progress: actions.move_many(paths, folder, progress)))


@app.post("/api/rename")
def rename():
    path = indexed_path()
    fence.refuse_holding(path, True)
    lock.refuse_clash(path, os.path.join(os.path.dirname(path), str(body().get("name") or "").strip()))
    return jsonify(path=actions.rename(path, body().get("name")))


@app.post("/api/trash")
def trash():
    """파일·폴더들을 휴지통으로. 화면의 확인 창에서 [확인]을 눌러 confirm이 true일 때만 한다."""
    paths = indexed_paths()
    if body().get("confirm") is not True:
        raise ValueError("확인한 뒤에만 휴지통으로 보냅니다.")
    for path in paths:
        fence.refuse_holding(path, False)
    return jsonify(job=jobs.start("휴지통으로 보내기", lambda progress: actions.trash_many(paths, progress)))


def not_busy():
    """색인이나 뒤의 작업(옮기기·휴지통 등)이 도는 동안에는 차단·구조 보호·잠금을 바꾸지 않는다.
    색인은 시작할 때 읽은 목록으로 그 폴더를 다시 넣고, 작업은 시작할 때 검사를 마쳤기 때문이다(그사이 안의 폴더를 잠그면 휴지통으로 함께 간다)."""
    if index.status["running"]:
        raise ValueError("색인하는 중입니다. 색인이 끝난 뒤(또는 [멈추기] 뒤)에 하세요.")
    if jobs.status["running"]:
        raise ValueError(f"'{jobs.status['name']}' 작업이 끝난 뒤에 하세요.")


@app.post("/api/blacklist")
def change_blacklist():
    """차단 폴더 넣기·빼기(관리자 권한으로 실행했을 때만). 넣으면 그 아래의 색인 기록을 바로 지운다(실제 파일은 그대로)."""
    data = body()
    path = data.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("폴더를 고르세요.")
    not_busy()
    if data.get("remove"):
        return jsonify(blacklist=blacklist.remove(path))
    paths = blacklist.add(path, {exclude.key(p) for p in lock.folders()})
    index.removed([path])
    return jsonify(blacklist=paths)


@app.post("/api/protect")
def change_protect():
    """구조 보호 폴더 넣기·빼기(관리자 권한으로 실행했을 때만). 색인은 그대로 둔다."""
    data = body()
    path = data.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("폴더를 고르세요.")
    not_busy()
    return jsonify(protected=protect.remove(path) if data.get("remove") else protect.add(path))


@app.get("/api/blacklist/candidates")
def blacklist_candidates():
    """색인하는 드라이브에서 찾은 게임 폴더(아직 차단하지 않은 것)."""
    return jsonify(folders=blacklist.candidates(index.roots(settings.load())))


@app.post("/api/lock/password")
def lock_password():
    """잠금 비밀번호를 정하거나 바꾼다(바꿀 때는 지금 비밀번호가 맞아야 한다)."""
    data = body()
    lock.set_password(data.get("new"), data.get("old"))
    return jsonify(lock_password=True)


@app.post("/api/lock")
def lock_folder():
    """색인에 있는 폴더를 제자리에서 잠그거나(vault가 아니면) 금고로 모은다(vault: true). 색인한 맨 위 폴더는 다루지 않는다.
    끝나면 그 아래의 색인 기록을 지운다."""
    data = body()
    not_busy()
    path = indexed_path()
    if not os.path.isdir(path) or index.is_root(path):
        raise ValueError(f"잠글 수 없는 폴더입니다(색인한 맨 위 폴더나 파일): {path}")
    blocked = {exclude.key(p) for p in blacklist.load()}
    if data.get("vault"):  # 금고로 모으기는 폴더를 옮긴다.
        fence.refuse_holding(path, True, moving=True)
    (lock.collect if data.get("vault") else lock.lock)(path, data.get("password"), blocked)
    index.removed([path])
    return jsonify(locks=lock.listing())


@app.post("/api/lock/vault")
def lock_vault():
    """금고 위치를 place(잠금 창의 고르기 창으로 고른 폴더) 안으로 정한다."""
    data = body()
    return jsonify(vault=lock.set_vault(data.get("place"), data.get("password")))


@app.get("/api/lock/state")
def lock_state():
    """잠금 창이 그리는 것: 잠금 목록, 금고, 비밀번호를 정했는지, 화면 색."""
    return jsonify(locks=lock.listing(), vault=lock.vault_info(), password=lock.has_password(), theme=settings.load()["theme"], target=lock_target)


@app.post("/api/lock/open")
def lock_open():
    """비밀번호가 맞으면 잠금 폴더(금고 안의 것이면 금고)를 잠시 풀고 탐색기로 연다."""
    data = body()
    path = lock.open_folder(data.get("path"), data.get("password"))
    os.startfile(path)
    return jsonify(locks=lock.listing())


@app.post("/api/lock/take_out")
def lock_take_out():
    """금고에서 꺼내 원래 자리로. 원래 위 폴더가 없으면 need_folder로 알리고, 화면이 고른 folder로 다시 부른다."""
    data = body()
    try:
        path = lock.take_out(data.get("path"), data.get("password"), data.get("folder") or None)
    except lock.NoPlace as e:
        return jsonify(need_folder=str(e))
    return jsonify(path=path, locks=lock.listing())


@app.post("/api/lock/close")
def lock_close():
    lock.close(body().get("path"))
    return jsonify(locks=lock.listing())


@app.post("/api/lock/release")
def lock_release():
    """잠금을 아주 푼다. 다음 색인부터 다시 목록에 나온다."""
    data = body()
    lock.release(data.get("path"), data.get("password"))
    return jsonify(locks=lock.listing())


@app.post("/api/open")
def open_path():
    """색인에 있는 파일·폴더만 연다. reveal이면 탐색기에서 그 파일을 골라 보여 준다."""
    data = body()
    path = indexed_path()
    if data.get("reveal"):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        os.startfile(path)
    return jsonify(ok=True)
