const state = {
  detections: new Map(),
  selectedId: null,
  eventSource: null,
  csrfToken: null,
  username: null,
};

const elements = {
  layout: document.querySelector(".layout"),
  loginPanel: document.getElementById("login-panel"),
  loginForm: document.getElementById("login-form"),
  loginUsername: document.getElementById("login-username"),
  loginPassword: document.getElementById("login-password"),
  currentUser: document.getElementById("current-user"),
  logout: document.getElementById("logout"),
  list: document.getElementById("detection-list"),
  notification: document.getElementById("notification"),
  sseStatus: document.getElementById("sse-status"),
  summary: document.getElementById("selected-summary"),
  video: document.getElementById("video-player"),
  confirm: document.getElementById("confirm-fall"),
  reject: document.getElementById("reject-fall"),
  deleteEvent: document.getElementById("delete-event"),
};

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ja-JP");
}

function labelFor(value) {
  const labels = {
    CAPTURING: "生成中",
    UPLOADING: "送信中",
    READY: "再生可能",
    FAILED: "処理失敗",
    UNREVIEWED: "未確認",
    FALL_CONFIRMED: "転倒",
    NO_FALL: "誤検知",
  };
  return labels[value] || value;
}

function badge(value) {
  const item = document.createElement("span");
  item.className = `badge ${value.toLowerCase()}`;
  item.textContent = labelFor(value);
  return item;
}

function showNotification(message) {
  elements.notification.hidden = false;
  elements.notification.textContent = message;
  elements.notification.classList.add("visible");
  window.setTimeout(() => {
    elements.notification.classList.remove("visible");
    elements.notification.textContent = "";
    elements.notification.hidden = true;
  }, 4500);
}

function clearNotification() {
  elements.notification.classList.remove("visible");
  elements.notification.textContent = "";
  elements.notification.hidden = true;
}

function setConnectionStatus(status) {
  if (status === "reconnecting") {
    elements.sseStatus.textContent = "通知サーバーに再接続しています…";
    elements.sseStatus.hidden = false;
    elements.sseStatus.className = "connection disconnected";
    return;
  }

  elements.sseStatus.textContent = "";
  elements.sseStatus.hidden = true;
  elements.sseStatus.className = "connection";
}

function setAuthenticated(user) {
  state.username = user.username;
  state.csrfToken = user.csrf_token;
  elements.loginPanel.hidden = true;
  elements.layout.hidden = false;
  elements.logout.hidden = false;
  elements.currentUser.textContent = user.username;
}

function setUnauthenticated() {
  state.username = null;
  state.csrfToken = null;
  state.detections = new Map();
  state.selectedId = null;
  state.eventSource?.close();
  state.eventSource = null;
  clearNotification();
  setConnectionStatus("hidden");
  elements.currentUser.textContent = "";
  elements.logout.hidden = true;
  elements.layout.hidden = true;
  elements.loginPanel.hidden = false;
  elements.loginPassword.value = "";
  render();
}

function upsertDetections(detections) {
  detections.forEach((detection) => state.detections.set(detection.event_id, detection));
}

async function loadSession() {
  const response = await fetch("/api/me", { cache: "no-store" });
  if (response.status === 401) {
    setUnauthenticated();
    return false;
  }
  if (!response.ok) throw new Error("ログイン状態の確認に失敗しました。");
  setAuthenticated(await response.json());
  return true;
}

async function loadDetections() {
  const response = await fetch("/api/detections", { cache: "no-store" });
  if (response.status === 401) {
    setUnauthenticated();
    return;
  }
  if (!response.ok) throw new Error("検知記録を取得できませんでした。");
  const detections = await response.json();
  state.detections = new Map();
  upsertDetections(detections);
  render();
}

async function refreshDetection(eventId) {
  const response = await fetch(`/api/detections/${encodeURIComponent(eventId)}`, { cache: "no-store" });
  if (response.ok) {
    upsertDetections([await response.json()]);
    render();
  } else {
    await loadDetections();
  }
}

function render() {
  const detections = [...state.detections.values()].sort((a, b) => b.detected_at.localeCompare(a.detected_at));
  elements.list.replaceChildren();
  detections.forEach((detection) => {
    const row = document.createElement("tr");
    row.tabIndex = 0;
    row.className = detection.event_id === state.selectedId ? "selected" : "";
    row.addEventListener("click", () => selectDetection(detection.event_id));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") selectDetection(detection.event_id);
    });
    const values = [detection.camera_id, formatDate(detection.detected_at)];
    values.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    const videoCell = document.createElement("td");
    videoCell.append(badge(detection.video_status));
    row.append(videoCell);
    const reviewCell = document.createElement("td");
    reviewCell.append(badge(detection.review_status));
    row.append(reviewCell);
    elements.list.append(row);
  });
  renderSelected();
}

function selectDetection(eventId) {
  state.selectedId = eventId;
  render();
}

function renderSelected() {
  const detection = state.detections.get(state.selectedId);
  const hasSelection = Boolean(detection);
  elements.confirm.disabled = !hasSelection;
  elements.reject.disabled = !hasSelection;
  elements.deleteEvent.disabled = !hasSelection;
  if (!detection) {
    elements.summary.textContent = "確認する検知記録を選択してください。";
    elements.video.removeAttribute("src");
    elements.video.load();
    return;
  }
  elements.summary.textContent = `カメラ：${detection.camera_id} / 検知日時：${formatDate(detection.detected_at)} / 動画：${labelFor(detection.video_status)}`;
  if (detection.video_status === "READY") {
    const source = `/api/detections/${encodeURIComponent(detection.event_id)}/video`;
    if (elements.video.getAttribute("src") !== source) {
      elements.video.src = source;
      elements.video.load();
    }
  } else {
    elements.video.removeAttribute("src");
    elements.video.load();
  }
}

async function submitReview(reviewResult) {
  if (!state.selectedId) return;
  const response = await fetch(`/api/detections/${encodeURIComponent(state.selectedId)}/review`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrfToken || "" },
    body: JSON.stringify({ review_result: reviewResult }),
  });
  if (response.status === 401) {
    setUnauthenticated();
    return;
  }
  if (!response.ok) {
    showNotification("確認結果を登録できませんでした。");
    return;
  }
  upsertDetections([await response.json()]);
  render();
  showNotification("確認結果を登録しました。");
}

async function deleteSelectedDetection() {
  if (!state.selectedId) return;
  const detection = state.detections.get(state.selectedId);
  if (!detection) return;
  const ok = window.confirm(
    `${formatDate(detection.detected_at)}の検知記録と動画を削除します。よろしいですか？`
  );
  if (!ok) return;

  const response = await fetch(`/api/detections/${encodeURIComponent(state.selectedId)}`, {
    method: "DELETE",
    headers: { "X-CSRF-Token": state.csrfToken || "" },
  });
  if (response.status === 401) {
    setUnauthenticated();
    return;
  }
  if (!response.ok) {
    showNotification("検知記録を削除できませんでした。");
    return;
  }

  state.detections.delete(state.selectedId);
  state.selectedId = null;
  render();
  showNotification("検知記録と動画を削除しました。");
}

function connectEvents() {
  state.eventSource?.close();
  state.eventSource = new EventSource("/api/events");
  state.eventSource.addEventListener("open", async () => {
    setConnectionStatus("connected");
    try {
      await loadDetections();
    } catch (error) {
      showNotification(error.message);
    }
  });
  state.eventSource.addEventListener("error", () => setConnectionStatus("reconnecting"));
  state.eventSource.addEventListener("fall_detected", async (event) => {
    const { event_id: eventId } = JSON.parse(event.data);
    await refreshDetection(eventId);
    showNotification("転倒を検知しました。");
  });
  state.eventSource.addEventListener("video_ready", async (event) => {
    const { event_id: eventId } = JSON.parse(event.data);
    await refreshDetection(eventId);
    showNotification("転倒前後の動画を再生できます。");
  });
}

async function submitLogin(event) {
  event.preventDefault();
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: elements.loginUsername.value,
      password: elements.loginPassword.value,
    }),
  });
  if (response.status === 401) {
    showNotification("ユーザー名またはパスワードが正しくありません。");
    return;
  }
  if (!response.ok) {
    showNotification("ログイン処理に失敗しました。");
    return;
  }
  setAuthenticated(await response.json());
  elements.loginPassword.value = "";
  await loadDetections();
  connectEvents();
}

async function logout() {
  clearNotification();
  await fetch("/api/logout", {
    method: "POST",
    headers: { "X-CSRF-Token": state.csrfToken || "" },
  });
  setUnauthenticated();
}

elements.confirm.addEventListener("click", () => submitReview("FALL_CONFIRMED"));
elements.reject.addEventListener("click", () => submitReview("NO_FALL"));
elements.deleteEvent.addEventListener("click", deleteSelectedDetection);
elements.loginForm.addEventListener("submit", submitLogin);
elements.logout.addEventListener("click", logout);

window.addEventListener("beforeunload", () => state.eventSource?.close());

loadSession()
  .then(async (authenticated) => {
    if (!authenticated) return;
    await loadDetections();
    connectEvents();
  })
  .catch((error) => {
    setUnauthenticated();
    showNotification(error.message);
  });
