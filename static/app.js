"use strict";
// 화면: 왼쪽 사이드바(폴더 나무, 휴지통 영역, 정리하기·보관·설정), 가운데 목록(또는 정리·보관·설정), 오른쪽 상세. 서버는 server.py.
// 목록에서 여러 줄을 고르고(Ctrl·Shift·끌어서 사각형) 나무의 폴더나 휴지통 영역에 끌어 놓을 수 있다.

const $ = (id) => document.getElementById(id);
let state = null;                 // /api/state의 답
let page = "files";               // files, organize, archive, setup
let scope = {all: true};          // 목록의 범위: {all} | {folder, name}
let filter = "";                  // 고른 필터 칩(VIEWS의 이름, "" = 없음)
let kind = "";                    // 고른 종류 칩("" = 전체)
let ext = "";                     // 고른 확장자 칩("" = 그 종류 모두). 종류를 골랐을 때만 나온다.
let sort = "date", order = "desc";
let more = {};                    // [조건]의 값
let picked = null;                // 상세에 보이는 파일(목록의 줄)
let menuPath = null;              // 오른쪽 클릭한 파일
let source = "", dest = "";       // 정리할 곳, 놓을 곳
let listed = 0;                   // 늦게 온 목록 응답을 버리기 위한 번호
const nodes = new Map();          // 왼쪽 폴더 나무의 항목: 경로(소문자) → li
const opened = new Set();         // 펼쳐 둔 폴더(소문자 경로). 나무를 다시 그려도 펼친 채로 둔다.
const selected = new Map();       // 목록에서 고른 줄: 경로(소문자) → 줄
let rows = [];                    // 지금 목록의 줄(Shift·사각형 고르기용)
let anchor = -1;                  // Shift로 범위를 고를 때의 시작 줄
let archiveDest = "";             // 보관할 곳
const DRAG = "application/x-file-organizer";  // 끌어 놓기로 넘기는 경로 목록의 형식

const VIEWS = {  // 목록의 필터 칩이자 설정 화면 맨 위의 카드(이 순서). mark: 정리할 거리라 카드에 노란 점을 붙인다.
  // 날짜 열은 이름이 늘 '날짜'이고 내용은 서버가 고른다(받은 파일은 받은 날짜, 안 본 파일은 마지막으로 연 날짜, 그 밖은 수정한 날짜).
  duplicates: {title: "중복 의심", mark: true},
  downloaded: {title: "최근 다운로드"},
  modified: {title: "최근 수정"},
  stale: {title: () => `${state.settings.stale_days}일 넘게 안 본 파일`, mark: true},
};
// 확장자 배지 색(라이트·다크 같음, 글자는 흰색)
const BADGES = [
  [["xls", "xlsx", "xlsm", "csv", "tsv", "ods"], "#2e7d4f"],
  [["pdf"], "#c4473d"],
  [["doc", "docx", "hwp", "hwpx", "txt", "md", "rtf", "odt"], "#3d64c8"],
  [["ppt", "pptx", "pps", "ppsx", "key", "odp"], "#d0712d"],
  [["jpg", "jpeg", "heic", "heif", "raw", "cr2", "cr3", "nef", "arw", "dng", "orf", "rw2"], "#8d5b12"],
  [["png", "gif", "bmp", "webp", "svg", "tif", "tiff", "ico", "psd", "ai"], "#a8572a"],
  [["mp4", "mkv", "avi", "mov", "wmv", "webm", "flv", "m4v", "ts"], "#26808a"],
  [["mp3", "wav", "flac", "aac", "m4a", "ogg", "wma"], "#b0467c"],
  [["zip", "7z", "rar", "tar", "gz", "bz2", "xz", "alz", "egg"], "#7a55c4"],
  [["exe", "msi", "msix", "appx", "apk", "iso", "dmg"], "#4b5563"],
];

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

// 뒤에서 도는 작업(정리·옮기기·휴지통·보관): 시작 요청의 답을 받아 끝날 때까지 아래 진행률 막대에 보여 주고, 끝난 상태를 돌려준다.
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
  box.classList.add("done");
  $("job-text").textContent = status.error ? `${status.name} 실패: ${status.error}` : `${status.name} 끝`;
  setTimeout(() => box.classList.add("hidden"), status.error ? 6000 : 2000);
  if (status.error) throw new Error(status.error);
  return status.result;
}

function failedText(result) {
  return result.failed.length ? ` ${result.failed.length}개는 못 했습니다.\n` + result.failed.map((f) => `${f.from}: ${f.error}`).join("\n") : "";
}

function say(id, text, kind = "") {
  $(id).textContent = text;
  $(id).className = "message " + kind;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== false));
  return node;
}

const lower = (path) => path.toLowerCase();
const two = (n) => String(n).padStart(2, "0");

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

function badge(ext, big = false) {
  const name = (ext || "").replace(".", "").toLowerCase();
  const color = (BADGES.find(([exts]) => exts.includes(name)) || [null, "#6b7280"])[1];
  const node = el("div", {className: "badge" + (big ? " big" : ""), textContent: (name || "?").slice(0, 4).toUpperCase()});
  node.style.background = color;
  return node;
}

// 조사: 이름 끝 글자에 받침이 있으면 앞의 것(을, 으로), 없으면 뒤의 것(를, 로). 한글이 아니면 둘 다 적는다.
function josa(word, withFinal, without) {
  const code = word.charCodeAt(word.length - 1) - 0xac00;
  if (code < 0 || code > 11171) return `${withFinal}(${without})`.replace("((", "(");
  const final = code % 28;
  if (withFinal === "으로") return final && final !== 8 ? "으로" : "로";  // ㄹ 받침은 '로'
  return final ? withFinal : without;
}

const extOf = (name) => (name.includes(".") ? name.slice(name.lastIndexOf(".")) : "");
const viewTitle = (view) => (typeof VIEWS[view].title === "function" ? VIEWS[view].title() : VIEWS[view].title);

// ---------- 상태·사이드바 ----------
async function loadState() {
  state = await api("/api/state");
  source = source || state.settings.source;
  dest = dest || state.settings.dest;
  await drawTree();
  showIndex(state.index);
  showOrganize();
  showArchive();
  showSetup();
}

function countChip(name) {
  // 종류 칩을 누른 횟수를 서버에 저장한다(다음 목록부터 많이 누른 칩이 왼쪽으로).
  return api("/api/chip", {kind: name}).catch(() => {});
}

// 한 줄 칩 바: 휠을 굴리면 좌우로, 눌러 끌면 좌우로 민다. 끌었을 때는 손을 뗄 때의 클릭을 무시한다.
function scroller(bar) {
  bar.classList.add("one-line");
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

// ---------- 왼쪽 폴더 나무 ----------
// 맨 위는 '모든 파일', 그 아래 색인한 폴더들. ▸를 누르면 펼치고, 이름을 누르면 가운데에 그 폴더(하위 포함)의 파일이 나온다.
// 가운데에서 파일을 고르면 그 파일이 든 폴더까지 펼치고, 폴더 아래에 고른 파일 한 줄을 붙인다(누르면 탐색기에서 연다).
function folderNode(folder, top = false) {
  const twist = el("button", {className: "twist", textContent: folder.has_children ? "▸" : ""});
  const row = el("div", {className: "row", title: folder.path}, twist, el("span", {className: "name", textContent: folder.name}),
    el("span", {className: "n", textContent: folder.files.toLocaleString()}));
  const li = el("li", {className: top ? "top" : ""}, row, el("ul", {className: "hidden"}));
  li.folder = folder;
  twist.addEventListener("click", (e) => {
    e.stopPropagation();
    if (folder.has_children) expand(li, li.lastChild.classList.contains("hidden"));
  });
  row.addEventListener("click", () => openScope({folder: folder.path, name: folder.name}));
  if (!top) {  // 색인한 맨 위 폴더는 통째로 옮기거나 지우지 않는다.
    row.draggable = true;
    row.addEventListener("dragstart", (e) => startDrag(e, [folder.path], `폴더 '${folder.name}'`));
  }
  dropTarget(row, (paths) => moveTo(paths, folder.path, folder.name));
  nodes.set(lower(folder.path), li);
  return li;
}

async function expand(li, open = true) {
  const list = li.lastChild;
  if (open && !li.loaded) {
    const {folders} = await api("/api/tree?path=" + encodeURIComponent(li.folder.path));
    list.replaceChildren(...folders.map((f) => folderNode(f)));
    li.loaded = true;
    for (const child of list.children) if (opened.has(lower(child.folder.path))) await expand(child);
  }
  list.classList.toggle("hidden", !open);
  if (li.folder.has_children) li.querySelector(":scope > .row .twist").textContent = open ? "▾" : "▸";
  open ? opened.add(lower(li.folder.path)) : opened.delete(lower(li.folder.path));
}

async function drawTree() {
  nodes.clear();
  const all = el("div", {className: "row"}, el("span", {className: "twist", textContent: "▾"}), el("span", {className: "name", textContent: "모든 파일"}),
    el("span", {className: "n", textContent: state.counts.all.toLocaleString()}));
  all.addEventListener("click", () => openScope({all: true}));
  const top = el("li", {className: "top"}, all, el("ul", {}, ...state.places.map((p) => folderNode(p, true))));
  $("tree").replaceChildren(top);
  for (const li of top.lastChild.children) if (opened.has(lower(li.folder.path))) await expand(li);
  markTree();
  if (picked) await showFileInTree(picked);
}

function markTree() {
  const files = page === "files";
  $("tree").querySelectorAll(".row.on").forEach((row) => row.classList.remove("on"));
  const current = files && (scope.folder ? nodes.get(lower(scope.folder))?.firstChild : scope.all && $("tree").querySelector(".top > .row"));
  if (current) current.classList.add("on");
  document.querySelectorAll(".side-foot [data-page]").forEach((b) => b.classList.toggle("on", b.dataset.page === page));
}

async function showFileInTree(row) {
  $("tree").querySelectorAll(".picked-file").forEach((x) => x.remove());
  $("tree").querySelectorAll(".row.here").forEach((x) => x.classList.remove("here"));
  if (!row) return;
  const {folders} = await api("/api/chain?path=" + encodeURIComponent(row.path));
  let li = null;
  for (const f of folders) {
    li = nodes.get(lower(f.path));
    if (!li) return;
    await expand(li);
  }
  if (!li || picked !== row) return;
  const file = el("li", {className: "picked-file"}, el("div", {className: "row", title: "탐색기에서 열기: " + row.path},
    el("span", {className: "twist"}), el("span", {className: "name", textContent: row.name})));
  file.firstChild.addEventListener("click", () => openPath(row.path, true));
  li.lastChild.prepend(file);
  li.lastChild.classList.remove("hidden");
  li.firstChild.classList.add("here");
  file.scrollIntoView({block: "nearest"});
}

function showIndex(status) {
  const progress = status.total ? `${status.done.toLocaleString()} / ${status.total.toLocaleString()}` : `${status.done.toLocaleString()}개`;
  $("index-status").textContent = status.running
    ? `색인하는 중… ${status.step} ${progress}`
    : status.error ? "색인 실패: " + status.error
    : status.finished ? `색인 갱신 ${fullDate(status.finished)}` : "";
  if (status.stopped && !status.running) $("index-status").textContent = `색인을 멈췄습니다. 다시 누르면 남은 것부터 이어서 합니다.`;
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
    if (Date.now() - shown > 10000) {  // 색인하는 동안에도 10초마다 숫자·나무·목록을 새로 읽어 저장된 만큼 보여 준다.
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
  markTree();
}

document.querySelectorAll(".side-foot [data-page]").forEach((b) => b.addEventListener("click", () => showPage(b.dataset.page)));

// ---------- 목록 ----------
function openScope(next, view = "") {
  scope = next;
  filter = view;
  kind = "";
  ext = "";
  showPage("files");
  loadFiles();
  if (next.folder) openFolderInTree(next.folder);
}

async function openFolderInTree(path) {
  // 상세의 경로처럼 나무에서 아직 펼치지 않은 폴더를 골랐을 때: 그 폴더까지 펼쳐 표시한다.
  const {folders} = await api("/api/chain?folder=1&path=" + encodeURIComponent(path));
  for (const f of folders.slice(0, -1)) {
    const li = nodes.get(lower(f.path));
    if (!li) return;
    await expand(li);
  }
  markTree();
}

async function loadFiles() {
  const number = ++listed;
  const query = {...more, name: $("name").value, kind, ext, sort, order};
  if (filter) query.view = filter;
  if (scope.folder) query.folder = scope.folder;
  $("title").textContent = scope.folder ? scope.name : "모든 파일";
  try {
    const found = await api("/api/files", query);
    if (number !== listed) return;
    $("list-error").classList.add("hidden");
    showFiles(found);
  } catch (e) {
    $("list-error").textContent = e.message;
    $("list-error").classList.remove("hidden");
  }
}

function showFiles(found) {
  $("summary").textContent = `${found.total.toLocaleString()}개 항목, 합계 ${size(found.bytes)}` +
    (found.more ? ` · 앞의 ${found.rows.length.toLocaleString()}개만 보입니다` : "");
  const viewCounts = Object.fromEntries(found.views);
  $("filters").replaceChildren(...Object.keys(VIEWS).map((view) => {  // 설정 화면의 카드와 같은 순서
    const n = viewCounts[view];
    const button = el("button", {className: filter === view ? "on" : ""}, viewTitle(view), el("span", {className: "n", textContent: n.toLocaleString()}));
    button.addEventListener("click", () => { filter = filter === view ? "" : view; loadFiles(); });
    return button;
  }));
  const all = found.kinds.reduce((sum, [, n]) => sum + n, 0);
  // 종류는 서버가 많이 누른 순서로 준다(search.chip_order). [전체]는 늘 맨 앞.
  $("chips").replaceChildren(...[["", "전체", all], ...found.kinds.map(([k, n]) => [k, k, n])].map(([value, label, n]) => {
    const button = el("button", {className: (kind === value ? "on" : "") + (n ? "" : " none")}, label, el("span", {className: "n", textContent: n.toLocaleString()}));
    button.addEventListener("click", () => {
      kind = value;
      ext = "";
      (value ? countChip(value) : Promise.resolve()).then(loadFiles);
    });
    return button;
  }));
  const inKind = found.exts.reduce((sum, [, n]) => sum + n, 0);
  $("exts").replaceChildren(...[["", "모두", inKind], ...found.exts.map(([e, n]) => [e, e.replace(".", "") || "(확장자 없음)", n])].map(([value, label, n]) => {
    const button = el("button", {className: ext === value ? "on" : ""}, label, el("span", {className: "n", textContent: n.toLocaleString()}));
    button.addEventListener("click", () => { ext = value; loadFiles(); });
    return button;
  }));
  $("exts").classList.toggle("hidden", !kind || !found.exts.length);
  document.querySelectorAll("#files th[data-sort]").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.sort === sort);
    th.dataset.arrow = th.dataset.sort === sort ? (order === "asc" ? " ↑" : " ↓") : "";
    th.textContent = th.textContent.replace(/ [↑↓]$/, "") + th.dataset.arrow;
  });
  const stale = `${state.settings.stale_days}일 넘게 안 열었어요`;
  rows = found.rows;
  for (const key of [...selected.keys()]) if (!rows.some((r) => lower(r.path) === key)) selected.delete(key);  // 목록에서 빠진 줄은 고르지 않은 것으로
  $("files").tBodies[0].replaceChildren(...found.rows.map((row, i) => {
    const sub = el("div", {className: "file-sub"}, el("span", {textContent: row.place}),
      row.duplicate ? el("span", {className: "tag", textContent: "중복 의심"}) : null,
      row.stale ? el("span", {className: "tag", textContent: stale}) : null);
    const text = el("div", {className: "file-text"}, el("div", {className: "file-name", textContent: row.name}), sub,
      row.snippet ? el("div", {className: "snippet", textContent: "… " + row.snippet.replace(/\s+/g, " ") + " …"}) : null);
    const tr = el("tr", {className: selected.has(lower(row.path)) ? "picked" : ""},
      el("td", {}, el("div", {className: "file"}, badge(row.ext), text)),
      el("td", {className: "muted", textContent: row.kind}),
      el("td", {className: "muted", textContent: size(row.size)}),
      el("td", {className: "muted", textContent: shortDate(row.date)}));
    tr.row = row;
    tr.index = i;
    return tr;
  }));
  showSelection();
  $("empty").textContent = state.counts.all ? "조건에 맞는 파일이 없습니다." : "아직 색인이 없습니다. [설정]에서 [색인 갱신]을 누르세요.";
  $("empty").classList.toggle("hidden", found.rows.length > 0);
}

let typing = null;
$("name").addEventListener("input", () => {
  clearTimeout(typing);
  typing = setTimeout(loadFiles, 250);
});

$("more-toggle").addEventListener("click", (e) => {
  $("more").classList.toggle("hidden");
  e.target.classList.toggle("on", !$("more").classList.contains("hidden"));
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
  order = next === sort ? (order === "asc" ? "desc" : "asc") : (next === "name" || next === "kind" ? "asc" : "desc");
  sort = next;
  loadFiles();
}));

// ---------- 여러 개 고르기 ----------
// 클릭: 그 줄만. Ctrl+클릭: 더하거나 빼기. Shift+클릭: anchor부터 그 줄까지. 고르지 않은 줄이나 빈 곳에서 끌기: 사각형 안의 줄.
function showSelection() {
  document.querySelectorAll("#files tbody tr").forEach((tr) => tr.classList.toggle("picked", selected.has(lower(tr.row.path))));
  const n = selected.size;
  $("detail-multi").classList.toggle("hidden", n < 2);
  $("detail-multi").textContent = `${n.toLocaleString()}개 선택됨 · 옮기기와 휴지통은 고른 것 모두에 합니다. 끌어서 나무의 폴더나 휴지통 영역에 놓을 수도 있습니다.`;
  $("act-rename").disabled = n > 1;
}

function selectedPaths() {
  return selected.size ? [...selected.values()].map((r) => r.path) : picked ? [picked.path] : [];
}

let marqueeJustEnded = false;  // 사각형으로 고른 직후의 click은 무시한다.

$("files").addEventListener("click", (e) => {
  const tr = e.target.closest("tbody tr");
  if (!tr || marqueeJustEnded) return;
  const key = lower(tr.row.path);
  if (e.ctrlKey || e.metaKey) {
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
  showSelection();
  showDetail(tr.row);
});

$("files").addEventListener("mousedown", (e) => {
  const tr = e.target.closest("tbody tr");
  if (e.button !== 0) return;
  if (tr && selected.has(lower(tr.row.path)) && !e.ctrlKey && !e.shiftKey) {
    tr.draggable = true;  // 고른 줄에서 끌면 끌어 놓기
    return;
  }
  if (tr) tr.draggable = false;
  startMarquee(e);
});
document.querySelector("#page-files .table-card").addEventListener("mousedown", (e) => {
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
  startDrag(e, paths, `'${tr.row.name}'`);
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
  if (message) {
    $("job").classList.remove("hidden");
    $("job-text").textContent = message;
    setTimeout(() => $("job").classList.add("hidden"), 5000);
  }
}

async function moveTo(paths, folder, name) {
  try {
    const result = await runJob((await api("/api/move", {paths, folder})).job);
    if (result) await afterChange(`${result.moved.toLocaleString()}개를 '${name}'${josa(name, "으로", "로")} 옮겼습니다.${failedText(result)}`);
  } catch (e) {
    say("detail-message", e.message, "error");
  }
}

async function trashPaths(paths) {
  const first = paths[0].slice(paths[0].lastIndexOf("\\") + 1);
  const folder = nodes.has(lower(paths[0]));
  const more = paths.length > 1 ? ` 외 ${(paths.length - 1).toLocaleString()}개 항목` : "";
  const text = `선택한 ${folder ? "폴더" : "파일"} '${first}'${more}${more ? "을" : josa(first, "을", "를")} 휴지통으로 이동하시겠습니까?`;
  if (!(await ask({title: "휴지통으로 보낼까요?", text: text + "\n휴지통에서 다시 꺼낼 수 있습니다."}))) return;
  try {
    const result = await runJob((await api("/api/trash", {paths, confirm: true})).job);
    await afterChange(`${result.trashed.toLocaleString()}개를 휴지통으로 보냈습니다.${failedText(result)}`);
  } catch (e) {
    say("detail-message", e.message, "error");
  }
}

dropTarget($("trash-zone"), trashPaths);
$("files").addEventListener("dblclick", (e) => {
  const tr = e.target.closest("tbody tr");
  if (tr) openPath(tr.row.path, false);
});
$("files").addEventListener("contextmenu", (e) => {
  const tr = e.target.closest("tbody tr");
  if (tr) showMenu(e, tr.row.path);
});

// ---------- 상세 ----------
async function showDetail(row) {
  picked = row;
  showFileInTree(row);
  $("detail-empty").classList.toggle("hidden", !!row);
  $("detail-body").classList.toggle("hidden", !row);
  say("detail-message", "");
  if (!row) return;
  $("detail-badge").replaceWith(Object.assign(badge(row.ext, true), {id: "detail-badge"}));
  $("detail-name").textContent = row.name;
  const facts = [["종류", row.kind], ["크기", size(row.size)], ["위치", row.place], ["마지막 수정", fullDate(row.modified)]];
  $("detail-rows").replaceChildren(...facts.map(([k, v]) => el("div", {}, el("dt", {textContent: k}), el("dd", {textContent: v}))));
  const {folders} = await api("/api/chain?path=" + encodeURIComponent(row.path));
  if (picked !== row) return;
  $("detail-path").replaceChildren(...folders.map((f) => {
    const button = el("button", {textContent: f.name, title: f.path});
    button.addEventListener("click", () => openScope({folder: f.path, name: f.name}));
    return el("li", {}, button);
  }), el("li", {}, el("span", {className: "here", textContent: row.name})));
}

async function changed(row, path) {
  // 옮기거나 이름을 바꾼 뒤: 상세는 새 경로로, 목록과 숫자는 다시 읽는다.
  const name = path.slice(path.lastIndexOf("\\") + 1);
  picked = {...row, path, name, ext: extOf(name)};  // 목록을 다시 그릴 때 새 경로의 줄이 골라진 채로 나오게
  selected.clear();
  selected.set(lower(path), picked);
  await loadState();
  await loadFiles();
  await showDetail(picked);
}

$("act-move").addEventListener("click", (e) => busy(e.target, "detail-message", async () => {
  const paths = selectedPaths();
  const result = await runJob((await api("/api/move", {paths})).job);  // 폴더 고르기 창을 닫으면 job이 null
  if (!result) return;
  if (paths.length === 1 && result.paths.length === 1) {
    await changed(picked, result.paths[0]);
    say("detail-message", "옮겼습니다." + failedText(result), result.failed.length ? "error" : "ok");
  } else {
    await afterChange(`${result.moved.toLocaleString()}개를 옮겼습니다.${failedText(result)}`);
  }
}));

$("act-rename").addEventListener("click", async (e) => {
  const row = picked;
  const name = await ask({title: "이름 바꾸기", text: row.name, value: row.name});
  if (name === null || name === row.name) return;
  busy(e.target, "detail-message", async () => {
    const {path} = await api("/api/rename", {path: row.path, name});
    await changed(row, path);
    say("detail-message", "이름을 바꿨습니다.", "ok");
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

// ---------- 오른쪽 클릭 메뉴 ----------
function showMenu(e, path) {
  e.preventDefault();
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
    say("detail-message", e.message, "error");
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
  } catch (e) {
    say("detail-message", e.message, "error");
  }
}

// ---------- 다운로드 정리하기 ----------
function choice(label, path, current, onPick) {
  const button = el("button", {textContent: label, title: path, className: lower(path) === lower(current || "") ? "on" : ""});
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
  $("preview-table").tBodies[0].replaceChildren(...moves.map((m) => {
    const name = m.from.slice(m.from.lastIndexOf("\\") + 1);
    return el("tr", {},
      el("td", {}, el("div", {className: "file"}, badge(extOf(name)), el("div", {className: "file-name", textContent: name}))),
      el("td", {className: "muted", textContent: m.kind}),
      el("td", {className: "where", textContent: m.to.slice(dest.length).replace(/^\\/, ""), title: m.to}));
  }));
  $("preview-card").classList.toggle("hidden", !moves.length);
  const counts = {};
  moves.forEach((m) => { counts[m.kind] = (counts[m.kind] || 0) + 1; });
  const summary = Object.entries(counts).map(([k, n]) => `${k} ${n}`).join(", ");
  say("organize-message", moves.length ? `${moves.length}개를 옮깁니다 (${summary}). 확인한 뒤 [정리]를 누르세요.` : "옮길 파일이 없습니다.");
  await loadState();
  $("run").disabled = !moves.length;
}));

function report(result, verb) {
  const failed = result.failed.map((f) => `${f.from}: ${f.error}`).join("\n");
  say("organize-message", `${result.moved}개를 ${verb}.` + (result.failed.length ? ` ${result.failed.length}개는 못 했습니다.\n${failed}` : ""),
      result.failed.length ? "error" : "ok");
}

$("run").addEventListener("click", (e) => busy(e.target, "organize-message", async () => {
  const result = await runJob(await api("/api/organize/run", {source, dest}));
  clearPreview();
  report(result, "옮겼습니다");
  await loadState();
}).then(() => { e.target.disabled = true; }));  // 옮긴 뒤에는 다시 미리보기를 해야 정리할 수 있다.

$("undo").addEventListener("click", async (e) => {
  if (!(await ask({title: "마지막 정리를 되돌릴까요?", text: `${state.undo}개를 원래 자리로 옮깁니다.`}))) return;
  busy(e.target, "organize-message", async () => {
    const result = await runJob(await api("/api/organize/undo", {}));
    clearPreview();
    report(result, "되돌렸습니다");
    await loadState();
  });
});

document.querySelectorAll("[data-pick]").forEach((button) => button.addEventListener("click", async () => {
  const {path} = await api("/api/pick", {});
  if (!path) return;
  if (button.dataset.pick === "source") setSource(path);
  else if (button.dataset.pick === "archive") { archiveDest = path; clearArchive(); showArchive(); }
  else if (button.dataset.pick === "dest") { dest = path; clearPreview(); showOrganize(); }
  else await saveSettings({roots: [...state.settings.roots, path]}, "index-progress");
}));

// ---------- 오래된 파일 보관 ----------
function showArchive() {
  archiveDest = archiveDest || state.settings.archive_dest;
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
    el("td", {textContent: k}), el("td", {className: "muted", textContent: n.toLocaleString()}), el("td", {className: "muted", textContent: size(b)}))));
  $("archive-card").classList.toggle("hidden", !found.files);
  say("archive-message", found.files
    ? `${found.files.toLocaleString()}개, ${size(found.bytes)}를 zip 하나로 묶고 원본은 휴지통으로 보냅니다.` +
      (found.blocked ? ` (휴지통으로 보낼 수 없는 곳의 ${found.blocked.toLocaleString()}개는 넣지 않습니다.)` : "")
    : "보관할 파일이 없습니다.");
  state.settings = (await api("/api/state")).settings;
  $("archive-run").disabled = !found.files;
}));

$("archive-run").addEventListener("click", async (e) => {
  if (!(await ask({title: "보관할까요?", text: `${archiveDest}에 zip을 만들고, 확인한 뒤 원본을 휴지통으로 보냅니다.\n휴지통에서 다시 꺼낼 수 있습니다.`}))) return;
  busy(e.target, "archive-message", async () => {
    const result = await runJob(await api("/api/archive/run", {...archiveQuery(), confirm: true}));
    clearArchive();
    say("archive-message", `${result.files.toLocaleString()}개(${size(result.original)})를 ${result.zip}(${size(result.zip_size)})로 보관했습니다.\n` +
      `휴지통을 비우면 ${size(result.freed)}가 비워집니다.${failedText(result)}`, result.failed.length ? "error" : "ok");
    await loadState();
    await loadFiles();
  });
});

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

function removable(text, onRemove) {
  const button = el("button", {className: "ghost small", textContent: "빼기"});
  button.addEventListener("click", onRemove);
  return el("li", {}, el("span", {className: "path", textContent: text}), button);
}

function showSetup() {
  $("views").replaceChildren(...Object.keys(VIEWS).map((view) => {
    const button = el("button", {},
      el("span", {className: "label"}, VIEWS[view].mark && state.counts[view] ? el("span", {className: "mark"}) : null, viewTitle(view)),
      el("span", {className: "count"}, state.counts[view].toLocaleString(), el("small", {textContent: "개"})));
    button.addEventListener("click", () => openScope({all: true}, view));
    return button;
  }));
  $("access-off").classList.toggle("hidden", state.last_access);
  const roots = state.settings.roots;
  $("roots").replaceChildren(...roots.map((root) => removable(root, () => saveSettings({roots: roots.filter((r) => r !== root)}, "index-progress"))));
  const has = new Set(roots.map(lower));
  const places = [...Object.entries(state.known), ...state.drives.map((d) => [d.path, d.path])].filter(([, path]) => !has.has(lower(path)));
  $("root-choices").replaceChildren(...places.map(([label, path]) => {
    const button = el("button", {textContent: "+ " + label, title: path});
    button.addEventListener("click", () => saveSettings({roots: [...roots, path]}, "index-progress"));
    return button;
  }));
  $("recent_days").value = state.settings.recent_days;
  $("stale_days").value = state.settings.stale_days;
  $("excludes").replaceChildren(...state.exclude.map((rule) =>
    removable(`${rule.kind === "folder" ? "폴더" : "파일"}  ${rule.path}`, () => changeExclude(rule.path, true))));
  $("excludes-empty").classList.toggle("hidden", state.exclude.length > 0);
}

$("days-save").addEventListener("click", async () => {
  const changes = {recent_days: Number($("recent_days").value), stale_days: Number($("stale_days").value)};
  if (await saveSettings(changes, "days-message")) {
    say("days-message", "저장했습니다.", "ok");
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
  await loadState();
  await loadFiles();
  if (state.index.running) pollIndex();
})();
