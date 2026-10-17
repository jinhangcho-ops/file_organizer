"""화면이 부르는 Flask 서버. desktop.py가 127.0.0.1의 빈 포트에 띄우고, 열쇠(KEY)를 아는 프로그램 창의 요청만 받는다."""
import os
import secrets
import subprocess

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import actions
import drives
import exclude
import index
import kinds
import organize
import search
import settings
import tree

app = Flask(__name__, static_folder="static")
KEY = secrets.token_urlsafe(32)  # 실행할 때마다 새로 만든다. desktop.py가 창의 첫 주소에 넣는다.
ask_folder = None  # 폴더 고르는 대화상자(→ 경로 또는 None). desktop.py가 채운다.
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


def body():
    return request.get_json(silent=True) or {}


@app.get("/api/state")
def state():
    return jsonify(settings=settings.load(), drives=drives.list_drives(), known=drives.known_folders(), kinds=kinds.NAMES,
                   last_access=drives.last_access_on(), undo=len(organize.last()), index=index.status, counts=search.counts(),
                   places=tree.places(), exclude=exclude.load())


@app.post("/api/settings")
def save_settings():
    return jsonify(settings=settings.save(body()))


def pick_folder():
    if ask_folder is None:
        raise ValueError("폴더 고르기는 프로그램 창에서만 됩니다.")
    return ask_folder()


@app.post("/api/pick")
def pick():
    return jsonify(path=pick_folder())


@app.post("/api/organize/preview")
def preview():
    data = body()
    settings.save({"source": data.get("source"), "dest": data.get("dest")})  # 고른 곳을 기억한다(검사도 여기서).
    return jsonify(moves=organize.plan(data["source"], data["dest"]))


@app.post("/api/organize/run")
def organize_run():
    data = body()
    return jsonify(organize.run(data.get("source"), data.get("dest")))


@app.post("/api/organize/undo")
def organize_undo():
    return jsonify(organize.undo())


@app.route("/api/index", methods=["GET", "POST"])
def build_index():
    if request.method == "POST":
        paths = index.roots(settings.load())
        if not paths:
            raise ValueError("색인할 폴더가 없습니다.")
        if not index.start(paths):
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
    return jsonify(folders=tree.chain(request.args["path"]))


@app.post("/api/exclude")
def change_exclude():
    data = body()
    return jsonify(exclude=exclude.remove(data["path"]) if data.get("remove") else exclude.add(data["path"]))


def indexed_path():
    """요청의 path. 색인에 있고 지금도 있는 파일·폴더만 받는다(화면이 다루는 파일은 모두 색인에서 왔다)."""
    path = body().get("path") or ""
    if not index.known(path) or not os.path.exists(path):
        raise ValueError(f"색인에 없는 경로입니다. 색인을 갱신하세요: {path}")
    return path


@app.post("/api/move")
def move():
    """폴더 고르기 창으로 고른 곳에 옮긴다. 고르지 않으면 path가 null."""
    path = indexed_path()
    folder = pick_folder()
    return jsonify(path=folder and actions.move_to(path, folder))


@app.post("/api/rename")
def rename():
    return jsonify(path=actions.rename(indexed_path(), body().get("name")))


@app.post("/api/trash")
def trash():
    actions.trash(indexed_path())
    return jsonify(ok=True)


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
