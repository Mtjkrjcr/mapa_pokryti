const CONFIG = {
  nodesUrl: "/out/nodes.geojson",
  coverageMetaUrl: "/out/coverage_meta.json",
  nodeOverlayIndexUrl: "/out/node_overlays/index.json",
  gsmwebCsvUrl: "/data/gsmweb_lte/all_operators_lte_b20_utf8_with_coords.csv",
  forcePngOverlay: false,
  tileUrlTemplate: "/out/tiles/{z}/{x}/{y}.png",
  tileMaxZoom: 18,
  gsmMaxVisiblePoints: 8000,
  gsmInterferenceRadiusM: 5000,
};

const map = L.map("map").setView([50.08, 14.44], 11);
map.createPane("lteB20Pane");
map.getPane("lteB20Pane").style.zIndex = "350";

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
let gsmEntries = [];
let gsmLoadPromise = null;
let gsmLoaded = false;
let gsmLoadError = null;
let gsmMarkersVisible = 0;
let gsmMarkersMatched = 0;
const gsmLayerGroup = L.layerGroup();
const gsmCanvasRenderer = L.canvas({ padding: 0.2, pane: "lteB20Pane" });
const nodeCoords = [];
let nodesGeoJsonLayer = null;
const GSM_COLORS = {
  o2: "#246bce",
  tmobile: "#e20074",
  vodafone: "#d40000",
};

const opacitySlider = document.getElementById("opacity");
const opacityValue = document.getElementById("opacityValue");
const resetNodeCoverageBtn = document.getElementById("resetNodeCoverage");
const selectedNodeInfo = document.getElementById("selectedNodeInfo");
const nodeSearch = document.getElementById("nodeSearch");
const nodesList = document.getElementById("nodesList");
const aggregateModeSelect = document.getElementById("aggregateMode");
const gsmPointsEnabled = document.getElementById("gsmPointsEnabled");
const gsmOperatorFilter = document.getElementById("gsmOperatorFilter");
const gsmSearch = document.getElementById("gsmSearch");
const gsmStatus = document.getElementById("gsmStatus");

function toWebOutPath(absOrRel) {
  return String(absOrRel || "").replace(/^.*out\//, "/out/");
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function parseCsvLine(line, delimiter = ";") {
  const out = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (ch === delimiter && !inQuotes) {
      out.push(cur);
      cur = "";
      continue;
    }
    cur += ch;
  }
  out.push(cur);
  return out;
}

function parseSemicolonCsv(text) {
  const lines = String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n")
    .filter((line) => line.length > 0);
  if (!lines.length) return [];
  const headers = parseCsvLine(lines[0], ";");
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const cols = parseCsvLine(lines[i], ";");
    if (!cols.length) continue;
    const row = {};
    for (let j = 0; j < headers.length; j++) {
      row[headers[j]] = cols[j] ?? "";
    }
    rows.push(row);
  }
  return rows;
}

function formatOperator(op) {
  if (op === "o2") return "O2";
  if (op === "tmobile") return "T-Mobile";
  if (op === "vodafone") return "Vodafone";
  return op || "n/a";
}

function buildGsmPopupHtml(entry) {
  return [
    `<b>${escapeHtml(formatOperator(entry.operator))}</b>`,
    `LTE band: B20 / ${escapeHtml(entry.Band || "800")} MHz`,
    `CellID: ${escapeHtml(entry.CellID)}`,
    `PhysCID: ${escapeHtml(entry.PhysCID)}`,
    `TAC: ${escapeHtml(entry.TAC)}`,
    `GSMCID: ${escapeHtml(entry.GSMCID || "")}`,
    `Datum: ${escapeHtml(entry.Datum)}`,
    `Okres: ${escapeHtml(entry.Okr)}`,
    `Umisteni: ${escapeHtml(entry.Umisteni)}`,
    `GPS: ${entry.lat.toFixed(6)}, ${entry.lon.toFixed(6)}`,
  ].join("<br>");
}

function updateGsmStatus(text) {
  gsmStatus.textContent = text;
}

function getGsmFilterQuery() {
  return gsmSearch.value.trim().toLowerCase();
}

function haversineMeters(lat1, lon1, lat2, lon2) {
  const toRad = Math.PI / 180;
  const dLat = (lat2 - lat1) * toRad;
  const dLon = (lon2 - lon1) * toRad;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.sin(dLon / 2) ** 2;
  return 6371000 * 2 * Math.asin(Math.sqrt(a));
}

function isPotentialInterfererForNode(entry) {
  // LTE dataset is already B20-only; keep only nearby sites around our nodes.
  if (!nodeCoords.length) return false;
  if (typeof entry._isInterferer === "boolean") return entry._isInterferer;

  const radiusM = CONFIG.gsmInterferenceRadiusM;
  const latTol = radiusM / 111320;
  const lonTolBase = radiusM / 111320;
  for (const n of nodeCoords) {
    const dLat = Math.abs(entry.lat - n.lat);
    if (dLat > latTol) continue;
    const cosLat = Math.max(0.2, Math.cos((entry.lat * Math.PI) / 180));
    const lonTol = lonTolBase / cosLat;
    const dLon = Math.abs(entry.lon - n.lon);
    if (dLon > lonTol) continue;
    if (haversineMeters(entry.lat, entry.lon, n.lat, n.lon) <= radiusM) {
      entry._isInterferer = true;
      return true;
    }
  }
  entry._isInterferer = false;
  return false;
}

async function loadGsmwebPoints() {
  if (gsmLoaded) return gsmEntries;
  if (gsmLoadPromise) return gsmLoadPromise;

  gsmLoadPromise = (async () => {
    updateGsmStatus("LTE800/B20 body: nacitam CSV...");
    const res = await fetch(CONFIG.gsmwebCsvUrl);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status} pri nacteni ${CONFIG.gsmwebCsvUrl}`);
    }
    const text = await res.text();
    const rows = parseSemicolonCsv(text);
    gsmEntries = rows
      .map((r) => {
        const lat = Number(r.lat);
        const lon = Number(r.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
        return {
          ...r,
          lat,
          lon,
          operator: String(r.operator || "").toLowerCase(),
          _search: [
            r.operator,
            r.CellID,
            r.PhysCID,
            r.TAC,
            r.Band,
            r.GSMCID,
            r.Datum,
            r.Okr,
            r.Umisteni,
          ]
            .join(" ")
            .toLowerCase(),
        };
      })
      .filter(Boolean);
    gsmLoaded = true;
    gsmLoadError = null;
    updateGsmStatus(`LTE800/B20 body: nacteno ${gsmEntries.length} zaznamu`);
    return gsmEntries;
  })().catch((err) => {
    gsmLoadError = err;
    updateGsmStatus(`LTE800/B20 body: chyba (${err.message})`);
    throw err;
  });

  return gsmLoadPromise;
}

function clearGsmMarkers() {
  gsmLayerGroup.clearLayers();
  gsmMarkersVisible = 0;
  gsmMarkersMatched = 0;
}

function renderGsmPoints() {
  if (!gsmPointsEnabled.checked) {
    if (map.hasLayer(gsmLayerGroup)) map.removeLayer(gsmLayerGroup);
    clearGsmMarkers();
    updateGsmStatus("LTE800/B20 body: vypnuto");
    return;
  }
  if (gsmLoadError) {
    updateGsmStatus(`LTE800/B20 body: chyba (${gsmLoadError.message})`);
    return;
  }
  if (!gsmLoaded) {
    updateGsmStatus("LTE800/B20 body: nacitam CSV...");
    return;
  }

  const op = gsmOperatorFilter.value || "all";
  const q = getGsmFilterQuery();
  const bounds = map.getBounds();
  const maxVisible = CONFIG.gsmMaxVisiblePoints;

  clearGsmMarkers();

  let clipped = 0;
  for (const entry of gsmEntries) {
    if (!isPotentialInterfererForNode(entry)) continue;
    if (op !== "all" && entry.operator !== op) continue;
    if (q && !entry._search.includes(q)) continue;
    gsmMarkersMatched++;
    if (!bounds.contains([entry.lat, entry.lon])) continue;
    if (gsmMarkersVisible >= maxVisible) {
      clipped++;
      continue;
    }

    const color = GSM_COLORS[entry.operator] || "#666";
    const marker = L.circleMarker([entry.lat, entry.lon], {
      renderer: gsmCanvasRenderer,
      pane: "lteB20Pane",
      radius: 3.5,
      weight: 1,
      color,
      fillColor: color,
      fillOpacity: 0.65,
    });
    marker.bindPopup(buildGsmPopupHtml(entry));
    gsmLayerGroup.addLayer(marker);
    gsmMarkersVisible++;
  }

  if (!map.hasLayer(gsmLayerGroup)) gsmLayerGroup.addTo(map);
  if (nodesGeoJsonLayer && nodesGeoJsonLayer.bringToFront) nodesGeoJsonLayer.bringToFront();

  const clippedSuffix = clipped > 0 ? ` (omezeno na ${maxVisible} ve vyrezu)` : "";
  const qSuffix = q ? `; text filtr: "${q}"` : "";
  updateGsmStatus(
    `LTE800/B20 kandidati ruseni (<= ${Math.round(CONFIG.gsmInterferenceRadiusM / 1000)} km od nodu): ${gsmMarkersVisible} zobrazeno ve vyrezu / ${gsmMarkersMatched} odpovida filtru${clippedSuffix}${qSuffix}`
  );
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

gsmPointsEnabled.addEventListener("change", async () => {
  if (gsmPointsEnabled.checked && !gsmLoaded && !gsmLoadError) {
    try {
      await loadGsmwebPoints();
    } catch {
      return;
    }
  }
  renderGsmPoints();
});

gsmOperatorFilter.addEventListener("change", () => {
  renderGsmPoints();
});

let gsmSearchTimer = null;
gsmSearch.addEventListener("input", () => {
  if (gsmSearchTimer) clearTimeout(gsmSearchTimer);
  gsmSearchTimer = setTimeout(() => {
    renderGsmPoints();
  }, 180);
});

map.on("moveend zoomend", () => {
  if (gsmPointsEnabled.checked && gsmLoaded) {
    renderGsmPoints();
  }
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
      const ll = layer.getLatLng ? layer.getLatLng() : null;
      nodeMarkerById.set(nodeId, layer);
      nodeEntries.push({ id: nodeId, name: nodeName, lat: ll?.lat, lon: ll?.lng });
      if (ll && Number.isFinite(ll.lat) && Number.isFinite(ll.lng)) {
        nodeCoords.push({ lat: ll.lat, lon: ll.lng });
      }

      layer.bindPopup(
        `Nazev: ${nodeName}<br>ID: ${nodeId}<br>height_m: ${p.height_m ?? "n/a"}<br><button type="button" onclick="window.__toggleNodeCoverage('${nodeId}')">Prepnout pokryti tohoto nodu</button>`
      );
      layer.on("click", async () => {
        await toggleNodeSelection(nodeId);
      });
    },
  }).addTo(map);
  nodesGeoJsonLayer = layer;
  if (nodesGeoJsonLayer.bringToFront) nodesGeoJsonLayer.bringToFront();

  nodeEntries.sort((a, b) => a.name.localeCompare(b.name, "cs"));
  renderNodesList();
  if (gsmPointsEnabled.checked && gsmLoaded) renderGsmPoints();

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
