const defaults = {
  apiBase: import.meta.env.VITE_ARA_API_BASE_URL || "https://api.ara.so",
  appId: import.meta.env.VITE_ARA_APP_ID || "",
  runtimeKey: import.meta.env.VITE_ARA_RUNTIME_KEY || "",
  agentId: import.meta.env.VITE_ARA_CHAT_AGENT_ID || "demo-chat",
  extraInput: import.meta.env.VITE_ARA_EXTRA_INPUT_JSON || "",
};

const els = {
  apiBase: document.getElementById("apiBase"),
  appId: document.getElementById("appId"),
  runtimeKey: document.getElementById("runtimeKey"),
  agentId: document.getElementById("agentId"),
  extraInput: document.getElementById("extraInput"),
  saveConfig: document.getElementById("saveConfig"),
  status: document.getElementById("status"),
  messages: document.getElementById("messages"),
  chatForm: document.getElementById("chatForm"),
  messageInput: document.getElementById("messageInput"),
};

function pickNonEmpty(value, fallback = "") {
  const direct = typeof value === "string" ? value.trim() : "";
  if (direct) return direct;
  return typeof fallback === "string" ? fallback.trim() : "";
}

function loadConfig() {
  const raw = localStorage.getItem("araSessionChatConfig");
  if (!raw) return { ...defaults };
  try {
    const parsed = JSON.parse(raw);
    return {
      apiBase: pickNonEmpty(parsed?.apiBase, defaults.apiBase),
      appId: pickNonEmpty(parsed?.appId, defaults.appId),
      runtimeKey: pickNonEmpty(parsed?.runtimeKey, defaults.runtimeKey),
      agentId: pickNonEmpty(parsed?.agentId, defaults.agentId),
      extraInput: typeof parsed?.extraInput === "string" ? parsed.extraInput : defaults.extraInput,
    };
  } catch {
    return { ...defaults };
  }
}

function saveConfig() {
  const cfg = {
    apiBase: pickNonEmpty(els.apiBase.value, defaults.apiBase),
    appId: pickNonEmpty(els.appId.value, defaults.appId),
    runtimeKey: pickNonEmpty(els.runtimeKey.value, defaults.runtimeKey),
    agentId: pickNonEmpty(els.agentId.value, defaults.agentId),
    extraInput: typeof els.extraInput.value === "string" ? els.extraInput.value.trim() : "",
  };
  // Demo-only tradeoff: persists runtime key in localStorage for convenience.
  // Any XSS on this origin can read and exfiltrate the stored key.
  localStorage.setItem("araSessionChatConfig", JSON.stringify(cfg));
  setStatus("Config saved.");
  return cfg;
}

function setStatus(text) {
  els.status.textContent = text;
}

function addMessage(role, content) {
  const wrap = document.createElement("div");
  wrap.className = `bubble ${role}`;
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = role;
  const body = document.createElement("div");
  body.textContent = content;
  wrap.appendChild(meta);
  wrap.appendChild(body);
  els.messages.appendChild(wrap);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function extractReply(data) {
  if (!data || typeof data !== "object") return "Run completed. No text output.";
  const candidates = [
    data?.result?.output_text,
    data?.result?.text,
    data?.result?.message,
    data?.output_text,
    data?.text,
    data?.message,
  ];
  for (const c of candidates) {
    if (typeof c === "string" && c.trim()) return c.trim();
  }
  return "Run completed. No text output.";
}

function parseExtraInput(raw) {
  const text = typeof raw === "string" ? raw.trim() : "";
  if (!text) return {};
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("Extra Input JSON must be valid JSON object text.");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Extra Input JSON must be an object.");
  }
  return parsed;
}

async function sendMessage(cfg, inputPayload) {
  const url = `${cfg.apiBase.replace(/\/+$/, "")}/v1/apps/${cfg.appId}/run`;
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
    throw new Error(err);
  }
  return extractReply(data);
}

const initial = loadConfig();
els.apiBase.value = initial.apiBase || defaults.apiBase;
els.appId.value = initial.appId || "";
els.runtimeKey.value = initial.runtimeKey || "";
els.agentId.value = initial.agentId || defaults.agentId;
els.extraInput.value = initial.extraInput || "";

addMessage("assistant", "Ready. This demo sends direct runtime requests to Ara API.");

els.saveConfig.addEventListener("click", () => {
  saveConfig();
});

if (defaults.appId && defaults.runtimeKey) {
  setStatus("Loaded App config from Vite env.");
} else {
  setStatus("Set app config in .env.local or the form.");
}

els.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = els.messageInput.value.trim();
  if (!text) return;
  const cfg = saveConfig();
  if (!cfg.appId) {
    setStatus("Set App ID first.");
    return;
  }
  if (!cfg.runtimeKey) {
    setStatus("Set Runtime Key first.");
    return;
  }
  let extraInput = {};
  try {
    extraInput = parseExtraInput(cfg.extraInput);
  } catch (err) {
    setStatus(`Invalid Extra Input JSON: ${err instanceof Error ? err.message : String(err)}`);
    return;
  }
  const inputPayload = {
    ...extraInput,
    message: text,
    run_id: `web-${Date.now()}`,
  };
  addMessage("user", text);
  els.messageInput.value = "";
  setStatus("Sending...");
  try {
    const reply = await sendMessage(cfg, inputPayload);
    addMessage("assistant", reply);
    setStatus("OK");
  } catch (err) {
    addMessage("assistant", `Error: ${err instanceof Error ? err.message : String(err)}`);
    setStatus("Request failed");
  }
});
