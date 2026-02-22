const CONFIG = {
  nodesUrl: "/out/nodes.geojson",
  coverageMetaUrl: "/out/coverage_meta.json",
  nodeOverlayIndexUrl: "/out/node_overlays/index.json",
  forcePngOverlay: false,
  tileUrlTemplate: "/out/tiles/{z}/{x}/{y}.png",
  tileMaxZoom: 18,
};

const map = L.map("map").setView([50.08, 14.44], 11);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

let coverageLayer = null;
let aggregateMeta = null;
let aggregateMode = "color";
let nodeOverlayIndex = null;
const selectedNodeOverlays = new Map();
const selectedNodeIds = new Set();
const nodeMarkerById = new Map();
let nodeEntries = [];

const opacitySlider = document.getElementById("opacity");
const opacityValue = document.getElementById("opacityValue");
const resetNodeCoverageBtn = document.getElementById("resetNodeCoverage");
const selectedNodeInfo = document.getElementById("selectedNodeInfo");
const nodeSearch = document.getElementById("nodeSearch");
const nodesList = document.getElementById("nodesList");
const aggregateModeSelect = document.getElementById("aggregateMode");

function toWebOutPath(absOrRel) {
  return String(absOrRel || "").replace(/^.*out\//, "/out/");
}

function setCoverageOpacity(v) {
  if (coverageLayer) {
    if (coverageLayer.setOpacity) coverageLayer.setOpacity(v);
    if (coverageLayer.setStyle) coverageLayer.setStyle({ opacity: v, fillOpacity: v });
  }
  for (const layer of selectedNodeOverlays.values()) {
    if (layer.setOpacity) layer.setOpacity(v);
  }
}

function setAggregateCoverageVisible(visible) {
  if (!coverageLayer) return;
  const has = map.hasLayer(coverageLayer);
  if (visible && !has) {
    coverageLayer.addTo(map);
  } else if (!visible && has) {
    map.removeLayer(coverageLayer);
  }
}

function removeAggregateLayer() {
  if (coverageLayer && map.hasLayer(coverageLayer)) {
    map.removeLayer(coverageLayer);
  }
  coverageLayer = null;
}

function buildAggregateLayer() {
  if (!aggregateMeta) return;
  removeAggregateLayer();

  const b = aggregateMeta.bounds_epsg4326;
  const bounds = [[b.south, b.west], [b.north, b.east]];

  if (aggregateMode === "red") {
    const src = aggregateMeta.coverage_binary_png || aggregateMeta.coverage_png;
    coverageLayer = L.imageOverlay(toWebOutPath(src), bounds, {
      opacity: Number(opacitySlider.value),
    }).addTo(map);
    return;
  }

  if (!CONFIG.forcePngOverlay) {
    coverageLayer = L.tileLayer(CONFIG.tileUrlTemplate, {
      opacity: Number(opacitySlider.value),
      maxZoom: CONFIG.tileMaxZoom,
      tms: false,
    });
    coverageLayer.on("tileerror", () => {
      if (!coverageLayer || coverageLayer._fallback) return;
      coverageLayer._fallback = true;
      map.removeLayer(coverageLayer);
      coverageLayer = L.imageOverlay(toWebOutPath(aggregateMeta.coverage_png), bounds, {
        opacity: Number(opacitySlider.value),
      }).addTo(map);
    });
    coverageLayer.addTo(map);
  } else {
    coverageLayer = L.imageOverlay(toWebOutPath(aggregateMeta.coverage_png), bounds, {
      opacity: Number(opacitySlider.value),
    }).addTo(map);
  }
}

function updateSelectedNodeInfo() {
  if (selectedNodeIds.size === 0) {
    selectedNodeInfo.textContent = "Rezim: agregovane pokryti";
    return;
  }

  const names = [];
  for (const nodeId of selectedNodeIds) {
    const found = nodeEntries.find((n) => n.id === nodeId);
    if (found) names.push(found.name || nodeId);
    if (names.length >= 3) break;
  }
  const suffix = selectedNodeIds.size > 3 ? ", ..." : "";
  selectedNodeInfo.textContent = `Rezim: ${selectedNodeIds.size} node(s) [${names.join(", ")}${suffix}]`;
}

function updateMarkerStyle(nodeId) {
  const marker = nodeMarkerById.get(nodeId);
  if (!marker) return;
  const selected = selectedNodeIds.has(nodeId);
  marker.setStyle(
    selected
      ? { radius: 6, weight: 2, color: "#0b3d91", fillColor: "#00c2ff", fillOpacity: 1 }
      : { radius: 5, weight: 1, color: "#123", fillColor: "#ff8c42", fillOpacity: 0.9 }
  );
}

function renderNodesList() {
  const q = nodeSearch.value.trim().toLowerCase();
  const filtered = nodeEntries.filter((n) => {
    if (!q) return true;
    return n.id.toLowerCase().includes(q) || n.name.toLowerCase().includes(q);
  });

  nodesList.innerHTML = "";
  for (const n of filtered) {
    const row = document.createElement("label");
    row.className = "node-item";
    row.innerHTML = `
      <input type="checkbox" data-node-id="${n.id}" ${selectedNodeIds.has(n.id) ? "checked" : ""} />
      <div>
        <div>${n.name}</div>
        <div class="node-item-id">${n.id}</div>
      </div>
    `;

    const cb = row.querySelector("input");
    cb.addEventListener("change", async (e) => {
      await setNodeSelection(n.id, e.target.checked);
    });

    nodesList.appendChild(row);
  }
}

async function loadNodeOverlayIndex() {
  if (nodeOverlayIndex) return nodeOverlayIndex;
  const res = await fetch(CONFIG.nodeOverlayIndexUrl);
  if (!res.ok) return null;
  nodeOverlayIndex = await res.json();
  return nodeOverlayIndex;
}

async function setNodeSelection(nodeId, selected) {
  const idx = await loadNodeOverlayIndex();
  if (!idx || !idx.nodes || !idx.nodes[nodeId]) {
    selectedNodeInfo.textContent = `Node ${nodeId}: overlay nenalezen`;
    return;
  }

  if (selected) {
    if (!selectedNodeOverlays.has(nodeId)) {
      const entry = idx.nodes[nodeId];
      const b = entry.bounds_epsg4326;
      const bounds = [[b.south, b.west], [b.north, b.east]];
      const layer = L.imageOverlay(toWebOutPath(entry.png), bounds, {
        opacity: Number(opacitySlider.value),
      }).addTo(map);
      selectedNodeOverlays.set(nodeId, layer);
    }
    selectedNodeIds.add(nodeId);
  } else {
    const layer = selectedNodeOverlays.get(nodeId);
    if (layer) {
      map.removeLayer(layer);
      selectedNodeOverlays.delete(nodeId);
    }
    selectedNodeIds.delete(nodeId);
  }

  setAggregateCoverageVisible(selectedNodeIds.size === 0);
  updateMarkerStyle(nodeId);
  updateSelectedNodeInfo();
  renderNodesList();
}

async function toggleNodeSelection(nodeId) {
  const next = !selectedNodeIds.has(nodeId);
  await setNodeSelection(nodeId, next);
}

function resetNodeCoverage() {
  for (const layer of selectedNodeOverlays.values()) {
    map.removeLayer(layer);
  }
  selectedNodeOverlays.clear();
  selectedNodeIds.clear();
  setAggregateCoverageVisible(true);
  for (const nodeId of nodeMarkerById.keys()) updateMarkerStyle(nodeId);
  updateSelectedNodeInfo();
  renderNodesList();
}

opacitySlider.addEventListener("input", (e) => {
  const v = Number(e.target.value);
  opacityValue.textContent = v.toFixed(2);
  setCoverageOpacity(v);
});

resetNodeCoverageBtn.addEventListener("click", () => {
  resetNodeCoverage();
});

nodeSearch.addEventListener("input", () => {
  renderNodesList();
});

async function loadNodes() {
  const res = await fetch(CONFIG.nodesUrl);
  if (!res.ok) return;
  const geojson = await res.json();

  const layer = L.geoJSON(geojson, {
    pointToLayer: (feature, latlng) =>
      L.circleMarker(latlng, {
        radius: 5,
        weight: 1,
        color: "#123",
        fillColor: "#ff8c42",
        fillOpacity: 0.9,
      }),
    onEachFeature: (feature, layer) => {
      const p = feature.properties || {};
      const nodeId = String(p.id || "");
      const nodeName = String(p.name || nodeId);
      nodeMarkerById.set(nodeId, layer);
      nodeEntries.push({ id: nodeId, name: nodeName });

      layer.bindPopup(
        `Nazev: ${nodeName}<br>ID: ${nodeId}<br>height_m: ${p.height_m ?? "n/a"}<br><button type="button" onclick="window.__toggleNodeCoverage('${nodeId}')">Prepnout pokryti tohoto nodu</button>`
      );
      layer.on("click", async () => {
        await toggleNodeSelection(nodeId);
      });
    },
  }).addTo(map);

  nodeEntries.sort((a, b) => a.name.localeCompare(b.name, "cs"));
  renderNodesList();

  const b = layer.getBounds();
  if (b.isValid()) map.fitBounds(b.pad(0.2));
}

async function loadCoverage() {
  const res = await fetch(CONFIG.coverageMetaUrl);
  if (!res.ok) return;
  aggregateMeta = await res.json();
  buildAggregateLayer();
}

function renderLegend() {
  const values = [0, 1, 5, 10, 20, 50, 100];
  const colors = ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#35b779", "#fde725"];
  const legend = document.getElementById("legend");

  legend.innerHTML = "";
  for (let i = 0; i < values.length; i++) {
    const row = document.createElement("div");
    row.className = "legend-item";
    row.innerHTML = `<span class="swatch" style="background:${colors[i]}"></span><span>${values[i]}+</span>`;
    legend.appendChild(row);
  }
}

(async () => {
  window.__toggleNodeCoverage = (nodeId) => toggleNodeSelection(String(nodeId || ""));
  aggregateModeSelect.addEventListener("change", () => {
    aggregateMode = aggregateModeSelect.value === "red" ? "red" : "color";
    if (selectedNodeIds.size === 0) {
      buildAggregateLayer();
    }
  });
  renderLegend();
  updateSelectedNodeInfo();
  await Promise.all([loadNodes(), loadCoverage()]);
})();
