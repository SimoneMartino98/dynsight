/* dynsight label tool - frontend */

"use strict";

/* ---------- constants ---------- */

const PALETTE = [
    "#f43f5e", "#f97316", "#eab308", "#22c55e", "#06b6d4",
    "#3b82f6", "#8b5cf6", "#ec4899", "#14b8a6", "#a3e635",
];
const MIN_BOX_SIZE = 3; // px, in image space
const HANDLE_SIZE = 7; // px, in screen space
const HANDLE_HIT = 6; // px tolerance
const MIN_SCALE = 0.05;
const MAX_SCALE = 32;

/* ---------- state ---------- */

const state = {
    workspace: "",
    images: [], // [{name, width, height}]
    annotations: {}, // name -> [{label, x, y, w, h}]
    frames: {}, // name -> {source, frame_index, reviewed, split}
    regions: {}, // name -> reviewed rectangles within a frame
    review_queue: {}, // region suggestions and decisions
    comparisons: [], // reports linked to this session
    labels: [], // [{name, color}]
    activeLabel: null,
    current: -1,
    selection: -1,
    view: { scale: 1, x: 0, y: 0 },
    fitted: true, // refit on container resize until the user zooms/pans
};

let drag = null; // {mode, ...} while a pointer drag is active
let hover = { box: -1, handle: -1 };
let pointer = { x: 0, y: 0, inside: false };
let spaceDown = false;
let syncTimer = null;
let dirty = false; // changes not yet saved to a session file
let sessionPath = null; // last file the session was saved to / loaded from
let quitAfterSave = false;
let comparisonOverlay = null;
let selectedRegionId = null;

const imageCache = new Map(); // name -> HTMLImageElement
const imageVersion = new Map(); // name -> int, bumped on re-upload

function imageUrl(name) {
    const version = imageVersion.get(name);
    const suffix = version ? `?v=${version}` : "";
    return `/images/${encodeURIComponent(name)}${suffix}`;
}

/* ---------- dom ---------- */

const $ = (id) => document.getElementById(id);
const canvas = $("canvas");
const ctx = canvas.getContext("2d");
const stage = $("stage");

/* ---------- helpers ---------- */

function currentImage() {
    return state.images[state.current] || null;
}

function currentBoxes() {
    const img = currentImage();
    if (!img) return [];
    if (!state.annotations[img.name]) state.annotations[img.name] = [];
    return state.annotations[img.name];
}

for (const [id, field] of [["uncertainCheck", "uncertain"], ["borderCheck", "border_truncated"]]) {
    $(id).onchange = (event) => {
        const box = currentBoxes()[state.selection];
        if (!box) return;
        box[field] = event.target.checked;
        annotationsChanged();
    };
}

function labelColor(name) {
    const label = state.labels.find((l) => l.name === name);
    return label ? label.color : "#9ca3af";
}

function clamp(value, lo, hi) {
    return Math.max(lo, Math.min(hi, value));
}

function screenToImage(sx, sy) {
    return {
        x: (sx - state.view.x) / state.view.scale,
        y: (sy - state.view.y) / state.view.scale,
    };
}

/* ---------- api ---------- */

async function api(path, options = {}) {
    const response = await fetch(path, options);
    let payload = {};
    try {
        payload = await response.json();
    } catch {
        /* non-json error */
    }
    if (!response.ok) {
        throw new Error(payload.error || `Request failed (${response.status})`);
    }
    return payload;
}

function setSaveStatus() {
    const el = $("saveStatus");
    if (dirty) {
        el.textContent = "● Unsaved session";
        el.className = "busy";
    } else if (sessionPath) {
        el.textContent = `Saved ✓ (${sessionPath})`;
        el.className = "ok";
    } else {
        el.textContent = "";
        el.className = "";
    }
    const reviewed = Object.values(state.frames).filter((frame) => frame.reviewed).length;
    const test = Object.values(state.frames).filter((frame) => frame.split === "test").length;
    const regions = Object.values(state.regions).flat().filter((region) => region.reviewed).length;
    $("sessionSummary").textContent = `${state.labels.length} classes · ${reviewed} reviewed · ${test} test · ${regions} verified tiles`;
}

// The session is never written to disk automatically: edits are only
// mirrored to the server's memory so a page reload does not lose work
// while the server is running. Disk writes happen exclusively through
// the "Save session" dialog, to a user-chosen path.
function markChanged() {
    dirty = true;
    setSaveStatus();
    clearTimeout(syncTimer);
    syncTimer = setTimeout(syncSession, 300);
}

function annotationsChanged() {
    const image = currentImage();
    if (image) {
        for (const region of state.regions[image.name] || []) {
            region.reviewed = false;
        }
    }
    markChanged();
    renderRegions();
    renderImages();
}

async function syncSession() {
    clearTimeout(syncTimer);
    try {
        await api("/api/sync", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: sessionBody(),
        });
    } catch {
        /* retried on the next change */
    }
}

function sessionBody() {
    return JSON.stringify({
        labels: state.labels,
        annotations: state.annotations,
        frames: state.frames,
        regions: state.regions,
        review_queue: state.review_queue,
        comparisons: state.comparisons,
    });
}

// Mirror unsaved work to the server's memory when the page is closed
// or reloaded (no disk write). The server keeps running: it is stopped
// only via the Quit button or Ctrl+C.
window.addEventListener("pagehide", () => {
    navigator.sendBeacon(
        "/api/sync",
        new Blob([sessionBody()], { type: "application/json" }),
    );
});

// Warn before leaving the page with an unsaved session.
window.addEventListener("beforeunload", (e) => {
    if (dirty) e.preventDefault();
});

/* ---------- toasts ---------- */

function toast(message, cls = "", detail = "", timeout = 6000) {
    const el = document.createElement("div");
    el.className = `toast ${cls}`;
    el.textContent = message;
    if (detail) {
        const line = document.createElement("span");
        line.className = "mono";
        line.textContent = detail;
        el.appendChild(line);
    }
    el.onclick = () => el.remove();
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), timeout);
}

/* ---------- progress ---------- */

let progressPoll = null;

function showProgress(label) {
    $("progressLabel").textContent = label;
    $("progressPct").textContent = "";
    $("progressFill").classList.add("indeterminate");
    $("progress").classList.remove("hidden");
}

function setProgress(done, total) {
    const fill = $("progressFill");
    if (total > 0) {
        const pct = Math.min(100, Math.round((done / total) * 100));
        fill.classList.remove("indeterminate");
        fill.style.width = `${pct}%`;
        $("progressPct").textContent = `${pct}%`;
    } else {
        fill.classList.add("indeterminate");
        $("progressPct").textContent = done > 0 ? String(done) : "";
    }
}

function hideProgress() {
    stopProgressPoll();
    $("progress").classList.add("hidden");
    $("progressFill").style.width = "0%";
    $("progressFill").classList.remove("indeterminate");
}

// Long server-side operations (export, synthesize, frame extraction)
// report their progress through /api/progress, polled while the main
// request is in flight.
function startProgressPoll(fallbackLabel) {
    stopProgressPoll();
    progressPoll = setInterval(async () => {
        try {
            const p = await api("/api/progress");
            if (p.active) {
                $("progressLabel").textContent =
                    (p.label || fallbackLabel) + "…";
                setProgress(p.done || 0, p.total || 0);
            }
        } catch {
            /* server busy or gone; keep the bar as-is */
        }
    }, 250);
}

function stopProgressPoll() {
    if (progressPoll) {
        clearInterval(progressPoll);
        progressPoll = null;
    }
}

/* ---------- sidebar: labels ---------- */

function renderLabels() {
    const list = $("labelList");
    list.innerHTML = "";
    state.labels.forEach((label, idx) => {
        const li = document.createElement("li");
        if (label.name === state.activeLabel) li.classList.add("active");

        const id = document.createElement("span");
        id.className = "class-id";
        id.textContent = String(idx);

        const dot = document.createElement("span");
        dot.className = "color-dot";
        dot.style.backgroundColor = label.color;

        const name = document.createElement("span");
        name.className = "item-name";
        name.textContent = label.name;

        const count = document.createElement("span");
        count.className = "item-badge";
        count.textContent = String(countBoxes(label.name));

        const del = document.createElement("button");
        del.className = "del-btn";
        del.textContent = "×";
        del.title = "Delete label and its boxes";
        del.onclick = (e) => {
            e.stopPropagation();
            deleteLabel(label.name);
        };

        li.append(id, dot, name, count, del);
        li.onclick = () => {
            state.activeLabel = label.name;
            const boxes = currentBoxes();
            if (state.selection >= 0 && boxes[state.selection]) {
                boxes[state.selection].label = label.name;
                markChanged();
            }
            renderLabels();
            render();
        };
        list.appendChild(li);
    });
    $("labelHint").classList.toggle("hidden", state.labels.length > 0);
}

function countBoxes(labelName) {
    let total = 0;
    for (const boxes of Object.values(state.annotations)) {
        total += boxes.filter((b) => b.label === labelName).length;
    }
    return total;
}

function addLabel(name) {
    if (!name || state.labels.some((l) => l.name === name)) return;
    const color = PALETTE[state.labels.length % PALETTE.length];
    state.labels.push({ name, color });
    state.activeLabel = name;
    markChanged();
    renderLabels();
    render();
}

function deleteLabel(name) {
    const used = countBoxes(name);
    if (
        used > 0 &&
        !confirm(`Delete label "${name}" and its ${used} box(es)?`)
    ) {
        return;
    }
    state.labels = state.labels.filter((l) => l.name !== name);
    for (const key of Object.keys(state.annotations)) {
        state.annotations[key] = state.annotations[key].filter(
            (b) => b.label !== name,
        );
    }
    if (state.activeLabel === name) state.activeLabel = null;
    state.selection = -1;
    markChanged();
    renderLabels();
    renderImages();
    render();
}

$("labelForm").onsubmit = (e) => {
    e.preventDefault();
    addLabel($("labelInput").value.trim());
    $("labelInput").value = "";
};

/* ---------- sidebar: images ---------- */

function renderImages() {
    const list = $("imageList");
    list.innerHTML = "";
    const filter = $("imageFilter").value;
    state.images.forEach((info, idx) => {
        const frame = state.frames[info.name] || {};
        if (filter === "unreviewed" && frame.reviewed) return;
        if (filter === "reviewed" && !frame.reviewed) return;
        if (filter === "test" && frame.split !== "test") return;
        const li = document.createElement("li");
        if (idx === state.current) li.classList.add("active");

        const thumb = document.createElement("img");
        thumb.className = "thumb";
        thumb.loading = "lazy";
        thumb.src = imageUrl(info.name);

        const name = document.createElement("span");
        name.className = "item-name";
        name.textContent = info.name;
        name.title = info.name;

        const count = document.createElement("span");
        count.className = "item-badge";
        const n = (state.annotations[info.name] || []).length;
        const meta = state.frames[info.name] || {};
        count.textContent = `${meta.reviewed ? "✓" : "○"}${meta.split === "test" ? " T" : ""}${n ? ` ${n}` : ""}`;

        const del = document.createElement("button");
        del.className = "del-btn";
        del.textContent = "×";
        del.title = "Remove image";
        del.onclick = (e) => {
            e.stopPropagation();
            deleteImage(info.name);
        };

        li.append(thumb, name, count, del);
        li.onclick = () => selectImage(idx);
        list.appendChild(li);
    });
    $("imageCounter").textContent = state.images.length
        ? `${state.current + 1} / ${state.images.length}`
        : "0 / 0";
    $("emptyState").classList.toggle("hidden", state.images.length > 0);
    const image = currentImage();
    const meta = image ? (state.frames[image.name] || {}) : {};
    $("reviewedCheck").checked = Boolean(meta.reviewed);
    $("reviewedCheck").disabled = !image;
    $("testCheck").checked = meta.split === "test";
    $("testCheck").disabled = !image || !meta.reviewed;
}

$("imageFilter").onchange = renderImages;

$("reviewedCheck").onchange = (e) => {
    const image = currentImage();
    if (!image) return;
    const meta = state.frames[image.name] || {};
    if (e.target.checked && (state.annotations[image.name] || []).length > 20 &&
        !confirm("Have you checked every box in this frame? Use verified tiles for partial review.")) {
        e.target.checked = false;
        return;
    }
    meta.reviewed = e.target.checked;
    if (!meta.reviewed) meta.split = "train";
    state.frames[image.name] = meta;
    markChanged();
    renderImages();
};

$("testCheck").onchange = (e) => {
    const image = currentImage();
    if (!image) return;
    const meta = state.frames[image.name] || {};
    meta.split = e.target.checked ? "test" : "train";
    state.frames[image.name] = meta;
    markChanged();
    renderImages();
};

async function deleteImage(name) {
    if (!confirm(`Remove "${name}" and its annotations?`)) return;
    try {
        await api(`/api/images?name=${encodeURIComponent(name)}`, {
            method: "DELETE",
        });
    } catch (err) {
        toast(`Could not delete: ${err.message}`, "error");
        return;
    }
    const idx = state.images.findIndex((i) => i.name === name);
    state.images = state.images.filter((i) => i.name !== name);
    delete state.annotations[name];
    delete state.frames[name];
    for (const region of state.regions[name] || []) delete state.review_queue[region.id];
    delete state.regions[name];
    imageCache.delete(name);
    if (state.current >= state.images.length) {
        state.current = state.images.length - 1;
    } else if (idx <= state.current) {
        state.current = Math.max(0, state.current - (idx < state.current));
    }
    state.selection = -1;
    markChanged();
    selectImage(state.current, true);
    renderLabels();
}

function selectImage(idx, force = false) {
    if (idx === state.current && !force) return;
    state.current = clamp(idx, -1, state.images.length - 1);
    state.selection = -1;
    comparisonOverlay = null;
    const info = currentImage();
    if (info && !imageCache.has(info.name)) {
        const img = new Image();
        img.onload = () => {
            if (currentImage() === info) fitView();
        };
        img.src = imageUrl(info.name);
        imageCache.set(info.name, img);
    }
    fitView();
    renderImages();
    renderRegions();
}

$("prevBtn").onclick = () => {
    if (state.current > 0) selectImage(state.current - 1);
};
$("nextBtn").onclick = () => {
    if (state.current < state.images.length - 1) {
        selectImage(state.current + 1);
    }
};

/* ---------- uploads ---------- */

async function uploadImages(files) {
    const list = Array.from(files);
    if (!list.length) return;
    let done = 0;
    showProgress(`Uploading images (0/${list.length})…`);
    setProgress(0, list.length);
    try {
        for (const [idx, file] of list.entries()) {
            try {
                const info = await api(
                    `/api/images?name=${encodeURIComponent(file.name)}`,
                    { method: "POST", body: file },
                );
                addImportedImage(info, file.name);
                done += 1;
            } catch (err) {
                toast(`"${file.name}": ${err.message}`, "error");
            }
            $("progressLabel").textContent =
                `Uploading images (${idx + 1}/${list.length})…`;
            setProgress(idx + 1, list.length);
        }
    } finally {
        hideProgress();
    }
    if (done > 0) {
        toast(`Added ${done} image(s).`, "ok");
        if (state.current < 0) selectImage(0);
        renderImages();
    }
}

function addImportedImage(info, source) {
    const existing = state.images.findIndex((image) => image.name === info.name);
    if (existing >= 0) {
        state.images[existing] = info;
        imageCache.delete(info.name);
        imageVersion.set(info.name, (imageVersion.get(info.name) || 0) + 1);
    } else {
        state.images.push(info);
    }
    state.frames[info.name] = {source, reviewed: false, split: "train"};
    markChanged();
}

async function importServerImages(paths) {
    if (!paths.length) return;
    let done = 0;
    showProgress(`Importing server images (0/${paths.length})…`);
    try {
        for (const [index, path] of paths.entries()) {
            try {
                const info = await api("/api/images/from-path", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({path}),
                });
                addImportedImage(info, path);
                done += 1;
            } catch (err) {
                toast(`${path}: ${err.message}`, "error");
            }
            $("progressLabel").textContent =
                `Importing server images (${index + 1}/${paths.length})…`;
            setProgress(index + 1, paths.length);
        }
    } finally {
        hideProgress();
    }
    if (done) {
        toast(`Added ${done} server image(s).`, "ok");
        if (state.current < 0) selectImage(0);
        renderImages();
    }
}

let pendingVideo = null;

function askVideoStride(file) {
    pendingVideo = file;
    $("videoFileName").textContent = file.path || file.name;
    $("videoDialog").showModal();
}

// Upload with XMLHttpRequest to get byte-level upload progress, then
// poll /api/progress while the server extracts frames.
function uploadVideo(url, file) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", url);
        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
                $("progressLabel").textContent = "Uploading video…";
                setProgress(e.loaded, e.total);
            }
        };
        xhr.upload.onload = () => {
            showProgress("Extracting frames…");
            startProgressPoll("Extracting frames");
        };
        xhr.onload = () => {
            let payload = {};
            try {
                payload = JSON.parse(xhr.responseText);
            } catch {
                /* non-json */
            }
            if (xhr.status >= 200 && xhr.status < 300) resolve(payload);
            else {
                reject(
                    new Error(
                        payload.error || `Request failed (${xhr.status})`,
                    ),
                );
            }
        };
        xhr.onerror = () => reject(new Error("Network error"));
        xhr.send(file);
    });
}

$("videoForm").onsubmit = async (e) => {
    e.preventDefault();
    const stride = $("videoForm").elements.stride.value || "1";
    const file = pendingVideo;
    pendingVideo = null;
    $("videoDialog").close();
    if (!file) return;
    showProgress(file.path ? "Extracting server video…" : "Uploading video…");
    try {
        let result;
        if (file.path) {
            startProgressPoll("Extracting frames");
            result = await api("/api/video/from-path", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({path: file.path, stride: Number(stride)}),
            });
        } else {
            result = await uploadVideo(
                `/api/video?name=${encodeURIComponent(file.name)}` +
                    `&stride=${encodeURIComponent(stride)}`,
                file,
            );
        }
        for (const info of result.frames) {
            const existing = state.images.findIndex((image) => image.name === info.name);
            if (existing >= 0) {
                state.images[existing] = info;
                imageCache.delete(info.name);
                imageVersion.set(info.name, (imageVersion.get(info.name) || 0) + 1);
            } else {
                state.images.push(info);
            }
            state.frames[info.name] = {
                source: info.source, frame_index: info.frame_index,
                timestamp_ms: info.timestamp_ms,
                reviewed: false, split: "train",
            };
        }
        markChanged();
        toast(`Added ${result.frames.length} frame(s).`, "ok");
        if (state.current < 0) selectImage(0);
        renderImages();
    } catch (err) {
        toast(`Video import failed: ${err.message}`, "error");
    } finally {
        hideProgress();
    }
};

$("addImagesBtn").onclick = () => $("imageFiles").click();
$("addVideoBtn").onclick = () => $("videoFile").click();
$("browseImagesBtn").onclick = () => openBrowser("image", "image");
$("browseVideoBtn").onclick = () => openBrowser("video", "video");
$("imageFiles").onchange = (e) => {
    uploadImages(e.target.files);
    e.target.value = "";
};
$("videoFile").onchange = (e) => {
    if (e.target.files[0]) askVideoStride(e.target.files[0]);
    e.target.value = "";
};

/* drag & drop */

let dragDepth = 0;
stage.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragDepth += 1;
    $("dropHint").classList.remove("hidden");
});
stage.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) $("dropHint").classList.add("hidden");
});
stage.addEventListener("dragover", (e) => e.preventDefault());
stage.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    $("dropHint").classList.add("hidden");
    const files = Array.from(e.dataTransfer.files);
    const videos = files.filter((f) => f.type.startsWith("video/"));
    const images = files.filter((f) => f.type.startsWith("image/"));
    if (images.length) uploadImages(images);
    if (videos.length) askVideoStride(videos[0]);
});

/* ---------- canvas: view ---------- */

function resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const rect = stage.getBoundingClientRect();
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (state.fitted) fitView();
    else render();
}

function fitView() {
    const info = currentImage();
    if (!info) {
        render();
        return;
    }
    const rect = stage.getBoundingClientRect();
    const pad = 24;
    const scale = Math.min(
        (rect.width - pad * 2) / info.width,
        (rect.height - pad * 2) / info.height,
        1,
    );
    state.view.scale = Math.max(scale, MIN_SCALE);
    state.view.x = (rect.width - info.width * state.view.scale) / 2;
    state.view.y = (rect.height - info.height * state.view.scale) / 2;
    state.fitted = true;
    updateZoomText();
    render();
}

function setZoom(newScale, cx, cy) {
    const scale = clamp(newScale, MIN_SCALE, MAX_SCALE);
    const before = screenToImage(cx, cy);
    state.view.scale = scale;
    state.view.x = cx - before.x * scale;
    state.view.y = cy - before.y * scale;
    state.fitted = false;
    updateZoomText();
    render();
}

function updateZoomText() {
    $("zoomText").textContent = `${Math.round(state.view.scale * 100)}%`;
}

$("fitBtn").onclick = fitView;
$("zoomInBtn").onclick = () => {
    const rect = stage.getBoundingClientRect();
    setZoom(state.view.scale * 1.25, rect.width / 2, rect.height / 2);
};
$("zoomOutBtn").onclick = () => {
    const rect = stage.getBoundingClientRect();
    setZoom(state.view.scale / 1.25, rect.width / 2, rect.height / 2);
};

canvas.addEventListener(
    "wheel",
    (e) => {
        e.preventDefault();
        const factor = Math.exp(-e.deltaY * 0.0015);
        setZoom(state.view.scale * factor, e.offsetX, e.offsetY);
    },
    { passive: false },
);

/* ---------- canvas: hit testing ---------- */

function handlePositions(box) {
    const s = state.view.scale;
    const x = box.x * s + state.view.x;
    const y = box.y * s + state.view.y;
    const w = box.w * s;
    const h = box.h * s;
    return [
        { x, y, cursor: "nwse-resize", dx: -1, dy: -1 },
        { x: x + w / 2, y, cursor: "ns-resize", dx: 0, dy: -1 },
        { x: x + w, y, cursor: "nesw-resize", dx: 1, dy: -1 },
        { x: x + w, y: y + h / 2, cursor: "ew-resize", dx: 1, dy: 0 },
        { x: x + w, y: y + h, cursor: "nwse-resize", dx: 1, dy: 1 },
        { x: x + w / 2, y: y + h, cursor: "ns-resize", dx: 0, dy: 1 },
        { x, y: y + h, cursor: "nesw-resize", dx: -1, dy: 1 },
        { x, y: y + h / 2, cursor: "ew-resize", dx: -1, dy: 0 },
    ];
}

function hitTest(sx, sy) {
    const boxes = currentBoxes();
    // Handles of the selected box take priority.
    if (state.selection >= 0 && boxes[state.selection]) {
        const handles = handlePositions(boxes[state.selection]);
        for (let h = 0; h < handles.length; h++) {
            if (
                Math.abs(sx - handles[h].x) <= HANDLE_HIT &&
                Math.abs(sy - handles[h].y) <= HANDLE_HIT
            ) {
                return { box: state.selection, handle: h };
            }
        }
    }
    const pt = screenToImage(sx, sy);
    let best = -1;
    let bestArea = Infinity;
    boxes.forEach((box, idx) => {
        const inside =
            pt.x >= box.x &&
            pt.x <= box.x + box.w &&
            pt.y >= box.y &&
            pt.y <= box.y + box.h;
        const area = box.w * box.h;
        if (inside && area < bestArea) {
            best = idx;
            bestArea = area;
        }
    });
    return { box: best, handle: -1 };
}

/* ---------- canvas: interactions ---------- */

canvas.addEventListener("pointerdown", (e) => {
    const blocked = comparisonOverlay || (currentImage() && state.frames[currentImage().name]?.split === "test");
    if (blocked && e.button === 0 && !spaceDown) {
        if (!comparisonOverlay) toast("Remove this frame from the test set before editing boxes.", "error");
        return;
    }
    if (!currentImage() && e.button === 0) return;
    canvas.setPointerCapture(e.pointerId);
    const info = currentImage();

    if (e.button === 1 || (e.button === 0 && (spaceDown || !info))) {
        drag = {
            mode: "pan",
            startX: e.offsetX,
            startY: e.offsetY,
            viewX: state.view.x,
            viewY: state.view.y,
        };
        return;
    }
    if (e.button !== 0) return;

    const hit = hitTest(e.offsetX, e.offsetY);
    const boxes = currentBoxes();

    if (hit.handle >= 0) {
        const box = boxes[hit.box];
        drag = {
            mode: "resize",
            index: hit.box,
            handle: hit.handle,
            orig: { ...box },
            moved: false,
        };
        return;
    }
    if (hit.box >= 0) {
        state.selection = hit.box;
        const pt = screenToImage(e.offsetX, e.offsetY);
        const box = boxes[hit.box];
        drag = {
            mode: "move",
            index: hit.box,
            offsetX: pt.x - box.x,
            offsetY: pt.y - box.y,
            moved: false,
        };
        render();
        return;
    }

    state.selection = -1;
    if (state.activeLabel) {
        const pt = screenToImage(e.offsetX, e.offsetY);
        const x = clamp(pt.x, 0, info.width);
        const y = clamp(pt.y, 0, info.height);
        drag = { mode: "draw", startX: x, startY: y, rect: null };
    } else {
        drag = {
            mode: "pan",
            startX: e.offsetX,
            startY: e.offsetY,
            viewX: state.view.x,
            viewY: state.view.y,
        };
    }
    render();
});

canvas.addEventListener("pointermove", (e) => {
    pointer = { x: e.offsetX, y: e.offsetY, inside: true };
    const info = currentImage();

    if (!drag) {
        hover = info ? hitTest(e.offsetX, e.offsetY) : { box: -1, handle: -1 };
        updateCursor();
        render();
        return;
    }

    if (drag.mode === "pan") {
        state.view.x = drag.viewX + (e.offsetX - drag.startX);
        state.view.y = drag.viewY + (e.offsetY - drag.startY);
        state.fitted = false;
        render();
        return;
    }

    const pt = screenToImage(e.offsetX, e.offsetY);
    const boxes = currentBoxes();

    if (drag.mode === "draw") {
        const x = clamp(pt.x, 0, info.width);
        const y = clamp(pt.y, 0, info.height);
        drag.rect = {
            x: Math.min(drag.startX, x),
            y: Math.min(drag.startY, y),
            w: Math.abs(x - drag.startX),
            h: Math.abs(y - drag.startY),
        };
    } else if (drag.mode === "move") {
        const box = boxes[drag.index];
        box.x = clamp(pt.x - drag.offsetX, 0, info.width - box.w);
        box.y = clamp(pt.y - drag.offsetY, 0, info.height - box.h);
        drag.moved = true;
    } else if (drag.mode === "resize") {
        resizeBox(boxes[drag.index], drag, pt, info);
        drag.moved = true;
    }
    render();
});

function resizeBox(box, dragState, pt, info) {
    const orig = dragState.orig;
    const handle = handlePositions(orig)[dragState.handle];
    let x1 = orig.x;
    let y1 = orig.y;
    let x2 = orig.x + orig.w;
    let y2 = orig.y + orig.h;
    const px = clamp(pt.x, 0, info.width);
    const py = clamp(pt.y, 0, info.height);
    if (handle.dx < 0) x1 = px;
    if (handle.dx > 0) x2 = px;
    if (handle.dy < 0) y1 = py;
    if (handle.dy > 0) y2 = py;
    box.x = Math.min(x1, x2);
    box.y = Math.min(y1, y2);
    box.w = Math.abs(x2 - x1);
    box.h = Math.abs(y2 - y1);
}

canvas.addEventListener("pointerup", (e) => {
    if (!drag) return;
    const info = currentImage();

    if (drag.mode === "draw" && drag.rect && info) {
        if (drag.rect.w >= MIN_BOX_SIZE && drag.rect.h >= MIN_BOX_SIZE) {
            const boxes = currentBoxes();
            boxes.push({ label: state.activeLabel, ...drag.rect });
            state.selection = boxes.length - 1;
            annotationsChanged();
            renderLabels();
            renderImages();
        }
    } else if (
        (drag.mode === "move" || drag.mode === "resize") &&
        drag.moved
    ) {
        annotationsChanged();
    }
    drag = null;
    hover = info ? hitTest(e.offsetX, e.offsetY) : { box: -1, handle: -1 };
    updateCursor();
    render();
});

canvas.addEventListener("pointerleave", () => {
    pointer.inside = false;
    hover = { box: -1, handle: -1 };
    render();
});

canvas.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (comparisonOverlay) return;
    if (!currentImage()) return;
    const hit = hitTest(e.offsetX, e.offsetY);
    if (hit.box >= 0) deleteBox(hit.box);
});

function deleteBox(index) {
    if (currentImage() && state.frames[currentImage().name]?.split === "test") return;
    const boxes = currentBoxes();
    boxes.splice(index, 1);
    if (state.selection === index) state.selection = -1;
    else if (state.selection > index) state.selection -= 1;
    annotationsChanged();
    renderLabels();
    renderImages();
    render();
}

function updateCursor() {
    if (spaceDown || (drag && drag.mode === "pan")) {
        canvas.style.cursor = "grab";
    } else if (hover.handle >= 0) {
        const boxes = currentBoxes();
        canvas.style.cursor = handlePositions(boxes[hover.box])[
            hover.handle
        ].cursor;
    } else if (hover.box >= 0) {
        canvas.style.cursor = "move";
    } else if (state.activeLabel && currentImage()) {
        canvas.style.cursor = "crosshair";
    } else {
        canvas.style.cursor = "default";
    }
}

/* ---------- keyboard ---------- */

document.addEventListener("keydown", (e) => {
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    if (document.querySelector("dialog[open]")) return;

    if (e.code === "Space") {
        spaceDown = true;
        updateCursor();
        e.preventDefault();
    } else if (e.key === "Escape") {
        comparisonOverlay = null;
        if (drag && drag.mode === "draw") drag = null;
        state.selection = -1;
        render();
    } else if (e.key === "Delete" || e.key === "Backspace") {
        if (state.selection >= 0) {
            deleteBox(state.selection);
            e.preventDefault();
        }
    } else if (e.key === "ArrowLeft") {
        $("prevBtn").click();
    } else if (e.key === "ArrowRight") {
        $("nextBtn").click();
    }
});

document.addEventListener("keyup", (e) => {
    if (e.code === "Space") {
        spaceDown = false;
        updateCursor();
    }
});

/* ---------- rendering ---------- */

function render() {
    const rect = stage.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);
    const info = currentImage();
    const selected = currentBoxes()[state.selection];
    for (const [id, field] of [["uncertainCheck", "uncertain"], ["borderCheck", "border_truncated"]]) {
        $(id).disabled = !selected;
        $(id).checked = Boolean(selected && selected[field]);
    }
    $("boxReason").textContent = selected?.uncertainty_reasons?.length
        ? `Check: ${selected.uncertainty_reasons.join(", ")}`
        : selected?.provenance === "propagated_draft"
            ? "Propagated draft: verify before using."
            : "";
    if (!info) return;

    const img = imageCache.get(info.name);
    const { scale, x: ox, y: oy } = state.view;

    if (img && img.complete && img.naturalWidth) {
        ctx.imageSmoothingEnabled = scale < 4;
        ctx.drawImage(img, ox, oy, info.width * scale, info.height * scale);
    }
    ctx.strokeStyle = "rgba(255,255,255,0.25)";
    ctx.lineWidth = 1;
    ctx.strokeRect(ox, oy, info.width * scale, info.height * scale);

    if (comparisonOverlay && comparisonOverlay.name === info.name) {
        for (const [boxes, color] of [[comparisonOverlay.truth_boxes, "#22c55e"], [comparisonOverlay.prediction_boxes, "#f43f5e"]]) {
            for (const box of boxes) {
                ctx.save();
                ctx.strokeStyle = color;
                ctx.lineWidth = 2;
                ctx.strokeRect(ox + box.x * scale, oy + box.y * scale, box.w * scale, box.h * scale);
                ctx.restore();
            }
        }
    } else {
        const boxes = currentBoxes();
        boxes.forEach((box, idx) => {
            drawBox(box, idx === state.selection, idx === hover.box);
        });
    }

    const region = (state.regions[info.name] || []).find(
        (item) => item.id === selectedRegionId,
    );
    if (region) {
        ctx.save();
        ctx.strokeStyle = region.reviewed ? "#22c55e" : "#facc15";
        ctx.lineWidth = 3;
        ctx.strokeRect(
            ox + region.x * scale, oy + region.y * scale,
            region.w * scale, region.h * scale,
        );
        ctx.restore();
    }

    if (drag && drag.mode === "draw" && drag.rect) {
        drawBox({ label: state.activeLabel, ...drag.rect }, false, false);
    }

    // Crosshair guides while drawing is possible.
    const drawing = drag && drag.mode === "draw";
    const idle = !drag && hover.box < 0 && state.activeLabel && !spaceDown;
    if (pointer.inside && (drawing || idle)) {
        ctx.save();
        ctx.strokeStyle = "rgba(230,233,239,0.35)";
        ctx.lineWidth = 1;
        ctx.setLineDash([5, 5]);
        ctx.beginPath();
        ctx.moveTo(pointer.x, 0);
        ctx.lineTo(pointer.x, rect.height);
        ctx.moveTo(0, pointer.y);
        ctx.lineTo(rect.width, pointer.y);
        ctx.stroke();
        ctx.restore();
    }
}

function drawBox(box, selected, hovered) {
    const { scale, x: ox, y: oy } = state.view;
    const x = box.x * scale + ox;
    const y = box.y * scale + oy;
    const w = box.w * scale;
    const h = box.h * scale;
    const color = labelColor(box.label);

    ctx.save();
    ctx.fillStyle = hexToRgba(color, selected || hovered ? 0.22 : 0.12);
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = color;
    ctx.lineWidth = selected ? 2.5 : 2;
    ctx.setLineDash(selected ? [] : [6, 4]);
    ctx.strokeRect(x, y, w, h);
    ctx.setLineDash([]);

    // Label tag.
    const text = box.label || "";
    ctx.font = "11px " + getComputedStyle(document.body).fontFamily;
    const tw = ctx.measureText(text).width + 10;
    const ty = y - 17 < 0 ? y : y - 17;
    ctx.fillStyle = color;
    ctx.fillRect(x, ty, tw, 17);
    ctx.fillStyle = "#fff";
    ctx.fillText(text, x + 5, ty + 12);

    if (selected) {
        for (const handle of handlePositions(box)) {
            ctx.fillStyle = "#fff";
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            ctx.fillRect(
                handle.x - HANDLE_SIZE / 2,
                handle.y - HANDLE_SIZE / 2,
                HANDLE_SIZE,
                HANDLE_SIZE,
            );
            ctx.strokeRect(
                handle.x - HANDLE_SIZE / 2,
                handle.y - HANDLE_SIZE / 2,
                HANDLE_SIZE,
                HANDLE_SIZE,
            );
        }
    }
    ctx.restore();
}

function hexToRgba(hex, alpha) {
    const value = parseInt(hex.slice(1), 16);
    const r = (value >> 16) & 255;
    const g = (value >> 8) & 255;
    const b = value & 255;
    return `rgba(${r},${g},${b},${alpha})`;
}

/* ---------- dialogs ---------- */

let browserTarget = null;
let browserKind = "session";
let browserAppend = false;
let browserDirectory = "";
let browserParent = "";
let browserItems = [];
let browserAction = "path";
let browserSelected = new Set();

function renderBrowserItems() {
    const list = $("browserEntries");
    const filter = $("browserSearch").value.trim().toLowerCase();
    list.replaceChildren();
    const visible = browserItems.filter(item => item.name.toLowerCase().includes(filter));
    for (const item of visible) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "browser-entry";
        if (browserAction === "image" && browserSelected.has(item.path)) {
            button.classList.add("selected");
        }
        const marker = browserAction === "image" && !item.is_dir
            ? (browserSelected.has(item.path) ? "☑" : "☐")
            : (item.is_dir ? "📁" : "📄");
        button.textContent = `${marker} ${item.name}`;
        button.title = item.path;
        button.onclick = () => {
            if (item.is_dir) {
                loadBrowserDirectory(item.path);
            } else if (browserAction === "image") {
                if (browserSelected.has(item.path)) browserSelected.delete(item.path);
                else browserSelected.add(item.path);
                renderBrowserItems();
            } else if (browserAction === "video") {
                $("fileBrowserDialog").close();
                askVideoStride({path: item.path, name: item.name});
            } else {
                if (browserAppend) {
                    const prior = browserTarget.value.trim();
                    if (!prior.split("\n").includes(item.path)) {
                        browserTarget.value = prior ? `${prior}\n${item.path}` : item.path;
                    }
                } else {
                    browserTarget.value = item.path;
                }
                browserTarget.dispatchEvent(new Event("input", {bubbles: true}));
                $("fileBrowserDialog").close();
            }
        };
        list.appendChild(button);
    }
    $("browserImportSelected").disabled = browserSelected.size === 0;
    $("browserMessage").textContent = visible.length ? `${visible.length} item(s)` : "No matching files in this folder.";
}

async function loadBrowserDirectory(path) {
    $("browserMessage").textContent = "Loading…";
    try {
        const query = new URLSearchParams({kind: browserKind});
        if (path) query.set("path", path);
        const data = await api(`/api/files?${query}`);
        browserDirectory = data.path;
        browserParent = data.parent;
        browserItems = data.entries;
        browserSelected = new Set();
        $("browserPath").value = data.path;
        $("browserSearch").value = "";
        renderBrowserItems();
    } catch (err) {
        $("browserMessage").textContent = err.message;
    }
}

function openBrowser(kind, action = "path", target = null, append = false) {
    browserTarget = target;
    browserKind = kind;
    browserAction = action;
    browserAppend = append;
    const existing = target
        ? (append ? target.value.trim().split("\n").at(-1) : target.value.trim())
        : "";
    const initial = existing && existing.includes("/")
        ? (existing.slice(0, existing.lastIndexOf("/")) || "/")
        : state.workspace;
    $("browserUseFolder").hidden = target?.id !== "saveSessionPath";
    $("browserImportSelected").hidden = action !== "image";
    $("fileBrowserDialog").showModal();
    loadBrowserDirectory(initial);
}

for (const button of document.querySelectorAll(".browse-btn")) {
    button.onclick = () => {
        const [formId, fieldName] = button.dataset.target.split(":");
        openBrowser(
            button.dataset.kind,
            "path",
            $(formId).elements[fieldName],
            button.dataset.append === "true",
        );
    };
}
$("browserUp").onclick = () => loadBrowserDirectory(browserParent);
$("browserGo").onclick = () => loadBrowserDirectory($("browserPath").value.trim());
$("browserPath").onkeydown = (event) => {
    if (event.key === "Enter") { event.preventDefault(); $("browserGo").click(); }
};
$("browserSearch").oninput = renderBrowserItems;
$("browserCancel").onclick = () => $("fileBrowserDialog").close();
$("browserImportSelected").onclick = () => {
    const paths = [...browserSelected];
    if (!paths.length) return;
    $("fileBrowserDialog").close();
    importServerImages(paths);
};
$("browserUseFolder").onclick = () => {
    browserTarget.value = `${browserDirectory.replace(/\/$/, "")}/session.json`;
    browserTarget.dispatchEvent(new Event("input", {bubbles: true}));
    $("fileBrowserDialog").close();
};

for (const dialog of document.querySelectorAll("dialog")) {
    const closeBtn = dialog.querySelector("[data-close]");
    if (closeBtn) closeBtn.onclick = () => dialog.close();
}

$("exportBtn").onclick = () => $("exportDialog").showModal();
$("predictBtn").onclick = () => $("predictDialog").showModal();
$("compareBtn").onclick = () => $("compareDialog").showModal();
async function openComparison(id) {
    try {
        const report = await api(`/api/comparison?id=${id}&status=1`);
        for (const warning of report.input_warnings || []) {
            toast(warning, "error", "This is a saved historical comparison.", 12000);
        }
    } catch (error) {
        toast(`Cannot open comparison: ${error.message}`, "error");
        return;
    }
    $("comparisonFrame").src = `/reports/${id}`;
    $("comparisonHtmlLink").href = `/reports/${id}`;
    $("comparisonJsonLink").href = `/api/comparison?id=${id}`;
    $("comparisonViewDialog").showModal();
}

function renderComparisons() {
    const list = $("comparisonList");
    list.innerHTML = "";
    for (const item of state.comparisons) {
        const li = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "ghost-btn";
        button.textContent = `${item.id} · ${item.benchmark_id}`;
        button.onclick = () => openComparison(item.id);
        li.append(button);
        list.append(li);
    }
}
$("benchmarkBtn").onclick = async () => {
    await syncSession();
    try {
        const result = await api("/api/benchmark", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
        $("compareForm").elements.benchmark.value = result.path;
        toast(`Frozen benchmark: ${result.frames} frame(s).`, "ok", result.path, 12000);
    } catch (err) { toast(`Benchmark failed: ${err.message}`, "error"); }
};

$("predictForm").onsubmit = async (e) => {
    e.preventDefault();
    await syncSession();
    showProgress("Importing predictions…");
    try {
        const form = e.target.elements;
        const result = await api("/api/predict", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({model: form.model.value.trim(), confidence: Number(form.confidence.value), nms_iou: Number(form.nms_iou.value), imgsz: Number(form.imgsz.value), max_det: Number(form.max_det.value), device: form.device.value.trim() || null})});
        Object.assign(state.annotations, result.predictions);
        state.frames = result.frames;
        const addedClasses = !state.labels.length && result.labels.length;
        state.labels = result.labels;
        for (const name of Object.keys(result.predictions)) {
            for (const region of state.regions[name] || []) region.reviewed = false;
        }
        markChanged();
        renderLabels(); renderImages(); render();
        $("predictDialog").close();
        toast(`Imported draft boxes for ${Object.keys(result.predictions).length} frame(s).`, "ok");
        if (addedClasses) toast("Model classes were added to this session. Check the class list before reviewing.", "ok");
    } catch (err) { toast(`Prediction import failed: ${err.message}`, "error"); }
    finally { hideProgress(); }
};

$("compareForm").onsubmit = async (e) => {
    e.preventDefault();
    const form = e.target.elements;
    showProgress("Comparing models…");
    startProgressPoll("Comparing models");
    try {
        const result = await api("/api/compare", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({benchmark: form.benchmark.value.trim(), models: form.models.value.split("\n").map(x => x.trim()).filter(Boolean), match_iou: Number(form.match_iou.value), confidence: Number(form.confidence.value), nms_iou: Number(form.nms_iou.value), imgsz: Number(form.imgsz.value), max_det: Number(form.max_det.value), device: form.device.value.trim() || null})});
        state.comparisons = result.comparisons;
        markChanged();
        renderComparisons();
        $("compareResults").textContent = result.reports.map(r => `${r.model}: P ${r.precision.toFixed(3)}, R ${r.recall.toFixed(3)}, F1 ${r.f1.toFixed(3)}, TP ${r.tp}, FP ${r.fp}, FN ${r.fn}`).join("\n") + `\nFull frame-level report: ${result.path}`;
        const list = $("comparisonFrames");
        list.innerHTML = "";
        for (const report of result.reports) {
            const heading = document.createElement("h4");
            heading.textContent = report.model;
            list.appendChild(heading);
            for (const frame of [...report.per_frame].sort((a, b) => (b.fp + b.fn) - (a.fp + a.fn))) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "ghost-btn";
                button.textContent = `${frame.name}: ${frame.tp} found, ${frame.fp} extra, ${frame.fn} missed`;
                button.onclick = () => {
                    const idx = state.images.findIndex(image => image.name === frame.name);
                    if (idx >= 0) {
                        selectImage(idx, true);
                        comparisonOverlay = frame;
                        render();
                        $("compareDialog").close();
                        toast("Overlay: green = benchmark, red = prediction", "ok");
                    }
                };
                list.appendChild(button);
            }
        }
        $("compareDialog").close();
        openComparison(result.id);
    } catch (err) { toast(`Comparison failed: ${err.message}`, "error"); }
    finally { hideProgress(); }
};
$("synthBtn").onclick = () => $("synthDialog").showModal();

$("exportForm").onsubmit = async (e) => {
    e.preventDefault();
    const form = e.target.elements;
    const submitBtn = e.target.querySelector("button[type=submit]");
    submitBtn.disabled = true;
    await syncSession();
    showProgress("Exporting dataset…");
    startProgressPoll("Exporting dataset");
    try {
        const result = await api("/api/export", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name: form.name.value.trim() || "yolo_dataset",
                train_split: Number(form.train.value) / 100,
                shuffle: form.shuffle.checked,
                seed: form.seed.value === "" ? null : Number(form.seed.value),
                output_dir: form.output.value.trim() || null,
            }),
        });
        $("exportDialog").close();
        toast(
            `Dataset exported (${result.num_train} train / ` +
                `${result.num_val} val).`,
            "ok",
            result.yaml,
            12000,
        );
    } catch (err) {
        toast(`Export failed: ${err.message}`, "error");
    } finally {
        hideProgress();
        submitBtn.disabled = false;
    }
};

$("synthForm").onsubmit = async (e) => {
    e.preventDefault();
    const form = e.target.elements;
    const submitBtn = e.target.querySelector("button[type=submit]");
    submitBtn.disabled = true;
    await syncSession();
    showProgress("Synthesizing dataset…");
    startProgressPoll("Synthesizing dataset");
    try {
        const result = await api("/api/synthesize", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name: form.name.value.trim() || "synt_dataset",
                num_images: Number(form.num.value),
                width: Number(form.width.value),
                height: Number(form.height.value),
                per_image: Number(form.per.value),
                train_split: Number(form.train.value) / 100,
                scale_min: Number(form.smin.value),
                scale_max: Number(form.smax.value),
                background: form.background.value,
                background_mode: form.background_mode.value,
                output_dir: form.output.value.trim() || null,
            }),
        });
        $("synthDialog").close();
        toast(
            `Synthetic dataset created (${result.num_train} train / ` +
                `${result.num_val} val).`,
            "ok",
            result.yaml,
            12000,
        );
    } catch (err) {
        toast(`Synthesis failed: ${err.message}`, "error");
    } finally {
        hideProgress();
        submitBtn.disabled = false;
    }
};

/* ---------- session save / load ---------- */

$("saveSessionBtn").onclick = () => {
    quitAfterSave = false;
    openSaveDialog();
};

function openSaveDialog() {
    const input = $("saveForm").elements.path;
    if (sessionPath && !input.value) input.value = sessionPath;
    $("saveDialog").showModal();
}

$("saveForm").onsubmit = async (e) => {
    e.preventDefault();
    const path = e.target.elements.path.value.trim();
    if (!path) return;
    try {
        const result = await api("/api/session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                path,
                labels: state.labels,
                annotations: state.annotations,
                frames: state.frames,
                regions: state.regions,
                review_queue: state.review_queue,
                comparisons: state.comparisons,
            }),
        });
        sessionPath = result.path;
        dirty = false;
        setSaveStatus();
        $("saveDialog").close();
        toast("Session saved.", "ok", result.path);
        if (quitAfterSave) {
            quitAfterSave = false;
            await shutdownServer();
        }
    } catch (err) {
        toast(`Save failed: ${err.message}`, "error");
    }
};

$("loadSessionBtn").onclick = () => {
    const input = $("loadForm").elements.path;
    if (sessionPath && !input.value) input.value = sessionPath;
    $("loadDialog").showModal();
};

$("loadForm").onsubmit = async (e) => {
    e.preventDefault();
    const path = e.target.elements.path.value.trim();
    if (!path) return;
    if (
        dirty &&
        !confirm("Loading a session discards unsaved changes. Continue?")
    ) {
        return;
    }
    try {
        const session = await api("/api/session/load", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path }),
        });
        state.labels = session.labels || [];
        state.annotations = session.annotations || {};
        state.frames = session.frames || {};
        state.regions = session.regions || {};
        state.review_queue = session.review_queue || {};
        state.comparisons = session.comparisons || [];
        state.images = session.images || [];
        state.activeLabel = null;
        state.selection = -1;
        sessionPath = path;
        dirty = false;
        setSaveStatus();
        $("loadDialog").close();
        renderLabels();
        renderImages();
        renderComparisons();
        render();
        toast("Session loaded.", "ok", path);
    } catch (err) {
        toast(`Load failed: ${err.message}`, "error");
    }
};

/* ---------- quit ---------- */

async function shutdownServer() {
    dirty = false; // suppress the beforeunload warning
    try {
        await api("/api/shutdown", { method: "POST" });
    } catch {
        /* server is going down */
    }
    document.body.innerHTML =
        '<div id="emptyState"><p><strong>Server stopped.</strong></p>' +
        "<p>You can close this tab.</p></div>";
}

$("quitBtn").onclick = () => {
    if (dirty) $("quitDialog").showModal();
    else shutdownServer();
};

$("quitDiscardBtn").onclick = () => {
    $("quitDialog").close();
    shutdownServer();
};

$("quitSaveBtn").onclick = () => {
    $("quitDialog").close();
    quitAfterSave = true;
    openSaveDialog();
};

/* ---------- init ---------- */

async function init() {
    resizeCanvas();
    try {
        const data = await api("/api/state");
        state.workspace = data.workspace;
        state.images = data.images;
        state.labels = data.labels;
        state.annotations = data.annotations;
        state.frames = data.frames || {};
        state.regions = data.regions || {};
        state.review_queue = data.review_queue || {};
        state.comparisons = data.comparisons || [];
        sessionPath = data.session_path;
        dirty = Boolean(data.dirty);
        $("workspacePath").textContent = data.workspace;
        setSaveStatus();
        renderLabels();
        renderImages();
        renderComparisons();
        if (state.images.length) selectImage(0, true);
    } catch (err) {
        toast(`Could not load session: ${err.message}`, "error");
    }
}

window.addEventListener("resize", resizeCanvas);
new ResizeObserver(resizeCanvas).observe(stage);
init();

/* ---------- region review and work queue ---------- */

function regionIntersects(box, region) {
    return box.x < region.x + region.w && box.x + box.w > region.x &&
        box.y < region.y + region.h && box.y + box.h > region.y;
}

function selectedRegion() {
    const image = currentImage();
    return (state.regions[image?.name] || []).find(
        (item) => item.id === selectedRegionId,
    );
}

function fitRegion() {
    const region = selectedRegion();
    if (!region) return;
    const rect = stage.getBoundingClientRect();
    const scale = Math.min(
        (rect.width - 40) / region.w,
        (rect.height - 40) / region.h,
        MAX_SCALE,
    );
    state.view.scale = Math.max(MIN_SCALE, scale);
    state.view.x = (rect.width - region.w * scale) / 2 - region.x * scale;
    state.view.y = (rect.height - region.h * scale) / 2 - region.y * scale;
    state.fitted = false;
    updateZoomText();
    render();
}

function selectRegion(name, id) {
    const index = state.images.findIndex((image) => image.name === name);
    if (index < 0) return;
    selectImage(index, true);
    selectedRegionId = id;
    renderRegions();
    fitRegion();
}

function renderRegions() {
    const list = $("regionList");
    list.innerHTML = "";
    const image = currentImage();
    for (const region of state.regions[image?.name] || []) {
        const li = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "ghost-btn";
        button.textContent = `${region.reviewed ? "✓" : "○"} ${region.x},${region.y} · ${region.w}×${region.h}`;
        button.onclick = () => selectRegion(image.name, region.id);
        li.append(button);
        list.append(li);
    }
    $("verifyRegionBtn").disabled = !selectedRegion();
    $("unverifyRegionBtn").disabled = !selectedRegion();
    $("fitRegionBtn").disabled = !selectedRegion();
}

$("makeRegionsBtn").onclick = () => {
    const image = currentImage();
    if (!image) return;
    if (state.regions[image.name]?.length &&
        !confirm("Replace this frame's region grid and verification decisions?")) return;
    const size = 256;
    state.regions[image.name] = [];
    for (let y = 0; y < image.height; y += size) {
        for (let x = 0; x < image.width; x += size) {
            const w = Math.min(size, image.width - x);
            const h = Math.min(size, image.height - y);
            state.regions[image.name].push({
                id: `${image.name}:${x}:${y}:${w}:${h}`,
                x, y, w, h, reviewed: false,
            });
        }
    }
    selectedRegionId = state.regions[image.name][0]?.id || null;
    markChanged();
    renderRegions();
    fitRegion();
};
$("fitRegionBtn").onclick = fitRegion;
$("verifyRegionBtn").onclick = () => {
    const region = selectedRegion();
    if (!region) return;
    if (currentImage() && state.frames[currentImage().name]?.split === "test") {
        toast("Test frames cannot enter region training export.", "error");
        return;
    }
    region.reviewed = true;
    region.verified_at = new Date().toISOString();
    markChanged();
    renderRegions();
    renderImages();
    render();
};
$("unverifyRegionBtn").onclick = () => {
    const region = selectedRegion();
    if (!region) return;
    region.reviewed = false;
    markChanged();
    renderRegions();
    renderImages();
    render();
};
$("exportRegionsBtn").onclick = async () => {
    const count = Object.values(state.regions).flat().filter((r) => r.reviewed).length;
    if (!count) return toast("Verify at least one tile first.", "error");
    const name = prompt("Dataset name for verified regions", "verified_regions");
    if (!name) return;
    await syncSession();
    try {
        const result = await api("/api/regions/export", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name}),
        });
        toast(`Exported ${result.regions} verified regions.`, "ok", result.path, 12000);
    } catch (err) {
        toast(`Region export failed: ${err.message}`, "error");
    }
};

async function rankQueue() {
    let comparison = null;
    const latest = state.comparisons.at(-1);
    if (latest) {
        try { comparison = await api(`/api/comparison?id=${latest.id}`); }
        catch { /* comparison file may have moved */ }
    }
    const candidates = [];
    for (const [name, regions] of Object.entries(state.regions)) {
        if (state.frames[name]?.split === "test") continue;
        const boxes = state.annotations[name] || [];
        const compared = comparison?.reports?.map((report) =>
            report.per_frame.find((row) => row.name === name),
        ).filter(Boolean) || [];
        for (const region of regions) {
            if (region.reviewed || state.review_queue[region.id]) continue;
            const nearby = boxes.filter((box) => regionIntersects(box, region));
            const reasons = [];
            if (nearby.some((box) => (box.confidence ?? 1) < 0.5)) reasons.push("low confidence");
            if (nearby.some((box) => box.uncertain)) reasons.push("uncertain box");
            for (const reason of new Set(nearby.flatMap((box) => box.uncertainty_reasons || []))) reasons.push(reason);
            if (nearby.some((box) => box.w / Math.max(1, box.h) > 3 ||
                box.h / Math.max(1, box.w) > 3)) reasons.push("unusual aspect ratio");
            if (nearby.some((box) => box.x < region.x || box.y < region.y ||
                box.x + box.w > region.x + region.w || box.y + box.h > region.y + region.h)) reasons.push("tile border");
            if (compared.some((row) => row.prediction_boxes.some((box, idx) =>
                row.false_positives.includes(idx) && regionIntersects(box, region)))) reasons.push("false positives");
            if (compared.some((row) => row.truth_boxes.some((box, idx) =>
                row.missed.includes(idx) && regionIntersects(box, region)))) reasons.push("missed cells");
            if (nearby.length === 0) reasons.push("ordinary empty sample");
            const frame = state.frames[name] || {};
            const previous = Object.entries(state.frames).filter(([, meta]) =>
                meta.source === frame.source &&
                typeof meta.frame_index === "number" &&
                meta.frame_index < frame.frame_index,
            ).sort((a, b) => b[1].frame_index - a[1].frame_index)[0];
            if (previous && Math.abs(boxes.length -
                (state.annotations[previous[0]] || []).length) > 30) reasons.push("temporal count change");
            if (!reasons.length) reasons.push("ordinary sample");
            candidates.push({name, region, reasons, estimated: nearby.length,
                score: reasons.filter((reason) => !reason.startsWith("ordinary")).length * 10 + Math.min(nearby.length, 20)});
        }
    }
    candidates.sort((a, b) => b.score - a.score);
    const seen = new Map();
    const diverse = candidates.filter((item) => {
        const meta = state.frames[item.name] || {};
        const source = meta.source || item.name;
        const frame = meta.frame_index;
        if (typeof frame !== "number") return true;
        const chosen = seen.get(source) || [];
        const same = chosen.filter((value) => value === frame).length;
        const adjacent = chosen.some((value) => value !== frame && Math.abs(value - frame) < 5);
        if (same >= 3 || adjacent) return false;
        seen.set(source, [...chosen, frame]);
        return true;
    });
    const hard = diverse.filter((item) => item.score >= 10).slice(0, 24);
    const ordinary = diverse.filter((item) => item.score < 10).slice(0, 6);
    renderQueue([...hard, ...ordinary]);
}

function renderQueue(items) {
    const list = $("reviewQueueList");
    list.innerHTML = "";
    for (const item of items) {
        const li = document.createElement("li");
        const view = document.createElement("button");
        view.type = "button";
        view.className = "ghost-btn";
        view.textContent = `${item.name} · ${item.region.x},${item.region.y} · ${item.estimated} boxes · ${item.reasons.join(", ")}`;
        view.onclick = () => selectRegion(item.name, item.region.id);
        li.append(view);
        for (const [decision, label] of [["accepted", "Accept"], ["rejected", "Reject"], ["deferred", "Defer"]]) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "ghost-btn";
            button.textContent = label;
            button.onclick = () => {
                state.review_queue[item.region.id] = decision;
                markChanged();
                rankQueue();
            };
            li.append(button);
        }
        list.append(li);
    }
}
$("refreshQueueBtn").onclick = rankQueue;
$("propagateBtn").onclick = async () => {
    const image = currentImage();
    if (!image) return;
    await syncSession();
    try {
        const result = await api("/api/propagate", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({source: image.name}),
        });
        state.annotations[result.target] = result.boxes;
        state.frames = result.frames;
        for (const region of state.regions[result.target] || []) region.reviewed = false;
        markChanged();
        selectImage(state.images.findIndex((item) => item.name === result.target), true);
        toast("Suggested boxes are drafts. Review before trusting them.", "ok");
    } catch (err) { toast(`Propagation failed: ${err.message}`, "error"); }
};

$("pseudoExportBtn").onclick = () => $("pseudoDialog").showModal();
$("pseudoForm").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target.elements;
    await syncSession();
    try {
        const result = await api("/api/pseudo/export", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                name: form.name.value.trim(),
                confidence: Number(form.confidence.value),
                match_iou: Number(form.match_iou.value),
                max_frame_gap: Number(form.max_frame_gap.value),
                train_split: Number(form.train_split.value) / 100,
            }),
        });
        $("pseudoDialog").close();
        toast(`Exported ${result.boxes} pseudo-label proposals as a separate dataset.`,
            "ok", result.path, 12000);
    } catch (error) {
        toast(`Pseudo-label export failed: ${error.message}`, "error");
    }
};
