const API_BASE_URL = "https://api.ara.so";
const STORAGE_KEY = "ara11labsDemoConfig";

const defaults = {
  appId: import.meta.env.VITE_ARA_11LABS_APP_ID || "",
  runtimeKey: import.meta.env.VITE_ARA_11LABS_RUNTIME_KEY || "",
  agentId: import.meta.env.VITE_ARA_11LABS_AGENT_ID || "labs11_voice_ops_assistant",
  toNumber: import.meta.env.VITE_ARA_11LABS_TO_NUMBER || "",
  message: import.meta.env.VITE_ARA_11LABS_MESSAGE || "Team standup in 10 minutes.",
};

const els = {
  appId: document.getElementById("appId"),
  runtimeKey: document.getElementById("runtimeKey"),
  agentId: document.getElementById("agentId"),
  toNumber: document.getElementById("toNumber"),
  messageText: document.getElementById("messageText"),
  saveConfig: document.getElementById("saveConfig"),
  startCall: document.getElementById("startCall"),
  adminStatus: document.getElementById("adminStatus"),
  actionStatus: document.getElementById("actionStatus"),
  indicatorCall: document.getElementById("indicatorCall"),
  chatMessages: document.getElementById("chatMessages"),
  chatForm: document.getElementById("chatForm"),
  chatInput: document.getElementById("chatInput"),
  result: document.getElementById("result"),
};

function nonEmpty(value, fallback = "") {
  const text = typeof value === "string" ? value.trim() : "";
  return text || fallback;
}

function setAdminStatus(text) {
  els.adminStatus.textContent = text;
}

function setActionStatus(text) {
  els.actionStatus.textContent = text;
}

function setResult(obj) {
  els.result.textContent = JSON.stringify(obj, null, 2);
}

function setIndicator(state, label) {
  els.indicatorCall.className = `indicator ${state}`;
  els.indicatorCall.textContent = label;
}

function addChatMessage(role, text) {
  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${role}`;
  bubble.textContent = text;
  els.chatMessages.appendChild(bubble);
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
}

function extractReply(data) {
  const candidates = [
    data?.result?.output_text,
    data?.result?.text,
    data?.result?.message,
    data?.output_text,
    data?.text,
    data?.message,
  ];
  for (const item of candidates) {
    if (typeof item === "string" && item.trim()) {
      return item.trim();
    }
  }
  return "Run completed.";
}

function detectCallIntent(text) {
  const value = nonEmpty(text).toLowerCase();
  return value.includes("call me") || value.includes("start call") || value.includes("call");
}

function loadConfig() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return { ...defaults };
  try {
    const parsed = JSON.parse(raw);
    return {
      appId: nonEmpty(parsed.appId, defaults.appId),
      runtimeKey: nonEmpty(parsed.runtimeKey, defaults.runtimeKey),
      agentId: nonEmpty(parsed.agentId, defaults.agentId),
      toNumber: nonEmpty(parsed.toNumber, defaults.toNumber),
      message: nonEmpty(parsed.message, defaults.message),
    };
  } catch {
    return { ...defaults };
  }
}

function saveConfig() {
  const cfg = {
    appId: nonEmpty(els.appId.value, defaults.appId),
    runtimeKey: nonEmpty(els.runtimeKey.value, defaults.runtimeKey),
    agentId: nonEmpty(els.agentId.value, defaults.agentId),
    toNumber: nonEmpty(els.toNumber.value, defaults.toNumber),
    message: nonEmpty(els.messageText.value, defaults.message),
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg));
  return cfg;
}

function assertConfig(cfg) {
  if (!cfg.appId) throw new Error("Set App ID first.");
  if (!cfg.runtimeKey) throw new Error("Set Runtime Key first.");
  if (!cfg.agentId) throw new Error("Set Agent ID first.");
}

async function runAgent(cfg, inputPayload) {
  const url = `${API_BASE_URL}/v1/apps/${cfg.appId}/run`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${cfg.runtimeKey}`,
    },
    body: JSON.stringify({
      agent_id: cfg.agentId,
      workflow_id: cfg.agentId,
      warmup: false,
      input: inputPayload,
    }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const err = data?.error || data?.detail || data?.message || `HTTP ${response.status}`;
    throw new Error(typeof err === "string" ? err : JSON.stringify(err));
  }
  return data;
}

async function runCallAction() {
  const cfg = saveConfig();
  assertConfig(cfg);
  if (!cfg.toNumber) throw new Error("Set To Number first.");

  setIndicator("running", "Running");
  setActionStatus("Starting call...");
  try {
    const data = await runAgent(cfg, {
      mode: "call-only",
      to_number: cfg.toNumber,
      message: cfg.message,
      run_id: `web-11labs-call-${Date.now()}`,
    });
    setResult(data);
    setIndicator("success", "Success");
    setActionStatus("Call request complete");
    return data;
  } catch (error) {
    setIndicator("error", "Error");
    setActionStatus("Call failed");
    setResult({ ok: false, error: String(error) });
    throw error;
  }
}

const initial = loadConfig();
els.appId.value = initial.appId;
els.runtimeKey.value = initial.runtimeKey;
els.agentId.value = initial.agentId;
els.toNumber.value = initial.toNumber;
els.messageText.value = initial.message;
setAdminStatus(initial.appId && initial.runtimeKey ? "Ready" : "Fill config and save.");
setActionStatus("Idle");
setIndicator("idle", "Idle");
addChatMessage("assistant", "Ready. Say 'call me' or use Start Call.");

els.saveConfig.addEventListener("click", () => {
  saveConfig();
  setAdminStatus("Config saved.");
});

els.startCall.addEventListener("click", async () => {
  await runCallAction().catch(() => {});
});

els.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = nonEmpty(els.chatInput.value);
  if (!text) return;
  const cfg = saveConfig();
  try {
    assertConfig(cfg);
    addChatMessage("user", text);
    els.chatInput.value = "";

    if (detectCallIntent(text)) {
      const data = await runCallAction();
      addChatMessage("assistant", "Call flow executed. Check result panel on the right.");
      if (data?.result?.ok) addChatMessage("assistant", "Call request accepted.");
      return;
    }

    setActionStatus("Sending chat...");
    const payload = { message: text, run_id: `web-11labs-chat-${Date.now()}` };
    if (cfg.toNumber) payload.to_number = cfg.toNumber;
    const data = await runAgent(cfg, payload);
    setResult(data);
    addChatMessage("assistant", extractReply(data));
    setActionStatus("Chat done");
  } catch (error) {
    addChatMessage("assistant", `Error: ${String(error)}`);
    setActionStatus("Chat failed");
    setResult({ ok: false, error: String(error) });
  }
});
