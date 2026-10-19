"use strict";
// 화면: 제목 줄(직접 그림) · 도구 줄 · 왼쪽 폴더 목록 · 가운데 파일 표 · 오른쪽 상세(파일을 고를 때만). 설정·정리·보관은 본문 전체를 바꿔 연다.
// 목록에서 여러 줄을 고르고(체크·Ctrl·Shift·끌어서 사각형) 왼쪽 폴더나 위쪽 휴지통에 끌어 놓을 수 있다. 서버는 server.py.

const $ = (id) => document.getElementById(id);
let state = null;                 // /api/state의 답
let page = "files";               // files, organize, archive, setup
let scope = {all: true};          // 목록의 범위: {all} | {folder, name}
let trail = [];                   // 지나온 범위(빈 곳 오른쪽 클릭으로 돌아간다)
let filter = "";                  // 고른 필터 칩(VIEWS의 이름, "" = 없음)
let kind = "";                    // 고른 종류 칩("" = 전체)
let ext = "";                     // 고른 확장자 칩("" = 그 종류 모두). 종류를 골랐을 때만 나온다.
let sort = "date", order = "desc";
let more = {};                    // [조건]의 값
let picked = null;                // 상세에 보이는 파일(목록의 줄)
let menuPath = null;              // 오른쪽 클릭한 파일
let source = "", dest = "", archiveDest = "";
let listed = 0;                   // 늦게 온 목록 응답을 버리기 위한 번호
let rows = [];                    // 지금 목록의 줄
let anchor = -1;                  // Shift로 범위를 고를 때의 시작 줄
const selected = new Map();       // 고른 줄: 경로(소문자) → 줄
const opened = new Set();         // 펼쳐 둔 폴더(소문자 경로). 다시 그려도 펼친 채로 둔다.
const children = new Map();       // 읽어 둔 하위 폴더: 경로(소문자) → [폴더]
const DRAG = "application/x-file-organizer";  // 끌어 놓기로 넘기는 경로 목록의 형식
const FOLDER_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M3 6.5A2.5 2.5 0 0 1 5.5 4h4l2 2.5h7A2.5 2.5 0 0 1 21 9v8.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/></svg>';

const VIEWS = {  // 위쪽 필터 칩이자 설정 화면의 숫자 카드(이 순서). warn: 정리할 거리라 숫자를 경고 색으로.
  // 날짜 열의 내용은 서버가 고른다(받은 파일은 받은 날짜, 안 본 파일은 마지막으로 연 날짜, 그 밖은 수정한 날짜).
  duplicates: {title: "중복 의심", warn: true},
  downloaded: {title: "최근 다운로드"},
  modified: {title: "최근 수정"},
  stale: {title: () => `${state.settings.stale_days}일 넘게 안 본 파일`, warn: true},
};
const PAGES = {files: "파일 정리", setup: "설정", organize: "다운로드 정리하기", archive: "오래된 파일 보관"};

// ---------- 도움 함수 ----------
async function api(path, data) {
  const options = data === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)};
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({error: `응답을 읽지 못했습니다 (${response.status})`}));
  if (!response.ok) throw new Error(body.error || String(response.status));
  return body;
}

// 요청하는 동안 버튼을 잠그고, 실패하면 messageId에 문구를 보여 준다.
async function busy(button, messageId, work) {
  button.disabled = true;
  try {
    await work();
  } catch (e) {
    say(messageId, e.message, "error");
  } finally {
    button.disabled = false;
  }
}

function say(id, text, kind = "") {
  $(id).textContent = text;
  $(id).className = "message " + kind;
}

let toastTimer;
function toast(text) {
  $("toast").textContent = text;
  $("toast").classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").classList.remove("on"), 3000);
}

function el(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...kids.filter((kid) => kid !== null && kid !== undefined && kid !== false));
  return node;
}

const lower = (path) => path.toLowerCase();
const two = (n) => String(n).padStart(2, "0");
const baseName = (path) => path.slice(path.lastIndexOf("\\") + 1);
const extOf = (name) => (name.includes(".") ? name.slice(name.lastIndexOf(".")) : "");
const viewTitle = (view) => (typeof VIEWS[view].title === "function" ? VIEWS[view].title() : VIEWS[view].title);

// Windows 탐색기와 같은 크기 표기: 1,024바이트 미만은 바이트, 그 위는 KB·MB·GB·TB에 유효 숫자 세 자리(버림). 예: 88.0 KB, 1.20 MB, 182 MB
function size(bytes) {
  const units = ["KB", "MB", "GB", "TB"];
  if (!bytes || bytes < 1024) return `${bytes || 0}바이트`;
  let n = bytes / 1024, i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1024; i++; }
  const digits = n < 10 ? 2 : n < 100 ? 1 : 0;
  const cut = Math.floor(n * 10 ** digits) / 10 ** digits;
  return `${cut.toFixed(digits)} ${units[i]}`;
}

function shortDate(seconds) {
  if (!seconds) return "";
  const d = new Date(seconds * 1000);
  return d.getFullYear() === new Date().getFullYear() ? `${d.getMonth() + 1}월 ${d.getDate()}일` : `${d.getFullYear()}. ${d.getMonth() + 1}. ${d.getDate()}.`;
}

function fullDate(seconds) {
  const d = new Date(seconds * 1000);
  return `${d.getFullYear()}. ${two(d.getMonth() + 1)}. ${two(d.getDate())}. ${two(d.getHours())}:${two(d.getMinutes())}`;
}

// 조사: 이름 끝 글자에 받침이 있으면 앞의 것(을, 으로), 없으면 뒤의 것(를, 로). 한글이 아니면 둘 다 적는다.
function josa(word, withFinal, without) {
  const code = word.charCodeAt(word.length - 1) - 0xac00;
  if (code < 0 || code > 11171) return `${withFinal}(${without})`;
  const final = code % 28;
  if (withFinal === "으로") return final && final !== 8 ? "으로" : "로";  // ㄹ 받침은 '로'
  return final ? withFinal : without;
}

function failedText(result) {
  return result.failed.length ? `\n${result.failed.length}개는 못 했어요.\n` + result.failed.map((f) => `${f.from}: ${f.error}`).join("\n") : "";
}

// 뒤에서 도는 작업(정리·옮기기·휴지통·보관): 시작 요청의 답을 받아 끝날 때까지 아래 진행률 막대에 보여 주고, 결과를 돌려준다.
async function runJob(started) {
  if (!started) return null;
  const box = $("job");
  box.classList.remove("hidden", "done");
  let status = started;
  while (status.running) {
    const percent = status.total ? Math.round(status.done / status.total * 100) : 0;
    $("job-text").textContent = `${status.name} 중… ${status.total ? `${status.done.toLocaleString()} / ${status.total.toLocaleString()}` : ""}`;
    $("job-bar").style.width = percent + "%";
    await new Promise((resolve) => setTimeout(resolve, 400));
    status = await api("/api/job");
  }
  box.classList.add("hidden");
  if (status.error) throw new Error(status.error);
  return status.result;
}

// ---------- 화면 색·창 ----------
const darkQuery = matchMedia("(prefers-color-scheme: dark)");

function applyTheme() {
  const theme = state?.settings.theme || "auto";
  document.documentElement.dataset.theme = theme === "auto" ? (darkQuery.matches ? "dark" : "light") : theme;
}
darkQuery.addEventListener("change", applyTheme);

$("theme").addEventListener("click", async () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  state.settings.theme = next;
  applyTheme();
  await api("/api/settings", {theme: next}).catch(() => {});
});

const windowCall = (action, data = {}) => api("/api/window", {action, ...data}).catch(() => null);  // 창 밖(검사용 브라우저)에서는 아무 일도 없다

$("w-min").addEventListener("click", () => windowCall("minimize"));
$("w-close").addEventListener("click", () => windowCall("close"));
async function toggleMaximize() {
  const answer = await windowCall("toggle");
  if (answer) document.documentElement.classList.toggle("maximized", answer.maximized);
}
$("w-max").addEventListener("click", toggleMaximize);
$("titlebar").addEventListener("dblclick", (e) => { if (!e.target.closest("button")) toggleMaximize(); });

// 가장자리 손잡이: 테두리 없는 창이라 크기 조절을 직접 한다. 누를 때의 창 위치·크기에서 마우스가 움직인 만큼 바꾼다.
document.querySelectorAll(".grip").forEach((grip) => grip.addEventListener("mousedown", async (e) => {
  e.preventDefault();
  const start = await windowCall("bounds");
  if (!start || start.maximized) return;
  const edge = grip.dataset.edge, x0 = e.screenX, y0 = e.screenY;
  let pending = null, sending = false;
  const send = async () => {
    if (sending || !pending) return;
    sending = true;
    const next = pending;
    pending = null;
    await windowCall("place", next);
    sending = false;
    send();
  };
  const move = (m) => {
    const dx = m.screenX - x0, dy = m.screenY - y0;
    const box = {x: start.x, y: start.y, width: start.width, height: start.height};
    if (edge.includes("e")) box.width = start.width + dx;
    if (edge.includes("s")) box.height = start.height + dy;
    if (edge.includes("w")) { box.width = start.width - dx; box.x = start.x + dx; }
    if (edge.includes("n")) { box.height = start.height - dy; box.y = start.y + dy; }
    pending = box;
    send();
  };
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
  };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
}));

// ---------- 상태 ----------
async function loadState() {
  state = await api("/api/state");
  applyTheme();
  source = source || state.settings.source;
  dest = dest || state.settings.dest;
  archiveDest = archiveDest || state.settings.archive_dest;
  await drawTree();
  showIndex(state.index);
  showOrganize();
  showArchive();
  showSetup();
}

function showIndex(status) {
  const progress = status.total ? `${status.done.toLocaleString()} / ${status.total.toLocaleString()}` : `${status.done.toLocaleString()}개`;
  $("index-status").textContent = status.running
    ? `색인하는 중… ${status.step} ${progress}`
    : status.error ? "색인 실패: " + status.error
    : status.stopped ? "색인을 멈췄어요. 다시 누르면 남은 것부터 이어서 해요."
    : status.finished ? `색인 갱신 ${fullDate(status.finished)}` : "";
  $("index-progress").textContent = status.running ? status.current : "";
  $("reindex").disabled = status.running;
  document.querySelectorAll("[data-stop]").forEach((b) => b.classList.toggle("hidden", !status.running));
}

document.querySelectorAll("[data-stop]").forEach((button) => button.addEventListener("click", async () => {
  button.disabled = true;
  try {
    showIndex(await api("/api/index/stop", {}));
  } finally {
    button.disabled = false;
  }
}));

let shown = 0;  // 색인 중에 마지막으로 화면을 새로 읽은 때

async function pollIndex() {
  const status = await api("/api/index");
  showIndex(status);
  if (status.running) {
    if (Date.now() - shown > 10000) {  // 색인하는 동안에도 10초마다 숫자·폴더·목록을 새로 읽어 저장된 만큼 보여 준다.
      shown = Date.now();
      await loadState();
      await loadFiles();
    }
    setTimeout(pollIndex, 1000);
  } else {
    await loadState();
    await loadFiles();
  }
}

function showPage(name) {
  page = name;
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("hidden", p.id !== "page-" + name));
  document.querySelectorAll(".files-only").forEach((b) => b.classList.toggle("hidden", name !== "files"));
  $("more").classList.add("hidden");
  $("back").classList.toggle("hidden", name === "files");
  $("ttl").textContent = PAGES[name];
}

document.querySelectorAll("[data-page]").forEach((b) => b.addEventListener("click", () => showPage(b.dataset.page)));
$("back").addEventListener("click", () => showPage("files"));

// ---------- 왼쪽 폴더 목록 ----------
// 맨 위는 '모든 파일', 그 아래 색인한 폴더들. 형제끼리는 파일이 많은 폴더부터. 막대는 가장 큰 맨 위 폴더에 견준 크기(제곱근).
// 이름을 누르면 그 폴더(하위 포함)의 파일이 가운데에 나오고 펼쳐진다. ▸/▾는 펼치기·접기만 한다.
// 가운데에서 파일을 고르면 그 폴더까지 펼치고, 폴더 아래에 고른 파일 한 줄을 붙인다(누르면 탐색기에서 연다).
const byCount = (list) => [...list].sort((a, b) => b.files - a.files);

function folderRow(folder, depth, biggest, top) {
  const key = lower(folder.path);
  const open = opened.has(key);
  const caret = el("span", {className: "c", textContent: folder.has_children ? (open ? "▾" : "▸") : ""});
  const name = el("div", {className: "nm"}, caret);
  name.style.paddingLeft = depth * 18 + "px";
  name.insertAdjacentHTML("beforeend", FOLDER_ICON);
  name.append(el("span", {textContent: folder.name, style: top ? "font-weight:600" : ""}));
  const bar = el("div", {className: "bar"}, el("i"));
  bar.firstChild.style.width = Math.min(100, Math.sqrt(folder.files / biggest) * 100).toFixed(1) + "%";
  const row = el("div", {className: "fl", title: folder.path}, name, el("div", {className: "m"}, bar, el("span", {textContent: folder.files.toLocaleString()})));
  row.dataset.path = folder.path;
  caret.addEventListener("click", (e) => {
    e.stopPropagation();
    if (folder.has_children) toggleFolder(folder, !open);
  });
  row.addEventListener("click", () => {
    openScope({folder: folder.path, name: folder.name});
    if (folder.has_children && !open) toggleFolder(folder, true);
  });
  if (!top) {  // 색인한 맨 위 폴더는 통째로 옮기거나 지우지 않는다.
    row.draggable = true;
    row.addEventListener("dragstart", (e) => startDrag(e, [folder.path], `폴더 '${folder.name}'`));
  }
  dropTarget(row, (paths) => moveTo(paths, folder.path, folder.name));
  return row;
}

async function toggleFolder(folder, open) {
  const key = lower(folder.path);
  if (open && !children.has(key)) children.set(key, (await api("/api/tree?path=" + encodeURIComponent(folder.path))).folders);
  open ? opened.add(key) : opened.delete(key);
  await drawTree(false);
}

async function drawTree(reload = true) {
  if (reload) children.clear();
  const places = byCount(state.places);
  const biggest = Math.max(1, ...places.map((p) => p.files));
  const all = el("div", {className: "fl"}, el("div", {className: "nm"}, el("span", {className: "c"}), el("span", {textContent: "모든 파일", style: "font-weight:600"})),
    el("div", {className: "m"}, el("div"), el("span", {textContent: state.counts.all.toLocaleString()})));
  all.dataset.all = "1";
  all.addEventListener("click", () => openScope({all: true}));
  const list = [all];
  const add = async (folders, depth, top) => {
    for (const folder of byCount(folders)) {
      list.push(folderRow(folder, depth, biggest, top));
      const key = lower(folder.path);
      if (pickedFolder && key === lower(pickedFolder)) list.push(pickedRow());
      if (opened.has(key)) {
        if (!children.has(key)) children.set(key, (await api("/api/tree?path=" + encodeURIComponent(folder.path))).folders);
        await add(children.get(key), depth + 1, false);
      }
    }
  };
  await add(places, 0, true);
  $("tree").replaceChildren(...list);
  $("folders-sum").textContent = `항목이 많은 폴더부터 · 합계 ${size(places.reduce((sum, p) => sum + p.size, 0))}`;
  markTree();
}

function markTree() {
  $("tree").querySelectorAll(".fl").forEach((row) => {
    row.classList.toggle("cur", scope.all ? !!row.dataset.all : !!row.dataset.path && lower(row.dataset.path) === lower(scope.folder || ""));
    row.classList.toggle("here", !!pickedFolder && !!row.dataset.path && lower(row.dataset.path) === lower(pickedFolder));
  });
}

let pickedFolder = null;  // 고른 파일이 든 폴더(나무에 고른 파일 한 줄을 붙일 곳)

function pickedRow() {
  const name = el("div", {className: "nm"}, el("span", {className: "c", textContent: "↳"}), el("span", {textContent: picked.name}));
  name.style.paddingLeft = "36px";
  const row = el("div", {className: "fl file", title: "탐색기에서 열기: " + picked.path}, name, el("div"));
  row.addEventListener("click", () => openPath(picked.path, true));
  return row;
}

async function showFileInTree(row) {
  pickedFolder = null;
  if (row) {
    const {folders} = await api("/api/chain?path=" + encodeURIComponent(row.path));
    if (picked !== row) return;
    for (const f of folders.slice(0, -1)) {  // 고른 파일이 든 폴더의 위 폴더들을 펼친다
      const key = lower(f.path);
      if (!children.has(key)) children.set(key, (await api("/api/tree?path=" + encodeURIComponent(f.path))).folders);
      opened.add(key);
    }
    pickedFolder = folders.length ? folders[folders.length - 1].path : null;
  }
  await drawTree(false);
  $("tree").querySelector(".fl.file")?.scrollIntoView({block: "nearest"});
}

// ---------- 목록 ----------
function openScope(next, view = "", remember = true) {
  if (remember && page === "files" && JSON.stringify(next) !== JSON.stringify(scope)) trail.push(scope);
  scope = next;
  filter = view;
  kind = "";
  ext = "";
  selected.clear();
  showDetail(null);
  showPage("files");
  loadFiles();
  markTree();
}

function goBack() {
  if (page !== "files") return showPage("files");
  if (!trail.length) return toast("더 돌아갈 곳이 없어요");
  openScope(trail.pop(), "", false);
  toast("이전 폴더로 돌아왔어요");
}

async function loadFiles() {
  const number = ++listed;
  const query = {...more, name: $("name").value, kind, ext, sort, order};
  if (filter) query.view = filter;
  if (scope.folder) query.folder = scope.folder;
  try {
    const [found, chain] = await Promise.all([api("/api/files", query),
      scope.folder ? api("/api/chain?folder=1&path=" + encodeURIComponent(scope.folder)) : Promise.resolve({folders: []})]);
    if (number !== listed) return;
    $("list-error").classList.add("hidden");
    showFiles(found, chain.folders);
  } catch (e) {
    $("list-error").textContent = e.message;
    $("list-error").classList.remove("hidden");
  }
}

function chips(box, items, current, onPick) {
  box.replaceChildren(...items.map(([value, label, n]) => {
    const button = el("button", {className: (current === value ? "on" : "") + (n ? "" : " none")}, label, el("span", {className: "n", textContent: n.toLocaleString()}));
    button.addEventListener("click", () => onPick(value));
    return button;
  }));
}

function showFiles(found, chain) {
  $("title").textContent = scope.folder ? scope.name : "모든 파일";
  const where = chain.length ? chain.map((f) => f.name).join(" › ") + " · " : "";
  $("summary").textContent = `${where}파일 ${found.total.toLocaleString()}개 · ${size(found.bytes)}` + (found.more ? ` · 앞의 ${found.rows.length.toLocaleString()}개만 보여요` : "");
  const viewCounts = Object.fromEntries(found.views);
  chips($("filters"), Object.keys(VIEWS).map((v) => [v, viewTitle(v), viewCounts[v]]), filter, (v) => { filter = filter === v ? "" : v; loadFiles(); });
  // 종류는 서버가 많이 누른 순서로 준다(search.chip_order). [전체]는 늘 맨 앞.
  const all = found.kinds.reduce((sum, [, n]) => sum + n, 0);
  chips($("chips"), [["", "전체", all], ...found.kinds.map(([k, n]) => [k, k, n])], kind, (value) => {
    kind = value;
    ext = "";
    (value ? countChip(value) : Promise.resolve()).then(loadFiles);
  });
  const inKind = found.exts.reduce((sum, [, n]) => sum + n, 0);
  chips($("exts"), [["", "모두", inKind], ...found.exts.map(([e, n]) => [e, e.replace(".", "") || "(확장자 없음)", n])], ext, (value) => { ext = value; loadFiles(); });
  $("exts-bar").classList.toggle("hidden", !kind || !found.exts.length);
  document.querySelectorAll("#files th[data-sort]").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.sort === sort);
    th.textContent = th.textContent.replace(/ [↑↓]$/, "") + (th.dataset.sort === sort ? (order === "asc" ? " ↑" : " ↓") : "");
  });
  rows = found.rows;
  for (const key of [...selected.keys()]) if (!rows.some((r) => lower(r.path) === key)) selected.delete(key);  // 목록에서 빠진 줄은 고르지 않은 것으로
  const stale = `${state.settings.stale_days}일 넘게 안 열었어요`;
  $("files").tBodies[0].replaceChildren(...rows.map((row, i) => {
    const name = el("td", {className: "name"}, el("span", {className: "ext", textContent: (extOf(row.name).slice(1) || "?").slice(0, 5).toUpperCase()}), row.name,
      row.duplicate ? el("span", {className: "pill", textContent: "중복 의심"}) : null,
      row.stale ? el("span", {className: "pill", textContent: stale}) : null,
      row.snippet ? el("div", {className: "snippet", textContent: "… " + row.snippet.replace(/\s+/g, " ") + " …"}) : null);
    const tr = el("tr", {}, el("td", {className: "ck"}, el("input", {type: "checkbox"})), name,
      el("td", {className: "n mute", textContent: size(row.size)}), el("td", {className: "mute", textContent: shortDate(row.date)}));
    tr.row = row;
    tr.index = i;
    return tr;
  }));
  $("empty").textContent = state.counts.all ? "조건에 맞는 파일이 없어요." : "아직 색인이 없어요. 설정(톱니)에서 [색인 갱신]을 누르세요.";
  $("empty").classList.toggle("hidden", rows.length > 0);
  showSelection();
}

let typing = null;
$("name").addEventListener("input", () => {
  clearTimeout(typing);
  typing = setTimeout(loadFiles, 250);
});

$("more-toggle").addEventListener("click", (e) => {
  $("more").classList.toggle("hidden");
  e.currentTarget.classList.toggle("p", !$("more").classList.contains("hidden"));
});
$("more").addEventListener("submit", (e) => {
  e.preventDefault();
  more = Object.fromEntries(new FormData(e.target));
  loadFiles();
});
$("more-clear").addEventListener("click", () => {
  $("more").reset();
  more = {};
  loadFiles();
});

document.querySelectorAll("#files th[data-sort]").forEach((th) => th.addEventListener("click", () => {
  const next = th.dataset.sort;
  order = next === sort ? (order === "asc" ? "desc" : "asc") : (next === "name" ? "asc" : "desc");
  sort = next;
  loadFiles();
}));

function countChip(name) {
  return api("/api/chip", {kind: name}).catch(() => {});  // 누른 횟수를 저장한다(다음 목록부터 많이 누른 칩이 왼쪽으로).
}

// 한 줄 칩 바: 휠을 굴리면 좌우로, 눌러 끌면 좌우로 민다. 끌었을 때는 손을 뗄 때의 클릭을 무시한다.
function scroller(bar) {
  bar.addEventListener("wheel", (e) => {
    if (bar.scrollWidth <= bar.clientWidth) return;
    e.preventDefault();
    bar.scrollLeft += e.deltaY || e.deltaX;
  }, {passive: false});
  let start = null, panned = false;
  bar.addEventListener("pointerdown", (e) => {
    if (e.button === 0) { start = {x: e.clientX, left: bar.scrollLeft}; panned = false; }
  });
  document.addEventListener("pointermove", (e) => {
    if (!start) return;
    if (!panned && Math.abs(e.clientX - start.x) < 5) return;
    panned = true;
    bar.classList.add("panning");
    bar.scrollLeft = start.left - (e.clientX - start.x);
  });
  document.addEventListener("pointerup", () => {
    start = null;
    bar.classList.remove("panning");
  });
  bar.addEventListener("click", (e) => {
    if (panned) { e.stopPropagation(); e.preventDefault(); panned = false; }
  }, true);
}
["filters", "chips", "exts"].forEach((id) => scroller($(id)));

// ---------- 고르기 ----------
// 클릭: 그 줄만. 체크 칸·Ctrl+클릭: 더하거나 빼기. Shift+클릭: anchor부터 그 줄까지. 고르지 않은 줄이나 표 아래 빈 곳에서 끌기: 사각형 안의 줄.
function showSelection() {
  document.querySelectorAll("#files tbody tr").forEach((tr) => {
    const on = selected.has(lower(tr.row.path));
    tr.classList.toggle("sel", on);
    tr.classList.toggle("cur", !!picked && lower(picked.path) === lower(tr.row.path));
    tr.querySelector("input").checked = on;
  });
  const n = selected.size;
  $("check-all").checked = n > 0 && n === rows.length;
  $("sbar").replaceChildren(...(n
    ? [el("b", {textContent: `${n.toLocaleString()}개 선택됨`}), el("span", {textContent: "위쪽 휴지통이나 왼쪽 폴더에 끌어다 놓을 수 있어요"})]
    : [el("span", {textContent: "파일을 누르면 상세 정보가 열리고, 빈 곳을 누르면 닫혀요. 빈 곳을 오른쪽 클릭하면 이전 폴더로 돌아가요."})]));
  $("detail-multi").classList.toggle("hidden", n < 2);
  $("detail-multi").textContent = `${n.toLocaleString()}개 선택됨 · 옮기기와 휴지통은 고른 것 모두에 해요.`;
  $("act-rename").disabled = n > 1;
}

function selectedPaths() {
  return selected.size ? [...selected.values()].map((r) => r.path) : picked ? [picked.path] : [];
}

let marqueeJustEnded = false;  // 사각형으로 고른 직후의 click은 무시한다.

$("files").addEventListener("click", (e) => {
  const tr = e.target.closest("tbody tr");
  if (!tr || marqueeJustEnded) return;
  e.stopPropagation();
  const key = lower(tr.row.path);
  if (e.target.type === "checkbox" || e.ctrlKey || e.metaKey) {
    selected.has(key) ? selected.delete(key) : selected.set(key, tr.row);
    anchor = tr.index;
  } else if (e.shiftKey && anchor >= 0) {
    selected.clear();
    const [from, to] = [Math.min(anchor, tr.index), Math.max(anchor, tr.index)];
    rows.slice(from, to + 1).forEach((r) => selected.set(lower(r.path), r));
  } else {
    selected.clear();
    selected.set(key, tr.row);
    anchor = tr.index;
  }
  showDetail(tr.row);
});

$("check-all").addEventListener("click", (e) => {
  e.stopPropagation();
  selected.clear();
  if (e.target.checked) rows.forEach((r) => selected.set(lower(r.path), r));
  showSelection();
});

$("files").addEventListener("dblclick", (e) => {
  const tr = e.target.closest("tbody tr");
  if (tr) openPath(tr.row.path, false);
});

$("files").addEventListener("mousedown", (e) => {
  const tr = e.target.closest("tbody tr");
  if (e.button !== 0 || e.target.type === "checkbox") return;
  if (tr && selected.has(lower(tr.row.path)) && !e.ctrlKey && !e.shiftKey) {
    tr.draggable = true;  // 고른 줄에서 끌면 끌어 놓기
    return;
  }
  if (tr) tr.draggable = false;
  startMarquee(e);
});
$("tw").addEventListener("mousedown", (e) => {
  if (e.button === 0 && !e.target.closest("tr")) startMarquee(e);  // 표 아래 빈 곳에서도 사각형 고르기
});

function startMarquee(e) {
  const x0 = e.clientX, y0 = e.clientY, box = $("marquee");
  let moved = false;
  const before = e.ctrlKey ? new Map(selected) : new Map();  // Ctrl을 누르고 끌면 이미 고른 것에 더한다.
  const move = (m) => {
    if (!moved && Math.hypot(m.clientX - x0, m.clientY - y0) < 5) return;
    moved = true;
    m.preventDefault();
    const [left, top, right, bottom] = [Math.min(x0, m.clientX), Math.min(y0, m.clientY), Math.max(x0, m.clientX), Math.max(y0, m.clientY)];
    Object.assign(box.style, {left: left + "px", top: top + "px", width: right - left + "px", height: bottom - top + "px"});
    box.classList.remove("hidden");
    selected.clear();
    before.forEach((v, k) => selected.set(k, v));
    document.querySelectorAll("#files tbody tr").forEach((tr) => {
      const r = tr.getBoundingClientRect();
      if (r.bottom > top && r.top < bottom && r.right > left && r.left < right) selected.set(lower(tr.row.path), tr.row);
    });
    showSelection();
  };
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
    box.classList.add("hidden");
    if (moved) {
      marqueeJustEnded = true;
      setTimeout(() => { marqueeJustEnded = false; }, 0);
      const last = [...selected.values()].pop();
      if (last) showDetail(last);
    }
  };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
}

// 빈 곳을 누르면 상세를 닫고 고른 것을 푼다(시안과 같다). 버튼·입력·칩·폴더·상세 안은 빼고.
$("page-files").addEventListener("click", (e) => {
  if (marqueeJustEnded || e.target.closest("tbody tr, #detail, .fl, button, input, .chips, th")) return;
  if (!picked && !selected.size) return;
  selected.clear();
  showDetail(null);
});

// ---------- 끌어 놓기 ----------
function startDrag(e, paths, label) {
  e.dataTransfer.setData(DRAG, JSON.stringify(paths));
  e.dataTransfer.effectAllowed = "move";
  const badge = $("drag-badge");
  badge.textContent = paths.length > 1 ? `${paths.length.toLocaleString()}개 항목 이동 중` : `${label} 이동 중`;
  e.dataTransfer.setDragImage(badge, -12, -12);
}

$("files").addEventListener("dragstart", (e) => {
  const tr = e.target.closest("tbody tr");
  if (!tr) return;
  const paths = selected.has(lower(tr.row.path)) ? selectedPaths() : [tr.row.path];
  startDrag(e, paths, `파일 '${tr.row.name}'`);
});

function dropTarget(target, onDrop) {
  target.addEventListener("dragover", (e) => {
    if (!e.dataTransfer.types.includes(DRAG)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    target.classList.add("drop");
  });
  target.addEventListener("dragleave", () => target.classList.remove("drop"));
  target.addEventListener("drop", (e) => {
    target.classList.remove("drop");
    if (!e.dataTransfer.types.includes(DRAG)) return;
    e.preventDefault();
    e.stopPropagation();
    onDrop(JSON.parse(e.dataTransfer.getData(DRAG)));
  });
}

// 바깥(탐색기)에서 파일을 끌어 와 창에 놓아도 그 파일이 열리지 않게 한다.
document.addEventListener("dragover", (e) => { if (!e.dataTransfer.types.includes(DRAG)) e.preventDefault(); });
document.addEventListener("drop", (e) => e.preventDefault());

async function afterChange(message) {
  selected.clear();
  await showDetail(null);
  await loadState();
  await loadFiles();
  if (message) toast(message);
}

async function moveTo(paths, folder, name) {
  try {
    const result = await runJob((await api("/api/move", {paths, folder})).job);
    if (result) await afterChange(`${result.moved.toLocaleString()}개를 '${name}'${josa(name, "으로", "로")} 옮겼어요.${failedText(result)}`);
  } catch (e) {
    toast(e.message);
  }
}

async function trashPaths(paths) {
  if (!paths.length) return;
  const first = baseName(paths[0]);
  const folder = !rows.some((r) => lower(r.path) === lower(paths[0]));  // 목록의 파일이 아니면 왼쪽에서 끌어 온 폴더
  const more = paths.length > 1 ? ` 외 ${(paths.length - 1).toLocaleString()}개 항목` : "";
  const text = `선택한 ${folder ? "폴더" : "파일"} '${first}'${more}${more ? "을" : josa(first, "을", "를")} 휴지통으로 이동하시겠습니까?`;
  if (!(await ask({title: "휴지통으로 보낼까요?", text: text + "\n휴지통에서 다시 꺼낼 수 있어요."}))) return;
  try {
    const result = await runJob((await api("/api/trash", {paths, confirm: true})).job);
    await afterChange(`${result.trashed.toLocaleString()}개를 휴지통으로 보냈어요.${failedText(result)}`);
  } catch (e) {
    toast(e.message);
  }
}

dropTarget($("trash"), trashPaths);
$("trash").addEventListener("click", () => {
  if (selected.size) trashPaths(selectedPaths());
  else toast("파일을 고른 뒤 누르거나, 파일·폴더를 여기에 끌어다 놓으세요");
});

// ---------- 상세 ----------
async function showDetail(row) {
  picked = row;
  $("detail").classList.toggle("hidden", !row);
  say("detail-message", "");
  showSelection();
  showFileInTree(row);
  if (!row) return;
  $("detail-name").textContent = row.name;
  $("detail-pills").replaceChildren(...[row.duplicate && "중복 의심", row.stale && `${state.settings.stale_days}일 넘게 안 열었어요`]
    .filter(Boolean).map((t) => el("span", {className: "pill", textContent: t})));
  const [chain, same] = await Promise.all([api("/api/chain?path=" + encodeURIComponent(row.path)), api("/api/same?path=" + encodeURIComponent(row.path))]);
  if (picked !== row) return;
  const where = el("div");
  chain.folders.forEach((f, i) => {
    const button = el("button", {className: "crumb", textContent: f.name, title: f.path});
    button.addEventListener("click", () => openScope({folder: f.path, name: f.name}));
    where.append(...(i ? [" › ", button] : [button]));
  });
  const facts = [["종류", row.kind], ["크기", size(row.size)], ["위치", where], ["마지막 수정", fullDate(row.modified)]];
  $("detail-rows").replaceChildren(...facts.flatMap(([k, v]) => [el("span", {textContent: k}), typeof v === "string" ? el("div", {textContent: v}) : v]));
  showSame("same-content", "내용이 같은 파일", same.content, "내용이 같은 파일이 없어요.");
  showSame("same-name", "이름이 같은 파일", same.name, "이름이 같은 파일이 없어요.");
}

function showSame(id, title, list, none) {
  $(id + "-title").textContent = list.length ? `${title} ${list.length.toLocaleString()}개${list.length >= 50 ? " 이상" : ""}` : title;
  $(id).replaceChildren(...(list.length ? list.map((f) => {
    const folder = f.path.slice(0, f.path.lastIndexOf("\\"));
    const button = el("button", {className: "li", title: f.path}, el("span", {textContent: baseName(folder) === f.place ? f.place : `${f.place} › … › ${baseName(folder)}`}), el("span", {textContent: size(f.size)}));
    button.addEventListener("click", () => goToFile(f));
    return button;
  }) : [el("div", {className: "li"}, el("span", {textContent: none}))]));
}

async function goToFile(file) {
  // 같은 파일 목록에서 누른 파일로: 그 폴더를 열고 그 파일을 고른다.
  const folder = file.path.slice(0, file.path.lastIndexOf("\\"));
  openScope({folder, name: baseName(folder)});
  await loadFiles();
  const row = rows.find((r) => lower(r.path) === lower(file.path));
  if (row) {
    selected.set(lower(row.path), row);
    showDetail(row);
  }
}

async function changed(row, path) {
  // 옮기거나 이름을 바꾼 뒤: 상세는 새 경로로, 목록과 숫자는 다시 읽는다.
  const name = baseName(path);
  picked = {...row, path, name, ext: extOf(name)};
  selected.clear();
  selected.set(lower(path), picked);
  await loadState();
  await loadFiles();
  await showDetail(picked);
}

$("act-move").addEventListener("click", (e) => busy(e.currentTarget, "detail-message", async () => {
  const paths = selectedPaths();
  const result = await runJob((await api("/api/move", {paths})).job);  // 폴더 고르기 창을 닫으면 job이 null
  if (!result) return;
  if (paths.length === 1 && result.paths.length === 1) {
    await changed(picked, result.paths[0]);
    say("detail-message", "옮겼어요." + failedText(result), result.failed.length ? "error" : "ok");
  } else {
    await afterChange(`${result.moved.toLocaleString()}개를 옮겼어요.${failedText(result)}`);
  }
}));

$("act-rename").addEventListener("click", async (e) => {
  const row = picked;
  const name = await ask({title: "이름 바꾸기", text: row.name, value: row.name});
  if (name === null || name === row.name) return;
  busy(e.target, "detail-message", async () => {
    const {path} = await api("/api/rename", {path: row.path, name});
    await changed(row, path);
    say("detail-message", "이름을 바꿨어요.", "ok");
  });
});

$("act-trash").addEventListener("click", () => trashPaths(selectedPaths()));

// ---------- 대화상자: 입력(value가 있으면)이나 확인. 확인하면 입력값 또는 true, 취소하면 null ----------
function ask({title, text = "", value}) {
  $("dialog-title").textContent = title;
  $("dialog-text").textContent = text;
  const input = $("dialog-input");
  input.classList.toggle("hidden", value === undefined);
  $("dialog").classList.remove("hidden");
  if (value !== undefined) {
    input.value = value;
    input.focus();
    const dot = value.lastIndexOf(".");
    input.setSelectionRange(0, dot > 0 ? dot : value.length);  // 확장자 앞까지 골라 둔다.
  } else {
    $("dialog-ok").focus();
  }
  return new Promise((resolve) => {
    const finish = (answer) => {
      $("dialog").classList.add("hidden");
      $("dialog-form").onsubmit = $("dialog-cancel").onclick = null;
      resolve(answer);
    };
    $("dialog-form").onsubmit = (e) => { e.preventDefault(); finish(value === undefined ? true : input.value); };
    $("dialog-cancel").onclick = () => finish(null);
  });
}

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  hideMenu();
  if (!$("dialog").classList.contains("hidden")) $("dialog-cancel").click();
});

// ---------- 오른쪽 클릭: 파일 줄 위는 메뉴, 그 밖은 이전 폴더로(설정 등에서는 목록으로) ----------
document.addEventListener("contextmenu", (e) => {
  if (e.target.closest("input")) return;  // 입력 칸은 붙여넣기 메뉴 그대로
  e.preventDefault();
  if (!$("dialog").classList.contains("hidden")) return;
  const tr = page === "files" && e.target.closest("#files tbody tr");
  if (tr) return showMenu(e, tr.row.path);
  hideMenu();
  goBack();
});

function showMenu(e, path) {
  menuPath = path;
  const menu = $("menu");
  menu.classList.remove("hidden");
  menu.style.left = Math.min(e.clientX, innerWidth - menu.offsetWidth - 4) + "px";
  menu.style.top = Math.min(e.clientY, innerHeight - menu.offsetHeight - 4) + "px";
}

const hideMenu = () => $("menu").classList.add("hidden");
document.addEventListener("click", (e) => { if (!e.target.closest("#menu")) hideMenu(); });
document.addEventListener("scroll", hideMenu, true);

async function openPath(path, reveal) {
  try {
    await api("/api/open", {path, reveal});
  } catch (e) {
    toast(e.message);
  }
}

$("menu").addEventListener("click", (e) => {
  const act = e.target.dataset.act;
  if (!act) return;
  hideMenu();
  if (act === "open") openPath(menuPath, false);
  if (act === "reveal") openPath(menuPath, true);
  if (act === "exclude") changeExclude(menuPath, false);
});

async function changeExclude(path, remove) {
  try {
    state.exclude = (await api("/api/exclude", {path, remove})).exclude;
    await loadState();
    if (page === "files") await loadFiles();
    toast(remove ? "예외에서 뺐어요" : "예외로 지정했어요. 중복 의심 등 네 목록에서 빠져요.");
  } catch (e) {
    toast(e.message);
  }
}

// ---------- 다운로드 정리하기 ----------
function choice(label, path, current, onPick) {
  const button = el("button", {className: "chip" + (lower(path) === lower(current || "") ? " on" : ""), textContent: label, title: path});
  button.addEventListener("click", () => onPick(path));
  return button;
}

function showOrganize() {
  const places = [["다운로드", state.known["다운로드"]], ...state.drives.map((d) => [`${d.path} (${d.type})`, d.path])];
  $("source-choices").replaceChildren(...places.map(([label, path]) => choice(label, path, source, setSource)));
  $("source").textContent = source || "고르지 않음";
  $("dest").textContent = dest || "고르지 않음";
  $("undo").disabled = !state.undo;
  $("undo").textContent = state.undo ? `마지막 정리 되돌리기 (${state.undo}개)` : "마지막 정리 되돌리기";
}

function clearPreview() {
  $("run").disabled = true;
  $("preview-card").classList.add("hidden");
}

function setSource(path) {
  source = path;
  clearPreview();
  showOrganize();
}

$("preview").addEventListener("click", (e) => busy(e.target, "organize-message", async () => {
  clearPreview();
  const {moves} = await api("/api/organize/preview", {source, dest});
  $("preview-table").tBodies[0].replaceChildren(...moves.map((m) => el("tr", {},
    el("td", {}, el("span", {className: "ext", textContent: (extOf(baseName(m.from)).slice(1) || "?").slice(0, 5).toUpperCase()}), baseName(m.from)),
    el("td", {className: "mute", textContent: m.kind}),
    el("td", {className: "mute", textContent: m.to.slice(dest.length).replace(/^\\/, ""), title: m.to}))));
  $("preview-card").classList.toggle("hidden", !moves.length);
  const counts = {};
  moves.forEach((m) => { counts[m.kind] = (counts[m.kind] || 0) + 1; });
  const summary = Object.entries(counts).map(([k, n]) => `${k} ${n}`).join(", ");
  say("organize-message", moves.length ? `${moves.length}개를 옮겨요 (${summary}). 확인한 뒤 [정리]를 누르세요.` : "옮길 파일이 없어요.");
  await loadState();
  $("run").disabled = !moves.length;
}));

function report(result, verb) {
  say("organize-message", `${result.moved}개를 ${verb}.${failedText(result)}`, result.failed.length ? "error" : "ok");
}

$("run").addEventListener("click", (e) => busy(e.target, "organize-message", async () => {
  const result = await runJob(await api("/api/organize/run", {source, dest}));
  clearPreview();
  report(result, "옮겼어요");
  await loadState();
}).then(() => { e.target.disabled = true; }));  // 옮긴 뒤에는 다시 미리보기를 해야 정리할 수 있다.

$("undo").addEventListener("click", async (e) => {
  if (!(await ask({title: "마지막 정리를 되돌릴까요?", text: `${state.undo}개를 원래 자리로 옮겨요.`}))) return;
  busy(e.target, "organize-message", async () => {
    const result = await runJob(await api("/api/organize/undo", {}));
    clearPreview();
    report(result, "되돌렸어요");
    await loadState();
  });
});

// ---------- 오래된 파일 보관 ----------
function showArchive() {
  $("archive-dest").textContent = archiveDest || "고르지 않음";
  if (!$("cold_days").value) $("cold_days").value = state.settings.cold_days;
}

function clearArchive() {
  $("archive-run").disabled = true;
  $("archive-card").classList.add("hidden");
}

const archiveQuery = () => ({dest: archiveDest, days: Number($("cold_days").value)});

$("archive-preview").addEventListener("click", (e) => busy(e.target, "archive-message", async () => {
  clearArchive();
  const found = await api("/api/archive/preview", archiveQuery());
  $("archive-table").tBodies[0].replaceChildren(...found.kinds.map(([k, n, b]) => el("tr", {},
    el("td", {textContent: k}), el("td", {className: "n mute", textContent: n.toLocaleString()}), el("td", {className: "n mute", textContent: size(b)}))));
  $("archive-card").classList.toggle("hidden", !found.files);
  say("archive-message", found.files
    ? `${found.files.toLocaleString()}개(${size(found.bytes)})를 zip 하나로 묶고 원본은 휴지통으로 보내요.` +
      (found.blocked ? ` 휴지통으로 보낼 수 없는 곳의 ${found.blocked.toLocaleString()}개는 넣지 않아요.` : "")
    : "보관할 파일이 없어요.");
  state.settings = (await api("/api/state")).settings;
  $("archive-run").disabled = !found.files;
}));

$("archive-run").addEventListener("click", async (e) => {
  if (!(await ask({title: "보관할까요?", text: `${archiveDest}에 zip을 만들고, 확인한 뒤 원본을 휴지통으로 보내요.\n휴지통에서 다시 꺼낼 수 있어요.`}))) return;
  busy(e.target, "archive-message", async () => {
    const result = await runJob(await api("/api/archive/run", {...archiveQuery(), confirm: true}));
    clearArchive();
    say("archive-message", `${result.files.toLocaleString()}개(${size(result.original)})를 ${result.zip}(${size(result.zip_size)})로 보관했어요.\n` +
      `휴지통을 비우면 ${size(result.freed)}가 비워져요.${failedText(result)}`, result.failed.length ? "error" : "ok");
    await loadState();
    await loadFiles();
  });
});

document.querySelectorAll("[data-pick]").forEach((button) => button.addEventListener("click", async () => {
  const {path} = await api("/api/pick", {}).catch((e) => (toast(e.message), {}));
  if (!path) return;
  if (button.dataset.pick === "source") setSource(path);
  else if (button.dataset.pick === "dest") { dest = path; clearPreview(); showOrganize(); }
  else if (button.dataset.pick === "archive") { archiveDest = path; clearArchive(); showArchive(); }
  else await saveSettings({roots: [...state.settings.roots, path]}, "index-progress");
}));

// ---------- 설정 ----------
async function saveSettings(changes, messageId) {
  try {
    state.settings = (await api("/api/settings", changes)).settings;
    showSetup();
    return true;
  } catch (e) {
    $(messageId).textContent = e.message;
    return false;
  }
}

function listRow(text, onRemove, icon = true) {
  const row = el("div");
  if (icon) row.insertAdjacentHTML("beforeend", FOLDER_ICON);
  const button = el("button", {className: "btn s", textContent: "빼기"});
  button.addEventListener("click", onRemove);
  row.append(el("span", {className: "mono", textContent: text}), button);
  return row;
}

function showSetup() {
  $("views").replaceChildren(...Object.keys(VIEWS).map((view) => {
    const card = el("button", {className: "stat" + (VIEWS[view].warn ? " w" : "")}, el("span", {textContent: viewTitle(view)}),
      el("b", {}, state.counts[view].toLocaleString(), el("i", {textContent: "개"})));
    card.addEventListener("click", () => openScope({all: true}, view));
    return card;
  }));
  $("access-off").classList.toggle("hidden", state.last_access);
  const roots = state.settings.roots;
  $("roots").replaceChildren(...roots.map((root) => listRow(root, () => saveSettings({roots: roots.filter((r) => r !== root)}, "index-progress"))));
  const has = new Set(roots.map(lower));
  const places = [...Object.entries(state.known), ...state.drives.map((d) => [d.path, d.path])].filter(([, path]) => !has.has(lower(path)));
  $("root-choices").replaceChildren(...places.map(([label, path]) => {
    const button = el("button", {className: "chip", textContent: "+ " + label, title: path});
    button.addEventListener("click", () => saveSettings({roots: [...roots, path]}, "index-progress"));
    return button;
  }));
  $("recent_days").value = state.settings.recent_days;
  $("stale_days").value = state.settings.stale_days;
  $("excludes").replaceChildren(...state.exclude.map((rule) =>
    listRow(`${rule.kind === "folder" ? "폴더" : "파일"}  ${rule.path}`, () => changeExclude(rule.path, true), rule.kind === "folder")));
  $("excludes-empty").classList.toggle("hidden", state.exclude.length > 0);
}

$("days-save").addEventListener("click", async () => {
  const changes = {recent_days: Number($("recent_days").value), stale_days: Number($("stale_days").value)};
  if (await saveSettings(changes, "days-message")) {
    say("days-message", "기간을 저장했어요.", "ok");
    await loadState();
  } else {
    $("days-message").className = "message error";
  }
});

$("reindex").addEventListener("click", async () => {
  try {
    showIndex(await api("/api/index", {}));
    pollIndex();
  } catch (e) {
    $("index-progress").textContent = e.message;
  }
});

// ---------- 시작 ----------
(async () => {
  showPage("files");
  await loadState();
  await loadFiles();
  const bounds = await windowCall("bounds");
  if (bounds) document.documentElement.classList.toggle("maximized", bounds.maximized);
  if (state.index.running) pollIndex();
})();
