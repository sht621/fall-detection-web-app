const state = {
  detections: new Map(),
  selectedId: null,
  eventSource: null,
};

const elements = {
  list: document.getElementById("detection-list"),
  notification: document.getElementById("notification"),
  sseStatus: document.getElementById("sse-status"),
  summary: document.getElementById("selected-summary"),
  video: document.getElementById("video-player"),
  confirm: document.getElementById("confirm-fall"),
  reject: document.getElementById("reject-fall"),
};

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ja-JP");
}

function labelFor(value) {
  const labels = {
    CAPTURING: "準備中",
    UPLOADING: "アップロード中",
    READY: "準備完了",
    FAILED: "失敗",
    UNREVIEWED: "未確認",
    FALL_CONFIRMED: "転倒確認済み",
    NO_FALL: "転倒ではない",
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
  elements.notification.textContent = message;
  elements.notification.classList.add("visible");
  window.setTimeout(() => elements.notification.classList.remove("visible"), 4500);
}

function setConnectionStatus(connected) {
  elements.sseStatus.textContent = connected ? "SSE 接続中" : "SSE 切断中";
  elements.sseStatus.className = `connection ${connected ? "connected" : "disconnected"}`;
}

function upsertDetections(detections) {
  detections.forEach((detection) => state.detections.set(detection.event_id, detection));
}

async function loadDetections() {
  const response = await fetch("/api/detections", { cache: "no-store" });
  if (!response.ok) throw new Error("イベント一覧を取得できませんでした。");
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
  if (!detection) {
    elements.summary.textContent = "イベントを選択してください。";
    elements.video.removeAttribute("src");
    elements.video.load();
    return;
  }
  elements.summary.textContent = `${detection.camera_id} / ${formatDate(detection.detected_at)} / ${labelFor(detection.video_status)}`;
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_result: reviewResult }),
  });
  if (!response.ok) {
    showNotification("確認結果を保存できませんでした。");
    return;
  }
  upsertDetections([await response.json()]);
  render();
  showNotification("確認結果を保存しました。");
}

function connectEvents() {
  state.eventSource = new EventSource("/api/events");
  state.eventSource.addEventListener("open", async () => {
    setConnectionStatus(true);
    try {
      await loadDetections();
    } catch (error) {
      showNotification(error.message);
    }
  });
  state.eventSource.addEventListener("error", () => setConnectionStatus(false));
  state.eventSource.addEventListener("fall_detected", async (event) => {
    const { event_id: eventId } = JSON.parse(event.data);
    await refreshDetection(eventId);
    showNotification("新しい転倒検知イベントを受信しました。");
  });
  state.eventSource.addEventListener("video_ready", async (event) => {
    const { event_id: eventId } = JSON.parse(event.data);
    await refreshDetection(eventId);
    showNotification("検知映像の準備が完了しました。");
  });
}

elements.confirm.addEventListener("click", () => submitReview("FALL_CONFIRMED"));
elements.reject.addEventListener("click", () => submitReview("NO_FALL"));

window.addEventListener("beforeunload", () => state.eventSource?.close());

loadDetections().catch((error) => showNotification(error.message));
connectEvents();

