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
    Object.values(resourceAccess).forEach((entry) => {
      roles.push(...(entry?.roles || []));
    });

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

  function redirectToLogin(reason = "") {
    logWarn("Redirecionando para login.", { reason, currentUrl: window.location.href });
    const loginUrl = new URL(buildUrl(CONFIG.pages.login));
    if (reason) {
      loginUrl.searchParams.set("reason", reason);
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
    if (view === "clientes" || view === "conciliador") {
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
    const digits = String(value || "").replace(/\D/g, "");
    if (!digits) {
      return "";
    }

    if (digits.length === 11) {
      return `${digits.slice(0, 3)}.${digits.slice(3, 6)}.${digits.slice(6, 9)}-${digits.slice(9)}`;
    }

    if (digits.length === 14) {
      return `${digits.slice(0, 2)}.${digits.slice(2, 5)}.${digits.slice(5, 8)}/${digits.slice(8, 12)}-${digits.slice(12)}`;
    }

    if (digits.length > 4) {
      return `${digits.slice(0, 2)}***${digits.slice(-2)}`;
    }

    return digits;
  }

  function maskPhone(value) {
    const digits = String(value || "").replace(/\D/g, "");
    if (!digits) {
      return "";
    }

    if (digits.length === 10) {
      return `(${digits.slice(0, 2)}) ***-${digits.slice(-4)}`;
    }

    if (digits.length === 11) {
      return `(${digits.slice(0, 2)}) *****-${digits.slice(-4)}`;
    }

    if (digits.length > 4) {
      return `***${digits.slice(-4)}`;
    }

    return digits;
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
    const form = panel.querySelector("[data-client-form]");
    const modalTitle = panel.querySelector("[data-client-modal-title]");
    const submitButton = panel.querySelector("[data-client-submit]");
    const list = panel.querySelector("[data-client-list]");
    const searchInput = panel.querySelector("[data-client-search]");
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

    return {
      id: String(record?.id || "").trim(),
      codigo: String(record?.codigo || "").trim(),
      nome: String(record?.nome || "").trim(),
      cpf_cnpj: String(record?.cpf_cnpj || "").trim(),
      ie: String(record?.ie || "").trim(),
      telefone: String(record?.telefone || "").trim(),
      data_inicio: String(record?.data_inicio || "").trim(),
      situacao,
      situacao_label: situacaoLabel,
      searchIndex: normalizeSearchTerm([
        record?.codigo,
        record?.nome,
        record?.cpf_cnpj,
        record?.ie,
        record?.telefone,
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
      ie: String(data.ie || "").trim(),
      telefone: String(data.telefone || "").trim(),
      data_inicio: String(data.data_inicio || "").trim(),
      situacao: String(data.situacao || "ATIVO").trim() || "ATIVO",
    };
  }

  function setClientFormData(form, record) {
    const client = normalizeClientRecord(record);
    if (form.elements.codigo) form.elements.codigo.value = client.codigo;
    if (form.elements.nome) form.elements.nome.value = client.nome;
    if (form.elements.cpf_cnpj) form.elements.cpf_cnpj.value = client.cpf_cnpj;
    if (form.elements.ie) form.elements.ie.value = client.ie;
    if (form.elements.telefone) form.elements.telefone.value = client.telefone;
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
    const form = panel.querySelector("[data-client-form]");
    const modalTitle = panel.querySelector("[data-client-modal-title]");
    const submitButton = panel.querySelector("[data-client-submit]");
    const list = panel.querySelector("[data-client-list]");
    const searchInput = panel.querySelector("[data-client-search]");
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

    const state = {
      clients: [],
      loading: false,
      error: "",
      activeClientId: null,
      activeClientMode: "create",
    };

function setClientFormMode(mode) {
      form.dataset.clientMode = mode;

      form.querySelectorAll("input, textarea").forEach((field) => {
        if (field.name === "codigo") {
          field.readOnly = true;
          return;
        }
        field.readOnly = mode === "view";
      });

      if (form.elements.situacao) {
        form.elements.situacao.disabled = mode === "view";
      }

      if (submitButton) {
        submitButton.textContent = mode === "view" ? "Fechar" : mode === "create" ? "Salvar cliente" : "Salvar alterações";
      }
    }

    function resetModalState() {
      state.activeClientId = null;
      state.activeClientMode = "create";
      form.reset();
      if (modalTitle) {
        modalTitle.textContent = "Novo cliente";
      }
      if (form.elements.data_inicio) {
        form.elements.data_inicio.value = getTodayDateValue();
      }
      if (form.elements.situacao) {
        form.elements.situacao.value = "ATIVO";
      }
      setClientFormMode("create");
    }

    function openModal(record = null, mode = "create") {
      state.activeClientId = record?.id || null;
      state.activeClientMode = mode;
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
        modalTitle.textContent = mode === "view" ? "Visualizar cliente" : mode === "edit" ? "Editar cliente" : "Novo cliente";
      }

      setClientFormMode(mode);

      if (typeof modal.showModal === "function") {
        if (!modal.open) {
          modal.showModal();
        }
      } else {
        modal.setAttribute("open", "open");
      }

      window.requestAnimationFrame(() => {
        const firstField = form.elements.nome || form.elements.codigo;
        if (firstField && typeof firstField.focus === "function") {
          firstField.focus();
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

    function renderState() {
      const query = normalizeSearchTerm(searchInput?.value || "");
      const visibleClients = query
        ? state.clients.filter((client) => client.searchIndex.includes(query))
        : state.clients;

      list.replaceChildren(...visibleClients.map(createClientCard));

      if (countLabel) {
        countLabel.textContent = query && state.clients.length > 0
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
          emptyState.textContent = "Nenhum cliente encontrado para essa pesquisa.";
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

    if (searchInput) {
      searchInput.addEventListener("input", renderState);
    }

    modal.addEventListener("close", resetModalState);
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        closeModal();
      }
    });

    list.addEventListener("click", (event) => {
      const viewButton = event.target.closest("[data-client-view]");
      const editButton = event.target.closest("[data-client-edit]");
      const deleteButton = event.target.closest("[data-client-delete]");

      if (viewButton) {
        const clientItem = viewButton.closest("[data-client-item]");
        if (!clientItem) {
          return;
        }

        const record = state.clients.find((client) => client.id === clientItem.dataset.clientId);
        if (record) {
          openModal(record, "view");
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
    logInfo("Trocando authorization code por tokens.", { redirectUri });
    try {
      const response = await fetch(authEndpoint("token"), {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          client_id: CONFIG.keycloak.clientId,
          code,
          redirect_uri: redirectUri,
          code_verifier: verifier,
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
        endpoint: authEndpoint("token"),
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
    const messages = {
      forbidden: "Sua conta não possui a role USER-GM.",
      token_exchange_failed: "Não foi possível obter o JWT do Keycloak. Verifique Web Origins/CORS.",
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

      updateText(loginStatus, deniedMessage || "Clique em Entrar com SSO para autenticar via Keycloak.");
    } catch (_error) {
      updateText(loginStatus, "Não foi possível validar a sessão agora. Tente novamente.");
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

    try {
      const session = await ensureSession({ interactive: false, redirectUri: buildUrl(CONFIG.pages.panel) });
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

      setupPanelNavigation();
      setupClientsCrud(session);
      setupConciliadorDraft();
      setPanelView(getInitialPanelView(), { updateUrl: false });
      updatePanelWithSession(session);
      document.body.classList.add("panel-ready");
      await testProtectedEndpoint(session);

      if (sessionStatus) {
        sessionStatus.textContent = session.roles.includes(CONFIG.keycloak.requiredRole)
          ? "USER-GM OK"
          : "SEM USER-GM";
      }

      if (logoutButton) {
        logoutButton.addEventListener("click", async () => {
          logInfo("Clique em Sair.");
          await logout(session);
        });
      }
    } catch (_error) {
      logError("Falha ao abrir o painel.", _error);
      clearAppState();
      redirectToLogin("token_exchange_failed");
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
