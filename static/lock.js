"use strict";
// 잠금 창: 비밀번호 정하기, 금고 위치, 잠금 목록(열기·다시 잠그기·꺼내기·잠금 풀기), 본 창에서 고른 폴더 잠그기·금고로 모으기.
// 이 창은 desktop.open_lock이 화면 캡처에서 빼 둔다. 바꾼 뒤에는 본 창이 목록을 다시 읽게 한다(refresh).

let lockState = null;  // /api/lock/state의 답
const password = (title, text = "") => ask({title, text, value: "", secret: true});

const applyTheme = followTheme(() => lockState?.theme);

async function load() {
  lockState = await api("/api/lock/state");
  applyTheme();
  $("target-box").classList.toggle("hidden", !lockState.target);
  $("target").textContent = lockState.target;
  $("locks").replaceChildren(...lockState.locks.map(lockRow));
  $("locks-empty").classList.toggle("hidden", lockState.locks.length > 0);
  const vault = lockState.vault;
  $("vault-path").textContent = vault.path ? `금고: ${vault.path} (${vault.count.toLocaleString()}개${vault.open ? ", 열림" : ""})` : "금고: 정하지 않음";
  $("lock-vault").textContent = vault.path ? "금고 위치 바꾸기" : "금고 위치 정하기";
  $("lock-password").textContent = lockState.password ? "비밀번호 바꾸기" : "비밀번호 정하기";
}

// 잠금을 바꾸는 요청. 성공하면 다시 그리고 본 창의 목록·숫자도 다시 읽게 한다.
async function lockCall(url, data, done) {
  try {
    const answer = await api(url, data);
    await load();
    if (data.path) await api("/api/window", {action: "refresh"}).catch(() => {});  // 비밀번호·금고 위치는 본 창의 목록을 바꾸지 않는다
    if (done) say("lock-message", typeof done === "function" ? done(answer) : done, "ok");
    return answer;
  } catch (e) {
    say("lock-message", e.message, "error");
    return null;
  }
}

async function lockFolder(vault) {
  const path = lockState.target;
  if (!lockState.password) return say("lock-message", "먼저 [비밀번호 정하기]를 하세요.", "error");
  if (vault && !lockState.vault.path) return say("lock-message", "먼저 [금고 위치 정하기]를 하세요.", "error");
  const pw = vault
    ? await password("금고로 모을까요?", `${path}\n\n금고(${lockState.vault.path}) 안으로 옮기고 금고를 잠가요. 비밀번호를 넣어야 열리고, [꺼내기]로 이 자리에 되돌려요.`)
    : await password("폴더를 잠글까요?", `${path}\n\n비밀번호를 넣어야 열려요. 잠그면 목록과 검색에서 빠져요.`);
  if (pw !== null && await lockCall("/api/lock", {path, password: pw, vault}, vault ? "금고로 모았어요." : "잠갔어요.")) {
    $("target-box").classList.add("hidden");
  }
}

$("do-lock").addEventListener("click", () => lockFolder(false));
$("do-vault").addEventListener("click", () => lockFolder(true));

$("lock-vault").addEventListener("click", async () => {
  if (!lockState.password) return say("lock-message", "먼저 [비밀번호 정하기]를 하세요.", "error");
  const pw = await password("금고 위치 정하기", "다음 창에서 금고를 둘 곳을 고르세요. 그 안에 숨긴 .vault 폴더를 만들어 잠가요. 금고와 같은 디스크의 폴더만 모을 수 있어요.");
  if (pw === null) return;
  const place = await pickPath({title: "금고를 둘 곳"});
  if (place) await lockCall("/api/lock/vault", {password: pw, place}, (answer) => "금고를 정했어요: " + answer.vault);
});

async function takeOut(item) {
  const pw = await password("금고에서 꺼낼까요?", `${item.path}\n\n원래 자리로 되돌려요. 같은 이름이 있으면 ‘이름 (2)’로 둬요.`);
  if (pw === null) return;
  let answer = await lockCall("/api/lock/take_out", {path: item.path, password: pw});
  if (answer?.need_folder) {
    if (!(await ask({title: "꺼낼 곳을 골라 주세요", text: answer.need_folder}))) return;
    const folder = await pickPath({title: "꺼낼 곳"});
    if (!folder) return;
    answer = await lockCall("/api/lock/take_out", {path: item.path, password: pw, folder});
  }
  if (answer?.path) say("lock-message", `꺼냈어요: ${answer.path}. 다음 [색인 갱신]부터 목록에 나와요.`, "ok");
}

$("lock-password").addEventListener("click", async () => {
  let old;
  if (lockState.password) {
    old = await password("지금 비밀번호");
    if (old === null) return;
  }
  const next = await password("새 비밀번호", "4자 이상. 잊으면 README의 ‘비밀번호를 잊었을 때’ 명령으로 풀어요.");
  if (next === null) return;
  if (await password("새 비밀번호 확인") !== next) return say("lock-message", "두 번 넣은 비밀번호가 달라요.", "error");
  await lockCall("/api/lock/password", {old, new: next}, "비밀번호를 정했어요.");
});

function lockRow(item) {
  const row = el("div");
  row.insertAdjacentHTML("beforeend", FOLDER_ICON);
  const where = item.vault ? "금고" : "제자리";
  const status = item.missing ? "찾을 수 없음" : item.open ? "열림" : "잠김";
  row.append(el("span", {className: "mono", textContent: `${where}·${status}  ${item.path}`,
    title: item.missing ? "앱 밖에서 옮기거나 지운 것 같아요. [잠금 풀기]로 목록에서 뺄 수 있어요." : item.path}));
  const button = (text, act) => {
    const b = el("button", {className: "btn s", textContent: text});
    b.addEventListener("click", act);
    row.append(b);
  };
  if (item.open) button("다시 잠그기", () => lockCall("/api/lock/close", {path: item.path}, item.vault ? "금고를 다시 잠갔어요." : "다시 잠갔어요."));
  else if (!item.missing) button("열기", async () => {
    const pw = await password(item.vault ? "금고에서 열기" : "잠금 폴더 열기", item.vault ? `${item.path}\n\n금고를 잠시 풀고 열어요.` : item.path);
    if (pw !== null) await lockCall("/api/lock/open", {path: item.path, password: pw});
  });
  if (item.vault) button("꺼내기", () => takeOut(item));
  else button("잠금 풀기", async () => {
    const pw = await password("잠금을 아주 풀까요?", `${item.path}\n\n다음 [색인 갱신]부터 목록에 다시 나와요.`);
    if (pw !== null) await lockCall("/api/lock/release", {path: item.path, password: pw}, "잠금을 풀었어요.");
  });
  return row;
}

load();
