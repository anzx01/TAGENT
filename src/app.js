    const API = "http://127.0.0.1:18899";
    const $ = (id) => document.getElementById(id);
    const btnRecord = $("btnRecord"), btnStop = $("btnStop"), btnPlay = $("btnPlay");
    const btnPause = $("btnPause"), btnReplayStop = $("btnReplayStop"), btnList = $("btnList");
    const btnClose = $("btnClose");
    const statusEl = $("status"), panel = $("panel"), taskList = $("taskList");
    const stepList = $("stepList"), mask = $("modalMask"), modalBox = $("modalBox");
    const scoutView = $("scoutView"), taskView = $("taskView"), historyView = $("historyView"), settingsView = $("settingsView");
    let workspaceTab = "scout";
    let currentTask = null;
    let pollTimer = null;   // 回放状态轮询
    let pollGeneration = 0; // 丢弃旧请求返回，避免停止/继续后的竞态
    let playback = {
      playing: false,
      paused: false,
      currentStep: 0,
      total: 0,
      stepDesc: "",
      reason: "",
      recordingId: "",
    };
    let takeoverOpen = false;
    let takeoverBusy = false;

    function escapeHtml(value) {
      return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
      }[ch]));
    }

    function setStatus(text, cls) {
      statusEl.textContent = text;
      statusEl.className = "status" + (cls ? " " + cls : "");
    }
    async function apiJson(path, options = {}, timeoutMs = 8000) {
      // All control requests share a timeout.  A WebView fetch can otherwise
      // remain pending forever when the sidecar is being restarted.
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const res = await fetch(API + path, { ...options, signal: controller.signal });
        let data = null;
        try { data = await res.json(); } catch (_) { data = {}; }
        if (!res.ok) {
          const err = new Error((data && data.msg) || `HTTP ${res.status}`);
          err.data = data;
          err.status = res.status;
          throw err;
        }
        return data;
      } finally {
        clearTimeout(timer);
      }
    }
    function apiErrorMessage(err, fallback = "服务未连接") {
      if (err && err.data && err.data.msg) return err.data.msg;
      if (err && err.name === "AbortError") return "后端响应超时，请稍后重试";
      return fallback;
    }
    function el(tag, cls, text) {
      const node = document.createElement(tag);
      if (cls) node.className = cls;
      if (text !== undefined) node.textContent = text;
      return node;
    }
    function setRecording(on) {
      btnRecord.disabled = on;
      btnStop.disabled = !on;
      btnPlay.disabled = on || playback.playing;
      btnPause.disabled = on || !playback.playing || playback.paused;
      btnReplayStop.disabled = on || !playback.playing;
    }
    function setReplaying(on) {
      playback.playing = !!on;
      // Recording and replaying are mutually exclusive.  The old UI left
      // the red Record button enabled while a takeover dialog was open,
      // allowing a second session to corrupt the paused player.
      if (on) btnRecord.disabled = true;
      else if (btnStop.disabled) btnRecord.disabled = false;
      btnPlay.disabled = on || btnRecord.disabled;
      btnReplayStop.disabled = !on;
      btnPause.disabled = !on || playback.paused;
    }
    function setReplayPaused(on) {
      playback.paused = !!on;
      if (on) playback.playing = true;
      if (on) {
        // 暂停期间仍保留“停回放”，避免接管弹窗被关闭后无法脱离。
        btnRecord.disabled = true;
        btnPlay.disabled = true;
        btnPause.disabled = true;
        btnReplayStop.disabled = false;
      } else {
        if (btnStop.disabled) btnRecord.disabled = false;
        btnPlay.disabled = playback.playing || btnRecord.disabled;
        btnPause.disabled = !playback.playing;
        btnReplayStop.disabled = !playback.playing;
      }
    }
    function showModal(html) {
      modalBox.innerHTML = html;
      mask.dataset.locked = "";
      mask.classList.add("show");
    }
    function hideModal() {
      mask.classList.remove("show");
      mask.dataset.locked = "";
      takeoverOpen = false;
      takeoverBusy = false;
    }
    mask.addEventListener("click", (e) => {
      if (e.target === mask && mask.dataset.locked !== "1") hideModal();
    });
    document.querySelectorAll(".workspace-tab").forEach((button) => {
      button.addEventListener("click", () => {
        workspaceTab = button.dataset.tab || "scout";
        document.querySelectorAll(".workspace-tab").forEach((b) => b.classList.toggle("active", b === button));
        scoutView.style.display = workspaceTab === "scout" ? "block" : "none";
        taskView.style.display = workspaceTab === "tasks" ? "block" : "none";
        historyView.style.display = workspaceTab === "history" ? "block" : "none";
        settingsView.style.display = workspaceTab === "settings" ? "block" : "none";
        if (workspaceTab === "scout") { refreshDiscovery(); refreshSuggestions(); }
        if (workspaceTab === "tasks") refreshTasks();
        if (workspaceTab === "history") refreshHistory();
        if (workspaceTab === "settings") refreshStartupSetting();
      });
    });

    async function refreshSuggestions() {
      try {
        const s = await apiJson("/suggestions");
        $("suggestionBlock").innerHTML = (s.items || []).map(it =>
          `<div class="history-row"><span>建议 · ${escapeHtml(it.rec_id)}：${escapeHtml(it.message)}</span></div>`).join("")
          || "<div class='history-empty'>暂无失败模式建议。</div>";
      } catch (_) { $("suggestionBlock").innerHTML = ""; }
    }
    async function refreshStartupSetting() {
      const toggle = $("startupEnabled");
      try {
        const setting = await apiJson("/settings/startup");
        toggle.checked = setting.enabled === true;
        toggle.disabled = !setting.supported;
      } catch (err) { setStatus(apiErrorMessage(err, "读取开机启动设置失败"), "error"); }
      try {
        const n = await apiJson("/notify");
        $("notifyUrl").value = (n.config && n.config.url) || "";
      } catch (_) {}
      try {
        const c = await apiJson("/connectors");
        const row = $("connectorRow");
        row.innerHTML = "";
        for (const name of c.connectors) {
          const sel = el("select", "", "");
          for (const lv of ["readonly", "act"]) sel.append(new Option(lv, lv));
          sel.value = c.permissions[name];
          sel.onchange = async () => {
            const r = await apiJson("/connectors/" + name, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ level: sel.value }) });
            setStatus(r.ok ? "连接器 " + name + " → " + sel.value : (r.msg || "失败"), r.ok ? "" : "error");
          };
          row.append(el("span", "workspace-copy", name + ":"), sel);
        }
        const guard = await apiJson("/network-guard");
        $("whitelistToggle").checked = !!(guard.config && guard.config.enabled);
      } catch (_) {}
      try { await refreshMemory(); } catch (_) {}
      try {
        const a = await apiJson("/audit/summary");
        const s = a.summary || {};
        $("auditCard").textContent = "审计：共 " + s.total_runs + " 次运行（近期成功率 " + Math.round((s.recent_success_rate || 0) * 100) + "%）· 定时 " + s.scheduled_jobs + " · 盯守 " + s.watchers + " · 记忆 " + s.memory_rules + " 条 · 导出 Skill " + s.exported_skills + " 份" + (s.notify_enabled ? " · 推送已配置" : "") + (s.network_whitelist ? " · 白名单开启" : "");
      } catch (_) { $("auditCard").textContent = "审计摘要不可用"; }
    }
    async function refreshMemory() {
      const m = await apiJson("/memory");
      const list = $("memoryList");
      list.innerHTML = (m.rules || []).map(r => "<div style='display:flex;gap:6px;align-items:center'><span class='workspace-copy'>" + escapeHtml(r.text) + "</span><button class='mini del' data-mid='" + r.id + "'>遗忘</button></div>").join("") || "<span class='workspace-copy'>暂无记忆</span>";
      for (const b of list.querySelectorAll("[data-mid]")) b.onclick = async () => { await apiJson("/memory/" + b.dataset.mid, { method: "DELETE" }); refreshMemory(); };
    }
    $("notifySave").onclick = async () => {
      try {
        const r = await apiJson("/notify", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: $("notifyUrl").value.trim(), enabled: true }) });
        setStatus(r.ok ? (r.test_sent ? "推送已配置并测试成功" : "推送已配置（测试未发出，检查 url）") : (r.msg || "失败"), r.ok ? "" : "error");
        if (r.ok) refreshStartupSetting();
      } catch (err) { setStatus(apiErrorMessage(err, "推送配置失败"), "error"); }
    };
    $("whitelistToggle").onchange = async () => {
      const enabled = $("whitelistToggle").checked;
      const allow = [];
      try { const n = await apiJson("/notify"); if (n.config && n.config.url) allow.push(new URL(n.config.url).hostname); } catch (_) {}
      const r = await apiJson("/network-guard", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled, allow_hosts: allow }) });
      setStatus(r.ok ? (enabled ? "白名单已开启（允许：" + (allow.join(",") || "仅本机") + "）" : "白名单已关闭") : (r.msg || "失败"), r.ok ? "" : "error");
    };
    $("memoryAdd").onclick = async () => {
      const text = $("memoryInput").value.trim();
      if (!text) return;
      const r = await apiJson("/memory", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
      if (r.ok) { $("memoryInput").value = ""; refreshMemory(); setStatus("已记住"); } else setStatus(r.msg || "失败", "error");
    };
    $("startupEnabled").addEventListener("change", async (event) => {
      const toggle = event.target;
      toggle.disabled = true;
      try {
        const result = await apiJson("/settings/startup", {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: toggle.checked }),
        });
        if (!result.ok) throw new Error(result.msg || "保存失败");
        setStatus(toggle.checked ? "已开启开机启动" : "已关闭开机启动");
      } catch (err) {
        toggle.checked = !toggle.checked;
        setStatus(apiErrorMessage(err, "开机启动设置失败"), "error");
      } finally { toggle.disabled = false; }
    });

    btnRecord.addEventListener("click", async () => {
      try {
        const data = await apiJson("/record/start", { method: "POST" });
        if (data.ok) { setStatus("录制中…", "recording"); setRecording(true); }
        else setStatus(data.msg || "失败");
      } catch (err) { setStatus(apiErrorMessage(err)); }
    });
    btnStop.addEventListener("click", async () => {
      try {
        const data = await apiJson("/record/stop", { method: "POST" });
        if (data.ok) {
          const count = Number(data.event_count || 0);
          setStatus(data.replayable === false
            ? (data.warning || "未捕获步骤，请确认目标窗口在前台后重试")
            : `已保存 ${count} 步`, data.replayable === false ? "error" : "");
          setRecording(false);
          if (pendingAppendTarget && data.recording_id) await finishAppend(data.recording_id);
          else refreshTasks();
        }
        else setStatus(data.msg || "失败");
      } catch (err) { setStatus(apiErrorMessage(err)); }
    });
    btnPlay.addEventListener("click", async () => {
      const ok = await callReplay(null);
      if (ok) startPoll();
    });
    btnPause.addEventListener("click", async () => {
      if (takeoverBusy || playback.paused) return;
      try {
        const data = await apiJson("/replay/pause", { method: "POST" });
        if (data.ok) {
          setReplayPaused(true);
          setStatus(data.msg || "已暂停，请接管操作", "paused");
          setTakeover(playback);
        } else {
          await syncReplayStatus(false);
          setStatus(data.msg || "没有进行中的回放", "error");
        }
      } catch (err) { setStatus(apiErrorMessage(err)); await syncReplayStatus(false); }
    });
    btnReplayStop.addEventListener("click", async () => {
      await stopReplay();
    });
    btnList.addEventListener("click", async () => {
      panel.classList.toggle("show");
      if (panel.classList.contains("show")) { refreshTasks(); refreshDiscovery(); }
      // 悬浮条窗口默认 60px 高，任务库展开时需撑高窗口，否则面板在窗口外不可见
      try {
        const t = window.__TAURI__;
        if (t && t.core && t.core.invoke) {
          await t.core.invoke("set_panel_height", { open: panel.classList.contains("show") });
        }
      } catch (_) { /* 纯浏览器环境无窗口控制 */ }
    });

    // ---------- 退出应用（右上角 ✕）----------
    let TAGENT_TOKEN = "";
    (async () => { try { const t = await fetch(API + "/api-token").then(r => r.json()); TAGENT_TOKEN = t.token || ""; } catch (_) {} })();
    async function detachBackend() {
      try {
        await fetch(API + "/lifecycle/detach", { method: "POST",
          headers: TAGENT_TOKEN ? { "X-TAGENT-Token": TAGENT_TOKEN } : {} });
      } catch (_) { /* 后端可能已退出 */ }
    }
    btnClose.addEventListener("click", async () => {
      try {
        const t = window.__TAURI__;
        if (t && t.window && t.window.getCurrentWindow) {
          await t.window.getCurrentWindow().close();
          return;
        }
      } catch (_) { /* fallthrough */ }
      await detachBackend();
      setStatus("窗口可关闭；进行中的任务会在后台完成");
    });

    // ---------- 回放状态轮询（状态小窗 + 暂停接管） ----------
    function applyReplayStatus(d, fromStartup = false) {
      // `playing` is authoritative when supplied.  In particular the
      // backend may retain a terminal `state: "stopping"` snapshot after the
      // worker has been released; treating that state as active would strand
      // the WebView in an endless replay loop.
      const playing = d && (d.playing !== undefined
        ? d.playing === true
        : ["starting", "playing", "paused", "stopping"].includes(d.state));
      playback.recordingId = (d && (d.recording_id || d.recordingId)) || playback.recordingId || "";
      playback.currentStep = Number((d && (d.current_step ?? d.currentStep)) || 0);
      playback.total = Number((d && d.total) || 0);
      playback.stepDesc = (d && (d.step_desc || d.stepDesc)) || "";
      playback.reason = (d && (d.pause_reason || d.reason)) || "";

      if (!playing) {
        playback.playing = false;
        playback.paused = false;
        takeoverOpen = false;
        hideModal();
        setReplaying(false);
        setStatus(fromStartup ? "就绪" :
          d && d.state === "failed" ? (d.error || "回放失败") :
          d && d.state === "stopped" ? "回放已停止" : (d.summary || "回放完成"),
          d && (d.state === "failed" || String(d.summary || "").includes("断点保存失败")) ? "error" : "");
        refreshTasks();
        return false;
      }

      setReplaying(true);
      if (d.paused || d.state === "paused" || d.state === "call_user") {
        setReplayPaused(true);
        const prefix = d.state === "call_user" ? "需人工接管（步骤失败）" : "等待接管";
        setStatus(`${prefix}${playback.stepDesc ? `（${playback.currentStep + 1}/${playback.total}: ${playback.stepDesc}）` : ""}`, "paused");
        setTakeover({ ...playback, waitingStep: d.waiting_step, reason: playback.reason });
      } else {
        // A stale modal can survive a WebView reconnect.  Close it only after
        // the server confirms that replay is active again.
        if (takeoverOpen) hideModal();
        setReplayPaused(false);
        const stepInfo = playback.stepDesc
          ? ` (${playback.currentStep + 1}/${playback.total}: ${playback.stepDesc})` : "";
        setStatus(d.error ? `步骤出错: ${playback.stepDesc || "未知步骤"}` : "回放中…" + stepInfo,
          d.error ? "error" : "replaying");
      }
      return true;
    }

    async function syncReplayStatus(showError = true) {
      try {
        const d = await apiJson("/replay/status", {}, 5000);
        applyReplayStatus(d, false);
        return d;
      } catch (err) {
        if (showError) setStatus(apiErrorMessage(err), "error");
        return null;
      }
    }

    function startPoll() {
      stopPoll();
      const generation = pollGeneration;
      playback.playing = true;
      setReplaying(true);
      let failures = 0;
      const tick = async () => {
        if (generation !== pollGeneration) return;
        try {
          const d = await apiJson("/replay/status", {}, 5000);
          if (generation !== pollGeneration) return;
          failures = 0;
          if (!applyReplayStatus(d)) {
            stopPoll();
            return;
          }
        } catch (err) {
          if (generation !== pollGeneration) return;
          failures += 1;
          // Keep the replay controls alive across a short sidecar restart;
          // stopping the timer here used to leave the UI permanently stuck.
          setStatus(failures < 3 ? "正在重连后端…" : apiErrorMessage(err, "后端暂时不可用"), "error");
        }
        if (generation === pollGeneration) {
          const delay = failures ? Math.min(4000, 700 + failures * 500) : 700;
          pollTimer = setTimeout(tick, delay);
        }
      };
      tick();
    }
    function stopPoll() {
      pollGeneration += 1;
      if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    }

    async function callReplay(id) {
      try {
        const data = await apiJson("/replay/start", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(id ? { recording_id: id } : {}),
        });
        if (!data.ok) {
          setStatus(data.msg || "启动回放失败", "error");
          return false;
        }
        playback.recordingId = data.recording_id || id || "";
        playback.paused = false;
        setStatus("回放启动中…", "replaying");
        return data.ok || false;
      } catch (err) { setStatus(apiErrorMessage(err, "回放启动失败"), "error"); return false; }
    }

    // ---------- 接管对话框（阶段3 人在回路） ----------
    function setTakeover(info = playback) {
      // Polling continues while paused so that a reconnect can recover. Do
      // not rebuild the modal on every poll; rebuilding would detach a
      // clicked button and make a slow resume request look ineffective.
      if (takeoverOpen) return;
      takeoverOpen = true;
      const stepNo = Number.isFinite(Number(info.waitingStep))
        ? Number(info.waitingStep) + 1
        : Number(info.currentStep || 0) + 1;
      const total = Number(info.total || 0);
      const stepLabel = info.stepDesc
        ? `${stepNo}${total ? `/${total}` : ""}：${escapeHtml(info.stepDesc)}` : "当前步骤";
      const reason = escapeHtml(info.reason || "定位、输入或安全检查未能自动完成");
      const verifying = String(info.stepDesc || "").startsWith("核实「");
      showModal(`
        <h4>请接管操作</h4>
        <p style="font-size:12px;color:#ccc;margin-bottom:10px;">
          回放已暂停（${stepLabel}）。<br/><span style="color:#aaa">原因：${reason}</span>
        </p>
        <button class="primary" id="takeoverResume">${verifying ? "人工确认完成，继续" : "已接管完成，继续"}</button>
        <button class="cancel" id="takeoverRetry">${verifying ? "重新检查完成条件" : "重试当前步骤"}</button>
        ${verifying ? "" : '<button class="cancel" id="takeoverRerecord">重新演示此步</button>'}
        <button class="cancel" id="takeoverStop">停止回放</button>
        <div class="hint">接管后可继续执行；若目标已恢复可先重试。重新演示会从此步开始录制替代操作。</div>
      `);
      mask.dataset.locked = "1";
      const bind = (id, fn) => {
        const button = $(id);
        if (button) button.addEventListener("click", fn);
      };
      bind("takeoverResume", () => resumeReplay());
      bind("takeoverRetry", () => retryReplay());
      if (!verifying) bind("takeoverRerecord", () => rerecordStep());
      bind("takeoverStop", () => stopReplay());
    }
    async function resumeReplay() {
      if (takeoverBusy) return;
      takeoverBusy = true;
      const button = $("takeoverResume");
      if (button) button.disabled = true;
      try {
        const data = await apiJson("/replay/resume", { method: "POST" });
        if (data.ok) {
          hideModal();
          setReplayPaused(false);
          setStatus(data.msg || "已继续回放", "replaying");
          startPoll();
        } else {
          const state = await syncReplayStatus(false);
          if (!state || !state.playing) {
            hideModal();
            setReplaying(false);
            setStatus("回放已结束，请重新运行");
          } else if (!state.paused) {
            hideModal();
            startPoll();
          } else {
            setStatus(data.msg || "回放仍在等待接管", "error");
          }
        }
      } catch (err) {
        setStatus(apiErrorMessage(err), "error");
        await syncReplayStatus(false);
      } finally {
        takeoverBusy = false;
        const b = $("takeoverResume");
        if (b) b.disabled = false;
      }
    }
    async function retryReplay() {
      if (takeoverBusy) return;
      takeoverBusy = true;
      const button = $("takeoverRetry");
      if (button) button.disabled = true;
      try {
        let data;
        try {
          data = await apiJson("/replay/retry", { method: "POST" });
        } catch (err) {
          // Older sidecars do not expose /replay/retry; resume is a safe
          // compatibility fallback and simply skips the blocked step.
          if (err && err.status === 404) data = await apiJson("/replay/resume", { method: "POST" });
          else throw err;
        }
        if (data.ok) {
          hideModal();
          setReplayPaused(false);
          setStatus(data.msg || "正在重试当前步骤", "replaying");
          startPoll();
        } else {
          setStatus(data.msg || "无法重试当前步骤", "error");
          await syncReplayStatus(false);
        }
      } catch (err) {
        setStatus(apiErrorMessage(err), "error");
        await syncReplayStatus(false);
      } finally {
        takeoverBusy = false;
        const b = $("takeoverRetry");
        if (b) b.disabled = false;
      }
    }
    async function rerecordStep() {
      if (takeoverBusy) return;
      takeoverBusy = true;
      const button = $("takeoverRerecord");
      if (button) button.disabled = true;
      try {
        const data = await apiJson("/replay/rerecord", { method: "POST" });
        if (data.ok) {
          stopPoll();
          hideModal();
          playback.playing = false;
          playback.paused = false;
          setStatus("重新演示中：请操作正确步骤…", "recording");
          setRecording(true);
        } else {
          setStatus(data.msg || "操作失败");
          await syncReplayStatus(false);
        }
      } catch (err) { setStatus(apiErrorMessage(err), "error"); await syncReplayStatus(false); }
      finally { takeoverBusy = false; }
    }
    async function stopReplay() {
      if (takeoverBusy && !$("takeoverStop")) return;
      takeoverBusy = true;
      try {
        let data = null;
        try { data = await apiJson("/replay/stop", { method: "POST" }); }
        catch (err) {
          // A sidecar restart already stopped the worker; make stop idempotent
          // from the user's perspective and reconcile the visible state.
          if (!(err && err.status === 404)) throw err;
        }
        stopPoll();
        hideModal();
        playback.playing = false;
        playback.paused = false;
        setReplaying(false);
        setStatus((data && data.msg) || "已停止回放");
        refreshTasks();
      } catch (err) {
        setStatus(apiErrorMessage(err), "error");
        const state = await syncReplayStatus(false);
        if (!state) {
          // If the sidecar is offline, there is no useful action left for a
          // modal that belongs to the old session.  Release the local lock so
          // the user can close/reconnect the UI instead of being trapped in
          // “请接管操作”.  The next start request will still be guarded by
          // the server if an old worker is alive.
          stopPoll();
          hideModal();
          playback.playing = false;
          playback.paused = false;
          setReplaying(false);
          setStatus("后端未连接，已解除本地回放状态", "error");
        }
      } finally { takeoverBusy = false; }
    }

    // ---------- 任务库渲染 ----------
    async function refreshTasks() {
      const refreshVersion = (window.__tagentTasksRefreshVersion || 0) + 1;
      window.__tagentTasksRefreshVersion = refreshVersion;
      try {
        const res = await fetch(API + "/tasks");
        const data = await res.json();
        if (refreshVersion !== window.__tagentTasksRefreshVersion) return;
        taskList.innerHTML = "";
        const conf = await fetch(API + "/tasks/security/summary").then(r => r.json()).catch(() => null);
        if (refreshVersion !== window.__tagentTasksRefreshVersion) return;
        $("confBadge").textContent = conf && conf.password_edit ? "⚠ 前台含密码框" : "";
        const tasks = data.tasks || [];
        const scout = $("scoutSummary");
        if (scout) scout.textContent = `Scout · ${tasks.length} workflows · ${tasks.reduce((n,t)=>n+t.steps,0)} steps · repetitive work discovered`;
        const taskCount = $("scoutTaskCount"), stepCount = $("scoutStepCount"), runCount = $("scoutRunCount");
        if (taskCount) taskCount.textContent = tasks.length;
        if (stepCount) stepCount.textContent = tasks.reduce((n, t) => n + Number(t.steps || 0), 0);
        if (runCount) runCount.textContent = tasks.reduce((n, t) => n + Number(t.runs || 0), 0);
        for (const task of tasks) {
          taskList.appendChild(taskCard(task));
        }
      } catch (_) { taskList.innerHTML = "<p>加载失败</p>"; }
    }

    async function refreshHistory() {
      const list = $("historyList");
      if (!list) return;
      list.innerHTML = "<div class='history-empty'>加载运行记录…</div>";
      try {
        const data = await apiJson("/tasks");
        const tasks = data.tasks || [];
        const runs = [];
        for (const task of tasks.slice(0, 30)) {
          const result = await apiJson(`/tasks/${task.id}/runs`, {}, 5000).catch(() => null);
          for (const run of (result && result.runs) || []) runs.push({ ...run, task: task.name || task.id, taskId: task.id });
        }
        runs.sort((a, b) => String(b.ts || "").localeCompare(String(a.ts || "")));
        list.innerHTML = runs.length ? runs.slice(0, 20).map((run) =>
          `<div class="history-row"><span>${escapeHtml(run.task)} · ${escapeHtml(run.ts || "")}</span><span class="ok">${run.success ? "成功" : "需关注"}</span><br/><span style="color:#858b9b">${run.trigger === "schedule" ? "定时" : "手动"} · ${run.step_count} 步 · 暂停 ${run.pause_count} 次${run.failed_capability ? ` · 失败能力：${escapeHtml(run.failed_capability.name || "")}` : ""} · 方案 ${run.plan_revision || "—"}</span><br/><button class="mini plan" data-run-id="${escapeHtml(run.run_id)}" data-task-id="${escapeHtml(run.taskId || "")}">查看详情</button><div class="run-detail" hidden></div></div>`
        ).join("") : "<div class='history-empty'>还没有运行记录。</div>";
        for (const button of list.querySelectorAll("[data-run-id]")) {
          button.onclick = async () => {
            const detail = button.parentElement.querySelector(".run-detail");
            if (!detail.hidden) { detail.hidden = true; return; }
            try {
              const report = await apiJson(`/tasks/${button.dataset.taskId}/runs/${button.dataset.runId}`);
              detail.textContent = report.ok ? `${report.report.summary || ""} · ${report.report.steps.filter(s => s.status !== "ok").map(s => `${Number(s.step) + 1}: ${s.detail || s.status}`).join("；") || "无失败步骤"}` : report.msg;
            } catch (err) { detail.textContent = apiErrorMessage(err, "详情读取失败"); }
            detail.hidden = false;
          };
        }
      } catch (err) {
        list.innerHTML = `<div class='history-empty'>${escapeHtml(apiErrorMessage(err, "运行记录加载失败"))}</div>`;
      }
    }

    async function refreshDiscovery() {
      const list = $("scoutCandidates");
      if (!list) return;
      try {
        const data = await apiJson("/tasks/discovery", {}, 8000);
        const candidates = (data.candidates || []).filter((item) => item.steps > 0).slice(0, 5);
        list.innerHTML = candidates.length ? candidates.map((item) =>
          `<div class="history-row"><strong>${escapeHtml(item.name)}</strong><span class="ok">重复度 ${item.repeat_score}</span><br/><span style="color:#858b9b">${item.runs} 次运行 · 成功率 ${item.success_rate == null ? "—" : Math.round(item.success_rate * 100) + "%"} · 人工介入 ${item.human_takeovers} 次</span></div>`
        ).join("") : "<div class='history-empty'>运行几次任务后，这里会显示可优先优化的重复流程。</div>";
      } catch (err) {
        list.innerHTML = `<div class='history-empty'>${escapeHtml(apiErrorMessage(err, "Discovery 暂不可用"))}</div>`;
      }
    }

    function taskCard(task) {
      const card = el("div", "task-card");
      card.dataset.taskId = task.id;
      const row1 = el("div", "row1", null);
      row1.appendChild(el("span", "name", task.name));
      if (task.schedule) {
        const s = task.schedule;
        const label = s.kind === "daily"
          ? `每日 ${String(s.hour).padStart(2,"0")}:${String(s.minute).padStart(2,"0")}`
          : `每 ${s.minutes} 分钟`;
        row1.appendChild(el("span", "timer-tag", label + " ⏱"));
      }
      card.appendChild(row1);
      card.appendChild(el("div", "meta",
        `${task.steps} 步 · 变量 ${task.vars} · 纠错 ${task.corrections}`));
      const btns = el("div", "btns");
      const run = aBtn(task.replayable === false ? "无步骤" : "运行", "mini play", () => {
        if (task.replayable === false) { setStatus("该录制没有捕获步骤，请重新录制"); return; }
        runTask(task);
      });
      run.disabled = task.replayable === false;
      const resume = task.checkpoint
        ? aBtn(`续跑 ${task.checkpoint.next_step}/${task.checkpoint.total}`, "mini play",
          () => runTask(task, task.checkpoint.next_step))
        : null;
      const sch = aBtn("定时", "mini sched", () => schedModal(task));
      const plan = aBtn("方案", "mini plan", () => showPlan(task));
      const intent = aBtn("意图", "mini var", () => analyzeIntent(task));
      const exportBtn = aBtn("导出", "mini export", () => exportModal(task));
      const reuse = aBtn("保存能力", "mini var", () => saveCapability(task));
      const vars = aBtn("步骤/变量", "mini var", async () => {
        const res = await fetch(API + "/recordings/" + task.id + "/steps");
        const d = await res.json();
        showSteps(task.id, d.steps || []);
      });
      const appd = aBtn("追加", "mini sched", () => appendTask(task));
      const watch = aBtn("盯守", "mini var", () => watchModal(task));
      const chain = aBtn("联动", "mini var", () => chainModal(task));
      const del = aBtn("删除", "mini del", async () => {
        if (confirm("删除该任务？")) {
          try {
            const result = await apiJson("/recordings/" + task.id, { method: "DELETE" });
            if (!result.ok) { setStatus(result.msg || "删除失败", "error"); return; }
            if (currentTask === task.id) currentTask = null;
            setStatus("任务已删除");
            refreshTasks();
          } catch (err) { setStatus(apiErrorMessage(err, "删除失败"), "error"); }
        }
      });
      btns.appendChild(run);
      if (resume) btns.appendChild(resume);
      btns.appendChild(plan); btns.appendChild(intent); btns.appendChild(exportBtn); btns.appendChild(reuse); btns.appendChild(sch); btns.appendChild(vars);
      btns.appendChild(appd); btns.appendChild(watch); btns.appendChild(chain); btns.appendChild(del);
      card.appendChild(btns);
      return card;
    }

    function saveCapability(task) {
      showModal(`<h4>保存可复用能力 · ${escapeHtml(task.name)}</h4>
        <div class="hint">选择连续步骤，登记为以后可查找的操作片段。步骤从 1 开始。</div>
        <input id="capName" placeholder="能力名称，如打开订单列表" />
        <input id="capStart" type="number" min="1" max="${task.steps}" value="1" placeholder="起始步骤" />
        <input id="capEnd" type="number" min="1" max="${task.steps}" value="${task.steps}" placeholder="结束步骤" />
        <select id="capCheckType"><option value="">不设置完成条件</option><option value="window">当前窗口标题包含</option><option value="text">当前屏幕文字包含</option></select>
        <input id="capCheckValue" placeholder="完成时应看到的标题或文字" />
        <button class="primary" id="saveCap">保存</button>
        <button class="cancel" onclick="hideModal()">取消</button>`);
      $("saveCap").onclick = async () => {
        const name = $("capName").value.trim();
        const start = Number($("capStart").value);
        const end = Number($("capEnd").value);
        const checkType = $("capCheckType").value;
        const checkValue = $("capCheckValue").value.trim();
        if (!name || !Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start || end > task.steps) {
          setStatus("请填写名称和有效的步骤区间", "error"); return;
        }
        if (checkType && !checkValue) { setStatus("请填写完成时应看到的内容", "error"); return; }
        try {
          const result = await apiJson("/tasks/capabilities", {method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name, source_task: task.id, step_range: [start - 1, end],
              postcondition: checkType ? {type: checkType, value: checkValue} : {}})});
          if (!result.ok) { setStatus(result.msg || "保存失败", "error"); return; }
          hideModal(); setStatus(`已保存能力「${name}」`, "ok"); await refreshTasks();
        } catch (err) { setStatus(apiErrorMessage(err, "保存失败"), "error"); }
      };
    }

    async function composeCapabilities() {
      try {
        const data = await apiJson("/tasks/capabilities");
        const caps = data.capabilities || [];
        if (!caps.length) { setStatus("请先从任务中保存可复用能力", "error"); return; }
        showModal(`<h4>组合可复用能力</h4>
          <input id="composedName" placeholder="新任务名称" />
          <div class="hint">填写执行顺序；留空表示不加入。</div>
          <div class="step-list">${caps.map((cap) => `<label class="step-item"><span>${escapeHtml(cap.name)} · ${escapeHtml(cap.description || "")}</span><input class="cap-order" data-cap-id="${escapeHtml(cap.id)}" type="number" min="1" max="${caps.length}" placeholder="顺序" /></label>`).join("")}</div>
          <button class="primary" id="composeCaps">生成任务</button>
          <button class="cancel" onclick="hideModal()">取消</button>`);
        $("composeCaps").onclick = async () => {
          const selected = [...document.querySelectorAll(".cap-order")]
            .filter((input) => input.value !== "")
            .map((input) => ({id: input.dataset.capId, order: Number(input.value)}))
            .sort((a, b) => a.order - b.order);
          const name = $("composedName").value.trim();
          if (!name || !selected.length || selected.some((item, i) => !Number.isInteger(item.order) || item.order < 1 || item.order > selected.length || (i && item.order === selected[i - 1].order))) {
            setStatus("请填写任务名，并为所选能力填写不重复的顺序", "error"); return;
          }
          try {
            const result = await apiJson("/tasks/capabilities/compose", {method: "POST", headers: {"Content-Type": "application/json"},
              body: JSON.stringify({name, capability_ids: selected.map((item) => item.id)})});
            if (!result.ok) { setStatus(result.msg || "组合失败", "error"); return; }
            hideModal(); setStatus(`已生成任务「${name}」，请审阅执行方案`, "ok"); await refreshTasks();
          } catch (err) { setStatus(apiErrorMessage(err, "组合失败"), "error"); }
        };
      } catch (err) { setStatus(apiErrorMessage(err, "能力列表读取失败"), "error"); }
    }

    async function showPlan(task) {
      try {
        const data = await apiJson(`/tasks/${task.id}/plan`);
        if (!data.ok) { setStatus(data.msg || "方案加载失败", "error"); return; }
        const plan = data.plan || {};
        const reviewData = await apiJson(`/tasks/${task.id}/review`).catch(() => ({review: {}}));
        const savedAnswers = (reviewData.review || {}).answers || {};
        const modeLabel = { deterministic: "确定性", agent_assist: "辅助建议", human_required: "人工确认" };
        const rows = (plan.steps || []).map((s) => `
          <div class="step-item"><span><span class="action">${s.index + 1}. ${escapeHtml(s.action)}</span>
          ${s.text_preview ? ` ${escapeHtml(s.text_preview)}` : ""}${s.capability ? ` · ${escapeHtml(s.capability.name)}` : ""}${s.postcondition ? ` · 验证${s.postcondition.type === "window" ? "窗口" : "文字"}「${escapeHtml(s.postcondition.value)}」` : ""}</span>
          <span style="color:${s.mode === "human_required" ? "#f39c12" : s.mode === "agent_assist" ? "#9b59b6" : "#2ecc71"}">${modeLabel[s.mode] || s.mode}</span></div>`).join("");
        const questions = (plan.open_questions || []).map((q) => `<label class="step-item"><span>步骤 ${q.step + 1}：${escapeHtml(q.question)}</span><input class="plan-answer" data-step="${q.step}" value="${escapeHtml(savedAnswers[q.step] || "")}" /></label>`).join("");
        showModal(`<h4>Automation Plan · ${escapeHtml(task.name)}</h4>
          <div class="hint">执行策略：确定性优先，辅助只提出建议，不在运行中改写流程。</div>
          <div class="step-list">${rows || "<div class='hint'>暂无步骤</div>"}</div>
          ${questions ? `<div class="hint" style="color:#f3c26b;margin-top:8px">请回答开放问题</div><div class="step-list">${questions}</div>` : "<div class='hint' style='color:#2ecc71'>没有待确认问题，可批准并导出。</div>"}
          <button class="primary" id="approvePlan">批准此方案</button>
          <button class="cancel" onclick="hideModal()">关闭</button>`);
        const approve = $("approvePlan");
        if (approve) approve.onclick = async () => {
          const answers = Object.fromEntries([...document.querySelectorAll(".plan-answer")].map((input) => [input.dataset.step, input.value.trim()]));
          const result = await apiJson(`/tasks/${task.id}/review`, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({approved: true, feedback: [], answers})});
          if (!result.ok) { setStatus(result.msg || "仍有问题需要回答", "error"); return; }
          approve.textContent = "已批准"; approve.disabled = true; setStatus("方案已批准，可导出最终产物", "ok");
        };
      } catch (err) { setStatus(apiErrorMessage(err, "方案加载失败"), "error"); }
    }

    function exportModal(task) {
      showModal(`<h4>导出最终产物 · ${escapeHtml(task.name)}</h4>
        <div class="hint">先审阅 Plan，再选择可移植的 Skill 或 Automation。导出不会自动运行任务。</div>
        <button class="primary" id="exportSkill">导出 Skill.md</button>
        <button class="primary" id="exportAutomation">导出 automation.json</button>
        <button class="primary" id="exportBundle">导出证据包(zip)</button>
        <button class="cancel" onclick="hideModal()">取消</button>`);
      $("exportSkill").onclick = () => doExport(task, "skill");
      $("exportAutomation").onclick = () => doExport(task, "automation");
      $("exportBundle").onclick = () => doExport(task, "bundle");
    }
    async function doExport(task, kind) {
      try {
        const data = await apiJson(`/tasks/${task.id}/export`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind }),
        });
        if (!data.ok) { setStatus(data.msg || "导出失败", "error"); return; }
        showModal(`<h4>文件已生成</h4><p class="hint">保存在本机：</p><code style="display:block;overflow-wrap:anywhere;user-select:text">${escapeHtml(data.path || "")}</code><button class="cancel" onclick="hideModal()">关闭</button>`);
        setStatus(`${kind === "skill" ? "Skill.md" : kind === "bundle" ? "证据包" : "automation.json"} 已生成`, "replaying");
      } catch (err) { setStatus(apiErrorMessage(err, "导出失败"), "error"); }
    }
    async function analyzeIntent(task) {
      try {
        const d = await apiJson(`/tasks/${task.id}/analysis`, { method: "POST" });
        if (!d.ok) { setStatus(d.msg || "分析失败", "error"); return; }
        const a = d.analysis;
        window.__analysisTask = task;
        showModal(`
          <h4>意图分析 - ${escapeHtml(task.name || task.id)}</h4>
          <div class="hint">标题</div><input id="analysisTitle" value="${escapeHtml(a.title || "")}" />
          <div class="hint">意图</div><input id="analysisIntent" value="${escapeHtml(a.intent || "")}" />
          <div class="hint">${a.steps.length} 步（${a.steps.filter(s => s.kind === "action").length} 个改状态动作）· ${escapeHtml(a.generalization || "")}</div>
          <button class="primary" onclick="saveAnalysis('${task.id}')">保存并批准</button>
          <button class="cancel" onclick="hideModal()">取消</button>
        `);
      } catch (err) { setStatus(apiErrorMessage(err, "分析失败"), "error"); }
    }
    async function saveAnalysis(id) {
      try {
        const d = await apiJson(`/tasks/${id}/analysis`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: $("analysisTitle").value, intent: $("analysisIntent").value, approved: true }),
        });
        setStatus(d.ok ? "意图分析已批准（导出 Skill.md 将包含该意图）" : (d.msg || "保存失败"), d.ok ? "" : "error");
        hideModal();
      } catch (err) { setStatus(apiErrorMessage(err, "保存失败"), "error"); }
    }
    let pendingAppendTarget = null;
    async function appendTask(task) {
      if (pendingAppendTarget) return;
      try {
        const res = await fetch(API + "/record/start", { method: "POST" });
        const data = await res.json();
        if (data.ok) {
          pendingAppendTarget = task.id;
          setStatus(`追加录制中…（将并入「${task.name}」）`, "recording");
          setRecording(true);
        } else setStatus(data.msg || "失败");
      } catch (_) { setStatus("服务未连接"); }
    }
    async function finishAppend(recId) {
      const target = pendingAppendTarget;
      pendingAppendTarget = null;
      try {
        const dry = await fetch(`${API}/recordings/${target}/merge/${recId}`,
          { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ dry: true }) });
        const dryData = await dry.json();
        if (dryData.warning && !confirm(dryData.warning)) {
          setStatus("已取消合并，追加录制保留为独立任务");
          refreshTasks();
          return;
        }
        const res = await fetch(`${API}/recordings/${target}/merge/${recId}`, { method: "POST" });
        const data = await res.json();
        if (data.ok) setStatus(`已合并：任务现有 ${data.steps} 步，去重 ${data.removed} 步`);
        else setStatus(data.msg || "合并失败");
      } catch (_) { setStatus("服务未连接"); }
      refreshTasks();
    }
    function aBtn(text, cls, fn) {
      const b = el("button", cls, text);
      b.onclick = fn;
      return b;
    }
    async function runTask(id) {
      if (await callReplay(id)) { setStatus("回放中…", "replaying"); startPoll(); }
    }
    async function runTaskWithVars(id, vars, fromStep = 0) {
      try {
        const d = await apiJson("/replay/start", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ recording_id: id, variables: vars, from_step: fromStep,
            resume_checkpoint: fromStep > 0 }),
        });
        if (d.ok) {
          hideModal();
          playback.recordingId = d.recording_id || id;
          setStatus("回放启动中…", "replaying");
          startPoll();
        } else setStatus(d.msg || "失败", "error");
      } catch (err) { setStatus(apiErrorMessage(err, "回放启动失败"), "error"); }
    }
    async function runTask(task, fromStep = 0) {
      if (task.vars > 0) {
        try {
          const d = await apiJson("/tasks/" + task.id + "/vars", {}, 5000);
          const names = d.vars || ["value"];
          const examples = Object.fromEntries((d.details || []).map(x => [x.name, x.example]));
          showModal(`
            <h4>${fromStep ? "续跑" : "运行"}「${task.name}」</h4>
            <div class="hint">填写本次运行的变量值（已预填录制示例，可直接运行）：</div>
            ${names.map((n, i) =>
              `<input id="varInput${i}" data-name="${n}" placeholder="${n}" value="${escapeHtml(examples[n] || "")}" />`).join("")}
            <button class="primary" onclick="runWithVars('${task.id}',${names.length},${fromStep})">${fromStep ? "续跑" : "运行"}</button>
            <button class="cancel" onclick="hideModal()">取消</button>
          `);
        } catch (err) {
          setStatus(apiErrorMessage(err, "读取变量失败"), "error");
        }
      } else {
        await runTaskWithVars(task.id, {}, fromStep);
      }
    }
    async function runWithVars(id, count, fromStep = 0) {
      const vars = {};
      for (let i = 0; i < count; i++) {
        const inp = $("varInput" + i);
        const name = inp.dataset.name;
        const value = inp.value.trim();
        if (value && name) vars[name] = value;
      }
      runTaskWithVars(id, vars, fromStep);
    }

    async function watchModal(task) {
      try {
        const w = await apiJson("/watchers");
        const mine = (w.watchers || []).find(x => x.rec_id === task.id) || {};
        const c = mine.condition || { kind: "file_exists", value: "" };
        showModal(`
          <h4>盯守 - ${escapeHtml(task.name || task.id)}</h4>
          <div class="hint">条件成立时自动运行该任务（冷却 ${mine.cooldown_min || 10} 分钟，每 20 秒检查）</div>
          <select id="watchKind">
            <option value="file_exists" ${c.kind === "file_exists" ? "selected" : ""}>文件出现</option>
            <option value="window_appears" ${c.kind === "window_appears" ? "selected" : ""}>窗口出现</option>
          </select>
          <input id="watchValue" placeholder="${c.kind === "window_appears" ? "窗口标题包含…" : "文件路径或 glob"}" value="${escapeHtml(c.value || "")}" />
          <button class="primary" onclick="saveWatch('${task.id}')">保存盯守</button>
          <button class="cancel" onclick="delWatch('${task.id}')">关闭盯守</button>
          <button class="cancel" onclick="hideModal()">取消</button>
        `);
      } catch (err) { setStatus(apiErrorMessage(err, "读取盯守失败"), "error"); }
    }
    async function saveWatch(id) {
      const kind = $("watchKind").value, value = $("watchValue").value.trim();
      const r = await apiJson("/watchers", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rec_id: id, condition: { kind, value } }) });
      setStatus(r.ok ? "盯守已保存" : (r.msg || "失败"), r.ok ? "" : "error");
      if (r.ok) hideModal();
    }
    async function delWatch(id) {
      await apiJson("/watchers?rec_id=" + encodeURIComponent(id), { method: "DELETE" });
      setStatus("盯守已关闭"); hideModal();
    }
    async function chainModal(task) {
      try {
        const p = await apiJson("/tasks/" + task.id + "/pipeline");
        const t = await apiJson("/tasks", {}, 8000);
        const others = (t.tasks || []).filter(x => x.id !== task.id).slice(0, 30);
        const cur = (p.pipeline && p.pipeline.steps) || [];
        window.__chainOthers = others;
        showModal(`
          <h4>联动 - ${escapeHtml(task.name || task.id)}</h4>
          <div class="hint">本任务结束后自动运行下一个（success=成功时 / fail=失败时 / always=总是）</div>
          <div id="chainRows">
            ${cur.map(s => chainRow(others, s)).join("") || "<div class='hint'>尚未配置联动</div>"}
          </div>
          <button class="mini" onclick="addChainRow()">添加一环</button>
          <button class="primary" onclick="saveChain('${task.id}')">保存联动</button>
          <button class="cancel" onclick="hideModal()">取消</button>
        `);
      } catch (err) { setStatus(apiErrorMessage(err, "读取联动失败"), "error"); }
    }
    function chainRow(others, cur) {
      cur = cur || { rec_id: "", on: "success" };
      const opts = others.map(o => `<option value="${o.id}" ${o.id === cur.rec_id ? "selected" : ""}>${escapeHtml(o.name || o.id)}</option>`).join("");
      return `<div style="display:flex;gap:6px;margin:4px 0"><select style="flex:1" data-chain-task>${cur.rec_id ? "" : "<option value=''>选择任务…</option>"}${opts}</select><select data-chain-on>${["success", "fail", "always"].map(k => `<option ${k === cur.on ? "selected" : ""}>${k}</option>`).join("")}</select><button class="mini del" onclick="this.parentElement.remove()">删</button></div>`;
    }
    function addChainRow() { $("chainRows").insertAdjacentHTML("beforeend", chainRow(window.__chainOthers || [])); }
    async function saveChain(id) {
      const steps = [...document.querySelectorAll("#chainRows > div")].map(row => ({
        rec_id: row.querySelector("[data-chain-task]").value,
        on: row.querySelector("[data-chain-on]").value,
      })).filter(s => s.rec_id);
      const r = await apiJson("/tasks/" + id + "/pipeline", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ steps }) });
      setStatus(r.ok ? "联动已保存" : (r.msg || "失败"), r.ok ? "" : "error");
      if (r.ok) hideModal();
    }
    async function schedModal(task) {
      window.__scheduleTask = task;
      showModal(`
        <h4>定时执行 - ${task.name}</h4>
        <select id="schedKind" onchange="toggleSchedForm()">
          <option value="interval" ${task.schedule && task.schedule.kind === "interval" ? "selected" : ""}>每隔 N 分钟</option>
          <option value="daily" ${task.schedule && task.schedule.kind === "daily" ? "selected" : ""}>每天固定时间</option>
        </select>
        <div id="schedWhat"></div>
        <div id="schedVars"></div>
        <button class="primary" onclick="saveSched('${task.id}')">保存</button>
        <button class="cancel" onclick="deleteSched('${task.id}')">关闭定时</button>
        <button class="cancel" onclick="hideModal()">取消</button>
      `);
      toggleSchedForm();
      try {
        const [vars, bindings] = await Promise.all([
          apiJson(`/tasks/${task.id}/vars`), apiJson(`/tasks/${task.id}/var_sources`),
        ]);
        const names = vars.vars || [];
        $("schedVars").innerHTML = names.length ? `<div class="hint">定时运行时从哪里读取变量：</div>${names.map((name) => {
          const source = (bindings.sources || {})[name] || {};
          return `<label class="step-item">${escapeHtml(name)}
            <select class="sched-var-source" data-name="${escapeHtml(name)}">
              <option value="">请选择来源</option>
              <option value="clipboard" ${source.type === "clipboard" ? "selected" : ""}>剪贴板</option>
              <option value="ui" ${source.type === "ui" ? "selected" : ""}>当前窗口控件</option>
            </select><input class="sched-var-control" data-name="${escapeHtml(name)}" placeholder="控件名称（界面控件时填写）" value="${escapeHtml(source.control_name || "")}" /></label>`;
        }).join("")}` : "";
      } catch (err) { setStatus(apiErrorMessage(err, "变量来源读取失败"), "error"); }
    }
    function toggleSchedForm() {
      const kind = $("schedKind") ? $("schedKind").value : "interval";
      $("schedWhat").innerHTML = kind === "interval"
        ? `<input id="schedMinutes" type="number" min="1" placeholder="间隔分钟数，默认60" />`
        : `<div class="row">
             <input id="schedHour" type="number" min="0" max="23" placeholder="时" />
             <input id="schedMin" type="number" min="0" max="59" placeholder="分" />
           </div>`;
    }
    async function saveSched(id) {
      const kind = $("schedKind").value;
      const sched = kind === "interval"
        ? { kind, minutes: parseInt($("schedMinutes").value || 60, 10) }
        : { kind, hour: parseInt($("schedHour").value || 9, 10), minute: parseInt($("schedMin").value || 0, 10) };
      try {
        for (const select of document.querySelectorAll(".sched-var-source")) {
          const name = select.dataset.name;
          const type = select.value;
          const control = [...document.querySelectorAll(".sched-var-control")].find((input) => input.dataset.name === name)?.value.trim();
          if (!type || (type === "ui" && !control)) { setStatus(`请设置变量「${name}」的来源`, "error"); return; }
          const source = type === "ui" ? {type, control_name: control} : {type};
          const saved = await apiJson(`/tasks/${id}/var_sources/${encodeURIComponent(name)}`, {
            method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(source),
          });
          if (!saved.ok) { setStatus(saved.msg || "变量来源保存失败", "error"); return; }
        }
        const result = await apiJson("/tasks/" + id + "/schedule", {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(sched),
        });
        if (!result.ok) {
          setStatus(result.msg || "定时设置失败", "error");
          if ((result.msg || "").includes("审阅并批准") && window.__scheduleTask) {
            const task = window.__scheduleTask;
            hideModal(); await showPlan(task);
          }
          return;
        }
        hideModal(); refreshTasks();
      } catch (err) { setStatus(apiErrorMessage(err, "定时设置失败"), "error"); }
    }
    async function deleteSched(id) {
      try {
        const result = await apiJson("/tasks/" + id + "/schedule", { method: "DELETE" });
        if (!result.ok) { setStatus(result.msg || "取消定时失败", "error"); return; }
        hideModal(); refreshTasks();
      } catch (err) { setStatus(apiErrorMessage(err, "取消定时失败"), "error"); }
    }

    async function showSteps(taskId, steps) {
      currentTask = taskId;
      stepList.innerHTML = "";
      for (const s of steps) {
        const row = el("div", "step-item");
        const left = el("span");
        left.appendChild(el("span", "action", s.action || "?"));
        if (s.text) left.appendChild(document.createTextNode(" " + s.text + " "));
        if (s.key) left.appendChild(document.createTextNode(" [" + s.key + "] "));
        if (s.window) left.appendChild(document.createTextNode(s.window.slice(0, 18)));
        row.appendChild(left);
        if (s.action === "input_text") {
          const b = aBtn("设为变量", "var-btn", () => markVar(taskId, s.index));
          row.appendChild(b);
        }
        stepList.appendChild(row);
      }
    }
    async function markVar(recId, idx) {
      showModal(`
        <h4>变量标注</h4>
        <p class="hint">将这一步的输入替换为运行时可填写的变量。</p>
        <input id="varName" placeholder="变量名，如 订单号" />
        <button class="primary" onclick="confirmVar('${recId}',${idx})">标注为变量</button>
        <button class="cancel" onclick="hideModal()">取消</button>
      `);
    }
    async function confirmVar(recId, idx) {
      const name = $("varName").value.trim() || "value";
      await fetch(`${API}/tasks/${recId}/vars/${idx}?var=${encodeURIComponent(name)}`, { method: "PUT" });
      hideModal();
      const res = await fetch(`${API}/recordings/${recId}/steps`);
      const d = await res.json();
      showSteps(recId, d.steps || []);
      refreshTasks();
    }

    // Reconcile the WebView with the sidecar whenever the window is opened or
    // the backend is restarted.  Previously the page had no initial sync, so
    // a stale takeover dialog could remain visible while all controls said
    // “service disconnected”.
    let startupRetryTimer = null;
    async function syncAppState() {
      try {
        const s = await apiJson("/status", {}, 5000);
        if (startupRetryTimer) { clearTimeout(startupRetryTimer); startupRetryTimer = null; }
        if (s.recording) {
          stopPoll();
          playback.playing = false;
          playback.paused = false;
          hideModal();
          setRecording(true);
          setStatus("录制中…", "recording");
        } else {
          setRecording(false);
          const replay = await syncReplayStatus(false);
          if (replay && replay.playing) {
            // syncReplayStatus already rendered the paused/playing state;
            // start a resilient loop to follow it until terminal.
            startPoll();
          } else if (!playback.playing) {
            setStatus("就绪");
          }
        }
      } catch (_) {
        setStatus("服务未连接", "error");
        // Keep trying in the background so launching the sidecar after the UI
        // still recovers without requiring a page reload.
        if (!startupRetryTimer) {
          startupRetryTimer = setTimeout(() => {
            startupRetryTimer = null;
            syncAppState();
          }, 2500);
        }
      }
    }
    syncAppState();
  