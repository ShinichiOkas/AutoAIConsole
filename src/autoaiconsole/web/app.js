(() => {
  const DEFAULT_MODEL = "gemma4:31b-cloud";

  const state = {
    mode: "autonomous",          // autonomous | paused | manual
    engineState: "watching",     // watching | thinking | boundary | user | paused | manual
    backendMode: "demo",         // demo | live (live calls real backend -> returns 未実装)
    model: DEFAULT_MODEL,
    ollamaUrl: "http://127.0.0.1:11434",
    idleThresholdSec: 1.8,
    autoReplyCount: 2,
    lastActivityAt: Date.now(),
    silenceSec: 0.4,
    confidence: 18,
    lastTrigger: "? Run pytest for tests/test_auth.py? (Y/n)",
    mission: {
      goal: "ログイン API にメール形式・パスワード強度バリデーションを追加し、pytest を全件通過させる",
      policy: "正常系優先・確認プロンプト (Y/n) や軽微なテスト修正は AutoAI が自律回答して進める",
      boundaries: ["force push", "ファイル大量削除", "本番DB変更", "仕様の大幅分岐"],
    },
    lineSeq: 0,
    timers: [],
  };

  // DOM helpers
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
    backendModeBtn: $("backendModeBtn"),
    chatTimeline: $("chatTimeline"),
    chatInput: $("chatInput"),
    sendChatBtn: $("sendChatBtn"),
    terminalViewport: $("terminalViewport"),
    terminalLines: $("terminalLines"),
    directTermInput: $("directTermInput"),
    sendDirectBtn: $("sendDirectBtn"),
    controlBadge: $("controlBadge"),
    resumeAutoBtn: $("resumeAutoBtn"),
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

  function nowTime() {
    const d = new Date();
    return d.toTimeString().slice(0, 8);
  }

  function clearScheduledTimers() {
    state.timers.forEach((t) => clearTimeout(t));
    state.timers = [];
  }

  function schedule(ms, fn) {
    const id = setTimeout(fn, ms);
    state.timers.push(id);
    return id;
  }

  function toast(message, kind = "info") {
    const div = document.createElement("div");
    div.className = `toast ${kind === "warn" ? "toast-warn" : kind === "error" ? "toast-error" : ""}`;
    div.textContent = message;
    el.toastContainer.appendChild(div);
    setTimeout(() => div.remove(), 3600);
  }

  function touchActivity() {
    state.lastActivityAt = Date.now();
    state.silenceSec = 0;
  }

  // ========== Status & Live Feedback Rendering ==========
  function renderStatus() {
    const pill = el.statusPill;
    pill.className = "status-pill";
    const map = {
      watching: ["state-watching", "AUTONOMOUS 監視中", "出力ストリーム監視中", "metric-emerald"],
      thinking: ["state-thinking", `THINKING 回答生成中 (${state.model})`, "Ollama で回答を判断中...", "metric-cyan"],
      boundary: ["state-boundary", "BOUNDARY 境界相談中", "不可逆境界を検知・ユーザー判断待ち", "metric-amber"],
      user: ["state-user", "USER_INPUT ユーザー操作中", "ユーザー直接入力を優先（AutoAI待機）", "metric-violet"],
      paused: ["state-paused", "PAUSED 一時停止", "監視一時停止中", ""],
      manual: ["state-manual", "MANUAL 手動モード", "手動操作モード（自動回答OFF）", ""],
    };
    const [cls, label, monText, monCls] = map[state.engineState] || map.watching;
    pill.classList.add(cls);
    el.statusLabel.textContent = label;
    el.autoCountText.textContent = `自動応答: ${state.autoReplyCount}回`;

    el.monStateText.textContent = monText;
    el.monStateText.className = `metric-value ${monCls}`;

    // Control badge in terminal toolbar
    if (state.engineState === "user") {
      el.controlBadge.className = "control-badge owner-user";
      el.controlBadge.textContent = "制御: ユーザー操作中（AutoAI待機）";
      el.resumeAutoBtn.classList.remove("hidden");
    } else if (state.engineState === "boundary") {
      el.controlBadge.className = "control-badge owner-boundary";
      el.controlBadge.textContent = "制御: 境界停止中";
      el.resumeAutoBtn.classList.add("hidden");
    } else {
      el.controlBadge.className = "control-badge owner-auto";
      el.controlBadge.textContent = state.mode === "autonomous" ? "制御: AutoAI + 直接入力可" : "制御: ユーザー手動";
      el.resumeAutoBtn.classList.add("hidden");
    }

    // Segmented mode buttons
    document.querySelectorAll(".seg-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.mode === state.mode);
    });
  }

  function renderMonitorMetrics() {
    const elapsedSec = Math.floor((Date.now() - state.lastActivityAt) / 1000);
    el.heartbeatText.textContent = `更新: ${elapsedSec}秒前`;
    el.monSilenceText.textContent = `${state.silenceSec.toFixed(1)}s / ${state.idleThresholdSec.toFixed(1)}s`;
    el.monConfBar.style.width = `${Math.min(100, Math.max(0, state.confidence))}%`;
    el.monConfPct.textContent = `${Math.round(state.confidence)}%`;
    el.monTriggerText.textContent = state.lastTrigger;
  }

  // 1-second live heartbeat loop (Live Feedback Rule: never >10s stale)
  setInterval(() => {
    if (state.engineState === "watching") {
      state.silenceSec = Math.min(state.idleThresholdSec, state.silenceSec + 0.2);
    }
    renderMonitorMetrics();
  }, 1000);

  // ========== Terminal Rendering ==========
  function appendTerminalLine(text, kind = "agent", badge = null) {
    state.lineSeq += 1;
    const lineId = `term-line-${state.lineSeq}`;
    const row = document.createElement("div");
    row.id = lineId;
    row.className = `term-line term-${kind}`;

    const ts = document.createElement("span");
    ts.className = "term-ts";
    ts.textContent = nowTime();

    const content = document.createElement("span");
    content.className = "term-text";

    if (badge === "autoai") {
      const b = document.createElement("span");
      b.className = "line-badge line-badge-auto";
      b.textContent = "AutoAI →";
      content.appendChild(b);
    } else if (badge === "user") {
      const b = document.createElement("span");
      b.className = "line-badge line-badge-user";
      b.textContent = "USER →";
      content.appendChild(b);
    }
    content.appendChild(document.createTextNode(text));

    row.appendChild(ts);
    row.appendChild(content);
    el.terminalLines.appendChild(row);
    el.terminalViewport.scrollTop = el.terminalViewport.scrollHeight;
    touchActivity();
    return lineId;
  }

  function focusTerminalLine(lineId) {
    const target = document.getElementById(lineId);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.remove("highlight-flash");
    void target.offsetWidth;
    target.classList.add("highlight-flash");
    setTimeout(() => target.classList.remove("highlight-flash"), 1600);
  }

  // ========== Chat Rendering ==========
  function appendChatMessage(role, text, extraMeta = "") {
    const wrap = document.createElement("div");
    wrap.className = `msg msg-${role}`;

    const meta = document.createElement("div");
    meta.className = "msg-meta";
    if (role === "user") {
      meta.innerHTML = `<span>${nowTime()}</span><strong>あなた</strong>`;
    } else {
      meta.innerHTML = `<strong>AutoAI</strong><span class="model-chip">${state.model}</span><span>${nowTime()}</span>${extraMeta}`;
    }

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    bubble.textContent = text;

    wrap.appendChild(meta);
    wrap.appendChild(bubble);
    el.chatTimeline.appendChild(wrap);
    el.chatTimeline.scrollTop = el.chatTimeline.scrollHeight;
  }

  function appendAutoActionCard({ promptText, reasoning, sentText, confidence, termLineId }) {
    const card = document.createElement("div");
    card.className = "msg card-auto";
    card.innerHTML = `
      <div class="card-header">
        <div class="card-title">⚡ 自律回答をターミナルへ送信 <span class="model-chip">検知 ${confidence}%</span></div>
        <button class="btn btn-ghost btn-xs jump-btn">ターミナル箇所 ↗</button>
      </div>
      <div class="card-detail">
        <div class="card-kv"><strong>問いかけ</strong><span class="mono"></span></div>
        <div class="card-kv"><strong>判断根拠</strong><span></span></div>
        <div class="card-kv"><strong>送信内容</strong><span class="sent-pill"></span></div>
      </div>
    `;
    const spans = card.querySelectorAll(".card-kv span");
    spans[0].textContent = promptText;
    spans[1].textContent = reasoning;
    spans[2].textContent = sentText;
    card.querySelector(".jump-btn").addEventListener("click", () => focusTerminalLine(termLineId));
    el.chatTimeline.appendChild(card);
    el.chatTimeline.scrollTop = el.chatTimeline.scrollHeight;
  }

  function appendBoundaryCard({ title, detectedCmd, riskReason, recommendation, recAnswer, termLineId }) {
    const card = document.createElement("div");
    card.className = "msg card-boundary";
    card.innerHTML = `
      <div class="card-header">
        <div class="card-title">⚠ 境界検知により自律停止: ${title}</div>
        <button class="btn btn-ghost btn-xs jump-btn">該当行 ↗</button>
      </div>
      <div class="boundary-box"></div>
      <div class="card-detail">
        <div class="card-kv"><strong>停止理由</strong><span></span></div>
        <div class="card-kv"><strong>推奨案</strong><span></span></div>
      </div>
      <div class="boundary-actions">
        <button class="btn btn-amber btn-sm act-rec">推奨案で回答して再開</button>
        <button class="btn btn-outline btn-sm act-allow">このまま許可 (y)</button>
        <button class="btn btn-danger-soft btn-sm act-abort">Ctrl+C で中止</button>
      </div>
    `;
    card.querySelector(".boundary-box").textContent = detectedCmd;
    const spans = card.querySelectorAll(".card-kv span");
    spans[0].textContent = riskReason;
    spans[1].textContent = recommendation;
    card.querySelector(".jump-btn").addEventListener("click", () => focusTerminalLine(termLineId));

    const actions = card.querySelector(".boundary-actions");
    const resolveCard = (label) => {
      actions.innerHTML = `<span class="boundary-resolved">✓ 決定済み: ${label}</span>`;
    };

    card.querySelector(".act-rec").addEventListener("click", () => {
      resolveCard(`推奨案を送信 (${recAnswer})`);
      state.engineState = "watching";
      state.confidence = 14;
      state.autoReplyCount += 1;
      const lid = appendTerminalLine(recAnswer, "autoai", "autoai");
      appendTerminalLine("✓ Understood. Skipping force push and keeping commit on local branch.", "success");
      appendAutoActionCard({
        promptText: detectedCmd,
        reasoning: "ユーザー判断（推奨案）に従い force push を拒否し、ローカルコミット維持を指示しました。",
        sentText: recAnswer,
        confidence: 100,
        termLineId: lid,
      });
      renderStatus();
      renderMonitorMetrics();
      toast("推奨案を送信し、自律監視を再開しました");
    });

    card.querySelector(".act-allow").addEventListener("click", () => {
      resolveCard("明示許可 (y) を送信");
      state.engineState = "watching";
      state.confidence = 10;
      appendTerminalLine("y", "user", "user");
      appendTerminalLine("> Proceeding per explicit user approval...", "agent");
      renderStatus();
      renderMonitorMetrics();
      toast("明示許可を送信しました");
    });

    card.querySelector(".act-abort").addEventListener("click", () => {
      resolveCard("Ctrl+C で操作を中断");
      state.engineState = "paused";
      state.mode = "paused";
      appendTerminalLine("^C (Interrupted by user from boundary card)", "danger", "user");
      renderStatus();
      toast("操作を中断し、一時停止しました", "warn");
    });

    el.chatTimeline.appendChild(card);
    el.chatTimeline.scrollTop = el.chatTimeline.scrollHeight;
  }

  function appendUnimplementedCard(endpoint, message) {
    const card = document.createElement("div");
    card.className = "msg card-unimpl";
    card.innerHTML = `<strong>[実API応答 / UIファースト確認] ${endpoint}</strong><div></div>`;
    card.querySelector("div").textContent = message;
    el.chatTimeline.appendChild(card);
    el.chatTimeline.scrollTop = el.chatTimeline.scrollHeight;
  }

  // ========== Mission Render ==========
  function renderMission() {
    el.missionGoalView.textContent = state.mission.goal;
    el.missionPolicyView.textContent = state.mission.policy;
    el.missionBoundaryView.innerHTML = "";
    state.mission.boundaries.forEach((b) => {
      const span = document.createElement("span");
      span.className = "tag-boundary";
      span.textContent = b;
      el.missionBoundaryView.appendChild(span);
    });
  }

  // ========== Seed Initial Realistic State ==========
  function seedInitialState() {
    clearScheduledTimers();
    el.chatTimeline.innerHTML = "";
    el.terminalLines.innerHTML = "";
    state.lineSeq = 0;
    state.mode = "autonomous";
    state.engineState = "watching";
    state.autoReplyCount = 2;
    state.confidence = 22;
    state.lastTrigger = "? Run pytest for tests/test_auth.py? (Y/n)";

    renderMission();

    appendTerminalLine("AutoAIConsole PTY session started (preset: claude, cwd: S:\\work\\develop\\sample-api)", "system");
    appendTerminalLine("$ claude", "agent");
    appendTerminalLine("Claude Code v1.x — Connected to workspace sample-api", "system");
    appendTerminalLine("Add email format and password strength validation to login API and make pytest pass.", "autoai", "autoai");
    appendTerminalLine("> Inspecting src/auth.py and tests/test_auth.py...", "agent");
    const p1 = appendTerminalLine("? Allow edit to src/auth.py (+28 / -4 lines)? (Y/n)", "prompt");
    const a1 = appendTerminalLine("y", "autoai", "autoai");
    appendTerminalLine("✓ Updated src/auth.py", "success");
    const p2 = appendTerminalLine("? Run pytest for tests/test_auth.py? (Y/n)", "prompt");
    const a2 = appendTerminalLine("y", "autoai", "autoai");
    appendTerminalLine("> Running pytest -q tests/test_auth.py ...", "agent");
    appendTerminalLine("....                                                               [100%] 4 passed in 0.42s", "success");

    appendChatMessage("user", "ログイン API にメール形式とパスワード強度のバリデーションを追加して、pytest を通しておいて。確認は任せるよ。");
    appendChatMessage(
      "ai",
      "了解しました。ミッションを登録し、右ターミナルの Claude Code に実装を指示しました。\n確認プロンプトや軽微な修正は自律で回答し、破壊的操作や仕様分岐の境界だけ相談します。"
    );
    appendAutoActionCard({
      promptText: "? Allow edit to src/auth.py (+28 / -4 lines)? (Y/n)",
      reasoning: "ミッション対象のログイン API ファイル編集であり、停止境界に該当しないため承認を自動送信。",
      sentText: "y",
      confidence: 97,
      termLineId: a1,
    });
    appendAutoActionCard({
      promptText: "? Run pytest for tests/test_auth.py? (Y/n)",
      reasoning: "ミッション達成条件（pytest 全件通過）の検証コマンドのため自動実行。",
      sentText: "y",
      confidence: 98,
      termLineId: a2,
    });
    appendChatMessage(
      "ai",
      "現在の状況: src/auth.py のバリデーション実装と 4 件のテスト通過まで完了しています。追加の指示があればそのまま話しかけてください。"
    );

    renderStatus();
    renderMonitorMetrics();
    void p1;
    void p2;
  }

  // ========== UX Scenarios ==========
  function runScenarioA() {
    clearScheduledTimers();
    state.mode = "autonomous";
    state.engineState = "watching";
    renderStatus();
    toast("シナリオA: 自律完走ループを開始します");

    appendChatMessage("user", "ついでに日本語エラーメッセージ対応と境界値テストも追加して、全テスト通して。");
    appendChatMessage("ai", "了解。Claude Code へ追加要件を投入し、問いかけを監視しながら完走させます。");
    appendTerminalLine("Add Japanese validation error messages and edge-case tests, then run full pytest.", "autoai", "autoai");

    schedule(900, () => {
      appendTerminalLine("> Analyzing error responses in src/auth.py...", "agent");
      state.confidence = 30;
      renderMonitorMetrics();
    });

    schedule(1900, () => {
      const q = "? Which format do you prefer for error response? (1: {detail: str} existing / 2: {errors: list}) [1/2]";
      appendTerminalLine(q, "prompt");
      state.engineState = "thinking";
      state.confidence = 95;
      state.lastTrigger = q;
      renderStatus();
      renderMonitorMetrics();
    });

    schedule(3400, () => {
      const lid = appendTerminalLine("1 — Keep existing {detail: str} schema for backward compatibility.", "autoai", "autoai");
      state.autoReplyCount += 1;
      state.engineState = "watching";
      state.confidence = 15;
      renderStatus();
      renderMonitorMetrics();
      appendAutoActionCard({
        promptText: "? Which format do you prefer for error response? [1/2]",
        reasoning: "既存クライアントとの互換性を保ちつつ正常系を最短で通すため、既存形式 (1) を自律選択。",
        sentText: "1 — Keep existing {detail: str} schema...",
        confidence: 95,
        termLineId: lid,
      });
    });

    schedule(4700, () => {
      appendTerminalLine("✓ Updated src/auth.py and tests/test_auth.py (+6 edge-case tests)", "success");
      const q2 = "? Run full test suite `pytest`? (Y/n)";
      appendTerminalLine(q2, "prompt");
      state.engineState = "thinking";
      state.confidence = 98;
      state.lastTrigger = q2;
      renderStatus();
      renderMonitorMetrics();
    });

    schedule(5800, () => {
      const lid2 = appendTerminalLine("y", "autoai", "autoai");
      state.autoReplyCount += 1;
      state.engineState = "watching";
      state.confidence = 12;
      renderStatus();
      renderMonitorMetrics();
      appendAutoActionCard({
        promptText: "? Run full test suite `pytest`? (Y/n)",
        reasoning: "最終検証のため全テスト実行を自動許可。",
        sentText: "y",
        confidence: 98,
        termLineId: lid2,
      });
    });

    schedule(7000, () => {
      appendTerminalLine("..........                                                         [100%] 10 passed in 0.84s", "success");
      appendChatMessage("ai", "完了しました！日本語エラーメッセージと境界値テスト 6 件を追加し、全 10 件の pytest が通過しています。");
      toast("シナリオA: 自律完走しました");
    });
  }

  function runScenarioB() {
    clearScheduledTimers();
    state.mode = "autonomous";
    state.engineState = "watching";
    renderStatus();
    toast("シナリオB: 境界検知による自律停止を再現します", "warn");

    appendTerminalLine("> Preparing to sync changes with remote repository...", "agent");
    schedule(700, () => {
      const cmd = "? Run `git push --force origin main` to overwrite diverged remote history? (y/N)";
      const lid = appendTerminalLine(cmd, "danger");
      state.engineState = "thinking";
      state.confidence = 99;
      state.lastTrigger = cmd;
      renderStatus();
      renderMonitorMetrics();

      schedule(1200, () => {
        state.engineState = "boundary";
        renderStatus();
        appendBoundaryCard({
          title: "不可逆なリモート履歴上書き (force push)",
          detectedCmd: cmd,
          riskReason: "停止境界ルール「force push」に該当。main ブランチのリモート履歴を不可逆的に書き換える操作のため自動応答を停止しました。",
          recommendation: "force push は拒否 (n) し、ローカルコミットに留めるか別ブランチへ push するよう Coding Agent へ指示する。",
          recAnswer: "n — Do NOT force push to main. Keep commits local only.",
          termLineId: lid,
        });
      });
    });
  }

  function runScenarioC() {
    clearScheduledTimers();
    state.mode = "autonomous";
    state.engineState = "user";
    state.confidence = 0;
    state.lastTrigger = "(ユーザー直接介入のため検知を一時譲歩)";
    renderStatus();
    renderMonitorMetrics();

    appendTerminalLine("^C", "danger", "user");
    appendTerminalLine("git diff --stat", "user", "user");
    appendTerminalLine(" src/auth.py       | 32 ++++++++++++++++++++++++----", "agent");
    appendTerminalLine(" tests/test_auth.py | 48 +++++++++++++++++++++++++++++++++++++++++", "agent");
    appendChatMessage(
      "ai",
      "ターミナルへの直接入力を検知したため、操作権をあなたへ譲り待機しています。\n確認が終わったら右上の「自律監視を再開」を押すか、ここで「続けて」と指示してください。"
    );
    toast("ユーザー直接介入を検知: AutoAI は操作権を譲って待機中");
  }

  // ========== Backend API Calls (UI-First Real Endpoint Check) ==========
  async function callBackend(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await res.json();
  }

  async function loadModels(customUrl) {
    const url = customUrl || state.ollamaUrl;
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
        sel.value = models.includes(state.model) ? state.model : DEFAULT_MODEL;
      });
      state.model = el.modelSelect.value;

      if (data.ollama_connected) {
        el.ollamaDot.className = "conn-dot conn-ok";
        el.ollamaStatusNote.textContent = `Ollama 接続済み (${models.length} モデル検出 / 既定: ${DEFAULT_MODEL})`;
      } else {
        el.ollamaDot.className = "conn-dot conn-mock";
        el.ollamaStatusNote.textContent = data.note || "Ollama 未接続（モック候補を表示）";
      }
    } catch {
      el.ollamaDot.className = "conn-dot conn-mock";
      el.ollamaStatusNote.textContent = "バックエンド未起動のため既定モデル候補を表示中";
    }
  }

  // ========== User Actions ==========
  async function handleSendChat() {
    const text = el.chatInput.value.trim();
    if (!text) return;
    el.chatInput.value = "";
    appendChatMessage("user", text);

    if (state.backendMode === "live") {
      try {
        const resp = await callBackend("/api/chat", { message: text, model: state.model });
        appendUnimplementedCard("POST /api/chat", resp.message);
      } catch (err) {
        appendUnimplementedCard("POST /api/chat", `通信エラー: ${err}`);
      }
      return;
    }

    // Demo UX response
    if (text.includes("続けて") || text.includes("再開")) {
      state.mode = "autonomous";
      state.engineState = "watching";
      renderStatus();
      appendChatMessage("ai", "自律監視を再開しました。ターミナルの出力を引き続き監視します。");
      return;
    }
    if (text.includes("要約")) {
      appendChatMessage(
        "ai",
        `【進捗要約】\n・使用モデル: ${state.model}\n・現在の状態: ${el.statusLabel.textContent}\n・これまでの自動応答: ${state.autoReplyCount} 回\n・直近の検知トリガー: ${state.lastTrigger}`
      );
      return;
    }

    state.engineState = "thinking";
    renderStatus();
    schedule(700, () => {
      const lid = appendTerminalLine(text, "autoai", "autoai");
      state.engineState = "watching";
      renderStatus();
      appendChatMessage(
        "ai",
        `了解しました。その指示を現在のミッション文脈に反映し、右の Coding Agent へ送信しました（モデル: ${state.model}）。問いかけが来たら自動で対応します。`
      );
      appendTerminalLine(`> Received instruction from AutoAI: "${text.slice(0, 60)}"`, "agent");
      void lid;
    });
  }

  async function handleDirectTerminalSend() {
    const text = el.directTermInput.value.trim();
    if (!text) return;
    el.directTermInput.value = "";

    if (state.backendMode === "live") {
      const resp = await callBackend("/api/terminal/input", { text, source: "user" });
      appendUnimplementedCard("POST /api/terminal/input", resp.message);
      return;
    }

    appendTerminalLine(text, "user", "user");
    if (state.mode === "autonomous" && state.engineState !== "user") {
      state.engineState = "user";
      renderStatus();
      toast("直接入力を検知したため AutoAI が操作権を譲りました");
    }
    schedule(350, () => {
      appendTerminalLine(`> Executed direct user input: ${text}`, "agent");
    });
  }

  // ========== Wire Events ==========
  el.sendChatBtn.addEventListener("click", handleSendChat);
  el.chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendChat();
    }
  });

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      el.chatInput.value = chip.dataset.prompt;
      handleSendChat();
    });
  });

  el.sendDirectBtn.addEventListener("click", handleDirectTerminalSend);
  el.directTermInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleDirectTerminalSend();
    }
  });

  el.terminalViewport.addEventListener("click", () => {
    el.directTermInput.focus();
  });

  // Mode segmented buttons
  document.querySelectorAll(".seg-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const mode = btn.dataset.mode;
      state.mode = mode;
      state.engineState = mode === "autonomous" ? "watching" : mode;
      renderStatus();
      if (state.backendMode === "live") {
        const resp = await callBackend("/api/monitor/mode", { mode });
        appendUnimplementedCard("POST /api/monitor/mode", resp.message);
      } else {
        toast(`動作モードを「${btn.textContent}」に切り替えました`);
      }
    });
  });

  el.resumeAutoBtn.addEventListener("click", () => {
    state.mode = "autonomous";
    state.engineState = "watching";
    renderStatus();
    toast("自律監視を再開しました");
  });

  // Backend mode toggle (UX Demo vs Live API Not-Implemented check)
  el.backendModeBtn.addEventListener("click", () => {
    state.backendMode = state.backendMode === "demo" ? "live" : "demo";
    el.backendModeBtn.textContent =
      state.backendMode === "demo" ? "モード: UXデモ" : "モード: 実API(未実装確認)";
    toast(
      state.backendMode === "demo"
        ? "UXデモモード: シナリオと対話モックで動作します"
        : "実APIモード: 操作するとバックエンドの「未実装」応答を確認できます",
      state.backendMode === "live" ? "warn" : "info"
    );
  });

  // Terminal toolbar actions
  $("startTermBtn").addEventListener("click", async () => {
    const preset = $("agentPreset").value;
    const cwd = $("cwdInput").value;
    if (state.backendMode === "live") {
      const resp = await callBackend("/api/terminal/start", { agent_preset: preset, cwd });
      appendUnimplementedCard("POST /api/terminal/start", resp.message);
      return;
    }
    appendTerminalLine(`--- Restarting PTY preset=${preset} cwd=${cwd} ---`, "system");
    appendTerminalLine(`$ ${preset}`, "agent");
    toast(`${preset} の起動をシミュレートしました`);
  });

  $("ctrlCBtn").addEventListener("click", async () => {
    if (state.backendMode === "live") {
      const resp = await callBackend("/api/terminal/interrupt", {});
      appendUnimplementedCard("POST /api/terminal/interrupt", resp.message);
      return;
    }
    appendTerminalLine("^C", "danger", "user");
    state.engineState = "user";
    renderStatus();
    toast("Ctrl+C を送信しました（AutoAI は一時待機）", "warn");
  });

  $("clearTermBtn").addEventListener("click", () => {
    el.terminalLines.innerHTML = "";
    appendTerminalLine("Terminal view cleared.", "system");
  });

  // Model selector sync
  el.modelSelect.addEventListener("change", () => {
    state.model = el.modelSelect.value;
    el.settingsModelSelect.value = state.model;
    renderStatus();
    toast(`使用モデルを ${state.model} に変更しました`);
  });

  // Scenarios
  $("btnScenarioA").addEventListener("click", runScenarioA);
  $("btnScenarioB").addEventListener("click", runScenarioB);
  $("btnScenarioC").addEventListener("click", runScenarioC);
  $("btnScenarioReset").addEventListener("click", () => {
    seedInitialState();
    toast("初期デモ状態にリセットしました");
  });
  $("toggleScenarioBtn").addEventListener("click", () => {
    $("scenarioBar").classList.add("hidden");
  });

  // Mission collapse & edit
  el.collapseMissionBtn.addEventListener("click", () => {
    const hidden = el.missionBody.classList.toggle("hidden");
    el.collapseMissionBtn.textContent = hidden ? "展開" : "折り畳む";
    el.collapseMissionBtn.setAttribute("aria-expanded", String(!hidden));
  });

  $("editMissionBtn").addEventListener("click", () => {
    $("editGoalInput").value = state.mission.goal;
    $("editPolicyInput").value = state.mission.policy;
    $("editBoundariesInput").value = state.mission.boundaries.join(", ");
    $("missionModal").classList.remove("hidden");
  });

  $("saveMissionBtn").addEventListener("click", async () => {
    state.mission.goal = $("editGoalInput").value.trim() || state.mission.goal;
    state.mission.policy = $("editPolicyInput").value.trim() || state.mission.policy;
    state.mission.boundaries = $("editBoundariesInput")
      .value.split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    renderMission();
    $("missionModal").classList.add("hidden");
    if (state.backendMode === "live") {
      const resp = await callBackend("/api/mission", state.mission);
      appendUnimplementedCard("POST /api/mission", resp.message);
    } else {
      toast("ミッション（自律判断基準）を更新しました");
    }
  });

  // Settings modal
  $("settingsBtn").addEventListener("click", () => {
    $("ollamaUrlInput").value = state.ollamaUrl;
    $("idleThresholdRange").value = String(state.idleThresholdSec);
    $("idleThresholdVal").textContent = `${state.idleThresholdSec.toFixed(1)}秒`;
    $("settingsModal").classList.remove("hidden");
  });

  $("idleThresholdRange").addEventListener("input", (e) => {
    $("idleThresholdVal").textContent = `${Number(e.target.value).toFixed(1)}秒`;
  });

  $("refreshModelsBtn").addEventListener("click", () => {
    loadModels($("ollamaUrlInput").value.trim());
  });

  $("saveSettingsBtn").addEventListener("click", () => {
    state.ollamaUrl = $("ollamaUrlInput").value.trim() || state.ollamaUrl;
    state.model = el.settingsModelSelect.value || DEFAULT_MODEL;
    el.modelSelect.value = state.model;
    state.idleThresholdSec = Number($("idleThresholdRange").value);
    $("settingsModal").classList.add("hidden");
    renderStatus();
    renderMonitorMetrics();
    toast(`設定を適用しました (${state.model})`);
  });

  document.querySelectorAll(".modal-close, [data-close]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.close;
      if (target) $(target).classList.add("hidden");
    });
  });

  // Session switcher
  $("sessionSelect").addEventListener("change", (e) => {
    if (e.target.value === "s2") {
      state.mission.goal = "既存モジュールの重複コードを整理し、回帰テストを全通過させる";
      seedInitialState();
      toast("セッション #2 に切り替えました");
    } else {
      state.mission.goal = "ログイン API にメール形式・パスワード強度バリデーションを追加し、pytest を全件通過させる";
      seedInitialState();
      toast("セッション #1 に切り替えました");
    }
  });

  $("newSessionBtn").addEventListener("click", () => {
    const sel = $("sessionSelect");
    const n = sel.options.length + 1;
    const opt = document.createElement("option");
    opt.value = `s${n}`;
    opt.textContent = `#${n} 新規セッション`;
    sel.appendChild(opt);
    sel.value = opt.value;
    el.chatTimeline.innerHTML = "";
    el.terminalLines.innerHTML = "";
    appendChatMessage("ai", "新規セッションを開始しました。達成したい目的を教えてください。");
    appendTerminalLine("Ready. Waiting for mission or agent startup...", "system");
    toast(`新規セッション #${n} を作成しました`);
  });

  // Draggable Splitter
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
    const clamped = Math.min(68, Math.max(26, pct));
    paneChat.style.width = `${clamped}%`;
  });

  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove("dragging");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  });

  // Boot
  seedInitialState();
  loadModels();
})();
