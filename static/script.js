let mode = "upload";

const $ = (id) => document.getElementById(id);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function setStatus(message) {
    const el = $("status");
    if (el) el.textContent = message || "";
}

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function showError(message) {
    const el = $("errorState");
    if (!el) return;
    el.classList.remove("hidden");
    el.textContent = message || "Something went wrong.";
}

function clearError() {
    const el = $("errorState");
    if (el) {
        el.classList.add("hidden");
        el.textContent = "";
    }
}

/* SESSION */
async function loadSession() {
    try {
        const response = await fetch("/auth/me", {
            credentials: "include",
            cache: "no-store"
        });

        if (!response.ok) {
            window.location.href = "/login";
            return;
        }

        const user = await response.json();

        if ($("userName")) {
            $("userName").textContent = user.username || "User";
        }
    } catch (error) {
        console.error("SESSION ERROR:", error);
        window.location.href = "/login";
    }
}

/* TABS */
function setupTabs() {
    document.querySelectorAll(".tab").forEach((button) => {
        button.addEventListener("click", () => {
            document.querySelectorAll(".tab").forEach((item) => {
                item.classList.remove("active");
            });

            button.classList.add("active");
            mode = button.dataset.mode || "upload";

            $("uploadPanel")?.classList.toggle(
                "hidden",
                mode !== "upload"
            );

            $("urlPanel")?.classList.toggle(
                "hidden",
                mode !== "url"
            );

            setStatus("");
            clearError();
        });
    });
}

/* FILE */
function setupFileInput() {
    const input = $("videoFile");
    if (!input) return;

    input.addEventListener("change", () => {
        const file = input.files?.[0];

        if ($("fileName")) {
            $("fileName").textContent = file
                ? `${file.name} · ${(file.size / 1048576).toFixed(1)} MB`
                : "";
        }

        if (file && $("videoUrl")) {
            $("videoUrl").value = "";
        }
    });
}

/* URL VALIDATION */
function setupUrlValidation() {
    const input = $("videoUrl");
    if (!input) return;

    input.addEventListener("blur", async () => {
        const url = input.value.trim();
        if (!url) {
            if ($("urlStatus")) $("urlStatus").textContent = "";
            return;
        }

        if ($("urlStatus")) $("urlStatus").textContent = "Checking…";

        try {
            const response = await fetch("/video/url/validate", {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url })
            });

            const text = await response.text();
            let data = null;

            try {
                data = text ? JSON.parse(text) : null;
            } catch {
                if ($("urlStatus")) {
                    $("urlStatus").textContent =
                        "Server returned an invalid response.";
                }
                return;
            }

            if (!response.ok) {
                if ($("urlStatus")) {
                    $("urlStatus").textContent =
                        data?.detail ||
                        data?.message ||
                        "URL validation failed.";
                }
                return;
            }

            if ($("urlStatus")) {
                $("urlStatus").textContent = data?.supported
                    ? `✓ ${data.source || "Video"} detected`
                    : "Unsupported URL";
            }
        } catch (error) {
            console.error("URL VALIDATION ERROR:", error);
            if ($("urlStatus")) {
                $("urlStatus").textContent =
                    "Unable to validate this URL.";
            }
        }
    });
}

/* LOGOUT */
function setupLogout() {
    const button = $("logoutBtn");
    if (!button) return;

    button.addEventListener("click", async () => {
        try {
            await fetch("/auth/logout", {
                method: "POST",
                credentials: "include"
            });
        } finally {
            window.location.href = "/login";
        }
    });
}

/* PROGRESS */
function showProgress(progress, message) {
    $("idleState")?.classList.add("hidden");
    $("resultState")?.classList.add("hidden");
    $("progressState")?.classList.remove("hidden");

    const value = Math.max(
        0,
        Math.min(100, Number(progress) || 0)
    );

    if ($("progressBar")) $("progressBar").style.width = `${value}%`;
    if ($("progressTitle")) $("progressTitle").textContent = message || "Processing...";
    if ($("progressPercent")) $("progressPercent").textContent = `${value}%`;

    const steps = document.querySelectorAll("#steps span");
    let active = 0;
    if (value >= 85) active = 5;
    else if (value >= 70) active = 4;
    else if (value >= 55) active = 3;
    else if (value >= 35) active = 2;
    else if (value >= 15) active = 1;

    steps.forEach((step, index) => {
        step.classList.toggle("active", index <= active);
    });
}

/* ONE CLICK */
async function processOneClick() {
    const file = $("videoFile")?.files?.[0] || null;
    const url = $("videoUrl")?.value?.trim() || "";
    const prompt = $("prompt")?.value?.trim() || "Find the best moments";
    const compression = $("compression")?.value || "medium";
    const button = $("processBtn");

    // Respect the selected tab. Do not accidentally send both inputs.
    if (mode === "upload" && !file) {
        setStatus("Please select a video file.");
        return;
    }

    if (mode === "url" && !url) {
        setStatus("Please enter a video URL.");
        return;
    }

    clearError();

    const formData = new FormData();

    if (mode === "upload") {
        formData.append("file", file);
    } else {
        formData.append("url", url);
    }

    formData.append("prompt", prompt);
    formData.append("compression", compression);

    if (button) {
        button.disabled = true;
        button.textContent = "Processing...";
    }

    showProgress(0, "Starting processing...");
    setStatus("Uploading / preparing video...");

    try {
        const response = await fetch("/process/one-click", {
            method: "POST",
            credentials: "include",
            body: formData
        });

        const text = await response.text();
        let data = null;

        try {
            data = text ? JSON.parse(text) : null;
        } catch {
            console.error("ONE-CLICK NON-JSON:", text);
            throw new Error(
                text
                    ? `Server returned a non-JSON response: ${text.slice(0, 300)}`
                    : "Server returned an empty response."
            );
        }

        if (!response.ok) {
            throw new Error(
                data?.detail ||
                data?.error ||
                data?.message ||
                `Request failed with HTTP ${response.status}.`
            );
        }

        if (!data?.job_id) {
            throw new Error("Server did not return a job ID.");
        }

        setStatus("Processing started...");
        await pollProcessingJob(data.job_id);
    } catch (error) {
        console.error("ONE-CLICK ERROR:", error);
        const message = error?.message || "Unknown processing error.";
        setStatus(`Processing failed: ${message}`);
        showError(message);
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = "Process Video";
        }
    }
}

/* JOB POLLING */
async function pollProcessingJob(jobId) {
    const maxAttempts = 180;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
        const response = await fetch(
            `/process/status/${encodeURIComponent(jobId)}`,
            {
                method: "GET",
                credentials: "include",
                cache: "no-store"
            }
        );

        const text = await response.text();
        let data = null;

        try {
            data = text ? JSON.parse(text) : null;
        } catch {
            console.error("STATUS NON-JSON:", text);
            throw new Error(
                text
                    ? `Invalid status response: ${text.slice(0, 300)}`
                    : "Empty status response."
            );
        }

        if (!response.ok) {
            throw new Error(
                data?.detail ||
                data?.error ||
                data?.message ||
                `Status request failed with HTTP ${response.status}.`
            );
        }

        const progress = Number(data?.progress || 0);
        const message = data?.message || "Processing...";

        showProgress(progress, message);
        setStatus(`${message} ${progress}%`);

        if (data?.status === "completed") {
            showProcessingResult(data?.result || {});
            setStatus("✓ Processing completed.");
            return;
        }

        if (data?.status === "failed") {
            throw new Error(data?.error || "Processing failed.");
        }

        await sleep(2000);
    }

    throw new Error("Processing timed out. Please try again.");
}

/* RESULTS */
function showProcessingResult(result) {
    $("progressState")?.classList.add("hidden");
    $("idleState")?.classList.add("hidden");
    $("resultState")?.classList.remove("hidden");

    if ($("videoPreview") && result?.video_url) {
        $("videoPreview").src = result.video_url;
        $("videoPreview").load();
    }

    const analysis = result?.analysis || {};

    if ($("durationMetric")) {
        $("durationMetric").textContent =
            analysis.duration_seconds != null
                ? `${analysis.duration_seconds}s`
                : "—";
    }

    if ($("resolutionMetric")) {
        $("resolutionMetric").textContent =
            analysis.width && analysis.height
                ? `${analysis.width} × ${analysis.height}`
                : "—";
    }

    if ($("languageMetric")) {
        $("languageMetric").textContent =
            String(result?.language || "unknown").toUpperCase();
    }

    const highlights = Array.isArray(result?.highlights)
        ? result.highlights
        : [];

    if ($("highlightMetric")) {
        $("highlightMetric").textContent = highlights.length;
    }

    if ($("summaryText")) {
        $("summaryText").textContent =
            result?.summary || "No summary.";
    }

    renderHighlights(highlights);
    renderClips(
        Array.isArray(result?.clips) ? result.clips : []
    );

    if ($("transcriptText")) {
        $("transcriptText").textContent =
            result?.transcript || "No speech detected.";
    }

    // Keep a compact result card below the source controls too.
    const resultsBox = $("results");

    if (resultsBox) {
        const clips = Array.isArray(result?.clips)
            ? result.clips
            : [];

        resultsBox.innerHTML = `
            <div class="result-card">
                <h2>SmartClip Ready ✓</h2>
                <p><strong>Video:</strong> ${escapeHtml(
                    result?.title ||
                    result?.filename ||
                    "Processed video"
                )}</p>
                <p><strong>Language:</strong> ${escapeHtml(
                    result?.language || "unknown"
                )}</p>
                <h3>Summary</h3>
                <p>${escapeHtml(
                    result?.summary || "No summary."
                )}</p>
                <h3>Highlights</h3>
                ${
                    highlights.length
                        ? highlights.map((h) => `
                            <div class="highlight">
                                <strong>Score: ${Number(h?.score || 0)}</strong>
                                <p>${escapeHtml(h?.text || "")}</p>
                            </div>
                        `).join("")
                        : "<p>No highlights found.</p>"
                }
                <h3>Generated Clips</h3>
                ${
                    clips.length
                        ? clips.map((clip) => `
                            <div class="clip">
                                <h4>Clip ${Number(clip?.clip_number || 0)}</h4>
                                <p>${escapeHtml(clip?.text || "")}</p>
                                ${
                                    clip?.url
                                        ? `
                                            <video controls preload="metadata"
                                                src="${escapeHtml(clip.url)}"></video>
                                            <br>
                                            <a href="${escapeHtml(clip.url)}" download>
                                                Download Clip
                                            </a>
                                        `
                                        : ""
                                }
                            </div>
                        `).join("")
                        : "<p>No clips generated.</p>"
                }
                <h3>Transcript</h3>
                <div class="transcript">${escapeHtml(
                    result?.transcript || "No speech detected."
                )}</div>
            </div>
        `;
    }
}

function renderHighlights(highlights) {
    const container = $("highlightsList");
    if (!container) return;

    if (!highlights.length) {
        container.innerHTML = "<p>No highlights.</p>";
        return;
    }

    container.innerHTML = highlights.map((highlight, index) => {
        const start = Number(highlight?.start ?? 0);
        const end = Number(highlight?.end ?? 0);
        const score = Number(highlight?.score ?? 0);

        return `
            <div class="item">
                <div class="rank">${index + 1}</div>
                <div class="copy">
                    <p>${escapeHtml(highlight?.text || "")}</p>
                    <small>
                        ${start.toFixed(1)}s → ${end.toFixed(1)}s
                        · ${escapeHtml(highlight?.reason || "")}
                    </small>
                </div>
                <div class="score">${score}</div>
            </div>
        `;
    }).join("");
}

function renderClips(clips) {
    const container = $("clipsList");
    if (!container) return;

    if (!clips.length) {
        container.innerHTML = "<p>No clips generated.</p>";
        return;
    }

    container.innerHTML = clips.map((clip) => `
        <div class="item">
            <div class="rank">▶</div>
            <div class="copy">
                <p>Clip ${Number(clip?.clip_number || 0)}</p>
                <small>
                    ${Number(clip?.duration || 0).toFixed(2)}s
                    · score ${Number(clip?.score || 0)}
                </small>
            </div>
            ${
                clip?.url
                    ? `<a class="clip"
                         href="${escapeHtml(clip.url)}"
                         target="_blank"
                         rel="noopener">Open MP4</a>`
                    : ""
            }
        </div>
    `).join("");
}

/* INIT */
document.addEventListener("DOMContentLoaded", () => {
    loadSession();
    setupTabs();
    setupFileInput();
    setupUrlValidation();
    setupLogout();

    // input.html does not need inline onclick.
    $("processBtn")?.addEventListener("click", processOneClick);
});
