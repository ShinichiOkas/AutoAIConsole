(() => {
  const DEFAULT_MODEL = "gemma4:31b-cloud";

  const state = {
    status: {
      mode: "autonomous",
      engine_state: "idle",
      model: DEFAULT_MODEL,
      ollama_url: "http://127.0.0.1:11434",
      idle_threshold_sec: 2.5,
      pty_running: false,
      pty_command: "",
      pty_cwd: "",
      auto_reply_count: 0,
      silence_sec: 0,
      confidence: 0,
      last_trigger: "",
      last_eval_note: "",
      elapsed_sec: 0,
    },
    mission: { goal: "", policy: "", boundaries: [] },
    presets: [],
    renderedIds: new Set(),
    sendingChat: false,
  };

  const $ = (id) => document.getElementById(id);
  const el = {
    statusPill: $("statusPill"),
    statusLabel: $("statusLabel"),
    heartbeatText: $("heartbeatText"),
    autoCountText: $("autoCountText"),
    modelSelect: $("modelSelect"),
    settingsModelSelect: $("settingsModelSelect"),
    ollamaDot: $("ollamaDot"),
    ollamaStatusNote: $("ollamaStatusNote"),
    chatTimeline: $("chatTimeline"),
    chatInput: $("chatInput"),
    sendChatBtn: $("sendChatBtn"),
    agentPreset: $("agentPreset"),
    customCmdInput: $("customCmdInput"),
    cwdInput: $("cwdInput"),
    controlBadge: $("controlBadge"),
    resumeAutoBtn: $("resumeAutoBtn"),
    evalNowBtn: $("evalNowBtn"),
    startTermBtn: $("startTermBtn"),
    stopTermBtn: $("stopTermBtn"),
    ctrlCBtn: $("ctrlCBtn"),
    xtermMount: $("xtermMount"),
    termEmptyOverlay: $("termEmptyOverlay"),
    monStateText: $("monStateText"),
    monSilenceText: $("monSilenceText"),
    monConfBar: $("monConfBar"),
    monConfPct: $("monConfPct"),
    monTriggerText: $("monTriggerText"),
    missionGoalView: $("missionGoalView"),
    missionPolicyView: $("missionPolicyView"),
    missionBoundaryView: $("missionBoundaryView"),
    missionBody: $("missionBody"),
    collapseMissionBtn: $("collapseMissionBtn"),
    toastContainer: $("toastContainer"),
  };

  function toast(message, kind = "info") {
    const div = document.createElement("div");
    div.className = `toast ${kind === "warn" ? "toast-warn" : kind === "error" ? "toast-error" : ""}`;
    div.textContent = message;
    el.toastContainer.appendChild(div);
    setTimeout(() => div.remove(), 3800);
  }

  async function postJson(url, body = {}) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await res.json();
  }

  // ========== Real xterm.js Setup (Authentic CLI Coding Agent Screen) ==========
  const term = new window.Terminal({
    cursorBlink: true,
    cursorStyle: "bar",
    scrollback: 5000,
    fontFamily: '"Cascadia Mono", "Cascadia Code", "JetBrains Mono", "Consolas", "MS Gothic", monospace',
    fontSize: 13,
    lineHeight: 1.28,
    convertEol: false,
    allowProposedApi: true,
    theme: {
      background: "#040507",
      foreground: "#e5e7eb",
      cursor: "#38bdf8",
      cursorAccent: "#040507",
      selectionBackground: "rgba(56, 189, 248, 0.3)",
      black: "#090a0f",
      red: "#f87171",
      green: "#34d399",
      yellow: "#facc15",
      blue: "#60a5fa",
      magenta: "#c084fc",
      cyan: "#22d3ee",
      white: "#e5e7eb",
      brightBlack: "#64748b",
      brightRed: "#fca5a5",
      brightGreen: "#6ee7b7",
      brightYellow: "#fde047",
      brightBlue: "#93c5fd",
      brightMagenta: "#d8b4fe",
      brightCyan: "#67e8f9",
      brightWhite: "#ffffff",
    },
  });

  const fitAddon = new window.FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(el.xtermMount);

  let termWs = null;
  let eventsWs = null;

  function sendTerminalResize() {
    try {
      fitAddon.fit();
      if (termWs && termWs.readyState === WebSocket.OPEN) {
        termWs.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
    } catch {
      // Ignore transient fit errors while hidden
    }
  }

  // Handle copy/paste cleanly inside xterm.js
  term.attachCustomKeyEventHandler((ev) => {
    if (ev.type === "keydown" && ev.ctrlKey && ev.key.toLowerCase() === "c" && term.hasSelection()) {
      navigator.clipboard?.writeText(term.getSelection()).catch(() => {});
      return false;
    }
    if (ev.type === "keydown" && ev.ctrlKey && ev.key.toLowerCase() === "v") {
      navigator.clipboard?.readText().then((txt) => {
        if (txt && termWs && termWs.readyState === WebSocket.OPEN) {
          termWs.send(JSON.stringify({ type: "input", data: txt }));
        }
      }).catch(() => {});
      return false;
    }
    return true;
  });

  term.onData((data) => {
    if (termWs && termWs.readyState === WebSocket.OPEN) {
      termWs.send(JSON.stringify({ type: "input", data }));
    }
  });

  function wsUrl(path) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}${path}`;
  }

  function connectTerminalWs() {
    termWs = new WebSocket(wsUrl("/ws/terminal"));
    termWs.onopen = () => {
      sendTerminalResize();
    };
    termWs.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "output" && typeof msg.data === "string") {
          term.write(msg.data);
        }
      } catch {
        // Ignore malformed frame
      }
    };
    termWs.onclose = () => {
      setTimeout(connectTerminalWs, 1500);
    };
  }

  function connectEventsWs() {
    eventsWs = new WebSocket(wsUrl("/ws/events"));
    eventsWs.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "init" && msg.state) {
          applyFullState(msg.state);
        } else if (msg.type === "status" && msg.status) {
          state.status = msg.status;
          renderStatus();
        } else if (msg.type === "mission" && msg.mission) {
          state.mission = msg.mission;
          renderMission();
        } else if (msg.type === "timeline_item" && msg.item) {
          appendTimelineItem(msg.item);
        } else if (msg.type === "boundary_resolved") {
          markBoundaryResolved(msg.id, msg.label);
        } else if (msg.type === "terminal_reset") {
          term.reset();
          setTimeout(() => {
            sendTerminalResize();
            term.focus();
          }, 50);
        }
      } catch {
        // Ignore malformed event
      }
    };
    eventsWs.onclose = () => {
      setTimeout(connectEventsWs, 1500);
    };
  }

  // ========== Rendering ==========
  function renderMission() {
    el.missionGoalView.textContent = state.mission.goal || "";
    el.missionPolicyView.textContent = state.mission.policy || "";
    el.missionBoundaryView.innerHTML = "";
    (state.mission.boundaries || []).forEach((b) => {
      const span = document.createElement("span");
      span.className = "tag-boundary";
      span.textContent = b;
      el.missionBoundaryView.appendChild(span);
    });
  }

  function renderPresets(presets) {
    if (!presets || !presets.length) return;
    state.presets = presets;
    const prev = el.agentPreset.value;
    el.agentPreset.innerHTML = "";
    presets.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.available ? p.label : `${p.label} (未検出)`;
      el.agentPreset.appendChild(opt);
    });
    if (prev && presets.some((p) => p.id === prev)) {
      el.agentPreset.value = prev;
    } else {
      const firstAvail = presets.find((p) => p.available && p.id !== "custom");
      if (firstAvail) el.agentPreset.value = firstAvail.id;
    }
    el.customCmdInput.classList.toggle("hidden", el.agentPreset.value !== "custom");
  }

  function renderStatus() {
    const s = state.status;
    el.statusPill.className = "status-pill";
    const map = {
      idle: ["state-idle", "IDLE ターミナル未起動", "未起動", ""],
      watching: ["state-watching", "AUTONOMOUS 監視中", "出力ストリーム監視中", "metric-emerald"],
      thinking: ["state-thinking", `THINKING 判断中 (${s.model})`, "Ollama で画面解析中...", "metric-cyan"],
      boundary: ["state-boundary", "BOUNDARY 境界相談中", "境界検知・ユーザー判断待ち", "metric-amber"],
      user: ["state-user", "USER_INPUT ユーザー操作中", "ユーザー直接入力を優先中", "metric-violet"],
      paused: ["state-paused", "PAUSED 一時停止", "監視一時停止中", ""],
      manual: ["state-manual", "MANUAL 手動モード", "手動操作モード（自動回答OFF）", ""],
    };
    const [cls, label, monText, monCls] = map[s.engine_state] || map.idle;
    el.statusPill.classList.add(cls);
    el.statusLabel.textContent = label;
    el.heartbeatText.textContent = `更新: ${s.elapsed_sec ?? 0}秒前`;
    el.autoCountText.textContent = `自動応答: ${s.auto_reply_count ?? 0}回`;

    el.monStateText.textContent = monText;
    el.monStateText.className = `metric-value ${monCls}`;
    el.monSilenceText.textContent = `${Number(s.silence_sec || 0).toFixed(1)}s / ${Number(s.idle_threshold_sec || 2.5).toFixed(1)}s`;
    el.monConfBar.style.width = `${Math.min(100, Math.max(0, s.confidence || 0))}%`;
    el.monConfPct.textContent = `${Math.round(s.confidence || 0)}%`;
    el.monTriggerText.textContent = s.last_eval_note || s.last_trigger || "";

    // Terminal overlay & buttons
    el.termEmptyOverlay.classList.toggle("hidden", Boolean(s.pty_running));
    el.startTermBtn.textContent = s.pty_running ? "再起動" : "起動";
    el.stopTermBtn.classList.toggle("hidden", !s.pty_running);
    el.evalNowBtn.disabled = !s.pty_running;
    el.ctrlCBtn.disabled = !s.pty_running;

    if (!el.cwdInput.value && s.pty_cwd) {
      el.cwdInput.value = s.pty_cwd;
    }

    if (!s.pty_running) {
      el.controlBadge.className = "control-badge owner-idle";
      el.controlBadge.textContent = "未起動";
      el.resumeAutoBtn.classList.add("hidden");
    } else if (s.engine_state === "user") {
      el.controlBadge.className = "control-badge owner-user";
      el.controlBadge.textContent = "ユーザー操作中";
      el.resumeAutoBtn.classList.remove("hidden");
    } else if (s.engine_state === "boundary") {
      el.controlBadge.className = "control-badge owner-boundary";
      el.controlBadge.textContent = "境界停止中";
      el.resumeAutoBtn.classList.add("hidden");
    } else {
      el.controlBadge.className = "control-badge owner-auto";
      el.controlBadge.textContent = s.mode === "autonomous" ? "AutoAI 監視中" : "手動";
      el.resumeAutoBtn.classList.add("hidden");
    }

    document.querySelectorAll(".seg-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.mode === s.mode);
    });
  }

  function appendTimelineItem(item) {
    if (!item || (item.id && state.renderedIds.has(item.id))) return;
    if (item.id) state.renderedIds.add(item.id);

    if (item.kind === "user" || item.kind === "ai") {
      const wrap = document.createElement("div");
      wrap.className = `msg msg-${item.kind}`;
      const meta = document.createElement("div");
      meta.className = "msg-meta";
      if (item.kind === "user") {
        meta.innerHTML = `<span>${item.time || ""}</span><strong>あなた</strong>`;
      } else {
        meta.innerHTML = `<strong>AutoAI</strong><span class="model-chip">${item.model || state.status.model}</span><span>${item.time || ""}</span>`;
      }
      const bubble = document.createElement("div");
      bubble.className = "msg-bubble";
      bubble.textContent = item.text || "";
      wrap.appendChild(meta);
      wrap.appendChild(bubble);
      el.chatTimeline.appendChild(wrap);
    } else if (item.kind === "system") {
      const div = document.createElement("div");
      div.className = "msg msg-system";
      div.textContent = `${item.time ? `[${item.time}] ` : ""}${item.text || ""}`;
      el.chatTimeline.appendChild(div);
    } else if (item.kind === "error") {
      const div = document.createElement("div");
      div.className = "msg msg-error";
      div.textContent = item.text || "";
      el.chatTimeline.appendChild(div);
    } else if (item.kind === "auto_action") {
      const card = document.createElement("div");
      card.className = "msg card-auto";
      card.innerHTML = `
        <div class="card-header">
          <div class="card-title">⚡ 自律回答をターミナルへ送信 <span class="model-chip">検知 ${item.confidence ?? 0}%</span></div>
          <span class="msg-meta">${item.time || ""}</span>
        </div>
        <div class="card-detail">
          <div class="card-kv"><strong>問いかけ</strong><span class="mono"></span></div>
          <div class="card-kv"><strong>判断根拠</strong><span></span></div>
          <div class="card-kv"><strong>送信内容</strong><span class="sent-pill"></span></div>
        </div>
      `;
      const spans = card.querySelectorAll(".card-kv span");
      spans[0].textContent = item.prompt_text || "";
      spans[1].textContent = item.reasoning || "";
      spans[2].textContent = item.sent_text || "";
      el.chatTimeline.appendChild(card);
    } else if (item.kind === "boundary") {
      const card = document.createElement("div");
      card.className = "msg card-boundary";
      card.dataset.boundaryId = item.id || "";
      card.innerHTML = `
        <div class="card-header">
          <div class="card-title">⚠ 境界検知により自律停止: <span class="b-title"></span></div>
          <span class="msg-meta">${item.time || ""}</span>
        </div>
        <div class="boundary-box"></div>
        <div class="card-detail">
          <div class="card-kv"><strong>停止理由</strong><span></span></div>
          <div class="card-kv"><strong>推奨案</strong><span></span></div>
        </div>
        <div class="boundary-actions"></div>
      `;
      card.querySelector(".b-title").textContent = item.title || "";
      card.querySelector(".boundary-box").textContent = item.detected_cmd || "";
      const spans = card.querySelectorAll(".card-kv span");
      spans[0].textContent = item.risk_reason || "";
      spans[1].textContent = item.recommendation || "";

      const actions = card.querySelector(".boundary-actions");
      if (item.resolved) {
        actions.innerHTML = `<span class="boundary-resolved">✓ 決定済み: ${item.resolved}</span>`;
      } else {
        actions.innerHTML = `
          <button class="btn btn-amber btn-sm act-rec">推奨案で回答して再開</button>
          <button class="btn btn-outline btn-sm act-allow">このまま許可 (y)</button>
          <button class="btn btn-danger-soft btn-sm act-abort">Ctrl+C で中止</button>
        `;
        actions.querySelector(".act-rec").addEventListener("click", () => postJson("/api/boundary/resolve", { action: "recommend" }));
        actions.querySelector(".act-allow").addEventListener("click", () => postJson("/api/boundary/resolve", { action: "allow" }));
        actions.querySelector(".act-abort").addEventListener("click", () => postJson("/api/boundary/resolve", { action: "abort" }));
      }
      el.chatTimeline.appendChild(card);
    }

    el.chatTimeline.scrollTop = el.chatTimeline.scrollHeight;
  }

  function markBoundaryResolved(id, label) {
    const card = el.chatTimeline.querySelector(`[data-boundary-id="${id}"]`);
    if (!card) return;
    const actions = card.querySelector(".boundary-actions");
    if (actions) {
      actions.innerHTML = "";
      const span = document.createElement("span");
      span.className = "boundary-resolved";
      span.textContent = `✓ 決定済み: ${label}`;
      actions.appendChild(span);
    }
  }

  function applyFullState(full) {
    if (full.status) state.status = full.status;
    if (full.mission) state.mission = full.mission;
    if (full.presets) renderPresets(full.presets);
    renderMission();
    renderStatus();
    if (Array.isArray(full.timeline)) {
      full.timeline.forEach(appendTimelineItem);
    }
  }

  // ========== Ollama Models ==========
  async function loadModels(customUrl) {
    const url = customUrl || state.status.ollama_url || "http://127.0.0.1:11434";
    el.ollamaDot.className = "conn-dot conn-unknown";
    el.ollamaStatusNote.textContent = "Ollama へ接続確認中...";
    try {
      const res = await fetch(`/api/models?ollama_url=${encodeURIComponent(url)}`);
      const data = await res.json();
      const models = data.models || [DEFAULT_MODEL];
      [el.modelSelect, el.settingsModelSelect].forEach((sel) => {
        sel.innerHTML = "";
        models.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m;
          opt.textContent = m === DEFAULT_MODEL ? `${m} ★` : m;
          sel.appendChild(opt);
        });
        sel.value = models.includes(state.status.model) ? state.status.model : DEFAULT_MODEL;
      });
      if (data.ollama_connected) {
        el.ollamaDot.className = "conn-dot conn-ok";
        el.ollamaStatusNote.textContent = `Ollama 接続済み (${models.length} モデル検出 / 既定: ${DEFAULT_MODEL})`;
      } else {
        el.ollamaDot.className = "conn-dot conn-mock";
        el.ollamaStatusNote.textContent = data.note || "Ollama 未接続";
      }
    } catch {
      el.ollamaDot.className = "conn-dot conn-mock";
      el.ollamaStatusNote.textContent = "モデル取得に失敗しました";
    }
  }

  // ========== User Actions ==========
  async function startTerminalWith(presetOverride) {
    const preset = presetOverride || el.agentPreset.value || "pwsh";
    if (presetOverride) {
      el.agentPreset.value = presetOverride;
      el.customCmdInput.classList.toggle("hidden", presetOverride !== "custom");
    }
    const cwd = el.cwdInput.value.trim() || ".";
    const command = preset === "custom" ? el.customCmdInput.value.trim() : null;
    const res = await postJson("/api/terminal/start", { agent_preset: preset, cwd, command });
    if (!res.ok) {
      toast(res.message || "ターミナル起動に失敗しました", "error");
      return;
    }
    toast(res.message || "ターミナルを起動しました");
    setTimeout(() => {
      sendTerminalResize();
      term.focus();
    }, 100);
  }

  async function sendChat() {
    if (state.sendingChat) return;
    const text = el.chatInput.value.trim();
    if (!text) return;
    el.chatInput.value = "";
    state.sendingChat = true;
    el.sendChatBtn.disabled = true;
    try {
      const res = await postJson("/api/chat", { message: text });
      if (!res.ok && res.error) {
        toast(`チャットエラー: ${res.error}`, "error");
      }
    } catch (err) {
      toast(`通信エラー: ${err}`, "error");
    } finally {
      state.sendingChat = false;
      el.sendChatBtn.disabled = false;
    }
  }

  // Wire buttons
  el.sendChatBtn.addEventListener("click", sendChat);
  el.chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      el.chatInput.value = chip.dataset.prompt;
      sendChat();
    });
  });

  el.agentPreset.addEventListener("change", () => {
    el.customCmdInput.classList.toggle("hidden", el.agentPreset.value !== "custom");
  });

  el.startTermBtn.addEventListener("click", () => startTerminalWith(null));
  $("quickLaunchClaude").addEventListener("click", () => startTerminalWith("claude"));
  $("quickLaunchGemini").addEventListener("click", () => startTerminalWith("gemini"));
  $("quickLaunchAgy").addEventListener("click", () => startTerminalWith("agy"));
  $("quickLaunchPwsh").addEventListener("click", () => startTerminalWith("pwsh"));

  el.stopTermBtn.addEventListener("click", async () => {
    await postJson("/api/terminal/stop");
    toast("ターミナルを停止しました", "warn");
  });

  el.ctrlCBtn.addEventListener("click", async () => {
    await postJson("/api/terminal/interrupt");
    toast("Ctrl+C を送信しました", "warn");
  });

  el.evalNowBtn.addEventListener("click", async () => {
    toast("現在のターミナル画面を AutoAI に解析させています...");
    const res = await postJson("/api/monitor/evaluate");
    if (res.decision) {
      toast(`判定結果: ${res.decision} — ${res.reason || ""}`);
    }
  });

  el.resumeAutoBtn.addEventListener("click", async () => {
    await postJson("/api/monitor/resume");
    toast("自律監視を再開しました");
  });

  document.querySelectorAll(".seg-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await postJson("/api/monitor/mode", { mode: btn.dataset.mode });
      toast(`モードを「${btn.textContent}」に切り替えました`);
    });
  });

  el.modelSelect.addEventListener("change", async () => {
    const model = el.modelSelect.value;
    el.settingsModelSelect.value = model;
    await postJson("/api/settings", {
      ollama_url: state.status.ollama_url,
      model,
      idle_threshold_sec: state.status.idle_threshold_sec,
    });
    toast(`使用モデルを ${model} に変更しました`);
  });

  // Mission collapse & edit
  el.collapseMissionBtn.addEventListener("click", () => {
    const hidden = el.missionBody.classList.toggle("hidden");
    el.collapseMissionBtn.textContent = hidden ? "展開" : "折り畳む";
    el.collapseMissionBtn.setAttribute("aria-expanded", String(!hidden));
  });

  $("editMissionBtn").addEventListener("click", () => {
    $("editGoalInput").value = state.mission.goal || "";
    $("editPolicyInput").value = state.mission.policy || "";
    $("editBoundariesInput").value = (state.mission.boundaries || []).join(", ");
    $("missionModal").classList.remove("hidden");
  });

  $("saveMissionBtn").addEventListener("click", async () => {
    const goal = $("editGoalInput").value.trim();
    const policy = $("editPolicyInput").value.trim();
    const boundaries = $("editBoundariesInput").value.split(",").map((s) => s.trim()).filter(Boolean);
    await postJson("/api/mission", { goal, policy, boundaries });
    $("missionModal").classList.add("hidden");
    toast("ミッションを更新しました");
  });

  // Settings modal
  $("settingsBtn").addEventListener("click", () => {
    $("ollamaUrlInput").value = state.status.ollama_url || "http://127.0.0.1:11434";
    $("idleThresholdRange").value = String(state.status.idle_threshold_sec || 2.5);
    $("idleThresholdVal").textContent = `${Number(state.status.idle_threshold_sec || 2.5).toFixed(1)}秒`;
    $("settingsModal").classList.remove("hidden");
  });

  $("idleThresholdRange").addEventListener("input", (e) => {
    $("idleThresholdVal").textContent = `${Number(e.target.value).toFixed(1)}秒`;
  });

  $("refreshModelsBtn").addEventListener("click", () => {
    loadModels($("ollamaUrlInput").value.trim());
  });

  $("saveSettingsBtn").addEventListener("click", async () => {
    const ollama_url = $("ollamaUrlInput").value.trim();
    const model = el.settingsModelSelect.value || DEFAULT_MODEL;
    const idle_threshold_sec = Number($("idleThresholdRange").value);
    await postJson("/api/settings", { ollama_url, model, idle_threshold_sec });
    el.modelSelect.value = model;
    $("settingsModal").classList.add("hidden");
    toast(`設定を適用しました (${model})`);
  });

  document.querySelectorAll(".modal-close, [data-close]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.close;
      if (target) $(target).classList.add("hidden");
    });
  });

  // Draggable Splitter + ResizeObserver for xterm.js fit
  const splitter = $("splitter");
  const paneChat = $("paneChat");
  const workspace = $("workspace");
  let dragging = false;

  splitter.addEventListener("mousedown", () => {
    dragging = true;
    splitter.classList.add("dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });

  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const rect = workspace.getBoundingClientRect();
    const pct = ((e.clientX - rect.left) / rect.width) * 100;
    const clamped = Math.min(62, Math.max(24, pct));
    paneChat.style.width = `${clamped}%`;
    sendTerminalResize();
  });

  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove("dragging");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    sendTerminalResize();
  });

  window.addEventListener("resize", () => sendTerminalResize());
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => sendTerminalResize()).observe($("xtermStage"));
  }

  // Boot
  fetch("/api/state")
    .then((r) => r.json())
    .then((full) => {
      applyFullState(full);
      loadModels(full.status?.ollama_url);
      connectTerminalWs();
      connectEventsWs();
      setTimeout(sendTerminalResize, 120);
    })
    .catch((err) => toast(`初期化エラー: ${err}`, "error"));
})();
