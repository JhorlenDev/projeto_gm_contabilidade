(() => {
  const CONFIG = window.GM_CONFIG;
  if (!CONFIG) {
    throw new Error("GM_CONFIG não foi carregado.");
  }

  const APP_STORAGE_PREFIX = "gm.contabilidade.";
  const STORAGE_KEY = "gm.contabilidade.session";
  const PKCE_KEY_PREFIX = "gm.contabilidade.pkce";
  const DEBUG_ENABLED = Boolean(CONFIG.debug);
  const DEBUG_HISTORY_KEY = "gm.debug.history";

  const textEncoder = new TextEncoder();
  const textDecoder = new TextDecoder();
  let debugPanel = null;

  function readDebugHistory() {
    try {
      const raw = localStorage.getItem(DEBUG_HISTORY_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (_error) {
      return [];
    }
  }

  function saveDebugHistory(entries) {
    try {
      localStorage.setItem(DEBUG_HISTORY_KEY, JSON.stringify(entries.slice(-80)));
    } catch (_error) {
      // Ignore debug storage failures.
    }
  }

  function serializeDebugData(data) {
    if (data === undefined) {
      return "";
    }

    if (typeof data === "string") {
      return data;
    }

    if (data instanceof Error) {
      return JSON.stringify(
        {
          name: data.name,
          message: data.message,
          stack: data.stack,
        },
        null,
        2,
      );
    }

    try {
      return JSON.stringify(data, null, 2);
    } catch (_error) {
      return String(data);
    }
  }

  function ensureDebugPanel() {
    if (!DEBUG_ENABLED || debugPanel || !document.body) {
      return debugPanel;
    }

    const panel = document.createElement("div");
    panel.id = "gm-debug-panel";
    panel.style.cssText = [
      "position:fixed",
      "right:12px",
      "bottom:12px",
      "z-index:9999",
      "width:min(520px,calc(100vw - 24px))",
      "max-height:38vh",
      "overflow:auto",
      "padding:12px",
      "border-radius:12px",
      "background:rgba(10,10,10,0.92)",
      "color:#e8e8e8",
      "font:12px/1.45 monospace",
      "box-shadow:0 16px 40px rgba(0,0,0,0.35)",
      "border:1px solid rgba(255,255,255,0.12)",
    ].join(";");

    const title = document.createElement("div");
    title.textContent = "GM DEBUG";
    title.style.cssText = "font-weight:700;letter-spacing:0.08em;margin-bottom:10px;color:#7cf0b6;";

    const logList = document.createElement("div");
    logList.id = "gm-debug-log";
    logList.style.cssText = "display:grid;gap:8px;white-space:pre-wrap;word-break:break-word;";

    panel.appendChild(title);
    panel.appendChild(logList);
    document.body.appendChild(panel);
    debugPanel = logList;

    readDebugHistory().forEach((entry) => {
      renderDebugEntry(debugPanel, entry);
    });

    return debugPanel;
  }

  function renderDebugEntry(container, entryData) {
    if (!container) {
      return;
    }

    const entry = document.createElement("div");
    entry.style.cssText = [
      "padding:8px 10px",
      "border-radius:8px",
      "background:rgba(255,255,255,0.06)",
      entryData.level === "error" ? "color:#ffb4b4" : entryData.level === "warn" ? "color:#ffd68a" : "color:#e8e8e8",
    ].join(";");
    entry.textContent = entryData.payload
      ? `[GM ${entryData.stamp}] ${entryData.message}\n${entryData.payload}`
      : `[GM ${entryData.stamp}] ${entryData.message}`;
    container.appendChild(entry);
    return entry;
  }

  function writeDebug(level, message, data) {
    if (!DEBUG_ENABLED) {
      return;
    }

    const stamp = new Date().toISOString();
    const line = `[GM ${stamp}] ${message}`;
    const payload = serializeDebugData(data);
    const method = console[level] || console.log;

    if (payload) {
      method.call(console, line, data);
    } else {
      method.call(console, line);
    }

    if (!DEBUG_ENABLED || !document.body) {
      return;
    }

    const container = ensureDebugPanel();
    if (!container) {
      return;
    }

    const entryData = { level, message, payload, stamp };
    renderDebugEntry(container, entryData);
    const history = readDebugHistory();
    history.push(entryData);
    saveDebugHistory(history);
    while (container.childElementCount > 40) {
      container.removeChild(container.firstElementChild);
    }
    container.parentElement.scrollTop = container.parentElement.scrollHeight;
  }

  function logInfo(message, data) {
    writeDebug("info", message, data);
  }

  function logWarn(message, data) {
    writeDebug("warn", message, data);
  }

  function logError(message, data) {
    writeDebug("error", message, data);
  }

  function buildUrl(pageName) {
    return new URL(pageName, window.location.href).toString();
  }

  function authRoot() {
    return CONFIG.keycloak.realmUrl.replace(/\/$/, "");
  }

  function authEndpoint(name) {
    return `${authRoot()}/protocol/openid-connect/${name}`;
  }

  function apiUrl(path = "") {
    const base = CONFIG.apiBaseUrl.replace(/\/$/, "");
    const suffix = path.startsWith("/") ? path : `/${path}`;
    return `${base}${suffix}`;
  }

  function randomString(length = 96) {
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
    const values = crypto.getRandomValues(new Uint8Array(length));
    return Array.from(values, (value) => alphabet[value % alphabet.length]).join("");
  }

  function base64UrlEncode(bytes) {
    let binary = "";
    bytes.forEach((byte) => {
      binary += String.fromCharCode(byte);
    });
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  async function sha256(input) {
    const hash = await crypto.subtle.digest("SHA-256", textEncoder.encode(input));
    return base64UrlEncode(new Uint8Array(hash));
  }

  function decodeBase64UrlJson(segment) {
    const normalized = segment.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    return JSON.parse(textDecoder.decode(bytes));
  }

  function parseJwt(token) {
    const segments = token.split(".");
    if (segments.length < 2) {
      throw new Error("JWT inválido.");
    }

    return decodeBase64UrlJson(segments[1]);
  }

  function extractRoles(claims) {
    const roles = [];
    const realmRoles = claims?.realm_access?.roles || [];
    roles.push(...realmRoles);

    const resourceAccess = claims?.resource_access || {};
    const clientRoles = resourceAccess?.[CONFIG.keycloak.clientId]?.roles || [];
    roles.push(...clientRoles);

    return Array.from(new Set(roles));
  }

  function readSession() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_error) {
      logWarn("Falha ao ler sessionStorage da sessão.");
      return null;
    }
  }

  function saveSession(session) {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    logInfo("Sessão salva no navegador.", summarizeSession(session));
  }

  function clearSession() {
    sessionStorage.removeItem(STORAGE_KEY);
  }

  function clearStorageWithPrefix(storage) {
    try {
      const keys = [];
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (key && key.startsWith(APP_STORAGE_PREFIX)) {
          keys.push(key);
        }
      }

      keys.forEach((key) => storage.removeItem(key));
    } catch (_error) {
      // Ignore storage access issues.
    }
  }

  function clearAppState() {
    logInfo("Limpando estado local do app.");
    clearStorageWithPrefix(sessionStorage);
    clearStorageWithPrefix(localStorage);
  }

  function normalizeRole(role) {
    return String(role || "").trim().toUpperCase().replace(/_/g, "-");
  }

  function hasRequiredRole(session) {
    const requiredRole = normalizeRole(CONFIG.keycloak.requiredRole);
    return Boolean(session?.roles?.some((role) => normalizeRole(role) === requiredRole));
  }

  function redirectToLogin(reason = "", detail = "") {
    logWarn("Redirecionando para login.", { reason, currentUrl: window.location.href });
    const loginUrl = new URL(buildUrl(CONFIG.pages.login));
    if (reason) {
      loginUrl.searchParams.set("reason", reason);
    }
    if (detail) {
      loginUrl.searchParams.set("detail", detail);
    }
    window.location.replace(loginUrl.toString());
  }

  function pkceKey(state) {
    return `${PKCE_KEY_PREFIX}:${state}`;
  }

  function savePkce(state, payload) {
    sessionStorage.setItem(pkceKey(state), JSON.stringify(payload));
  }

  function readPkce(state) {
    try {
      const raw = sessionStorage.getItem(pkceKey(state));
      return raw ? JSON.parse(raw) : null;
    } catch (_error) {
      return null;
    }
  }

  function clearPkce(state) {
    sessionStorage.removeItem(pkceKey(state));
  }

  function formatDuration(milliseconds) {
    if (!Number.isFinite(milliseconds) || milliseconds <= 0) {
      return "0s";
    }

    const totalSeconds = Math.ceil(milliseconds / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;

    if (minutes > 0) {
      return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
    }

    return `${seconds}s`;
  }

  const CONCILIADOR_ALLOWED_EXTENSIONS = new Set(["pdf", "csv", "xls", "xlsx"]);

  function formatConciliadorFileSize(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return "0 B";
    }

    const units = ["B", "KB", "MB", "GB"];
    let size = bytes;
    let unitIndex = 0;

    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex += 1;
    }

    const rounded = unitIndex === 0 || size >= 10 ? Math.round(size) : Number(size.toFixed(1));
    return `${rounded} ${units[unitIndex]}`;
  }

  function getConciliadorFileExtension(fileName) {
    const match = /\.([^.]+)$/.exec(String(fileName || "").trim());
    return match ? match[1].toLowerCase() : "";
  }

  function updateText(element, value) {
    if (element) {
      element.textContent = value;
    }
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function normalizePanelView(view) {
    if (view === "clientes" || view === "conciliador" || view === "perfis" || view === "contabilidade") {
      return view;
    }
    return "dashboard";
  }

  function getInitialPanelView() {
    const params = new URLSearchParams(window.location.search);
    return normalizePanelView(params.get("view") || document.body.dataset.initialView || "dashboard");
  }

  function setPanelView(view, options = {}) {
    const target = normalizePanelView(view);
    const updateUrl = options.updateUrl !== false;

    document.querySelectorAll("[data-panel-view-section]").forEach((section) => {
      section.classList.toggle("is-active", section.dataset.panelViewSection === target);
    });

    document.querySelectorAll("[data-panel-view-target]").forEach((button) => {
      const buttonView = normalizePanelView(button.dataset.panelViewTarget);
      button.classList.toggle("active", buttonView === target);
      button.setAttribute("aria-pressed", buttonView === target ? "true" : "false");
    });

    document.body.dataset.currentPanelView = target;
    document.body.setAttribute("data-current-panel-view", target);

    if (updateUrl) {
      const url = new URL(CONFIG.pages.panel, window.location.origin);
      if (target !== "dashboard") {
        url.searchParams.set("view", target);
      }
      window.history.replaceState({}, document.title, `${url.pathname}${url.search}${url.hash}`);
    }

    return target;
  }

  // ── Combobox reutilizável ─────────────────────────────────────────────────
  // setupCombobox(inputEl, getList, options)
  //   getList: array ou function() que retorna array com itens { codigo, nome }
  //   options.getLabel(item): texto exibido no dropdown
  //   options.getValue(item): valor salvo no input
  function setupCombobox(inputEl, getList, options = {}) {
    if (!inputEl) return;
    const getLabel = options.getLabel || ((item) => `${item.codigo} \u2014 ${item.nome}`);
    const getValue = options.getValue || ((item) => String(item.codigo));
    const maxResults = options.maxResults || 60;

    const wrap = document.createElement("div");
    wrap.className = "combobox-wrap";
    inputEl.parentNode.insertBefore(wrap, inputEl);
    wrap.appendChild(inputEl);

    const dropdown = document.createElement("div");
    dropdown.className = "combobox-dropdown";
    dropdown.hidden = true;
    wrap.appendChild(dropdown);

    let activeIdx = -1;

    function getListNow() {
      return typeof getList === "function" ? getList() : (Array.isArray(getList) ? getList : []);
    }

    function filterList(q) {
      if (!q) return [];
      const norm = q.toLowerCase().trim();
      const normNoSep = norm.replace(/[.\-\s]/g, "");
      return getListNow()
        .filter((item) => {
          const code = String(item.codigo).toLowerCase();
          const codNoSep = code.replace(/[.\-\s]/g, "");
          const name = String(item.nome).toLowerCase();
          return codNoSep.startsWith(normNoSep) || code.startsWith(norm) || name.includes(norm);
        })
        .slice(0, maxResults);
    }

    function renderDropdown(items) {
      activeIdx = -1;
      if (!items.length) { dropdown.hidden = true; return; }
      dropdown.innerHTML = items
        .map((item, i) =>
          `<div class="combobox-option" data-idx="${i}" data-val="${escapeHtml(getValue(item))}">${escapeHtml(getLabel(item))}</div>`
        )
        .join("");
      dropdown.hidden = false;
      dropdown.querySelectorAll(".combobox-option").forEach((opt) => {
        opt.addEventListener("mousedown", (e) => {
          e.preventDefault();
          inputEl.value = opt.dataset.val;
          dropdown.hidden = true;
          inputEl.dispatchEvent(new Event("input", { bubbles: true }));
        });
      });
    }

    function setActive(idx) {
      const opts = dropdown.querySelectorAll(".combobox-option");
      opts.forEach((o) => o.classList.remove("is-active"));
      activeIdx = (idx >= 0 && idx < opts.length) ? idx : -1;
      if (activeIdx >= 0) {
        opts[activeIdx].classList.add("is-active");
        opts[activeIdx].scrollIntoView({ block: "nearest" });
      }
    }

    inputEl.addEventListener("input", () => {
      renderDropdown(filterList(inputEl.value.trim()));
    });

    inputEl.addEventListener("focus", () => {
      const q = inputEl.value.trim();
      const list = getListNow();
      if (q) {
        renderDropdown(filterList(q));
      } else if (list.length) {
        renderDropdown(list.slice(0, maxResults));
      }
    });

    inputEl.addEventListener("blur", () => {
      setTimeout(() => { dropdown.hidden = true; }, 160);
    });

    inputEl.addEventListener("keydown", (e) => {
      if (dropdown.hidden) return;
      const opts = dropdown.querySelectorAll(".combobox-option");
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive(Math.min(activeIdx + 1, opts.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(Math.max(activeIdx - 1, 0));
      } else if (e.key === "Enter") {
        if (activeIdx >= 0) {
          e.preventDefault();
          inputEl.value = opts[activeIdx].dataset.val;
          dropdown.hidden = true;
          inputEl.dispatchEvent(new Event("input", { bubbles: true }));
        }
      } else if (e.key === "Escape") {
        dropdown.hidden = true;
      }
    });
  }

  function setupPanelNavigation() {
    document.querySelectorAll("[data-panel-view-target]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = normalizePanelView(button.dataset.panelViewTarget);
        logInfo("Troca de view do painel.", { view: target });
        setPanelView(target);
      });
    });
  }

  function normalizeSearchTerm(value) {
    return String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
  }

  function formatClientCount(count) {
    return `${count} cliente${count === 1 ? "" : "s"}`;
  }

  function formatCurrencyBRL(value) {
    const amount = Number(value || 0);
    return new Intl.NumberFormat("pt-BR", {
      style: "currency",
      currency: "BRL",
    }).format(Number.isFinite(amount) ? amount : 0);
  }

  function getTodayDateValue() {
    const now = new Date();
    const localDate = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
    return localDate.toISOString().slice(0, 10);
  }

  function getTodayMonthValue() {
    return getTodayDateValue().slice(0, 7);
  }

  function formatConciliadorReference(value) {
    const match = /^([0-9]{4})-([0-9]{2})$/.exec(String(value || ""));
    if (!match) {
      return "";
    }

    const monthLabels = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
    const monthIndex = Number(match[2]) - 1;
    const month = monthLabels[monthIndex];
    if (!month) {
      return "";
    }

    return `${month}-${match[1]}`;
  }

  function setupConciliadorReferenceControl() {
    const input = document.getElementById("conciliador-referencia");
    const hint = document.querySelector("[data-conciliador-reference-hint]");

    if (!input) {
      return;
    }

    if (!input.value) {
      input.value = getTodayMonthValue();
    }

    const syncReference = () => {
      const label = formatConciliadorReference(input.value) || "selecione mês e ano";
      input.dataset.referenceLabel = label;
      if (hint) {
        hint.textContent = label;
      }
    };

    syncReference();
    input.addEventListener("change", syncReference);
    input.addEventListener("input", syncReference);
  }

  function clientActionIcon(kind) {
    const icons = {
      view: `
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
          <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"/>
        </svg>
      `,
      accounts: `
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M4 10.5 12 6l8 4.5v1.2H4v-1.2Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
          <path d="M6 11.7v6.3M10 11.7v6.3M14 11.7v6.3M18 11.7v6.3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
          <path d="M3.5 19h17" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
        </svg>
      `,
      edit: `
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M4 20h4l10.5-10.5a1.8 1.8 0 0 0 0-2.5l-1.5-1.5a1.8 1.8 0 0 0-2.5 0L4 16v4Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
          <path d="m13.5 6.5 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
        </svg>
      `,
      delete: `
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M4 7h16" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
          <path d="M9 7V5.8A1.8 1.8 0 0 1 10.8 4h2.4A1.8 1.8 0 0 1 15 5.8V7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
          <path d="M7 7l1 12a1.8 1.8 0 0 0 1.8 1.6h4.4A1.8 1.8 0 0 0 16 19L17 7" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
          <path d="M10 11v5M14 11v5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
        </svg>
      `,
    };

    return icons[kind] || "";
  }

  function clientMetaIcon(kind) {
    const icons = {
      document: `
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M7 4.5h7l3.5 3.5V19A1.5 1.5 0 0 1 16 20.5H7A1.5 1.5 0 0 1 5.5 19V6A1.5 1.5 0 0 1 7 4.5Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
          <path d="M14 4.5V8h3.5" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
          <path d="M8.2 11h7.6M8.2 14h7.6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
        </svg>
      `,
      code: `
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M9 7 4 12l5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M15 7l5 5-5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="m13 5-2 14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
        </svg>
      `,
    };

    return icons[kind] || "";
  }

  function formatClientCode(value) {
    const raw = String(value || "").trim();
    if (!raw) {
      return "";
    }

    return raw.replace(/^CLI[-_]?/i, "");
  }

  function maskDocument(value) {
    const raw = String(value || "").trim();
    const digits = String(value || "").replace(/\D/g, "");
    if (!digits) {
      return raw;
    }

    if (digits.length === 11) {
      return `${digits.slice(0, 3)}.${digits.slice(3, 6)}.${digits.slice(6, 9)}-${digits.slice(9)}`;
    }

    if (digits.length === 14) {
      return `${digits.slice(0, 2)}.${digits.slice(2, 5)}.${digits.slice(5, 8)}/${digits.slice(8, 12)}-${digits.slice(12)}`;
    }

    return raw || digits;
  }

  function maskPhone(value) {
    const raw = String(value || "").trim();
    const digits = String(value || "").replace(/\D/g, "");
    if (!digits) {
      return raw;
    }

    if (digits.length === 10) {
      return `(${digits.slice(0, 2)}) ${digits.slice(2, 6)}-${digits.slice(6)}`;
    }

    if (digits.length === 11) {
      return `(${digits.slice(0, 2)}) ${digits.slice(2, 7)}-${digits.slice(7)}`;
    }

    return raw || digits;
  }

  function setClientField(element, kind, value) {
    if (element) {
      element.innerHTML = `${clientMetaIcon(kind)}<span>${escapeHtml(value)}</span>`;
    }
  }

  function normalizeClientStatus(status) {
    const normalized = normalizeSearchTerm(status);
    if (normalized.includes("analise")) {
      return "analise";
    }

    if (normalized.includes("paus")) {
      return "pausado";
    }

    if (normalized.includes("inativ")) {
      return "inativo";
    }

    return "ativo";
  }

  const CLIENT_STATUS_FILTERS = {
    all: "Cadastrados",
    ativo: "Ativos",
    inativo: "Inativos",
    pausado: "Pausados",
    analise: "Em Análise",
  };

  function getClientStatusFilterLabel(filterKey) {
    return CLIENT_STATUS_FILTERS[filterKey] || CLIENT_STATUS_FILTERS.all;
  }

  function formatClientStatus(status) {
    const statusKey = normalizeClientStatus(status);
    if (statusKey === "analise") {
      return "Em análise";
    }

    if (statusKey === "pausado") {
      return "Pausado";
    }

    if (statusKey === "inativo") {
      return "Inativo";
    }

    return "Ativo";
  }

  function applyClientStatusPill(pill, status) {
    if (!pill) {
      return;
    }

    const statusKey = normalizeClientStatus(status);
    pill.className = `pill status-${statusKey}`;
    pill.dataset.statusKey = statusKey;
    pill.textContent = formatClientStatus(status);
  }

  function readClientCardData(card) {
    return {
      name: card.dataset.clientName || "",
      document: card.dataset.clientDocument || "",
      email: card.dataset.clientEmail || "",
      phone: card.dataset.clientPhone || "",
      status: card.dataset.clientStatus || "Ativo",
    };
  }

  function normalizeClientData(data) {
    return {
      name: String(data?.name || "").trim(),
      document: String(data?.document || "").trim(),
      email: String(data?.email || "").trim(),
      phone: String(data?.phone || "").trim(),
      status: String(data?.status || "Ativo").trim() || "Ativo",
    };
  }

  function updateClientCard(card, data) {
    const normalized = normalizeClientData(data);
    const statusKey = normalizeClientStatus(normalized.status);
    const searchIndex = normalizeSearchTerm([
      normalized.name,
      normalized.document,
      normalized.email,
      normalized.phone,
      normalized.status,
    ].join(" "));

    card.dataset.clientName = normalized.name;
    card.dataset.clientDocument = normalized.document;
    card.dataset.clientEmail = normalized.email;
    card.dataset.clientPhone = normalized.phone;
    card.dataset.clientStatus = normalized.status;
    card.dataset.clientStatusKey = statusKey;
    card.dataset.clientSearchIndex = searchIndex;

    updateText(card.querySelector("[data-client-name-label]"), normalized.name);
    updateText(card.querySelector("[data-client-document-label]"), `CNPJ ${normalized.document}`);
    updateText(card.querySelector("[data-client-email-label]"), normalized.email);
    updateText(card.querySelector("[data-client-phone-label]"), normalized.phone);
    applyClientStatusPill(card.querySelector("[data-client-status-pill]"), normalized.status);
  }

  function buildClientCard(data) {
    const card = document.createElement("article");
    card.className = "client-item";
    card.dataset.clientItem = "true";
    card.innerHTML = `
      <div class="client-main">
        <div class="client-title-row">
          <strong data-client-name-label></strong>
          <span class="pill" data-client-status-pill></span>
        </div>
        <div class="client-meta">
          <span data-client-document-label></span>
          <span data-client-email-label></span>
          <span data-client-phone-label></span>
        </div>
      </div>
      <div class="client-actions">
        <button class="client-action client-action-edit" type="button" data-client-edit>Editar</button>
        <button class="client-action client-action-delete" type="button" data-client-delete>Excluir</button>
      </div>
    `;
    updateClientCard(card, data);
    return card;
  }

  function updateClientOverview(items, labels) {
    const counts = {
      total: items.length,
      ativo: 0,
      analise: 0,
      pausado: 0,
      inativo: 0,
    };

    items.forEach((item) => {
      const statusKey = normalizeClientStatus(item.dataset.clientStatus);
      if (Object.prototype.hasOwnProperty.call(counts, statusKey)) {
        counts[statusKey] += 1;
      } else {
        counts.ativo += 1;
      }
    });

    updateText(labels.total, counts.total);
    updateText(labels.active, counts.ativo);
    updateText(labels.review, counts.analise);
    updateText(labels.paused, counts.pausado);
  }

  function setupClientsSection() {
    const panel = document.querySelector('[data-panel-view-section="clientes"]');
    if (!panel) {
      return;
    }

    const modal = panel.querySelector("[data-client-modal]");
    const modalCard = panel.querySelector("[data-client-modal-card]");
    const form = panel.querySelector("[data-client-form]");
    const modalEyebrow = panel.querySelector(".client-modal-eyebrow");
    const modalTitle = panel.querySelector("[data-client-modal-title]");
    const modalDescription = panel.querySelector("[data-client-modal-description]");
    const modalModeLabel = panel.querySelector("[data-client-modal-mode-label]");
    const modalStatus = panel.querySelector("[data-client-modal-status]");
    const modalQuickActions = panel.querySelector("[data-client-modal-quick-actions]");
    const modalQuickEdit = panel.querySelector("[data-client-modal-quick-edit]");
    const modalQuickToggle = panel.querySelector("[data-client-modal-quick-toggle]");
    const submitButton = panel.querySelector("[data-client-submit]");
    const list = panel.querySelector("[data-client-list]");
    const searchInput = panel.querySelector("[data-client-search]");
    const filterRoot = panel.querySelector("[data-client-filter]");
    const filterToggle = panel.querySelector("[data-client-filter-toggle]");
    const filterMenu = panel.querySelector("[data-client-filter-menu]");
    const filterLabel = panel.querySelector("[data-client-filter-label]");
    const filterOptions = Array.from(panel.querySelectorAll("[data-client-filter-option]"));
    const addButtons = panel.querySelectorAll("[data-client-modal-open]");
    const closeButtons = panel.querySelectorAll("[data-client-modal-close]");
    const emptyState = panel.querySelector("[data-client-empty]");
    const countLabel = panel.querySelector("[data-client-count]");
    const summaryTotal = panel.querySelector("[data-client-summary-total]");
    const summaryActive = panel.querySelector("[data-client-summary-active]");
    const summaryReview = panel.querySelector("[data-client-summary-review]");
    const summaryPaused = panel.querySelector("[data-client-summary-paused]");

    if (!modal || !form || !list) {
      return;
    }

    const fields = {
      name: form.elements.namedItem("name"),
      document: form.elements.namedItem("document"),
      email: form.elements.namedItem("email"),
      phone: form.elements.namedItem("phone"),
      status: form.elements.namedItem("status"),
    };

    let activeClientItem = null;

    function resetModalState() {
      activeClientItem = null;
      form.reset();

      if (modalTitle) {
        modalTitle.textContent = "Novo cliente";
      }

      if (submitButton) {
        submitButton.textContent = "Salvar cliente";
      }
    }

    function openModal(clientItem = null) {
      activeClientItem = clientItem;
      form.reset();

      if (clientItem) {
        const data = readClientCardData(clientItem);

        if (modalTitle) {
          modalTitle.textContent = "Editar cliente";
        }

        if (submitButton) {
          submitButton.textContent = "Salvar alterações";
        }

        if (fields.name) fields.name.value = data.name;
        if (fields.document) fields.document.value = data.document;
        if (fields.email) fields.email.value = data.email;
        if (fields.phone) fields.phone.value = data.phone;
        if (fields.status) fields.status.value = data.status;
      } else {
        if (modalTitle) {
          modalTitle.textContent = "Novo cliente";
        }

        if (submitButton) {
          submitButton.textContent = "Salvar cliente";
        }
      }

      if (typeof modal.showModal === "function") {
        if (!modal.open) {
          modal.showModal();
        }
      } else {
        modal.setAttribute("open", "open");
      }

      window.requestAnimationFrame(() => {
        if (fields.name && typeof fields.name.focus === "function") {
          fields.name.focus();
        }
      });
    }

    function closeModal() {
      if (typeof modal.close === "function") {
        if (modal.open) {
          modal.close();
        }
      } else {
        modal.removeAttribute("open");
      }

      resetModalState();
    }

    function refreshList() {
      const query = normalizeSearchTerm(searchInput?.value || "");
      const items = Array.from(list.querySelectorAll("[data-client-item]"));
      let visibleCount = 0;

      updateClientOverview(items, {
        total: summaryTotal,
        active: summaryActive,
        review: summaryReview,
        paused: summaryPaused,
      });

      items.forEach((item) => {
        const searchable = item.dataset.clientSearchIndex || normalizeSearchTerm(item.textContent);
        const visible = !query || searchable.includes(query);
        item.hidden = !visible;

        if (visible) {
          visibleCount += 1;
        }
      });

      const totalCount = items.length;

      if (countLabel) {
        countLabel.textContent = query && totalCount > 0
          ? `Mostrando ${visibleCount} de ${totalCount} clientes`
          : formatClientCount(totalCount);
      }

      if (emptyState) {
        if (totalCount === 0) {
          emptyState.hidden = false;
          emptyState.textContent = "Nenhum cliente cadastrado ainda. Clique em 'Novo cliente' para começar.";
        } else if (visibleCount === 0) {
          emptyState.hidden = false;
          emptyState.textContent = "Nenhum cliente encontrado para essa pesquisa.";
        } else {
          emptyState.hidden = true;
        }
      }
    }

    addButtons.forEach((button) => {
      button.addEventListener("click", () => {
        openModal();
      });
    });

    closeButtons.forEach((button) => {
      button.addEventListener("click", closeModal);
    });

    if (searchInput) {
      searchInput.addEventListener("input", refreshList);
    }

    modal.addEventListener("close", resetModalState);
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        closeModal();
      }
    });

    list.addEventListener("click", (event) => {
      const editButton = event.target.closest("[data-client-edit]");
      const deleteButton = event.target.closest("[data-client-delete]");

      if (editButton) {
        const clientItem = editButton.closest("[data-client-item]");
        if (clientItem) {
          openModal(clientItem);
        }
        return;
      }

      if (deleteButton) {
        const clientItem = deleteButton.closest("[data-client-item]");
        if (!clientItem) {
          return;
        }

        const clientName = clientItem.dataset.clientName || "este cliente";
        if (window.confirm(`Excluir ${clientName}?`)) {
          if (activeClientItem === clientItem) {
            closeModal();
          }

          clientItem.remove();
          refreshList();
        }
      }
    });

    form.addEventListener("submit", (event) => {
      event.preventDefault();

      const payload = {
        name: fields.name?.value || "",
        document: fields.document?.value || "",
        email: fields.email?.value || "",
        phone: fields.phone?.value || "",
        status: fields.status?.value || "Ativo",
      };

      if (activeClientItem) {
        updateClientCard(activeClientItem, payload);
      } else {
        list.prepend(buildClientCard(payload));
      }

      if (searchInput) {
        searchInput.value = "";
      }

      closeModal();
      refreshList();
    });

    refreshList();
  }

  function normalizeClientRecord(record) {
    const situacao = String(record?.situacao || "ATIVO").trim() || "ATIVO";
    const situacaoLabel = String(record?.situacao_label || situacao).trim();
    const statusKey = normalizeClientStatus(situacaoLabel || situacao);

    return {
      id: String(record?.id || "").trim(),
      codigo: String(record?.codigo || "").trim(),
      nome: String(record?.nome || "").trim(),
      cpf_cnpj: String(record?.cpf_cnpj || "").trim(),
      email: String(record?.email || "").trim(),
      ie: String(record?.ie || "").trim(),
      telefone: String(record?.telefone || "").trim(),
      conta_corrente: String(record?.conta_corrente || "").trim(),
      conta_contabil: String(record?.conta_contabil || "").trim(),
      data_inicio: String(record?.data_inicio || "").trim(),
      situacao,
      situacao_label: situacaoLabel,
      statusKey,
      searchIndex: normalizeSearchTerm([
        record?.codigo,
        record?.nome,
        record?.cpf_cnpj,
        record?.email,
        record?.ie,
        record?.telefone,
        record?.conta_corrente,
        record?.conta_contabil,
        record?.data_inicio,
        situacaoLabel,
        situacao,
      ].join(" ")),
    };
  }

  function createClientCard(record) {
    const client = normalizeClientRecord(record);
    const card = document.createElement("article");
    card.className = "client-item";
    card.dataset.clientItem = "true";
    card.dataset.clientId = client.id;
    card.dataset.clientCodigo = client.codigo;
    card.dataset.clientNome = client.nome;
    card.dataset.clientCpfCnpj = client.cpf_cnpj;
    card.dataset.clientIe = client.ie;
    card.dataset.clientTelefone = client.telefone;
    card.dataset.clientDataInicio = client.data_inicio;
    card.dataset.clientSituacao = client.situacao;
    card.dataset.clientStatusKey = client.statusKey || normalizeClientStatus(client.situacao_label || client.situacao);
    card.dataset.clientSearchIndex = client.searchIndex;
    card.innerHTML = `
      <div class="client-main">
        <div class="client-title-row">
          <strong data-client-name-label></strong>
          <span class="pill" data-client-status-pill></span>
        </div>
        <div class="client-meta">
          <span data-client-cpf-cnpj-label></span>
        </div>
      </div>
      <div class="client-actions">
        <div class="client-actions-row">
          <button class="client-action client-action-view" type="button" data-client-view aria-label="Ver cliente" title="Ver cliente">${clientActionIcon("view")}</button>
          <button class="client-action client-action-accounts" type="button" data-client-accounts aria-label="Bancos e contas" title="Bancos e contas">${clientActionIcon("accounts")}</button>
          <button class="client-action client-action-edit" type="button" data-client-edit aria-label="Editar cliente" title="Editar cliente">${clientActionIcon("edit")}</button>
          <button class="client-action client-action-delete" type="button" data-client-delete aria-label="Excluir cliente" title="Excluir cliente">${clientActionIcon("delete")}</button>
        </div>
      </div>
    `;

    updateText(card.querySelector("[data-client-name-label]"), client.nome || "Sem nome");
    const formattedDoc = maskDocument(client.cpf_cnpj) || "não informado";
    const clientCode = formatClientCode(client.codigo) || "";
    const docLabel = card.querySelector("[data-client-cpf-cnpj-label]");
    if (clientCode) {
      docLabel.innerHTML = `<span>${formattedDoc}</span> <span>Código: ${clientCode}</span>`;
    } else {
      updateText(docLabel, formattedDoc);
    }
    applyClientStatusPill(card.querySelector("[data-client-status-pill]"), client.situacao_label || client.situacao);

    return card;
  }

  function updateClientOverviewSummary(clients, labels) {
    const counts = {
      total: clients.length,
      ativo: 0,
      analise: 0,
      pausado: 0,
      inativo: 0,
    };

    clients.forEach((client) => {
      const statusKey = normalizeClientStatus(client.situacao_label || client.situacao);
      if (Object.prototype.hasOwnProperty.call(counts, statusKey)) {
        counts[statusKey] += 1;
      }
    });

    updateText(labels.total, counts.total);
    updateText(labels.active, counts.ativo);
    updateText(labels.review, counts.analise);
    updateText(labels.paused, counts.pausado);
  }

  function buildApiHeaders(session, extraHeaders = {}) {
    return {
      Authorization: `Bearer ${session.accessToken}`,
      ...extraHeaders,
    };
  }

  function extractApiErrorMessage(payload) {
    if (typeof payload === "string") {
      return payload;
    }

    if (!payload || typeof payload !== "object") {
      return "";
    }

    if (payload.detail || payload.message) {
      return payload.detail || payload.message;
    }

    const messages = Object.values(payload)
      .flatMap((value) => (Array.isArray(value) ? value : [value]))
      .map((value) => String(value))
      .filter(Boolean);

    return messages.join(" ") || JSON.stringify(payload);
  }

  async function apiRequest(session, path, options = {}) {
    const body = options.body;
    const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
    const response = await fetch(apiUrl(path), {
      method: options.method || "GET",
      headers: {
        ...buildApiHeaders(session, body && !isFormData ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
      body: isFormData ? body : body ? JSON.stringify(body) : undefined,
    });

    if (response.status === 204) {
      return null;
    }

    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : await response.text();

    if (!response.ok) {
      throw new Error(extractApiErrorMessage(payload) || `Falha na requisição (${response.status}).`);
    }

    return payload;
  }

  function getClientFormData(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    return {
      codigo: String(data.codigo || "").trim(),
      nome: String(data.nome || "").trim(),
      cpf_cnpj: String(data.cpf_cnpj || "").trim(),
      email: String(data.email || "").trim(),
      ie: String(data.ie || "").trim(),
      telefone: String(data.telefone || "").trim(),
      conta_corrente: String(data.conta_corrente || "").trim(),
      conta_contabil: String(data.conta_contabil || "").trim(),
      data_inicio: String(data.data_inicio || "").trim(),
      situacao: String(data.situacao || "ATIVO").trim() || "ATIVO",
    };
  }

  function setClientFormData(form, record) {
    const client = normalizeClientRecord(record);
    if (form.elements.codigo) form.elements.codigo.value = client.codigo;
    if (form.elements.nome) form.elements.nome.value = client.nome;
    if (form.elements.cpf_cnpj) form.elements.cpf_cnpj.value = client.cpf_cnpj;
    if (form.elements.email) form.elements.email.value = client.email;
    if (form.elements.ie) form.elements.ie.value = client.ie;
    if (form.elements.telefone) form.elements.telefone.value = client.telefone;
    if (form.elements.conta_corrente) form.elements.conta_corrente.value = client.conta_corrente;
    if (form.elements.conta_contabil) form.elements.conta_contabil.value = client.conta_contabil;
    if (form.elements.data_inicio) form.elements.data_inicio.value = client.data_inicio || getTodayDateValue();
    const situacaoValue = (record?.situacao || record?.situacao_label || "ATIVO").toUpperCase().trim().replace(" ", "_");
    if (form.elements.situacao) {
      const opts = form.elements.situacao.options;
      const found = Array.from(opts).some(opt => opt.value === situacaoValue);
      form.elements.situacao.value = found ? situacaoValue : "ATIVO";
    }
  }

  async function loadClientsFromApi(session) {
    const payload = await apiRequest(session, "/clientes/");
    const records = Array.isArray(payload) ? payload : (payload?.results || []);
    return records.map(normalizeClientRecord);
  }

  async function loadOfficesFromApi(session) {
    const payload = await apiRequest(session, "/escritorios/");
    const records = Array.isArray(payload) ? payload : (payload?.results || []);
    return records.map((record) => ({
      id: String(record?.id || "").trim(),
      nome: String(record?.nome || "").trim(),
      cnpj: String(record?.cnpj || "").trim(),
    }));
  }

  function getConciliadorCompanySelect() {
    return document.getElementById("conciliador-empresa");
  }

  function getConciliadorOfficeSelect() {
    return document.getElementById("conciliador-escritorio");
  }

  function setConciliadorCompanyPlaceholder(select, message) {
    select.replaceChildren();

    const option = document.createElement("option");
    option.value = "";
    option.disabled = true;
    option.selected = true;
    option.textContent = message;

    select.appendChild(option);
    return option;
  }

  function setConciliadorOfficePlaceholder(select, message) {
    select.replaceChildren();

    const option = document.createElement("option");
    option.value = "";
    option.disabled = true;
    option.selected = true;
    option.textContent = message;

    select.appendChild(option);
    return option;
  }

  async function populateConciliadorCompanySelect(session) {
    const select = getConciliadorCompanySelect();
    if (!select) {
      return;
    }

    const placeholder = select.dataset.placeholder || "Selecione uma empresa...";
    const hasSeededOptions = select.options.length > 1;

    if (!hasSeededOptions) {
      setConciliadorCompanyPlaceholder(select, "Carregando clientes...");
    }

    try {
      const clients = await loadClientsFromApi(session);
      if (!clients.length) {
        if (!hasSeededOptions) {
          setConciliadorCompanyPlaceholder(select, "Nenhum cliente cadastrado");
        }
        return;
      }

      select.replaceChildren();

      const defaultOption = document.createElement("option");
      defaultOption.value = "";
      defaultOption.disabled = true;
      defaultOption.selected = true;
      defaultOption.textContent = clients.length > 0 ? placeholder : "Nenhum cliente cadastrado";
      select.appendChild(defaultOption);

      clients.forEach((client) => {
        const option = document.createElement("option");
        option.value = client.id || client.codigo || client.nome;
        option.textContent = client.nome || client.codigo || "Cliente sem nome";
        option.dataset.clientId = client.id || "";
        option.dataset.clientCodigo = client.codigo || "";
        option.dataset.clientNome = client.nome || "";
        select.appendChild(option);
      });

      select.dataset.conciliadorState = clients.length > 0 ? "ready" : "empty";
    } catch (error) {
      logWarn("Falha ao carregar clientes para o campo Empresa.", error);
      if (!hasSeededOptions) {
        setConciliadorCompanyPlaceholder(select, "Não foi possível carregar clientes");
      }
      select.dataset.conciliadorState = "error";
    }
  }

  async function populateConciliadorOfficeSelect(session) {
    const select = getConciliadorOfficeSelect();
    if (!select) {
      return;
    }

    const placeholder = select.dataset.placeholder || "Selecione um escritório...";
    const hasSeededOptions = select.options.length > 1;

    if (!hasSeededOptions) {
      setConciliadorOfficePlaceholder(select, "Carregando escritórios...");
    }

    try {
      const offices = await loadOfficesFromApi(session);
      if (!offices.length) {
        if (!hasSeededOptions) {
          setConciliadorOfficePlaceholder(select, "Nenhum escritório cadastrado");
        }
        return;
      }

      select.replaceChildren();

      const defaultOption = document.createElement("option");
      defaultOption.value = "";
      defaultOption.disabled = true;
      defaultOption.selected = true;
      defaultOption.textContent = offices.length > 0 ? placeholder : "Nenhum escritório cadastrado";
      select.appendChild(defaultOption);

      offices.forEach((office) => {
        const option = document.createElement("option");
        option.value = office.id || office.nome;
        const cnpj = office.cnpj ? maskDocument(office.cnpj) : "";
        option.textContent = office.nome ? (cnpj ? `${office.nome} - ${cnpj}` : office.nome) : "Escritório sem nome";
        option.dataset.officeId = office.id || "";
        option.dataset.officeNome = office.nome || "";
        option.dataset.officeCnpj = office.cnpj || "";
        select.appendChild(option);
      });

      select.dataset.conciliadorState = offices.length > 0 ? "ready" : "empty";
    } catch (error) {
      logWarn("Falha ao carregar escritórios para o campo Escritório.", error);
      if (!hasSeededOptions) {
        setConciliadorOfficePlaceholder(select, "Não foi possível carregar escritórios");
      }
      select.dataset.conciliadorState = "error";
    }
  }

  function setupConciliadorWorkspace(session) {
    const panel = document.querySelector('[data-panel-view-section="conciliador"]');
    if (!panel) {
      return;
    }

    const openButton = panel.querySelector("[data-conciliador-import-open]");
    const contextFilterButton = panel.querySelector("[data-conciliador-context-filter]");
    const workspace = panel.querySelector("[data-conciliador-workspace]");
    const statusLabel = panel.querySelector("[data-conciliador-status]");
    const stepButtons = Array.from(panel.querySelectorAll("[data-conciliador-step-target]"));
    const stepPanels = Array.from(panel.querySelectorAll("[data-conciliador-step-panel]"));
    const uploadForm = panel.querySelector("[data-conciliador-upload-form]");
    const configForm = panel.querySelector("[data-conciliador-config-form]");
    const previewCard = panel.querySelector("[data-conciliador-preview]");
    const previewTable = panel.querySelector("[data-conciliador-preview-table]");
    const transactionSearch = panel.querySelector("[data-conciliador-transaction-search]");
    const movementFilters = Array.from(panel.querySelectorAll("[data-conciliador-movement-filter]"));
    const transactionsBody = panel.querySelector("[data-conciliador-transactions-body]");
    const rulesBody = panel.querySelector("[data-conciliador-rules-body]");
    const resultBody = panel.querySelector("[data-conciliador-result-body]");
    const resultSummary = panel.querySelector("[data-conciliador-result-summary]");
    const ruleForm = panel.querySelector("[data-conciliador-rule-form]");
    const ruleResetButton = panel.querySelector("[data-conciliador-rule-reset]");
    const applyRulesButton = panel.querySelector("[data-conciliador-apply-rules]");
    const transactionModal = panel.querySelector("[data-conciliador-transaction-modal]");
    const transactionForm = panel.querySelector("[data-conciliador-transaction-form]");
    const transactionTitle = panel.querySelector("[data-conciliador-transaction-title]");
    const transactionCloseButtons = Array.from(panel.querySelectorAll("[data-conciliador-transaction-close]"));
    const columnSelects = Object.fromEntries(
      Array.from(panel.querySelectorAll("[data-conciliador-column-select]")).map((select) => [select.dataset.conciliadorColumnSelect, select]),
    );
    const contextFields = {
      escritorio: Array.from(panel.querySelectorAll('[data-conciliador-context-select="escritorio"]')),
      empresa: Array.from(panel.querySelectorAll('[data-conciliador-context-select="empresa"]')),
      referencia: Array.from(panel.querySelectorAll('[data-conciliador-context-select="referencia"]')),
    };

    if (!workspace || !uploadForm || !configForm || !ruleForm || !transactionForm) {
      return;
    }

    const state = {
      activeStep: "upload",
      importacao: null,
      transactions: [],
      rules: [],
      transactionSearch: "",
      movementFilter: "TODOS",
      activeTransactionId: null,
      activeRuleId: null,
    };

    function getContextValue(name) {
      const list = contextFields[name] || [];
      return list[0]?.value || "";
    }

    function setContextValue(name, value) {
      (contextFields[name] || []).forEach((field) => {
        if (field.value !== value) {
          field.value = value || "";
        }
      });
    }

    function getActiveContext() {
      return {
        escritorio: getContextValue("escritorio") || state.importacao?.escritorio || "",
        empresa: getContextValue("empresa") || state.importacao?.empresa || "",
        referencia: getContextValue("referencia") || state.importacao?.referencia || getTodayMonthValue(),
      };
    }

    function setStatus(message) {
      if (statusLabel) {
        statusLabel.textContent = message;
      }
    }

    function setStep(step) {
      state.activeStep = step;
      stepButtons.forEach((button) => {
        button.classList.toggle("is-active", button.dataset.conciliadorStepTarget === step);
      });
      stepPanels.forEach((panelSection) => {
        panelSection.classList.toggle("is-active", panelSection.dataset.conciliadorStepPanel === step);
      });
    }

    function openWorkspace(step = "upload") {
      workspace.hidden = false;
      setStep(step);
      window.requestAnimationFrame(() => {
        workspace.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }

    function syncUploadFormFromContext() {
      const context = getContextValue("escritorio") || getContextValue("empresa") || getContextValue("referencia")
        ? getActiveContext()
        : {
            escritorio: uploadForm.elements.escritorio?.value || "",
            empresa: uploadForm.elements.empresa?.value || "",
            referencia: uploadForm.elements.referencia?.value || getTodayMonthValue(),
          };

      if (uploadForm.elements.escritorio && context.escritorio) {
        uploadForm.elements.escritorio.value = context.escritorio;
      }
      if (uploadForm.elements.empresa && context.empresa) {
        uploadForm.elements.empresa.value = context.empresa;
      }
      if (uploadForm.elements.referencia) {
        uploadForm.elements.referencia.value = context.referencia || getTodayMonthValue();
      }

      if (ruleForm.elements.empresa && context.empresa && !ruleForm.elements.empresa.value) {
        ruleForm.elements.empresa.value = context.empresa;
      }
    }

    function syncContextFromImportacao(importacao) {
      if (!importacao) {
        return;
      }

      setContextValue("escritorio", importacao.escritorio || "");
      setContextValue("empresa", importacao.empresa || "");
      setContextValue("referencia", importacao.referencia || getTodayMonthValue());
      syncUploadFormFromContext();
    }

    function setEmptyBody(tbody, message, colspan) {
      if (!tbody) {
        return;
      }

      tbody.innerHTML = `<tr><td colspan="${colspan}" class="empty-state">${escapeHtml(message)}</td></tr>`;
    }

    function normalizeHeaderValue(value) {
      return normalizeSearchTerm(String(value || ""));
    }

    function guessHeader(headers, candidates, fallbackToFirst = false) {
      const normalizedHeaders = headers.map((header) => ({ raw: header, norm: normalizeHeaderValue(header) }));

      for (const candidate of candidates) {
        const normalizedCandidate = normalizeHeaderValue(candidate);
        const found = normalizedHeaders.find((header) => header.norm === normalizedCandidate || header.norm.includes(normalizedCandidate));
        if (found) {
          return found.raw;
        }
      }

      return fallbackToFirst ? headers[0] || "" : "";
    }

    function populateColumnSelects(headers = []) {
      const cleanedHeaders = Array.from(new Set((headers || []).map((header) => String(header || "").trim()).filter(Boolean)));
      const aliases = {
        data: ["data", "data movimento", "data lançamento", "data lancamento", "movimento", "date"],
        descricao: ["descricao", "histórico", "historico", "lançamento", "lancamento", "description"],
        valor: ["valor", "amount", "v.valor", "vlr"],
        credito: ["credito", "crédito", "credit"],
        debito: ["debito", "débito", "debit"],
      };

      Object.entries(columnSelects).forEach(([key, select]) => {
        if (!select) {
          return;
        }

        select.replaceChildren();

        const emptyOption = document.createElement("option");
        emptyOption.value = "";
        emptyOption.textContent = key === "credito" || key === "debito" ? "Opcional / Auto" : "Auto detectar";
        select.appendChild(emptyOption);

        cleanedHeaders.forEach((header) => {
          const option = document.createElement("option");
          option.value = header;
          option.textContent = header;
          select.appendChild(option);
        });

        const guessed = guessHeader(cleanedHeaders, aliases[key] || [], key === "data" || key === "descricao" || key === "valor");
        if (guessed) {
          select.value = guessed;
        }
      });
    }

    function renderUploadPreview(metadata = {}) {
      if (!previewCard) {
        return;
      }

      const sampleRows = Array.isArray(metadata.amostra) ? metadata.amostra : [];
      const headers = Array.isArray(metadata.cabecalhos) && metadata.cabecalhos.length
        ? metadata.cabecalhos
        : sampleRows[0]
          ? Object.keys(sampleRows[0]).filter((key) => !key.startsWith("__"))
          : [];

      if (metadata.texto_preview) {
        previewCard.innerHTML = `<pre>${escapeHtml(metadata.texto_preview)}</pre>`;
      } else if (headers.length > 0) {
        previewCard.innerHTML = `
          <div class="conciliador-preview-meta">
            ${headers.map((header) => `<span class="conciliador-badge is-aplicado">${escapeHtml(header)}</span>`).join("")}
          </div>
        `;
      } else {
        previewCard.innerHTML = '<div class="empty-state">Nenhuma amostra disponível.</div>';
      }

      if (previewTable) {
        if (!sampleRows.length) {
          setEmptyBody(previewTable, "Nenhuma amostra encontrada.", 2);
          return;
        }

        const firstRow = sampleRows[0] || {};
        const entries = Object.entries(firstRow).filter(([key]) => !String(key).startsWith("__"));
        previewTable.innerHTML = entries
          .map(([field, value]) => `<tr><td>${escapeHtml(field)}</td><td>${escapeHtml(String(value ?? ""))}</td></tr>`)
          .join("");
      }
    }

    function buildConfigPayload() {
      const data = Object.fromEntries(new FormData(configForm).entries());
      return {
        colunas: {
          data: String(data.col_data || "").trim(),
          descricao: String(data.col_descricao || "").trim(),
          valor: String(data.col_valor || "").trim(),
          credito: String(data.col_credito || "").trim(),
          debito: String(data.col_debito || "").trim(),
        },
        data_format: String(data.data_format || "").trim(),
        normalizacao: {
          remover_numeros: Boolean(configForm.elements.remover_numeros?.checked),
          remover_especiais: Boolean(configForm.elements.remover_especiais?.checked),
          remover_acentos: Boolean(configForm.elements.remover_acentos?.checked),
          maiusculo: Boolean(configForm.elements.maiusculo?.checked),
        },
      };
    }

    function formatDateBR(value) {
      if (!value) {
        return "";
      }

      const parsed = new Date(`${value}T00:00:00`);
      if (Number.isNaN(parsed.getTime())) {
        return String(value);
      }

      return new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" }).format(parsed);
    }

    function transactionStatusBadge(transaction) {
      if (transaction.revisado_manual) {
        return { label: "Manual", className: "is-manual" };
      }

      if (transaction.regra_aplicada || transaction.regra_aplicada_nome) {
        return { label: "Aplicado", className: "is-aplicado" };
      }

      return { label: "Pendente", className: "is-pendente" };
    }

    function movementLabel(value) {
      if (value === "DEBITO") {
        return "Débito";
      }
      if (value === "CREDITO") {
        return "Crédito";
      }
      return value || "";
    }

    function filteredTransactions() {
      const query = normalizeSearchTerm(state.transactionSearch);

      return state.transactions.filter((transaction) => {
        const searchable = normalizeSearchTerm([
          transaction.descricao_original,
          transaction.descricao_normalizada,
          transaction.regra_aplicada_nome,
          transaction.regra_aplicada_texto,
          transaction.categoria,
          transaction.codigo_historico,
        ].join(" "));

        const searchMatches = !query || searchable.includes(query);
        const movementMatches = state.movementFilter === "TODOS"
          ? true
          : state.movementFilter === "PENDENTE"
            ? !transaction.regra_aplicada && !transaction.revisado_manual
            : transaction.tipo_movimento === state.movementFilter;

        return searchMatches && movementMatches;
      });
    }

    function renderTransactions() {
      if (!transactionsBody) {
        return;
      }

      const rows = filteredTransactions();
      if (!rows.length) {
        setEmptyBody(transactionsBody, "Nenhuma transação encontrada.", 7);
        return;
      }

      transactionsBody.innerHTML = rows
        .map((transaction) => {
          const badge = transactionStatusBadge(transaction);
          const movementBadge = "is-neutro";
          return `
            <tr data-conciliador-transaction-id="${escapeHtml(transaction.id)}">
              <td>${escapeHtml(formatDateBR(transaction.data_movimento))}</td>
              <td>${escapeHtml(transaction.descricao_original || "")}</td>
              <td>${escapeHtml(transaction.descricao_normalizada || "")}</td>
              <td>${escapeHtml(transaction.valor_formatado || formatCurrencyBRL(transaction.valor))}</td>
              <td><span class="conciliador-badge ${movementBadge}">${escapeHtml(movementLabel(transaction.tipo_movimento))}</span></td>
              <td>
                <div>${escapeHtml(transaction.regra_aplicada_nome || "PENDENTE")}</div>
                <span class="conciliador-badge ${badge.className}">${escapeHtml(badge.label)}</span>
              </td>
              <td>
                <button class="secondary" type="button" data-conciliador-transaction-edit>Editar</button>
              </td>
            </tr>
          `;
        })
        .join("");
    }

    function renderRules() {
      if (!rulesBody) {
        return;
      }

      if (!state.rules.length) {
        setEmptyBody(rulesBody, "Nenhuma regra cadastrada para este contexto.", 6);
        return;
      }

      rulesBody.innerHTML = state.rules
        .map((rule) => `
          <tr data-conciliador-rule-id="${escapeHtml(rule.id)}">
            <td>${escapeHtml(String(rule.prioridade ?? ""))}</td>
            <td>
              <strong>${escapeHtml(rule.nome || "")}</strong><br />
              <small>${escapeHtml(rule.categoria || "")}${rule.subcategoria ? ` / ${escapeHtml(rule.subcategoria)}` : ""}</small>
            </td>
            <td>${escapeHtml(rule.texto_referencia || "")}</td>
            <td>${escapeHtml(rule.tipo_movimento_label || movementLabel(rule.tipo_movimento))}</td>
            <td>
              <span class="conciliador-badge ${rule.ativo ? "is-aplicado" : "is-erro"}">${rule.ativo ? "Ativa" : "Inativa"}</span>
            </td>
            <td>
              <button class="secondary" type="button" data-conciliador-rule-edit>Editar</button>
              <button class="secondary" type="button" data-conciliador-rule-delete>Excluir</button>
            </td>
          </tr>
        `)
        .join("");
    }

    function renderResultSummary() {
      if (!resultSummary) {
        return;
      }

      const total = state.transactions.length;
      const manual = state.transactions.filter((item) => Boolean(item.revisado_manual)).length;
      const applied = state.transactions.filter((item) => Boolean((item.regra_aplicada || item.regra_aplicada_nome) && !item.revisado_manual)).length;
      const pending = state.transactions.filter((item) => !item.revisado_manual && !(item.regra_aplicada || item.regra_aplicada_nome)).length;

      resultSummary.innerHTML = `
        <div class="stat"><strong>${total}</strong><span>Total</span></div>
        <div class="stat"><strong>${applied}</strong><span>Aplicadas</span></div>
        <div class="stat"><strong>${pending}</strong><span>Pendentes</span></div>
        <div class="stat"><strong>${manual}</strong><span>Manuais</span></div>
      `;
    }

    function renderResult() {
      if (!resultBody) {
        return;
      }

      const rows = filteredTransactions();
      renderResultSummary();

      if (!rows.length) {
        setEmptyBody(resultBody, "O resultado final vai aparecer aqui.", 6);
        return;
      }

      resultBody.innerHTML = rows
        .map((transaction) => {
          const badge = transactionStatusBadge(transaction);
          return `
            <tr>
              <td>${escapeHtml(formatDateBR(transaction.data_movimento))}</td>
              <td>${escapeHtml(transaction.debito || "")}</td>
              <td>${escapeHtml(transaction.credito || "")}</td>
              <td>${escapeHtml(transaction.valor_formatado || formatCurrencyBRL(transaction.valor))}</td>
              <td>${escapeHtml(transaction.historico_final || transaction.descricao_normalizada || transaction.descricao_original || "")}</td>
              <td><span class="conciliador-badge ${badge.className}">${escapeHtml(badge.label)}</span></td>
            </tr>
          `;
        })
        .join("");
    }

    function syncMovementFilterButtons() {
      movementFilters.forEach((button) => {
        button.classList.toggle("is-active", button.dataset.conciliadorMovementFilter === state.movementFilter);
      });
    }

    function setMovementFilter(value) {
      state.movementFilter = value;
      syncMovementFilterButtons();
      renderTransactions();
      renderResult();
    }

    function populateTransactionModal(transaction) {
      if (!transactionForm) {
        return;
      }

      transactionForm.reset();
      transactionForm.elements.id.value = transaction?.id || "";
      transactionForm.elements.descricao_original.value = transaction?.descricao_original || "";
      transactionForm.elements.descricao_normalizada.value = transaction?.descricao_normalizada || "";
      transactionForm.elements.categoria.value = transaction?.categoria || "";
      transactionForm.elements.subcategoria.value = transaction?.subcategoria || "";
      transactionForm.elements.conta_debito.value = transaction?.conta_debito || "";
      transactionForm.elements.conta_credito.value = transaction?.conta_credito || "";
      transactionForm.elements.codigo_historico.value = transaction?.codigo_historico || "";
      transactionForm.elements.revisado_manual.checked = Boolean(transaction?.revisado_manual);
      if (transactionTitle) {
        transactionTitle.textContent = transaction?.descricao_original ? `Editar ${transaction.descricao_original}` : "Editar transação";
      }
    }

    function openTransactionModal(transaction) {
      if (!transactionModal || !transactionForm || !transaction) {
        return;
      }

      state.activeTransactionId = transaction.id;
      populateTransactionModal(transaction);
      if (typeof transactionModal.showModal === "function") {
        if (!transactionModal.open) {
          transactionModal.showModal();
        }
      } else {
        transactionModal.setAttribute("open", "open");
      }
    }

    function closeTransactionModal() {
      if (!transactionModal) {
        return;
      }

      if (typeof transactionModal.close === "function") {
        if (transactionModal.open) {
          transactionModal.close();
        }
      } else {
        transactionModal.removeAttribute("open");
      }

      state.activeTransactionId = null;
      if (transactionTitle) {
        transactionTitle.textContent = "Editar transação";
      }
    }

    function resetRuleForm() {
      state.activeRuleId = null;
      ruleForm.reset();
      if (ruleForm.elements.id) {
        ruleForm.elements.id.value = "";
      }
      if (ruleForm.elements.prioridade) {
        ruleForm.elements.prioridade.value = "100";
      }
      if (ruleForm.elements.ativo) {
        ruleForm.elements.ativo.checked = true;
      }
      if (ruleForm.elements.aplicar_automatico) {
        ruleForm.elements.aplicar_automatico.checked = true;
      }
      const context = getActiveContext();
      if (ruleForm.elements.empresa) {
        ruleForm.elements.empresa.value = context.empresa || "";
      }
    }

    function populateRuleForm(rule) {
      ruleForm.elements.id.value = rule?.id || "";
      ruleForm.elements.nome.value = rule?.nome || "";
      ruleForm.elements.texto_referencia.value = rule?.texto_referencia || "";
      ruleForm.elements.tipo_comparacao.value = rule?.tipo_comparacao || "CONTEM";
      ruleForm.elements.tipo_movimento.value = rule?.tipo_movimento || "AMBOS";
      ruleForm.elements.categoria.value = rule?.categoria || "";
      ruleForm.elements.subcategoria.value = rule?.subcategoria || "";
      ruleForm.elements.codigo_historico.value = rule?.codigo_historico || "";
      ruleForm.elements.conta_debito.value = rule?.conta_debito || "";
      ruleForm.elements.conta_credito.value = rule?.conta_credito || "";
      ruleForm.elements.prioridade.value = rule?.prioridade || 100;
      ruleForm.elements.aplicar_automatico.checked = rule?.aplicar_automatico ?? true;
      ruleForm.elements.ativo.checked = rule?.ativo ?? true;
      ruleForm.elements.empresa.value = rule?.empresa || "";
      state.activeRuleId = rule?.id || null;
    }

    async function loadRules() {
      const context = getActiveContext();
      if (!context.escritorio) {
        state.rules = [];
        renderRules();
        return;
      }

      const searchParams = new URLSearchParams();
      searchParams.set("escritorio", context.escritorio);
      if (context.empresa) {
        searchParams.set("empresa", context.empresa);
      }

      const payload = await apiRequest(session, `/conciliador-regras/?${searchParams.toString()}`);
      state.rules = Array.isArray(payload) ? payload : (payload?.results || []);
      renderRules();
    }

    async function loadTransactions() {
      if (!state.importacao?.id) {
        state.transactions = [];
        renderTransactions();
        renderResult();
        return;
      }

      const payload = await apiRequest(session, `/conciliador-importacoes/${state.importacao.id}/transacoes/`);
      state.transactions = Array.isArray(payload) ? payload : [];
      renderTransactions();
      renderResult();
    }

    async function refreshAfterContextChange() {
      syncUploadFormFromContext();
      try {
        await loadRules();
        setStatus("Contexto aplicado.");
      } catch (error) {
        setStatus(error?.message || "Não foi possível carregar as regras deste contexto.");
      }
    }

    async function submitUpload() {
      const formData = new FormData(uploadForm);
      const payload = await apiRequest(session, "/conciliador-importacoes/", {
        method: "POST",
        body: formData,
      });

      state.importacao = payload;
      syncContextFromImportacao(payload);
      renderUploadPreview(payload.metadados || {});
      populateColumnSelects(payload.metadados?.cabecalhos || []);
      setStatus(
        payload.mensagem_erro
          ? `Arquivo ${payload.arquivo_nome || "enviado"} carregado com aviso: ${payload.mensagem_erro}`
          : `Arquivo ${payload.arquivo_nome || "enviado"} carregado com sucesso.`,
      );
      await loadRules();
      setStep("config");
    }

    async function submitConfig() {
      if (!state.importacao?.id) {
        throw new Error("Faça o upload do arquivo antes de processar.");
      }

      const configuracao = buildConfigPayload();
      const payload = await apiRequest(session, `/conciliador-importacoes/${state.importacao.id}/processar/`, {
        method: "POST",
        body: { configuracao },
      });

      state.importacao = payload;
      syncContextFromImportacao(payload);
      await loadTransactions();
      await loadRules();
      setStatus(`Processamento concluído: ${payload.summary?.transacoes_processadas || 0} transações.`);
      setStep("transactions");
    }

    async function applyRules() {
      if (!state.importacao?.id) {
        throw new Error("Faça o upload e processe o extrato antes de aplicar regras.");
      }

      const payload = await apiRequest(session, `/conciliador-importacoes/${state.importacao.id}/aplicar-regras/`, {
        method: "POST",
      });

      state.importacao = payload;
      await loadTransactions();
      await loadRules();
      setStatus(`Regras aplicadas: ${payload.summary?.regras_aplicadas || 0} itens.`);
      setStep("result");
    }

    async function saveTransactionEdit() {
      if (!state.activeTransactionId) {
        return;
      }

      const payload = {
        descricao_normalizada: transactionForm.elements.descricao_normalizada.value,
        categoria: transactionForm.elements.categoria.value,
        subcategoria: transactionForm.elements.subcategoria.value,
        conta_debito: transactionForm.elements.conta_debito.value,
        conta_credito: transactionForm.elements.conta_credito.value,
        codigo_historico: transactionForm.elements.codigo_historico.value,
        revisado_manual: Boolean(transactionForm.elements.revisado_manual.checked),
      };

      await apiRequest(session, `/conciliador-transacoes/${state.activeTransactionId}/`, {
        method: "PATCH",
        body: payload,
      });

      await loadTransactions();
      setStatus("Transação atualizada manualmente.");
      closeTransactionModal();
    }

    async function saveRule() {
      const context = getActiveContext();
      if (!context.escritorio) {
        throw new Error("Selecione um escritório antes de salvar regras.");
      }

      const payload = {
        escritorio: context.escritorio,
        empresa: ruleForm.elements.empresa.value || null,
        nome: ruleForm.elements.nome.value,
        texto_referencia: ruleForm.elements.texto_referencia.value,
        tipo_comparacao: ruleForm.elements.tipo_comparacao.value,
        tipo_movimento: ruleForm.elements.tipo_movimento.value,
        categoria: ruleForm.elements.categoria.value,
        subcategoria: ruleForm.elements.subcategoria.value,
        codigo_historico: ruleForm.elements.codigo_historico.value,
        conta_debito: ruleForm.elements.conta_debito.value,
        conta_credito: ruleForm.elements.conta_credito.value,
        aplicar_automatico: Boolean(ruleForm.elements.aplicar_automatico.checked),
        prioridade: Number(ruleForm.elements.prioridade.value || 100),
        ativo: Boolean(ruleForm.elements.ativo.checked),
      };

      const hasId = Boolean(ruleForm.elements.id.value);
      await apiRequest(session, hasId ? `/conciliador-regras/${ruleForm.elements.id.value}/` : "/conciliador-regras/", {
        method: hasId ? "PATCH" : "POST",
        body: payload,
      });

      await loadRules();
      resetRuleForm();
      setStatus("Regra salva com sucesso.");
    }

    function wireTableActions() {
      if (transactionsBody) {
        transactionsBody.addEventListener("click", (event) => {
          const button = event.target.closest("[data-conciliador-transaction-edit]");
          if (!button) {
            return;
          }

          const row = button.closest("tr[data-conciliador-transaction-id]");
          if (!row) {
            return;
          }

          const record = state.transactions.find((item) => item.id === row.dataset.conciliadorTransactionId);
          if (record) {
            openTransactionModal(record);
          }
        });
      }

      if (rulesBody) {
        rulesBody.addEventListener("click", async (event) => {
          const editButton = event.target.closest("[data-conciliador-rule-edit]");
          const deleteButton = event.target.closest("[data-conciliador-rule-delete]");
          const row = event.target.closest("tr[data-conciliador-rule-id]");
          if (!row) {
            return;
          }

          const record = state.rules.find((item) => item.id === row.dataset.conciliadorRuleId);
          if (!record) {
            return;
          }

          if (editButton) {
            populateRuleForm(record);
            setStep("rules");
            return;
          }

          if (deleteButton && window.confirm(`Excluir a regra ${record.nome}?`)) {
            try {
              await apiRequest(session, `/conciliador-regras/${record.id}/`, { method: "DELETE" });
              await loadRules();
              setStatus("Regra excluída.");
            } catch (error) {
              setStatus(error?.message || "Falha ao excluir a regra.");
              logError("Falha ao excluir regra do conciliador.", error);
            }
          }
        });
      }
    }

    stepButtons.forEach((button) => {
      button.addEventListener("click", () => setStep(button.dataset.conciliadorStepTarget));
    });

    if (openButton) {
      openButton.addEventListener("click", () => {
        syncUploadFormFromContext();
        openWorkspace("upload");
        setStatus("Envie um arquivo para iniciar a importação.");
      });
    }

    if (contextFilterButton) {
      contextFilterButton.addEventListener("click", async () => {
        syncUploadFormFromContext();
        try {
          await refreshAfterContextChange();
        } catch (error) {
          setStatus(error?.message || "Não foi possível atualizar o contexto.");
        }
      });
    }

    ["escritorio", "empresa", "referencia"].forEach((name) => {
      (contextFields[name] || []).forEach((field) => {
        field.addEventListener("change", () => {
          if (uploadForm.elements[name]) {
            uploadForm.elements[name].value = field.value;
          }
          if (name === "empresa" && ruleForm.elements.empresa) {
            ruleForm.elements.empresa.value = field.value || "";
          }
        });
      });
    });

    if (transactionSearch) {
      transactionSearch.addEventListener("input", () => {
        state.transactionSearch = transactionSearch.value;
        renderTransactions();
        renderResult();
      });
    }

    movementFilters.forEach((button) => {
      button.addEventListener("click", () => {
        setMovementFilter(button.dataset.conciliadorMovementFilter || "TODOS");
      });
    });

    uploadForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        setStatus("Enviando arquivo...");
        await submitUpload();
      } catch (error) {
        setStatus(error?.message || "Falha ao enviar o arquivo.");
        logError("Falha ao enviar arquivo do conciliador.", error);
      }
    });

    configForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        setStatus("Processando extrato...");
        await submitConfig();
      } catch (error) {
        setStatus(error?.message || "Falha ao processar o extrato.");
        logError("Falha ao processar extrato.", error);
      }
    });

    if (applyRulesButton) {
      applyRulesButton.addEventListener("click", async () => {
        try {
          setStatus("Aplicando regras...");
          await applyRules();
        } catch (error) {
          setStatus(error?.message || "Falha ao aplicar regras.");
          logError("Falha ao aplicar regras do conciliador.", error);
        }
      });
    }

    ruleResetButton?.addEventListener("click", () => {
      resetRuleForm();
    });

    ruleForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await saveRule();
      } catch (error) {
        setStatus(error?.message || "Falha ao salvar regra.");
        logError("Falha ao salvar regra do conciliador.", error);
      }
    });

    transactionForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await saveTransactionEdit();
      } catch (error) {
        setStatus(error?.message || "Falha ao salvar a transação.");
        logError("Falha ao salvar transação manual.", error);
      }
    });

    transactionCloseButtons.forEach((button) => {
      button.addEventListener("click", closeTransactionModal);
    });

    transactionModal?.addEventListener("click", (event) => {
      if (event.target === transactionModal) {
        closeTransactionModal();
      }
    });

    wireTableActions();
    syncMovementFilterButtons();
    syncUploadFormFromContext();
    resetRuleForm();
    setStep("upload");
    setStatus("Selecione um escritório e uma empresa para começar.");
  }

  function setupClientsCrud(session) {
    const panel = document.querySelector('[data-panel-view-section="clientes"]');
    if (!panel) {
      return;
    }

    const modal = panel.querySelector("[data-client-modal]");
    const summaryPanel = panel.querySelector("[data-clients-summary]");
    const modalCard = panel.querySelector("[data-client-modal-card]");
    const form = panel.querySelector("[data-client-form]");
    const modalEyebrow = panel.querySelector(".client-modal-eyebrow");
    const modalTitle = panel.querySelector("[data-client-modal-title]");
    const eyebrow = panel.querySelector("[data-client-detail-eyebrow]");
    const modalDescription = panel.querySelector("[data-client-modal-description]");
    const modalModeLabel = panel.querySelector("[data-client-modal-mode-label]");
    const modalStatus = panel.querySelector("[data-client-modal-status]");
    const modalQuickActions = panel.querySelector("[data-client-modal-quick-actions]");
    const modalQuickEdit = panel.querySelector("[data-client-modal-quick-edit]");
    const modalQuickToggle = panel.querySelector("[data-client-modal-quick-toggle]");
    const submitButton = panel.querySelector("[data-client-submit]");
    const list = panel.querySelector("[data-client-list]");
    const searchInput = panel.querySelector("[data-client-search]");
    const filterRoot = panel.querySelector("[data-client-filter]");
    const filterToggle = panel.querySelector("[data-client-filter-toggle]");
    const filterMenu = panel.querySelector("[data-client-filter-menu]");
    const filterLabel = panel.querySelector("[data-client-filter-label]");
    const filterOptions = Array.from(panel.querySelectorAll("[data-client-filter-option]"));
    const addButtons = panel.querySelectorAll("[data-client-modal-open]");
    const closeButtons = panel.querySelectorAll("[data-client-modal-close]");
    const tabButtons = panel.querySelectorAll("[data-client-tab]");
    const tabPanes = panel.querySelectorAll("[data-client-tab-pane]");
    const tabRegras = panel.querySelector("[data-client-tab-regras]");
    const tabExtratos = panel.querySelector("[data-client-tab-extratos]");
    const emptyState = panel.querySelector("[data-client-empty]");
    const countLabel = panel.querySelector("[data-client-count]");
    const summaryTotal = panel.querySelector("[data-client-summary-total]");
    const summaryActive = panel.querySelector("[data-client-summary-active]");
    const summaryReview = panel.querySelector("[data-client-summary-review]");
    const summaryPaused = panel.querySelector("[data-client-summary-paused]");
    const accountNewButton = panel.querySelector("[data-client-account-new]");
    const accountForm = panel.querySelector("[data-client-account-form]");
    const accountFormTitle = panel.querySelector("[data-client-account-form-title]");
    const accountFormSubtitle = panel.querySelector("[data-client-account-form-subtitle]");
    const accountFormCancel = panel.querySelector("[data-client-account-cancel]");
    const accountFormSubmit = panel.querySelector("[data-client-account-submit]");
    const accountList = panel.querySelector("[data-client-account-list]");
    const accountLayout = panel.querySelector(".client-account-layout");
    const accountEmpty = panel.querySelector("[data-client-account-empty]");
    const accountHint = panel.querySelector("[data-client-account-hint]");
    const accountType = accountForm?.querySelector("[name='tipo']") || null;
    const accountFieldsBank = panel.querySelector("[data-account-fields='bancaria']");
    const accountFieldsContabil = panel.querySelector("[data-account-fields='contabil']");
    const ACCOUNT_BANK_OTHER_VALUE = "OUTRO";
    const ACCOUNT_BANK_OPTIONS = [
      "Banco do Brasil",
      "Bradesco",
      "Caixa",
      "Santander",
      "Itaú",
      "Banco Inter",
      "Nubank",
      "Sicredi",
      "Sicoob",
      "BTG Pactual",
      "Safra",
      "Banrisul",
      "C6 Bank",
      "Banco Original",
      "Banco do Nordeste",
      "Mercantil do Brasil",
      "Daycoval",
      "Banco PAN",
      "BV",
    ];
    const ACCOUNT_BANK_ALIASES = {
      "caixa economica federal": "Caixa",
      "itau unibanco": "Itaú",
      inter: "Banco Inter",
      original: "Banco Original",
      "banco safra": "Safra",
      mercantil: "Mercantil do Brasil",
      "banco pan": "Banco PAN",
    };
    const BANK_VISUALS = {
      "banco do brasil": { initials: "BB", className: "bb" },
      bradesco: { initials: "BR", className: "bradesco" },
      caixa: { initials: "CX", className: "caixa" },
      santander: { initials: "ST", className: "santander" },
      itau: { initials: "IT", className: "itau" },
      "banco inter": { initials: "IN", className: "inter" },
      nubank: { initials: "NU", className: "nubank" },
      sicredi: { initials: "SI", className: "sicredi" },
      sicoob: { initials: "SC", className: "sicoob" },
      "btg pactual": { initials: "BT", className: "btg" },
      safra: { initials: "SF", className: "safra" },
      banrisul: { initials: "BN", className: "banrisul" },
      "c6 bank": { initials: "C6", className: "c6" },
      "banco original": { initials: "OR", className: "original" },
      "banco do nordeste": { initials: "NE", className: "nordeste" },
      "mercantil do brasil": { initials: "MB", className: "mercantil" },
      daycoval: { initials: "DY", className: "daycoval" },
      "banco pan": { initials: "PAN", className: "pan" },
      bv: { initials: "BV", className: "bv" },
    };
    const certificateStatus = panel.querySelector("[data-client-certificate-status]");
    const certificateMeta = panel.querySelector("[data-client-certificate-meta]");
    const certificateInput = panel.querySelector("[data-client-certificate-file]");
    const certificateSubmit = panel.querySelector("[data-client-certificate-submit]");
    const certificateClear = panel.querySelector("[data-client-certificate-clear]");
    const certificateDelete = panel.querySelector("[data-client-certificate-delete]");
    const accountsCard = panel.querySelector(".client-accounts-card");
    const certificateCard = panel.querySelector(".client-certificate-card");
    const relatedGrid = panel.querySelector(".client-related-grid");
    const basicSummaryCard = panel.querySelector("[data-client-basic-summary]");
    const basicSummaryFields = {
      document: panel.querySelector("[data-client-basic-document]"),
      email: panel.querySelector("[data-client-basic-email]"),
      phone: panel.querySelector("[data-client-basic-phone]"),
      start: panel.querySelector("[data-client-basic-start]"),
    };
    const clientAccountFormFields = accountForm ? {
      id: accountForm.elements.namedItem("id"),
      cliente: accountForm.elements.namedItem("cliente"),
      tipo: accountForm.elements.namedItem("tipo"),
      apelido: accountForm.elements.namedItem("apelido"),
      bankSelect: accountForm.querySelector("[data-client-account-bank-select]"),
      bankOtherWrap: accountForm.querySelector("[data-client-account-bank-other-wrap]"),
      banco: accountForm.elements.namedItem("banco"),
      agencia: accountForm.elements.namedItem("agencia"),
      numero: accountForm.elements.namedItem("numero"),
      codigo_contabil: accountForm.elements.namedItem("codigo_contabil"),
      descricao_contabil: accountForm.elements.namedItem("descricao_contabil"),
      observacoes: accountForm.elements.namedItem("observacoes"),
    } : {};
    const historicoSection = panel.querySelector("[data-client-historico-section]");
    const historicoLoading = panel.querySelector("[data-client-historico-loading]");
    const historicoEmpty = panel.querySelector("[data-client-historico-empty]");
    const historicoList = panel.querySelector("[data-client-historico-list]");
    const historicoCount = panel.querySelector("[data-client-historico-count]");
    const extratoHistoricoSection = panel.querySelector("[data-client-extrato-historico-section]");
    const extratoHistoricoEmpty = panel.querySelector("[data-client-extrato-historico-empty]");
    const extratoHistoricoList = panel.querySelector("[data-client-extrato-historico-list]");
    const extratoHistoricoCount = panel.querySelector("[data-client-extrato-historico-count]");

    if (!modal || !form || !list) {
      return;
    }

    const state = {
      clients: [],
      loading: false,
      error: "",
      activeClientId: null,
      activeClientRecord: null,
      activeClientMode: "create",
      statusFilter: "all",
      accounts: [],
      accountEditingId: null,
      certificate: null,
      certificateLoading: false,
      relationRequestToken: 0,
    };

    let lastCustomBankValue = "";

    function getClientModeLabel(mode) {
      if (mode === "view") {
        return "Visualização";
      }

      if (mode === "accounts") {
        return "Contas";
      }

      if (mode === "edit") {
        return "Edição";
      }

      return "Criação";
    }

    function getClientModeDescription(mode) {
      if (mode === "view") {
        return "Confira os dados principais do cliente em modo somente leitura.";
      }

      if (mode === "accounts") {
        return "Veja os dados básicos e gerencie as contas bancárias e contábeis deste cliente.";
      }

      if (mode === "edit") {
        return "Atualize os dados principais do cliente.";
      }

      return "Preencha os dados principais do cliente.";
    }

    function isClientReadOnlyMode(mode = state.activeClientMode) {
      return mode === "view" || mode === "accounts";
    }

    function getActiveClientRecord() {
      if (state.activeClientRecord) {
        return state.activeClientRecord;
      }

      if (!state.activeClientId) {
        return null;
      }

      return state.clients.find((client) => client.id === state.activeClientId) || null;
    }

    function getClientStatusLabel(record) {
      return String(record?.situacao_label || record?.situacao || "—").trim() || "—";
    }

    function getClientStatusToggleLabel(record) {
      const statusKey = normalizeClientStatus(record?.situacao_label || record?.situacao);
      return statusKey === "ativo" ? "Desativar" : "Ativar";
    }

    function getClientStatusToggleValue(record) {
      const statusKey = normalizeClientStatus(record?.situacao_label || record?.situacao);
      return statusKey === "ativo" ? "INATIVO" : "ATIVO";
    }

    function syncClientModalHeader(mode) {
      const client = getActiveClientRecord();
      const readOnlyMode = isClientReadOnlyMode(mode);

      if (modalEyebrow) {
        modalEyebrow.textContent = readOnlyMode
          ? (mode === "accounts" ? "Bancos e contas" : "Visualização de cliente")
          : (mode === "edit" ? "Edição de cliente" : "Cadastro de cliente");
      }

      if (modalTitle) {
        modalTitle.textContent = readOnlyMode && client
          ? client.nome || "Cliente"
          : mode === "edit"
            ? "Editar cliente"
            : "Novo cliente";
      }

      if (modalModeLabel) {
        modalModeLabel.textContent = getClientModeLabel(mode);
      }

      if (modalDescription) {
        modalDescription.textContent = getClientModeDescription(mode);
      }

      if (modalStatus) {
        modalStatus.hidden = !readOnlyMode || !client;
        modalStatus.textContent = getClientStatusLabel(client);
      }

      if (modalQuickActions) {
        modalQuickActions.hidden = !readOnlyMode || !client;
      }

      if (modalQuickEdit) {
        modalQuickEdit.hidden = !readOnlyMode || !client;
      }

      if (modalQuickToggle) {
        modalQuickToggle.hidden = !readOnlyMode || !client;
        modalQuickToggle.textContent = getClientStatusToggleLabel(client);
      }
    }

    function updateRelatedControls() {
      const readOnlyMode = isClientReadOnlyMode();
      const accountsMode = state.activeClientMode === "accounts";
      const certificateMode = state.activeClientMode === "view" || state.activeClientMode === "edit";
      const relatedEnabled = Boolean(state.activeClientId) && accountsMode && !state.certificateLoading;
      const certificateEnabled = Boolean(state.activeClientId) && certificateMode && !state.certificateLoading;

      if (accountNewButton) accountNewButton.disabled = !relatedEnabled;
      if (accountFormSubmit) accountFormSubmit.disabled = !relatedEnabled;
      if (certificateSubmit) certificateSubmit.disabled = !certificateEnabled;
      if (certificateClear) certificateClear.disabled = !certificateEnabled;
      if (certificateDelete) certificateDelete.disabled = !certificateEnabled;
      if (certificateInput) certificateInput.disabled = !certificateEnabled;
    }

    function renderClientBasicSummary(record) {
      if (!basicSummaryCard) {
        return;
      }

      const client = record ? normalizeClientRecord(record) : null;
      if (!client) {
        if (basicSummaryFields.document) basicSummaryFields.document.textContent = "—";
        if (basicSummaryFields.email) basicSummaryFields.email.textContent = "—";
        if (basicSummaryFields.phone) basicSummaryFields.phone.textContent = "—";
        if (basicSummaryFields.start) basicSummaryFields.start.textContent = "—";
        return;
      }

      const startLabel = client.data_inicio
        ? new Date(`${client.data_inicio}T00:00:00`).toLocaleDateString("pt-BR")
        : "—";

      if (basicSummaryFields.document) basicSummaryFields.document.textContent = maskDocument(client.cpf_cnpj) || client.cpf_cnpj || "—";
      if (basicSummaryFields.email) basicSummaryFields.email.textContent = client.email || "—";
      if (basicSummaryFields.phone) basicSummaryFields.phone.textContent = maskPhone(client.telefone) || client.telefone || "—";
      if (basicSummaryFields.start) basicSummaryFields.start.textContent = startLabel;
    }

    function normalizeBankValue(value) {
      return String(value || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase()
        .trim();
    }

    function resolveKnownBank(value) {
      const normalized = normalizeBankValue(value);
      if (!normalized) {
        return "";
      }

      const alias = ACCOUNT_BANK_ALIASES[normalized];
      if (alias) {
        return alias;
      }

      return ACCOUNT_BANK_OPTIONS.find((option) => normalizeBankValue(option) === normalized) || "";
    }

    function makeBankInitials(value) {
      const words = String(value || "")
        .trim()
        .split(/\s+/)
        .filter((word) => !["banco", "bank", "do", "da", "de", "dos", "das"].includes(normalizeBankValue(word)));
      const source = words.length ? words : String(value || "Banco").trim().split(/\s+/);
      const initials = source.slice(0, 2).map((word) => word.charAt(0)).join("").toUpperCase();
      return initials || "BK";
    }

    function getAccountVisual(account) {
      if (account.tipo === "CONTABIL") {
        return {
          label: "Conta contábil",
          initials: "CT",
          className: "contabil",
        };
      }

      const bankName = resolveKnownBank(account.banco) || account.banco || "Conta bancária";
      const visual = BANK_VISUALS[normalizeBankValue(bankName)];
      return {
        label: bankName,
        initials: visual?.initials || makeBankInitials(bankName),
        className: visual?.className || "other",
      };
    }

    function syncAccountBankField() {
      const bankSelect = clientAccountFormFields.bankSelect;
      const bankInput = clientAccountFormFields.banco;
      if (!bankSelect || !bankInput) {
        return;
      }

      const isBankAccount = accountType?.value === "BANCARIA";
      const selectedValue = String(bankSelect.value || "").trim();
      const knownBank = resolveKnownBank(selectedValue);

      if (!isBankAccount) {
        bankSelect.disabled = true;
        bankSelect.required = false;
        bankInput.disabled = true;
        bankInput.required = false;
        if (clientAccountFormFields.bankOtherWrap) {
          clientAccountFormFields.bankOtherWrap.hidden = true;
        }
        return;
      }

      bankSelect.disabled = false;
      bankSelect.required = true;

      if (selectedValue === ACCOUNT_BANK_OTHER_VALUE) {
        bankInput.disabled = false;
        bankInput.required = true;
        bankInput.value = lastCustomBankValue || "";
        if (clientAccountFormFields.bankOtherWrap) {
          clientAccountFormFields.bankOtherWrap.hidden = false;
        }
        return;
      }

      if (selectedValue && !knownBank) {
        bankSelect.value = "";
        bankInput.value = "";
        bankInput.disabled = true;
        bankInput.required = false;
        if (clientAccountFormFields.bankOtherWrap) {
          clientAccountFormFields.bankOtherWrap.hidden = true;
        }
        return;
      }

      if (bankInput.value && !resolveKnownBank(bankInput.value)) {
        lastCustomBankValue = bankInput.value.trim();
      }

      bankInput.value = knownBank ? knownBank : "";
      bankInput.disabled = true;
      bankInput.required = false;
      if (clientAccountFormFields.bankOtherWrap) {
        clientAccountFormFields.bankOtherWrap.hidden = true;
      }

      if (knownBank) {
        bankSelect.value = knownBank;
      }
    }

    function setAccountBankSelection(bankValue) {
      const normalized = String(bankValue || "").trim();
      const knownBank = resolveKnownBank(normalized);
      lastCustomBankValue = knownBank ? "" : normalized;

      if (clientAccountFormFields.bankSelect) {
        clientAccountFormFields.bankSelect.value = knownBank ? knownBank : normalized ? ACCOUNT_BANK_OTHER_VALUE : "";
      }

      if (clientAccountFormFields.banco) {
        clientAccountFormFields.banco.value = knownBank ? knownBank : normalized;
      }

      syncAccountBankField();
    }

    function getSelectedAccountBank() {
      const bankSelect = clientAccountFormFields.bankSelect;
      const bankInput = clientAccountFormFields.banco;

      if (!bankSelect || !bankInput) {
        return "";
      }

      const selectedValue = String(bankSelect.value || "").trim();
      if (selectedValue === ACCOUNT_BANK_OTHER_VALUE) {
        return String(bankInput.value || "").trim();
      }

      return resolveKnownBank(selectedValue) || String(bankInput.value || "").trim();
    }

    function setClientFormMode(mode) {
      const viewMode = mode === "view";
      const accountsMode = mode === "accounts";
      const certificateMode = mode === "view" || mode === "edit";
      form.dataset.clientMode = mode;
      if (modalCard) {
        modalCard.dataset.clientMode = mode;
      }

      syncClientModalHeader(mode);

      if (form) {
        form.hidden = accountsMode;
      }

      if (basicSummaryCard) {
        basicSummaryCard.hidden = !accountsMode;
      }

      if (relatedGrid) {
        relatedGrid.hidden = !(accountsMode || certificateMode);
      }

      if (accountsCard) {
        accountsCard.hidden = !accountsMode;
      }

      if (certificateCard) {
        certificateCard.hidden = !certificateMode;
      }

      form.querySelectorAll("input, textarea").forEach((field) => {
        if (field.name === "codigo") {
          field.readOnly = true;
          return;
        }
        field.readOnly = viewMode;
      });

      if (form.elements.situacao) {
        form.elements.situacao.disabled = viewMode;
      }

      if (submitButton) {
        submitButton.textContent = viewMode ? "Fechar" : mode === "accounts" ? "Fechar" : mode === "create" ? "Salvar cliente" : "Salvar alterações";
      }

      if (accountFormCancel) accountFormCancel.disabled = false;

      updateRelatedControls();

      renderAccounts();
      renderCertificate();
    }

    function syncAccountTypeFields() {
      if (!accountForm) {
        return;
      }
      // Sempre bancária — garante visibilidade
      if (accountFieldsBank) {
        accountFieldsBank.classList.add("is-active");
      }
      syncAccountBankField();
    }

    function validateCodigoContabil(value) {
      if (!value) return null; // vazio = neutro
      const codigoNorm = String(value).trim();
      const plano = window.__GM_PLANO_CONTAS__ || [];
      return plano.some((c) => String(c.codigo).trim() === codigoNorm);
    }

    function syncCodigoContabilBadge(inputEl) {
      const badge = accountForm?.querySelector("[data-client-account-contabil-badge]");
      if (!badge || !inputEl) return;
      const val = String(inputEl.value || "").trim();
      if (!val) {
        badge.hidden = true;
        badge.className = "client-account-contabil-badge";
        badge.textContent = "";
        return;
      }
      const valid = validateCodigoContabil(val);
      badge.hidden = false;
      if (valid === true) {
        badge.className = "client-account-contabil-badge is-valid";
        badge.textContent = "✓";
        badge.title = "Código encontrado no plano de contas";
      } else if (valid === false) {
        badge.className = "client-account-contabil-badge is-invalid";
        badge.textContent = "✗";
        badge.title = "Código não encontrado no plano de contas";
      } else {
        badge.hidden = true;
      }
    }

    function formatCertificateSize(size) {
      if (typeof formatConciliadorFileSize === "function") {
        return formatConciliadorFileSize(size);
      }

      if (!Number.isFinite(size) || size <= 0) {
        return "0 B";
      }

      if (size < 1024) return `${size} B`;
      if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
      return `${(size / (1024 * 1024)).toFixed(1)} MB`;
    }

    function normalizeAccount(record) {
      return {
        id: String(record?.id || "").trim(),
        cliente: String(record?.cliente || state.activeClientId || "").trim(),
        tipo: String(record?.tipo || "BANCARIA").trim() || "BANCARIA",
        tipo_label: String(record?.tipo_label || record?.tipo || "Conta bancária").trim(),
        apelido: String(record?.apelido || "").trim(),
        banco: String(record?.banco || "").trim(),
        agencia: String(record?.agencia || "").trim(),
        numero: String(record?.numero || "").trim(),
        codigo_contabil: String(record?.codigo_contabil || "").trim(),
        descricao_contabil: String(record?.descricao_contabil || "").trim(),
        observacoes: String(record?.observacoes || "").trim(),
        ativo: Boolean(record?.ativo),
        resumo: String(record?.resumo || "").trim(),
      };
    }

    function normalizeCertificate(record) {
      if (!record) {
        return null;
      }

      return {
        id: String(record?.id || "").trim(),
        cliente: String(record?.cliente || state.activeClientId || "").trim(),
        arquivo_original: String(record?.arquivo_original || "").trim(),
        tipo_arquivo: String(record?.tipo_arquivo || "PFX").trim() || "PFX",
        tipo_arquivo_label: String(record?.tipo_arquivo_label || record?.tipo_arquivo || "PFX").trim(),
        tamanho_bytes: Number(record?.tamanho_bytes || 0),
        tamanho_formatado: String(record?.tamanho_formatado || "").trim(),
        hash_sha256: String(record?.hash_sha256 || "").trim(),
        ativo: Boolean(record?.ativo),
        resumo: String(record?.resumo || "").trim(),
        criado_em: String(record?.criado_em || "").trim(),
        atualizado_em: String(record?.atualizado_em || "").trim(),
      };
    }

    function setAccountFormVisible(visible) {
      if (!accountForm) {
        return;
      }

      accountForm.hidden = !visible;
      if (accountLayout) {
        accountLayout.classList.toggle("has-account-form", Boolean(visible));
      }
    }

    function resetAccountForm() {
      state.accountEditingId = null;
      if (!accountForm) {
        return;
      }

      accountForm.reset();
      if (clientAccountFormFields.id) clientAccountFormFields.id.value = "";
      if (clientAccountFormFields.cliente) clientAccountFormFields.cliente.value = state.activeClientId || "";
      lastCustomBankValue = "";
      setAccountBankSelection("");
      syncAccountTypeFields();
      syncCodigoContabilBadge(clientAccountFormFields.codigo_contabil);
      setAccountFormVisible(false);
      if (accountFormTitle) accountFormTitle.textContent = "Nova conta";
      if (accountFormSubtitle) accountFormSubtitle.textContent = "Configure as contas bancárias vinculadas a este cliente.";
      if (accountFormSubmit) accountFormSubmit.textContent = "Salvar conta";
    }

    function openAccountForm(record = null) {
      if (!accountForm || !state.activeClientId || state.activeClientMode !== "accounts") {
        return;
      }

      state.accountEditingId = record?.id || null;
      accountForm.reset();
      if (clientAccountFormFields.id) clientAccountFormFields.id.value = record?.id || "";
      if (clientAccountFormFields.cliente) clientAccountFormFields.cliente.value = state.activeClientId || "";
      if (clientAccountFormFields.apelido) clientAccountFormFields.apelido.value = record?.apelido || "";
      setAccountBankSelection(record?.banco || "");
      if (clientAccountFormFields.agencia) clientAccountFormFields.agencia.value = record?.agencia || "";
      if (clientAccountFormFields.numero) clientAccountFormFields.numero.value = record?.numero || "";
      if (clientAccountFormFields.codigo_contabil) clientAccountFormFields.codigo_contabil.value = record?.codigo_contabil || "";
      if (clientAccountFormFields.observacoes) clientAccountFormFields.observacoes.value = record?.observacoes || "";

      if (accountFormTitle) accountFormTitle.textContent = record?.id ? "Editar conta" : "Nova conta";
      if (accountFormSubtitle) accountFormSubtitle.textContent = record?.id
        ? `Atualize os dados da conta ${record.apelido || record.resumo || "selecionada"}.`
        : "Configure contas bancárias ou contábeis vinculadas a este cliente.";
      if (accountFormSubmit) accountFormSubmit.textContent = record?.id ? "Salvar alterações" : "Salvar conta";

      syncAccountTypeFields();
      syncCodigoContabilBadge(clientAccountFormFields.codigo_contabil);
      setAccountFormVisible(true);
      if (accountHint) accountHint.textContent = record?.id ? "Editando conta selecionada." : "Preencha os campos da nova conta.";
      window.requestAnimationFrame(() => {
        const firstField = clientAccountFormFields.apelido || clientAccountFormFields.tipo;
        if (firstField && typeof firstField.focus === "function") {
          firstField.focus();
        }
      });
    }

    function renderAccounts() {
      if (!accountList || !accountEmpty) {
        return;
      }

      const accounts = Array.isArray(state.accounts) ? state.accounts.map(normalizeAccount) : [];
      const loading = state.certificateLoading && Boolean(state.activeClientId);
      const accountsMode = state.activeClientMode === "accounts";
      const canManage = Boolean(state.activeClientId) && accountsMode && !loading;
      accountList.innerHTML = accounts.map((account) => {
        const bankVisual = getAccountVisual(account);
        const bankLabel = account.tipo === "CONTABIL" ? "Conta contábil" : bankVisual.label;
        const accountLabel = account.tipo === "CONTABIL"
          ? (account.codigo_contabil || account.descricao_contabil || "—")
          : (account.numero || "—");
        const statusLabel = account.ativo ? "Ativa" : "Inativa";
        const statusClass = account.ativo ? "status-ok" : "status-error";

        return `
          <article class="client-account-item" data-client-account-id="${escapeHtml(account.id)}">
            <div class="client-account-item-head">
              <div class="client-account-main">
                <span class="client-bank-logo client-bank-logo--${escapeHtml(bankVisual.className)}" title="${escapeHtml(bankVisual.label)}" aria-hidden="true">${escapeHtml(bankVisual.initials)}</span>
                <div class="client-account-item-title">
                  <strong>${escapeHtml(bankLabel)}</strong>
                  <span>Agência: ${escapeHtml(account.agencia || "—")}</span>
                  <span>Conta: ${escapeHtml(accountLabel)}</span>
                </div>
              </div>
              <span class="client-related-badge ${statusClass}">${statusLabel}</span>
            </div>
            ${account.observacoes ? `<div class="client-account-item-meta">${escapeHtml(account.observacoes)}</div>` : ""}
            ${canManage ? `
              <div class="client-account-item-actions">
                <button type="button" class="secondary" data-client-account-edit>Editar</button>
                <button type="button" class="secondary" data-client-account-toggle>${account.ativo ? "Desativar" : "Ativar"}</button>
              </div>
            ` : ""}
          </article>
        `;
      }).join("");

      const hasAccounts = accounts.length > 0;
      if (loading && !hasAccounts) {
        accountEmpty.hidden = false;
        accountEmpty.textContent = "Carregando contas vinculadas...";
      } else {
        accountEmpty.hidden = hasAccounts || !state.activeClientId;
        accountEmpty.textContent = state.activeClientId
          ? "Ainda não há contas vinculadas a este cliente."
          : "Salve o cliente para começar a vincular contas.";
      }

      if (accountHint) {
        if (loading) {
          accountHint.textContent = "Carregando contas vinculadas...";
        } else {
          accountHint.textContent = state.activeClientId
            ? (canManage ? "Gerencie as contas vinculadas abaixo." : "Abra a área de bancos para gerenciar contas vinculadas.")
            : "Salve o cliente para começar a vincular contas.";
        }
      }

      if (!hasAccounts && state.activeClientId && accountsMode && !state.accountEditingId && accountForm?.hidden) {
        setAccountFormVisible(false);
      }

      updateRelatedControls();
    }

    function renderCertificate() {
      if (!certificateStatus || !certificateMeta) {
        return;
      }

      const certificate = state.certificate;
      const loading = state.certificateLoading && Boolean(state.activeClientId);
      const certificateMode = state.activeClientMode === "view" || state.activeClientMode === "edit";
      const canManage = Boolean(state.activeClientId) && certificateMode && !loading;

      if (loading) {
        certificateStatus.textContent = "Carregando certificado...";
        certificateMeta.innerHTML = `
          <strong>Carregando dados relacionados</strong>
          <span>Aguarde enquanto o certificado digital do cliente é carregado.</span>
        `;
        if (certificateDelete) certificateDelete.hidden = true;
        if (certificateInput) certificateInput.disabled = true;
        if (certificateSubmit) certificateSubmit.disabled = true;
        if (certificateClear) certificateClear.disabled = true;
        updateRelatedControls();
        return;
      }

      if (!state.activeClientId) {
        certificateStatus.textContent = "Salve o cliente";
        certificateMeta.innerHTML = `
          <strong>Nenhum certificado enviado</strong>
          <span>Salve o cliente para enviar o certificado digital.</span>
        `;
        if (certificateDelete) certificateDelete.hidden = true;
        if (certificateInput) certificateInput.disabled = true;
        if (certificateSubmit) certificateSubmit.disabled = true;
        if (certificateClear) certificateClear.disabled = true;
        return;
      }

      if (!certificate) {
        certificateStatus.textContent = canManage ? "Sem certificado" : "Visualização";
        certificateMeta.innerHTML = `
          <strong>Nenhum certificado enviado</strong>
          <span>Anexe um arquivo .pfx ou .p12 para manter o certificado disponível em armazenamento privado.</span>
        `;
        if (certificateDelete) certificateDelete.hidden = true;
        if (certificateInput) certificateInput.disabled = !canManage;
        if (certificateSubmit) certificateSubmit.disabled = !canManage;
        if (certificateClear) certificateClear.disabled = !canManage;
        return;
      }

      const normalized = normalizeCertificate(certificate);
      const fileName = normalized.arquivo_original || "certificado digital";
      const dateLabel = normalized.atualizado_em ? new Date(normalized.atualizado_em).toLocaleDateString("pt-BR") : "—";
      const sizeLabel = normalized.tamanho_formatado || formatCertificateSize(normalized.tamanho_bytes);

      certificateStatus.textContent = normalized.ativo ? "Certificado ativo" : "Certificado inativo";
      certificateMeta.innerHTML = `
        <strong>${escapeHtml(fileName)}</strong>
        <span>${escapeHtml(normalized.tipo_arquivo_label || normalized.tipo_arquivo)} · ${escapeHtml(sizeLabel)} · atualizado em ${escapeHtml(dateLabel)}</span>
      `;

      if (certificateDelete) {
        certificateDelete.hidden = !canManage;
      }

      if (certificateInput) certificateInput.disabled = !canManage;
      if (certificateSubmit) certificateSubmit.disabled = !canManage;
      if (certificateClear) certificateClear.disabled = !canManage;

      updateRelatedControls();
    }

    async function loadClientRelations(clientId, options = {}) {
      const includeAccounts = options.includeAccounts !== false;
      const includeCertificate = options.includeCertificate !== false;

      if (!clientId) {
        state.accounts = [];
        state.certificate = null;
        state.certificateLoading = false;
        renderAccounts();
        renderCertificate();
        return;
      }

      const requestToken = ++state.relationRequestToken;
      state.accounts = [];
      state.certificate = null;
      state.certificateLoading = true;
      updateRelatedControls();
      renderAccounts();
      renderCertificate();

      try {
        const requests = [];
        if (includeAccounts) {
          requests.push(apiRequest(session, `/contas-clientes/?cliente=${clientId}`));
        }
        if (includeCertificate) {
          requests.push(apiRequest(session, `/certificados-clientes/?cliente=${clientId}`));
        }

        const responses = await Promise.all(requests);
        const accountsPayload = includeAccounts ? responses.shift() : [];
        const certificatePayload = includeCertificate ? responses.shift() : [];

        if (requestToken !== state.relationRequestToken || clientId !== state.activeClientId) {
          return;
        }

        state.accounts = includeAccounts
          ? (Array.isArray(accountsPayload) ? accountsPayload : (accountsPayload?.results || []))
          : [];
        if (includeCertificate) {
          const certRecords = Array.isArray(certificatePayload) ? certificatePayload : (certificatePayload?.results || []);
          state.certificate = certRecords[0] || null;
        } else {
          state.certificate = null;
        }
      } catch (error) {
        if (requestToken !== state.relationRequestToken || clientId !== state.activeClientId) {
          return;
        }

        state.accounts = [];
        state.certificate = null;
        logWarn("Falha ao carregar contas ou certificado do cliente.", error);
        if (accountHint) {
          accountHint.textContent = "Não foi possível carregar contas vinculadas agora.";
        }
      } finally {
        if (requestToken !== state.relationRequestToken || clientId !== state.activeClientId) {
          return;
        }

        state.certificateLoading = false;
        updateRelatedControls();
        renderAccounts();
        renderCertificate();

        if (state.activeClientMode === "accounts" && accountNewButton && !accountNewButton.disabled && typeof accountNewButton.focus === "function") {
          accountNewButton.focus();
        }
      }
    }

    async function toggleClientStatus() {
      const client = getActiveClientRecord();
      if (!client?.id) {
        return;
      }

      const nextStatus = getClientStatusToggleValue(client);

      try {
        const saved = await apiRequest(session, `/clientes/${client.id}/`, {
          method: "PATCH",
          body: { situacao: nextStatus },
        });

        const normalized = normalizeClientRecord(saved || { ...client, situacao: nextStatus });
        state.activeClientRecord = normalized;

        const listRecord = state.clients.find((item) => item.id === normalized.id);
        if (listRecord) {
          Object.assign(listRecord, normalized);
        }

        syncClientModalHeader(state.activeClientMode);
        renderClientBasicSummary(normalized);
        updateDashboardStats(state.clients);
        renderState();
      } catch (error) {
        logError("Falha ao atualizar a situação do cliente.", error);
      }
    }

    async function saveAccount() {
      if (!accountForm || !state.activeClientId) {
        return;
      }

      if (accountFormSubmit) accountFormSubmit.disabled = true;
      try {
        const banco = getSelectedAccountBank();

        if (!banco) {
          throw new Error("Selecione um banco ou escolha Outro para digitar.");
        }

        const payload = {
          cliente: state.activeClientId,
          tipo: "BANCARIA",
          apelido: String(clientAccountFormFields.apelido?.value || "").trim(),
          banco,
          agencia: String(clientAccountFormFields.agencia?.value || "").trim(),
          numero: String(clientAccountFormFields.numero?.value || "").trim(),
          codigo_contabil: String(clientAccountFormFields.codigo_contabil?.value || "").trim(),
          observacoes: String(clientAccountFormFields.observacoes?.value || "").trim(),
        };

        const path = state.accountEditingId ? `/contas-clientes/${state.accountEditingId}/` : "/contas-clientes/";
        await apiRequest(session, path, {
          method: state.accountEditingId ? "PATCH" : "POST",
          body: payload,
        });

        state.accountEditingId = null;
        resetAccountForm();
        await loadClientRelations(state.activeClientId, { includeCertificate: false });
        if (accountHint) accountHint.textContent = "Conta salva com sucesso.";
      } catch (error) {
        if (accountHint) accountHint.textContent = error?.message || "Falha ao salvar a conta.";
        logError("Falha ao salvar conta vinculada.", error);
      } finally {
        if (accountFormSubmit) accountFormSubmit.disabled = !isClientReadOnlyMode() || !state.activeClientId;
      }
    }

    async function toggleAccountStatus(accountId, active) {
      if (!accountId) {
        return;
      }

      try {
        await apiRequest(session, `/contas-clientes/${accountId}/`, {
          method: "PATCH",
          body: { ativo: active },
        });
        await loadClientRelations(state.activeClientId, { includeCertificate: false });
      } catch (error) {
        logError("Falha ao atualizar o status da conta.", error);
      }
    }

    async function submitCertificate() {
      if (!state.activeClientId || !certificateInput) {
        return;
      }

      const file = Array.from(certificateInput.files || [])[0];
      if (!file) {
        if (certificateStatus) certificateStatus.textContent = "Selecione um arquivo.";
        return;
      }

      const formData = new FormData();
      formData.append("cliente", state.activeClientId);
      formData.append("arquivo", file);

      if (certificateSubmit) certificateSubmit.disabled = true;

      try {
        const path = state.certificate?.id ? `/certificados-clientes/${state.certificate.id}/` : "/certificados-clientes/";
        const method = state.certificate?.id ? "PATCH" : "POST";
        const saved = await apiRequest(session, path, {
          method,
          body: formData,
        });

        state.certificate = saved;
        certificateInput.value = "";
        renderCertificate();
        if (certificateStatus) certificateStatus.textContent = "Certificado salvo com sucesso.";
      } catch (error) {
        if (certificateStatus) certificateStatus.textContent = error?.message || "Falha ao enviar o certificado.";
        logError("Falha ao enviar certificado digital.", error);
      } finally {
        if (certificateSubmit) {
          const certificateMode = state.activeClientMode === "view" || state.activeClientMode === "edit";
          certificateSubmit.disabled = !certificateMode || !state.activeClientId;
        }
      }
    }

    async function removeCertificate() {
      if (!state.certificate?.id) {
        return;
      }

      if (!window.confirm("Remover este certificado digital?")) {
        return;
      }

      try {
        await apiRequest(session, `/certificados-clientes/${state.certificate.id}/`, { method: "DELETE" });
        state.certificate = null;
        if (certificateInput) certificateInput.value = "";
        renderCertificate();
      } catch (error) {
        logError("Falha ao remover certificado digital.", error);
      }
    }

    if (accountNewButton) {
      accountNewButton.addEventListener("click", () => openAccountForm());
    }

    if (accountFormCancel) {
      accountFormCancel.addEventListener("click", () => {
        resetAccountForm();
      });
    }

    if (modalQuickEdit) {
      modalQuickEdit.addEventListener("click", () => {
        const client = getActiveClientRecord();
        if (client) {
          openModal(client, "edit");
        }
      });
    }

    if (modalQuickToggle) {
      modalQuickToggle.addEventListener("click", () => {
        toggleClientStatus();
      });
    }

    if (accountType) {
      accountType.addEventListener("change", syncAccountTypeFields);
    }

    if (clientAccountFormFields.bankSelect) {
      clientAccountFormFields.bankSelect.addEventListener("change", syncAccountBankField);
    }

    if (clientAccountFormFields.codigo_contabil) {
      clientAccountFormFields.codigo_contabil.addEventListener("input", () => {
        syncCodigoContabilBadge(clientAccountFormFields.codigo_contabil);
      });
    }

    if (clientAccountFormFields.banco) {
      clientAccountFormFields.banco.addEventListener("input", () => {
        if (clientAccountFormFields.bankSelect?.value === ACCOUNT_BANK_OTHER_VALUE) {
          lastCustomBankValue = String(clientAccountFormFields.banco.value || "").trim();
        }
      });
    }

    if (accountForm) {
      accountForm.addEventListener("submit", (event) => {
        event.preventDefault();
        saveAccount();
      });
    }

    if (accountList) {
      accountList.addEventListener("click", (event) => {
        const editButton = event.target.closest("[data-client-account-edit]");
        const toggleButton = event.target.closest("[data-client-account-toggle]");
        const item = event.target.closest("[data-client-account-id]");
        if (!item) {
          return;
        }

        const account = state.accounts.find((record) => String(record?.id || "") === item.dataset.clientAccountId);
        if (!account) {
          return;
        }

        if (editButton) {
          openAccountForm(account);
          return;
        }

        if (toggleButton) {
          toggleAccountStatus(account.id, !account.ativo);
        }
      });
    }

    if (certificateInput) {
      certificateInput.addEventListener("change", () => {
        const [file] = Array.from(certificateInput.files || []);
        if (!file) {
          if (certificateStatus) certificateStatus.textContent = state.certificate ? "Certificado pronto para substituição." : "Selecione um arquivo.";
          return;
        }

        if (certificateStatus) {
          certificateStatus.textContent = `Arquivo selecionado: ${file.name}`;
        }
      });
    }

    if (certificateSubmit) {
      certificateSubmit.addEventListener("click", submitCertificate);
    }

    if (certificateClear) {
      certificateClear.addEventListener("click", () => {
        if (certificateInput) {
          certificateInput.value = "";
        }
        if (certificateStatus) {
          certificateStatus.textContent = state.certificate ? "Certificado pronto para substituição." : "Selecione um arquivo.";
        }
      });
    }

    if (certificateDelete) {
      certificateDelete.addEventListener("click", removeCertificate);
    }

    function setClientFilterMenuOpen(isOpen) {
      if (filterMenu) {
        filterMenu.hidden = !isOpen;
      }

      if (filterRoot) {
        filterRoot.classList.toggle("is-open", Boolean(isOpen));
      }

      if (filterToggle) {
        filterToggle.setAttribute("aria-expanded", String(Boolean(isOpen)));
      }
    }

    function syncClientFilterControls() {
      const label = getClientStatusFilterLabel(state.statusFilter);

      if (filterLabel) {
        filterLabel.textContent = label;
      }

      if (filterToggle) {
        filterToggle.setAttribute("aria-label", `Filtro de clientes: ${label}`);
        filterToggle.classList.toggle("is-filtered", state.statusFilter !== "all");
      }

      filterOptions.forEach((option) => {
        const isActive = option.dataset.clientFilterOption === state.statusFilter;
        option.classList.toggle("is-active", isActive);
        option.setAttribute("aria-pressed", String(isActive));
      });
    }

    function setClientFilter(filterKey, { render = true, closeMenu = true } = {}) {
      const nextFilter = Object.prototype.hasOwnProperty.call(CLIENT_STATUS_FILTERS, filterKey) ? filterKey : "all";
      state.statusFilter = nextFilter;
      syncClientFilterControls();

      if (closeMenu) {
        setClientFilterMenuOpen(false);
      }

      if (render) {
        renderState();
      }
    }

    function matchesClientFilter(client) {
      if (state.statusFilter === "all") {
        return true;
      }

      return (client.statusKey || normalizeClientStatus(client.situacao_label || client.situacao)) === state.statusFilter;
    }

    function switchTab(tabName) {
      tabButtons.forEach((btn) => {
        const active = btn.dataset.clientTab === tabName;
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-selected", active ? "true" : "false");
      });
      tabPanes.forEach((pane) => {
        pane.hidden = pane.dataset.clientTabPane !== tabName;
        pane.classList.toggle("is-active", pane.dataset.clientTabPane === tabName);
      });
    }

    function resetModalState() {
      state.activeClientId = null;
      state.activeClientRecord = null;
      state.activeClientMode = "create";
      state.accounts = [];
      state.accountEditingId = null;
      state.certificate = null;
      state.certificateLoading = false;
      form.reset();
      if (modalTitle) {
        modalTitle.textContent = "Novo cliente";
      }
      if (eyebrow) {
        eyebrow.textContent = "NOVO CLIENTE";
      }
      if (form.elements.data_inicio) {
        form.elements.data_inicio.value = getTodayDateValue();
      }
      if (form.elements.situacao) {
        form.elements.situacao.value = "ATIVO";
      }
      setClientFormMode("create");
      resetAccountForm();
      if (certificateInput) {
        certificateInput.value = "";
      }
      if (certificateDelete) {
        certificateDelete.hidden = true;
      }
      renderAccounts();
      renderCertificate();
      if (historicoSection) historicoSection.hidden = true;
      if (historicoList) historicoList.innerHTML = "";
      if (extratoHistoricoList) extratoHistoricoList.innerHTML = "";
      switchTab("dados");
    }

    function openModal(record = null, mode = "create") {
      const normalizedRecord = record ? normalizeClientRecord(record) : null;
      const readOnlyMode = isClientReadOnlyMode(mode);

      state.activeClientId = record?.id || null;
      state.activeClientRecord = normalizedRecord;
      state.activeClientMode = mode;
      state.accounts = [];
      state.certificate = null;
      state.certificateLoading = Boolean(record?.id && (mode === "view" || mode === "edit" || mode === "accounts"));
      state.accountEditingId = null;
      form.reset();

      if (record) {
        setClientFormData(form, record);
      } else {
        if (form.elements.data_inicio) {
          form.elements.data_inicio.value = getTodayDateValue();
        }

        if (form.elements.situacao) {
          form.elements.situacao.value = "ATIVO";
        }
      }

      if (form.elements.codigo) {
        form.elements.codigo.value = record?.codigo || "";
      }

      if (modalTitle) {
        modalTitle.textContent = readOnlyMode && normalizedRecord
          ? normalizedRecord.nome || "Cliente"
          : mode === "edit"
            ? "Editar cliente"
            : "Novo cliente";
      }
      if (eyebrow) {
        const eyebrowMap = { view: "VISUALIZAR", accounts: "BANCOS E CONTAS", edit: "EDITAR CLIENTE", create: "NOVO CLIENTE" };
        eyebrow.textContent = eyebrowMap[mode] || "CLIENTE";
      }

      setClientFormMode(mode);
      renderClientBasicSummary(mode === "accounts" ? normalizedRecord : null);
      resetAccountForm();
      if (certificateInput) {
        certificateInput.value = "";
      }

      const showTabs = tabButtons.length > 0 && mode === "view" && !!record?.id;
      if (tabRegras) tabRegras.hidden = !showTabs;
      if (tabExtratos) tabExtratos.hidden = !showTabs;

      if (showTabs) {
        loadClientHistorico(record.id);
        loadExtratoHistorico(record.id);
      } else if (historicoSection) {
        historicoSection.hidden = true;
      }

      // Volta para a aba Dados sempre que abre
      switchTab("dados");

      if (summaryPanel) summaryPanel.hidden = true;
      if (typeof modal.showModal === "function") {
        modal.showModal();
      } else {
        modal.setAttribute("open", "open");
      }

      if (record?.id && mode === "accounts") {
        loadClientRelations(record.id, { includeAccounts: true, includeCertificate: false });
      } else if (record?.id && (mode === "view" || mode === "edit")) {
        loadClientRelations(record.id, { includeAccounts: false, includeCertificate: true });
      } else {
        state.accounts = [];
        state.certificate = null;
        renderAccounts();
        renderCertificate();
      }

      window.requestAnimationFrame(() => {
        if (mode === "accounts" && accountNewButton && typeof accountNewButton.focus === "function") {
          accountNewButton.focus();
          return;
        }

        if (readOnlyMode && modalQuickEdit && typeof modalQuickEdit.focus === "function") {
          modalQuickEdit.focus();
          return;
        }

        const firstField = form.elements.nome || form.elements.codigo;
        if (!readOnlyMode && firstField && typeof firstField.focus === "function") {
          firstField.focus();
        }
      });
    }

    async function loadClientHistorico(clientId) {
      if (!historicoList || !historicoLoading || !historicoEmpty) return;
      historicoLoading.hidden = false;
      historicoEmpty.hidden = true;
      historicoList.innerHTML = "";
      if (historicoCount) historicoCount.textContent = "";

      try {
        const data = await apiRequest(session, `/conciliador-regras/?empresa=${clientId}&ativo=true`);
        const regras = Array.isArray(data) ? data : (data?.results || []);

        historicoLoading.hidden = true;

        if (!regras.length) {
          historicoEmpty.hidden = false;
          return;
        }

        if (historicoCount) historicoCount.textContent = `${regras.length} regra${regras.length > 1 ? "s" : ""}`;

        // Ordena por data de criação mais recente
        regras.sort((a, b) => new Date(b.criado_em) - new Date(a.criado_em));

        historicoList.innerHTML = regras.map((r) => {
          const dt = r.criado_em ? new Date(r.criado_em).toLocaleDateString("pt-BR") : "—";
          const atualizado = r.atualizado_em && r.atualizado_em !== r.criado_em
            ? ` · atualizado ${new Date(r.atualizado_em).toLocaleDateString("pt-BR")}` : "";
          const debito = r.conta_debito ? `D: ${escapeHtml(r.conta_debito)}` : "";
          const credito = r.conta_credito ? `C: ${escapeHtml(r.conta_credito)}` : "";
          const hist = r.codigo_historico ? `H: ${escapeHtml(r.codigo_historico)}` : "";
          const codigos = [debito, credito, hist].filter(Boolean).join(" · ");
          return `
            <li class="client-historico-item">
              <div class="client-historico-item-head">
                <span class="client-historico-item-nome">${escapeHtml(r.nome || r.texto_referencia || "—")}</span>
                <span class="client-historico-item-data">${dt}${atualizado}</span>
              </div>
              ${codigos ? `<div class="client-historico-item-codigos">${codigos}</div>` : ""}
            </li>
          `;
        }).join("");
      } catch (err) {
        historicoLoading.hidden = true;
        historicoEmpty.hidden = false;
        historicoEmpty.textContent = "Falha ao carregar regras.";
        logWarn("Falha ao carregar histórico do cliente.", err);
      }
    }

    async function loadExtratoHistorico(clientId) {
      if (!extratoHistoricoList || !extratoHistoricoEmpty) return;
      extratoHistoricoEmpty.hidden = true;
      extratoHistoricoList.innerHTML = "<li class=\"client-extrato-hist-loading\">Carregando...</li>";
      if (extratoHistoricoCount) extratoHistoricoCount.textContent = "";

      try {
        const data = await apiRequest(session, `/extrato-historico/?empresa=${clientId}`);
        const historicos = data?.historicos || [];

        extratoHistoricoList.innerHTML = "";

        if (!historicos.length) {
          extratoHistoricoEmpty.hidden = false;
          return;
        }

        if (extratoHistoricoCount) {
          extratoHistoricoCount.textContent = `${historicos.length} extrato${historicos.length > 1 ? "s" : ""}`;
        }

        extratoHistoricoList.innerHTML = historicos.map((h) => {
          const dt = h.criado_em ? new Date(h.criado_em).toLocaleDateString("pt-BR") : "—";
          const banco = h.banco ? escapeHtml(h.banco.charAt(0).toUpperCase() + h.banco.slice(1)) : "—";
          const periodo = (h.periodo_inicio && h.periodo_fim)
            ? `${formatDateBRFromISO(h.periodo_inicio)} a ${formatDateBRFromISO(h.periodo_fim)}`
            : (h.periodo_inicio ? formatDateBRFromISO(h.periodo_inicio) : "—");
          return `
            <li class="client-extrato-hist-item" data-hist-id="${escapeHtml(h.id)}">
              <div class="client-extrato-hist-head">
                <span class="client-extrato-hist-banco">${banco}</span>
                <span class="client-extrato-hist-data">${dt}</span>
              </div>
              <div class="client-extrato-hist-meta">
                <span>${periodo}</span>
                <span>${h.total_lancamentos} lançamento${h.total_lancamentos !== 1 ? "s" : ""}</span>
              </div>
              <div class="client-extrato-hist-actions">
                <button type="button" class="regras-auto-item-btn client-extrato-hist-del" data-hist-del-id="${escapeHtml(h.id)}">Excluir</button>
              </div>
            </li>
          `;
        }).join("");

        // Botões excluir
        extratoHistoricoList.querySelectorAll("[data-hist-del-id]").forEach((btn) => {
          btn.addEventListener("click", async () => {
            if (!confirm("Excluir este histórico de extrato?")) return;
            try {
              await apiRequest(session, `/extrato-historico/${btn.dataset.histDelId}/`, { method: "DELETE" });
              loadExtratoHistorico(clientId);
            } catch (err) {
              alert("Falha ao excluir: " + (err.message || ""));
            }
          });
        });

      } catch (err) {
        extratoHistoricoList.innerHTML = "";
        extratoHistoricoEmpty.hidden = false;
        extratoHistoricoEmpty.textContent = "Falha ao carregar histórico.";
        logWarn("Falha ao carregar histórico de extratos.", err);
      }
    }

    function formatDateBRFromISO(iso) {
      if (!iso) return "—";
      const [y, m, d] = String(iso).split("-");
      return `${d}/${m}/${y}`;
    }

    function closeModal() {
      if (typeof modal.close === "function" && modal.open) {
        modal.close();
      } else {
        modal.removeAttribute("open");
      }
      if (summaryPanel) summaryPanel.hidden = false;
      resetModalState();
      panel.querySelectorAll("[data-client-item].is-selected").forEach((el) => el.classList.remove("is-selected"));
    }

    function renderState() {
      const query = normalizeSearchTerm(searchInput?.value || "");
      const visibleClients = state.clients.filter((client) => {
        const matchesSearch = !query || client.searchIndex.includes(query);
        const matchesFilter = matchesClientFilter(client);
        return matchesSearch && matchesFilter;
      });
      const hasStatusFilter = state.statusFilter !== "all";

      list.replaceChildren(...visibleClients.map(createClientCard));

      if (countLabel) {
        countLabel.textContent = (query || hasStatusFilter) && state.clients.length > 0
          ? `Mostrando ${visibleClients.length} de ${state.clients.length} clientes`
          : formatClientCount(state.clients.length);
      }

      updateClientOverviewSummary(state.clients, {
        total: summaryTotal,
        active: summaryActive,
        review: summaryReview,
        paused: summaryPaused,
      });

      if (emptyState) {
        if (state.error) {
          emptyState.hidden = false;
          emptyState.textContent = state.error;
        } else if (state.clients.length === 0) {
          emptyState.hidden = false;
          emptyState.textContent = "Nenhum cliente cadastrado ainda. Clique em 'Novo cliente' para começar.";
        } else if (visibleClients.length === 0) {
          emptyState.hidden = false;
          if (query && hasStatusFilter) {
            emptyState.textContent = "Nenhum cliente encontrado para essa pesquisa e o filtro selecionado.";
          } else if (query) {
            emptyState.textContent = "Nenhum cliente encontrado para essa pesquisa.";
          } else if (hasStatusFilter) {
            emptyState.textContent = `Nenhum cliente encontrado para o filtro ${getClientStatusFilterLabel(state.statusFilter).toLowerCase()}.`;
          } else {
            emptyState.textContent = "Nenhum cliente encontrado.";
          }
        } else {
          emptyState.hidden = true;
        }
      }
    }

    async function reloadClients() {
      state.loading = true;
      try {
        state.clients = await loadClientsFromApi(session);
        state.error = "";
        updateDashboardStats(state.clients);
      } catch (error) {
        state.clients = [];
        state.error = error?.message || "Não foi possível carregar os clientes.";
        logError("Falha ao carregar clientes.", { message: state.error });
      } finally {
        state.loading = false;
        renderState();
      }
    }

    async function saveClient() {
      const payload = getClientFormData(form);
      if (!payload.nome || !payload.cpf_cnpj) {
        throw new Error("Informe nome e CPF/CNPJ.");
      }

      if (!payload.data_inicio) {
        payload.data_inicio = getTodayDateValue();
      }

      if (state.activeClientId) {
        await apiRequest(session, `/clientes/${state.activeClientId}/`, {
          method: "PATCH",
          body: payload,
        });
      } else {
        await apiRequest(session, "/clientes/", {
          method: "POST",
          body: payload,
        });
      }

      await reloadClients();
      closeModal();
    }

    addButtons.forEach((button) => {
      button.addEventListener("click", () => openModal());
    });

    closeButtons.forEach((button) => {
      button.addEventListener("click", closeModal);
    });

    tabButtons.forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.clientTab));
    });

    if (searchInput) {
      searchInput.addEventListener("input", renderState);
    }

    if (filterToggle) {
      filterToggle.addEventListener("click", () => {
        const shouldOpen = filterMenu ? filterMenu.hidden : false;
        setClientFilterMenuOpen(shouldOpen);
      });
    }

    filterOptions.forEach((option) => {
      option.addEventListener("click", () => {
        setClientFilter(option.dataset.clientFilterOption || "all");
      });
    });

    document.addEventListener("click", (event) => {
      if (!filterRoot || filterRoot.contains(event.target)) {
        return;
      }

      setClientFilterMenuOpen(false);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        setClientFilterMenuOpen(false);
      }
    });

    syncClientFilterControls();
    setClientFilterMenuOpen(false);

    modal.addEventListener("close", resetModalState);
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        closeModal();
      }
    });
    list.addEventListener("click", (event) => {
      const viewButton = event.target.closest("[data-client-view]");
      const accountsButton = event.target.closest("[data-client-accounts]");
      const editButton = event.target.closest("[data-client-edit]");
      const deleteButton = event.target.closest("[data-client-delete]");

      if (viewButton) {
        const clientItem = viewButton.closest("[data-client-item]");
        if (!clientItem) {
          return;
        }

        const record = state.clients.find((client) => client.id === clientItem.dataset.clientId);
        if (record) {
          panel.querySelectorAll("[data-client-item].is-selected").forEach((el) => el.classList.remove("is-selected"));
          clientItem.classList.add("is-selected");
          openModal(record, "view");
        }
        return;
      }

      if (accountsButton) {
        const clientItem = accountsButton.closest("[data-client-item]");
        if (!clientItem) {
          return;
        }

        const record = state.clients.find((client) => client.id === clientItem.dataset.clientId);
        if (record) {
          openModal(record, "accounts");
        }
        return;
      }

      if (editButton) {
        const clientItem = editButton.closest("[data-client-item]");
        if (!clientItem) {
          return;
        }

        const record = state.clients.find((client) => client.id === clientItem.dataset.clientId);
        if (record) {
          openModal(record, "edit");
        }
        return;
      }

      if (deleteButton) {
        const clientItem = deleteButton.closest("[data-client-item]");
        if (!clientItem) {
          return;
        }

        const record = state.clients.find((client) => client.id === clientItem.dataset.clientId);
        const clientName = record?.nome || clientItem.dataset.clientNome || "este cliente";

        if (window.confirm(`Excluir ${clientName}?`)) {
          if (state.activeClientId === clientItem.dataset.clientId) {
            closeModal();
          }

          apiRequest(session, `/clientes/${clientItem.dataset.clientId}/`, { method: "DELETE" })
            .then(() => reloadClients())
            .catch((error) => {
              state.error = error?.message || "Falha ao excluir cliente.";
              logError("Falha ao excluir cliente.", { message: state.error });
              renderState();
            });
        }
      }
    });

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (state.activeClientMode === "view") {
        closeModal();
        return;
      }
      saveClient().catch((error) => {
        state.error = error?.message || "Falha ao salvar cliente.";
        logError("Falha ao salvar cliente.", { message: state.error });
        renderState();
      });
    });

    reloadClients();
  }

  // ── Perfis de Conciliação ──────────────────────────────────────────────────

  function setupPerfisCrud(session) {
    const panel = document.querySelector('[data-panel-view-section="perfis"]');
    if (!panel) {
      return;
    }

    const modal = panel.querySelector("[data-perfis-modal]");
    const form = panel.querySelector("[data-perfis-form]");
    const modalTitle = panel.querySelector("[data-perfis-modal-title]");
    const modalSub = panel.querySelector("[data-perfis-modal-sub]");
    const submitButton = panel.querySelector("[data-perfis-submit]");
    const formError = panel.querySelector("[data-perfis-form-error]");
    const tbody = panel.querySelector("[data-perfis-tbody]");
    const emptyRow = panel.querySelector("[data-perfis-empty]");
    const novoButton = panel.querySelector("[data-perfis-novo]");
    const duplicarButton = panel.querySelector("[data-perfis-duplicar]");
    const manageSearch = panel.querySelector("[data-perfis-manage-search]");
    const filterSearch = panel.querySelector("[data-perfis-filter-search]");
    const closeButtons = Array.from(panel.querySelectorAll("[data-perfis-modal-close]"));
    const selectEscritorio = panel.querySelector("[data-perfis-select-escritorio]");
    const selectEmpresa = panel.querySelector("[data-perfis-select-empresa]");

    const state = {
      perfis: [],
      activeId: null,
      loading: false,
    };

    // ── Carrega escritórios e empresas para os selects ────────────────────
    async function loadSelects() {
      try {
        const [escritorios, empresas] = await Promise.all([
          apiRequest(session, "/escritorios/"),
          apiRequest(session, "/clientes/"),
        ]);

        const escs = Array.isArray(escritorios) ? escritorios : (escritorios?.results || []);
        const emps = Array.isArray(empresas) ? empresas : (empresas?.results || []);

        if (selectEscritorio) {
          const cur = selectEscritorio.value;
          selectEscritorio.innerHTML = '<option value="">Selecione o escritório...</option>' +
            escs.map((e) => `<option value="${escapeHtml(e.id)}">${escapeHtml(e.nome)}</option>`).join("");
          if (cur) selectEscritorio.value = cur;
          // auto-seleciona se houver apenas 1
          if (escs.length === 1 && !selectEscritorio.value) selectEscritorio.value = escs[0].id;
        }

        if (selectEmpresa) {
          const cur = selectEmpresa.value;
          selectEmpresa.innerHTML = '<option value="">Selecione a empresa...</option>' +
            emps.map((e) => `<option value="${escapeHtml(e.id)}">${escapeHtml(e.nome)} ${e.cpf_cnpj ? `(${escapeHtml(e.cpf_cnpj)})` : ""}</option>`).join("");
          if (cur) selectEmpresa.value = cur;
        }
      } catch (err) {
        logWarn("Falha ao carregar selects de escritório/empresa para perfis.", err);
      }
    }

    function showFormError(message) {
      if (formError) {
        formError.textContent = message;
        formError.hidden = !message;
      }
    }

    function openModal(perfil = null) {
      const isNew = !perfil || !perfil.id;
      state.activeId = perfil?.id || null;
      form.reset();
      showFormError("");

      // Garante que os selects estão populados antes de abrir
      loadSelects().then(() => {
        if (perfil) {
          form.elements.id.value = perfil.id || "";
          form.elements.nome.value = perfil.nome || "";
          form.elements.descricao.value = perfil.descricao || "";
          form.elements.conta_bancaria.value = perfil.conta_bancaria || "";
          form.elements.codigo_historico.value = perfil.codigo_historico || "";
          form.elements.codigo_empresa.value = perfil.codigo_empresa || "";
          form.elements.cnpj.value = perfil.cnpj || "";
          if (selectEscritorio && perfil.escritorio) selectEscritorio.value = perfil.escritorio;
          if (selectEmpresa && perfil.empresa) selectEmpresa.value = perfil.empresa;
        }
      });

      if (isNew) {
        form.elements.id.value = "";
        if (modalTitle) modalTitle.textContent = "Novo Perfil";
        if (modalSub) modalSub.textContent = "Preencha os dados para criar um novo perfil de configuração.";
        if (submitButton) submitButton.textContent = "+ Criar Perfil";
      } else {
        if (modalTitle) modalTitle.textContent = "Editar Perfil";
        if (modalSub) modalSub.textContent = "Altere os dados do perfil de configuração.";
        if (submitButton) submitButton.textContent = "Salvar Alterações";
      }

      if (typeof modal.showModal === "function" && !modal.open) {
        modal.showModal();
      } else {
        modal.setAttribute("open", "open");
      }

      window.requestAnimationFrame(() => {
        const nomeField = form.elements.nome;
        if (nomeField && typeof nomeField.focus === "function") nomeField.focus();
      });
    }

    function closeModal() {
      if (typeof modal.close === "function" && modal.open) {
        modal.close();
      } else {
        modal.removeAttribute("open");
      }
      state.activeId = null;
      form.reset();
      showFormError("");
    }

    function getSelectedRow() {
      return tbody.querySelector("tr[data-perfil-id].is-selected");
    }

    function getFormData() {
      return {
        escritorio: String(form.elements.escritorio?.value || "").trim(),
        empresa: String(form.elements.empresa?.value || "").trim(),
        nome: String(form.elements.nome?.value || "").trim(),
        descricao: String(form.elements.descricao?.value || "").trim(),
        conta_bancaria: String(form.elements.conta_bancaria?.value || "").trim(),
        codigo_historico: String(form.elements.codigo_historico?.value || "").trim(),
        codigo_empresa: String(form.elements.codigo_empresa?.value || "").trim(),
        cnpj: String(form.elements.cnpj?.value || "").trim(),
      };
    }

    function renderRows(filterQuery = "") {
      const query = normalizeSearchTerm(filterQuery);
      const rows = Array.from(tbody.querySelectorAll("tr[data-perfil-id]"));

      rows.forEach((row) => {
        const searchable = normalizeSearchTerm(row.dataset.perfilSearch || "");
        row.hidden = Boolean(query && !searchable.includes(query));
      });

      const visibleRows = rows.filter((row) => !row.hidden);
      if (emptyRow) {
        emptyRow.hidden = visibleRows.length > 0 || rows.length > 0;
      }
    }

    function buildRow(perfil) {
      const tr = document.createElement("tr");
      tr.dataset.perfilId = perfil.id;
      tr.dataset.perfilNome = perfil.nome || "";
      tr.dataset.perfilSearch = normalizeSearchTerm([
        perfil.nome,
        perfil.conta_bancaria,
        perfil.codigo_historico,
        perfil.codigo_empresa,
        perfil.cnpj,
      ].join(" "));

      const paramCount = typeof perfil.parametros_count === "number" ? perfil.parametros_count : (perfil.parametros?.length || 0);

      tr.innerHTML = `
        <td><strong>${escapeHtml(perfil.nome || "")}</strong></td>
        <td><span class="perfis-code">${escapeHtml(perfil.conta_bancaria || "–")}</span></td>
        <td>${escapeHtml(perfil.codigo_historico || "–")}</td>
        <td>${escapeHtml(perfil.codigo_empresa || "–")}</td>
        <td>
          ${paramCount > 0
            ? `<button class="perfis-param-badge" type="button" data-perfil-params>${paramCount}</button>`
            : `<span class="perfis-code" style="opacity:0.45">0</span>`
          }
        </td>
        <td>
          <div class="perfis-actions-row">
            <button class="client-action client-action-edit" type="button" data-perfil-edit aria-label="Editar perfil" title="Editar">${clientActionIcon("edit")}</button>
            <button class="client-action client-action-delete" type="button" data-perfil-delete aria-label="Excluir perfil" title="Excluir">${clientActionIcon("delete")}</button>
          </div>
        </td>
      `;

      tr.addEventListener("click", (e) => {
        if (e.target.closest("[data-perfil-edit]") || e.target.closest("[data-perfil-delete]")) return;
        const wasSelected = tr.classList.contains("is-selected");
        tbody.querySelectorAll("tr[data-perfil-id]").forEach((r) => r.classList.remove("is-selected"));
        tr.classList.toggle("is-selected", !wasSelected);
        if (duplicarButton) {
          duplicarButton.disabled = !tr.classList.contains("is-selected");
        }
      });

      return tr;
    }

    function loadPerfis() {
      apiRequest(session, "/conciliador-perfis/")
        .then((payload) => {
          state.perfis = Array.isArray(payload) ? payload : (payload?.results || []);
          // Clear existing rows
          Array.from(tbody.querySelectorAll("tr[data-perfil-id]")).forEach((r) => r.remove());

          state.perfis.forEach((perfil) => {
            tbody.insertBefore(buildRow(perfil), emptyRow || null);
          });

          renderRows();
        })
        .catch((error) => {
          logWarn("Falha ao carregar perfis de conciliação.", error);
          renderRows();
        });
    }

    // Carrega selects na inicialização
    loadSelects();

    async function savePerfil() {
      const data = getFormData();
      if (!data.escritorio) {
        showFormError("Selecione o escritório.");
        return;
      }
      if (!data.empresa) {
        showFormError("Selecione a empresa (cliente).");
        return;
      }
      if (!data.nome) {
        showFormError("O nome do perfil é obrigatório.");
        return;
      }

      if (submitButton) submitButton.disabled = true;
      showFormError("");

      try {
        let saved;
        if (state.activeId) {
          saved = await apiRequest(session, `/conciliador-perfis/${state.activeId}/`, { method: "PATCH", body: data });
          const existingRow = tbody.querySelector(`tr[data-perfil-id="${CSS.escape(state.activeId)}"]`);
          if (existingRow) {
            const updated = { ...state.perfis.find((p) => p.id === state.activeId), ...data, ...saved };
            existingRow.replaceWith(buildRow(updated));
            state.perfis = state.perfis.map((p) => (p.id === state.activeId ? updated : p));
          }
        } else {
          saved = await apiRequest(session, "/conciliador-perfis/", { method: "POST", body: data });
          state.perfis.push(saved);
          tbody.insertBefore(buildRow(saved), emptyRow || null);
        }

        renderRows(filterSearch?.value || "");
        closeModal();
      } catch (error) {
        showFormError(error?.message || "Falha ao salvar o perfil.");
      } finally {
        if (submitButton) submitButton.disabled = false;
      }
    }

    async function deletePerfil(id, nome) {
      if (!window.confirm(`Excluir o perfil "${nome}"?`)) return;
      try {
        await apiRequest(session, `/conciliador-perfis/${id}/`, { method: "DELETE" });
        const row = tbody.querySelector(`tr[data-perfil-id="${CSS.escape(id)}"]`);
        if (row) row.remove();
        state.perfis = state.perfis.filter((p) => p.id !== id);
        renderRows(filterSearch?.value || "");
      } catch (error) {
        window.alert("Falha ao excluir o perfil: " + (error?.message || "erro desconhecido"));
      }
    }

    // Events
    if (novoButton) {
      novoButton.addEventListener("click", () => openModal());
    }

    // Listener: criar perfil a partir do extrato
    document.addEventListener("extrato:criar-perfil", (e) => {
      const { nome, cnpj } = e.detail || {};
      setPanelView("perfis");
      openModal({ id: null, nome: nome || "", cnpj: cnpj || "", descricao: "", conta_bancaria: "", codigo_historico: "", codigo_empresa: "" });
    });

    if (duplicarButton) {
      duplicarButton.addEventListener("click", () => {
        const row = getSelectedRow();
        if (!row) return;
        const perfil = state.perfis.find((p) => p.id === row.dataset.perfilId);
        if (!perfil) return;
        const copy = { ...perfil, id: null, nome: `${perfil.nome} (cópia)` };
        openModal(copy);
        // clear id so we create a new record
        form.elements.id.value = "";
        state.activeId = null;
      });
    }

    closeButtons.forEach((btn) => btn.addEventListener("click", closeModal));

    modal.addEventListener("close", () => {
      state.activeId = null;
      showFormError("");
    });

    modal.addEventListener("click", (event) => {
      if (event.target === modal) closeModal();
    });

    if (filterSearch) {
      filterSearch.addEventListener("input", () => renderRows(filterSearch.value));
    }

    if (manageSearch) {
      manageSearch.addEventListener("input", () => renderRows(manageSearch.value));
    }

    tbody.addEventListener("click", (event) => {
      const editBtn = event.target.closest("[data-perfil-edit]");
      const deleteBtn = event.target.closest("[data-perfil-delete]");

      if (editBtn) {
        const row = editBtn.closest("tr[data-perfil-id]");
        if (!row) return;
        const perfil = state.perfis.find((p) => p.id === row.dataset.perfilId);
        if (perfil) openModal(perfil);
        return;
      }

      if (deleteBtn) {
        const row = deleteBtn.closest("tr[data-perfil-id]");
        if (!row) return;
        deletePerfil(row.dataset.perfilId, row.dataset.perfilNome || "este perfil");
      }
    });

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      savePerfil().catch((error) => {
        showFormError(error?.message || "Falha ao salvar o perfil.");
      });
    });

    loadPerfis();
  }

  // ── Contabilidade (Plano de Contas + Histórico Contábil) ──────────────────

  function setupContabilidadeCrud(session) {
    const panel = document.querySelector('[data-panel-view-section="contabilidade"]');
    if (!panel) return;

    // refs
    const tabButtons    = Array.from(panel.querySelectorAll("[data-contab-tab]"));
    const searchInput   = panel.querySelector("[data-contab-search]");
    const inativosChk   = panel.querySelector("[data-contab-inativos]");
    const novoBtn       = panel.querySelector("[data-contab-novo]");
    const countPlano    = panel.querySelector("[data-contab-count-plano]");
    const countHist     = panel.querySelector("[data-contab-count-historico]");
    const thead         = panel.querySelector("[data-contab-thead]");
    const tbody         = panel.querySelector("[data-contab-tbody]");
    const loadingEl     = panel.querySelector("[data-contab-loading]");
    const emptyEl       = panel.querySelector("[data-contab-empty]");
    const dialog        = panel.querySelector("[data-contab-dialog]");
    const dialogLabel   = panel.querySelector("[data-contab-dialog-label]");
    const dialogTitle   = panel.querySelector("[data-contab-dialog-title]");
    const dialogClose   = panel.querySelector("[data-contab-dialog-close]");
    const dialogCancel  = panel.querySelector("[data-contab-dialog-cancel]");
    const form          = panel.querySelector("[data-contab-form]");
    const formError     = panel.querySelector("[data-contab-form-error]");
    const fieldsPlano   = panel.querySelector("[data-contab-fields='plano']");
    const fieldsHist    = panel.querySelector("[data-contab-fields='historico']");

    const state = {
      tab: "plano",          // "plano" | "historico"
      plano: [],
      historico: [],
      showInativos: false,
      search: "",
      editingId: null,       // null = novo registro
    };

    // ── helpers ──────────────────────────────────────────────────────────────

    function setLoading(on) {
      if (loadingEl) loadingEl.hidden = !on;
      if (tbody) tbody.hidden = on;
    }

    function showError(msg) {
      if (!formError) return;
      formError.textContent = msg;
      formError.hidden = !msg;
    }

    function currentList() {
      return state.tab === "plano" ? state.plano : state.historico;
    }

    function filteredList() {
      const q = state.search.toLowerCase();
      return currentList().filter((item) => {
        if (!state.showInativos && !item.ativo) return false;
        if (!q) return true;
        const cod = String(item.codigo).toLowerCase();
        const nom = (item.nome || "").toLowerCase();
        return cod.includes(q) || nom.includes(q);
      });
    }

    // ── render ────────────────────────────────────────────────────────────────

    function renderTable() {
      if (!thead || !tbody) return;
      const list = filteredList();

      // cabeçalho dinâmico
      if (state.tab === "plano") {
        thead.innerHTML = `<tr>
          <th>Código</th><th>Classificação</th><th>Nome</th>
          <th>Tipo</th><th>Natureza</th><th>Status</th><th>Ações</th>
        </tr>`;
      } else {
        thead.innerHTML = `<tr>
          <th>Código</th><th>Nome</th><th>Grupo</th><th>Status</th><th>Ações</th>
        </tr>`;
      }

      if (list.length === 0) {
        tbody.innerHTML = "";
        if (emptyEl) emptyEl.hidden = false;
        return;
      }
      if (emptyEl) emptyEl.hidden = true;

      if (state.tab === "plano") {
        tbody.innerHTML = list.map((item) => `
          <tr class="contab-row ${item.ativo ? "" : "contab-row--inativo"}" data-contab-row-id="${item.id}">
            <td class="contab-cell-code">${escHtml(String(item.codigo))}</td>
            <td>${escHtml(item.classificacao || "—")}</td>
            <td class="contab-cell-nome">${escHtml(item.nome)}</td>
            <td>${escHtml(item.tipo || "—")}</td>
            <td>${escHtml(item.natureza || "—")}</td>
            <td>
              <span class="contab-badge ${item.ativo ? "contab-badge--ativo" : "contab-badge--inativo"}">
                ${item.ativo ? "Ativo" : "Inativo"}
              </span>
            </td>
            <td class="contab-cell-actions">
              <button class="contab-btn-edit" type="button" data-contab-edit="${item.id}" title="Editar">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                Editar
              </button>
              <button class="contab-btn-toggle" type="button" data-contab-toggle="${item.id}" data-ativo="${item.ativo}" title="${item.ativo ? "Inativar" : "Ativar"}">
                ${item.ativo
                  ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M8 12h8"/></svg> Inativar`
                  : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 8v8M8 12h8"/></svg> Ativar`
                }
              </button>
            </td>
          </tr>`).join("");
      } else {
        tbody.innerHTML = list.map((item) => `
          <tr class="contab-row ${item.ativo ? "" : "contab-row--inativo"}" data-contab-row-id="${item.id}">
            <td class="contab-cell-code">${escHtml(String(item.codigo))}</td>
            <td class="contab-cell-nome">${escHtml(item.nome)}</td>
            <td>${escHtml(item.grupo || "—")}</td>
            <td>
              <span class="contab-badge ${item.ativo ? "contab-badge--ativo" : "contab-badge--inativo"}">
                ${item.ativo ? "Ativo" : "Inativo"}
              </span>
            </td>
            <td class="contab-cell-actions">
              <button class="contab-btn-edit" type="button" data-contab-edit="${item.id}" title="Editar">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                Editar
              </button>
              <button class="contab-btn-toggle" type="button" data-contab-toggle="${item.id}" data-ativo="${item.ativo}" title="${item.ativo ? "Inativar" : "Ativar"}">
                ${item.ativo
                  ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M8 12h8"/></svg> Inativar`
                  : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 8v8M8 12h8"/></svg> Ativar`
                }
              </button>
            </td>
          </tr>`).join("");
      }

      // atualizar contadores de aba
      if (countPlano) countPlano.textContent = state.plano.filter((x) => x.ativo || state.showInativos).length;
      if (countHist) countHist.textContent = state.historico.filter((x) => x.ativo || state.showInativos).length;
    }

    function escHtml(s) {
      return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    // ── dialog ────────────────────────────────────────────────────────────────

    function openDialog(item = null) {
      if (!dialog || !form) return;
      state.editingId = item ? item.id : null;
      showError("");
      form.reset();

      const isPlano = state.tab === "plano";
      if (fieldsPlano) fieldsPlano.hidden = !isPlano;
      if (fieldsHist)  fieldsHist.hidden  = isPlano;

      if (dialogLabel) dialogLabel.textContent = item ? "Editar registro" : "Novo registro";
      if (dialogTitle) dialogTitle.textContent = isPlano ? "Plano de Contas" : "Histórico Contábil";

      if (item) {
        if (isPlano) {
          form.elements.codigo.value         = item.codigo        || "";
          form.elements.classificacao.value  = item.classificacao || "";
          form.elements.nome.value           = item.nome          || "";
          form.elements.tipo.value           = item.tipo          || "";
          form.elements.natureza.value       = item.natureza      || "";
          form.elements.ativo.checked        = !!item.ativo;
        } else {
          form.elements.codigo_historico.value  = item.codigo  || "";
          form.elements.grupo.value             = item.grupo   || "";
          form.elements.nome_historico.value    = item.nome    || "";
          form.elements.ativo_historico.checked = !!item.ativo;
        }
      } else {
        // defaults para novo
        if (isPlano) form.elements.ativo.checked = true;
        else form.elements.ativo_historico.checked = true;
      }

      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }

    function closeDialog() {
      if (typeof dialog?.close === "function") dialog.close();
      else if (dialog) dialog.removeAttribute("open");
    }

    // ── API ───────────────────────────────────────────────────────────────────

    async function loadData() {
      setLoading(true);
      try {
        const [pcData, hcData] = await Promise.all([
          apiRequest(session, "/plano-contas/?todos=1"),
          apiRequest(session, "/historico-contabil/?todos=1"),
        ]);
        state.plano    = Array.isArray(pcData)   ? pcData   : (pcData?.results   || []);
        state.historico = Array.isArray(hcData) ? hcData : (hcData?.results || []);
        // atualizar cache global com apenas ativos
        window.__GM_PLANO_CONTAS__ = state.plano.filter((x) => x.ativo);
        window.__GM_HISTORICOS__   = state.historico.filter((x) => x.ativo);
      } catch (e) {
        logError("Falha ao carregar dados de contabilidade.", e);
      } finally {
        setLoading(false);
        renderTable();
      }
    }

    async function saveItem(payload) {
      const endpoint = state.tab === "plano" ? "/plano-contas/" : "/historico-contabil/";
      const isEdit   = !!state.editingId;
      const url      = isEdit ? `${endpoint}${state.editingId}/` : endpoint;
      const method   = isEdit ? "PATCH" : "POST";

      const resp = await fetch(url.startsWith("/") ? `/api${url}` : url, {
        method,
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.accessToken}`,
        },
        body: JSON.stringify(payload),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(Object.values(err).flat().join(" ") || `Erro ${resp.status}`);
      }
      return resp.json();
    }

    async function toggleAtivo(id, currentAtivo) {
      const endpoint = state.tab === "plano" ? "/plano-contas/" : "/historico-contabil/";
      const url = `/api${endpoint}${id}/`;
      const resp = await fetch(url, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.accessToken}`,
        },
        body: JSON.stringify({ ativo: !currentAtivo }),
      });
      if (!resp.ok) throw new Error(`Erro ${resp.status}`);
      return resp.json();
    }

    // ── events ────────────────────────────────────────────────────────────────

    // abas
    tabButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        state.tab = btn.dataset.contabTab;
        tabButtons.forEach((b) => {
          b.classList.toggle("is-active", b.dataset.contabTab === state.tab);
          b.setAttribute("aria-selected", b.dataset.contabTab === state.tab ? "true" : "false");
        });
        if (searchInput) searchInput.value = "";
        state.search = "";
        renderTable();
      });
    });

    // pesquisa
    if (searchInput) {
      searchInput.addEventListener("input", () => {
        state.search = searchInput.value;
        renderTable();
      });
    }

    // toggle inativos
    if (inativosChk) {
      inativosChk.addEventListener("change", () => {
        state.showInativos = inativosChk.checked;
        renderTable();
      });
    }

    // botão Novo
    if (novoBtn) novoBtn.addEventListener("click", () => openDialog(null));

    // fechar dialog
    if (dialogClose)  dialogClose.addEventListener("click",  closeDialog);
    if (dialogCancel) dialogCancel.addEventListener("click", closeDialog);
    if (dialog) {
      dialog.addEventListener("click", (e) => { if (e.target === dialog) closeDialog(); });
    }

    // delegação: editar e toggle na tabela
    if (tbody) {
      tbody.addEventListener("click", async (e) => {
        const editBtn   = e.target.closest("[data-contab-edit]");
        const toggleBtn = e.target.closest("[data-contab-toggle]");

        if (editBtn) {
          const id   = editBtn.dataset.contabEdit;
          const item = currentList().find((x) => x.id === id);
          if (item) openDialog(item);
          return;
        }

        if (toggleBtn) {
          const id       = toggleBtn.dataset.contabToggle;
          const ativo    = toggleBtn.dataset.ativo === "true";
          toggleBtn.disabled = true;
          try {
            const updated = await toggleAtivo(id, ativo);
            // atualizar na lista local
            const list = state.tab === "plano" ? state.plano : state.historico;
            const idx  = list.findIndex((x) => x.id === id);
            if (idx !== -1) list[idx] = updated;
            window.__GM_PLANO_CONTAS__ = state.plano.filter((x) => x.ativo);
            window.__GM_HISTORICOS__   = state.historico.filter((x) => x.ativo);
            renderTable();
          } catch (err) {
            logError("Erro ao alternar status.", err);
          } finally {
            toggleBtn.disabled = false;
          }
          return;
        }
      });
    }

    // submit do form
    if (form) {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        showError("");
        const submitBtn = panel.querySelector("[data-contab-form-submit]");
        if (submitBtn) submitBtn.disabled = true;

        try {
          let payload;
          if (state.tab === "plano") {
            payload = {
              codigo:        form.elements.codigo.value.trim(),
              classificacao: form.elements.classificacao.value.trim(),
              nome:          form.elements.nome.value.trim(),
              tipo:          form.elements.tipo.value.trim(),
              natureza:      form.elements.natureza.value.trim(),
              ativo:         form.elements.ativo.checked,
            };
            if (!payload.codigo || !payload.nome) { showError("Código e Nome são obrigatórios."); return; }
          } else {
            payload = {
              codigo: parseInt(form.elements.codigo_historico.value, 10),
              nome:   form.elements.nome_historico.value.trim(),
              grupo:  form.elements.grupo.value.trim(),
              ativo:  form.elements.ativo_historico.checked,
            };
            if (!payload.codigo || !payload.nome) { showError("Código e Nome são obrigatórios."); return; }
          }

          const saved = await saveItem(payload);
          const list  = state.tab === "plano" ? state.plano : state.historico;
          const idx   = list.findIndex((x) => x.id === saved.id);
          if (idx !== -1) list[idx] = saved;
          else list.push(saved);
          list.sort((a, b) => String(a.codigo).localeCompare(String(b.codigo), undefined, { numeric: true }));
          window.__GM_PLANO_CONTAS__ = state.plano.filter((x) => x.ativo);
          window.__GM_HISTORICOS__   = state.historico.filter((x) => x.ativo);
          closeDialog();
          renderTable();
        } catch (err) {
          showError(err.message || "Falha ao salvar.");
        } finally {
          if (submitBtn) submitBtn.disabled = false;
        }
      });
    }

    loadData();
  }

  // ─────────────────────────────────────────────────────────────────────────

  function setupExtratoImport(session) {
    const panel = document.querySelector('[data-panel-view-section="conciliador"]');
    if (!panel) return;

    const form = panel.querySelector("[data-extrato-form]");
    const fileInput = panel.querySelector("[data-extrato-file-input]");
    const fileLabel = panel.querySelector("[data-extrato-file-label]");
    const dropzone = panel.querySelector("[data-extrato-dropzone]");
    const comprovanteInput = panel.querySelector("[data-comprovante-file-input]");
    const comprovanteLabel = panel.querySelector("[data-comprovante-file-label]");
    const comprovanteDropzone = panel.querySelector("[data-comprovante-dropzone]");
    const submitBtn = panel.querySelector("[data-extrato-submit]");
    const errorEl = panel.querySelector("[data-extrato-error]");
    const resultCard = panel.querySelector("[data-extrato-result]");
    const metaEl = panel.querySelector("[data-extrato-meta]");
    const criarPerfilBtn = panel.querySelector("[data-extrato-criar-perfil]");
    const salvarHistoricoBtn = panel.querySelector("[data-extrato-salvar-historico]");
    const tbody = panel.querySelector("[data-extrato-tbody]");
    const searchInput = panel.querySelector("[data-extrato-search]");
    const filterChips = Array.from(panel.querySelectorAll("[data-extrato-natureza-filter]"));
    const totalsEl = panel.querySelector("[data-extrato-totals]");
    const totalCreditoEl = panel.querySelector("[data-extrato-total-credito]");
    const totalDebitoEl = panel.querySelector("[data-extrato-total-debito]");
    const totalSaldoEl = panel.querySelector("[data-extrato-total-saldo]");
    const countEls = {
      TODOS: panel.querySelector("[data-extrato-count-todos]"),
      CREDITO: panel.querySelector("[data-extrato-count-credito]"),
      DEBITO: panel.querySelector("[data-extrato-count-debito]"),
      INDEFINIDA: panel.querySelector("[data-extrato-count-indefinido]"),
      SEM_REGRA: panel.querySelector("[data-extrato-count-sem-regra]"),
    };

    if (!form || !fileInput || !tbody) return;

    const state = {
      lancamentos: [],
      filtroNatureza: "TODOS",
      busca: "",
      extratoMeta: null,
      componentes: {},   // keyed by lancamento linha_key
      detalheKey: null,  // key do lançamento aberto no dialog
      regras: {},        // keyed by lancamento key → { id?, codDebito, codCredito, codHistorico }
      empresaId: null,   // empresa selecionada para este extrato
      escritorioId: null, // escritório principal (carregado da API)
      regrasMap: {},     // keyed por historicNormKey → { id, codDebito, codCredito, codHistorico }
      regrasFlexi: [],   // todas as regras ordenadas por prioridade (para matching CONTEM/IGUAL/COMECA_COM)
      clientes: [],      // lista completa de clientes carregados (para validação de CNPJ)
      comprovantes: {},  // keyed por documento (string) → ComprovanteResult da API
      contasBancarias: [], // contas bancárias da empresa selecionada (para pré-preenchimento)
    };

    // ── Dropzone drag & drop ──────────────────────────────────────────────
    if (dropzone) {
      dropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropzone.classList.add("is-drag-over");
      });
      dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-drag-over"));
      dropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropzone.classList.remove("is-drag-over");
        const file = e.dataTransfer?.files?.[0];
        if (file) {
          const dt = new DataTransfer();
          dt.items.add(file);
          fileInput.files = dt.files;
          updateFileLabel(file);
        }
      });
    }

    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      if (file) updateFileLabel(file);
    });

    function updateFileLabel(file) {
      if (fileLabel) fileLabel.textContent = file.name;
      if (dropzone) dropzone.dataset.hasFile = "true";
    }

    if (comprovanteDropzone && comprovanteInput) {
      comprovanteDropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        comprovanteDropzone.classList.add("is-drag-over");
      });
      comprovanteDropzone.addEventListener("dragleave", () => comprovanteDropzone.classList.remove("is-drag-over"));
      comprovanteDropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        comprovanteDropzone.classList.remove("is-drag-over");
        const dropped = e.dataTransfer?.files;
        if (dropped && dropped.length > 0) {
          // Acumula com arquivos já selecionados
          const dt = new DataTransfer();
          for (const f of comprovanteInput.files) dt.items.add(f);
          for (const f of dropped) {
            if (f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf")) {
              dt.items.add(f);
            }
          }
          comprovanteInput.files = dt.files;
          comprovanteInput.dispatchEvent(new Event("change"));
        }
      });
    }

    if (comprovanteInput) {
      comprovanteInput.addEventListener("change", () => {
        const files = comprovanteInput.files;
        if (files && files.length > 0) {
          if (comprovanteLabel) {
            comprovanteLabel.textContent = files.length === 1
              ? files[0].name
              : `${files.length} arquivo${files.length > 1 ? "s" : ""} selecionado${files.length > 1 ? "s" : ""}`;
          }
          if (comprovanteDropzone) comprovanteDropzone.dataset.hasFile = "true";
        }
      });
    }

    // ── Upload e vinculação de comprovantes ───────────────────────────────
    async function uploadComprovantes(files) {
      try {
        const fd = new FormData();
        for (const f of files) fd.append("arquivos", f);
        const resp = await fetch(apiUrl("/comprovante-preview/"), {
          method: "POST",
          headers: { Authorization: `Bearer ${session.accessToken}` },
          body: fd,
        });
        if (!resp.ok) return;
        const data = await resp.json();
        // Indexa por documento (normalizado: sem zeros à esquerda)
        state.comprovantes = {};
        (data.comprovantes || []).forEach((c) => {
          if (c.documento) {
            state.comprovantes[c.documento] = c;
          }
        });
        // Re-renderiza tabela para mostrar indicadores de comprovante
        renderTable();
      } catch (_) {
        // Silencioso — comprovantes são opcionais
      }
    }

    // ── Helpers ───────────────────────────────────────────────────────────
    function setError(msg) {
      if (!errorEl) return;
      errorEl.textContent = msg;
      errorEl.hidden = !msg;
    }

    function normalizeCnpj(value) {
      return String(value || "").replace(/\D/g, "");
    }

    function formatDateBR(iso) {
      if (!iso) return "";
      const [y, m, d] = iso.split("-");
      return `${d}/${m}/${y}`;
    }

    function formatCurrency(valStr) {
      const num = parseFloat(valStr || "0");
      return new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(isFinite(num) ? num : 0);
    }

    function naturezaBadge(nat) {
      // Inversão contábil: crédito bancário = débito contábil, e vice-versa
      if (nat === "CREDITO") return '<span class="extrato-nat extrato-nat--debito">Débito</span>';
      if (nat === "DEBITO") return '<span class="extrato-nat extrato-nat--credito">Crédito</span>';
      return '<span class="extrato-nat extrato-nat--indefinido">Indefinido</span>';
    }

    function hasRegra(l) {
      const r = state.regras[lancamentoKey(l)];
      return r && (r.codDebito || r.codCredito || r.codHistorico);
    }

    function filteredRows() {
      const query = normalizeSearchTerm(state.busca);
      return state.lancamentos.filter((l) => {
        const isSemRegraFilter = state.filtroNatureza === "SEM_REGRA";
        const natMatch = state.filtroNatureza === "TODOS" || isSemRegraFilter || l.natureza === state.filtroNatureza;
        const semRegraMatch = !isSemRegraFilter || !hasRegra(l);
        const textMatch = !query || normalizeSearchTerm(l.historico + " " + l.documento).includes(query);
        return natMatch && semRegraMatch && textMatch;
      });
    }

    function regraCell(l) {
      const r = state.regras[lancamentoKey(l)];
      if (!r || (!r.codDebito && !r.codCredito && !r.codHistorico)) {
        return '<td class="extrato-td-regra extrato-td-regra--vazia"><span class="extrato-regra-vazia">—</span></td>';
      }
      // Acha o nome da regra no regrasFlexi pelo id
      const def = state.regrasFlexi.find((x) => x.id === r.id);
      const nome = def?.nome ? escapeHtml(def.nome.slice(0, 40)) : "";
      const codigos = [
        r.codDebito ? `<span class="extrato-regra-cod extrato-regra-cod--d" title="Débito">D: ${escapeHtml(r.codDebito)}</span>` : "",
        r.codCredito ? `<span class="extrato-regra-cod extrato-regra-cod--c" title="Crédito">C: ${escapeHtml(r.codCredito)}</span>` : "",
        r.codHistorico ? `<span class="extrato-regra-cod extrato-regra-cod--h" title="Histórico">H: ${escapeHtml(r.codHistorico)}</span>` : "",
      ].filter(Boolean).join("");
      return `<td class="extrato-td-regra"><span class="extrato-regra-nome" title="${nome}">${nome}</span><span class="extrato-regra-codigos">${codigos}</span></td>`;
    }

    function renderTable() {
      const rows = filteredRows();
      if (!rows.length) {
        tbody.innerHTML = '<tr data-extrato-empty><td colspan="6" class="empty-state">Nenhum lançamento encontrado para o filtro.</td></tr>';
        if (totalsEl) totalsEl.hidden = true;
        return;
      }

      tbody.innerHTML = rows.map((l) => {
        const key = lancamentoKey(l);
        const comps = state.componentes[key] || [];
        const badge = comps.length > 0
          ? `<span class="detalhe-badge">${comps.length}</span>`
          : "";
        const docKey = normalizeDoc(l.documento);
        const compBadge = (docKey && state.comprovantes[docKey]?.itens?.length > 1)
          ? `<span class="comprovante-badge" title="Comprovante vinculado">📎</span>`
          : "";
        return `
        <tr data-extrato-row data-natureza="${escapeHtml(l.natureza)}" data-linha-key="${escapeHtml(key)}" class="extrato-row-clickable" title="Clique para detalhar">
          <td class="extrato-td-date">${escapeHtml(formatDateBR(l.data))}</td>
          <td class="extrato-td-hist">${escapeHtml(l.historico)}${badge}${compBadge}</td>
          <td class="extrato-td-doc">${escapeHtml(l.documento || "—")}</td>
          <td class="extrato-td-valor text-right">${escapeHtml(formatCurrency(l.valor))}</td>
          <td>${naturezaBadge(l.natureza)}</td>
          ${regraCell(l)}
        </tr>
        `;
      }).join("");

      // Totais
      const visibleAll = state.filtroNatureza === "TODOS" ? state.lancamentos : rows;
      const totalC = state.lancamentos.filter((l) => l.natureza === "CREDITO").reduce((s, l) => s + parseFloat(l.valor || 0), 0);
      const totalD = state.lancamentos.filter((l) => l.natureza === "DEBITO").reduce((s, l) => s + parseFloat(l.valor || 0), 0);
      if (totalCreditoEl) totalCreditoEl.textContent = formatCurrency(totalC);
      if (totalDebitoEl) totalDebitoEl.textContent = formatCurrency(totalD);
      if (totalSaldoEl) totalSaldoEl.textContent = formatCurrency(totalC - totalD);
      if (totalsEl) totalsEl.hidden = false;
    }

    function updateCounts() {
      const counts = { TODOS: state.lancamentos.length, CREDITO: 0, DEBITO: 0, INDEFINIDA: 0, SEM_REGRA: 0 };
      state.lancamentos.forEach((l) => {
        if (l.natureza === "CREDITO") counts.CREDITO++;
        else if (l.natureza === "DEBITO") counts.DEBITO++;
        else counts.INDEFINIDA++;
        if (!hasRegra(l)) counts.SEM_REGRA++;
      });
      Object.entries(countEls).forEach(([key, el]) => {
        if (el) el.textContent = counts[key] ?? 0;
      });
    }

    function setActiveChip(value) {
      state.filtroNatureza = value;
      filterChips.forEach((chip) => {
        chip.classList.toggle("is-active", chip.dataset.extratoNaturezaFilter === value);
      });
      renderTable();
    }

    // ── Chave única por lançamento ─────────────────────────────────────────
    function lancamentoKey(l) {
      return `${l.linha || l.linha_origem || 0}_${l.data}_${l.valor}`;
    }

    // ── Normalização de histórico ──────────────────────────────────────────
    function historicNormKey(historico) {
      return String(historico || "").trim().toLowerCase();
    }

    // ── Regras — integração com API ───────────────────────────────────────
    async function loadRegrasFromAPI() {
      if (!state.empresaId) return;
      try {
        const data = await apiRequest(session, `/conciliador-regras/?empresa=${state.empresaId}&ativo=true`);
        const rules = Array.isArray(data) ? data : (data?.results || []);
        state.regrasMap = {};
        state.regrasFlexi = [];
        rules.forEach((r) => {
          const nk = historicNormKey(r.texto_referencia);
          const entry = {
            id: r.id,
            codDebito: r.conta_debito || "",
            codCredito: r.conta_credito || "",
            codHistorico: r.codigo_historico || "",
          };
          state.regrasMap[nk] = entry;
          state.regrasFlexi.push({
            ...entry,
            textoRef: r.texto_referencia || "",
            tipoComp: r.tipo_comparacao || "CONTEM",
            tipoMov: r.tipo_movimento || "AMBOS",
            prioridade: r.prioridade ?? 100,
            nome: r.nome || r.texto_referencia || "",
            empresaId: r.empresa || null,   // null = regra global
          });
        });
        // Ordena: menor prioridade = aplica primeiro
        state.regrasFlexi.sort((a, b) => a.prioridade - b.prioridade);
        applyApiRulesToLancamentos();
        updateCounts();
        renderRegrasAutoList();
        renderTable();
      } catch (err) {
        logWarn("Falha ao carregar regras de conciliação.", err);
      }
    }

    async function loadContasBancarias(empresaId) {
      if (!empresaId) { state.contasBancarias = []; return; }
      try {
        const data = await apiRequest(session, `/contas-clientes/?cliente=${empresaId}&tipo=BANCARIA&ativo=true`);
        const lista = Array.isArray(data) ? data : (data?.results || []);
        state.contasBancarias = lista;
      } catch (err) {
        state.contasBancarias = [];
        logWarn("Falha ao carregar contas bancárias da empresa.", err);
      }
    }

    function matchesRule(desc, textoRef, tipoComp) {
      const ref = textoRef.toLowerCase();
      if (tipoComp === "IGUAL") return desc === ref;
      if (tipoComp === "COMECA_COM") return desc.startsWith(ref);
      return desc.includes(ref); // CONTEM (padrão)
    }

    function applyApiRulesToLancamentos() {
      state.lancamentos.forEach((l) => {
        const desc = historicNormKey(l.historico);
        const nat = (l.natureza || "").toUpperCase();
        const regra = state.regrasFlexi.find((r) => {
          // Filtro por tipo de movimento
          if (r.tipoMov === "CREDITO" && nat !== "CREDITO") return false;
          if (r.tipoMov === "DEBITO" && nat !== "DEBITO") return false;
          return matchesRule(desc, r.textoRef, r.tipoComp);
        });
        if (regra) {
          state.regras[lancamentoKey(l)] = {
            id: regra.id,
            codDebito: regra.codDebito,
            codCredito: regra.codCredito,
            codHistorico: regra.codHistorico,
          };
        }
      });
    }

    function parseBRLInput(str) {
      // Aceita "20,00" ou "20.00" ou "1.234,56"
      const s = String(str || "").trim().replace(/\./g, "").replace(",", ".");
      const n = parseFloat(s);
      return isFinite(n) && n > 0 ? n : null;
    }

    // ── Dialog de detalhe ─────────────────────────────────────────────────
    const detalheDialog = panel.querySelector("[data-detalhe-dialog]");
    const detalheHist = panel.querySelector("[data-detalhe-hist]");
    const detalheData = panel.querySelector("[data-detalhe-data]");
    const detalheDoc = panel.querySelector("[data-detalhe-doc]");
    const detalheValor = panel.querySelector("[data-detalhe-valor]");
    const detalheNatureza = panel.querySelector("[data-detalhe-natureza]");
    const detalheList = panel.querySelector("[data-detalhe-list]");
    const detalheEmpty = panel.querySelector("[data-detalhe-empty]");
    const detalheDistribuido = panel.querySelector("[data-detalhe-distribuido]");
    const detalheRestante = panel.querySelector("[data-detalhe-restante]");
    const detalheFill = panel.querySelector("[data-detalhe-progress-fill]");
    const detalheAddForm = panel.querySelector("[data-detalhe-add-form]");
    const detalheAddError = panel.querySelector("[data-detalhe-add-error]");
    const detalheClose = panel.querySelector("[data-detalhe-close]");
    const detalheRegrasForm = panel.querySelector("[data-detalhe-regras-form]");
    const detalheRegrasError = panel.querySelector("[data-detalhe-regras-error]");
    const detalheRegrasOk = panel.querySelector("[data-detalhe-regras-ok]");
    const detalheRegraSalva = panel.querySelector("[data-detalhe-regra-salva]");
    const detalheRegraCodDebito = panel.querySelector("[data-detalhe-regra-cod-debito]");
    const detalheRegraCodCredito = panel.querySelector("[data-detalhe-regra-cod-credito]");
    const detalheRegraCodHistorico = panel.querySelector("[data-detalhe-regra-cod-historico]");
    const detalheComprovante = panel.querySelector("[data-detalhe-comprovante-info]");
    const extrairXlsBtn = panel.querySelector("[data-extrato-extrair-xls]");

    // ── Regras Automáticas — refs ─────────────────────────────────────────
    const regrasAutoSection = panel.querySelector("[data-regras-auto-section]");
    const regrasAutoList = panel.querySelector("[data-regras-auto-list]");
    const regrasAutoCount = panel.querySelector("[data-regras-auto-count]");
    const regrasAutoAddBtn = panel.querySelector("[data-regras-auto-add-btn]");
    const regrasAutoToggleBtn = panel.querySelector("[data-regras-auto-toggle]");
    const regrasAutoCollapsible = panel.querySelector("[data-regras-auto-collapsible]");
    const regrasAutoForm = panel.querySelector("[data-regras-auto-form]");
    const regrasAutoCancel = panel.querySelector("[data-regras-auto-cancel]");
    const regrasAutoFormError = panel.querySelector("[data-regras-auto-form-error]");
    const selectEmpresa = panel.querySelector("[data-extrato-empresa]");

    // ── Comboboxes de plano de contas e histórico ─────────────────────────
    if (regrasAutoForm) {
      setupCombobox(regrasAutoForm.elements.conta_debito, () => window.__GM_PLANO_CONTAS__ || []);
      setupCombobox(regrasAutoForm.elements.conta_credito, () => window.__GM_PLANO_CONTAS__ || []);
      setupCombobox(regrasAutoForm.elements.codigo_historico, () => window.__GM_HISTORICOS__ || [], {
        getLabel: (item) => `${item.codigo} \u2014 ${item.nome}`,
        getValue: (item) => String(item.codigo),
      });
    }
    if (detalheRegrasForm) {
      setupCombobox(detalheRegrasForm.elements.codDebito, () => window.__GM_PLANO_CONTAS__ || []);
      setupCombobox(detalheRegrasForm.elements.codCredito, () => window.__GM_PLANO_CONTAS__ || []);
      setupCombobox(detalheRegrasForm.elements.codHistorico, () => window.__GM_HISTORICOS__ || [], {
        getLabel: (item) => `${item.codigo} \u2014 ${item.nome}`,
        getValue: (item) => String(item.codigo),
      });
    }

    // ── Regras Automáticas — render e CRUD ───────────────────────────────
    const COMP_LABELS = { CONTEM: "Contém", IGUAL: "Igual", COMECA_COM: "Começa com" };
    const MOV_LABELS = { AMBOS: "Ambos", CREDITO: "Só crédito", DEBITO: "Só débito" };

    function renderRegrasAutoList() {
      if (!regrasAutoSection) return;

      // Mostra seção apenas quando há empresa selecionada
      regrasAutoSection.hidden = !state.empresaId;
      if (!state.empresaId) return;

      const regras = state.regrasFlexi;
      if (regrasAutoCount) regrasAutoCount.textContent = regras.length;

      if (!regrasAutoList) return;
      if (!regras.length) {
        regrasAutoList.innerHTML = '<p class="regras-auto-item-empty">Nenhuma regra automática cadastrada para esta empresa.</p>';
        return;
      }

      regrasAutoList.innerHTML = regras.map((r) => {
        const codigos = [
          r.codDebito ? `D: ${escapeHtml(r.codDebito)}` : "",
          r.codCredito ? `C: ${escapeHtml(r.codCredito)}` : "",
          r.codHistorico ? `H: ${escapeHtml(r.codHistorico)}` : "",
        ].filter(Boolean).join(" · ");
        const compLabel = COMP_LABELS[r.tipoComp] || r.tipoComp;
        const movLabel = r.tipoMov !== "AMBOS" ? ` · ${MOV_LABELS[r.tipoMov] || r.tipoMov}` : "";
        const globalBadge = !r.empresaId
          ? `<span class="regra-badge-global" title="Aplica a todas as empresas">Global</span>`
          : "";
        return `
          <div class="regras-auto-item" data-regra-id="${escapeHtml(r.id)}">
            <div class="regras-auto-item-body">
              <span class="regras-auto-item-titulo" title="${escapeHtml(r.textoRef)}">
                ${escapeHtml(r.nome || r.textoRef)}${globalBadge}
              </span>
              <span class="regras-auto-item-meta">${escapeHtml(compLabel)}${escapeHtml(movLabel)}</span>
              ${codigos ? `<span class="regras-auto-item-codigos">${codigos}</span>` : ""}
            </div>
            <div class="regras-auto-item-actions">
              <button type="button" class="regras-auto-item-btn" data-regra-edit-id="${escapeHtml(r.id)}">Editar</button>
              <button type="button" class="regras-auto-item-btn regras-auto-item-btn--delete" data-regra-del-id="${escapeHtml(r.id)}">Excluir</button>
            </div>
          </div>
        `;
      }).join("");

      // Botões editar
      regrasAutoList.querySelectorAll("[data-regra-edit-id]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const regra = state.regrasFlexi.find((r) => r.id === btn.dataset.regraEditId);
          if (!regra || !regrasAutoForm) return;
          regrasAutoForm.elements.regra_id.value = regra.id;
          regrasAutoForm.elements.texto_referencia.value = regra.textoRef;
          regrasAutoForm.elements.tipo_comparacao.value = regra.tipoComp;
          regrasAutoForm.elements.tipo_movimento.value = regra.tipoMov;
          regrasAutoForm.elements.conta_debito.value = regra.codDebito;
          regrasAutoForm.elements.conta_credito.value = regra.codCredito;
          regrasAutoForm.elements.codigo_historico.value = regra.codHistorico;
          // Marca/desmarca toggle global
          if (regrasAutoForm.elements.global_rule) {
            regrasAutoForm.elements.global_rule.checked = !regra.empresaId;
          }
          if (regrasAutoFormError) { regrasAutoFormError.textContent = ""; regrasAutoFormError.hidden = true; }
          regrasAutoForm.hidden = false;
          regrasAutoForm.elements.texto_referencia.focus();
        });
      });

      // Botões excluir
      regrasAutoList.querySelectorAll("[data-regra-del-id]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (!confirm(`Excluir a regra "${btn.closest("[data-regra-id]")?.querySelector(".regras-auto-item-titulo")?.textContent.trim() || ""}"?`)) return;
          try {
            await apiRequest(session, `/conciliador-regras/${btn.dataset.regraDelId}/`, { method: "DELETE" });
            await loadRegrasFromAPI();
          } catch (err) {
            alert("Falha ao excluir a regra: " + (err.message || ""));
          }
        });
      });
    }

    if (regrasAutoAddBtn) {
      regrasAutoAddBtn.addEventListener("click", () => {
        if (!regrasAutoForm) return;
        regrasAutoForm.reset();
        regrasAutoForm.elements.regra_id.value = "";
        // Por padrão: nova regra é global
        if (regrasAutoForm.elements.global_rule) regrasAutoForm.elements.global_rule.checked = true;
        if (regrasAutoFormError) { regrasAutoFormError.textContent = ""; regrasAutoFormError.hidden = true; }
        regrasAutoForm.hidden = false;
        regrasAutoForm.elements.texto_referencia.focus();
      });
    }

    if (regrasAutoToggleBtn && regrasAutoCollapsible) {
      regrasAutoToggleBtn.addEventListener("click", () => {
        const isExpanded = regrasAutoToggleBtn.getAttribute("aria-expanded") === "true";
        const nowExpanded = !isExpanded;
        regrasAutoCollapsible.hidden = !nowExpanded;
        regrasAutoToggleBtn.setAttribute("aria-expanded", String(nowExpanded));
        const icon = regrasAutoToggleBtn.querySelector(".regras-auto-toggle-icon");
        if (icon) icon.style.transform = nowExpanded ? "" : "rotate(180deg)";
        regrasAutoToggleBtn.childNodes.forEach((n) => {
          if (n.nodeType === Node.TEXT_NODE) n.textContent = nowExpanded ? " Ocultar" : " Mostrar";
        });
      });
    }

    if (regrasAutoCancel) {
      regrasAutoCancel.addEventListener("click", () => {
        if (regrasAutoForm) regrasAutoForm.hidden = true;
      });
    }

    if (regrasAutoForm) {
      regrasAutoForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const textoRef = String(regrasAutoForm.elements.texto_referencia?.value || "").trim();
        if (!textoRef) {
          if (regrasAutoFormError) { regrasAutoFormError.textContent = "Informe a palavra ou frase."; regrasAutoFormError.hidden = false; }
          return;
        }
        const codDebito = String(regrasAutoForm.elements.conta_debito?.value || "").trim();
        const codCredito = String(regrasAutoForm.elements.conta_credito?.value || "").trim();
        const codHistorico = String(regrasAutoForm.elements.codigo_historico?.value || "").trim();
        if (!codDebito && !codCredito && !codHistorico) {
          if (regrasAutoFormError) { regrasAutoFormError.textContent = "Preencha ao menos um código (débito, crédito ou histórico)."; regrasAutoFormError.hidden = false; }
          return;
        }
        if (regrasAutoFormError) { regrasAutoFormError.textContent = ""; regrasAutoFormError.hidden = true; }

        const saveBtn = regrasAutoForm.querySelector("[data-regras-auto-save]");
        if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "Salvando..."; }

        try {
          if (!state.escritorioId) {
            const escsData = await apiRequest(session, "/escritorios/");
            const escs = Array.isArray(escsData) ? escsData : (escsData?.results || []);
            if (escs.length > 0) state.escritorioId = escs[0].id;
          }
          if (!state.escritorioId) throw new Error("Escritório não encontrado. Recarregue a página.");

          const regraId = regrasAutoForm.elements.regra_id.value;
          const isGlobal = regrasAutoForm.elements.global_rule?.checked ?? false;
          const body = {
            escritorio: state.escritorioId,
            empresa: isGlobal ? null : (state.empresaId || null),
            nome: textoRef.slice(0, 255),
            texto_referencia: textoRef.slice(0, 255),
            tipo_comparacao: regrasAutoForm.elements.tipo_comparacao.value,
            tipo_movimento: regrasAutoForm.elements.tipo_movimento.value,
            conta_debito: codDebito,
            conta_credito: codCredito,
            codigo_historico: codHistorico,
            aplicar_automatico: true,
          };

          if (regraId) {
            await apiRequest(session, `/conciliador-regras/${regraId}/`, { method: "PATCH", body });
          } else {
            await apiRequest(session, "/conciliador-regras/", { method: "POST", body });
          }

          regrasAutoForm.hidden = true;
          regrasAutoForm.reset();
          await loadRegrasFromAPI();
        } catch (err) {
          if (regrasAutoFormError) { regrasAutoFormError.textContent = err.message || "Falha ao salvar."; regrasAutoFormError.hidden = false; }
        } finally {
          if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = "Salvar regra"; }
        }
      });
    }

    function renderRegraSalva(r) {
      if (!detalheRegraSalva) return;
      const temRegra = r && (r.codDebito || r.codCredito || r.codHistorico);
      if (temRegra) {
        if (detalheRegraCodDebito) detalheRegraCodDebito.textContent = r.codDebito || "—";
        if (detalheRegraCodCredito) detalheRegraCodCredito.textContent = r.codCredito || "—";
        if (detalheRegraCodHistorico) detalheRegraCodHistorico.textContent = r.codHistorico || "—";
        detalheRegraSalva.hidden = false;
      } else {
        detalheRegraSalva.hidden = true;
      }
    }

    function openDetalhe(l) {
      if (!detalheDialog) return;
      const key = lancamentoKey(l);
      state.detalheKey = key;

      if (detalheHist) detalheHist.value = l.historico || "";
      if (detalheData) detalheData.textContent = formatDateBR(l.data) || "—";
      if (detalheDoc) detalheDoc.textContent = l.documento || "—";
      if (detalheValor) detalheValor.textContent = formatCurrency(l.valor);
      if (detalheNatureza) detalheNatureza.innerHTML = naturezaBadge(l.natureza);
      if (detalheAddForm) detalheAddForm.reset();
      if (detalheAddError) { detalheAddError.textContent = ""; detalheAddError.hidden = true; }

      // ── Comprovante vinculado ──────────────────────────────────────────
      const docKey = normalizeDoc(l.documento);
      const comp = docKey ? state.comprovantes[docKey] : null;

      // Mostra bloco de info do comprovante
      if (detalheComprovante) {
        if (comp) {
          const tipoLabel = {
            bb_boleto: "BB — Boleto",
            bb_pix: "BB — PIX",
            bb_ted: "BB — TED / Transferência",
            bb_convenio: "BB — Pagamento Convênio",
            bradesco_boleto: "Bradesco — Boleto",
            darf: "Receita Federal — DARF",
          }[comp.tipo] || comp.tipo;
          const dataPag = comp.data_pagamento ? formatDateBR(comp.data_pagamento) : "—";
          const benef = comp.beneficiario ? ` · ${escapeHtml(comp.beneficiario)}` : "";
          detalheComprovante.innerHTML =
            `<span class="detalhe-comp-tag">📎 Comprovante</span>` +
            `<span>${escapeHtml(tipoLabel)}${benef}</span>` +
            `<span>Pagamento: ${escapeHtml(dataPag)}</span>`;
          detalheComprovante.hidden = false;
        } else {
          detalheComprovante.hidden = true;
          detalheComprovante.innerHTML = "";
        }
      }

      // Auto-popula componentes do comprovante se ainda não foram adicionados (só quando há itens além do Principal)
      if (comp && comp.itens && comp.itens.length > 1 && !state.componentes[key]) {
        state.componentes[key] = comp.itens
          .filter((it) => parseFloat(it.valor || 0) !== 0)
          .map((it) => ({ descricao: it.descricao, valor: parseFloat(it.valor || 0) }));
      }

      if (detalheRegrasForm) {
        const nk = historicNormKey(l.historico);
        const r = state.regras[key] || state.regrasMap[nk] || {};
        detalheRegrasForm.elements.codDebito.value = r.codDebito || "";
        detalheRegrasForm.elements.codCredito.value = r.codCredito || "";
        detalheRegrasForm.elements.codHistorico.value = r.codHistorico || "";

        // Pré-preenchimento automático: quando não há regra, usa o código contábil
        // da conta bancária da empresa que corresponde ao banco do extrato.
        // Inversão contábil: DÉBITO no extrato → lançamento CRÉDITO contábil (e vice-versa)
        if (!r.codDebito && !r.codCredito && state.contasBancarias.length) {
          // Mapa: valor do select de banco no conciliador → substring do nome da conta bancária
          const BANCO_NORM = {
            bb: "banco do brasil",
            bradesco: "bradesco",
            amazonia: "amaz",
            santander: "santander",
            caixa: "caixa",
            itau: "ita",
            sicredi: "sicredi",
            sicoob: "sicoob",
            inter: "inter",
            nubank: "nubank",
            btg: "btg",
          };
          const bancoKey = (state.extratoMeta?.banco || "").toLowerCase().replace(/[^a-z]/g, "");
          const bancoNorm = BANCO_NORM[bancoKey] || bancoKey;
          const conta = state.contasBancarias.find(
            (c) => c.banco && c.codigo_contabil && c.banco.toLowerCase().includes(bancoNorm)
          );
          if (conta) {
            if (l.natureza === "DEBITO") {
              detalheRegrasForm.elements.codCredito.value = conta.codigo_contabil;
            } else if (l.natureza === "CREDITO") {
              detalheRegrasForm.elements.codDebito.value = conta.codigo_contabil;
            }
          }
        }

        renderRegraSalva(r);
      }
      if (detalheRegrasError) { detalheRegrasError.textContent = ""; detalheRegrasError.hidden = true; }
      if (detalheRegrasOk) { detalheRegrasOk.hidden = true; detalheRegrasOk.style.display = "none"; }

      renderDetalheList();

      if (typeof detalheDialog.showModal === "function" && !detalheDialog.open) {
        detalheDialog.showModal();
      } else {
        detalheDialog.setAttribute("open", "open");
      }
    }

    function closeDetalhe() {
      state.detalheKey = null;
      if (typeof detalheDialog?.close === "function" && detalheDialog.open) {
        detalheDialog.close();
      } else if (detalheDialog) {
        detalheDialog.removeAttribute("open");
      }
    }

    function renderDetalheList() {
      if (!detalheList) return;
      const key = state.detalheKey;
      const l = state.lancamentos.find((x) => lancamentoKey(x) === key);
      if (!l) return;

      const valorOriginal = parseFloat(l.valor || 0);
      const comps = state.componentes[key] || [];
      const totalDist = comps.reduce((s, c) => s + c.valor, 0);
      const restante = valorOriginal - totalDist;
      const pct = valorOriginal > 0 ? Math.min(100, (totalDist / valorOriginal) * 100) : 0;

      if (detalheDistribuido) detalheDistribuido.textContent = formatCurrency(totalDist);
      if (detalheRestante) {
        detalheRestante.textContent = formatCurrency(Math.max(0, restante));
        detalheRestante.style.color = restante < -0.005 ? "#b91c1c" : "";
      }
      if (detalheFill) detalheFill.style.width = `${pct.toFixed(1)}%`;

      if (!comps.length) {
        detalheList.innerHTML = '<li class="detalhe-componentes-empty" data-detalhe-empty>Nenhum componente adicionado ainda.</li>';
        return;
      }

      detalheList.innerHTML = comps.map((c, i) => `
        <li class="detalhe-comp-item">
          <span class="detalhe-comp-desc">${escapeHtml(c.descricao)}</span>
          <span class="detalhe-comp-valor">${escapeHtml(formatCurrency(c.valor))}</span>
          <button type="button" class="detalhe-comp-remove" data-comp-idx="${i}" title="Remover" aria-label="Remover ${escapeHtml(c.descricao)}">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M18 6L6 18M6 6l12 12"/>
            </svg>
          </button>
        </li>
      `).join("");

      detalheList.querySelectorAll("[data-comp-idx]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const idx = parseInt(btn.dataset.compIdx, 10);
          state.componentes[key].splice(idx, 1);
          if (!state.componentes[key].length) delete state.componentes[key];
          renderDetalheList();
          renderTable();
        });
      });
    }

    if (detalheClose) {
      detalheClose.addEventListener("click", closeDetalhe);
    }

    if (detalheDialog) {
      detalheDialog.addEventListener("click", (e) => {
        if (e.target === detalheDialog) closeDetalhe();
      });
    }

    if (detalheAddForm) {
      detalheAddForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const desc = String(detalheAddForm.elements.descricao?.value || "").trim();
        const valorInput = String(detalheAddForm.elements.valor?.value || "").trim();
        const valor = parseBRLInput(valorInput);

        if (!desc) {
          if (detalheAddError) { detalheAddError.textContent = "Informe a descrição."; detalheAddError.hidden = false; }
          return;
        }
        if (!valor) {
          if (detalheAddError) { detalheAddError.textContent = "Informe um valor válido maior que zero."; detalheAddError.hidden = false; }
          return;
        }

        const key = state.detalheKey;
        const l = state.lancamentos.find((x) => lancamentoKey(x) === key);
        const valorOriginal = parseFloat(l?.valor || 0);
        const compsAtuais = state.componentes[key] || [];
        const totalDist = compsAtuais.reduce((s, c) => s + c.valor, 0);

        if (totalDist + valor > valorOriginal + 0.005) {
          if (detalheAddError) {
            detalheAddError.textContent = `Valor excede o restante (${formatCurrency(Math.max(0, valorOriginal - totalDist))}).`;
            detalheAddError.hidden = false;
          }
          return;
        }

        if (detalheAddError) detalheAddError.hidden = true;
        if (!state.componentes[key]) state.componentes[key] = [];
        state.componentes[key].push({ descricao: desc, valor });
        detalheAddForm.reset();
        detalheAddForm.elements.descricao?.focus();
        renderDetalheList();
        renderTable();
      });
    }

    if (detalheRegrasForm) {
      detalheRegrasForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const key = state.detalheKey;
        if (!key) return;
        const codDebito = String(detalheRegrasForm.elements.codDebito?.value || "").trim();
        const codCredito = String(detalheRegrasForm.elements.codCredito?.value || "").trim();
        const codHistorico = String(detalheRegrasForm.elements.codHistorico?.value || "").trim();
        if (!codDebito && !codCredito && !codHistorico) {
          if (detalheRegrasError) { detalheRegrasError.textContent = "Preencha ao menos um código."; detalheRegrasError.hidden = false; }
          return;
        }
        if (detalheRegrasError) { detalheRegrasError.textContent = ""; detalheRegrasError.hidden = true; }

        const l = state.lancamentos.find((x) => lancamentoKey(x) === key);
        if (!l) return;

        const saveBtn = detalheRegrasForm.querySelector(".detalhe-regras-save-btn");
        if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "Salvando..."; }

        try {
          // Garante escritorioId carregado
          if (!state.escritorioId) {
            const escsData = await apiRequest(session, "/escritorios/");
            const escs = Array.isArray(escsData) ? escsData : (escsData?.results || []);
            if (escs.length > 0) state.escritorioId = escs[0].id;
          }
          if (!state.escritorioId) {
            throw new Error("Escritório não encontrado. Recarregue a página.");
          }

          const nk = historicNormKey(l.historico);
          const existing = state.regrasMap[nk];
          const nomeEditado = String(detalheHist?.value || l.historico || "").trim().slice(0, 255) || l.historico.slice(0, 255);
          let savedRule;

          if (existing?.id) {
            // Atualiza regra existente
            savedRule = await apiRequest(session, `/conciliador-regras/${existing.id}/`, {
              method: "PATCH",
              body: {
                nome: nomeEditado,
                conta_debito: codDebito,
                conta_credito: codCredito,
                codigo_historico: codHistorico,
              },
            });
          } else {
            // Cria nova regra
            savedRule = await apiRequest(session, "/conciliador-regras/", {
              method: "POST",
              body: {
                escritorio: state.escritorioId,
                empresa: state.empresaId || undefined,
                nome: nomeEditado,
                texto_referencia: l.historico.slice(0, 255),
                tipo_comparacao: "CONTEM",
                conta_debito: codDebito,
                conta_credito: codCredito,
                codigo_historico: codHistorico,
              },
            });
          }

          // Atualiza estado local e aplica a todos lançamentos com mesmo histórico
          const ruleData = { id: savedRule.id, codDebito, codCredito, codHistorico };
          state.regrasMap[nk] = ruleData;
          state.lancamentos.forEach((lc) => {
            if (historicNormKey(lc.historico) === nk) {
              state.regras[lancamentoKey(lc)] = { ...ruleData };
            }
          });

          // Atualiza regrasFlexi com o nome editado para que regraCell e renderRegrasAutoList reflitam imediatamente
          const flexiIdx = state.regrasFlexi.findIndex((x) => x.id === savedRule.id);
          if (flexiIdx >= 0) {
            state.regrasFlexi[flexiIdx] = {
              ...state.regrasFlexi[flexiIdx],
              nome: nomeEditado,
              codDebito,
              codCredito,
              codHistorico,
            };
          } else {
            state.regrasFlexi.push({
              id: savedRule.id,
              textoRef: l.historico.slice(0, 255),
              tipoComp: savedRule.tipo_comparacao || "CONTEM",
              tipoMov: savedRule.tipo_movimento || "AMBOS",
              prioridade: savedRule.prioridade ?? 100,
              nome: nomeEditado,
              codDebito,
              codCredito,
              codHistorico,
            });
            state.regrasFlexi.sort((a, b) => a.prioridade - b.prioridade);
          }

          updateCounts();
          renderRegrasAutoList();
          renderTable();
          renderRegraSalva(ruleData);

          if (detalheRegrasOk) {
            detalheRegrasOk.hidden = false;
            detalheRegrasOk.style.display = "flex";
            setTimeout(() => {
              if (detalheRegrasOk) {
                detalheRegrasOk.hidden = true;
                detalheRegrasOk.style.display = "none";
              }
            }, 3000);
          }
        } catch (err) {
          if (detalheRegrasError) {
            detalheRegrasError.textContent = err.message || "Falha ao salvar a regra.";
            detalheRegrasError.hidden = false;
          }
        } finally {
          if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = "Salvar regra"; }
        }
      });
    }

    // Normaliza número de documento para matching com comprovantes (remove zeros à esquerda)
    function normalizeDoc(doc) {
      return String(doc || "").replace(/\./g, "").replace(/,/g, "").trim().replace(/^0+/, "") || "";
    }

    if (extrairXlsBtn) {
      extrairXlsBtn.addEventListener("click", () => {
        if (!window.XLSX) {
          alert("Biblioteca XLSX não carregada. Verifique sua conexão com a internet.");
          return;
        }
        const rows = [["Data", "Conta Crédito", "Conta Débito", "Cód. Histórico", "Descrição", "Valor"]];
        state.lancamentos.forEach((l) => {
          const key = lancamentoKey(l);
          const r = state.regras[key] || {};
          const docKey = normalizeDoc(l.documento);
          const comp = docKey ? state.comprovantes[docKey] : null;

          // Se há comprovante com múltiplos itens, expande em sub-linhas
          if (comp && comp.itens && comp.itens.length > 1) {
            // Linha principal com valor_total do comprovante
            rows.push([
              l.data || "",
              r.codCredito || "",
              r.codDebito || "",
              r.codHistorico || "",
              l.historico || "",
              parseFloat(comp.valor_total || l.valor || 0),
            ]);
            // Sub-linhas (apenas itens com valor != 0, excluindo "Principal" se quiser detalhar)
            comp.itens.forEach((item) => {
              const v = parseFloat(item.valor || 0);
              if (v !== 0) {
                rows.push([
                  "",
                  "",
                  "",
                  "",
                  `  ${item.descricao}`,
                  v,
                ]);
              }
            });
          } else {
            // Sem comprovante ou apenas 1 item: linha simples
            rows.push([
              l.data || "",
              r.codCredito || "",
              r.codDebito || "",
              r.codHistorico || "",
              l.historico || "",
              parseFloat(l.valor || 0),
            ]);
          }
        });
        const ws = window.XLSX.utils.aoa_to_sheet(rows);
        // Formata coluna Valor como número
        const range = window.XLSX.utils.decode_range(ws["!ref"]);
        for (let R = 1; R <= range.e.r; R++) {
          const cell = ws[window.XLSX.utils.encode_cell({ r: R, c: 5 })];
          if (cell) cell.z = "#,##0.00";
        }
        const wb = window.XLSX.utils.book_new();
        window.XLSX.utils.book_append_sheet(wb, ws, "Extrato");
        window.XLSX.writeFile(wb, "extrato_conciliacao.xlsx");
      });
    }

    // Clique nas linhas da tabela → abre detalhe
    tbody.addEventListener("click", (e) => {
      const row = e.target.closest("[data-linha-key]");
      if (!row) return;
      const key = row.dataset.linhaKey;
      const l = state.lancamentos.find((x) => lancamentoKey(x) === key);
      if (l) openDetalhe(l);
    });

    // ── Wiring ────────────────────────────────────────────────────────────
    filterChips.forEach((chip) => {
      chip.addEventListener("click", () => setActiveChip(chip.dataset.extratoNaturezaFilter || "TODOS"));
    });

    if (searchInput) {
      searchInput.addEventListener("input", () => {
        state.busca = searchInput.value;
        renderTable();
      });
    }

    // ── Criar Perfil a partir do extrato ─────────────────────────────────
    if (criarPerfilBtn) {
      criarPerfilBtn.addEventListener("click", () => {
        if (!state.extratoMeta) return;
        const banco = state.extratoMeta.banco;
        const bancoLabel = banco === "bradesco" ? "Bradesco" : banco.charAt(0).toUpperCase() + banco.slice(1);
        const nomeBase = state.extratoMeta.empresa_nome || "Empresa";
        document.dispatchEvent(new CustomEvent("extrato:criar-perfil", {
          detail: {
            nome: `${nomeBase} - ${bancoLabel}`,
            cnpj: state.extratoMeta.empresa_cnpj,
          },
        }));
      });
    }

    // ── Salvar histórico ──────────────────────────────────────────────────
    if (salvarHistoricoBtn) {
      salvarHistoricoBtn.addEventListener("click", async () => {
        if (!state.empresaId) {
          alert("Selecione uma empresa antes de salvar o histórico.");
          return;
        }
        if (!state.lancamentos.length) {
          alert("Nenhum lançamento para salvar.");
          return;
        }

        salvarHistoricoBtn.disabled = true;
        salvarHistoricoBtn.textContent = "Salvando...";

        try {
          if (!state.escritorioId) {
            const escsData = await apiRequest(session, "/escritorios/");
            const escs = Array.isArray(escsData) ? escsData : (escsData?.results || []);
            if (escs.length > 0) state.escritorioId = escs[0].id;
          }

          // Serializa as regras por linha_key → {codDebito, codCredito, codHistorico}
          const regrasSerial = {};
          Object.entries(state.regras).forEach(([k, v]) => { regrasSerial[k] = v; });

          const body = {
            empresa: state.empresaId,
            escritorio: state.escritorioId,
            banco: state.extratoMeta?.banco || "",
            periodo_inicio: state.extratoMeta
              ? (state.lancamentos[0]?.data || null)
              : null,
            periodo_fim: state.extratoMeta
              ? (state.lancamentos[state.lancamentos.length - 1]?.data || null)
              : null,
            lancamentos: state.lancamentos,
            regras: regrasSerial,
            componentes: state.componentes,
            comprovantes: state.comprovantes,
            extratoMeta: state.extratoMeta || {},
          };

          await apiRequest(session, "/extrato-historico/", { method: "POST", body });

          salvarHistoricoBtn.textContent = "✓ Salvo!";
          setTimeout(() => {
            if (salvarHistoricoBtn) {
              salvarHistoricoBtn.disabled = false;
              salvarHistoricoBtn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg> Salvar histórico`;
            }
          }, 2500);
        } catch (err) {
          alert("Falha ao salvar histórico: " + (err.message || ""));
          salvarHistoricoBtn.disabled = false;
          salvarHistoricoBtn.textContent = "Salvar histórico";
        }
      });
    }

    // ── Submit ────────────────────────────────────────────────────────────
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      setError("");

      const file = fileInput.files?.[0];
      if (!file) {
        setError("Selecione um arquivo PDF.");
        return;
      }

      if (!state.empresaId) {
        setError("Selecione uma empresa antes de processar o extrato.");
        return;
      }

      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Processando...";
      }

      try {
        const formData = new FormData(form);
        const resp = await fetch(apiUrl("/extrato-preview/"), {
          method: "POST",
          headers: { Authorization: `Bearer ${session.accessToken}` },
          body: formData,
        });

        const payload = resp.ok ? await resp.json() : await resp.json().catch(() => ({}));

        if (!resp.ok) {
          throw new Error(payload.detail || `Erro ${resp.status}`);
        }

        // ── Valida conta corrente do extrato vs empresa selecionada ──────
        if (payload.conta) {
          const clienteSel = state.clientes.find((c) => c.id === state.empresaId);
          const contaEmpresa = String(clienteSel?.conta_corrente || "").replace(/\D/g, "");
          const contaExtrato = String(payload.conta || "").replace(/\D/g, "");
          if (contaEmpresa && contaExtrato && contaEmpresa !== contaExtrato) {
            const ok = window.confirm(
              `Atenção: a conta corrente do extrato (${payload.conta}) não corresponde à conta registrada para esta empresa (${clienteSel.conta_corrente}).\n\nDeseja continuar mesmo assim?`
            );
            if (!ok) {
              if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Processar extrato"; }
              return;
            }
          }
        }

        state.lancamentos = payload.lancamentos || [];

        // Filtra apenas lançamentos do mês do extrato: do dia 2 ao último dia do mês
        // Usa periodo_inicio do payload para determinar o mês de referência
        {
          const periodoRef = payload.periodo_inicio || (state.lancamentos[0]?.data ?? null);
          if (periodoRef) {
            const [pA, pM] = String(periodoRef).split("-").map(Number);
            const diaInicio = new Date(pA, pM - 1, 2);
            const diaFim = new Date(pA, pM, 0); // último dia do mês
            state.lancamentos = state.lancamentos.filter((l) => {
              if (!l.data) return true;
              const [a, m, d] = String(l.data).split("-").map(Number);
              const dataLanc = new Date(a, m - 1, d);
              return dataLanc >= diaInicio && dataLanc <= diaFim;
            });
          }
        }
        state.regras = {};
        state.filtroNatureza = "TODOS";
        state.busca = "";
        if (searchInput) searchInput.value = "";

        // Carrega regras da API para a empresa selecionada
        loadRegrasFromAPI();

        // Metadados
        if (metaEl) {
          const partes = [];
          if (payload.empresa_nome) partes.push(`<strong>${escapeHtml(payload.empresa_nome)}</strong>`);
          if (payload.empresa_cnpj) partes.push(`CNPJ: ${escapeHtml(payload.empresa_cnpj)}`);
          if (payload.agencia && payload.conta) partes.push(`Ag: ${escapeHtml(payload.agencia)} | CC: ${escapeHtml(payload.conta)}`);
          if (payload.periodo_inicio && payload.periodo_fim) {
            partes.push(`Período: ${escapeHtml(formatDateBR(payload.periodo_inicio))} a ${escapeHtml(formatDateBR(payload.periodo_fim))}`);
          }
          partes.push(`${payload.total} lançamentos`);
          metaEl.innerHTML = partes.map((p) => `<span class="extrato-meta-item">${p}</span>`).join("");
        }

        state.extratoMeta = {
          banco: payload.banco || "auto",
          empresa_nome: payload.empresa_nome || "",
          empresa_cnpj: payload.empresa_cnpj || "",
          agencia: payload.agencia || "",
          conta: payload.conta || "",
        };

        updateCounts();
        setActiveChip("TODOS");

        if (resultCard) resultCard.hidden = false;
        resultCard?.scrollIntoView({ behavior: "smooth", block: "start" });

        // ── Processa comprovantes (se houver) ──────────────────────────
        if (comprovanteInput && comprovanteInput.files && comprovanteInput.files.length > 0) {
          uploadComprovantes(comprovanteInput.files);
        }

      } catch (err) {
        setError(err.message || "Falha ao processar o extrato.");
        logWarn("Falha ao processar extrato.", err);
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = "Processar extrato";
        }
      }
    });

    // ── Empresa select ────────────────────────────────────────────────────
    if (selectEmpresa) {
      selectEmpresa.addEventListener("change", () => {
        state.empresaId = selectEmpresa.value || null;
        state.regrasFlexi = [];
        state.regrasMap = {};
        renderRegrasAutoList();
        if (state.empresaId) {
          loadRegrasFromAPI();
          loadContasBancarias(state.empresaId);
        } else {
          state.contasBancarias = [];
        }
      });
    }

    // ── Inicialização ─────────────────────────────────────────────────────
    (async () => {
      // Carrega escritório primário
      try {
        const data = await apiRequest(session, "/escritorios/");
        const escs = Array.isArray(data) ? data : (data?.results || []);
        if (escs.length > 0) state.escritorioId = escs[0].id;
      } catch (err) {
        logWarn("Falha ao carregar escritório.", err);
      }

      // Carrega empresas (clientes) para o select
      try {
        const data = await apiRequest(session, "/clientes/");
        const clientes = Array.isArray(data) ? data : (data?.results || []);
        state.clientes = clientes.map(normalizeClientRecord);
        if (selectEmpresa) {
          selectEmpresa.innerHTML = '<option value="">Selecione a empresa...</option>' +
            clientes.map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.nome)}${c.cpf_cnpj ? ` (${escapeHtml(c.cpf_cnpj)})` : ""}</option>`).join("");
          // auto-seleciona se houver apenas 1 empresa
          if (clientes.length === 1) {
            selectEmpresa.value = clientes[0].id;
            state.empresaId = clientes[0].id;
            renderRegrasAutoList();
            loadRegrasFromAPI();
            loadContasBancarias(state.empresaId);
          }
        }
      } catch (err) {
        logWarn("Falha ao carregar empresas.", err);
        if (selectEmpresa) selectEmpresa.innerHTML = '<option value="">Erro ao carregar empresas</option>';
      }
    })();
  }

  // ─────────────────────────────────────────────────────────────────────────

  function setupConciliadorDraft() {
    const panel = document.querySelector('[data-panel-view-section="conciliador"]');
    if (!panel) {
      return;
    }

    const form = panel.querySelector("[data-conciliador-shell-form]");
    const officeSelect = panel.querySelector("[data-conciliador-office-select]");
    const clientSelect = panel.querySelector("[data-conciliador-client-select]");
    const periodStart = panel.querySelector("[data-conciliador-period-start]");
    const periodEnd = panel.querySelector("[data-conciliador-period-end]");
    const shellStatus = panel.querySelector("[data-conciliador-shell-status]");
    const filterNote = panel.querySelector("[data-conciliador-filter-note]");
    const uploadZone = panel.querySelector("[data-conciliador-upload-zone]");
    const uploadInput = panel.querySelector("[data-conciliador-upload-input]");
    const uploadTrigger = panel.querySelector("[data-conciliador-upload-trigger]");
    const uploadClear = panel.querySelector("[data-conciliador-upload-clear]");
    const uploadName = panel.querySelector("[data-conciliador-upload-name]");
    const uploadMeta = panel.querySelector("[data-conciliador-upload-meta]");
    const uploadMessage = panel.querySelector("[data-conciliador-upload-message]");

    if (!form || !officeSelect || !clientSelect || !periodStart || !periodEnd || !uploadZone || !uploadInput || !uploadTrigger || !uploadClear || !uploadName || !uploadMeta || !uploadMessage) {
      return;
    }

    const state = {
      file: null,
    };

    function setShellStatus(message) {
      if (shellStatus) {
        shellStatus.textContent = message;
      }
    }

    function setFilterNote(message) {
      if (filterNote) {
        filterNote.textContent = message;
      }
    }

    function setUploadMessage(message, tone = "info") {
      uploadMessage.textContent = message;
      uploadMessage.dataset.tone = tone;
    }

    function clearSelectedFile(message = "Nenhum arquivo selecionado.") {
      state.file = null;
      uploadInput.value = "";
      uploadName.textContent = "Nenhum arquivo selecionado";
      uploadMeta.textContent = "PDF, CSV, XLS ou XLSX.";
      uploadClear.hidden = true;
      uploadZone.dataset.hasFile = "false";
      setUploadMessage(message, "info");
    }

    function updateContextState() {
      const hasOffice = Boolean(officeSelect.value);
      const hasClient = Boolean(clientSelect.value);
      const hasPeriodStart = Boolean(periodStart.value);
      const hasPeriodEnd = Boolean(periodEnd.value);
      const clientPlaceholder = clientSelect.options[0];

      clientSelect.disabled = !hasOffice;
      periodStart.disabled = !(hasOffice && hasClient);
      periodEnd.disabled = !(hasOffice && hasClient);

      if (clientPlaceholder) {
        clientPlaceholder.textContent = hasOffice ? "Selecione um cliente..." : "Escolha o escritório primeiro";
      }

      if (!hasOffice) {
        clientSelect.value = "";
        periodStart.value = "";
        periodEnd.value = "";
      } else if (!hasClient) {
        periodStart.value = "";
        periodEnd.value = "";
      }

      if (!hasOffice) {
        setShellStatus("Selecione um escritório para começar.");
        setFilterNote("O cliente e o período serão liberados após escolher o escritório.");
      } else if (!hasClient) {
        setShellStatus("Escritório selecionado. Agora escolha o cliente.");
        setFilterNote("O período será liberado depois que o cliente for escolhido.");
      } else if (!hasPeriodStart || !hasPeriodEnd) {
        setShellStatus("Cliente selecionado. Defina o período para continuar.");
        setFilterNote("Informe o período inicial e final para preparar a importação.");
      } else {
        setShellStatus("Contexto preparado. O arquivo já pode ser selecionado.");
        setFilterNote("O próximo passo será o upload do arquivo do extrato.");
      }
    }

    function applySelectedFile(file) {
      const extension = getConciliadorFileExtension(file.name);
      if (!CONCILIADOR_ALLOWED_EXTENSIONS.has(extension)) {
        clearSelectedFile("Formato inválido. Use PDF, CSV, XLS ou XLSX.");
        setUploadMessage("Formato inválido. Use PDF, CSV, XLS ou XLSX.", "error");
        return;
      }

      state.file = file;
      uploadName.textContent = file.name;
      uploadMeta.textContent = `${formatConciliadorFileSize(file.size)} • ${extension.toUpperCase()}`;
      uploadClear.hidden = false;
      uploadZone.dataset.hasFile = "true";

      const filtersReady = Boolean(officeSelect.value && clientSelect.value && periodStart.value && periodEnd.value);
      setUploadMessage(filtersReady ? "Arquivo pronto para a próxima etapa." : "Arquivo selecionado. Complete os filtros para seguir.", "success");
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
    });

    officeSelect.addEventListener("change", () => {
      clientSelect.value = "";
      periodStart.value = "";
      periodEnd.value = "";
      updateContextState();
      if (!officeSelect.value) {
        clearSelectedFile("Selecione um arquivo para iniciar a preparação da importação.");
      }
    });

    clientSelect.addEventListener("change", () => {
      periodStart.value = "";
      periodEnd.value = "";
      updateContextState();
    });

    periodStart.addEventListener("change", () => {
      updateContextState();
    });

    periodEnd.addEventListener("change", () => {
      updateContextState();
    });

    uploadTrigger.addEventListener("click", () => {
      uploadInput.click();
    });

    uploadClear.addEventListener("click", () => {
      clearSelectedFile("Arquivo removido. Selecione outro para continuar.");
    });

    uploadInput.addEventListener("change", () => {
      const [file] = Array.from(uploadInput.files || []);
      if (!file) {
        clearSelectedFile("Nenhum arquivo selecionado.");
        return;
      }

      applySelectedFile(file);
    });

    updateContextState();
    clearSelectedFile("O arquivo ficará preparado localmente para a próxima etapa.");
  }

  function buildSession(tokenResponse, fallback = {}) {
    if (!tokenResponse?.access_token) {
      throw new Error("Token de acesso não recebido do Keycloak.");
    }

    const accessClaims = parseJwt(tokenResponse.access_token);
    const idToken = tokenResponse.id_token || fallback.idToken || "";
    const idClaims = idToken ? parseJwt(idToken) : null;

    if (fallback.nonce && idClaims?.nonce && idClaims.nonce !== fallback.nonce) {
      throw new Error("Nonce inválido na resposta do Keycloak.");
    }

    const roles = extractRoles(accessClaims);
    const expiresIn = Number(tokenResponse.expires_in || 60);

    return {
      accessToken: tokenResponse.access_token,
      refreshToken: tokenResponse.refresh_token || fallback.refreshToken || "",
      idToken,
      accessClaims,
      idClaims,
      roles,
      username:
        accessClaims.preferred_username ||
        idClaims?.preferred_username ||
        accessClaims.name ||
        idClaims?.name ||
        accessClaims.email ||
        idClaims?.email ||
        accessClaims.sub ||
        "",
      email: accessClaims.email || idClaims?.email || "",
      sub: accessClaims.sub || idClaims?.sub || "",
      expiresAt: Date.now() + Math.max(expiresIn - 30, 5) * 1000,
    };
  }

  function summarizeSession(session) {
    if (!session) {
      return null;
    }

    return {
      username: session.username || session.sub || "",
      email: session.email || "",
      roles: session.roles || [],
      realmRoles: session.accessClaims?.realm_access?.roles || [],
      clientRoles: session.accessClaims?.resource_access?.[CONFIG.keycloak.clientId]?.roles || [],
      aud: session.accessClaims?.aud || null,
      azp: session.accessClaims?.azp || null,
      hasRequiredRole: hasRequiredRole(session),
      expiresAt: session.expiresAt || null,
    };
  }

  async function exchangeCodeForToken({ code, verifier, redirectUri }) {
    logInfo("Trocando authorization code por tokens via backend.", { redirectUri });
    try {
      const response = await fetch(apiUrl("/auth/keycloak/token/"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          code,
          verifier,
          redirect_uri: redirectUri,
        }),
      });

      const payload = await response.json();
      if (!response.ok) {
        logError("Falha ao trocar code por token.", payload);
        throw new Error(payload.error_description || payload.error || "Falha ao trocar o código por token.");
      }

      logInfo("Tokens recebidos do Keycloak.", {
        hasAccessToken: Boolean(payload.access_token),
        hasIdToken: Boolean(payload.id_token),
        expiresIn: payload.expires_in,
        refreshToken: Boolean(payload.refresh_token),
      });
      return payload;
    } catch (error) {
      logError("Falha ao buscar tokens no Keycloak.", {
        endpoint: apiUrl("/auth/keycloak/token/"),
        redirectUri,
        name: error?.name,
        message: error?.message,
      });
      throw error;
    }
  }

  async function startLogin(redirectUri) {
    const state = randomString(24);
    const verifier = randomString(96);
    const nonce = randomString(24);
    const challenge = await sha256(verifier);

    savePkce(state, { verifier, nonce, redirectUri });
    logInfo("Iniciando login Keycloak.", { redirectUri, state });

    const authUrl = new URL(authEndpoint("auth"));
    authUrl.search = new URLSearchParams({
      client_id: CONFIG.keycloak.clientId,
      redirect_uri: redirectUri,
      response_type: "code",
      scope: "openid profile email",
      state,
      nonce,
      code_challenge: challenge,
      code_challenge_method: "S256",
    }).toString();

    window.location.assign(authUrl.toString());
  }

  async function handleCallbackIfPresent() {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");

    logInfo("Verificando callback do Keycloak.", {
      hasCode: Boolean(code),
      hasState: Boolean(state),
      path: window.location.pathname,
    });

    if (!code) {
      return null;
    }

    if (!state) {
      logError("Callback do Keycloak sem state.");
      throw new Error("Retorno do Keycloak sem state.");
    }

    const pkce = readPkce(state);
    if (!pkce) {
      logError("PKCE não encontrado para o state retornado.", { state });
      throw new Error("Fluxo de login expirado. Tente novamente.");
    }

    try {
      const tokenResponse = await exchangeCodeForToken({
        code,
        verifier: pkce.verifier,
        redirectUri: pkce.redirectUri,
      });
      const session = buildSession(tokenResponse, pkce);
      saveSession(session);
      logInfo("Resumo dos claims do token.", {
        realmRoles: session.accessClaims?.realm_access?.roles || [],
        clientRoles: session.accessClaims?.resource_access?.[CONFIG.keycloak.clientId]?.roles || [],
        aud: session.accessClaims?.aud || null,
        azp: session.accessClaims?.azp || null,
      });
      logInfo("Callback processado com sucesso.", summarizeSession(session));
      return session;
    } finally {
      clearPkce(state);
      logInfo("State/PKCE limpos após callback.", { state });
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }

  async function ensureSession({ redirectUri, interactive = true } = {}) {
    logInfo("Garantindo sessão do usuário.", { interactive, redirectUri, currentPath: window.location.pathname });
    const callbackSession = await handleCallbackIfPresent();
    if (callbackSession) {
      return callbackSession;
    }

    const session = readSession();
    if (session) {
      logInfo("Sessão encontrada no storage.", summarizeSession(session));
      return session;
    }

    if (interactive) {
      logWarn("Sessão ausente, iniciando login interativo.");
      await startLogin(redirectUri);
    }

    logInfo("Nenhuma sessão ativa no momento.");
    return null;
  }

  async function logout(session) {
    logInfo("Iniciando logout completo no Keycloak.", summarizeSession(session));
    if (sessionExpiryInterval) clearInterval(sessionExpiryInterval);
    const logoutUrl = new URL(authEndpoint("logout"));
    logoutUrl.search = new URLSearchParams({
      client_id: CONFIG.keycloak.clientId,
      post_logout_redirect_uri: buildUrl(CONFIG.pages.login),
      ...(session?.idToken ? { id_token_hint: session.idToken } : {}),
    }).toString();

    clearAppState();
    window.location.assign(logoutUrl.toString());
  }

  let sessionExpiryInterval = null;

  function updatePanelWithSession(session) {
    const userName = document.getElementById("userName");
    const userEmail = document.getElementById("userEmail");
    const sessionBadge = document.getElementById("sessionBadge");
    const sessionExpiryValue = document.getElementById("sessionExpiryValue");
    const sessionExpiryLabel = document.getElementById("sessionExpiryLabel");

    updateText(userName, session.username || session.sub || "Usuário");
    updateText(userEmail, session.email || session.sub || "-");

    if (sessionBadge) {
      const hasRole = session.roles.includes(CONFIG.keycloak.requiredRole);
      sessionBadge.className = hasRole ? "status-ok" : "status-error";
      sessionBadge.innerHTML = hasRole 
        ? `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg> Acesso liberado`
        : `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg> Sem acesso`;
    }

    if (sessionExpiryInterval) clearInterval(sessionExpiryInterval);
    sessionExpiryInterval = setInterval(() => {
      const remaining = session.expiresAt - Date.now();
      const formatted = formatDuration(remaining);
      if (sessionExpiryValue) sessionExpiryValue.textContent = formatted;
      if (sessionExpiryLabel) sessionExpiryLabel.textContent = formatted;
      if (remaining <= 0) {
        if (sessionExpiryInterval) clearInterval(sessionExpiryInterval);
      }
    }, 1000);

    const expiresIn = formatDuration(session.expiresAt - Date.now());
    if (sessionExpiryValue) sessionExpiryValue.textContent = expiresIn;
    if (sessionExpiryLabel) sessionExpiryLabel.textContent = expiresIn;

    logInfo("Painel atualizado com a sessão.", summarizeSession(session));
  }

  function updateDashboardStats(clients) {
    const total = clients.length;
    const ativos = clients.filter(c => (c.situacao || "").toUpperCase() === "ATIVO").length;
    const analise = clients.filter(c => (c.situacao || "").toUpperCase() === "EM_ANALISE").length;
    const inativos = clients.filter(c => (c.situacao || "").toUpperCase() === "INATIVO").length;

    const totalEl = document.querySelector("[data-dashboard-total]");
    const ativosEl = document.querySelector("[data-dashboard-ativos]");
    const analiseEl = document.querySelector("[data-dashboard-analise]");
    const inativosEl = document.querySelector("[data-dashboard-inativos]");

    if (totalEl) totalEl.textContent = total;
    if (ativosEl) ativosEl.textContent = ativos;
    if (analiseEl) analiseEl.textContent = analise;
    if (inativosEl) inativosEl.textContent = inativos;
  }

  async function testProtectedEndpoint(session) {
    const apiStatusValue = document.getElementById("apiStatusValue");
    const apiStatusLabel = document.getElementById("apiStatusLabel");

    logInfo("Testando endpoint protegido.", { url: apiUrl("/teste/") });

    try {
      const response = await fetch(apiUrl("/teste/"), {
        headers: {
          Authorization: `Bearer ${session.accessToken}`,
        },
      });

      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        payload = {};
      }

      if (apiStatusValue) {
        apiStatusValue.textContent = String(response.status);
      }
      if (apiStatusLabel) {
        apiStatusLabel.textContent = payload.detail || (response.ok ? "Acesso liberado" : "Acesso negado");
      }

      logInfo("Resposta do endpoint protegido.", {
        status: response.status,
        ok: response.ok,
        detail: payload.detail || null,
        roles: payload.roles || null,
      });
    } catch (_error) {
      logError("Falha ao chamar endpoint protegido.", { url: apiUrl("/teste/") });
      if (apiStatusValue) {
        apiStatusValue.textContent = "OFF";
      }
      if (apiStatusLabel) {
        apiStatusLabel.textContent = "Backend indisponível ou sem CORS";
      }
    }
  }

  function showAccessDenied(session, reason) {
    const roles = Array.isArray(session?.roles) ? session.roles.join(", ") : "nenhuma";
    clearAppState();

    document.body.classList.add("panel-ready");
    document.body.innerHTML = `
      <main style="min-height:100dvh;display:grid;place-items:center;padding:24px;background:#f2f2f2;color:#161616;font-family:'Poppins','Segoe UI',sans-serif;">
        <section style="width:min(100%,460px);background:#fff;border:1px solid #ffd1d8;border-radius:18px;padding:34px 28px;box-shadow:0 18px 40px rgba(0,0,0,0.14);text-align:center;">
          <div style="margin-bottom:14px;font-size:0.78rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#c91b33;">Acesso negado</div>
          <h1 style="margin:0 0 12px;font-size:1.7rem;line-height:1.25;">Você não possui permissão para acessar.</h1>
          <p style="margin:0;color:#6d1f2b;line-height:1.7;">${escapeHtml(reason || "Sua conta não possui a role USER-GM.")}<br />Roles recebidas: ${escapeHtml(roles)}<br />Você será redirecionado para o login.</p>
        </section>
      </main>
    `;

    logWarn("Painel bloqueado por role ausente.", summarizeSession(session));
    window.setTimeout(() => redirectToLogin("forbidden"), 1500);
  }

  async function setupLoginPage() {
    const loginButton = document.getElementById("loginButton");
    const loginStatus = document.getElementById("loginStatus");
    const reason = new URLSearchParams(window.location.search).get("reason");
    const detail = new URLSearchParams(window.location.search).get("detail");
    const messages = {
      forbidden: "Sua conta não possui a role USER-GM.",
      token_exchange_failed: "Não foi possível obter o JWT do Keycloak. Verifique a configuração do Keycloak.",
      auth_failed: "Falha na autenticação com Keycloak.",
    };
    const deniedMessage = messages[reason] || "";

    logInfo("Abrindo tela de login.", { reason, href: window.location.href });

    try {
      const session = await ensureSession({ interactive: false });
      if (session) {
        if (hasRequiredRole(session)) {
          logInfo("Sessão válida com role exigida. Redirecionando para o painel.", summarizeSession(session));
          updateText(loginStatus, "Sessão já ativa. Abrindo o painel...");
          window.location.replace(buildUrl(CONFIG.pages.panel));
          return;
        }

        logWarn("Sessão válida, mas sem role exigida. Limpando estado e permanecendo no login.", summarizeSession(session));
        clearAppState();
        updateText(loginStatus, deniedMessage || "Sua conta não possui a role USER-GM.");
        return;
      }

      updateText(loginStatus, detail || deniedMessage || "Clique em Entrar com SSO para autenticar via Keycloak.");
    } catch (_error) {
      updateText(loginStatus, detail || "Não foi possível validar a sessão agora. Tente novamente.");
    }

    if (loginButton) {
      loginButton.addEventListener("click", async () => {
        logInfo("Clique em Entrar com SSO.");
        await startLogin(buildUrl(CONFIG.pages.panel));
      });
    }
  }

  async function setupPanelPage() {
    const logoutButton = document.getElementById("logoutButton");
    const sessionStatus = document.getElementById("sessionStatus");

    logInfo("Abrindo painel.", { href: window.location.href });

    let session;

    try {
      session = await ensureSession({ interactive: false, redirectUri: buildUrl(CONFIG.pages.panel) });
      if (!session) {
        logWarn("Sem sessão no painel. Voltando ao login.");
        redirectToLogin();
        return;
      }

      if (!hasRequiredRole(session)) {
        showAccessDenied(session, "Você não possui permissão para acessar.");
        return;
      }

      logInfo("Sessão autorizada para o painel.", summarizeSession(session));
      updatePanelWithSession(session);
      document.body.classList.add("panel-ready");
      await testProtectedEndpoint(session);
    } catch (error) {
      logError("Falha na autenticação do painel.", error);
      clearAppState();
      redirectToLogin("token_exchange_failed", error?.message || "Falha na autenticação com Keycloak.");
      return;
    }

    // Carregar listas de plano de contas e históricos contábeis
    try {
      const [pcRes, hcRes] = await Promise.all([
        fetch("/api/plano-contas/", { headers: { Authorization: `Bearer ${session.accessToken}` } }),
        fetch("/api/historico-contabil/", { headers: { Authorization: `Bearer ${session.accessToken}` } }),
      ]);
      if (pcRes.ok) window.__GM_PLANO_CONTAS__ = await pcRes.json();
      if (hcRes.ok) window.__GM_HISTORICOS__ = await hcRes.json();
    } catch (e) {
      logError("Falha ao carregar plano de contas / históricos.", e);
    }

    const setupErrors = [];
    const runPanelSetup = (label, setup) => {
      try {
        setup();
      } catch (error) {
        setupErrors.push({ label, error });
        logError(`Falha ao inicializar ${label}.`, error);
      }
    };

    try {
      runPanelSetup("navegação", setupPanelNavigation);
      runPanelSetup("clientes", () => setupClientsCrud(session));
      runPanelSetup("perfis", () => setupPerfisCrud(session));
      runPanelSetup("contabilidade", () => setupContabilidadeCrud(session));
      runPanelSetup("extrato", () => setupExtratoImport(session));
      runPanelSetup("rascunho do conciliador", setupConciliadorDraft);
      setPanelView(getInitialPanelView(), { updateUrl: false });

      if (sessionStatus) {
        sessionStatus.textContent = setupErrors.length
          ? `USER-GM OK · ${setupErrors.length} módulo(s) com erro`
          : (session.roles.includes(CONFIG.keycloak.requiredRole) ? "USER-GM OK" : "SEM USER-GM");
      }

      if (logoutButton) {
        logoutButton.addEventListener("click", async () => {
          logInfo("Clique em Sair.");
          await logout(session);
        });
      }
    } catch (error) {
      logError("Falha ao inicializar a interface do painel.", error);
      if (sessionStatus) {
        sessionStatus.textContent = "Painel autenticado, mas houve erro ao carregar a interface.";
      }
    }
  }

  window.addEventListener("error", (event) => {
    logError("Erro não tratado no navegador.", {
      message: event.message,
      filename: event.filename,
      line: event.lineno,
      column: event.colno,
    });
  });

  window.addEventListener("unhandledrejection", (event) => {
    logError("Promise rejeitada sem tratamento.", {
      reason: serializeDebugData(event.reason),
    });
  });

  window.GMApp = {
    ensureSession,
    hasRequiredRole,
    logout,
    showAccessDenied,
    updatePanelWithSession,
    testProtectedEndpoint,
    clearAppState,
    redirectToLogin,
    buildUrl,
  };

  document.addEventListener("DOMContentLoaded", () => {
    logInfo("DOM carregado.", { page: document.body.dataset.page, href: window.location.href });
    if (document.body.dataset.page === "login") {
      setupLoginPage();
    }

    if (document.body.dataset.page === "panel") {
      setupPanelPage();
    }
  });
})();
