/* ═══════════════════════════════════════════════════
   YouTube Automation Tool - Frontend Logic
═══════════════════════════════════════════════════ */

let currentJobId = null;
let pollInterval = null;
let currentVideoPath = null;
let currentVideoTitle = null;

// ════════════════════════════════════════════
// INIT
// ════════════════════════════════════════════
document.addEventListener("DOMContentLoaded", () => {
  setupNavigation();
  checkYouTubeStatus();
  loadSettings();

  // Listen for OAuth popup message
  window.addEventListener("message", (e) => {
    if (e.data === "yt_auth_success") {
      checkYouTubeStatus();
      showToast("✅ YouTube সফলভাবে কানেক্ট হয়েছে!", "success");
    }
  });
});

// ════════════════════════════════════════════
// NAVIGATION
// ════════════════════════════════════════════
function setupNavigation() {
  const navItems = document.querySelectorAll(".nav-item");
  navItems.forEach(item => {
    item.addEventListener("click", e => {
      e.preventDefault();
      const tab = item.dataset.tab;
      switchTab(tab);
      navItems.forEach(n => n.classList.remove("active"));
      item.classList.add("active");
    });
  });
}

const tabTitles = {
  analyze: "🎬 AI ভাইরাল ক্লিপ তৈরি",
  manual: "✂️ ম্যানুয়াল ক্রপ",
  schedule: "⏰ অটো আপলোড শিডিউল",
  logs: "📋 আপলোড লগ",
  settings: "⚙️ সেটিংস"
};

function switchTab(tab) {
  document.querySelectorAll(".tab-content").forEach(t => t.classList.add("hidden"));
  document.getElementById(`tab-${tab}`).classList.remove("hidden");
  document.getElementById("pageTitle").textContent = tabTitles[tab] || tab;
  if (tab === "schedule") loadSchedules();
  if (tab === "logs") loadLogs();
}

function toggleSidebar() {
  document.getElementById("sidebar").classList.toggle("open");
}

// ════════════════════════════════════════════
// YOUTUBE AUTH
// ════════════════════════════════════════════
async function checkYouTubeStatus() {
  const resp = await apiFetch("/api/youtube/status");
  const ytStatus = document.getElementById("ytStatus");
  const btnConnect = document.getElementById("btnConnect");
  const btnLogout = document.getElementById("btnLogout");
  const channelBadge = document.getElementById("channelBadge");

  if (resp.connected) {
    ytStatus.className = "yt-status connected";
    ytStatus.innerHTML = `<i class="fa-brands fa-youtube"></i><span>${resp.channel?.title || "Connected"}</span>`;
    btnConnect.classList.add("hidden");
    btnLogout.classList.remove("hidden");
    if (resp.channel) {
      channelBadge.classList.remove("hidden");
      document.getElementById("channelName").textContent = resp.channel.title;
      if (resp.channel.thumbnail) document.getElementById("channelThumb").src = resp.channel.thumbnail;
    }
  } else {
    ytStatus.className = "yt-status disconnected";
    ytStatus.innerHTML = `<i class="fa-brands fa-youtube"></i><span>YouTube কানেক্ট নেই</span>`;
    btnConnect.classList.remove("hidden");
    btnLogout.classList.add("hidden");
    channelBadge.classList.add("hidden");
  }
}

async function connectYouTube() {
  const resp = await apiFetch("/api/youtube/auth-url");
  if (resp.auth_url) {
    window.open(resp.auth_url, "_blank", "width=600,height=700");
  } else {
    showToast("❌ Auth URL পাওয়া যায়নি। API Keys সেটিংস চেক করুন।", "error");
  }
}

async function logoutYouTube() {
  if (!confirm("YouTube চ্যানেল ডিসকানেক্ট করবেন?")) return;
  await apiFetch("/api/youtube/logout", { method: "POST" });
  checkYouTubeStatus();
  showToast("YouTube ডিসকানেক্ট হয়েছে।", "info");
}

// ════════════════════════════════════════════
// ANALYZE VIDEO
// ════════════════════════════════════════════
async function startAnalysis() {
  const url = document.getElementById("videoUrl").value.trim();
  if (!url) { showToast("⚠️ ভিডিও URL দিন!", "error"); return; }

  const numClips = parseInt(document.getElementById("numClips").value) || 5;
  const useGemini = document.getElementById("useGemini").checked;
  const useGrok = document.getElementById("useGrok").checked;
  const whisperModel = document.getElementById("whisperModel").value;

  // Show progress card
  document.getElementById("progressCard").classList.remove("hidden");
  document.getElementById("resultsArea").innerHTML = "";
  setStep("dl");
  updateProgress(2, "শুরু হচ্ছে...");

  const resp = await apiFetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, num_clips: numClips, use_gemini: useGemini, use_grok: useGrok, whisper_model: whisperModel })
  });

  if (!resp.job_id) {
    showToast("❌ Job শুরু করা যায়নি।", "error");
    return;
  }

  currentJobId = resp.job_id;
  pollInterval = setInterval(() => pollJobStatus(resp.job_id), 1500);
}

async function pollJobStatus(jobId) {
  const data = await apiFetch(`/api/job/${jobId}`);
  const { status, progress, message, result } = data;

  updateProgress(progress, message);

  // Update steps
  if (progress > 5 && progress <= 52) setStep("dl");
  else if (progress > 52 && progress <= 65) setStep("tr");
  else if (progress > 65 && progress <= 75) setStep("ai");
  else if (progress > 75 && progress < 100) setStep("cp");
  else if (status === "done") setStep("done");

  if (status === "done") {
    clearInterval(pollInterval);
    document.getElementById("progressCard").classList.add("hidden");
    renderResults(result);
    showToast(`✅ ${result.total_clips} টি শর্টস ক্লিপ তৈরি হয়েছে!`, "success");
  } else if (status === "error") {
    clearInterval(pollInterval);
    document.getElementById("progressCard").classList.add("hidden");
    showToast(`❌ ${message}`, "error");
  }
}

function updateProgress(pct, msg) {
  document.getElementById("progressBar").style.width = `${pct}%`;
  document.getElementById("progressPct").textContent = `${Math.round(pct)}%`;
  if (msg) document.getElementById("progressMsg").textContent = msg;
}

function setStep(activeStep) {
  const steps = ["dl", "tr", "ai", "cp", "done"];
  const idx = steps.indexOf(activeStep);
  steps.forEach((s, i) => {
    const el = document.getElementById(`step-${s}`);
    if (!el) return;
    if (i < idx) el.className = "step done";
    else if (i === idx) el.className = "step active";
    else el.className = "step";
  });
}

// ════════════════════════════════════════════
// RENDER RESULTS
// ════════════════════════════════════════════
function renderResults(result) {
  currentVideoPath = result.video_path;
  currentVideoTitle = result.video_title;

  const area = document.getElementById("resultsArea");

  // Video info banner
  let bannerHTML = `
    <div class="video-info-banner">
      ${result.thumbnail_url ? `<img src="${result.thumbnail_url}" alt="thumb"/>` : ""}
      <div>
        <h3>${result.video_title}</h3>
        <small>⏱ Duration: ${formatDuration(result.video_duration)} &nbsp;|&nbsp; 
               🎬 ${result.total_clips} ক্লিপ &nbsp;|&nbsp; 
               ${result.has_heatmap ? "📊 Heatmap ✅" : "📊 Heatmap ❌"}
        </small>
      </div>
    </div>`;

  // Clips grid
  let clipsHTML = `<div class="clips-grid">`;
  result.clips.forEach(clip => {
    if (clip.error) {
      clipsHTML += `
        <div class="clip-card">
          <div class="clip-info">
            <p class="error-msg">❌ ক্লিপ ${clip.index} ত্রুটি: ${clip.error}</p>
          </div>
        </div>`;
      return;
    }

    const stars = "⭐".repeat(Math.min(5, Math.round(clip.viral_score / 2)));
    const catEmoji = {tip:"💡", funny:"😂", insight:"🔥", story:"📖", controversy:"⚡", highlight:"🎯", general:"🎬"}[clip.category] || "🎬";

    clipsHTML += `
    <div class="clip-card" id="clipCard_${clip.index}">
      <div class="clip-thumb-wrap">
        <video src="${clip.clip_url}" muted playsinline loop
               onmouseenter="this.play()" onmouseleave="this.pause();this.currentTime=0"
               ${clip.thumbnail_url ? `poster="${clip.thumbnail_url}"` : ""}>
        </video>
        <span class="rank-badge">#${clip.rank}</span>
        <span class="viral-badge">${stars} ${clip.viral_score?.toFixed(1)}</span>
      </div>
      <div class="clip-info">
        <div class="clip-title">${catEmoji} ${clip.title}</div>
        <div class="clip-meta">
          <span><i class="fa fa-clock"></i> ${clip.duration}s</span>
          <span><i class="fa fa-tag"></i> ${clip.category}</span>
          <span><i class="fa fa-film"></i> ${formatDuration(clip.start)} → ${formatDuration(clip.end)}</span>
        </div>
        ${clip.hook ? `<div class="clip-hook">"${clip.hook}"</div>` : ""}
        <div style="font-size:0.8rem;color:var(--text2);margin-top:4px">${clip.hashtags?.split(" ").slice(0,5).join(" ")}</div>
      </div>
      <div class="clip-actions">
        <a href="${clip.clip_url}" download class="btn-sm btn-secondary"><i class="fa fa-download"></i></a>
        <button class="btn-sm btn-secondary" onclick="openTextModal('${clip.clip_path}')"><i class="fa fa-text-height"></i> টেক্সট</button>
        <button class="btn-sm btn-primary" onclick="openUploadModal('${clip.clip_path}', '${clip.thumbnail_path || ''}', '${escapeAttr(clip.title)}', '${escapeAttr(clip.description)}', '${escapeAttr(clip.hashtags)}')">
          <i class="fa-brands fa-youtube"></i> আপলোড
        </button>
      </div>
    </div>`;
  });
  clipsHTML += `</div>`;

  // Set manual crop path from first clip's video
  if (result.video_path) {
    document.getElementById("manualVideoPath").value = result.video_path;
  }

  area.innerHTML = bannerHTML + `
    <div class="card">
      <h2><i class="fa fa-film"></i> তৈরি হওয়া শর্টস ক্লিপ</h2>
      <div style="display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap">
        <button class="btn-secondary btn-sm" onclick="uploadAllClips()"><i class="fa fa-upload"></i> সব আপলোড করুন</button>
        <button class="btn-secondary btn-sm" onclick="switchTab('manual'); document.querySelectorAll('.nav-item')[1].classList.add('active')">
          <i class="fa fa-scissors"></i> ম্যানুয়াল ক্রপ
        </button>
      </div>
      ${clipsHTML}
    </div>`;
}

// ════════════════════════════════════════════
// MANUAL CROP
// ════════════════════════════════════════════
async function doManualCrop() {
  const videoPath = document.getElementById("manualVideoPath").value.trim();
  const start = document.getElementById("manualStart").value.trim();
  const end = document.getElementById("manualEnd").value.trim();
  const name = document.getElementById("manualName").value.trim() || `manual_${Date.now()}`;

  if (!videoPath || !start || !end) {
    showToast("⚠️ সব ফিল্ড পূরণ করুন!", "error");
    return;
  }

  const result = document.getElementById("manualResult");
  result.innerHTML = `<div class="card"><p class="text-muted">✂️ ক্রপ হচ্ছে...</p></div>`;

  const resp = await apiFetch("/api/manual-crop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_path: videoPath, start_time: start, end_time: end, output_name: name })
  });

  if (resp.clip_url) {
    result.innerHTML = `
      <div class="card">
        <h2><i class="fa fa-check"></i> ক্রপ সফল!</h2>
        <video src="${resp.clip_url}" controls style="max-width:300px;border-radius:8px;"></video>
        <div style="margin-top:12px;display:flex;gap:8px;">
          <a href="${resp.clip_url}" download class="btn-primary btn-sm"><i class="fa fa-download"></i> ডাউনলোড</a>
          <button class="btn-secondary btn-sm" onclick="openTextModal('${resp.clip_path}')"><i class="fa fa-text-height"></i> টেক্সট যোগ</button>
          <button class="btn-primary btn-sm" onclick="openUploadModal('${resp.clip_path}', '', '', '', '#shorts #viral')">
            <i class="fa-brands fa-youtube"></i> আপলোড
          </button>
        </div>
      </div>`;
    showToast(`✅ ক্লিপ তৈরি হয়েছে (${resp.duration}s)`, "success");
  } else {
    result.innerHTML = `<div class="card"><p class="error-msg">❌ ত্রুটি: ${JSON.stringify(resp)}</p></div>`;
  }
}

// ════════════════════════════════════════════
// TEXT OVERLAY MODAL
// ════════════════════════════════════════════
function openTextModal(videoPath) {
  document.getElementById("modalVideoPath").value = videoPath;
  document.getElementById("overlayText").value = "";
  document.getElementById("textModal").classList.remove("hidden");
}

async function applyTextOverlay() {
  const videoPath = document.getElementById("modalVideoPath").value;
  const text = document.getElementById("overlayText").value.trim();
  const position = document.getElementById("overlayPos").value;
  const fontSize = parseInt(document.getElementById("overlayFontSize").value);
  const opacity = parseFloat(document.getElementById("overlayOpacity").value);

  if (!text) { showToast("টেক্সট লিখুন!", "error"); return; }

  closeModal("textModal");
  showToast("⏳ টেক্সট যোগ হচ্ছে...", "info");

  const resp = await apiFetch("/api/add-text", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_path: videoPath, text, position, font_size: fontSize, bg_opacity: opacity })
  });

  if (resp.clip_url) {
    showToast("✅ টেক্সট যোগ হয়েছে!", "success");
    window.open(resp.clip_url, "_blank");
  } else {
    showToast("❌ টেক্সট যোগ ব্যর্থ!", "error");
  }
}

// ════════════════════════════════════════════
// UPLOAD MODAL
// ════════════════════════════════════════════
function openUploadModal(videoPath, thumbPath, title, description, hashtags) {
  document.getElementById("uploadVideoPath").value = videoPath;
  document.getElementById("uploadThumbPath").value = thumbPath || "";
  document.getElementById("uploadTitle").value = decodeAttr(title) || "";
  document.getElementById("uploadDesc").value = decodeAttr(description) || "";
  document.getElementById("uploadHashtags").value = decodeAttr(hashtags) || "#shorts #viral #trending";
  document.getElementById("uploadModal").classList.remove("hidden");
}

async function doUpload() {
  const videoPath = document.getElementById("uploadVideoPath").value;
  const thumbPath = document.getElementById("uploadThumbPath").value;
  const title = document.getElementById("uploadTitle").value;
  const desc = document.getElementById("uploadDesc").value;
  const hashtags = document.getElementById("uploadHashtags").value;
  const privacy = document.getElementById("uploadPrivacy").value;

  closeModal("uploadModal");
  showToast("📤 YouTube এ আপলোড হচ্ছে...", "info");

  const resp = await apiFetch("/api/upload-youtube", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      video_path: videoPath,
      title, description: desc, hashtags, privacy,
      thumbnail_path: thumbPath
    })
  });

  if (resp.success) {
    showToast(`✅ আপলোড সফল! <a href="${resp.youtube_url}" target="_blank">দেখুন</a>`, "success");
  } else {
    showToast(`❌ আপলোড ব্যর্থ: ${resp.detail || JSON.stringify(resp)}`, "error");
  }
}

async function uploadAllClips() {
  showToast("সব ক্লিপ আপলোড ফিচার: প্রতিটি ক্লিপের আপলোড বাটন ক্লিক করুন।", "info");
}

// ════════════════════════════════════════════
// SCHEDULE
// ════════════════════════════════════════════
async function createSchedule() {
  const driveLink = document.getElementById("driveFolderLink").value.trim();
  if (!driveLink) { showToast("Google Drive লিংক দিন!", "error"); return; }

  const times = Array.from(document.querySelectorAll(".time-check:checked")).map(c => c.value);
  if (times.length === 0) { showToast("কমপক্ষে একটি সময় সিলেক্ট করুন!", "error"); return; }

  const resp = await apiFetch("/api/schedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ drive_folder_link: driveLink, upload_times: times })
  });

  if (resp.success) {
    showToast(resp.message, "success");
    loadSchedules();
  } else {
    showToast("❌ শিডিউল তৈরি ব্যর্থ!", "error");
  }
}

async function loadSchedules() {
  const data = await apiFetch("/api/schedule");
  const schedList = document.getElementById("schedulesList");
  const nextJobs = document.getElementById("nextJobs");

  if (data.schedules.length === 0) {
    schedList.innerHTML = `<p class="text-muted">কোনো শিডিউল নেই।</p>`;
  } else {
    schedList.innerHTML = data.schedules.map(s => `
      <div class="schedule-item">
        <div class="schedule-info">
          <strong>⏰ ${s.upload_times.join(", ")} BD সময়</strong>
          <small>${s.drive_folder_link.substring(0, 50)}...</small>
          <small>শেষ আপলোড: ${s.last_upload ? new Date(s.last_upload).toLocaleString("bn-BD") : "এখনো হয়নি"}</small>
        </div>
        <button class="btn-danger btn-sm" onclick="deleteSchedule('${s.schedule_id}')">
          <i class="fa fa-trash"></i>
        </button>
      </div>`).join("");
  }

  if (data.scheduler_jobs.length > 0) {
    nextJobs.innerHTML = data.scheduler_jobs.map(j => `
      <div class="schedule-item">
        <div class="schedule-info">
          <strong>${j.name}</strong>
          <small>পরবর্তী: ${j.next_run ? new Date(j.next_run).toLocaleString("bn-BD") : "N/A"}</small>
        </div>
      </div>`).join("");
  } else {
    nextJobs.innerHTML = `<p class="text-muted">কোনো scheduled job নেই।</p>`;
  }
}

async function deleteSchedule(id) {
  if (!confirm("এই শিডিউল মুছবেন?")) return;
  await apiFetch(`/api/schedule/${id}`, { method: "DELETE" });
  showToast("শিডিউল মোছা হয়েছে।", "info");
  loadSchedules();
}

// ════════════════════════════════════════════
// LOGS
// ════════════════════════════════════════════
async function loadLogs() {
  const logs = await apiFetch("/api/upload-logs");
  const el = document.getElementById("logsList");
  if (!logs.length) {
    el.innerHTML = `<p class="text-muted">কোনো লগ নেই।</p>`;
    return;
  }
  el.innerHTML = logs.map(l => `
    <div class="log-item">
      <span class="log-status ${l.status}">${l.status === "success" ? "✅ সফল" : l.status === "failed" ? "❌ ব্যর্থ" : "⏳ চলছে"}</span>
      <div style="flex:1">
        <strong>${l.title}</strong>
        ${l.video_id ? `<a href="https://youtu.be/${l.video_id}" target="_blank" style="color:var(--accent);font-size:0.8rem;margin-left:8px">${l.video_id}</a>` : ""}
      </div>
      <span style="font-size:0.75rem;color:var(--text2)">${new Date(l.uploaded_at).toLocaleString("bn-BD")}</span>
    </div>`).join("");
}

// ════════════════════════════════════════════
// SETTINGS
// ════════════════════════════════════════════
async function loadSettings() {
  try {
    const s = await apiFetch("/api/settings");
    // Show masked values
    if (s.gemini_api_key) document.getElementById("geminiKey").placeholder = s.gemini_api_key;
    if (s.grok_api_key) document.getElementById("grokKey").placeholder = s.grok_api_key;
    if (s.youtube_client_id) document.getElementById("ytClientId").placeholder = s.youtube_client_id;
    if (s.youtube_client_secret) document.getElementById("ytClientSecret").placeholder = s.youtube_client_secret;
    if (s.google_drive_api_key) document.getElementById("driveKey").placeholder = s.google_drive_api_key;
    if (s.openai_api_key) document.getElementById("openaiKey").placeholder = s.openai_api_key;
  } catch (e) {}
  // Load to env
  await apiFetch("/api/settings/load-to-env", { method: "POST" });
}

async function saveSettings() {
  const payload = {
    gemini_api_key: document.getElementById("geminiKey").value,
    grok_api_key: document.getElementById("grokKey").value,
    youtube_client_id: document.getElementById("ytClientId").value,
    youtube_client_secret: document.getElementById("ytClientSecret").value,
    google_drive_api_key: document.getElementById("driveKey").value,
    openai_api_key: document.getElementById("openaiKey").value,
  };
  const resp = await apiFetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (resp.success) {
    showToast("✅ " + resp.message, "success");
    checkYouTubeStatus();
  }
}

// ════════════════════════════════════════════
// UTILITIES
// ════════════════════════════════════════════
function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
}

function showToast(msg, type = "info") {
  const t = document.getElementById("toast");
  t.innerHTML = msg;
  t.className = `toast ${type}`;
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 4000);
}

function formatDuration(seconds) {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;
  return `${m}:${String(sec).padStart(2,"0")}`;
}

function escapeAttr(str) {
  if (!str) return "";
  return encodeURIComponent(str);
}

function decodeAttr(str) {
  if (!str) return "";
  try { return decodeURIComponent(str); } catch { return str; }
}

async function apiFetch(url, opts = {}) {
  try {
    const resp = await fetch(url, opts);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      console.error("API Error:", err);
      return err;
    }
    return await resp.json();
  } catch (e) {
    console.error("Fetch error:", e);
    return { error: e.message };
  }
}
