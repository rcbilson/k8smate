// k8smate frontend

const $ = (sel) => document.querySelector(sel);
const podList = $("#pod-list");
const modal = $("#modal");
const modalTitle = $("#modal-title");
const modalBody = $("#modal-body");

// State: workload info keyed by "namespace/containerName"
let workloadIndex = {};

async function fetchJSON(url, opts) {
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || resp.statusText);
  }
  return resp.json();
}

function shortDigest(digest) {
  if (!digest) return "";
  // sha256:abc123... -> sha256:abc123
  return digest.slice(0, 19) + "...";
}

function shortImage(image) {
  // Show repo:tag or repo@sha256:short
  if (image.includes("@sha256:")) {
    const [repo, digest] = image.split("@");
    return repo + "@" + shortDigest(digest);
  }
  return image;
}

// Build an index of workloads from the config repo YAML files
async function loadWorkloads() {
  try {
    const workloads = await fetchJSON("/api/workloads");
    workloadIndex = {};
    for (const w of workloads) {
      for (const c of w.containers) {
        const key = `${w.namespace}/${c.name}`;
        workloadIndex[key] = { ...c, file: w.file, workloadName: w.name };
      }
    }
  } catch (e) {
    console.error("Failed to load workloads:", e);
  }
}

async function loadPods() {
  podList.innerHTML = '<div class="spinner" style="margin:40px auto"></div>';
  await loadWorkloads();

  let grouped;
  try {
    grouped = await fetchJSON("/api/pods");
  } catch (e) {
    podList.innerHTML = `<p class="error">Failed to load pods: ${e.message}</p>`;
    return;
  }

  podList.innerHTML = "";

  // Sort namespaces, but put kube-system last
  const namespaces = Object.keys(grouped).sort((a, b) => {
    if (a === "kube-system") return 1;
    if (b === "kube-system") return -1;
    return a.localeCompare(b);
  });

  for (const ns of namespaces) {
    const section = document.createElement("div");
    section.className = "namespace-group";
    section.innerHTML = `<h2>${ns}</h2>`;

    for (const pod of grouped[ns].sort((a, b) => a.name.localeCompare(b.name))) {
      section.appendChild(renderPod(pod));
    }
    podList.appendChild(section);
  }
}

function renderPod(pod) {
  const card = document.createElement("div");
  card.className = "pod-card";

  const header = document.createElement("div");
  header.className = "pod-header";

  const nameLink = document.createElement("a");
  nameLink.className = "pod-name";
  nameLink.textContent = pod.name;
  nameLink.href = "#";
  nameLink.onclick = (e) => {
    e.preventDefault();
    showPodDetail(pod.namespace, pod.name);
  };

  const phase = document.createElement("span");
  phase.className = `pod-phase ${pod.phase}`;
  phase.textContent = pod.phase;

  const node = document.createElement("span");
  node.className = "pod-node";
  node.textContent = pod.nodeName || "";

  header.append(nameLink, phase, node);
  card.appendChild(header);

  for (const c of pod.containers) {
    card.appendChild(renderContainer(pod, c));
  }

  return card;
}

function renderContainer(pod, container) {
  const row = document.createElement("div");
  row.className = "container-row";
  row.dataset.ns = pod.namespace;
  row.dataset.container = container.name;

  const nameEl = document.createElement("span");
  nameEl.className = "container-name";
  nameEl.textContent = container.name;

  const imageEl = document.createElement("span");
  imageEl.className = "container-image";
  imageEl.textContent = shortImage(container.image);

  row.append(nameEl, imageEl);

  // Look up workload info for this container
  const wKey = `${pod.namespace}/${container.name}`;
  const w = workloadIndex[wKey];

  if (w && w.originalTag) {
    const tagInfo = document.createElement("span");
    tagInfo.className = "tag-info";

    const tagBadge = document.createElement("span");
    tagBadge.className = "tag-badge current";
    tagBadge.textContent = w.originalTag;
    tagInfo.appendChild(tagBadge);

    const checkBtn = document.createElement("button");
    checkBtn.className = "check-btn";
    checkBtn.textContent = "Check";
    checkBtn.onclick = () => checkForUpdate(row, w, tagInfo, checkBtn);
    tagInfo.appendChild(checkBtn);

    row.appendChild(tagInfo);
  }

  return row;
}

async function checkForUpdate(row, workload, tagInfo, checkBtn) {
  checkBtn.innerHTML = '<span class="spinner"></span>';
  checkBtn.disabled = true;

  try {
    const result = await fetchJSON("/api/check-tag", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        registry: workload.registry,
        repository: workload.repository,
        tag: workload.originalTag,
        currentDigest: workload.currentDigest,
      }),
    });

    // Remove the check button
    checkBtn.remove();

    if (result.upToDate) {
      const badge = document.createElement("span");
      badge.className = "tag-badge latest";
      badge.textContent = "up to date";
      tagInfo.appendChild(badge);
    } else {
      const badge = document.createElement("span");
      badge.className = "tag-badge outdated";
      badge.textContent = "update available";
      tagInfo.appendChild(badge);

      const upgradeBtn = document.createElement("button");
      upgradeBtn.className = "upgrade-btn";
      upgradeBtn.textContent = "Upgrade";
      upgradeBtn.onclick = () => doUpgrade(workload, upgradeBtn, badge);
      tagInfo.appendChild(upgradeBtn);
    }
  } catch (e) {
    checkBtn.textContent = "Error";
    checkBtn.title = e.message;
    checkBtn.disabled = false;
  }
}

async function doUpgrade(workload, btn, badge) {
  if (!confirm(`Upgrade ${workload.name} (${workload.originalTag}) in ${workload.file}?\n\nThis will update the YAML, git push, and kubectl apply.`)) {
    return;
  }

  btn.innerHTML = '<span class="spinner"></span>';
  btn.disabled = true;

  try {
    const result = await fetchJSON("/api/upgrade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file: workload.file,
        containerName: workload.name,
        registry: workload.registry,
        repository: workload.repository,
        tag: workload.originalTag,
      }),
    });

    btn.remove();
    badge.className = "tag-badge latest";
    badge.textContent = result.status === "already_up_to_date" ? "up to date" : "upgraded";
  } catch (e) {
    btn.textContent = "Failed";
    btn.title = e.message;
    btn.disabled = false;
    alert(`Upgrade failed: ${e.message}`);
  }
}

// --- Modal ---

let currentTab = "describe";

async function showPodDetail(namespace, name) {
  modal.classList.remove("hidden");
  modalTitle.textContent = `${namespace}/${name}`;
  modalBody.textContent = "Loading...";
  currentTab = "describe";
  updateTabUI();
  await loadTabContent(namespace, name);
}

function updateTabUI() {
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === currentTab);
  });
}

async function loadTabContent(namespace, name) {
  modalBody.textContent = "Loading...";
  const endpoint = currentTab === "describe" ? "describe" : "logs";
  try {
    const data = await fetchJSON(`/api/pods/${namespace}/${name}/${endpoint}`);
    modalBody.textContent = data.output;
  } catch (e) {
    modalBody.innerHTML = `<span class="error">${e.message}</span>`;
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    currentTab = tab.dataset.tab;
    updateTabUI();
    const [namespace, name] = modalTitle.textContent.split("/");
    loadTabContent(namespace, name);
  };
});

$("#modal-close").onclick = () => modal.classList.add("hidden");
modal.onclick = (e) => {
  if (e.target === modal) modal.classList.add("hidden");
};
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") modal.classList.add("hidden");
});

// --- Init ---

$("#refresh-btn").onclick = loadPods;
loadPods();
