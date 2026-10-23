"""가운데 목록: 위치(전체·폴더), 모아 보기(중복 의심·오래 안 연·최근 받은·최근 고친 파일), 검색 조건, 종류, 정렬. 색인을 읽는다.
- 중복 의심: 지문(index.fingerprint)이 같은 파일이 둘 이상.
- 오래 안 연 파일: 연 시각과 고친 시각 중 늦은 쪽이 기간보다 오래됨(기록이 꺼져 있던 동안은 연 시각이 오래된 채로 남기 때문).
- 최근 받은 파일: 다운로드 표시가 있고 만든 시각(받은 시각)이 기간 안.
모아 보기에는 예외(exclude)를 적용한다. 위치·검색에는 적용하지 않는다."""
import datetime
import os
import re
import time

import exclude
import extract
import index
import kinds
import settings

LIMIT = 500  # 한 번에 보여 줄 최대 줄 수
DAY = 86400
MB = 1024 * 1024
# 같은 파일의 종류(센 쪽부터): 바이트가 같음, 문서 정보만 다른 문서, 글자가 같은 문서, 보이는 내용이 비슷한 사진·동영상(media).
SAME = {
    "bytes": "f.hash in (select hash from files where hash is not null group by hash having count(*) > 1)",
    "doc": "f.dochash in (select dochash from files where dochash <> '' group by dochash having count(*) > 1)",
    "text": "f.texthash in (select texthash from files where texthash <> '' group by texthash having count(*) > 1)",
    "media": "f.id in (select a from media_pair union select b from media_pair)",
}
DUPLICATE = "(" + " or ".join(SAME.values()) + ")"
# 줄마다 붙이는 표시는 그 줄의 지문으로 색인을 찾아본다(위 SAME처럼 표 전체를 묶어 세면 보여 줄 500줄에도 매번 1초 넘게 걸린다).
SAME_ROW = {
    "bytes": "f.hash is not null and exists(select 1 from files g where g.hash = f.hash and g.id <> f.id)",
    "doc": "f.dochash <> '' and exists(select 1 from files g where g.dochash = f.dochash and g.id <> f.id)",
    "text": "f.texthash <> '' and exists(select 1 from files g where g.texthash = f.texthash and g.id <> f.id)",
    "media": "(exists(select 1 from media_pair where a = f.id) or exists(select 1 from media_pair where b = f.id))",
}
DUPLICATE_KIND = "case " + " ".join(f"when {condition} then '{name}'" for name, condition in SAME_ROW.items()) + " end"
SEEN = "max(f.accessed, f.modified)"
VIEWS = {  # 이름 → (조건, 기간 설정, 날짜 칸)
    "duplicates": (DUPLICATE, None, "f.modified"),
    "stale": (f"{SEEN} < ?", "stale_days", SEEN),
    "downloaded": ("f.downloaded = 1 and f.created >= ?", "recent_days", "f.created"),
    "modified": ("f.modified >= ?", "recent_days", "f.modified"),
}
SORTS = {"name": "lower(f.name)", "kind": "f.kind", "size": "f.size"}  # 그 밖에 "date"는 보기마다 다른 날짜 칸
FIELDS = "f.path, f.name, f.ext, f.kind, f.size, f.created, f.modified, f.accessed, f.downloaded"
MEDIA_KINDS = ("사진", "이미지", "동영상")  # 추정 칩(tags)을 보여 주는 종류


def day(text, end=False):
    """'2026-10-17' → 그날 0시(end면 다음 날 0시)의 Unix 초."""
    try:
        start = datetime.datetime.strptime(text.strip(), "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"날짜는 2026-10-17 모양으로 쓰세요: {text}") from None
    return int((start + datetime.timedelta(days=1 if end else 0)).timestamp())


def number(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}는 숫자로 쓰세요: {value}") from None
    if number < 0:
        raise ValueError(f"{name}는 0 이상으로 쓰세요.")
    return number


def under(path):
    """폴더 path 아래(하위 폴더 포함) 파일의 조건과 값. key가 '폴더\' 이상 '폴더]' 미만인 범위로 비교해 key 색인을 탄다
    (바이트 순서로 '\' 바로 다음 글자가 ']'라서, 이 범위는 정확히 '폴더\'로 시작하는 것들이다)."""
    prefix = os.path.join(exclude.key(path), "")
    return "f.key >= ? and f.key < ?", [prefix, prefix[:-1] + "]"]


def conditions(query, config):
    """query에서 종류를 뺀 조건들(종류별 개수를 셀 때 종류 조건 없이 세려고 나눈다). (조건 목록, 값 목록, 날짜 칸)"""
    parts, values = [], []
    view = query.get("view")
    date = "f.modified"
    if view:
        if view not in VIEWS:
            raise ValueError(f"모르는 목록입니다: {view}")
        condition, setting, date = VIEWS[view]
        parts.append(condition)
        if setting:
            values.append(int(time.time()) - config[setting] * DAY)
        rule, more = exclude.where(exclude.load())
        if rule:
            parts.append(rule)
            values += more
    if query.get("folder") and query.get("direct"):  # 탐색기처럼: 그 폴더에 바로 든 파일만(아래 폴더는 화면이 폴더 줄로 보여 준다)
        parts.append("f.folder = ?")
        values.append(exclude.key(query["folder"]))
    elif query.get("folder"):
        part, more = under(query["folder"])
        parts.append(part)
        values += more
    if query.get("drive"):  # 왼쪽에서 고른 드라이브('모든 파일'을 볼 때만 화면이 보낸다)
        if not re.fullmatch(r"[A-Za-z]:\\", str(query["drive"])):
            raise ValueError(f"드라이브는 'D:\\' 모양으로 쓰세요: {query['drive']}")
        part, more = under(query["drive"])
        parts.append(part)
        values += more
    if query.get("tag"):
        part, more = tag_condition(query["tag"], config)
        parts.append(part)
        values += more
    if (query.get("name") or "").strip():  # 이름이나 폴더 이름
        parts.append("(instr(lower(f.name), lower(?)) > 0 or instr(f.folder, lower(?)) > 0)")
        values += [query["name"].strip()] * 2
    if (query.get("date_from") or "").strip():
        parts.append("f.modified >= ?")
        values.append(day(query["date_from"]))
    if (query.get("date_to") or "").strip():
        parts.append("f.modified < ?")
        values.append(day(query["date_to"], end=True))
    if str(query.get("size_min") or "").strip():
        parts.append("f.size >= ?")
        values.append(number(query["size_min"], "최소 크기") * MB)
    if str(query.get("size_max") or "").strip():
        parts.append("f.size <= ?")
        values.append(number(query["size_max"], "최대 크기") * MB)
    text = (query.get("text") or "").strip()
    if text:
        if len(text) >= 3:  # trigram 색인으로 찾는다(대소문자 무시, 글자 그대로).
            parts.append("f.id in (select rowid from body where body match ?)")
            values.append('"' + text.replace('"', '""') + '"')
        else:  # 두 글자 이하는 색인을 쓸 수 없어 내용을 훑는다.
            parts.append("f.id in (select rowid from body where instr(lower(text), lower(?)) > 0)")
            values.append(text)
    return parts, values, date


def tag_condition(tag, config):
    """추정 칩 → 조건. 'group:animal'·'animal:dog'·'food:pizza'(인식한 종류), 'person:3'(같은 사람), 'custom:0'(내 분류 모델, 기준 점수 이상)."""
    kind, _, label = str(tag).partition(":")
    if kind == "person" and label.isdigit():
        return "f.id in (select id from faces where person = ?)", [int(label)]
    if kind == "custom" and label.isdigit():
        labels = config.get("custom_labels") or []
        threshold = labels[int(label)]["threshold"] if int(label) < len(labels) else 50
        return "f.id in (select id from tags where kind = 'custom' and label = ? and score >= ?)", [label, threshold]
    if kind in ("group", "animal", "food") and label:
        return "f.id in (select id from tags where kind = ? and label = ?)", [kind, label]
    raise ValueError(f"모르는 추정 칩입니다: {tag}")


def tag_counts(db, parts, values, config):
    """지금 조건(종류·확장자 포함)의 파일에서 추정 칩마다 몇 개인지: [[칩, 개수], …](많은 순). 칩으로 보기를 끈 내 분류 모델 분류는 뺀다."""
    kinds_only = where(parts + ["t.kind in ('group', 'animal', 'food')"])
    found = [[f"{kind}:{label}", n] for kind, label, n in db.execute(
        f"select t.kind, t.label, count(distinct t.id) n from tags t join files f on f.id = t.id{kinds_only} group by t.kind, t.label", values)]
    for person, n in db.execute(f"select p.person, count(distinct p.id) from faces p join files f on f.id = p.id{where(parts)} group by p.person", values):
        found.append([f"person:{person}", n])
    for i, label in enumerate(config.get("custom_labels") or []):
        if label["show"]:
            n = db.execute(f"select count(*) from files f{where(parts + [tag_condition(f'custom:{i}', config)[0]])}",
                           values + tag_condition(f"custom:{i}", config)[1]).fetchone()[0]
            found.append([f"custom:{i}", n])
    return sorted((item for item in found if item[1]), key=lambda item: -item[1])


def where(parts):
    return " where " + " and ".join(parts) if parts else ""


def listing(query):
    """목록 한 쪽. {"rows", "more", "total", "bytes", "kinds": [[종류, 개수], …](모든 종류, 칩 순서 chip_order대로), "views": [[목록, 개수], …],
    "exts": [[확장자, 개수], …](종류를 골랐을 때 그 종류 안의 확장자, 많은 순)}.
    종류별 개수는 종류·확장자 조건 없이, 확장자별 개수는 확장자 조건 없이, 목록(view)별 개수는 그 목록 조건만 바꿔 센다(나머지 조건은 그대로).
    줄마다 duplicate(같은 파일의 종류: SAME의 이름)·stale 태그와 위치 이름(place)을 붙인다.
    same_as(경로)가 있으면 그 파일과 같은 파일들만(그 파일이 맨 위) 보여 주고, 줄마다 why(그 파일과 같은 까닭)·score를 붙인다.
    rows_only면 줄(rows·more)만 돌려준다(정렬만 바꿀 때: 개수는 바뀌지 않으므로 화면이 직전 값을 쓴다).
    정렬의 2차 기준은 id라서 이름·크기·날짜 색인(B-트리)을 그대로 타 앞의 몇 줄만 읽는다."""
    config = settings.load()
    parts, values, date = conditions(query, config)
    pairs = {}
    if query.get("same_as"):
        db = index.connect()
        try:
            me, pairs = partners(db, exclude.key(query["same_as"]))
        finally:
            db.close()
        if me is None:
            raise ValueError(f"색인에 없는 파일입니다: {query['same_as']}")
        pairs = {me: ("self", None)} | pairs
        parts.append(f"f.id in ({', '.join('?' * len(pairs))})")
        values += list(pairs)
    kind = query.get("kind") or ""
    if kind and kind not in kinds.NAMES:
        raise ValueError(f"모르는 종류입니다: {kind}")
    ext = (query.get("ext") or "").lower()
    if ext and (not kind or kinds.of(ext) != kind):
        raise ValueError(f"{kind or '종류'}에 없는 확장자입니다: {ext}")
    sort = SORTS.get(query.get("sort"), date)
    order = "asc" if query.get("order") == "asc" else "desc"
    stale_since = int(time.time()) - config["stale_days"] * DAY
    by_kind = parts + (["f.kind = ?"] if kind else [])
    by_kind_values = values + ([kind] if kind else [])
    picked = by_kind + (["f.ext = ?"] if ext else [])
    picked_values = by_kind_values + ([ext] if ext else [])
    text = (query.get("text") or "").strip()
    snippet = ", (select substr(text, max(1, instr(lower(text), lower(?)) - 40), 120) from body where rowid = f.id) snippet" if text else ""
    db = index.connect()
    try:
        # 두 단계: 안쪽은 정렬해서 앞의 줄만 고르고(가벼운 칸만), 바깥은 그 줄에만 같은 파일 표시 같은 무거운 칸을 붙인다.
        # 한 번에 하면 색인을 못 타는 정렬(폴더별 묶기 등)에서 모든 줄의 무거운 칸을 먼저 계산해 25만 줄에 13초가 걸렸다.
        ordering = f"order by {first_keys(config, query)}{sort} {order}, f.id {order}"
        rows = db.execute(
            f"""select f.id, {FIELDS}, {date} date, {DUPLICATE_KIND} duplicate, ({SEEN} < ?) stale{snippet}
                from (select * from files f{where(picked)} {ordering} limit {LIMIT + 1}) f {ordering}""",
            [stale_since] + ([text] if text else []) + picked_values).fetchall()
        places = place_names(db)
        if query.get("rows_only"):
            return finish_rows(rows, places, pairs)
        counts = dict(db.execute(f"select f.kind, count(*) from files f{where(parts)} group by f.kind", values).fetchall())
        views = []
        for view in VIEWS:
            view_parts, view_values, _ = conditions(query | {"view": view}, config)
            view_parts += (["f.kind = ?"] if kind else []) + (["f.ext = ?"] if ext else [])
            view_values += ([kind] if kind else []) + ([ext] if ext else [])
            views.append([view, db.execute(f"select count(*) from files f{where(view_parts)}", view_values).fetchone()[0]])
        exts = db.execute(f"select f.ext, count(*) n from files f{where(by_kind)} group by f.ext order by n desc, f.ext",
                          by_kind_values).fetchall() if kind else []
        tags = tag_counts(db, picked, picked_values, config) if kind in MEDIA_KINDS else []
        total, size = db.execute(f"select count(*), coalesce(sum(f.size), 0) from files f{where(picked)}", picked_values).fetchone()
    finally:
        db.close()
    return finish_rows(rows, places, pairs) | {
        "total": total, "bytes": size, "kinds": [[name, counts.get(name, 0)] for name in chip_order(config)], "views": views,
        "exts": [list(row) for row in exts], "tags": tags}  # JSON 객체는 키가 정렬되므로 목록으로


INSTALLER = "설치파일"


def first_keys(config, query):
    """정렬 앞에 붙는 기준: 폴더별로 묶기(여러 폴더가 섞인 목록일 때, 같은 파일 보기는 빼고)와 설치파일 맨 위(묶으면 폴더 안에서 맨 위).
    이것이 붙으면 이름·크기·날짜 색인만으로 정렬할 수 없어 조금 느려진다(정렬할 줄 수만큼)."""
    keys = ["f.folder"] if config.get("list_group") == "folder" and not query.get("same_as") else []
    if config.get("pin_installers"):
        keys.append(f"(f.kind = '{INSTALLER}') desc")
    return "".join(key + ", " for key in keys)


def finish_rows(rows, places, pairs):
    """조회한 줄 → {"rows", "more"}: 위치 이름을 붙이고, same_as면 고른 파일을 맨 위에 두고 까닭을 붙인다."""
    rows = [dict(row) | {"place": place(row["path"], places)} for row in rows]
    if pairs:
        rows.sort(key=lambda row: pairs[row["id"]][0] != "self")  # 나머지 순서는 그대로
        for row in rows:
            row["why"], row["score"] = pairs[row["id"]]
    return {"rows": rows[:LIMIT], "more": len(rows) > LIMIT}


def chip_order(config):
    """종류 칩의 순서: 많이 누른 종류가 앞(settings의 chip_counts), 횟수가 같으면 종류 표의 순서."""
    used = config.get("chip_counts") or {}
    return sorted(kinds.NAMES, key=lambda name: -used.get(name, 0))


def place_names(db):
    """색인한 맨 위 폴더들: [(비교용 key + 구분자, 이름)]"""
    return [(os.path.join(row["key"], ""), row["name"]) for row in db.execute("select key, name from folders where parent is null")]


def place(path, places):
    key = exclude.key(path)
    return next((name for prefix, name in places if key.startswith(prefix)), "")


SAME_LIMIT = 50  # 상세에 보여 줄 같은 파일의 최대 수


def partners(db, key):
    """파일 key와 같은 파일들: (그 파일의 id 또는 None, {id: (까닭, 점수)}). 까닭은 SAME의 이름이고, 여러 까닭이면 센 쪽 하나.
    점수는 사진·동영상의 유사도(%)이고 나머지는 None."""
    me = db.execute("select id, hash, dochash, texthash from files where key = ?", (key,)).fetchone()
    if me is None:
        return None, {}
    found = {}
    for name, column in (("bytes", "hash"), ("doc", "dochash"), ("text", "texthash")):
        if me[column]:
            for (other,) in db.execute(f"select id from files where {column} = ? and id <> ?", (me[column], me["id"])):
                found.setdefault(other, (name, None))
    for other, score in db.execute("select case when a = ? then b else a end, score from media_pair where a = ? or b = ?", (me["id"],) * 3):
        found.setdefault(other, ("media", score))
    return me["id"], found


def same(path):
    """상세의 '내용이 같은 파일'(partners: 까닭 why·유사도 score와 함께)과 '이름이 같은 파일'(대소문자 무시, 내용은 다를 수 있음).
    각각 [{"path", "name", "size", "place", …}], 경로 순으로 SAME_LIMIT개까지."""
    key = exclude.key(path)
    db = index.connect()
    try:
        row = db.execute("select name from files where key = ?", (key,)).fetchone()
        if row is None:
            return {"content": [], "name": []}
        _, pairs = partners(db, key)
        columns = "id, path, name, size, modified"
        content = [dict(r) | {"why": pairs[r["id"]][0], "score": pairs[r["id"]][1]} for r in db.execute(
            f"select {columns} from files where id in ({', '.join('?' * len(pairs))}) order by lower(path) limit {SAME_LIMIT}", list(pairs))] if pairs else []
        name = [dict(r) for r in db.execute(
            f"select {columns} from files where lower(name) = lower(?) and key <> ? order by lower(path) limit {SAME_LIMIT}", (row["name"], key))]
        places = place_names(db)
    finally:
        db.close()
    return {k: [r | {"place": place(r["path"], places)} for r in rows] for k, rows in (("content", content), ("name", name))}


def recognized(path):
    """상세의 추정: {"tags": [[칩, 점수], …], "people": [사람 번호, …]}. 내 분류 모델은 칩으로 보기를 켠 분류만."""
    key = exclude.key(path)
    config = settings.load()
    labels = config.get("custom_labels") or []
    db = index.connect()
    try:
        row = db.execute("select id from files where key = ?", (key,)).fetchone()
        if row is None:
            return {"tags": [], "people": []}
        tags = [[f"{k}:{label}", score] for k, label, score in db.execute("select kind, label, score from tags where id = ? order by rowid", (row["id"],))
                if k != "custom" or (int(label) < len(labels) and labels[int(label)]["show"] and score >= labels[int(label)]["threshold"])]
        people = [p for (p,) in db.execute("select distinct person from faces where id = ? order by person", (row["id"],))]
    finally:
        db.close()
    return {"tags": tags, "people": people}


PREVIEW = 300  # 상세의 내용 미리보기 글자 수


def preview(path):
    """상세의 내용 미리보기: 색인이 읽어 둔 파일 글자의 앞부분(공백은 하나로, PREVIEW자, 넘치면 …).
    readable: 이 형식·크기의 글자를 읽는지(아니면 화면이 '읽지 않는 형식'이라고 알린다). 따로 저장하지 않고 그때 꺼낸다."""
    key = exclude.key(path)
    db = index.connect()
    try:
        row = db.execute("select ext, size from files where key = ?", (key,)).fetchone()
        body = db.execute("select substr(b.text, 1, ?) text from body b join files f on f.id = b.rowid where f.key = ?",
                          (PREVIEW * 4, key)).fetchone()
    finally:
        db.close()
    text = " ".join(body["text"].split()) if body else ""
    readable = bool(row) and extract.readable(row["ext"], row["size"])
    return {"text": text[:PREVIEW] + ("…" if len(text) > PREVIEW else ""), "readable": readable}


def counts():
    """사이드바의 숫자: 전체, 모아 보기마다의 파일 수."""
    config = settings.load()
    db = index.connect()
    try:
        found = {"all": db.execute("select count(*) from files").fetchone()[0]}
        for view in VIEWS:
            parts, values, _ = conditions({"view": view}, config)
            found[view] = db.execute(f"select count(*) from files f{where(parts)}", values).fetchone()[0]
        return found
    finally:
        db.close()
