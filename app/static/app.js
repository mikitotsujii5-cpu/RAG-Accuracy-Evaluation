"use strict";

const state = {
  projects: [],
  projectId: null,
  projectGeneration: 0,
  currentUser: null,
  projectDeletion: null,
  documentDeletions: new Map(),
  sessionDeletions: new Set(),
  confirmation: null,
  page: "data-preparation",
  documents: [],
  buildDocumentIds: new Set(),
  buildEligibleIds: new Set(),
  buildSelectionInitialized: false,
  variants: [],
  indexProfiles: [],
  indexProfilesStatus: "idle",
  selectedIndexProfile: null,
  embeddingModels: [],
  chatModels: [],
  judgeModels: [],
  evaluationDatasets: [],
  evaluationCases: [],
  evaluationCaseSelections: new Map(),
  evaluationRuns: [],
  sessions: [],
  sessionListOperation: 0,
  sessionId: null,
  sessionOperation: 0,
  sessionCreation: null,
  sessionCreationController: null,
  sessionListController: null,
  sessionCache: new Map(),
  sessionDrafts: new Map(),
  sessionRequests: new Map(),
  sessionPrefetchTimer: null,
  sessionLoadController: null,
  sessionLoadingId: null,
  chatSubmission: null,
  chatInputComposing: false,
  chatCompositionEndedAt: 0,
  activeChatRuns: new Map(),
  chatCancelRequests: new Map(),
  chatDetailsOpen: false,
  streamTimer: null,
  preparationMonitor: null,
  preparationRun: null,
  preparationRunProjectId: null,
  preparationRestore: null,
  preparationRequests: new Map(),
  evaluationRunId: null,
  evaluationPoll: null,
  evaluationController: null,
  evaluationOperation: 0,
  catalogView: "cards",
  pdfPrefetches: new Set(),
};

const PHASES = {
  phase_01: { label: "Phase 1", query: "Vector Search", filter: false, rerank: false, optimize: false },
  phase_02: { label: "Phase 2", query: "Hybrid Search", filter: false, rerank: false, optimize: false },
  phase_03: { label: "Phase 3", query: "Hybrid Search", filter: true, rerank: false, optimize: false },
  phase_04: { label: "Phase 4", query: "Hybrid Search", filter: true, rerank: true, optimize: false },
  phase_05: { label: "Phase 5", query: "Hybrid Search", filter: true, rerank: true, optimize: true },
};

const PREPARATION_TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELED"]);
const PREPARATION_POLL_INTERVAL_MS = 3000;
const PREPARATION_RETRY_MAX_MS = 30000;
const PREPARATION_STORAGE_PREFIX = "rag-eval-active-preparation-run:";
const BUILD_BUTTON_LABEL = "RAG検索データを作成・同期";
const MAX_CUSTOM_METADATA_FIELDS = 20;
const MAX_BUILD_DOCUMENTS = 100;
const SESSION_CACHE_TTL_MS = 60_000;
const SESSION_PREFETCH_LIMIT = 3;
const NEW_SESSION_DRAFT_KEY = "__new_session__";
let metadataRowCounter = 0;

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

document.addEventListener("DOMContentLoaded", initialize);

async function initialize() {
  installEventHandlers();
  restoreTheme();
  restoreChatPreferences();
  readRoute();
  updatePhaseSummary();
  await Promise.allSettled([checkHealth(), loadCurrentUser()]);
  await loadProjects();
}

function installEventHandlers() {
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.page)));
  $$('[data-navigate]').forEach((button) => button.addEventListener("click", () => navigate(button.dataset.navigate)));
  $("#project-select").addEventListener("change", (event) => switchProject(event.target.value));
  $("#create-project-button").addEventListener("click", openProjectModal);
  $("#delete-project-button").addEventListener("click", deleteCurrentProject);
  $$('[data-open-project-modal]').forEach((button) => button.addEventListener("click", openProjectModal));
  $("#project-modal-cancel").addEventListener("click", closeProjectModal);
  $("#project-form").addEventListener("submit", createProject);
  $("#project-modal").addEventListener("click", (event) => { if (event.target === event.currentTarget) closeProjectModal(); });
  $("#confirm-modal-cancel").addEventListener("click", () => closeConfirmation(false));
  $("#confirm-modal-accept").addEventListener("click", () => closeConfirmation(true));
  $("#confirm-modal").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeConfirmation(false);
  });
  $("#refresh-button").addEventListener("click", refreshPage);
  $("#theme-button").addEventListener("click", toggleTheme);
  $("#sidebar-toggle").addEventListener("click", toggleSidebar);

  const dropZone = $("#drop-zone");
  const input = $("#pdf-input");
  dropZone.addEventListener("click", () => input.click());
  dropZone.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); input.click(); } });
  input.addEventListener("change", () => setSelectedFile(input.files[0]));
  ["dragenter", "dragover"].forEach((name) => dropZone.addEventListener(name, (event) => { event.preventDefault(); dropZone.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach((name) => dropZone.addEventListener(name, (event) => { event.preventDefault(); dropZone.classList.remove("dragging"); }));
  dropZone.addEventListener("drop", (event) => {
    const files = event.dataTransfer.files;
    if (files.length) { input.files = files; setSelectedFile(files[0]); }
  });
  $("#upload-form").addEventListener("submit", uploadDocument);
  $("#upload-metadata-details").addEventListener("toggle", (event) => {
    const action = event.currentTarget.querySelector(".optional-panel-action");
    if (action) action.textContent = event.currentTarget.open ? "閉じる" : "開く";
  });
  $("#add-metadata-button").addEventListener("click", () => addCustomMetadataRow("", "", true));
  $("#custom-metadata-rows").addEventListener("click", (event) => {
    const removeButton = event.target.closest("[data-remove-metadata-row]");
    if (removeButton) removeButton.closest(".custom-metadata-row")?.remove();
  });
  resetCustomMetadataRows();
  $$('input[name="chunk-method"]').forEach((radio) => radio.addEventListener("change", () => {
    updateMethodCards();
    updateIndexProfileSelection();
  }));
  $$('input[name="chunk-size"]').forEach((radio) => radio.addEventListener("change", updateIndexProfileSelection));
  [$("#cleaning-toggle"), $("#semantic-toggle"), $("#layout-toggle")]
    .forEach((input) => input.addEventListener("change", updateIndexProfileSelection));
  $("#embedding-model").addEventListener("change", () => {
    showModelDetail("embedding");
    updateIndexProfileSelection();
  });
  $("#build-button").addEventListener("click", buildVariant);
  $("#select-all-build-documents").addEventListener("click", () => {
    state.buildDocumentIds = new Set(eligibleBuildDocuments().slice(0, MAX_BUILD_DOCUMENTS).map((doc) => doc.document_id));
    state.buildSelectionInitialized = true;
    renderBuildDocumentOptions();
  });
  $("#clear-build-documents").addEventListener("click", () => {
    state.buildDocumentIds.clear();
    state.buildSelectionInitialized = true;
    renderBuildDocumentOptions();
  });
  $("#build-document-list").addEventListener("change", (event) => {
    const checkbox = event.target.closest('input[data-build-document-id]');
    if (!checkbox) return;
    const documentId = checkbox.dataset.buildDocumentId;
    if (checkbox.checked && state.buildDocumentIds.size >= MAX_BUILD_DOCUMENTS) {
      checkbox.checked = false;
      toast("1回に選べるPDFは100件までです。");
      return;
    }
    if (checkbox.checked) state.buildDocumentIds.add(documentId);
    else state.buildDocumentIds.delete(documentId);
    renderBuildDocumentOptions();
  });
  $("#refresh-variants").addEventListener("click", () => loadVariants());

  $("#catalog-search").addEventListener("input", renderCatalog);
  $("#catalog-status-filter").addEventListener("change", renderCatalog);
  $$('[data-catalog-view]').forEach((button) => button.addEventListener("click", () => setCatalogView(button.dataset.catalogView)));
  $("#viewer-close").addEventListener("click", closeViewer);
  $("#viewer-scrim").addEventListener("click", closeViewer);
  $("#viewer-frame").addEventListener("load", (event) => {
    const frame = event.currentTarget;
    if (frame.dataset.requestedHref && frame.getAttribute("src") !== "about:blank") {
      frame.dataset.loadedHref = frame.dataset.requestedHref;
    }
    $("#viewer-loading").classList.add("hidden");
  });

  $("#new-chat-button").addEventListener("click", () => createSession());
  $("#session-search").addEventListener("input", renderSessions);
  $("#chat-phase").addEventListener("change", updatePhaseSummary);
  $("#chat-variant").addEventListener("change", updateRuntimeIndexDetails);
  $("#chat-model").addEventListener("change", () => showModelDetail("chat"));
  $("#chat-form").addEventListener("submit", sendChatMessage);
  const chatInput = $("#chat-input");
  chatInput.addEventListener("input", () => {
    saveChatDraft();
    resizeChatInput();
  });
  chatInput.addEventListener("compositionstart", () => {
    state.chatInputComposing = true;
  });
  chatInput.addEventListener("compositionend", () => {
    state.chatInputComposing = false;
    state.chatCompositionEndedAt = performance.now();
  });
  chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && activeChatRun() && !event.isComposing) {
      event.preventDefault();
      void stopChat();
      return;
    }
    if (event.key !== "Enter" || event.shiftKey) return;
    // Japanese IME confirms text with Enter.  Repeated keydown events must not
    // create multiple turns while the first submit is still being prepared.
    if (
      event.isComposing
      || state.chatInputComposing
      || event.keyCode === 229
      || performance.now() - state.chatCompositionEndedAt < 50
    ) return;
    event.preventDefault();
    if (event.repeat || state.chatSubmission || activeChatRun()) return;
    $("#chat-form").requestSubmit();
  });
  $("#stop-button").addEventListener("click", stopChat);
  $("#chat-details-toggle").addEventListener("click", () => {
    setChatDetailsOpen(!state.chatDetailsOpen, { persist: true });
  });
  $$(".suggestion-chips button").forEach(bindSuggestionButton);
  $$('[data-chat-tab]').forEach((button) => {
    button.addEventListener("click", () => setChatTab(button.dataset.chatTab));
    button.addEventListener("keydown", handleChatTabKeydown);
  });

  $("#start-evaluation").addEventListener("click", startEvaluation);
  $("#evaluation-variant").addEventListener("change", updateRuntimeIndexDetails);
  $("#cancel-evaluation").addEventListener("click", cancelEvaluation);
  $("#refresh-evaluation-runs").addEventListener("click", () => loadEvaluationRuns());
  $("#evaluation-case-form").addEventListener("submit", createEvaluationCase);
  $("#evaluation-case-editor").addEventListener("toggle", (event) => {
    const action = event.currentTarget.querySelector(".optional-panel-action");
    if (action) action.textContent = event.currentTarget.open ? "閉じる" : "開く";
  });
  $("#select-all-evaluation-cases").addEventListener("click", () => setAllEvaluationCasesSelected(true));
  $("#clear-evaluation-cases").addEventListener("click", () => setAllEvaluationCasesSelected(false));
  $("#evaluation-case-list").addEventListener("change", handleEvaluationCaseSelection);
  $$('input[name="eval-phase"]').forEach((input) => input.addEventListener("change", updateEvaluationSelectionUi));
  $("#trial-count").addEventListener("input", updateEvaluationSelectionUi);
  $("#dataset-version").addEventListener("change", () => {
    syncEvaluationDatasetSplit();
    loadEvaluationCases();
  });
  $("#dataset-split").addEventListener("change", () => {
    $("#evaluation-case-split").value = $("#dataset-split").value;
    loadEvaluationCases();
  });
  window.addEventListener("popstate", async () => {
    const route = routeStateFromLocation();
    state.page = route.page;
    state.routeDocumentId = route.routeDocumentId;
    if (route.projectId !== state.projectId) {
      await switchProject(route.projectId, { historyMode: "none", routeDocumentId: route.routeDocumentId });
      if (route.projectId && !state.projectId) history.replaceState({}, "", "/");
      return;
    }
    if (!state.projectId) {
      resetProjectBoundState();
      updateProjectChrome(null);
      return;
    }
    await showCurrentPage(currentProjectScope());
  });
  window.addEventListener("resize", () => {
    if (state.lastMetrics?.length) drawPhaseChart(state.lastMetrics);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.confirmation) closeConfirmation(false);
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...(options.headers || {}) },
  });
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `処理に失敗しました（HTTP ${response.status}）`);
    error.code = payload?.error?.code;
    error.details = payload?.error;
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function checkHealth() {
  try {
    const health = await api("/api/health");
    const banner = $("#configuration-banner");
    if (!health.databricks_ready) {
      banner.textContent = `管理者によるDatabricksリソース設定が必要です：${health.missing.join("、")}`;
      banner.classList.remove("hidden");
    } else {
      banner.textContent = "";
      banner.classList.add("hidden");
    }
  } catch (error) {
    showError(error);
  }
}

async function loadCurrentUser() {
  const email = $("#current-user-email");
  const avatar = $("#current-user-avatar");
  try {
    const user = await api("/api/me");
    state.currentUser = user;
    const label = user.email || user.user_name || user.principal || user.display_name || "Databricksユーザー";
    email.textContent = label;
    avatar.textContent = String(user.display_name || label).trim().charAt(0).toUpperCase() || "U";
    $("#current-user").title = user.display_name && user.display_name !== label
      ? `${user.display_name} · ${label}`
      : `Databricks Apps ログインユーザー: ${label}`;
  } catch (_) {
    // Authentication is enforced by Databricks Apps. Keep the app usable when
    // an older deployment does not yet expose the /api/me endpoint.
    email.textContent = "Databricksにログイン中";
    avatar.textContent = "U";
  }
}

async function loadProjects(preferredId = null) {
  try {
    const result = await api("/api/projects");
    state.projects = result.items || [];
    const select = $("#project-select");
    select.replaceChildren(new Option("プロジェクトを選択", ""));
    state.projects.forEach((project) => select.add(new Option(project.name, project.project_id)));
    const routeId = preferredId || state.projectId;
    const routeDocumentId = state.routeDocumentId || null;
    const storedId = localStorage.getItem("rag-eval-project-id");
    const selected = state.projects.find((item) => item.project_id === routeId)
      || state.projects.find((item) => item.project_id === storedId)
      || state.projects[0];
    if (selected) {
      select.value = selected.project_id;
      await switchProject(selected.project_id, {
        historyMode: "replace",
        routeDocumentId: selected.project_id === routeId ? routeDocumentId : null,
      });
    } else {
      await switchProject(null, { historyMode: "replace" });
    }
  } catch (error) {
    state.projects = [];
    await switchProject(null, { historyMode: "replace" });
    showError(error);
  }
}

async function switchProject(projectId, { historyMode = "push", routeDocumentId = null } = {}) {
  const project = state.projects.find((item) => item.project_id === projectId) || null;
  const nextProjectId = project?.project_id || null;
  stopActiveStreams();
  state.projectGeneration += 1;
  state.projectId = nextProjectId;
  resetProjectBoundState();
  state.routeDocumentId = nextProjectId ? routeDocumentId : null;
  $("#project-select").value = nextProjectId || "";

  if (!nextProjectId) {
    localStorage.removeItem("rag-eval-project-id");
    updateProjectChrome(null);
    if (historyMode === "push") history.pushState({}, "", "/");
    if (historyMode === "replace") history.replaceState({}, "", "/");
    return;
  }

  localStorage.setItem("rag-eval-project-id", nextProjectId);
  updateProjectChrome(project);
  const route = projectRoute(nextProjectId, state.page, routeDocumentId);
  if (historyMode === "push") history.pushState({}, "", route);
  if (historyMode === "replace") history.replaceState({}, "", route);
  const scope = currentProjectScope();
  await loadProjectData(scope);
  if (!isCurrentProjectScope(scope)) return;
  await showCurrentPage(scope);
}

function updateProjectChrome(project) {
  $$('[data-project-name]').forEach((element) => { element.textContent = project?.name || "プロジェクト"; });
  const status = $("#project-status");
  status.textContent = project ? formatStatus(project.status || "READY") : "未選択";
  status.className = project ? `status-pill ${statusClass(project.status)}` : "status-pill muted";
  const deleteButton = $("#delete-project-button");
  const canDeleteProject = project?.role === "OWNER";
  deleteButton.disabled = !canDeleteProject || Boolean(state.projectDeletion);
  deleteButton.title = !project
    ? "プロジェクトを選択してください"
    : canDeleteProject ? `「${project.name}」を削除` : "OWNERだけがプロジェクトを削除できます";
  showOnboarding(!project);
}

function currentProjectScope() {
  return state.projectId ? { projectId: state.projectId, generation: state.projectGeneration } : null;
}

function isCurrentProjectScope(scope) {
  return Boolean(scope
    && state.projectId === scope.projectId
    && state.projectGeneration === scope.generation);
}

async function loadProjectData(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  const restore = restoreActivePreparationRunForScope(scope);
  state.preparationRestore = restore;
  await Promise.allSettled([
    loadDocuments(scope), loadVariants(scope), loadIndexProfiles(scope), loadModels(scope), loadSessions(scope),
    loadEvaluationData(scope), loadEvaluationRuns(scope), restore,
  ]);
  if (state.preparationRestore === restore) state.preparationRestore = null;
}

async function loadModels(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  const requests = ["embedding", "chat", "judge"].map((capability) => api(`/api/model-options?capability=${capability}`));
  try {
    const [embedding, chat, judge] = await Promise.all(requests);
    if (!isCurrentProjectScope(scope)) return;
    state.embeddingModels = embedding.items || [];
    state.chatModels = chat.items || [];
    state.judgeModels = judge.items || [];
    populateModelSelect($("#embedding-model"), state.embeddingModels, { preferEmbeddingDefault: true });
    populateModelSelect($("#chat-model"), state.chatModels);
    populateModelSelect($("#evaluation-model"), state.chatModels);
    populateModelSelect($("#judge-model"), state.judgeModels);
    selectCompatibleEmbeddingModel();
    showModelDetail("embedding");
    showModelDetail("chat");
    updateIndexProfileSelection();
  } catch (error) {
    if (!isCurrentProjectScope(scope)) return;
    state.embeddingModels = [];
    state.chatModels = [];
    state.judgeModels = [];
    [$("#embedding-model"), $("#chat-model"), $("#evaluation-model"), $("#judge-model")]
      .forEach((select) => { select.replaceChildren(new Option("モデル一覧を取得できません", "")); });
    updateIndexProfileSelection();
    showError(error, false);
  }
}

async function loadIndexProfiles(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  state.indexProfilesStatus = "loading";
  state.selectedIndexProfile = null;
  updateIndexProfileSelection();
  try {
    const result = await api("/api/index-profiles");
    if (!isCurrentProjectScope(scope)) return;
    state.indexProfiles = (result?.items || []).map(normalizeIndexProfile).filter(Boolean);
    state.indexProfilesStatus = "ready";
    selectCompatibleEmbeddingModel();
    updateIndexProfileSelection();
  } catch (error) {
    if (!isCurrentProjectScope(scope)) return;
    state.indexProfiles = [];
    state.indexProfilesStatus = "error";
    state.selectedIndexProfile = null;
    updateIndexProfileSelection();
    showError(error, false);
  }
}

function populateModelSelect(select, models, { preferEmbeddingDefault = false } = {}) {
  select.replaceChildren();
  const selectable = models.filter((model) => model.selectable);
  if (!models.length) { select.add(new Option("利用可能なモデルがありません", "")); return; }
  models.forEach((model) => {
    const labels = [];
    if (preferEmbeddingDefault && (model.is_default || model.is_recommended)) labels.push("推奨");
    if (preferEmbeddingDefault && isQwenEmbedding06B(model)) labels.push("日本語対応");
    const option = new Option(
      `${model.display_name}${labels.length ? ` — ${labels.join("・")}` : ""}${model.selectable ? "" : ` — ${model.unavailable_reason || "選択不可"}`}`,
      model.model_key,
    );
    option.disabled = !model.selectable;
    select.add(option);
  });
  if (selectable.length) {
    const preferred = preferEmbeddingDefault
      ? selectable.find(isQwenEmbedding06B)
        || selectable.find((model) => model.is_default)
        || selectable.find((model) => model.is_recommended)
      : null;
    select.value = (preferred || selectable[0]).model_key;
  }
}

function isQwenEmbedding06B(model) {
  const value = `${model?.model_key || ""} ${model?.display_name || ""}`.toLowerCase();
  return value.includes("qwen3") && /(0[.\s_-]?6b|06b)/.test(value);
}

function showModelDetail(kind) {
  const select = kind === "embedding" ? $("#embedding-model") : $("#chat-model");
  const target = kind === "embedding" ? $("#embedding-model-detail") : $("#chat-model-detail");
  const models = kind === "embedding" ? state.embeddingModels : state.chatModels;
  const model = models.find((item) => item.model_key === select.value);
  if (!model) { target.textContent = "利用できるモデルを選択してください。"; return; }
  const details = [];
  if (model.dimension) details.push(`次元数 ${Number(model.dimension).toLocaleString()}`);
  if (model.max_context_tokens) details.push(`最大入力 ${Number(model.max_context_tokens).toLocaleString()} tokens`);
  if (model.capabilities?.length) details.push(`機能: ${model.capabilities.map(formatCapability).join("、")}`);
  details.push(`状態: ${formatStatus(model.state || "READY")}`);
  target.textContent = details.join(" / ");
}

function normalizeIndexProfile(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const config = raw.configuration && typeof raw.configuration === "object"
    ? raw.configuration
    : {};
  const profileKey = String(raw.profile_key || raw.index_profile_key || raw.variant_id || "").trim();
  if (!profileKey) return null;
  const chunkMethod = String(raw.chunk_method ?? config.chunk_method ?? "").toUpperCase();
  const rawChunkSize = raw.chunk_size_tokens ?? raw.chunk_size ?? config.chunk_size_tokens ?? config.chunk_size;
  const rawParentSize = raw.parent_chunk_size_tokens
    ?? raw.parent_chunk_size
    ?? config.parent_chunk_size_tokens
    ?? config.parent_chunk_size
    ?? null;
  const contentProfile = String(raw.content_profile ?? config.content_profile ?? "").toUpperCase();
  const indexName = String(raw.index_name || raw.index_fqn || "").trim();
  const sourceTable = String(raw.source_table || raw.source_table_name || "").trim();
  const unavailableReason = String(raw.unavailable_reason || "").trim();
  const status = String(raw.status || "READY").toUpperCase();
  const enabled = raw.enabled !== false
    && raw.selectable !== false
    && raw.available !== false
    && !["DISABLED", "UNAVAILABLE", "ERROR"].includes(status)
    && !unavailableReason;
  return {
    ...raw,
    profile_key: profileKey,
    display_name: String(raw.display_name || raw.name || profileKey),
    chunk_method: chunkMethod,
    chunk_size_tokens: Number(rawChunkSize),
    parent_chunk_size_tokens: rawParentSize == null ? null : Number(rawParentSize),
    content_profile: contentProfile,
    cleaning_enabled: raw.cleaning_enabled ?? config.cleaning_enabled,
    semantic_metadata_enabled: raw.semantic_metadata_enabled ?? config.semantic_metadata_enabled,
    embedding_model_key: String(raw.embedding_model_key ?? config.embedding_model_key ?? ""),
    index_name: indexName,
    source_table: sourceTable,
    enabled,
    status,
    unavailable_reason: unavailableReason,
  };
}

function currentPreparationConfiguration() {
  const method = $('input[name="chunk-method"]:checked')?.value || "";
  const size = Number($('input[name="chunk-size"]:checked')?.value || 0);
  return {
    chunk_method: method,
    chunk_size_tokens: size,
    parent_chunk_size_tokens: method === "PARENT_CHILD" ? Math.min(size * 4, 4096) : null,
    content_profile: $("#layout-toggle")?.checked ? "LAYOUT_PRESERVING" : "TEXT_ONLY",
    cleaning_enabled: Boolean($("#cleaning-toggle")?.checked),
    semantic_metadata_enabled: Boolean($("#semantic-toggle")?.checked),
    embedding_model_key: $("#embedding-model")?.value || "",
  };
}

function indexProfileMatchesConfiguration(profile, configuration) {
  return Boolean(
    profile?.enabled
    && profile.index_name
    && profile.chunk_method === configuration.chunk_method
    && profile.chunk_size_tokens === configuration.chunk_size_tokens
    && profile.parent_chunk_size_tokens === configuration.parent_chunk_size_tokens
    && profile.content_profile === configuration.content_profile
    && profile.cleaning_enabled === configuration.cleaning_enabled
    && profile.semantic_metadata_enabled === configuration.semantic_metadata_enabled
    && profile.embedding_model_key === configuration.embedding_model_key
  );
}

function matchingIndexProfiles(configuration = currentPreparationConfiguration()) {
  if (!configuration.embedding_model_key) return [];
  return state.indexProfiles.filter((profile) => indexProfileMatchesConfiguration(profile, configuration));
}

function selectedIndexProfileForCurrentSettings() {
  const matches = matchingIndexProfiles();
  return matches.length === 1 ? matches[0] : null;
}

function indexProfileMatchesConfigurationExceptEmbedding(profile, configuration) {
  return Boolean(
    profile?.enabled
    && profile.index_name
    && profile.chunk_method === configuration.chunk_method
    && profile.chunk_size_tokens === configuration.chunk_size_tokens
    && profile.parent_chunk_size_tokens === configuration.parent_chunk_size_tokens
    && profile.content_profile === configuration.content_profile
    && profile.cleaning_enabled === configuration.cleaning_enabled
    && profile.semantic_metadata_enabled === configuration.semantic_metadata_enabled
  );
}

function selectCompatibleEmbeddingModel() {
  const select = $("#embedding-model");
  if (!select || state.indexProfilesStatus !== "ready" || !state.embeddingModels.length) return false;

  const configuration = currentPreparationConfiguration();
  const currentMatches = matchingIndexProfiles(configuration);
  if (currentMatches.length) return false;

  const compatibleProfiles = state.indexProfiles.filter(
    (profile) => indexProfileMatchesConfigurationExceptEmbedding(profile, configuration),
  );
  const matchesPerModel = compatibleProfiles.reduce((counts, profile) => {
    counts.set(profile.embedding_model_key, (counts.get(profile.embedding_model_key) || 0) + 1);
    return counts;
  }, new Map());
  const candidates = state.embeddingModels.filter((model) => (
    model.selectable && matchesPerModel.get(model.model_key) === 1
  ));
  if (!candidates.length) return false;

  const preferred = candidates.find(isQwenEmbedding06B)
    || candidates.find((model) => model.is_default)
    || candidates.find((model) => model.is_recommended)
    || candidates[0];
  if (!preferred || select.value === preferred.model_key) return false;
  select.value = preferred.model_key;
  showModelDetail("embedding");
  return true;
}

function updateIndexProfileSelection() {
  const panel = $("#index-profile-match");
  const indexName = $("#selected-index-name");
  const message = $("#index-profile-message");
  if (!panel || !indexName || !message) return;

  const matches = matchingIndexProfiles();
  const profile = matches.length === 1 ? matches[0] : null;
  state.selectedIndexProfile = profile;
  panel.classList.toggle("available", Boolean(profile));
  panel.classList.toggle("unavailable", !profile && state.indexProfilesStatus !== "loading");

  if (state.indexProfilesStatus === "loading") {
    indexName.textContent = "確認中…";
    message.textContent = "事前登録済みのIndexを確認しています。";
  } else if (profile) {
    indexName.textContent = profile.index_name;
    message.textContent = `${profile.display_name} に完全一致しました。既存Indexへデータを書き込み、同期します。`;
  } else {
    indexName.textContent = "未登録";
    message.textContent = matches.length > 1
      ? "一致するIndex設定が複数あります。管理者に登録内容の確認を依頼してください。"
      : "この設定は管理者によるIndex準備が必要です";
  }
  setPreparationControls();
}

function variantIndexName(variant) {
  return String(variant?.index_name || variant?.index_fqn || variant?.index_display_name || "").trim();
}

function updateRuntimeIndexDetails() {
  const pairs = [
    [$("#chat-variant"), $("#chat-index-detail")],
    [$("#evaluation-variant"), $("#evaluation-index-detail")],
  ];
  pairs.forEach(([select, detail]) => {
    if (!select || !detail) return;
    const variant = state.variants.find((item) => item.variant_id === select.value);
    const name = variantIndexName(variant);
    detail.textContent = name ? `使用するIndex: ${name}` : "使用するIndexを選択してください。";
  });
}

function openProjectModal() {
  $("#project-modal").classList.remove("hidden");
  setTimeout(() => $("#project-name-input").focus(), 0);
}
function closeProjectModal() { $("#project-modal").classList.add("hidden"); }

function requestConfirmation({ title, message, confirmLabel = "削除する" }) {
  if (state.confirmation) return Promise.resolve(false);
  $("#confirm-modal-title").textContent = title;
  $("#confirm-modal-message").textContent = message;
  $("#confirm-modal-accept").textContent = confirmLabel;
  $("#confirm-modal").classList.remove("hidden");
  return new Promise((resolve) => {
    state.confirmation = { resolve };
    setTimeout(() => $("#confirm-modal-cancel").focus(), 0);
  });
}

function closeConfirmation(accepted = false) {
  const confirmation = state.confirmation;
  if (!confirmation) return;
  state.confirmation = null;
  $("#confirm-modal").classList.add("hidden");
  confirmation.resolve(Boolean(accepted));
}

function projectHasActiveOperation() {
  return hasActivePreparationRun()
    || state.documentDeletions.size > 0
    || state.activeChatRuns.size > 0
    || Boolean(state.chatSubmission || state.sessionCreation)
    || $("#page-evaluation").dataset.running === "true"
    || Boolean(state.evaluationPoll || state.evaluationController)
    || !$("#cancel-evaluation").classList.contains("hidden");
}

async function deleteCurrentProject() {
  const scope = currentProjectScope();
  const project = state.projects.find((item) => item.project_id === scope?.projectId);
  if (!project || state.projectDeletion) return;
  if (projectHasActiveOperation()) {
    showError(new Error("実行中のデータ準備・回答・評価を完了または停止してから、プロジェクトを削除してください。"));
    return;
  }
  const confirmed = await requestConfirmation({
    title: "プロジェクトを削除しますか？",
    message: `「${project.name}」を画面と通常操作から削除します。監査・復旧に備え、PDFや評価記録の実データは管理領域に保持されます。`,
    confirmLabel: "プロジェクトを削除",
  });
  if (!confirmed || !isCurrentProjectScope(scope) || projectHasActiveOperation()) return;

  const button = $("#delete-project-button");
  state.projectDeletion = { ...scope };
  setBusy(button, true, "削除中…");
  try {
    await api(`/api/projects/${scope.projectId}`, { method: "DELETE" });
    if (!isCurrentProjectScope(scope)) return;
    const remaining = state.projects.filter((item) => item.project_id !== scope.projectId);
    state.projects = remaining;
    toast("プロジェクトを削除しました。");
    await loadProjects(remaining[0]?.project_id || null);
  } catch (error) {
    if (isCurrentProjectScope(scope)) showError(error);
  } finally {
    state.projectDeletion = null;
    setBusy(button, false, "プロジェクトを削除");
    updateProjectChrome(state.projects.find((item) => item.project_id === state.projectId) || null);
  }
}

async function createProject(event) {
  event.preventDefault();
  // `Event.currentTarget` is only guaranteed while the synchronous event
  // listener is running. Keep the form before the first await so a successful
  // request can safely reset it after the browser clears currentTarget.
  const formElement = event.currentTarget;
  const submit = formElement.querySelector('[type="submit"]');
  setBusy(submit, true, "作成中…");
  try {
    const project = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name: $("#project-name-input").value, description: $("#project-description-input").value }),
    });
    closeProjectModal();
    formElement.reset();
    toast("プロジェクトを作成しました。");
    await loadProjects(project.project_id);
  } catch (error) { showError(error); }
  finally { setBusy(submit, false, "プロジェクトを作成"); }
}

function routeStateFromLocation() {
  const pieces = location.pathname.split("/").filter(Boolean);
  if (pieces[0] === "projects" && pieces[1]) {
    return {
      projectId: pieces[1],
      page: ["data-preparation", "catalog", "chat", "evaluation"].includes(pieces[2]) ? pieces[2] : "data-preparation",
      routeDocumentId: pieces[2] === "catalog" && pieces[3] ? pieces[3] : null,
    };
  }
  return { projectId: null, page: "data-preparation", routeDocumentId: null };
}

function readRoute() {
  const route = routeStateFromLocation();
  state.projectId = route.projectId;
  state.page = route.page;
  state.routeDocumentId = route.routeDocumentId;
  return route;
}

function projectRoute(projectId, page, documentId = null) {
  const base = `/projects/${projectId}/${page}`;
  if (page !== "catalog" || !documentId) return base;
  return `${base}/${documentId}${location.search || ""}`;
}

function ensureProjectRoute() {
  if (!state.projectId) return;
  const expectedPrefix = `/projects/${state.projectId}/`;
  if (!location.pathname.startsWith(expectedPrefix)) history.replaceState({}, "", `${expectedPrefix}${state.page}`);
}

async function navigate(page) {
  if (!state.projectId) { openProjectModal(); return; }
  const scope = currentProjectScope();
  state.page = page;
  state.routeDocumentId = null;
  closeViewer();
  history.pushState({}, "", `/projects/${state.projectId}/${page}`);
  closeSidebar();
  await showCurrentPage(scope);
}

async function showCurrentPage(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  $$("[data-page-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.pagePanel === state.page));
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.page === state.page));
  if (state.page === "catalog") {
    renderCatalog();
    if (state.routeDocumentId) openViewer(state.routeDocumentId, Number(new URLSearchParams(location.search).get("page")) || 1);
  } else if (state.page === "chat") {
    await ensureSession(scope);
  } else if (state.page === "evaluation") {
    syncEvaluationSelects();
  }
}

function showOnboarding(show) {
  $("#project-onboarding").classList.toggle("hidden", !show);
  $$("[data-page-panel]").forEach((panel) => panel.classList.toggle("hidden", show));
}

function resetProjectBoundState() {
  state.documents = [];
  state.buildDocumentIds = new Set();
  state.buildEligibleIds = new Set();
  state.buildSelectionInitialized = false;
  state.variants = [];
  state.indexProfiles = [];
  state.indexProfilesStatus = "idle";
  state.selectedIndexProfile = null;
  state.sessions = [];
  state.evaluationDatasets = [];
  state.evaluationCases = [];
  state.evaluationCaseSelections = new Map();
  state.evaluationRuns = [];
  state.sessionId = null;
  state.sessionOperation += 1;
  state.sessionListOperation += 1;
  state.sessionCreation = null;
  state.sessionCreationController?.abort();
  state.sessionCreationController = null;
  state.sessionListController?.abort();
  state.sessionListController = null;
  state.sessionLoadController?.abort();
  state.sessionLoadController = null;
  state.sessionLoadingId = null;
  state.sessionCache = new Map();
  state.sessionDrafts = new Map();
  state.documentDeletions = new Map();
  state.sessionDeletions = new Set();
  clearTimeout(state.sessionPrefetchTimer);
  state.sessionPrefetchTimer = null;
  state.sessionRequests.forEach((request) => request.controller.abort());
  state.sessionRequests = new Map();
  state.chatSubmission = null;
  state.chatInputComposing = false;
  state.chatCompositionEndedAt = 0;
  state.activeChatRuns = new Map();
  state.chatCancelRequests.forEach((request) => request.controller?.abort());
  state.chatCancelRequests = new Map();
  state.preparationRestore = null;
  state.routeDocumentId = null;
  state.pdfPrefetches = new Set();

  closeViewer({ reset: true });
  $("#upload-form").reset();
  $("#upload-metadata-details").open = false;
  $("#evaluation-case-form").reset();
  $("#evaluation-case-editor").open = false;
  setSelectedFile(null);
  $("#catalog-search").value = "";
  $("#catalog-status-filter").value = "";
  renderCatalog();
  renderBuildDocumentOptions();
  renderVariants();
  populateVariantSelects();
  renderSessions();
  resetChatUi();
  resetPreparationUi();
  resetEvaluationUi();
  populateEvaluationDatasets();
  renderEvaluationCases();
  renderEvaluationRuns();
  updateMethodCards();
  updateIndexProfileSelection();
  setPreparationControls();
  $("#refresh-button").disabled = false;
  $("#global-error").classList.add("hidden");
  $("#toast-region").replaceChildren();
}

function resetPreparationUi() {
  setPrepProgress("upload", 0, "PDFを選択して開始してください。");
}

function resetChatUi() {
  $("#chat-input").value = "";
  resizeChatInput();
  $("#session-search").value = "";
  renderMessages([]);
  $("#evidence-list").replaceChildren(emptyNode("質問すると検索されたPDFを表示します。"));
  $("#citation-list").replaceChildren();
  $("#query-chips").replaceChildren();
  setEvidenceCount(0);
  setChatTab("settings");
  setChatDetailsOpen(state.chatDetailsOpen);
  updateChatUi();
  $("#stream-timer").textContent = "0.0秒";
  setBusy($("#new-chat-button"), false, "＋ 新しい会話");
}

function resetEvaluationUi() {
  clearTimeout(state.evaluationPoll);
  state.evaluationPoll = null;
  state.evaluationController?.abort();
  state.evaluationController = null;
  state.evaluationOperation += 1;
  state.evaluationRunId = null;
  state.lastMetrics = [];
  setEvaluationRunningUi(false);
  $("#cancel-evaluation").classList.add("hidden");
  $("#evaluation-progress-card").classList.add("hidden");
  $("#evaluation-progress-label").textContent = "0 / 0";
  $("#evaluation-progress-bar").style.width = "0%";
  $("#phase-run-status").replaceChildren();
  $("#evaluation-progress-title").textContent = "Phase比較を実行中";
  renderMetrics([]);
  renderSuggestions([]);
}

function setSelectedFile(file) {
  $("#selected-file").textContent = file ? `${file.name} — ${formatBytes(file.size)}` : "まだ選択されていません";
}

function addCustomMetadataRow(key = "", value = "", focus = false) {
  const container = $("#custom-metadata-rows");
  if (container.children.length >= MAX_CUSTOM_METADATA_FIELDS) {
    toast(`追加メタデータは${MAX_CUSTOM_METADATA_FIELDS}項目までです。`, true);
    return;
  }
  metadataRowCounter += 1;
  const row = node("div", "custom-metadata-row");
  const keyLabel = document.createElement("label");
  const keyCaption = node("span", "sr-only", `追加メタデータ ${metadataRowCounter} の項目名`);
  const keyInput = document.createElement("input");
  keyInput.className = "custom-metadata-key";
  keyInput.maxLength = 80;
  keyInput.placeholder = "例：部署、製品名、版";
  keyInput.value = key;
  keyLabel.append(keyCaption, keyInput);

  const valueLabel = document.createElement("label");
  const valueCaption = node("span", "sr-only", `追加メタデータ ${metadataRowCounter} の値`);
  const valueInput = document.createElement("input");
  valueInput.className = "custom-metadata-value";
  valueInput.maxLength = 1000;
  valueInput.placeholder = "値を入力";
  valueInput.value = value;
  valueLabel.append(valueCaption, valueInput);

  const remove = node("button", "metadata-remove-button", "×");
  remove.type = "button";
  remove.dataset.removeMetadataRow = "";
  remove.setAttribute("aria-label", `追加メタデータ ${metadataRowCounter} を削除`);
  row.append(keyLabel, valueLabel, remove);
  container.append(row);
  if (focus) keyInput.focus();
}

function resetCustomMetadataRows() {
  $("#custom-metadata-rows").replaceChildren();
  addCustomMetadataRow();
}

function collectDocumentMetadata() {
  const customMetadata = Object.create(null);
  const normalizedKeys = new Set();
  const reservedKeys = new Set([
    "title", "category", "tags", "document_date", "source",
    "document_id", "project_id", "doc_uri",
  ]);
  $$(".custom-metadata-row", $("#custom-metadata-rows")).forEach((row) => {
    const key = $(".custom-metadata-key", row).value.trim();
    const value = $(".custom-metadata-value", row).value.trim();
    if (!key && !value) return;
    if (!key || !value) throw new Error("追加メタデータは、項目名と値を両方入力してください。");
    const normalizedKey = key.toLocaleLowerCase("ja-JP");
    if (key.length > 80) throw new Error("追加メタデータの項目名は80文字以内で入力してください。");
    if (value.length > 1000) throw new Error(`追加メタデータ「${key}」の値は1000文字以内で入力してください。`);
    if (/[\u0000-\u001f\u007f]/.test(key) || /[\u0000-\u001f\u007f]/.test(value)) {
      throw new Error("追加メタデータの項目名と値に制御文字は使えません。");
    }
    if (reservedKeys.has(normalizedKey)) throw new Error(`「${key}」は基本項目の名前なので、追加メタデータには使えません。`);
    if (normalizedKeys.has(normalizedKey)) throw new Error(`追加メタデータの項目名「${key}」が重複しています。`);
    normalizedKeys.add(normalizedKey);
    customMetadata[key] = value;
  });
  if (Object.keys(customMetadata).length > MAX_CUSTOM_METADATA_FIELDS) {
    throw new Error(`追加メタデータは${MAX_CUSTOM_METADATA_FIELDS}項目までです。`);
  }

  const tags = [];
  const normalizedTags = new Set();
  $("#document-tags").value.split(/[,、，\n]/).map((tag) => tag.trim()).filter(Boolean).forEach((tag) => {
    const normalizedTag = tag.toLocaleLowerCase("ja-JP");
    if (normalizedTags.has(normalizedTag)) throw new Error(`タグ「${tag}」が重複しています。`);
    normalizedTags.add(normalizedTag);
    tags.push(tag);
  });
  if (tags.length > 20) throw new Error("タグは20件まで指定できます。");
  if (tags.some((tag) => tag.length > 80)) throw new Error("タグは1件80文字以内で入力してください。");
  if (tags.some((tag) => /[\u0000-\u001f\u007f]/.test(tag))) throw new Error("タグに制御文字は使えません。");

  const metadata = { custom_metadata: customMetadata };
  const optionalFields = {
    title: $("#document-title").value.trim(),
    category: $("#document-category").value.trim(),
    document_date: $("#document-date").value,
    source: $("#document-source").value.trim(),
  };
  Object.entries(optionalFields).forEach(([key, value]) => { if (value) metadata[key] = value; });
  if (tags.length) metadata.tags = tags;
  return metadata;
}

async function uploadDocument(event) {
  event.preventDefault();
  // See createProject(): currentTarget becomes null after an async listener
  // yields, so retain the form for the success-path reset.
  const formElement = event.currentTarget;
  const originScope = currentProjectScope();
  if (!isCurrentProjectScope(originScope)) return openProjectModal();
  const originProjectId = originScope.projectId;
  const restore = state.preparationRestore;
  if (restore) await restore;
  if (!isCurrentProjectScope(originScope)) return;
  if (hasActivePreparationRun(originProjectId)) {
    return showError(new Error("現在のデータ準備が完了してから、次のPDFをアップロードしてください。"));
  }
  const file = $("#pdf-input").files[0];
  if (!file) return showError(new Error("アップロードするPDFを選択してください。"));
  if (!file.name.toLocaleLowerCase("ja-JP").endsWith(".pdf")) {
    return showError(new Error("PDFファイル（.pdf）を選択してください。"));
  }
  if (file.size > 100 * 1024 * 1024) {
    return showError(new Error("PDFは100 MB以下にしてください。"));
  }
  let metadata;
  try {
    metadata = collectDocumentMetadata();
  } catch (error) {
    return showError(error);
  }
  const form = new FormData();
  form.append("file", file);
  form.append("metadata", JSON.stringify(metadata));
  const operation = beginPreparationRequest(originProjectId, "UPLOAD");
  if (!operation) {
    return showError(new Error("現在のデータ準備が完了してから、次のPDFをアップロードしてください。"));
  }
  setPrepProgress("upload", 18, "PDFを保管領域へ保存しています…");
  try {
    const result = await api(`/api/projects/${originProjectId}/documents`, { method: "POST", body: form });
    storePreparationRun(originProjectId, result.parse_run_id);
    if (!isCurrentPreparationOperation(operation)) return;
    toast("PDFを登録し、解析を開始しました。");
    pollPreparationRun(result.parse_run_id, {
      projectId: originProjectId,
      initialRun: {
        ...result,
        prep_run_id: result.parse_run_id,
        run_type: "PARSE_ONLY",
        status: result.status || "QUEUED",
        current_step: "parsing",
        completed_steps: 1,
        total_steps: 3,
      },
    });
    formElement.reset();
    $("#upload-metadata-details").open = false;
    resetCustomMetadataRows();
    setSelectedFile(null);
    await loadDocuments();
  } catch (error) {
    if (isCurrentPreparationOperation(operation)) {
      setPrepProgress("upload", 0, error.message);
      showError(error);
    }
  }
  finally {
    finishPreparationRequest(operation);
  }
}

async function retryDocumentParse(documentId, button) {
  const scope = currentProjectScope();
  if (!isCurrentProjectScope(scope)) return;
  const restore = state.preparationRestore;
  if (restore) await restore;
  if (!isCurrentProjectScope(scope)) return;
  if (hasActivePreparationRun(scope.projectId)) {
    return showError(new Error("現在のデータ準備が完了してから再解析してください。"));
  }
  const operation = beginPreparationRequest(scope.projectId, "RETRY_PARSE");
  if (!operation) return;
  setBusy(button, true, "再解析を開始中…");
  setPrepProgress("parsing", 34, "ai_parse_documentで再解析を開始しています…");
  try {
    const result = await api(
      `/api/projects/${scope.projectId}/documents/${encodeURIComponent(documentId)}:parse`,
      { method: "POST", body: "{}" },
    );
    storePreparationRun(scope.projectId, result.parse_run_id);
    if (!isCurrentPreparationOperation(operation)) return;
    toast("PDFの再解析を開始しました。");
    pollPreparationRun(result.parse_run_id, {
      projectId: scope.projectId,
      initialRun: {
        ...result,
        prep_run_id: result.parse_run_id,
        run_type: "PARSE_ONLY",
        status: result.status || "QUEUED",
        current_step: "parsing",
        completed_steps: 1,
        total_steps: 3,
      },
    });
    await loadDocuments(scope);
  } catch (error) {
    if (isCurrentPreparationOperation(operation)) showError(error);
  } finally {
    finishPreparationRequest(operation);
  }
}

function preparationStorageKey(projectId) {
  return `${PREPARATION_STORAGE_PREFIX}${projectId}`;
}

function readStoredPreparationRun(projectId) {
  try {
    const value = JSON.parse(localStorage.getItem(preparationStorageKey(projectId)) || "null");
    return typeof value?.run_id === "string" && value.run_id ? value.run_id : null;
  } catch (_) {
    return null;
  }
}

function storePreparationRun(projectId, runId) {
  try {
    localStorage.setItem(preparationStorageKey(projectId), JSON.stringify({ run_id: runId, saved_at: new Date().toISOString() }));
  } catch (_) {
    // Monitoring still works in-memory when browser storage is unavailable.
  }
}

function clearStoredPreparationRun(projectId, runId = null) {
  try {
    if (!runId || readStoredPreparationRun(projectId) === runId) localStorage.removeItem(preparationStorageKey(projectId));
  } catch (_) {
    // Ignore browser storage failures; the server remains the source of truth.
  }
}

function preparationStatus(run) {
  return String(run?.status || "QUEUED").toUpperCase();
}

function activePreparationRequest(projectId = state.projectId) {
  return projectId ? state.preparationRequests.get(projectId) || null : null;
}

function beginPreparationRequest(projectId, kind) {
  if (!projectId || state.preparationRequests.has(projectId)) return null;
  const operation = { token: Symbol(kind), projectId, kind, generation: state.projectGeneration };
  state.preparationRequests.set(projectId, operation);
  setPreparationControls();
  return operation;
}

function finishPreparationRequest(operation) {
  if (operation && state.preparationRequests.get(operation.projectId) === operation) {
    state.preparationRequests.delete(operation.projectId);
  }
  setPreparationControls();
}

function isCurrentPreparationOperation(operation) {
  return Boolean(operation
    && state.projectId === operation.projectId
    && state.projectGeneration === operation.generation
    && state.preparationRequests.get(operation.projectId) === operation);
}

function hasActivePreparationRun(projectId = state.projectId) {
  return state.preparationRequests.has(projectId)
    || Boolean(
      state.preparationRunProjectId === projectId
      && state.preparationRun
      && !PREPARATION_TERMINAL_STATUSES.has(preparationStatus(state.preparationRun)),
    );
}

function setPreparationControls() {
  const projectId = state.projectId;
  const request = activePreparationRequest(projectId);
  const active = hasActivePreparationRun(projectId);
  const selectedProfile = selectedIndexProfileForCurrentSettings();
  const buildButton = $("#build-button");
  if (buildButton) {
    buildButton.disabled = active || !selectedProfile;
    buildButton.textContent = request?.kind === "BUILD_VARIANT"
      ? "作成・同期処理を開始中…"
      : active ? "RAG検索データを作成・同期中…" : BUILD_BUTTON_LABEL;
    buildButton.title = selectedProfile
      ? `既存Index「${selectedProfile.index_name}」へ検索データを作成して同期します`
      : "この設定は管理者によるIndex準備が必要です";
  }
  const uploadButton = $("#upload-button");
  if (uploadButton) {
    uploadButton.disabled = active;
    uploadButton.textContent = request?.kind === "UPLOAD" ? "アップロード中…" : "アップロードして解析";
  }
  renderBuildDocumentOptions();
}

function stopPreparationMonitor({ resetRun = false } = {}) {
  const monitor = state.preparationMonitor;
  if (monitor) {
    clearTimeout(monitor.timer);
    monitor.controller?.abort();
  }
  state.preparationMonitor = null;
  if (resetRun) {
    state.preparationRun = null;
    state.preparationRunProjectId = null;
  }
  setPreparationControls();
}

function preparationJobHref(run) {
  return safeJobHref(run?.job_run_url || run?.run_page_url || null);
}

function preparationMessage(run) {
  const status = preparationStatus(run);
  const jobState = String(run?.job_state || run?.task_state || "").toUpperCase();
  const rawStateMessage = String(run?.job_state_message || run?.state_message || run?.cluster_state_message || "");
  const rawQueueReason = String(run?.queue_reason || run?.queue_message || "");
  const combined = `${rawStateMessage} ${rawQueueReason}`.trim();

  if (status === "FAILED") return run?.error_message || "RAG検索データの作成・同期に失敗しました。実行結果を確認してください。";
  if (status === "CANCELED") return "RAG検索データの作成・同期を停止しました。";
  if (status === "SUCCEEDED" && run?.run_type === "PARSE_ONLY") return "PDFの解析が完了しました。次にRAG検索データを作成してください。";
  if (status === "SUCCEEDED") return "RAG検索データの作成と既存Indexの同期が完了しました。";
  if (rawQueueReason === "WAITING_FOR_JOB_CAPACITY" || /MAX_CONCURRENT_RUNS_REACHED|maximum concurrent runs/i.test(combined)) {
    return rawStateMessage || "別の検索データを作成中です。順番に開始します。";
  }
  if (rawQueueReason === "COMPUTE_STARTING" || /Waiting for cluster|Starting Spark|Setting up .*nodes?|cluster/i.test(combined) || jobState === "PENDING") {
    return rawStateMessage || "実行環境を起動しています。通常は数分かかります。";
  }
  if (["ENVIRONMENT_STARTING", "TASK_STARTING", "RETRY_WAIT", "BLOCKED", "JOB_SUBMITTING", "SUBMISSION_RETRY"].includes(rawQueueReason)) {
    return rawStateMessage || "データ作成処理の開始を待っています。";
  }
  if (status === "QUEUED" || jobState === "QUEUED") {
    return rawStateMessage || "処理を受け付けました。実行環境を準備しています。";
  }
  const stepLabels = {
    upload: "PDFを保管領域へ保存しています",
    parsing: "PDFを解析しています",
    summary: "解析結果を確認しています",
    validation: "入力データと設定を検証しています",
    chunking: "PDFを分割し、検索データを作成しています",
    embedding: "検索用ベクトルを作成しています",
    indexing: "使用する既存Indexを確認しています",
    index_sync: "既存Indexを同期しています",
    variant_registration: "検索設定の状態を更新し、最終確認しています",
    ready: "RAG検索の準備が完了しました",
    completed: "RAG検索データの作成が完了しました",
  };
  return `${stepLabels[run?.current_step] || "RAG検索データを作成しています"}。画面を移動しても処理は継続します。`;
}

function renderPreparationRun(run, overrideMessage = null) {
  const total = Math.max(1, Number(run?.total_steps || 1));
  const completed = Math.max(0, Number(run?.completed_steps || 0));
  const status = preparationStatus(run);
  if (status === "SUCCEEDED" && run?.run_type === "PARSE_ONLY") {
    setPrepProgress("chunking", 50, overrideMessage || preparationMessage(run), preparationJobHref(run));
    return;
  }
  const percentage = status === "SUCCEEDED" ? 100 : Math.min(99, Math.round((completed / total) * 100));
  setPrepProgress(run?.current_step || "chunking", percentage, overrideMessage || preparationMessage(run), preparationJobHref(run));
}

function schedulePreparationPoll(monitor, delayMs) {
  if (state.preparationMonitor !== monitor) return;
  clearTimeout(monitor.timer);
  monitor.timer = setTimeout(() => pollPreparationStatus(monitor), delayMs);
}

async function pollPreparationStatus(monitor) {
  if (state.preparationMonitor !== monitor || !isCurrentProjectScope(monitor)) return;
  monitor.controller = new AbortController();
  try {
    const run = await api(`/api/projects/${monitor.projectId}/preparation-runs/${monitor.runId}`, {
      signal: monitor.controller.signal,
    });
    if (state.preparationMonitor !== monitor || !isCurrentProjectScope(monitor)) return;
    monitor.failures = 0;
    state.preparationRun = run;
    state.preparationRunProjectId = monitor.projectId;
    storePreparationRun(monitor.projectId, monitor.runId);
    renderPreparationRun(run);
    setPreparationControls();
    if (PREPARATION_TERMINAL_STATUSES.has(preparationStatus(run))) {
      stopPreparationMonitor();
      clearStoredPreparationRun(monitor.projectId, monitor.runId);
      if (preparationStatus(run) === "SUCCEEDED" && monitor.notifyOnSuccess) {
        toast(run?.run_type === "PARSE_ONLY" ? "PDFの解析が完了しました。" : "RAG検索データの作成が完了しました。");
      }
      await Promise.allSettled([
        refreshCurrentProjectSummary(monitor), loadDocuments(monitor), loadVariants(monitor),
      ]);
      return;
    }
    const requestedDelay = Number(run?.retry_after_ms);
    const pollDelay = Number.isFinite(requestedDelay) && requestedDelay > 0
      ? Math.min(300000, Math.max(PREPARATION_POLL_INTERVAL_MS, Math.ceil(requestedDelay)))
      : PREPARATION_POLL_INTERVAL_MS;
    schedulePreparationPoll(monitor, pollDelay);
  } catch (error) {
    if (error?.name === "AbortError" || state.preparationMonitor !== monitor) return;
    monitor.failures += 1;
    const transient = isTransientApiError(error);
    const delay = transient
      ? Math.min(PREPARATION_RETRY_MAX_MS, 1500 * (2 ** Math.min(monitor.failures - 1, 5)))
      : PREPARATION_RETRY_MAX_MS;
    const seconds = Math.ceil(delay / 1000);
    const reason = transient ? "状態取得に一時失敗しました" : "状態を取得できませんでした";
    renderPreparationRun(state.preparationRun || { status: "QUEUED", current_step: "chunking", completed_steps: 0, total_steps: 4 },
      `${reason}。${seconds}秒後に再試行します。Databricks上の処理は継続しています。`);
    schedulePreparationPoll(monitor, delay);
  }
}

function pollPreparationRun(runId, { initialRun = null, projectId = state.projectId, notifyOnSuccess = true } = {}) {
  if (!projectId || !runId) return;
  if (state.projectId !== projectId) {
    storePreparationRun(projectId, runId);
    return;
  }
  stopPreparationMonitor();
  const monitor = {
    projectId,
    generation: state.projectGeneration,
    runId,
    timer: null,
    controller: null,
    failures: 0,
    notifyOnSuccess,
  };
  state.preparationMonitor = monitor;
  state.preparationRun = initialRun || {
    prep_run_id: runId,
    status: "QUEUED",
    current_step: "chunking",
    completed_steps: 0,
    total_steps: 4,
  };
  state.preparationRunProjectId = projectId;
  storePreparationRun(projectId, runId);
  renderPreparationRun(state.preparationRun);
  setPreparationControls();
  schedulePreparationPoll(monitor, 0);
}

function activePreparationFromPayload(payload) {
  if (!payload) return null;
  if (payload.prep_run_id) return payload;
  return payload.active_run || payload.item || null;
}

async function restoreActivePreparationRun(projectId) {
  return restoreActivePreparationRunForScope({ projectId, generation: state.projectGeneration });
}

async function restoreActivePreparationRunForScope(scope) {
  if (!isCurrentProjectScope(scope)) return;
  const { projectId } = scope;
  stopPreparationMonitor({ resetRun: true });
  const storedRunId = readStoredPreparationRun(projectId);
  if (storedRunId) {
    try {
      const storedRun = await api(`/api/projects/${projectId}/preparation-runs/${storedRunId}`);
      if (!isCurrentProjectScope(scope)) return;
      if (!PREPARATION_TERMINAL_STATUSES.has(preparationStatus(storedRun))) {
        pollPreparationRun(storedRunId, { initialRun: storedRun, projectId, notifyOnSuccess: false });
        return;
      }
      clearStoredPreparationRun(projectId, storedRunId);
    } catch (error) {
      if (!isCurrentProjectScope(scope)) return;
      if (error?.status !== 404) {
        pollPreparationRun(storedRunId, {
          initialRun: { prep_run_id: storedRunId, status: "QUEUED", current_step: "chunking", completed_steps: 0, total_steps: 4 },
          projectId,
          notifyOnSuccess: false,
        });
        return;
      }
      clearStoredPreparationRun(projectId, storedRunId);
    }
  }

  try {
    const payload = await api(`/api/projects/${projectId}/preparation-runs/active`);
    if (!isCurrentProjectScope(scope)) return;
    const activeRun = activePreparationFromPayload(payload);
    if (activeRun && !PREPARATION_TERMINAL_STATUSES.has(preparationStatus(activeRun))) {
      pollPreparationRun(activeRun.prep_run_id, { initialRun: activeRun, projectId, notifyOnSuccess: false });
      return;
    }
    state.preparationRun = null;
    state.preparationRunProjectId = null;
    setPreparationControls();
  } catch (error) {
    if (!isCurrentProjectScope(scope)) return;
    if (error?.status === 404) {
      state.preparationRun = null;
      state.preparationRunProjectId = null;
      setPreparationControls();
      return;
    }
    const retryRun = {
      status: "RECOVERING",
      current_step: "chunking",
      completed_steps: 0,
      total_steps: 4,
    };
    state.preparationRun = retryRun;
    state.preparationRunProjectId = projectId;
    setPreparationControls();
    setPrepProgress("chunking", 0, "実行状況の確認に一時失敗しました。5秒後に再試行します。", null);
    const monitor = {
      projectId,
      generation: scope.generation,
      runId: null,
      timer: null,
      controller: null,
      failures: 1,
      notifyOnSuccess: false,
    };
    state.preparationMonitor = monitor;
    monitor.timer = setTimeout(() => {
      if (state.preparationMonitor !== monitor || !isCurrentProjectScope(scope)) return;
      restoreActivePreparationRunForScope(scope);
    }, 5000);
  }
}

function isTransientApiError(error) {
  if (error instanceof TypeError || !Number.isFinite(Number(error?.status))) return true;
  return [408, 425, 429, 500, 502, 503, 504].includes(Number(error.status));
}

function setPrepProgress(step, percentage, message, jobHref = null) {
  $("#prep-percent").textContent = `${percentage}%`;
  $("#prep-progress-bar").style.width = `${Math.min(100, Math.max(0, percentage))}%`;
  const messageBox = $("#prep-message");
  messageBox.replaceChildren(node("span", "", message));
  if (jobHref && jobHref !== "#") {
    const link = node("a", "", "処理の詳細を開く");
    link.href = jobHref;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    messageBox.append(document.createTextNode(" "), link);
  }
  const order = ["upload", "parsing", "chunking", "sync"];
  const aliases = {
    summary: "parsing",
    validation: "chunking",
    embedding: "chunking",
    indexing: "sync",
    index_sync: "sync",
    variant_registration: "sync",
    ready: "sync",
    completed: "sync",
  };
  const current = order.indexOf(aliases[step] || step);
  $$("#prep-stepper li").forEach((item, index) => {
    item.classList.toggle("done", index < current || percentage === 100);
    item.classList.toggle("active", index === current && percentage < 100);
  });
}

async function buildVariant() {
  const originScope = currentProjectScope();
  if (!isCurrentProjectScope(originScope)) return openProjectModal();
  const originProjectId = originScope.projectId;
  const restore = state.preparationRestore;
  if (restore) await restore;
  if (!isCurrentProjectScope(originScope)) return;
  if (hasActivePreparationRun(originProjectId)) {
    if (state.preparationRunProjectId === originProjectId && state.preparationRun) renderPreparationRun(state.preparationRun);
    return showError(new Error("RAG検索データを作成中です。完了までお待ちください。"));
  }
  const eligibleIds = new Set(eligibleBuildDocuments().map((doc) => doc.document_id));
  const documentIds = [...state.buildDocumentIds].filter((documentId) => eligibleIds.has(documentId));
  if (!documentIds.length) return showError(new Error("先に解析済みのPDFを用意してください。"));
  if (documentIds.length > MAX_BUILD_DOCUMENTS) return showError(new Error("1回に選べるPDFは100件までです。"));
  const embedding = $("#embedding-model").value;
  if (!embedding) return showError(new Error("ベクトル化モデルを選択してください。"));
  const configuration = currentPreparationConfiguration();
  const indexProfile = selectedIndexProfileForCurrentSettings();
  if (!indexProfile) {
    updateIndexProfileSelection();
    return showError(new Error("この設定は管理者によるIndex準備が必要です。"));
  }
  const payload = {
    run_type: "BUILD_VARIANT",
    document_ids: documentIds,
    index_profile_key: indexProfile.profile_key,
    configuration,
  };
  const operation = beginPreparationRequest(originProjectId, "BUILD_VARIANT");
  if (!operation) {
    return showError(new Error("RAG検索データを作成中です。完了までお待ちください。"));
  }
  try {
    const run = await api(`/api/projects/${originProjectId}/preparation-runs`, { method: "POST", body: JSON.stringify(payload) });
    storePreparationRun(originProjectId, run.prep_run_id);
    if (!isCurrentPreparationOperation(operation)) return;
    toast(run.reused ? "実行中の作成・同期を表示します。" : `RAG検索データの作成と既存Index「${indexProfile.index_name}」の同期を開始しました。`);
    pollPreparationRun(run.prep_run_id, {
      projectId: originProjectId,
      initialRun: {
        ...run,
        run_type: "BUILD_VARIANT",
        status: run.status || "QUEUED",
        current_step: run.current_step || "chunking",
        completed_steps: run.completed_steps || 0,
        total_steps: run.total_steps || 4,
      },
    });
  } catch (error) {
    if (!isCurrentPreparationOperation(operation)) return;
    if (error?.status === 409) {
      toast("実行中の検索データ作成を表示します。");
      await restoreActivePreparationRun(originProjectId);
    } else if (isTransientApiError(error)) {
      toast("処理の受付結果を確認しています。再度クリックせず、そのままお待ちください。");
      await restoreActivePreparationRun(originProjectId);
      if (state.projectId === originProjectId && !hasActivePreparationRun(originProjectId)) showError(error);
    } else {
      showError(error);
    }
  } finally {
    finishPreparationRequest(operation);
  }
}

function updateMethodCards() {
  $$(".method-card").forEach((card) => card.classList.toggle("selected", card.querySelector("input").checked));
}

async function loadDocuments(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  try {
    const result = await api(`/api/projects/${scope.projectId}/documents`);
    if (!isCurrentProjectScope(scope)) return;
    state.documents = result.items || [];
    syncBuildDocumentSelection();
    renderBuildDocumentOptions();
    renderEvaluationCaseDocumentOptions();
    renderCatalog();
  } catch (error) {
    if (!isCurrentProjectScope(scope)) return;
    state.documents = [];
    syncBuildDocumentSelection();
    renderBuildDocumentOptions();
    renderEvaluationCaseDocumentOptions();
    renderCatalog();
    showError(error, false);
  }
}

function eligibleBuildDocuments() {
  return state.documents.filter((doc) =>
    ["PARSED", "READY"].includes(String(doc.processing_status || "").toUpperCase()),
  );
}

function syncBuildDocumentSelection() {
  const eligible = eligibleBuildDocuments();
  const eligibleIds = new Set(eligible.map((doc) => doc.document_id));
  state.buildDocumentIds = new Set(
    [...state.buildDocumentIds].filter((documentId) => eligibleIds.has(documentId)),
  );
  if (!state.buildSelectionInitialized) {
    eligible.slice(0, MAX_BUILD_DOCUMENTS).forEach((doc) => state.buildDocumentIds.add(doc.document_id));
    state.buildSelectionInitialized = true;
  } else {
    eligible.forEach((doc) => {
      if (!state.buildEligibleIds.has(doc.document_id) && state.buildDocumentIds.size < MAX_BUILD_DOCUMENTS) {
        state.buildDocumentIds.add(doc.document_id);
      }
    });
  }
  state.buildEligibleIds = eligibleIds;
}

function renderBuildDocumentOptions() {
  const container = $("#build-document-list");
  const count = $("#build-document-count");
  if (!container || !count) return;
  const eligible = eligibleBuildDocuments();
  count.textContent = `${state.buildDocumentIds.size} / ${eligible.length}件`;
  container.replaceChildren();
  if (!eligible.length) {
    container.append(emptyNode("解析済みPDFはまだありません。"));
    return;
  }
  eligible.forEach((doc) => {
    const label = node("label", "build-document-option");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.buildDocumentId = doc.document_id;
    input.checked = state.buildDocumentIds.has(doc.document_id);
    input.disabled = hasActivePreparationRun() || (!input.checked && state.buildDocumentIds.size >= MAX_BUILD_DOCUMENTS);
    label.append(input, node("span", "", doc.title || doc.original_filename || "PDF"), node("small", "", formatStatus(doc.processing_status)));
    container.append(label);
  });
}

async function refreshCurrentProjectSummary(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  const result = await api("/api/projects");
  if (!isCurrentProjectScope(scope)) return;
  state.projects = result.items || [];
  const select = $("#project-select");
  select.replaceChildren(new Option("プロジェクトを選択", ""));
  state.projects.forEach((project) => select.add(new Option(project.name, project.project_id)));
  select.value = scope.projectId;
  updateProjectChrome(state.projects.find((project) => project.project_id === scope.projectId) || null);
  populateVariantSelects();
}

async function loadEvaluationData(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  try {
    const result = await api(`/api/projects/${scope.projectId}/evaluation-datasets`);
    if (!isCurrentProjectScope(scope)) return;
    state.evaluationDatasets = result.items || [];
    populateEvaluationDatasets();
    await loadEvaluationCases(scope);
  } catch (error) {
    if (!isCurrentProjectScope(scope)) return;
    state.evaluationDatasets = [];
    state.evaluationCases = [];
    populateEvaluationDatasets();
    renderEvaluationCases();
    showError(error, false);
  }
}

async function loadEvaluationRuns(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  const refresh = $("#refresh-evaluation-runs");
  if (refresh) refresh.disabled = true;
  try {
    const result = await api(`/api/projects/${scope.projectId}/evaluation-runs`);
    if (!isCurrentProjectScope(scope)) return;
    state.evaluationRuns = result.items || [];
    renderEvaluationRuns();
  } catch (error) {
    if (!isCurrentProjectScope(scope)) return;
    state.evaluationRuns = [];
    renderEvaluationRuns();
    showError(error, false);
  } finally {
    if (isCurrentProjectScope(scope) && refresh) refresh.disabled = false;
  }
}

function renderEvaluationRuns() {
  const container = $("#evaluation-run-list");
  if (!container) return;
  container.replaceChildren();
  if (!state.evaluationRuns.length) {
    container.append(emptyNode("評価履歴はまだありません。"));
    return;
  }
  state.evaluationRuns.forEach((run) => {
    const button = node("button", "evaluation-run-item");
    button.type = "button";
    button.classList.toggle("active", run.eval_run_id === state.evaluationRunId);
    button.setAttribute("aria-label", `${formatDate(run.created_at)}の評価結果を表示`);
    const phases = (run.phases || []).map((item) => PHASES[item.phase_id]?.label || item.phase_id).join("、");
    const copy = node("span");
    copy.append(
      node("strong", "", `${formatDate(run.created_at)} · ${run.dataset_version || "版不明"}`),
      node("small", "", `${run.dataset_split || "用途不明"} / ${phases || "Phase情報なし"}`),
    );
    button.append(copy, statusPill(run.status));
    button.addEventListener("click", () => openEvaluationRun(run.eval_run_id));
    container.append(button);
  });
}

async function openEvaluationRun(runId) {
  const scope = currentProjectScope();
  if (!isCurrentProjectScope(scope) || !runId) return;
  clearTimeout(state.evaluationPoll);
  state.evaluationPoll = null;
  state.evaluationController?.abort();
  state.evaluationController = null;
  const operation = ++state.evaluationOperation;
  state.evaluationRunId = runId;
  const context = { ...scope, runId, operation };
  renderEvaluationRuns();
  $("#evaluation-progress-card").classList.remove("hidden");
  $("#evaluation-progress-title").textContent = "評価結果を読み込み中";
  try {
    const run = await api(`/api/projects/${scope.projectId}/evaluation-runs/${runId}`);
    if (!isCurrentEvaluationContext(context)) return;
    renderEvaluationProgress(run);
    const active = ["RUNNING", "QUEUED", "CANCEL_REQUESTED"].includes(run.status);
    $("#evaluation-progress-title").textContent = active ? "Phase比較を実行中" : "保存済みの評価結果";
    setEvaluationRunningUi(active, active ? "評価を実行中…" : "RAG精度を比較");
    $("#cancel-evaluation").classList.toggle("hidden", !active);
    if (active) pollEvaluation(context);
    else await loadEvaluationResults(context);
  } catch (error) {
    if (!isCurrentEvaluationContext(context)) return;
    setEvaluationRunningUi(false);
    showError(error);
  }
}

function populateEvaluationDatasets() {
  const select = $("#dataset-version");
  if (!select) return;
  const previous = select.value;
  const projectVersion = state.projects.find((item) => item.project_id === state.projectId)?.dataset_version || null;
  const versions = [...new Set(state.evaluationDatasets.map((item) => item.dataset_version).filter(Boolean))];
  select.replaceChildren();
  if (!versions.length) {
    select.add(new Option("評価質問を登録してください", ""));
    $("#evaluation-case-version").value = projectVersion || "v1.0.0";
    return;
  }
  versions.forEach((version) => select.add(new Option(version, version)));
  select.value = versions.includes(previous)
    ? previous
    : versions.includes(projectVersion) ? projectVersion : versions[0];
  syncEvaluationDatasetSplit();
}

function syncEvaluationDatasetSplit() {
  const version = $("#dataset-version").value;
  const splitSelect = $("#dataset-split");
  const availableSplits = [...new Set(
    state.evaluationDatasets
      .filter((item) => item.dataset_version === version)
      .map((item) => item.dataset_split)
      .filter(Boolean),
  )];
  // Preserve a deliberate split selection when that pair exists. When a
  // Project/version has only one populated split (a common first-run case),
  // switch to it automatically so the evaluation page never opens on an
  // unrelated empty question set.
  if (availableSplits.length && !availableSplits.includes(splitSelect.value)) {
    splitSelect.value = availableSplits.includes("development")
      ? "development"
      : availableSplits[0];
  }
  $("#evaluation-case-version").value = version || "v1.0.0";
  $("#evaluation-case-split").value = splitSelect.value;
}

async function loadEvaluationCases(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  const version = $("#dataset-version").value;
  const split = $("#dataset-split").value;
  if (!version) {
    state.evaluationCases = [];
    renderEvaluationCases();
    return;
  }
  try {
    const result = await api(`/api/projects/${scope.projectId}/evaluation-cases?dataset_version=${encodeURIComponent(version)}&dataset_split=${encodeURIComponent(split)}`);
    if (!isCurrentProjectScope(scope)) return;
    state.evaluationCases = result.items || [];
    reconcileEvaluationCaseSelection();
    renderEvaluationCases();
  } catch (error) {
    if (!isCurrentProjectScope(scope)) return;
    state.evaluationCases = [];
    renderEvaluationCases();
    showError(error, false);
  }
}

function evaluationDatasetKey() {
  return JSON.stringify([state.projectId || "", $("#dataset-version")?.value || "", $("#dataset-split")?.value || ""]);
}

function evaluationCaseId(item) {
  return String(item?.eval_case_id || "");
}

function reconcileEvaluationCaseSelection() {
  const key = evaluationDatasetKey();
  const validIds = new Set(state.evaluationCases.map(evaluationCaseId).filter(Boolean));
  if (!state.evaluationCaseSelections.has(key)) {
    state.evaluationCaseSelections.set(key, new Set(validIds));
    return;
  }
  const selected = state.evaluationCaseSelections.get(key);
  state.evaluationCaseSelections.set(key, new Set([...selected].filter((id) => validIds.has(id))));
}

function selectedEvaluationCaseIds() {
  const selected = state.evaluationCaseSelections.get(evaluationDatasetKey()) || new Set();
  const validIds = new Set(state.evaluationCases.map(evaluationCaseId).filter(Boolean));
  return [...selected].filter((id) => validIds.has(id));
}

function setAllEvaluationCasesSelected(selected) {
  if ($("#page-evaluation").dataset.running === "true") return;
  const key = evaluationDatasetKey();
  state.evaluationCaseSelections.set(key, new Set(
    selected ? state.evaluationCases.map(evaluationCaseId).filter(Boolean) : [],
  ));
  $$('#evaluation-case-list input[data-evaluation-case-id]').forEach((input) => {
    input.checked = selected;
  });
  updateEvaluationSelectionUi();
}

function handleEvaluationCaseSelection(event) {
  const input = event.target.closest('input[data-evaluation-case-id]');
  if (!input || $("#page-evaluation").dataset.running === "true") return;
  const key = evaluationDatasetKey();
  const selected = new Set(state.evaluationCaseSelections.get(key) || []);
  if (input.checked) selected.add(input.dataset.evaluationCaseId);
  else selected.delete(input.dataset.evaluationCaseId);
  state.evaluationCaseSelections.set(key, selected);
  updateEvaluationSelectionUi();
}

function evaluationCaseReadiness(item) {
  const hasRetrievalTruth = Boolean(item.document_id && (item.relevant_pages || []).length);
  if (hasRetrievalTruth && item.expected_answer) {
    return { className: "success", label: "回答・検索の正解あり", detail: "回答品質と検索品質を評価できます" };
  }
  if (hasRetrievalTruth) {
    return { className: "info", label: "検索の正解あり", detail: "期待する回答を追加すると回答正解率も評価できます" };
  }
  return { className: "error", label: "正解情報を確認", detail: "正解PDFとページを登録してください" };
}

function renderEvaluationCaseDocumentOptions() {
  const select = $("#evaluation-case-document");
  if (!select) return;
  const previous = select.value;
  const eligible = eligibleBuildDocuments();
  select.replaceChildren(new Option(eligible.length ? "正解PDFを選択" : "先にPDFを解析してください", ""));
  eligible.forEach((doc) => select.add(new Option(
    doc.title || doc.original_filename || "PDF", doc.document_id,
  )));
  if (eligible.some((doc) => doc.document_id === previous)) select.value = previous;
}

function renderEvaluationCases() {
  const container = $("#evaluation-case-list");
  const count = $("#evaluation-case-count");
  if (!container || !count) return;
  reconcileEvaluationCaseSelection();
  container.replaceChildren();
  if (!state.evaluationCases.length) {
    container.append(emptyNode("この版・用途の評価質問はまだありません。"));
    updateEvaluationSelectionUi();
    return;
  }
  const selected = new Set(selectedEvaluationCaseIds());
  state.evaluationCases.forEach((item) => {
    const caseId = evaluationCaseId(item);
    const readiness = evaluationCaseReadiness(item);
    const card = node("article", "evaluation-case-item");
    card.dataset.evaluationCaseRow = caseId;
    card.classList.toggle("selected", selected.has(caseId));

    const choice = node("div", "evaluation-case-choice");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = selected.has(caseId);
    input.disabled = !caseId || $("#page-evaluation").dataset.running === "true";
    input.dataset.evaluationCaseId = caseId;
    input.id = `evaluation-case-${caseId}`;
    const label = document.createElement("label");
    label.htmlFor = input.id;
    const question = node("strong", "", item.question);
    const context = node("small", "", `${item.document_title || "正解PDFなし"} · ${(item.relevant_pages || []).join(", ") || "—"}ページ`);
    label.append(question, context);
    choice.append(input, label);

    const status = node("span", `status-pill ${readiness.className}`, readiness.label);
    status.title = readiness.detail;

    const details = node("details", "evaluation-case-details");
    details.append(node("summary", "", "正解を確認"));
    const body = node("div", "evaluation-case-answer");
    const answerTitle = node("strong", "", "期待する回答");
    const answer = node("p", "", item.expected_answer || "未登録（検索精度のみ評価できます）");
    const evidenceTitle = node("strong", "", "正解の根拠");
    const evidence = node("p", "", `${item.document_title || "PDF未登録"} · ${(item.relevant_pages || []).join(", ") || "—"}ページ`);
    body.append(answerTitle, answer, evidenceTitle, evidence);
    if (item.document_id) {
      const openPdf = node("button", "text-button", "根拠PDFを開く ↗");
      openPdf.type = "button";
      body.append(openPdf);
      bindPdfOpenControl(openPdf, item.document_id, Number((item.relevant_pages || [1])[0]) || 1);
    }
    details.append(body);
    card.append(choice, status, details);
    container.append(card);
  });
  updateEvaluationSelectionUi();
}

function updateEvaluationSelectionUi() {
  const selectedIds = new Set(selectedEvaluationCaseIds());
  const total = state.evaluationCases.length;
  const selectedItems = state.evaluationCases.filter((item) => selectedIds.has(evaluationCaseId(item)));
  const selectedCount = selectedItems.length;
  const answerReady = selectedItems.filter((item) => Boolean(item.expected_answer)).length;
  const phases = $$('input[name="eval-phase"]:checked').length;
  const trials = Math.max(1, Number($("#trial-count")?.value) || 1);
  const running = $("#page-evaluation").dataset.running === "true";
  const count = $("#evaluation-case-count");
  if (count) count.textContent = `${total}件中${selectedCount}件を選択`;
  $("#select-all-evaluation-cases").disabled = running || !total || selectedCount === total;
  $("#clear-evaluation-cases").disabled = running || !selectedCount;
  $$("[data-evaluation-case-row]").forEach((row) => {
    row.classList.toggle("selected", selectedIds.has(row.dataset.evaluationCaseRow));
  });
  const summary = $("#evaluation-selection-summary");
  if (summary) {
    summary.replaceChildren(
      node("strong", "", `${selectedCount}問`),
      node("span", "", selectedCount
        ? `${phases} Phase × ${trials}回（最大${selectedCount * phases * trials}試行）`
        : "評価する質問を選択してください"),
    );
  }
  const help = $("#evaluation-selection-help");
  if (help) help.textContent = selectedCount
    ? `${selectedCount}問を評価に使用します。期待する回答の登録: ${answerReady}/${selectedCount}問。`
    : "質問を1件以上選択してください。選択した質問だけがPhase比較に使われます。";
  const start = $("#start-evaluation");
  if (start && !running) {
    start.disabled = selectedCount === 0;
    start.textContent = selectedCount ? `選択した${selectedCount}問でRAG精度を比較` : "質問を選択してください";
  }
}

function parseEvaluationPages(value) {
  const pages = String(value || "").split(/[,、，\s]+/).filter(Boolean).map(Number);
  if (!pages.length || pages.some((page) => !Number.isInteger(page) || page < 1 || page > 100000)) {
    throw new Error("正解ページは、1以上のページ番号をカンマ区切りで入力してください。");
  }
  if (new Set(pages).size !== pages.length) throw new Error("正解ページに重複があります。");
  return pages.sort((a, b) => a - b);
}

async function createEvaluationCase(event) {
  event.preventDefault();
  const scope = currentProjectScope();
  if (!isCurrentProjectScope(scope)) return openProjectModal();
  const formElement = event.currentTarget;
  const button = $("#add-evaluation-case");
  setBusy(button, true, "追加中…");
  try {
    const payload = {
      question: $("#evaluation-case-question").value,
      expected_answer: $("#evaluation-case-answer").value || null,
      relevant_document_id: $("#evaluation-case-document").value,
      relevant_pages: parseEvaluationPages($("#evaluation-case-pages").value),
      dataset_version: $("#evaluation-case-version").value,
      dataset_split: $("#evaluation-case-split").value,
      is_answerable: true,
      question_type: "general",
      language: "ja",
    };
    const savedCase = await api(`/api/projects/${scope.projectId}/evaluation-cases`, {
      method: "POST", body: JSON.stringify(payload),
    });
    if (!isCurrentProjectScope(scope)) return;
    $("#dataset-split").value = payload.dataset_split;
    formElement.reset();
    $("#evaluation-case-version").value = payload.dataset_version;
    $("#evaluation-case-split").value = payload.dataset_split;
    toast("評価質問を追加しました。");
    await refreshCurrentProjectSummary(scope);
    await loadEvaluationData(scope);
    if ([...$("#dataset-version").options].some((option) => option.value === payload.dataset_version)) {
      $("#dataset-version").value = payload.dataset_version;
      $("#dataset-split").value = payload.dataset_split;
      await loadEvaluationCases(scope);
    }
    const savedCaseId = evaluationCaseId(savedCase);
    if (savedCaseId && state.evaluationCases.some((item) => evaluationCaseId(item) === savedCaseId)) {
      const selected = new Set(state.evaluationCaseSelections.get(evaluationDatasetKey()) || []);
      selected.add(savedCaseId);
      state.evaluationCaseSelections.set(evaluationDatasetKey(), selected);
      renderEvaluationCases();
    }
  } catch (error) {
    if (isCurrentProjectScope(scope)) showError(error);
  } finally {
    setBusy(button, false, "＋ 評価質問を追加");
  }
}

async function loadVariants(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  try {
    const result = await api(`/api/projects/${scope.projectId}/variants`);
    if (!isCurrentProjectScope(scope)) return;
    state.variants = result.items || [];
    renderVariants();
    populateVariantSelects();
  } catch (error) {
    if (!isCurrentProjectScope(scope)) return;
    state.variants = [];
    renderVariants();
    populateVariantSelects();
    showError(error, false);
  }
}

function renderVariants() {
  const container = $("#variant-list");
  container.replaceChildren();
  if (!state.variants.length) {
    return container.append(emptyNode(
      state.documents.length
        ? "利用可能なRAG検索データはまだありません。"
        : "RAG検索データはありません。PDFを追加して作成してください。",
    ));
  }
  state.variants.slice(0, 6).forEach((variant) => {
    const item = node("div", "compact-item");
    item.append(node("strong", "", `${formatChunkMethod(variant.chunk_method)}${variant.chunk_size ? ` / ${variant.chunk_size} tokens` : ""}`));
    item.append(node("span", "", `${formatStatus(variant.status || "READY")} · ${formatDate(variant.created_at)}`));
    const indexName = variantIndexName(variant);
    if (indexName) item.append(node("span", "compact-index-name", `Index: ${indexName}`));
    container.append(item);
  });
}

function populateVariantSelects() {
  const activeVariantId = state.projects.find((item) => item.project_id === state.projectId)?.active_variant_id || null;
  const readyVariants = state.variants.filter(
    (variant) => String(variant.status || "READY").toUpperCase() === "READY",
  );
  [$("#chat-variant"), $("#evaluation-variant")].forEach((select) => {
    const previous = select.value;
    select.replaceChildren();
    if (!readyVariants.length) { select.add(new Option("利用可能な検索データがありません", "")); return; }
    readyVariants.forEach((variant) => select.add(new Option(
      `${formatChunkMethod(variant.chunk_method)}${variant.chunk_size ? ` · ${variant.chunk_size}` : ""}${variantIndexName(variant) ? ` · ${variantIndexName(variant)}` : ""}${variant.variant_id === activeVariantId ? " （使用中）" : ""}`,
      variant.variant_id,
    )));
    if (readyVariants.some((item) => item.variant_id === previous)) select.value = previous;
    else if (readyVariants.some((item) => item.variant_id === activeVariantId)) select.value = activeVariantId;
    else select.value = readyVariants[0].variant_id;
  });
  updateRuntimeIndexDetails();
}

function parseDocumentCustomMetadata(doc) {
  const raw = doc.custom_metadata ?? doc.custom_metadata_json ?? doc.metadata_json;
  if (!raw) return {};
  if (typeof raw === "object" && !Array.isArray(raw)) return raw.custom_metadata || raw;
  if (typeof raw !== "string") return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed.custom_metadata && typeof parsed.custom_metadata === "object" ? parsed.custom_metadata : parsed;
  } catch (_) {
    return {};
  }
}

function normalizeDocumentTags(value) {
  if (Array.isArray(value)) return value.map(String).map((tag) => tag.trim()).filter(Boolean);
  if (typeof value !== "string" || !value.trim()) return [];
  try {
    const parsed = JSON.parse(value);
    if (Array.isArray(parsed)) return parsed.map(String).map((tag) => tag.trim()).filter(Boolean);
  } catch (_) {
    // Plain comma-separated values remain supported for existing records.
  }
  return value.split(/[,、，\n]/).map((tag) => tag.trim()).filter(Boolean);
}

function displayMetadataValue(value) {
  const text = typeof value === "object" ? JSON.stringify(value) : String(value ?? "");
  return text.length > 80 ? `${text.slice(0, 77)}…` : text;
}

function documentMetadataChips(doc) {
  const custom = parseDocumentCustomMetadata(doc);
  const rawCategory = doc.category || custom.category || doc.document_type || "";
  const category = rawCategory ? formatDocumentType(rawCategory) : "";
  const tags = normalizeDocumentTags(doc.tags || custom.tags);
  const chips = [category, ...tags];

  // Keep previously registered vehicle documents readable while new projects
  // use the generic metadata contract.
  if (doc.model) chips.push(doc.model);
  if (doc.model_year) chips.push(String(doc.model_year));

  const reserved = new Set(["title", "category", "tags", "document_date", "source", "custom_metadata"]);
  Object.entries(custom).forEach(([key, value]) => {
    if (!reserved.has(key) && value !== null && value !== undefined && value !== "") {
      chips.push(`${key}: ${displayMetadataValue(value)}`);
    }
  });
  if (doc.page_count) chips.push(`${doc.page_count}ページ`);
  return [...new Set(chips.filter(Boolean).map(String))].slice(0, 10);
}

function documentOriginSummary(doc) {
  const custom = parseDocumentCustomMetadata(doc);
  const date = doc.document_date || custom.document_date || "";
  const source = doc.source || custom.source || "";
  return [date, source].filter(Boolean).join(" / ") || "—";
}

function isAiGeneratedSummary(doc) {
  return String(doc?.summary_source || "").startsWith("AI_GENERATED");
}

function filteredDocuments() {
  const query = $("#catalog-search")?.value.trim().toLowerCase() || "";
  const status = $("#catalog-status-filter")?.value || "";
  return state.documents.filter((doc) => {
    const text = [
      doc.title, doc.summary, doc.category, doc.tags, doc.document_date, doc.source,
      doc.model, doc.document_type, doc.model_year,
      ...Object.entries(parseDocumentCustomMetadata(doc)).flat(),
    ].join(" ").toLowerCase();
    return (!query || text.includes(query)) && (!status || String(doc.processing_status).toUpperCase() === status);
  });
}

function currentProjectCanEditDocuments() {
  const project = state.projects.find((item) => item.project_id === state.projectId);
  return ["OWNER", "EDITOR"].includes(String(project?.role || "").toUpperCase());
}

function documentTitle(doc) {
  return doc?.title || doc?.original_filename || "タイトルなしのPDF";
}

// Keep the DELETE contract in one adapter so backend response fields can evolve
// without leaking transport details into the catalog renderer.
async function requestDocumentDeletion(projectId, documentId) {
  return api(
    `/api/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(documentId)}`,
    { method: "DELETE" },
  );
}

function normalizeDocumentDeletionResult(payload) {
  const value = payload && typeof payload === "object" ? payload : {};
  const rebuilds = Array.isArray(value.rebuilds) ? value.rebuilds : [];
  // A Project can use several pre-registered Index profiles, while the
  // preparation UI intentionally owns one monitor. Follow the active profile
  // first and otherwise the first same-Index rebuild returned by the server.
  const monitoredRebuild = rebuilds.find((item) => item?.activate_on_success === true)
    || rebuilds[0]
    || null;
  const rawVariants = value.impacted_profile_keys
    || value.impacted_variant_ids
    || value.affected_variant_ids
    || value.variant_ids
    || value.impacted_variants
    || rebuilds.map((item) => item?.index_profile_key || item?.profile_key || item?.source_variant_id)
    || [];
  const impactedVariantIds = (Array.isArray(rawVariants) ? rawVariants : [])
    .map((item) => typeof item === "string"
      ? item
      : item?.index_profile_key || item?.profile_key || item?.variant_id)
    .filter(Boolean);
  const prepRunId = value.prep_run_id
    || value.rebuild_run_id
    || value.operation?.prep_run_id
    || value.operation?.run_id
    || monitoredRebuild?.preparation_run_id
    || monitoredRebuild?.prep_run_id
    || null;
  return {
    prepRunId,
    impactedVariantIds: [...new Set(impactedVariantIds)],
    rebuilds,
    profileKey: monitoredRebuild?.index_profile_key
      || monitoredRebuild?.profile_key
      || monitoredRebuild?.source_variant_id
      || null,
    indexName: monitoredRebuild?.index_name || value.index_name || null,
    corpusEmpty: Boolean(value.corpus_empty),
    status: String(monitoredRebuild?.status || value.status || value.deletion_status || "QUEUED").toUpperCase(),
    raw: value,
  };
}

function createDocumentDeleteButton(doc) {
  const title = documentTitle(doc);
  const button = node("button", "danger-text-button document-delete-button");
  button.type = "button";
  button.dataset.deleteDocument = doc.document_id;
  button.setAttribute("aria-label", `PDF「${title}」を削除`);
  const operation = state.documentDeletions.get(doc.document_id);
  const canEdit = currentProjectCanEditDocuments();
  button.disabled = Boolean(operation) || !canEdit;
  button.title = canEdit
    ? `「${title}」を検索対象から削除`
    : "PDFを削除できるのはOWNERまたはEDITORです";
  if (operation) {
    button.classList.add("is-busy");
    button.setAttribute("aria-busy", "true");
    const spinner = node("span", "spinner");
    spinner.setAttribute("aria-hidden", "true");
    button.append(spinner, node("span", "", "削除中…"));
  } else {
    button.textContent = "削除";
  }
  button.addEventListener("click", () => deleteDocument(doc.document_id));
  return button;
}

function removeDocumentFromClientState(documentId, deletionResult) {
  state.documents = state.documents.filter((doc) => doc.document_id !== documentId);
  state.buildDocumentIds.delete(documentId);
  state.buildEligibleIds.delete(documentId);

  if (!state.documents.length || deletionResult.corpusEmpty) {
    // Do not leave a stale searchable Variant selectable after its final source
    // PDF was removed. The server remains the source of truth on the next load.
    state.variants = [];
  } else if (deletionResult.impactedVariantIds.length) {
    const impacted = new Set(deletionResult.impactedVariantIds);
    state.variants = state.variants.map((variant) => impacted.has(variant.variant_id)
      ? { ...variant, status: "PREPARING" }
      : variant);
  }

  if ($("#pdf-viewer")?.dataset.documentId === documentId) closeViewer({ reset: true });
  syncBuildDocumentSelection();
  renderBuildDocumentOptions();
  renderEvaluationCaseDocumentOptions();
  renderCatalog();
  renderVariants();
  populateVariantSelects();
}

async function refreshAfterDocumentDeletion(scope, deletionResult) {
  const requests = [
    api(`/api/projects/${scope.projectId}/documents`),
    api(`/api/projects/${scope.projectId}/variants`),
    api("/api/projects"),
  ];
  const [documentsResult, variantsResult, projectsResult] = await Promise.allSettled(requests);
  if (!isCurrentProjectScope(scope)) return { failures: [] };

  if (documentsResult.status === "fulfilled") state.documents = documentsResult.value?.items || [];
  if (variantsResult.status === "fulfilled") state.variants = variantsResult.value?.items || [];
  if (projectsResult.status === "fulfilled") {
    state.projects = projectsResult.value?.items || [];
    const select = $("#project-select");
    select.replaceChildren(new Option("プロジェクトを選択", ""));
    state.projects.forEach((project) => select.add(new Option(project.name, project.project_id)));
    select.value = scope.projectId;
    updateProjectChrome(state.projects.find((project) => project.project_id === scope.projectId) || null);
  }

  if (!state.documents.length || deletionResult.corpusEmpty) {
    state.variants = [];
  } else if (deletionResult.impactedVariantIds.length) {
    const impacted = new Set(deletionResult.impactedVariantIds);
    state.variants = state.variants.map((variant) => impacted.has(variant.variant_id)
      ? { ...variant, status: "PREPARING" }
      : variant);
  }
  syncBuildDocumentSelection();
  renderBuildDocumentOptions();
  renderEvaluationCaseDocumentOptions();
  renderCatalog();
  renderVariants();
  populateVariantSelects();
  return {
    failures: [documentsResult, variantsResult, projectsResult]
      .filter((result) => result.status === "rejected")
      .map((result) => result.reason),
  };
}

function showDocumentDeletionError(error, title) {
  if (error?.status === 409) {
    showError(new Error(
      `「${title}」の削除と実行中の処理が競合しました。データ準備・既存Index同期の完了後にもう一度お試しください。`,
    ));
    return;
  }
  if (error?.status === 403) {
    showError(new Error(`「${title}」を削除する権限がありません。OWNERまたはEDITORに依頼してください。`));
    return;
  }
  showError(new Error(
    `「${title}」を削除できませんでした。PDFが一覧に残っていることを確認して、もう一度お試しください。${error?.message ? `（${error.message}）` : ""}`,
  ));
}

async function deleteDocument(documentId) {
  const scope = currentProjectScope();
  const doc = state.documents.find((item) => item.document_id === documentId);
  if (!isCurrentProjectScope(scope) || !doc || state.documentDeletions.has(documentId)) return;
  const title = documentTitle(doc);
  if (!currentProjectCanEditDocuments()) {
    showDocumentDeletionError({ status: 403 }, title);
    return;
  }

  const confirmed = await requestConfirmation({
    title: `PDF「${title}」を削除しますか？`,
    message: `「${title}」をRAGの検索対象から除外します。関連するチャンクを論理削除し、影響する検索データを同じ事前登録済みAI Search Indexへ再同期します。新しいTableやIndexは作成しません。過去の会話と評価結果は監査用に保持されます。`,
    confirmLabel: "PDFを削除",
  });
  if (!confirmed || !isCurrentProjectScope(scope) || state.documentDeletions.has(documentId)) return;

  const operation = { ...scope, documentId, token: Symbol("DELETE_DOCUMENT") };
  state.documentDeletions.set(documentId, operation);
  renderCatalog();
  try {
    const payload = await requestDocumentDeletion(scope.projectId, documentId);
    if (!isCurrentProjectScope(scope) || state.documentDeletions.get(documentId) !== operation) return;
    const result = normalizeDocumentDeletionResult(payload);
    removeDocumentFromClientState(documentId, result);
    toast(result.corpusEmpty
      ? "PDFを検索対象から除外しました。RAG検索データはありません。"
      : `PDFを検索対象から除外しました。${result.indexName ? `既存Index「${result.indexName}」を` : "既存Indexを"}再同期中です。`);

    if (result.prepRunId) {
      pollPreparationRun(result.prepRunId, {
        projectId: scope.projectId,
        initialRun: {
          ...result.raw,
          prep_run_id: result.prepRunId,
          run_type: result.raw.run_type || "DELETE_DOCUMENT",
          status: result.status,
          current_step: result.raw.current_step || "index_sync",
          completed_steps: result.raw.completed_steps || 0,
          total_steps: result.raw.total_steps || 4,
        },
      });
    }

    const refresh = await refreshAfterDocumentDeletion(scope, result);
    if (refresh.failures.length && isCurrentProjectScope(scope)) {
      toast("PDFは検索対象から除外済みです。一部の最新状態を取得できないため、右上の更新を押してください。", true);
    }
  } catch (error) {
    if (isCurrentProjectScope(scope)) showDocumentDeletionError(error, title);
  } finally {
    if (state.documentDeletions.get(documentId) === operation) state.documentDeletions.delete(documentId);
    if (isCurrentProjectScope(scope)) renderCatalog();
  }
}

function renderCatalog() {
  const grid = $("#catalog-grid");
  const body = $("#catalog-table-body");
  if (!grid || !body) return;
  const documents = filteredDocuments();
  $("#catalog-count").textContent = `${documents.length}件`;
  grid.replaceChildren(); body.replaceChildren();
  if (!documents.length) {
    const emptyMessage = state.documents.length
      ? "条件に一致するPDFがありません。検索条件を変更してください。"
      : "PDFがありません。RAG検索データもありません。データ準備からPDFを追加してください。";
    grid.append(emptyNode(emptyMessage));
  }
  documents.forEach((doc) => {
    const card = node("article", "card document-card");
    const top = node("div", "document-card-top");
    top.append(node("div", "document-icon", "PDF"), statusPill(doc.processing_status));
    card.append(top, node("h2", "", doc.title || "タイトルなし"));
    const summary = node("div", "document-summary-block");
    summary.append(
      node("span", "summary-label", isAiGeneratedSummary(doc) ? "AI概要" : "概要"),
      node("p", "document-summary", doc.summary || "概要を生成しています。解析完了後に自動表示されます。"),
    );
    card.append(summary);
    const metadata = node("div", "metadata-row");
    documentMetadataChips(doc).forEach((value) => metadata.append(node("span", "metadata-chip", value)));
    card.append(metadata);
    const footer = node("footer");
    footer.append(node("span", "subtle", isAiGeneratedSummary(doc) ? "AI生成の概要" : formatDate(doc.uploaded_at)));
    const actions = node("div", "catalog-actions");
    if (String(doc.processing_status || "").toUpperCase() === "ERROR") {
      const retry = node("button", "danger-text-button", "再解析"); retry.type = "button";
      retry.dataset.retryDocument = doc.document_id;
      retry.addEventListener("click", () => retryDocumentParse(doc.document_id, retry));
      actions.append(retry);
    }
    const open = node("button", "text-button", "PDFを開く →"); open.type = "button";
    bindPdfOpenControl(open, doc.document_id, 1);
    actions.append(open, createDocumentDeleteButton(doc)); footer.append(actions); card.append(footer); grid.append(card);

    const row = document.createElement("tr");
    [doc.title || "—", doc.summary || "—", documentMetadataChips(doc).join(" / ") || "—", documentOriginSummary(doc)]
      .forEach((value) => row.append(node("td", "", value)));
    const statusCell = document.createElement("td"); statusCell.append(statusPill(doc.processing_status)); row.append(statusCell);
    const linkCell = document.createElement("td");
    if (String(doc.processing_status || "").toUpperCase() === "ERROR") {
      const retry = node("button", "danger-text-button", "再解析"); retry.type = "button";
      retry.dataset.retryDocument = doc.document_id;
      retry.addEventListener("click", () => retryDocumentParse(doc.document_id, retry));
      linkCell.append(retry);
    }
    const link = node("button", "text-button", "PDFを開く"); link.type = "button"; bindPdfOpenControl(link, doc.document_id, 1);
    linkCell.classList.add("catalog-actions-cell");
    linkCell.append(link, createDocumentDeleteButton(doc)); row.append(linkCell); body.append(row);
  });
  setCatalogView(state.catalogView);
}

function pdfContentHref(documentId, page = null) {
  if (!state.projectId || !documentId) return "#";
  const base = `/api/projects/${encodeURIComponent(state.projectId)}/documents/${encodeURIComponent(documentId)}/content`;
  return page ? `${base}#page=${Math.max(1, Number(page) || 1)}` : base;
}

function warmPdf(documentId) {
  const href = pdfContentHref(documentId);
  if (href === "#" || state.pdfPrefetches.has(href)) return;
  state.pdfPrefetches.add(href);
  const hint = document.createElement("link");
  hint.rel = "prefetch";
  hint.as = "fetch";
  hint.href = href;
  hint.dataset.pdfPrefetch = "true";
  document.head.append(hint);
}

function bindPdfOpenControl(control, documentId, page = 1) {
  ["pointerenter", "pointerdown", "focus", "touchstart"].forEach((eventName) => {
    control.addEventListener(eventName, () => warmPdf(documentId), { passive: true });
  });
  control.addEventListener("click", () => openViewer(documentId, page));
}

function setCatalogView(view) {
  state.catalogView = view;
  $("#catalog-grid").classList.toggle("hidden", view !== "cards");
  $("#catalog-table-wrap").classList.toggle("hidden", view !== "table");
  $$('[data-catalog-view]').forEach((button) => button.classList.toggle("active", button.dataset.catalogView === view));
}

function openViewer(documentId, page = 1) {
  if (!state.projectId) return;
  const documentData = state.documents.find((doc) => doc.document_id === documentId);
  if (!documentData) return;
  $("#pdf-viewer").dataset.documentId = documentId;
  $("#viewer-title").textContent = documentData.title || "PDF";
  warmPdf(documentId);
  const frame = $("#viewer-frame");
  const href = pdfContentHref(documentId, page);
  const alreadyLoaded = frame.dataset.loadedHref === href && frame.getAttribute("src") === href;
  $("#viewer-loading").classList.toggle("hidden", alreadyLoaded);
  if (!alreadyLoaded) {
    frame.dataset.requestedHref = href;
    frame.src = href;
  }
  $("#pdf-viewer").classList.remove("hidden"); $("#viewer-scrim").classList.remove("hidden");
}
function closeViewer({ reset = false } = {}) {
  $("#pdf-viewer").classList.add("hidden");
  $("#viewer-scrim").classList.add("hidden");
  $("#viewer-loading").classList.add("hidden");
  if (reset) {
    const frame = $("#viewer-frame");
    frame.src = "about:blank";
    delete frame.dataset.requestedHref;
    delete frame.dataset.loadedHref;
    delete $("#pdf-viewer").dataset.documentId;
  }
}

async function loadSessions(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  const operation = ++state.sessionListOperation;
  state.sessionListController?.abort();
  const controller = new AbortController();
  state.sessionListController = controller;
  try {
    const result = await api(`/api/projects/${scope.projectId}/chat/sessions`, { signal: controller.signal });
    if (operation !== state.sessionListOperation || !isCurrentProjectScope(scope)) return;
    state.sessions = result.items || [];
    renderSessions();
    scheduleSessionPrefetch(scope);
  } catch (error) {
    if (operation !== state.sessionListOperation || !isCurrentProjectScope(scope)) return;
    // A transient list refresh must not erase the selected conversation or the
    // answer that is already visible.  Keep the last good cache and retry on
    // the next refresh.
    renderSessions();
    showError(error, false);
  } finally {
    if (state.sessionListController === controller) state.sessionListController = null;
  }
}

function activeChatRun(sessionId = state.sessionId) {
  return sessionId ? state.activeChatRuns.get(sessionId) || null : null;
}

function cachedSession(sessionId) {
  return state.sessionCache.get(sessionId)?.session || null;
}

function requestSessionSnapshot(sessionId, scope) {
  const existing = state.sessionRequests.get(sessionId);
  if (existing && existing.projectId === scope.projectId && existing.generation === scope.generation) {
    return existing;
  }
  const controller = new AbortController();
  const request = { ...scope, controller, promise: null };
  request.promise = api(
    `/api/projects/${scope.projectId}/chat/sessions/${sessionId}`,
    { signal: controller.signal, cache: "no-store" },
  ).then((session) => {
    if (isCurrentProjectScope(scope)) rememberSession(session);
    return session;
  }).finally(() => {
    if (state.sessionRequests.get(sessionId) === request) state.sessionRequests.delete(sessionId);
  });
  state.sessionRequests.set(sessionId, request);
  return request;
}

function scheduleSessionPrefetch(scope) {
  clearTimeout(state.sessionPrefetchTimer);
  state.sessionPrefetchTimer = setTimeout(() => {
    state.sessionPrefetchTimer = null;
    if (!isCurrentProjectScope(scope)) return;
    state.sessions
      .filter((session) => !cachedSession(session.session_id) && !activeChatRun(session.session_id))
      .slice(0, SESSION_PREFETCH_LIMIT)
      .forEach((session) => {
        requestSessionSnapshot(session.session_id, scope).promise.catch(() => {});
      });
  }, 120);
}

function draftKey(sessionId = state.sessionId) {
  return sessionId || NEW_SESSION_DRAFT_KEY;
}

function saveChatDraft(sessionId = state.sessionId) {
  const input = $("#chat-input");
  if (!input) return;
  const value = input.value;
  const key = draftKey(sessionId);
  if (value) state.sessionDrafts.set(key, value);
  else state.sessionDrafts.delete(key);
}

function restoreChatDraft(sessionId = state.sessionId) {
  const input = $("#chat-input");
  if (!input) return;
  input.value = state.sessionDrafts.get(draftKey(sessionId)) || "";
  resizeChatInput();
}

function resizeChatInput() {
  const input = $("#chat-input");
  if (!input) return;
  input.style.height = "auto";
  input.style.height = `${Math.min(180, Math.max(52, input.scrollHeight))}px`;
}

function chatMessageMatchesRun(message, run) {
  if (!message || !run || message.role !== "assistant") return false;
  if (message === run.assistantMessage) return true;
  if (message.message_id && run.assistantMessage.message_id) {
    return message.message_id === run.assistantMessage.message_id;
  }
  return Boolean(message.request_id && run.requestId && message.request_id === run.requestId);
}

function sessionWithActiveRun(session) {
  const run = activeChatRun(session?.session_id);
  if (!run) return session;
  const messages = [...(session.messages || [])];
  const assistantIndex = messages.findIndex((message) => chatMessageMatchesRun(message, run));
  if (assistantIndex >= 0) {
    // Keep the optimistic object receiving SSE deltas. Replacing it with a
    // Warehouse snapshot would detach the visible stream or roll it back.
    messages[assistantIndex] = run.assistantMessage;
  } else {
    const hasUserMessage = messages.some((message) => (
      message.role === "user"
      && message.request_id
      && message.request_id === run.requestId
    ));
    if (!hasUserMessage && run.userMessage) messages.push(run.userMessage);
    messages.push(run.assistantMessage);
  }
  return { ...session, messages };
}

function rememberSession(session) {
  if (!session?.session_id) return session;
  const reconciled = sessionWithActiveRun(session);
  state.sessionCache.set(session.session_id, { session: reconciled, cachedAt: Date.now() });
  return reconciled;
}

function renderSessions() {
  const container = $("#session-list"); container.replaceChildren();
  const query = $("#session-search").value.trim().toLowerCase();
  const sessions = state.sessions.filter((item) => !query || String(item.title || "").toLowerCase().includes(query));
  if (!sessions.length) return container.append(emptyNode("会話履歴はまだありません。"));
  sessions.forEach((session) => {
    const selected = session.session_id === state.sessionId;
    const run = activeChatRun(session.session_id);
    const loading = state.sessionLoadingId === session.session_id;
    const deleting = state.sessionDeletions.has(session.session_id);
    const row = node("div", `session-row${selected ? " active" : ""}`);
    const button = node(
      "button",
      `session-item${selected ? " active" : ""}${run ? " running" : ""}${loading ? " loading" : ""}`,
    );
    button.type = "button";
    button.disabled = deleting;
    button.setAttribute("aria-current", selected ? "true" : "false");
    button.setAttribute("aria-busy", loading ? "true" : "false");
    button.append(node("strong", "", session.title || "新しい会話"));
    const meta = node("small", "session-meta");
    if (run) {
      meta.append(node("span", "mini-spinner", ""), node("span", "", run.statusText || "回答を作成中"));
    } else if (loading) {
      meta.append(node("span", "mini-spinner", ""), node("span", "", "読み込み中"));
    } else {
      meta.textContent = formatDate(session.updated_at);
    }
    button.append(meta);
    button.addEventListener("click", () => openSession(session.session_id));
    const remove = node("button", "session-delete-button", deleting ? "…" : "×");
    remove.type = "button";
    remove.disabled = deleting || Boolean(run);
    remove.setAttribute("aria-label", `会話「${session.title || "新しい会話"}」を削除`);
    remove.title = run ? "回答を停止してから削除してください" : "会話を削除";
    remove.addEventListener("click", () => deleteSession(session.session_id));
    row.append(button, remove);
    container.append(row);
  });
}

async function deleteSession(sessionId) {
  const scope = currentProjectScope();
  const target = state.sessions.find((item) => item.session_id === sessionId);
  if (!target || !isCurrentProjectScope(scope) || state.sessionDeletions.has(sessionId)) return;
  if (activeChatRun(sessionId)) {
    showError(new Error("回答を停止してから、この会話を削除してください。"));
    return;
  }
  const confirmed = await requestConfirmation({
    title: "会話を削除しますか？",
    message: `「${target.title || "新しい会話"}」の質問と回答を削除します。この操作は取り消せません。`,
    confirmLabel: "会話を削除",
  });
  if (!confirmed || !isCurrentProjectScope(scope) || activeChatRun(sessionId)) return;

  state.sessionDeletions.add(sessionId);
  renderSessions();
  try {
    await api(`/api/projects/${scope.projectId}/chat/sessions/${sessionId}`, { method: "DELETE" });
    if (!isCurrentProjectScope(scope)) return;
    state.sessionListOperation += 1;
    state.sessionListController?.abort();
    state.sessionListController = null;
    const request = state.sessionRequests.get(sessionId);
    request?.controller.abort();
    state.sessionRequests.delete(sessionId);
    state.sessions = state.sessions.filter((item) => item.session_id !== sessionId);
    state.sessionCache.delete(sessionId);
    state.sessionDrafts.delete(sessionId);
    if (state.sessionId === sessionId) {
      state.sessionOperation += 1;
      state.sessionLoadController?.abort();
      state.sessionLoadController = null;
      state.sessionLoadingId = null;
      state.sessionId = null;
      renderMessages([]);
      renderSessionEvidence(null);
      restoreChatDraft(null);
      const next = state.sessions[0];
      if (next) await openSession(next.session_id, scope);
    }
    toast("会話を削除しました。");
  } catch (error) {
    if (isCurrentProjectScope(scope)) showError(error);
  } finally {
    state.sessionDeletions.delete(sessionId);
    if (isCurrentProjectScope(scope)) {
      renderSessions();
      updateChatUi();
    }
  }
}

async function ensureSession(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  if (state.sessionId) {
    updateChatUi();
    return;
  }
  if (state.sessions.length) {
    await openSession(state.sessions[0].session_id, scope);
    return;
  }
  // Keep the composer usable for a Project with no history.  The first
  // submit creates its conversation before posting the message.
  renderMessages([]);
  updateChatUi();
}

async function createSession(scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return openProjectModal();
  if (state.sessionCreation) return state.sessionCreation;
  const previousSessionId = state.sessionId;
  saveChatDraft(previousSessionId);
  if (previousSessionId) state.sessionDrafts.delete(NEW_SESSION_DRAFT_KEY);

  // A list request started before the INSERT may return without the new row.
  // Invalidate it so an old response cannot erase the optimistic conversation.
  state.sessionListOperation += 1;
  state.sessionListController?.abort();
  state.sessionListController = null;
  const operation = ++state.sessionOperation;
  const controller = new AbortController();
  state.sessionCreationController = controller;
  state.sessionId = null;
  restoreChatDraft(null);
  renderSessions();
  renderSessionLoading("新しい会話を準備しています…");
  updateChatUi();
  const button = $("#new-chat-button");
  setBusy(button, true, "作成中…");
  const task = (async () => {
    try {
      const session = await api(`/api/projects/${scope.projectId}/chat/sessions`, {
        method: "POST",
        body: JSON.stringify({ title: "新しい会話" }),
        signal: controller.signal,
      });
      if (operation !== state.sessionOperation || !isCurrentProjectScope(scope)) return null;
      // A session-list request that started before this POST must not replace
      // the newly created conversation with its older snapshot.
      state.sessionListOperation += 1;
      state.sessionListController?.abort();
      state.sessionListController = null;
      state.sessions = [session, ...state.sessions.filter((item) => item.session_id !== session.session_id)];
      state.sessionId = session.session_id;
      if (!previousSessionId) {
        const pendingDraft = state.sessionDrafts.get(NEW_SESSION_DRAFT_KEY) || "";
        if (pendingDraft) state.sessionDrafts.set(session.session_id, pendingDraft);
        state.sessionDrafts.delete(NEW_SESSION_DRAFT_KEY);
      }
      rememberSession({ ...session, messages: [] });
      renderSessions();
      renderMessages([]);
      renderSessionEvidence(session.session_id);
      setChatTab("settings");
      restoreChatDraft(session.session_id);
      $("#chat-input").focus();
      updateChatUi();
      return session;
    } catch (error) {
      if (operation === state.sessionOperation && isCurrentProjectScope(scope)) {
        state.sessionId = previousSessionId;
        renderSessions();
        const previous = cachedSession(previousSessionId);
        renderMessages(previous?.messages || []);
        renderSessionEvidence(previousSessionId);
        restoreChatDraft(previousSessionId);
        updateChatUi();
        if (error?.name !== "AbortError") showError(error);
      }
      return null;
    }
  })();
  state.sessionCreation = task;
  updateChatUi();
  try { return await task; }
  finally {
    if (state.sessionCreationController === controller) state.sessionCreationController = null;
    if (state.sessionCreation === task) {
      state.sessionCreation = null;
      if (isCurrentProjectScope(scope)) {
        setBusy(button, false, "＋ 新しい会話");
        updateChatUi();
      }
    }
  }
}

async function openSession(sessionId, scope = currentProjectScope()) {
  if (!isCurrentProjectScope(scope)) return;
  if (state.sessionLoadingId === sessionId && state.sessionLoadController) return;
  if (state.sessionId === sessionId && cachedSession(sessionId)) {
    restoreChatDraft(sessionId);
    $("#chat-input").focus();
    return;
  }
  saveChatDraft(state.sessionId);
  state.sessionCreationController?.abort();
  state.sessionCreationController = null;
  const operation = ++state.sessionOperation;
  state.sessionLoadController?.abort();
  state.sessionLoadController = null;
  state.sessionLoadingId = sessionId;
  state.sessionId = sessionId;
  restoreChatDraft(sessionId);
  renderSessions();
  renderSessionEvidence(sessionId);
  const cached = state.sessionCache.get(sessionId);
  if (cached) renderMessages(cached.session.messages || []);
  else renderSessionLoading("会話を読み込んでいます…");
  updateChatUi();
  if (cached && (activeChatRun(sessionId) || Date.now() - cached.cachedAt < SESSION_CACHE_TTL_MS)) {
    state.sessionLoadController = null;
    state.sessionLoadingId = null;
    renderSessions();
    updateChatUi();
    return;
  }
  const request = requestSessionSnapshot(sessionId, scope);
  const controller = request.controller;
  state.sessionLoadController = controller;
  try {
    const session = await request.promise;
    if (operation !== state.sessionOperation || !isCurrentProjectScope(scope)) return;
    const reconciled = rememberSession(session);
    if (state.sessionId === sessionId) renderMessages(reconciled.messages || []);
  } catch (error) {
    if (error.name !== "AbortError" && operation === state.sessionOperation && isCurrentProjectScope(scope)) {
      if (!cached) renderMessages([]);
      showError(error);
    }
  } finally {
    if (state.sessionLoadController === controller) {
      state.sessionLoadController = null;
      state.sessionLoadingId = null;
      renderSessions();
      updateChatUi();
    }
  }
}

function renderSessionLoading(label) {
  const container = $("#chat-messages");
  const loading = node("div", "session-loading");
  loading.setAttribute("role", "status");
  loading.append(node("span", "thinking-spinner", ""), node("span", "", label));
  container.replaceChildren(loading);
  container.setAttribute("aria-busy", "true");
}

function renderMessages(messages) {
  const container = $("#chat-messages"); container.replaceChildren();
  container.setAttribute("aria-busy", "false");
  const run = activeChatRun();
  if (run) run.view = null;
  if (!messages.length) {
    const empty = node("div", "chat-empty");
    const suggestions = node("div", "suggestion-chips");
    ["この資料の要点を3つ教えて", "手順と注意点を整理して"].forEach((text) => {
      const button = node("button", "", text);
      button.type = "button";
      bindSuggestionButton(button);
      suggestions.append(button);
    });
    empty.append(
      node("h2", "", "RAGチャットを開始"),
      node("p", "", "PDFについて質問すると、根拠リンク付きで回答します。"),
      suggestions,
    );
    container.append(empty);
    return;
  }
  const fragment = document.createDocumentFragment();
  messages.forEach((message) => {
    const view = appendMessage(
      message.role,
      message.content || "",
      message.citations || [],
      message.trace_id || null,
      message.trace_href || null,
      message,
      { target: fragment, autoScroll: false },
    );
    if (run && chatMessageMatchesRun(message, run)) run.view = view;
  });
  container.append(fragment);
  if (run) renderActiveAssistant(run);
  else container.scrollTop = container.scrollHeight;
}

function bindSuggestionButton(button) {
  button.addEventListener("click", () => {
    $("#chat-input").value = button.textContent;
    saveChatDraft();
    resizeChatInput();
    $("#chat-input").focus();
  });
}

function appendMessage(
  role,
  content = "",
  citations = [],
  traceId = null,
  traceHref = null,
  messageModel = null,
  { target = $("#chat-messages"), autoScroll = true } = {},
) {
  const container = $("#chat-messages");
  const empty = $(".chat-empty", container);
  if (empty) empty.remove();
  const wrapper = node("div", `message ${role}`);
  wrapper.append(node("span", "message-label", role === "user" ? "あなた" : "RAGアシスタント"));
  const generationStatus = String(messageModel?.generation_status || "").toUpperCase();
  const fallbackContent = role === "assistant" && !content
    ? ({
      CANCELLED: "回答を停止しました。",
      ERROR: "回答を生成できませんでした。",
      STREAMING: "前回の回答は完了しませんでした。",
    })[generationStatus] || "回答はありません。"
    : content;
  const bubble = node("div", "message-bubble", fallbackContent); wrapper.append(bubble);
  const citationBox = node("div", "message-citations");
  citations.forEach((citation) => appendMessageCitation(citationBox, citation));
  const traceBox = node("div", "message-trace");
  renderTraceLink(traceBox, traceId, traceHref);
  const stateBox = node("div", "message-state");
  if (generationStatus === "CANCELLED") stateBox.textContent = "回答を停止しました";
  wrapper.append(citationBox, traceBox, stateBox);
  target.append(wrapper);
  if (autoScroll) container.scrollTop = container.scrollHeight;
  return { wrapper, bubble, citationBox, traceBox, stateBox };
}

function updatePhaseSummary() {
  const value = $("#chat-phase").value;
  const custom = value === "custom"; $("#custom-settings").disabled = !custom;
  if (custom) { $("#phase-summary").textContent = "カスタム：検索設定を個別に変更できます。"; return; }
  const phase = PHASES[value];
  $("#phase-summary").textContent = `${phase.label}：${phase.query} / 条件絞り込み ${onOff(phase.filter)} / 並べ替え ${onOff(phase.rerank)} / 質問最適化 ${onOff(phase.optimize)}`;
}

async function sendChatMessage(event) {
  event.preventDefault();
  const message = $("#chat-input").value.trim();
  const scope = currentProjectScope();
  if (!message || state.chatSubmission || activeChatRun() || !isCurrentProjectScope(scope)) return;

  // Acquire the lock before the first await.  This closes the race where five
  // Enter events all waited for the same new-session request and then each
  // posted the same question.
  const submission = { token: Symbol("chat-submit"), kind: "send", sessionId: state.sessionId };
  state.chatSubmission = submission;
  updateChatUi();
  let context = null;
  try {
    if (!state.sessionId) {
      const createdSession = await createSession(scope);
      if (!createdSession || state.sessionId !== createdSession.session_id) return;
    }
    if (state.chatSubmission !== submission || !isCurrentProjectScope(scope) || !state.sessionId) return;
    const sessionId = state.sessionId;
    submission.sessionId = sessionId;
    if (activeChatRun(sessionId)) return;

    const variant = $("#chat-variant").value;
    const model = $("#chat-model").value;
    if (!variant || !model) throw new Error("利用可能な検索データと回答モデルを選択してください。");
    const phase = $("#chat-phase").value;
    const retrieval = phase === "custom" ? {
      mode: "CUSTOM", query_type: $("#search-type").value,
      metadata_filtering: $("#metadata-filtering").checked,
      reranking: $("#reranking").checked, query_optimization: $("#query-optimization").checked,
    } : { mode: "PRESET", phase_id: phase };
    const requestId = crypto.randomUUID();
    const payload = {
      message,
      rag_mode: $("#rag-mode").value,
      retrieval,
      variant_id: variant,
      answer_model_key: model,
      client_request_id: requestId,
    };
    const controller = new AbortController();
    const session = cachedSession(sessionId) || {
      ...(state.sessions.find((item) => item.session_id === sessionId) || { session_id: sessionId, title: "新しい会話" }),
      messages: [],
    };
    const userMessage = {
      role: "user", content: message, request_id: requestId, generation_status: "COMPLETED",
    };
    const assistantMessage = {
      role: "assistant", content: "", citations: [], trace_id: null,
      request_id: requestId, generation_status: "STREAMING",
    };
    session.messages = [...(session.messages || []), userMessage, assistantMessage];
    rememberSession(session);
    context = {
      ...scope,
      sessionId,
      controller,
      requestId,
      userMessage,
      assistantMessage,
      view: null,
      evidence: null,
      startedAt: performance.now(),
      statusText: "質問を送信しています",
      stopRequested: false,
      terminal: null,
    };
    state.activeChatRuns.set(sessionId, context);
    state.chatSubmission = null;
    state.sessionDrafts.delete(draftKey(sessionId));
    $("#chat-input").value = "";
    resizeChatInput();
    if (state.sessionId === sessionId) {
      renderMessages(session.messages);
      clearEvidence();
    }
    renderSessions();
    updateChatUi();

    const response = await fetch(`/api/projects/${scope.projectId}/chat/sessions/${sessionId}/messages:stream`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal: controller.signal,
    });
    if (!isCurrentChatContext(context)) return;
    if (!response.ok) {
      const body = await response.json().catch(() => ({})); throw new Error(body?.error?.message || `送信に失敗しました（${response.status}）`);
    }
    const acceptedRequestId = response.headers.get("X-Request-ID");
    if (acceptedRequestId) {
      context.requestId = acceptedRequestId;
      context.userMessage.request_id = acceptedRequestId;
      context.assistantMessage.request_id = acceptedRequestId;
    }
    context.statusText = "文書を検索しています";
    renderSessions();
    updateChatUi();
    await consumeSse(response, (eventData) => handleChatEvent(eventData, context));
    if (!context.terminal) throw new Error("回答ストリームが完了通知なしで終了しました。");
  } catch (error) {
    if (error.name !== "AbortError" && context && !context.terminal && isCurrentChatContext(context)) {
      context.terminal = "error";
      context.assistantMessage.content = `エラー：${error.message}`;
      showError(error);
    } else if (error.name !== "AbortError" && !context && isCurrentProjectScope(scope)) {
      showError(error);
    }
  } finally {
    if (state.chatSubmission === submission) state.chatSubmission = null;
    if (context && isCurrentChatContext(context)) finishChatRun(context);
    updateChatUi();
  }
}

function isCurrentChatContext(context) {
  return Boolean(isCurrentProjectScope(context)
    && state.activeChatRuns.get(context.sessionId) === context);
}

async function consumeSse(response, callback) {
  if (!response.body) throw new Error("回答ストリームを読み取れません。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      if (done && buffer.trim()) blocks.push(buffer);
      for (const block of blocks) {
        const data = block.split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data && callback(JSON.parse(data)) === false) {
          // A terminal SSE event is the protocol boundary. Do not keep the
          // composer locked while waiting for a slow proxy to close the socket.
          await reader.cancel().catch(() => {});
          return;
        }
      }
      if (done) return;
    }
  } finally {
    reader.releaseLock();
  }
}

function handleChatEvent(event, context) {
  if (!isCurrentChatContext(context)) return false;
  if (context.terminal) return false;
  const payload = event.payload || {};
  let sessionStatusChanged = false;
  if (event.type === "run.started") {
    context.requestId = event.request_id || context.requestId;
    context.userMessage.request_id = context.requestId;
    context.assistantMessage.request_id = context.requestId;
    context.assistantMessage.message_id = payload.message_id || context.assistantMessage.message_id;
    context.statusText = "文書を検索しています";
    sessionStatusChanged = true;
  }
  if (event.type === "trace.available") {
    context.assistantMessage.trace_id = payload.trace_id || null;
    context.assistantMessage.trace_href = payload.href || null;
    if (context.view && state.sessionId === context.sessionId) {
      renderTraceLink(context.view.traceBox, payload.trace_id, payload.href);
    }
  }
  if (event.type === "retrieval.completed") {
    context.evidence = payload;
    context.statusText = "回答を作成しています";
    sessionStatusChanged = true;
    if (state.sessionId === context.sessionId) renderEvidence(payload);
  }
  if (event.type === "response.delta") {
    context.statusText = "回答を作成しています";
    context.assistantMessage.content += payload.text || "";
  }
  if (event.type === "citation.added") {
    context.assistantMessage.citations.push(payload);
    if (context.view && state.sessionId === context.sessionId) appendMessageCitation(context.view.citationBox, payload);
    if (state.sessionId === context.sessionId) appendCitation(payload);
  }
  if (event.type === "run.completed") {
    context.terminal = "completed";
    context.assistantMessage.generation_status = "COMPLETED";
    sessionStatusChanged = true;
  }
  if (event.type === "run.cancelled") {
    context.terminal = "cancelled";
    context.assistantMessage.generation_status = "CANCELLED";
    sessionStatusChanged = true;
    toast("回答を停止しました。");
  }
  if (event.type === "run.error") {
    context.terminal = "error";
    context.assistantMessage.generation_status = "ERROR";
    context.assistantMessage.content = `エラー：${payload.message}`;
    sessionStatusChanged = true;
    showError(new Error(payload.message));
  }
  if (state.sessionId === context.sessionId) renderActiveAssistant(context);
  if (sessionStatusChanged) renderSessions();
  updateChatUi();
  return !context.terminal;
}

function appendMessageCitation(container, citation) {
  const page = citation.page_number || citation.page_numbers?.[0];
  const suffix = page ? ` ${page}ページ` : "";
  const link = node("a", "", `[${citation.citation_id || "根拠"}] PDFを開く：${citation.title || "文書"}${suffix}`);
  link.href = citationPdfHref(citation);
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  bindPdfReferencePrefetch(link, citation);
  container.append(link);
}

function citationDocumentId(citation) {
  if (typeof citation?.document_id === "string" && citation.document_id) return citation.document_id;
  const href = safeInternalHref(citation?.href);
  if (href === "#") return null;
  const match = href.match(/\/catalog\/([^/?#]+)/);
  if (!match) return null;
  try { return decodeURIComponent(match[1]); }
  catch (_) { return null; }
}

function citationPdfHref(citation) {
  const documentId = citationDocumentId(citation);
  const page = citation?.page_number || citation?.page_numbers?.[0] || 1;
  return documentId ? pdfContentHref(documentId, page) : "#";
}

function bindPdfReferencePrefetch(link, citation) {
  const documentId = citationDocumentId(citation);
  if (!documentId) return;
  ["pointerenter", "pointerdown", "focus", "touchstart"].forEach((eventName) => {
    link.addEventListener(eventName, () => warmPdf(documentId), { passive: true });
  });
}

function renderActiveAssistant(context) {
  if (!context.view || state.sessionId !== context.sessionId) return;
  const { bubble, wrapper, stateBox } = context.view;
  bubble.replaceChildren();
  if (context.assistantMessage.content) {
    bubble.append(document.createTextNode(context.assistantMessage.content));
  } else {
    const thinking = node("span", "thinking-indicator");
    // The single live-region below the message list announces progress. Keep
    // this visual indicator silent so screen readers do not hear it twice.
    thinking.setAttribute("aria-hidden", "true");
    thinking.append(node("span", "thinking-spinner", ""), node("span", "thinking-label", context.statusText || "考えています"));
    bubble.append(thinking);
  }
  stateBox.textContent = context.stopRequested ? "停止しています…" : "";
  wrapper.classList.toggle("is-thinking", !context.assistantMessage.content);
  wrapper.setAttribute("aria-busy", "true");
  $("#chat-messages").scrollTop = $("#chat-messages").scrollHeight;
}

function finishChatRun(context) {
  if (!isCurrentChatContext(context)) return;
  state.activeChatRuns.delete(context.sessionId);
  const terminal = context.terminal || (context.stopRequested ? "cancelled" : "completed");
  context.assistantMessage.generation_status = ({
    completed: "COMPLETED", cancelled: "CANCELLED", error: "ERROR",
  })[terminal] || "COMPLETED";
  if (!context.assistantMessage.content) {
    context.assistantMessage.content = terminal === "cancelled"
      ? "回答を停止しました。"
      : "回答を生成できませんでした。";
  }
  if (context.view && state.sessionId === context.sessionId) {
    context.view.wrapper.classList.remove("is-thinking");
    context.view.wrapper.setAttribute("aria-busy", "false");
    context.view.bubble.textContent = context.assistantMessage.content;
    context.view.stateBox.textContent = terminal === "cancelled" ? "回答を停止しました" : "";
  }
  const cached = cachedSession(context.sessionId);
  if (cached) rememberSession(cached);
  renderSessions();
  updateChatUi();
  void loadSessions({ projectId: context.projectId, generation: context.generation });
}

function renderEvidence(payload, activate = true) {
  setEvidenceCount(payload.result_count || 0);
  const chips = $("#query-chips"); chips.replaceChildren();
  (payload.expanded_queries || []).forEach((query) => chips.append(node("span", "", query)));
  const container = $("#evidence-list"); container.replaceChildren();
  (payload.evidence || []).forEach((item) => {
    const card = node("article", "evidence-item"); const header = document.createElement("header");
    header.append(node("span", "", `#${item.rank} ${item.title}`), node("span", "", item.page ? `${item.page}ページ` : ""));
    card.append(header);
    if (item.document_id) {
      const link = node("a", "citation-link", "PDF原文を開く");
      link.href = citationPdfHref({ document_id: item.document_id, page_number: item.page, href: item.href });
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      bindPdfReferencePrefetch(link, { document_id: item.document_id });
      card.append(link);
    } else {
      card.append(node("p", "", "PDF原文は、回答に付いた「PDFを開く」リンクから確認できます。"));
    }
    container.append(card);
  });
  if (!(payload.evidence || []).length) container.append(emptyNode("検索されたPDFがありません。"));
  if (activate) setChatTab("evidence");
}

function renderSessionEvidence(sessionId) {
  const run = activeChatRun(sessionId);
  if (run?.evidence) {
    renderEvidence(run.evidence, false);
    return;
  }
  $("#evidence-list").replaceChildren(emptyNode("この会話で質問すると検索されたPDFを表示します。"));
  $("#citation-list").replaceChildren();
  $("#query-chips").replaceChildren();
  setEvidenceCount(0);
}

function appendCitation(payload) {
  const link = node("a", "citation-link", `[${payload.citation_id}] PDFを開く：${payload.title} — ${payload.page_number}ページ`);
  link.href = citationPdfHref(payload);
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  bindPdfReferencePrefetch(link, payload);
  $("#citation-list").append(link);
}

function renderTraceLink(container, traceId, href) {
  container.replaceChildren();
  if (typeof traceId !== "string" || !/^tr-[0-9a-f]{32}$/.test(traceId)) return;
  const label = `MLflow Trace: ${traceId}`;
  const safeHref = safeTraceHref(href);
  if (safeHref === "#") {
    container.append(node("span", "", label));
    return;
  }
  const link = node("a", "", label);
  link.href = safeHref;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  container.append(link);
}

function setEvidenceCount(value) {
  const count = String(Number(value) || 0);
  $("#evidence-count").textContent = count;
  $("#chat-details-count").textContent = count;
}

function clearEvidence() {
  $("#evidence-list").replaceChildren(emptyNode("PDFを検索中…"));
  $("#citation-list").replaceChildren();
  $("#query-chips").replaceChildren();
  setEvidenceCount(0);
}

async function stopChat() {
  const context = activeChatRun();
  if (!context || context.stopRequested || state.chatCancelRequests.has(context.requestId)) return;
  context.stopRequested = true;
  context.statusText = "停止しています";

  // Abort the original SSE immediately so the button feels instantaneous and
  // the generator receives cancellation on the instance that owns it.  The
  // durable API call is still sent independently for audit/recovery.
  const cancelController = new AbortController();
  const cancelTimer = setTimeout(() => cancelController.abort(), 3000);
  const cancelRequest = { controller: cancelController, requestId: context.requestId };
  state.chatCancelRequests.set(context.requestId, cancelRequest);
  renderActiveAssistant(context);
  updateChatUi();
  const cancelTask = api(
    `/api/projects/${context.projectId}/chat/runs/${context.requestId}:cancel`,
    { method: "POST", body: "{}", signal: cancelController.signal },
  );
  context.controller.abort();
  context.terminal = "cancelled";
  context.assistantMessage.generation_status = "CANCELLED";
  finishChatRun(context);
  toast("回答を停止しました。");
  try {
    await cancelTask;
  } catch (error) {
    // A very early abort can close the stream before the server persists the
    // run.  The local request is already stopped, so a resulting 404 is not a
    // user-facing failure.
    if (error.name !== "AbortError" && error.status !== 404 && isCurrentProjectScope(context)) showError(error);
  } finally {
    clearTimeout(cancelTimer);
    if (state.chatCancelRequests.get(context.requestId) === cancelRequest) {
      state.chatCancelRequests.delete(context.requestId);
    }
    if (isCurrentProjectScope(context)) updateChatUi();
  }
}

function updateChatUi() {
  const run = activeChatRun();
  const submission = state.chatSubmission;
  const submittingCurrent = Boolean(submission && (!submission.sessionId || submission.sessionId === state.sessionId));
  // A cached conversation is already interactive while its stale snapshot is
  // refreshed in the background. Only a cache miss blocks the composer.
  const loadingCurrent = Boolean(
    state.sessionLoadingId
    && state.sessionLoadingId === state.sessionId
    && !cachedSession(state.sessionId),
  );
  const creatingCurrent = Boolean(state.sessionCreation && !state.sessionId);
  const busy = Boolean(run || submittingCurrent || loadingCurrent || creatingCurrent);
  const status = $("#stream-status");
  status.classList.toggle("hidden", !run && !submittingCurrent && !creatingCurrent);
  status.setAttribute("aria-live", "polite");
  const statusText = $("#stream-status-text");
  if (statusText) {
    statusText.textContent = run?.statusText
      || (creatingCurrent ? "新しい会話を準備しています" : "送信を準備しています");
  }
  const stop = $("#stop-button");
  stop.classList.toggle("hidden", !run);
  stop.disabled = !run || run.stopRequested;
  stop.replaceChildren(
    node("span", run?.stopRequested ? "button-spinner" : "stop-icon", ""),
    node("span", "", run?.stopRequested ? "停止中…" : "停止"),
  );
  const send = $("#send-button");
  send.classList.toggle("hidden", Boolean(run));
  send.disabled = busy;
  $("#chat-input").disabled = busy;
  $("#new-chat-button").disabled = Boolean(state.sessionCreation || submittingCurrent);
  $(".chat-pane")?.classList.toggle("is-streaming", Boolean(run || submittingCurrent));
  $("#chat-messages").setAttribute("aria-busy", busy ? "true" : "false");

  const needsTimer = state.activeChatRuns.size > 0;
  if (needsTimer && !state.streamTimer) {
    state.streamTimer = setInterval(updateChatTimer, 250);
  } else if (!needsTimer && state.streamTimer) {
    clearInterval(state.streamTimer);
    state.streamTimer = null;
  }
  updateChatTimer();
}

function updateChatTimer() {
  const run = activeChatRun();
  $("#stream-timer").textContent = run ? `${((performance.now() - run.startedAt) / 1000).toFixed(1)}秒` : "0.0秒";
}

function handleChatTabKeydown(event) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = $$('[data-chat-tab]');
  const currentIndex = tabs.indexOf(event.currentTarget);
  if (currentIndex < 0 || !tabs.length) return;
  event.preventDefault();
  let nextIndex = currentIndex;
  if (event.key === "Home") nextIndex = 0;
  else if (event.key === "End") nextIndex = tabs.length - 1;
  else if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
  else nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
  const nextTab = tabs[nextIndex];
  setChatTab(nextTab.dataset.chatTab);
  nextTab.focus();
}

function setChatTab(name) {
  $$('[data-chat-tab]').forEach((button) => {
    const selected = button.dataset.chatTab === name;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  const settings = $("#chat-settings");
  const evidence = $("#chat-evidence");
  settings.classList.toggle("active", name === "settings");
  evidence.classList.toggle("active", name === "evidence");
  settings.hidden = name !== "settings";
  evidence.hidden = name !== "evidence";
}

function setChatDetailsOpen(open, { persist = false } = {}) {
  state.chatDetailsOpen = Boolean(open);
  const layout = $(".chat-layout");
  const panel = $("#chat-details");
  const toggle = $("#chat-details-toggle");
  if (!layout || !panel || !toggle) return;
  layout.classList.toggle("details-open", state.chatDetailsOpen);
  panel.hidden = !state.chatDetailsOpen;
  toggle.setAttribute("aria-expanded", String(state.chatDetailsOpen));
  toggle.classList.toggle("active", state.chatDetailsOpen);
  if (persist) localStorage.setItem("rag-eval-chat-details", state.chatDetailsOpen ? "open" : "closed");
}

function syncEvaluationSelects() {
  copySelect($("#chat-variant"), $("#evaluation-variant"));
  copySelect($("#chat-model"), $("#evaluation-model"));
  updateRuntimeIndexDetails();
}

async function startEvaluation() {
  const scope = currentProjectScope();
  if (!isCurrentProjectScope(scope)) return openProjectModal();
  const phases = $$('input[name="eval-phase"]:checked').map((input) => input.value);
  const evaluationCaseIds = selectedEvaluationCaseIds();
  const payload = {
    phase_ids: phases, trial_count: Number($("#trial-count").value), dataset_version: $("#dataset-version").value,
    dataset_split: $("#dataset-split").value, variant_id: $("#evaluation-variant").value,
    evaluation_case_ids: evaluationCaseIds,
    answer_model_key: $("#evaluation-model").value, judge_model_key: $("#judge-model").value, final_k: 10,
  };
  if (!phases.length || !payload.dataset_version || !evaluationCaseIds.length || !payload.variant_id || !payload.answer_model_key || !payload.judge_model_key) return showError(new Error("評価質問、Phase、検索データ、回答モデル、採点モデルを選択してください。"));
  clearTimeout(state.evaluationPoll);
  state.evaluationPoll = null;
  state.evaluationController?.abort();
  state.evaluationController = null;
  const operation = ++state.evaluationOperation;
  state.evaluationRunId = null;
  $("#evaluation-progress-card").classList.add("hidden");
  $("#evaluation-progress-label").textContent = "0 / 0";
  $("#evaluation-progress-bar").style.width = "0%";
  $("#phase-run-status").replaceChildren();
  $("#evaluation-progress-title").textContent = "Phase比較を実行中";
  renderMetrics([]);
  renderSuggestions([]);
  setEvaluationRunningUi(true, "Jobを開始中…");
  try {
    const run = await api(`/api/projects/${scope.projectId}/evaluation-runs`, {
      method: "POST", headers: { "Idempotency-Key": crypto.randomUUID().replaceAll("-", "") }, body: JSON.stringify(payload),
    });
    if (!isCurrentProjectScope(scope) || operation !== state.evaluationOperation) return;
    state.evaluationRunId = run.eval_run_id;
    void loadEvaluationRuns(scope);
    setEvaluationRunningUi(true, "評価を実行中…");
    $("#evaluation-progress-card").classList.remove("hidden");
    $("#cancel-evaluation").classList.remove("hidden");
    toast("RAG精度の比較を開始しました。");
    renderEvaluationProgress({ phases: run.phases });
    pollEvaluation({ ...scope, runId: run.eval_run_id, operation });
  } catch (error) {
    if (!isCurrentProjectScope(scope) || operation !== state.evaluationOperation) return;
    showError(error);
    setEvaluationRunningUi(false);
  }
}

function isCurrentEvaluationContext(context) {
  return Boolean(isCurrentProjectScope(context)
    && state.evaluationOperation === context.operation
    && state.evaluationRunId === context.runId);
}

function setEvaluationRunningUi(active, startLabel = "RAG精度を比較") {
  $("#page-evaluation").dataset.running = String(Boolean(active));
  $$("#page-evaluation .evaluation-config input, #page-evaluation .evaluation-config select, #page-evaluation .evaluation-mutating-control, #page-evaluation .evaluation-case-item input")
    .forEach((control) => { control.disabled = active; });
  if (active) setBusy($("#start-evaluation"), true, startLabel);
  else updateEvaluationSelectionUi();
  if (!active) {
    $("#cancel-evaluation").disabled = false;
    $("#cancel-evaluation").textContent = "評価を停止";
  }
}

function pollEvaluation(context) {
  if (!isCurrentEvaluationContext(context)) return;
  clearTimeout(state.evaluationPoll);
  const poll = async () => {
    if (!isCurrentEvaluationContext(context)) return;
    state.evaluationPoll = null;
    const controller = new AbortController();
    state.evaluationController?.abort();
    state.evaluationController = controller;
    try {
      const run = await api(`/api/projects/${context.projectId}/evaluation-runs/${context.runId}`, { signal: controller.signal });
      if (!isCurrentEvaluationContext(context) || state.evaluationController !== controller) return;
      state.evaluationController = null;
      renderEvaluationProgress(run);
      if (["SUCCEEDED", "PARTIAL", "FAILED", "CANCELED"].includes(run.status)) {
        setEvaluationRunningUi(false);
        $("#cancel-evaluation").classList.add("hidden");
        $("#evaluation-progress-title").textContent = "保存済みの評価結果";
        await loadEvaluationResults(context);
        return;
      }
      state.evaluationPoll = setTimeout(poll, 4000);
    } catch (error) {
      if (error?.name === "AbortError" || !isCurrentEvaluationContext(context)) return;
      if (state.evaluationController === controller) state.evaluationController = null;
      showError(error, false);
      state.evaluationPoll = setTimeout(poll, 8000);
    }
  };
  poll();
}

function renderEvaluationProgress(run) {
  const phases = run.phases || []; const expected = phases.reduce((sum, item) => sum + Number(item.expected_trials || 0), 0); const completed = phases.reduce((sum, item) => sum + Number(item.completed_trials || 0), 0);
  const percent = expected ? Math.round(completed / expected * 100) : 0; $("#evaluation-progress-label").textContent = `${completed} / ${expected || "準備中"}`; $("#evaluation-progress-bar").style.width = `${percent}%`;
  const container = $("#phase-run-status"); container.replaceChildren();
  phases.forEach((phase) => { const item = node("div", "phase-run-item"); item.append(node("strong", "", PHASES[phase.phase_id]?.label || phase.phase_id), node("span", "", formatStatus(phase.status))); container.append(item); });
}

async function loadEvaluationResults(context) {
  if (!isCurrentEvaluationContext(context)) return;
  try {
    const result = await api(`/api/projects/${context.projectId}/evaluation-runs/${context.runId}/results`);
    if (!isCurrentEvaluationContext(context)) return;
    renderMetrics(result.metrics || []); renderSuggestions(result.suggestions || []);
    void loadEvaluationRuns(context);
  } catch (error) {
    if (isCurrentEvaluationContext(context)) showError(error);
  }
}

function renderMetrics(metrics) {
  state.lastMetrics = metrics;
  const latest = metrics.at(-1) || {};
  $("#metric-recall").textContent = percent(latest.recall_at_10);
  $("#metric-correctness").textContent = percent(latest.answer_correctness);
  $("#metric-groundedness").textContent = percent(latest.groundedness);
  $("#metric-citation-correctness").textContent = percent(latest.citation_correctness);
  $("#metric-ttft").textContent = duration(latest.ttft_p50_ms);
  $("#metric-latency-p50").textContent = duration(latest.latency_p50_ms);
  $("#metric-latency-p95").textContent = duration(latest.latency_p95_ms);
  $("#metric-tokens").textContent = formatTokens(latest.total_tokens);
  $("#metric-cost").textContent = formatCost(latest.cost_usd);
  const body = $("#metrics-table-body"); body.replaceChildren();
  if (!metrics.length) body.append(rowWithMessage("評価結果がありません。", 13));
  metrics.forEach((metric) => {
    const row = document.createElement("tr");
    [
      PHASES[metric.phase_id]?.label || metric.phase_id,
      percent(metric.recall_at_10), percent(metric.precision_at_10), decimal(metric.ndcg_at_10),
      percent(metric.answer_correctness), percent(metric.groundedness), percent(metric.citation_correctness),
      duration(metric.ttft_p50_ms), duration(metric.latency_p50_ms), duration(metric.latency_p95_ms),
      formatTokens(metric.total_tokens), formatCost(metric.cost_usd), percent(metric.error_rate),
    ].forEach((value) => row.append(node("td", "", value)));
    body.append(row);
  });
  drawPhaseChart(metrics);
}

function drawPhaseChart(metrics) {
  const canvas = $("#phase-chart");
  const empty = $("#chart-empty");
  empty.classList.toggle("hidden", metrics.length > 0);
  const previousContext = canvas.getContext("2d");
  previousContext.clearRect(0, 0, canvas.width, canvas.height);
  if (!metrics.length) return;
  const width = canvas.clientWidth || 900; const height = 280; const ratio = window.devicePixelRatio || 1; canvas.width = width * ratio; canvas.height = height * ratio;
  const ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio); ctx.clearRect(0, 0, width, height);
  const styles = getComputedStyle(document.documentElement); const ink = styles.getPropertyValue("--ink").trim(); const muted = styles.getPropertyValue("--muted").trim(); const line = styles.getPropertyValue("--line").trim(); const accent = styles.getPropertyValue("--accent").trim(); const blue = styles.getPropertyValue("--blue").trim();
  const left = 42, right = 38, top = 22, bottom = 38, plotW = width-left-right, plotH = height-top-bottom;
  ctx.font = "11px system-ui"; ctx.strokeStyle = line; ctx.fillStyle = muted;
  for (let i=0;i<=4;i++) { const y=top+plotH*i/4; ctx.beginPath(); ctx.moveTo(left,y); ctx.lineTo(width-right,y); ctx.stroke(); ctx.fillText(`${100-i*25}%`, 4, y+4); }
  const slot = plotW/metrics.length; const maxLatency = Math.max(...metrics.map((m) => Number(m.latency_p50_ms)||0), 1); const points=[];
  metrics.forEach((metric,index) => { const x=left+slot*(index+.5), value=Number(metric.recall_at_10)||0, barW=Math.min(48,slot*.45); ctx.fillStyle=accent; ctx.fillRect(x-barW/2,top+plotH*(1-value),barW,plotH*value); ctx.fillStyle=ink; ctx.textAlign="center"; ctx.fillText(PHASES[metric.phase_id]?.label||metric.phase_id,x,height-12); points.push([x,top+plotH*(1-(Number(metric.latency_p50_ms)||0)/maxLatency)]); });
  ctx.strokeStyle=blue; ctx.lineWidth=2; ctx.beginPath(); points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y)); ctx.stroke(); points.forEach(([x,y])=>{ctx.fillStyle=blue;ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fill();}); ctx.textAlign="left"; ctx.fillStyle=muted;
}

function renderSuggestions(suggestions) {
  const container = $("#suggestion-list"); container.replaceChildren();
  if (!suggestions.length) return container.append(emptyNode("評価後、Phaseごとの改善提案を表示します。"));
  suggestions.forEach((suggestion) => {
    const card = node("article", "suggestion-card"); const header = document.createElement("header"); header.append(node("strong", "", `${PHASES[suggestion.phase_id]?.label || suggestion.phase_id} · ${formatSuggestionTarget(suggestion.target)}`), node("span", `status-pill ${suggestion.priority === "high" ? "error" : "info"}`, formatPriority(suggestion.priority)));
    card.append(header, node("p", "", suggestion.diagnosis || ""), node("p", "", `提案：${suggestion.proposed_change || ""}`));
    const details = document.createElement("details"); details.append(node("summary", "", "期待効果・再評価方法"), node("p", "", suggestion.expected_effect || ""), node("p", "", suggestion.retest_plan || "")); card.append(details); container.append(card);
  });
}

async function cancelEvaluation() {
  const scope = currentProjectScope();
  const context = {
    ...scope,
    runId: state.evaluationRunId,
    operation: state.evaluationOperation,
  };
  if (!isCurrentEvaluationContext(context)) return;
  const button = $("#cancel-evaluation");
  setBusy(button, true, "停止を要求中…");
  try {
    await api(`/api/projects/${context.projectId}/evaluation-runs/${context.runId}:cancel`, { method: "POST", body: "{}" });
    if (!isCurrentEvaluationContext(context)) return;
    button.textContent = "停止要求済み";
    toast("評価の停止をリクエストしました。");
  } catch (error) {
    if (!isCurrentEvaluationContext(context)) return;
    setBusy(button, false, "評価を停止");
    showError(error);
  }
}

async function refreshPage() {
  const button = $("#refresh-button"); button.disabled = true;
  const scope = currentProjectScope();
  const generation = state.projectGeneration;
  try {
    const [, projectsResult] = await Promise.all([checkHealth(), api("/api/projects")]);
    if (generation !== state.projectGeneration || (scope && !isCurrentProjectScope(scope))) return;
    state.projects = projectsResult.items || [];
    const select = $("#project-select");
    select.replaceChildren(new Option("プロジェクトを選択", ""));
    state.projects.forEach((project) => select.add(new Option(project.name, project.project_id)));
    const currentProject = state.projects.find((project) => project.project_id === state.projectId) || null;
    if (!currentProject) {
      await switchProject(null, { historyMode: "replace" });
      toast("プロジェクト一覧を更新しました。");
      return;
    }
    select.value = currentProject.project_id;
    updateProjectChrome(currentProject);
    await loadProjectData(scope);
    if (isCurrentProjectScope(scope)) toast("表示とプロジェクト一覧を更新しました。");
  } catch (error) {
    if (!scope || isCurrentProjectScope(scope)) showError(error);
  } finally {
    button.disabled = false;
  }
}

function stopActiveStreams() {
  state.sessionLoadController?.abort();
  state.sessionLoadController = null;
  state.sessionLoadingId = null;
  state.activeChatRuns.forEach((run) => run.controller.abort());
  state.activeChatRuns.clear();
  state.chatCancelRequests.forEach((request) => request.controller.abort());
  state.chatCancelRequests.clear();
  state.chatSubmission = null;
  clearTimeout(state.evaluationPoll);
  clearInterval(state.streamTimer);
  state.streamTimer = null;
  stopPreparationMonitor({ resetRun: true });
}
function toggleSidebar() { const sidebar = $("#sidebar"); const open = sidebar.classList.toggle("open"); $("#sidebar-toggle").setAttribute("aria-expanded", String(open)); }
function closeSidebar() { $("#sidebar").classList.remove("open"); $("#sidebar-toggle").setAttribute("aria-expanded", "false"); }
function restoreTheme() { document.documentElement.dataset.theme = localStorage.getItem("rag-eval-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"); }
function restoreChatPreferences() { setChatDetailsOpen(localStorage.getItem("rag-eval-chat-details") === "open"); }
function toggleTheme() { const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = theme; localStorage.setItem("rag-eval-theme", theme); if (state.lastMetrics) drawPhaseChart(state.lastMetrics); }

function showError(error, prominent = true) {
  const message = error?.message || "処理に失敗しました。";
  if (prominent) { const banner = $("#global-error"); banner.textContent = message; banner.classList.remove("hidden"); setTimeout(() => banner.classList.add("hidden"), 9000); }
  toast(message, true);
}
function toast(message, isError = false) { const item = node("div", `toast${isError ? " error" : ""}`, message); $("#toast-region").append(item); setTimeout(() => item.remove(), 5200); }
function setBusy(button, busy, text) { button.disabled = busy; button.textContent = text; }
function node(tag, className = "", text = null) { const element = document.createElement(tag); if (className) element.className = className; if (text !== null && text !== undefined) element.textContent = String(text); return element; }
function emptyNode(text) { return node("p", "empty-inline", text); }
function statusPill(status = "UNKNOWN") { return node("span", `status-pill ${statusClass(status)}`, formatStatus(status)); }
function statusClass(status) { const value = String(status || "").toUpperCase(); if (["READY", "SUCCEEDED", "PARSED", "ACTIVE"].includes(value)) return "success"; if (["ERROR", "FAILED"].includes(value)) return "error"; if (["PARSING", "RUNNING", "PREPARING", "EVALUATING"].includes(value)) return "info"; return "muted"; }
function formatStatus(status) { const value = String(status || "UNKNOWN").toUpperCase(); return ({ READY: "利用可能", SUCCEEDED: "完了", PARSED: "解析済み", ACTIVE: "有効", EMPTY: "未準備", ERROR: "エラー", FAILED: "失敗", NOT_READY: "利用不可", PARSING: "解析中", RUNNING: "実行中", PREPARING: "準備中", EVALUATING: "評価中", QUEUED: "待機中", CANCELED: "停止", PARTIAL: "一部完了", UNKNOWN: "未確認" })[value] || String(status); }
function formatCapability(value) { return ({ embedding: "ベクトル化", chat: "回答生成", judge: "採点", tool_calling: "ツール連携" })[String(value)] || String(value); }
function formatChunkMethod(value) { const key = String(value || "").split(".").at(-1).toUpperCase(); return ({ STANDARD: "均等（Standard）", SEMANTIC: "意味単位（Semantic）", PARENT_CHILD: "親子（Parent-child）", CONFIGURED: "既定の検索データ" })[key] || value || "検索データ"; }
function formatDocumentType(value) { return ({ owners_guide: "取扱ガイド", equipment_spec: "グレード別装備表", safety_operation_guide: "安全支援操作ガイド", emergency_response_guide: "緊急時対応ガイド", model_change_report: "モデル変更レポート", glossary: "用語集", emergency_response_scan: "緊急時対応ガイド（スキャン）" })[String(value)] || String(value || "—"); }
function formatSuggestionTarget(value) { return ({ retrieval: "検索", metadata: "条件絞り込み", reranking: "検索順位", query_optimization: "質問最適化", chunking: "文章の分け方", embedding: "ベクトル化", prompt: "回答指示", evaluation_data: "評価データ" })[String(value)] || String(value || "改善"); }
function formatPriority(value) { return ({ high: "優先度 高", medium: "優先度 中", low: "優先度 低" })[String(value)] || "優先度 中"; }
function formatDate(value) { if (!value) return "日時なし"; const date = new Date(value); return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat("ja-JP", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date); }
function formatBytes(bytes) { if (bytes < 1024) return `${bytes} B`; if (bytes < 1024**2) return `${(bytes/1024).toFixed(1)} KB`; return `${(bytes/1024**2).toFixed(1)} MB`; }
function onOff(value) { return value ? "ON" : "OFF"; }
function percent(value) { return value === null || value === undefined ? "—" : `${(Number(value)*100).toFixed(1)}%`; }
function decimal(value) { return value === null || value === undefined ? "—" : Number(value).toFixed(3); }
function duration(value) { return value === null || value === undefined ? "—" : Number(value) >= 1000 ? `${(Number(value)/1000).toFixed(2)} s` : `${Number(value).toFixed(0)} ms`; }
function formatTokens(value) { return value === null || value === undefined ? "—" : Math.round(Number(value)).toLocaleString("ja-JP"); }
function formatCost(value) { return value === null || value === undefined ? "未取得" : `$${Number(value).toFixed(4)}`; }
function safeInternalHref(value) { return typeof value === "string" && value.startsWith(`/projects/${state.projectId}/catalog/`) ? value : "#"; }
function safeTraceHref(value) {
  if (typeof value !== "string") return "#";
  try {
    const url = new URL(value);
    const trustedHost = [".azuredatabricks.net", ".cloud.databricks.com", ".gcp.databricks.com"]
      .some((suffix) => url.hostname.endsWith(suffix));
    const trustedPath = /^\/ml\/experiments\/[^/]+\/traces$/.test(url.pathname);
    return url.protocol === "https:" && trustedHost && trustedPath ? url.href : "#";
  } catch (_) {
    return "#";
  }
}
function safeJobHref(value) {
  if (typeof value !== "string") return "#";
  try {
    const url = new URL(value);
    const trustedHost = [".azuredatabricks.net", ".cloud.databricks.com", ".gcp.databricks.com"]
      .some((suffix) => url.hostname.endsWith(suffix));
    const legacyJobHash = /^#job\/\d+\/run\/\d+$/.test(url.hash);
    const jobPath = /^\/(jobs|workflows)\/\d+(\/runs\/\d+)?\/?$/.test(url.pathname);
    return url.protocol === "https:" && trustedHost && (legacyJobHash || jobPath) ? url.href : "#";
  } catch (_) {
    return "#";
  }
}
function copySelect(source, target) {
  const value = target.value;
  const options = [...source.options].map((option) => {
    const copy = new Option(option.text, option.value, false, option.value === value);
    copy.disabled = option.disabled;
    return copy;
  });
  target.replaceChildren(...options);
  if (options.some((option) => option.value === value)) target.value = value;
}
function rowWithMessage(message, colspan) { const row = document.createElement("tr"); const cell = node("td", "empty-cell", message); cell.colSpan = colspan; row.append(cell); return row; }
