document.addEventListener("DOMContentLoaded", () => {
    const badgeServer = document.getElementById("badgeServer");
    const serverStatusText = document.getElementById("serverStatusText");
    const filesCount = document.getElementById("filesCount");
    const symbolsCount = document.getElementById("symbolsCount");
    const callsCount = document.getElementById("callsCount");

    const indexPathInput = document.getElementById("indexPathInput");
    const btnIndex = document.getElementById("btnIndex");
    const indexResultMsg = document.getElementById("indexResultMsg");

    const mapStatusSummary = document.getElementById("mapStatusSummary");
    const btnBuildMap = document.getElementById("btnBuildMap");
    const btnStopMap = document.getElementById("btnStopMap");
    const mapProgressBox = document.getElementById("mapProgressBox");
    const mapProgressText = document.getElementById("mapProgressText");
    const mapProgressFill = document.getElementById("mapProgressFill");
    const mapResultMsg = document.getElementById("mapResultMsg");

    const hwSummary = document.getElementById("hwSummary");
    const modelSelect = document.getElementById("modelSelect");
    const customModelPath = document.getElementById("customModelPath");
    const btnBrowseModel = document.getElementById("btnBrowseModel");
    const ctxSelect = document.getElementById("ctxSelect");
    const btnApplyModel = document.getElementById("btnApplyModel");
    const btnOpenCatalog = document.getElementById("btnOpenCatalog");
    const modelStatusMsg = document.getElementById("modelStatusMsg");

    const catalogModal = document.getElementById("catalogModal");
    const btnCloseCatalog = document.getElementById("btnCloseCatalog");
    const catalogList = document.getElementById("catalogList");
    const dlProgressBox = document.getElementById("dlProgressBox");
    const dlProgressText = document.getElementById("dlProgressText");
    const dlProgressFill = document.getElementById("dlProgressFill");

    const modeOptions = document.querySelectorAll(".mode-option");
    const questionInput = document.getElementById("questionInput");
    const btnSend = document.getElementById("btnSend");

    const welcomeBox = document.getElementById("welcomeBox");
    const answerContent = document.getElementById("answerContent");
    const citationsContainer = document.getElementById("citationsContainer");
    const citationsList = document.getElementById("citationsList");

    let currentMode = "funnel";
    let dlTimer = null;
    let mapTimer = null;

    modeOptions.forEach(opt => {
        opt.addEventListener("click", () => {
            modeOptions.forEach(o => o.classList.remove("active"));
            opt.classList.add("active");
            const radio = opt.querySelector("input[type='radio']");
            radio.checked = true;
            currentMode = radio.value;
        });
    });

    async function checkHealth() {
        try {
            const resp = await fetch("/api/health");
            if (resp.ok) {
                const data = await resp.json();
                const dot = badgeServer.querySelector(".dot");
                dot.className = "dot " + (data.qwen_online ? "green" : "yellow");
                serverStatusText.textContent = data.qwen_online
                    ? "Сервер в сети (LLM Ready)"
                    : "Бэкенд в сети (llama-server офлайн)";
                filesCount.textContent = data.files_indexed;
                symbolsCount.textContent = data.symbols_indexed;
                callsCount.textContent = data.calls_indexed;
            }
        } catch {
            const dot = badgeServer.querySelector(".dot");
            dot.className = "dot red";
            serverStatusText.textContent = "Сервер недоступен";
        }
    }

    async function loadHardwareAndModels() {
        try {
            const [hwResp, modResp] = await Promise.all([
                fetch("/api/system/hardware"),
                fetch("/api/models")
            ]);
            if (hwResp.ok) {
                const hw = await hwResp.json();
                const gpuLabel = (hw.gpus && hw.gpus.length > 0) ? hw.gpus[0].name : "CPU Only";
                hwSummary.textContent = `RAM: ${hw.ram_gb} GB | GPU: ${gpuLabel} | Рек.: ${hw.recommended_backend.toUpperCase()} (${hw.recommended_ctx})`;
            }
            if (modResp.ok) {
                const modData = await modResp.json();
                modelSelect.innerHTML = "";
                const locals = modData.local_models || [];
                if (locals.length === 0) {
                    const opt = document.createElement("option");
                    opt.value = "";
                    opt.textContent = "Нет скачанных моделей (.gguf)";
                    modelSelect.appendChild(opt);
                } else {
                    locals.forEach(m => {
                        const opt = document.createElement("option");
                        opt.value = m.path;
                        opt.textContent = `${m.name} (${m.size_gb} GB)`;
                        if (m.active) opt.selected = true;
                        modelSelect.appendChild(opt);
                    });
                }
                if (modData.context_size) {
                    ctxSelect.value = String(modData.context_size);
                }
                renderCatalog(modData.catalog || []);
            }
        } catch {
            hwSummary.textContent = "Не удалось получить данные о железе.";
        }
    }

    function renderCatalog(catalog) {
        catalogList.innerHTML = "";
        catalog.forEach(m => {
            const compat = m.compatibility || { status: "tight", badge: "yellow", note: "" };
            const badgeClass = compat.badge === "green" ? "compat-green" : (compat.badge === "red" ? "compat-red" : "compat-yellow");
            const badgeText = compat.badge === "green" ? "Оптимально" : (compat.badge === "red" ? "Тяжелая" : "С оговорками");
            const card = document.createElement("div");
            card.className = "catalog-card";

            const info = document.createElement("div");
            info.style.flex = "1";
            info.innerHTML = `
                <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
                    <strong>${m.name}</strong>
                    <span class="compat-badge ${badgeClass}">${badgeText}</span>
                </div>
                <div style="font-size:12px; color:var(--text-secondary);">
                    ${m.size_gb} GB | Мин. RAM: ${m.min_ram_gb} GB | VRAM: ${m.rec_vram_gb} GB<br>
                    ${m.use_case_ru || m.use_case_en}
                </div>
            `;

            const actBtn = document.createElement("button");
            actBtn.className = m.installed ? "btn btn-secondary" : "btn btn-primary";
            actBtn.textContent = m.installed ? "Выбрать" : "Скачать";
            actBtn.addEventListener("click", async () => {
                if (m.installed && m.local_path) {
                    catalogModal.classList.add("hidden");
                    await applyModel(m.local_path, m.rec_ctx);
                } else {
                    await startCatalogDownload(m.id);
                }
            });

            card.appendChild(info);
            card.appendChild(actBtn);
            catalogList.appendChild(card);
        });
    }

    async function applyModel(pathVal, ctxVal) {
        if (!pathVal) return;
        btnApplyModel.disabled = true;
        modelStatusMsg.className = "result-msg";
        modelStatusMsg.textContent = "⏳ Перезапуск llama-server с новой моделью...";
        try {
            const resp = await fetch("/api/models/select", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    model_path: pathVal,
                    context_size: parseInt(ctxVal || ctxSelect.value, 10),
                    restart_server: true
                })
            });
            const data = await resp.json();
            modelStatusMsg.className = "result-msg " + (data.success ? "success" : "error");
            modelStatusMsg.textContent = (data.success ? "✅ " : "❌ ") + data.message;
            await loadHardwareAndModels();
            await checkHealth();
        } catch (err) {
            modelStatusMsg.className = "result-msg error";
            modelStatusMsg.textContent = `❌ ${err.message}`;
        } finally {
            btnApplyModel.disabled = false;
        }
    }

    async function startCatalogDownload(modelId) {
        dlProgressBox.classList.remove("hidden");
        dlProgressFill.style.width = "2%";
        dlProgressText.textContent = "Инициализация загрузки...";
        const resp = await fetch("/api/models/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model_id: modelId })
        });
        const data = await resp.json();
        if (!data.success) {
            dlProgressText.textContent = `❌ ${data.message}`;
            return;
        }
        if (dlTimer) clearInterval(dlTimer);
        dlTimer = setInterval(async () => {
            const stResp = await fetch("/api/models/download/status");
            const st = await stResp.json();
            dlProgressFill.style.width = `${st.percent}%`;
            dlProgressText.textContent = `${st.filename}: ${st.downloaded_mb} / ${st.total_mb} MB (${st.percent}%) [${st.status}]`;
            if (!st.active) {
                clearInterval(dlTimer);
                dlTimer = null;
                await loadHardwareAndModels();
            }
        }, 900);
    }

    btnBrowseModel.addEventListener("click", async () => {
        const resp = await fetch("/api/models/browse", { method: "POST" });
        const data = await resp.json();
        if (data.selected && data.path) {
            customModelPath.value = data.path;
        }
    });

    btnApplyModel.addEventListener("click", async () => {
        const chosen = customModelPath.value.trim() || modelSelect.value;
        await applyModel(chosen, ctxSelect.value);
    });

    btnOpenCatalog.addEventListener("click", async () => {
        await loadHardwareAndModels();
        catalogModal.classList.remove("hidden");
    });
    btnCloseCatalog.addEventListener("click", () => catalogModal.classList.add("hidden"));

    async function refreshMapStatus() {
        try {
            const resp = await fetch("/api/map/status");
            if (!resp.ok) return;
            const st = await resp.json();
            const isRunning = st.state === "running";
            mapStatusSummary.textContent = `Покрытие: ${st.mapped_files} / ${st.total_files} файлов (${st.coverage_percent}%) | Строк карты: ${st.summary_entries} (~${st.approx_tokens} ток.)`;

            if (isRunning) {
                btnBuildMap.disabled = true;
                btnBuildMap.textContent = "⏳ Сборка карты...";
                btnStopMap.classList.remove("hidden");
                mapProgressBox.classList.remove("hidden");
                mapProgressFill.style.width = `${Math.max(2, st.percent || 0)}%`;
                const etaStr = st.eta_seconds > 0 ? ` | ETA ~${Math.round(st.eta_seconds)}с` : "";
                mapProgressText.textContent = `${st.processed}/${st.total_to_process} (${st.percent}%)${etaStr} — ${st.current_file || "..."}`;
                if (!mapTimer) {
                    mapTimer = setInterval(refreshMapStatus, 1200);
                }
            } else {
                btnBuildMap.disabled = false;
                btnBuildMap.textContent = st.mapped_files > 0 ? "🔄 Обновить карту (кэш)" : "🗺️ Собрать карту (1 раз)";
                btnStopMap.classList.add("hidden");
                if (mapTimer) {
                    clearInterval(mapTimer);
                    mapTimer = null;
                }
                if (st.state === "completed") {
                    mapProgressBox.classList.add("hidden");
                    mapResultMsg.className = "result-msg success";
                    mapResultMsg.textContent = `✅ Карта готова (${st.mapped_files}/${st.total_files} файл., ${st.duration_seconds}с)`;
                } else if (st.state === "cancelled") {
                    mapProgressBox.classList.add("hidden");
                    mapResultMsg.className = "result-msg";
                    mapResultMsg.textContent = `⏹ Остановлено. Сохранено ${st.mapped_files}/${st.total_files} файл. (можно продолжить).`;
                } else if (st.state === "failed") {
                    mapProgressBox.classList.add("hidden");
                    mapResultMsg.className = "result-msg error";
                    mapResultMsg.textContent = `❌ Ошибка карты: ${st.error || "сбой"}`;
                }
            }
        } catch {
            if (mapStatusSummary) mapStatusSummary.textContent = "Статус карты недоступен";
        }
    }

    btnBuildMap.addEventListener("click", async () => {
        btnBuildMap.disabled = true;
        mapResultMsg.className = "result-msg";
        mapResultMsg.textContent = "⏳ Запуск инкрементальной сборки карты...";
        try {
            const resp = await fetch("/api/map/build", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ force_rebuild: false })
            });
            const data = await resp.json();
            if (!data.started) {
                mapResultMsg.textContent = data.message || "Сборка уже идёт.";
            }
            await refreshMapStatus();
            if (!mapTimer) {
                mapTimer = setInterval(refreshMapStatus, 1200);
            }
        } catch (err) {
            btnBuildMap.disabled = false;
            mapResultMsg.className = "result-msg error";
            mapResultMsg.textContent = `❌ ${err.message}`;
        }
    });

    btnStopMap.addEventListener("click", async () => {
        try {
            await fetch("/api/map/cancel", { method: "POST" });
            mapResultMsg.textContent = "⏹ Остановка после текущего батча...";
        } catch {
            // ignore
        }
    });

    checkHealth();
    loadHardwareAndModels();
    refreshMapStatus();
    setInterval(checkHealth, 5000);

    btnIndex.addEventListener("click", async () => {
        const path = indexPathInput.value.trim();
        if (!path) {
            alert("Пожалуйста, укажите путь к каталогу проекта!");
            return;
        }

        btnIndex.disabled = true;
        btnIndex.textContent = "⏳ Индексация...";
        indexResultMsg.className = "result-msg";
        indexResultMsg.textContent = "Сканирование файлов и построение Call-Graph...";

        try {
            const resp = await fetch("/api/index", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path })
            });

            const data = await resp.json();
            if (data.success) {
                indexResultMsg.className = "result-msg success";
                indexResultMsg.innerHTML = `✅ Успешно! Файлов: ${data.files_count}, Символов: ${data.symbols_count}, Связей: ${data.calls_count} (${data.duration_seconds} сек.)`;
                checkHealth();
                refreshMapStatus();
            } else {
                indexResultMsg.className = "result-msg error";
                indexResultMsg.textContent = `❌ Ошибка: ${data.message}`;
            }
        } catch (err) {
            indexResultMsg.className = "result-msg error";
            indexResultMsg.textContent = `❌ Ошибка сети: ${err.message}`;
        } finally {
            btnIndex.disabled = false;
            btnIndex.textContent = "🔄 Индексировать";
        }
    });

    async function sendQuery() {
        const question = questionInput.value.trim();
        if (!question) return;

        btnSend.disabled = true;
        btnSend.textContent = "Поиск...";
        welcomeBox.classList.add("hidden");
        answerContent.classList.remove("hidden");
        if (currentMode === "funnel") {
            answerContent.textContent = "⏳ Smart Funnel (~1–2 мин): 100% обзор карты -> AST-скелет -> сырой код + Call-Graph Rescue...";
        } else if (currentMode === "reduce") {
            answerContent.textContent = "⏳ Reducing AI (бюджет до 5 мин): анализ карты/заголовков, .cpp и Rescue...";
        } else if (currentMode === "reduce_deep") {
            answerContent.textContent = "⏳ Deep Reducing (бюджет до 10 мин): глубокое полное сканирование проекта...";
        } else {
            answerContent.textContent = "⏳ Анализ кода и генерация ответа...";
        }
        citationsContainer.classList.add("hidden");
        citationsList.innerHTML = "";

        try {
            const resp = await fetch("/api/query", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    question,
                    mode: currentMode,
                    language: document.getElementById("langSelect") ? document.getElementById("langSelect").value : "ru"
                })
            });

            const data = await resp.json();
            if (data.success) {
                answerContent.textContent = data.answer;

                if (data.citations && data.citations.length > 0) {
                    citationsContainer.classList.remove("hidden");
                    data.citations.forEach(c => {
                        const li = document.createElement("li");
                        const fileName = c.file ? c.file.split(/[\\/]/).pop() : "";
                        li.textContent = `[${fileName}:${c.line}] ${c.symbol ? c.symbol + ' - ' : ''}${c.snippet || ''}`;
                        citationsList.appendChild(li);
                    });
                }
            } else {
                answerContent.textContent = `❌ Ошибка: ${data.error || "Неизвестная ошибка"}`;
            }
        } catch (err) {
            answerContent.textContent = `❌ Ошибка сети: ${err.message}`;
        } finally {
            btnSend.disabled = false;
            btnSend.textContent = "Отправить";
        }
    }

    btnSend.addEventListener("click", sendQuery);
    questionInput.addEventListener("keydown", (e) => {
        if (e.ctrlKey && e.key === "Enter") {
            e.preventDefault();
            sendQuery();
        }
    });
});
