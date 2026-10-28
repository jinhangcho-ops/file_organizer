"""가운데 목록: 위치(전체·폴더), 모아 보기(중복 의심·오래 안 연·최근 받은·최근 고친 파일), 검색 조건, 종류, 정렬. 색인을 읽는다.
- 중복 의심: 지문(index.fingerprint)이 같은 파일이 둘 이상.
- 오래 안 연 파일: 연 시각과 고친 시각 중 늦은 쪽이 기간보다 오래됨(기록이 꺼져 있던 동안은 연 시각이 오래된 채로 남기 때문).
- 최근 받은 파일: 다운로드 표시가 있고 만든 시각(받은 시각)이 기간 안.
모아 보기에는 예외(exclude)를 적용한다. 위치·검색에는 적용하지 않는다."""
import datetime
import functools
import json
import os
import time

from guard import exclude
from catalog import extract
from catalog import index
from app import kinds
from app import settings

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
# 중복 의심인 파일은 연결마다 한 번 임시 표에 모은다(same_files). 목록·종류별 개수·합계마다 세 가지 묶기를 다시 하면 30만 줄에 6.3초였다.
DUPLICATE = "f.id in (select id from temp.same_files)"
DUPLICATE_ONCE = "(" + " or ".join(SAME.values()) + ")"  # 한 번만 셀 때(사이드바 숫자): 임시 표를 만드는 것보다 빠르다
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
    "stale": (f"+{SEEN} < ?", "stale_days", SEEN),  # +: 날짜 색인을 타면 거의 모든 줄을 띄엄띄엄 읽어 더 느리다
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


WIDE = 10  # 걸리는 파일이 전체의 1/WIDE보다 많으면 색인을 따라가지 않고 표를 훑는다(폴더 범위·크기 조건).


def same_files(db):
    """이 연결의 임시 표 same_files에 중복 의심인 파일 id를 모은다(DUPLICATE를 쓰기 전에 부른다). 연결을 닫으면 사라진다."""
    db.execute("create temp table if not exists same_files(id integer primary key)")
    db.execute("insert or ignore into same_files select f.id from files f where " + DUPLICATE_ONCE)


def wide(db, sql, values, files=None):
    """sql(걸리는 줄을 고르는 select 1 …)이 전체 줄의 1/WIDE보다 많이 고르는지. 그만큼만 세어 보고 멈춘다.
    files: 전체 줄 수를 돌려주는 함수(목록 한 번에 여러 번 부를 때 한 번만 세게. 없으면 여기서 센다)."""
    # 줄 수로 센다: max(rowid)는 큰 폴더를 지운 뒤 id가 듬성듬성해지면 실제 줄 수보다 커서 넓은 조건도 좁다고 보았다.
    cap = (files() if files else db.execute("select count(*) from files").fetchone()[0]) // WIDE + 1
    return db.execute(f"select count(*) from ({sql} limit ?)", [*values, cap]).fetchone()[0] >= cap


def under(path, files=None):
    """폴더 path 아래(하위 폴더 포함) 파일의 조건과 값. key가 '폴더\' 이상 '폴더]' 미만인 범위로 비교해 key 색인을 탄다
    (바이트 순서로 '\' 바로 다음 글자가 ']'라서, 이 범위는 정확히 '폴더\'로 시작하는 것들이다).
    큰 폴더(전체의 많은 부분)면 +f.key로 색인을 쓰지 않는다: 색인 순서로 한 줄씩 찾아가면 30만 줄에 21.9초, 훑으면 5.0초였다."""
    prefix = index.prefix(path)
    values = [prefix, prefix[:-1] + "]"]
    db = index.connect()
    try:
        big = wide(db, "select 1 from files where key >= ? and key < ?", values, files)
    finally:
        db.close()
    column = "+f.key" if big else "f.key"
    return f"{column} >= ? and {column} < ?", values


def size_parts(low, high, files=None):
    """크기 조건(바이트, 없으면 None)들. 걸리는 파일이 적으면 크기 색인을 타고('1GB 이상' 등), 많으면 +f.size로 훑는다
    (넓은 범위를 색인으로 읽으면 1.4초, 훑으면 0.6ms. 좁은 범위를 훑으면 1.15초, 색인은 0.9ms였다)."""
    bounds = [b for b in (("f.size >= ?", low), ("f.size <= ?", high)) if b[1] is not None]
    if not bounds:
        return [], []
    values = [value for _, value in bounds]
    db = index.connect()
    try:
        big = wide(db, "select 1 from files f where " + " and ".join(part for part, _ in bounds), values, files)
    finally:
        db.close()
    return [("+" if big else "") + part for part, _ in bounds], values


def conditions(query, config, files=None):
    """query에서 종류를 뺀 조건들(종류별 개수를 셀 때 종류 조건 없이 세려고 나눈다). (조건 목록, 값 목록, 날짜 칸)
    files: 전체 줄 수를 돌려주는 함수(wide)."""
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
        part, more = under(query["folder"], files)
        parts.append(part)
        values += more
    if query.get("drive"):  # 왼쪽에서 고른 드라이브('모든 파일'을 볼 때만 화면이 보낸다)
        part, more = under(settings.check_drive(query["drive"]), files)
        parts.append(part)
        values += more
    if query.get("tag"):
        part, more = tag_condition(query["tag"], config)
        parts.append(part)
        values += more
    if (query.get("name") or "").strip().startswith("#"):  # #태그: 이름 대신 내가 단 태그로 찾는다(#만이면 조건 없음)
        if query["name"].strip("# \t\r\n"):
            part, more = tag_condition("user:" + clean_label(query["name"]), config)
            parts.append(part)
            values += more
    elif (query.get("name") or "").strip():  # 파일 이름이나 그 파일이 바로 든 폴더의 이름(위 폴더 경로 전체가 아니라)
        # 대소문자: SQLite lower()는 영문만 낮추므로, key(os.path.normcase)에서 이름 부분을 잘라 낮춘 찾는 말과 견준다.
        parts.append("(instr(substr(f.key, length(f.key) - length(f.name) + 1), ?) > 0"
                     " or f.folder in (select key from folders where instr(substr(key, length(key) - length(name) + 1), ?) > 0))")
        values += [os.path.normcase(query["name"].strip())] * 2
    if (query.get("date_from") or "").strip():
        parts.append("f.modified >= ?")
        values.append(day(query["date_from"]))
    if (query.get("date_to") or "").strip():
        parts.append("f.modified < ?")
        values.append(day(query["date_to"], end=True))
    low, high = ((number(query[name], label) * MB if str(query.get(name) or "").strip() else None)
                 for name, label in (("size_min", "최소 크기"), ("size_max", "최대 크기")))
    more_parts, more = size_parts(low, high, files)
    parts += more_parts
    values += more
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
    """추정 칩 → 조건. 'group:animal'·'animal:dog'·'food:pizza'(인식한 종류), 'person:3'(같은 사람), 'custom:0'(내 분류 모델, 기준 점수 이상).
    'user:코딩'(내가 단 태그, user_tags 표의 key로 잇는다)."""
    kind, _, label = str(tag).partition(":")
    if kind == "user" and label:
        return "f.key in (select key from user_tags where label = ?)", [label]
    if kind == "person" and label.isdigit():
        return "f.id in (select id from faces where person = ?)", [int(label)]
    if kind == "custom" and label.isdigit():
        labels = config.get("custom_labels") or []
        threshold = labels[int(label)]["threshold"] if int(label) < len(labels) else 50
        return "f.id in (select id from tags where kind = 'custom' and label = ? and score >= ?)", [label, threshold]
    if kind in ("group", "animal", "food") and label:
        return "f.id in (select id from tags where kind = ? and label = ?)", [kind, label]
    raise ValueError(f"모르는 추정 칩입니다: {tag}")


def clean_label(text):
    """내가 다는 태그의 이름: 앞의 #와 앞뒤 공백을 뗀다. 비었거나 50자를 넘거나 줄바꿈이 있으면 ValueError."""
    label = str(text or "").strip().removeprefix("#").strip()
    if not label or len(label) > 50 or "\n" in label or "\r" in label:
        raise ValueError("태그는 1~50자의 한 줄로 적어요.")
    return label


def tags_of(db, key):
    """key(exclude.key) 파일에 내가 단 태그들(가나다 순)."""
    return [row[0] for row in db.execute("select label from user_tags where key = ? order by label", (key,))]


def tag_counts(db, parts, values, config):
    """지금 조건(종류·확장자 포함)의 파일에서 추정 칩마다 몇 개인지: [[칩, 개수], …](많은 순). 칩으로 보기를 끈 내 분류 모델 분류는 뺀다."""
    kinds_only = where(parts + ["t.kind in ('group', 'animal', 'food')"])
    found = [[f"{kind}:{label}", n] for kind, label, n in db.execute(
        f"select t.kind, t.label, count(distinct t.id) n from tags t join files f on f.id = t.id{kinds_only} group by t.kind, t.label", values)]
    for person, n in db.execute(f"select p.person, count(distinct p.id) from faces p join files f on f.id = p.id{where(parts)} group by p.person", values):
        found.append([f"person:{person}", n])
    for i, label in enumerate(config.get("custom_labels") or []):
        if label["show"]:
            part, more = tag_condition(f"custom:{i}", config)
            n = db.execute(f"select count(*) from files f{where(parts + [part])}", values + more).fetchone()[0]
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
    files = functools.cache(index.count)  # 전체 줄 수는 이 목록에서 한 번만 센다(조건마다 세면 한 번 누를 때 열 번이었다)
    parts, values, date = conditions(query, config, files)
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
        parts.append("f.id in (select value from json_each(?))")  # 같은 파일이 SQLite 값 한도(32,766)보다 많아도 되게 하나로 넘긴다.
        values.append(json.dumps(list(pairs)))
    kind = query.get("kind") or ""
    if kind and kind not in kinds.NAMES:
        raise ValueError(f"모르는 종류입니다: {kind}")
    ext = (query.get("ext") or "").lower()
    if ext and (not kind or kinds.of(ext) != kind):
        raise ValueError(f"{kind or '종류'}에 없는 확장자입니다: {ext}")
    sort = SORTS.get(query.get("sort"), date)
    narrow = any(part.startswith(("f.size ", "f.key ")) for part in parts)  # size_parts·under가 좁다고 보고 색인을 타게 둔 조건
    if narrow:
        sort = "+" + sort  # 정렬 색인을 따라 전체를 훑지 않고 고른 몇 줄만 정렬한다(95MB 이상 1,165줄: 1.82초 → 0.03초)
    order = "asc" if query.get("order") == "asc" else "desc"
    stale_since = int(time.time()) - config["stale_days"] * DAY
    by_kind = parts + ([("+" if narrow else "") + "f.kind = ?"] if kind else [])  # 좁은 조건이면 종류 색인으로 그 종류 전체를 읽지 않게(1.93초 → 0.15초)
    by_kind_values = values + ([kind] if kind else [])
    extra, extra_values = by_kind[len(parts):] + (["f.ext = ?"] if ext else []), by_kind_values[len(values):] + ([ext] if ext else [])
    picked, picked_values = parts + extra, values + extra_values
    text = (query.get("text") or "").strip()
    snippet = ", (select substr(text, max(1, instr(lower(text), lower(?)) - 40), 120) from body where rowid = f.id) snippet" if text else ""
    db = index.connect()
    try:
        if query.get("view") == "duplicates" or not query.get("rows_only"):  # 아래의 목록(view) 개수가 늘 중복 의심을 센다(정렬만 바꿀 때는 필요 없다)
            same_files(db)
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
        # 조건이 있으면 +f.kind: 종류 색인을 따라 한 줄씩 찾아가지 않고 고른 줄만 묶는다(30만 줄 이름 검색 2.36초 → 1.12초).
        # 조건이 없으면 종류 색인만 읽는 쪽이 빠르다(0.04초, +는 0.16초).
        counts = dict(db.execute(f"select f.kind, count(*) from files f{where(parts)} group by {'+' if parts else ''}f.kind", values).fetchall())
        views = []
        for view in VIEWS:
            view_parts, view_values, _ = conditions(query | {"view": view}, config, files)
            views.append([view, db.execute(f"select count(*) from files f{where(view_parts + extra)}", view_values + extra_values).fetchone()[0]])
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
    return [(index.prefix(row["key"]), row["name"]) for row in db.execute("select key, name from folders where parent is null")]


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
            f"select {columns} from files where id in (select value from json_each(?)) order by lower(path) limit {SAME_LIMIT}", [json.dumps(list(pairs))])] if pairs else []
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
            parts = [DUPLICATE_ONCE if part == DUPLICATE else part for part in parts]  # 한 번만 센다: 임시 표 없이
            found[view] = db.execute(f"select count(*) from files f{where(parts)}", values).fetchone()[0]
        return found
    finally:
        db.close()
