frappe.pages["run-detail"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, single_column: true });
  $(wrapper).find(".page-head").hide();
  $('<div id="rd-root"></div>').appendTo(wrapper);

  const params = new URLSearchParams(window.location.search);
  const name = params.get("name");
  if (!name) { $("#rd-root").html('<p style="color:#aaa;padding:2rem">No run specified.</p>'); return; }

  frappe.call({
    method: "frappe.client.get",
    args: { doctype: "Run", name },
    callback: (r) => {
      if (!r.message) { $("#rd-root").html('<p style="color:#aaa;padding:2rem">Run not found.</p>'); return; }
      window.rdRun = r.message;
      rdRender(r.message);
    }
  });
};

function rdRender(run) {
  const pts = rdParsePoints(run.route_points);
  const splits = rdCalcSplits(pts, run.duration_sec, run.distance_km);
  const title = rdMakeTitle(run.date, run.location, run.activity_type);
  const dateStr = frappe.datetime.str_to_user(run.date);
  const dist = (run.distance_km || 0).toFixed(2);
  const dur = rdFmtDuration(run.duration_sec);
  const pace = rdFmtPace(run.distance_km, run.duration_sec);
  const elev = Math.round(run.elevation_gain || 0);
  const cal = Math.round(run.calories || 0);
  const actIcon = { Run: "🏃", Walk: "🚶", Swim: "🏊", Ride: "🚴" }[run.activity_type] || "🏃";
  const hasAlt = pts.length > 0 && pts[0].alt != null;

  $("#rd-root").html(`
    <div class="rd-wrap">
      <div class="rd-header">
        <div class="rd-title-block">
          <span class="rd-icon">${actIcon}</span>
          <div>
            <div class="rd-title">${title}</div>
            <div class="rd-meta">${dateStr} &nbsp;·&nbsp; ${run.location || "Unknown location"}</div>
          </div>
        </div>
        <div class="rd-actions">
          <button onclick="window.history.back()" class="rd-btn-back">← Back</button>
          <button onclick="rdShare()" class="rd-btn-share">Share</button>
        </div>
      </div>

      <div id="rd-map-wrap" class="rd-map-wrap">
        <div id="rd-map" style="width:100%;height:100%;"></div>
        ${pts.length === 0 ? '<div class="rd-map-empty">No GPS data for this run</div>' : ""}
      </div>

      <div class="rd-stats-bar">
        <div class="rd-stat">
          <div class="rd-stat-label">Distance</div>
          <div class="rd-stat-val">${dist}</div>
          <div class="rd-stat-unit">km</div>
        </div>
        <div class="rd-stat">
          <div class="rd-stat-label">Time</div>
          <div class="rd-stat-val">${dur}</div>
          <div class="rd-stat-unit">h:m:s</div>
        </div>
        <div class="rd-stat rd-stat-accent">
          <div class="rd-stat-label">Avg Pace</div>
          <div class="rd-stat-val">${pace}</div>
          <div class="rd-stat-unit">/km</div>
        </div>
        <div class="rd-stat">
          <div class="rd-stat-label">Elevation</div>
          <div class="rd-stat-val">${elev}</div>
          <div class="rd-stat-unit">m gain</div>
        </div>
        <div class="rd-stat">
          <div class="rd-stat-label">Calories</div>
          <div class="rd-stat-val">${cal}</div>
          <div class="rd-stat-unit">kcal</div>
        </div>
      </div>

      ${hasAlt ? `
      <div class="rd-section">
        <div class="rd-section-label">Elevation Profile</div>
        <div id="rd-elev-chart"></div>
      </div>` : ""}

      ${splits.length > 0 ? `
      <div class="rd-section">
        <div class="rd-section-label">Km Splits</div>
        <div class="rd-splits" id="rd-splits"></div>
      </div>` : ""}

      <div class="rd-section">
        <div class="rd-section-label">Notes</div>
        <textarea id="rd-notes" class="rd-notes" placeholder="Add notes…">${run.notes || ""}</textarea>
        <button onclick="rdSaveNotes()" class="rd-save-btn" id="rd-save-btn">Save</button>
      </div>
    </div>
  `);

  if (pts.length > 0) rdInitMap(pts);
  if (hasAlt) rdDrawElevation(pts);
  if (splits.length > 0) rdRenderSplits(splits);
  window.rdCurrentRun = run.name;
}

function rdInitMap(pts) {
  if (typeof L === "undefined") {
    const s = document.createElement("link");
    s.rel = "stylesheet";
    s.href = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css";
    document.head.appendChild(s);
    const sc = document.createElement("script");
    sc.src = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js";
    sc.onload = () => rdBuildMap(pts);
    document.head.appendChild(sc);
  } else {
    rdBuildMap(pts);
  }
}

function rdBuildMap(pts) {
  const latlngs = pts.map(p => [p.lat, p.lng]);
  const map = L.map("rd-map", { zoomControl: true, scrollWheelZoom: false });
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: "© CartoDB",
    maxZoom: 18
  }).addTo(map);
  const poly = L.polyline(latlngs, { color: "#e86400", weight: 3, opacity: 0.9 }).addTo(map);
  map.fitBounds(poly.getBounds(), { padding: [30, 30] });
  const mkStart = L.circleMarker(latlngs[0], { radius: 7, color: "#fff", fillColor: "#22c55e", fillOpacity: 1, weight: 2 }).addTo(map);
  const mkEnd = L.circleMarker(latlngs[latlngs.length - 1], { radius: 7, color: "#fff", fillColor: "#ef4444", fillOpacity: 1, weight: 2 }).addTo(map);
  mkStart.bindTooltip("Start");
  mkEnd.bindTooltip("End");
}

function rdDrawElevation(pts) {
  const alts = pts.map(p => p.alt);
  const mn = Math.min(...alts), mx = Math.max(...alts);
  const W = 820, H = 80, pad = 4;
  const sx = i => (i / (pts.length - 1)) * W;
  const sy = a => H - pad - ((a - mn) / (mx - mn || 1)) * (H - pad * 2);
  let d = `M0,${sy(alts[0])}`;
  for (let i = 1; i < alts.length; i++) d += ` L${sx(i).toFixed(1)},${sy(alts[i]).toFixed(1)}`;
  const fill = d + ` L${W},${H} L0,${H} Z`;
  $("#rd-elev-chart").html(`
    <svg width="100%" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="display:block;">
      <defs><linearGradient id="rdeg" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#e86400" stop-opacity="0.35"/>
        <stop offset="100%" stop-color="#e86400" stop-opacity="0.03"/>
      </linearGradient></defs>
      <path d="${fill}" fill="url(#rdeg)"/>
      <path d="${d}" fill="none" stroke="#e86400" stroke-width="2"/>
    </svg>
    <div style="display:flex;justify-content:space-between;font-size:11px;color:#666;margin-top:2px;">
      <span>${Math.round(mn)}m</span><span>${Math.round(mx)}m</span>
    </div>
  `);
}

function rdCalcSplits(pts, totalSec, totalKm) {
  if (!pts.length || !totalSec || !totalKm) return [];
  const totalDist = totalKm * 1000;
  const splits = [];
  let cumDist = 0, kmStart = 0, tStart = 0;
  for (let i = 1; i < pts.length; i++) {
    const seg = rdHaversine(pts[i - 1], pts[i]);
    cumDist += seg;
    while (cumDist >= (splits.length + 1) * 1000 && splits.length < Math.floor(totalKm)) {
      const kmIdx = splits.length + 1;
      const tEnd = (cumDist / totalDist) * totalSec;
      splits.push({ km: kmIdx, sec: tEnd - tStart });
      tStart = tEnd;
    }
  }
  return splits;
}

function rdRenderSplits(splits) {
  const secs = splits.map(s => s.sec);
  const best = Math.min(...secs);
  const avg = secs.reduce((a, b) => a + b, 0) / secs.length;
  const html = splits.map(s => {
    const cls = s.sec === best ? "rd-split-best" : s.sec < avg ? "rd-split-fast" : "";
    return `<div class="rd-split ${cls}">
      <div class="rd-split-km">km ${s.km}</div>
      <div class="rd-split-pace">${rdFmtPaceSec(s.sec)}</div>
    </div>`;
  }).join("");
  $("#rd-splits").html(html);
}

function rdSaveNotes() {
  const notes = $("#rd-notes").val();
  $("#rd-save-btn").text("Saving…");
  frappe.call({
    method: "frappe.client.set_value",
    args: { doctype: "Run", name: window.rdCurrentRun, fieldname: "notes", value: notes },
    callback: () => { $("#rd-save-btn").text("Saved ✓"); setTimeout(() => $("#rd-save-btn").text("Save"), 2000); }
  });
}

function rdShare() {
  const url = window.location.href;
  if (navigator.clipboard) {
    navigator.clipboard.writeText(url).then(() => frappe.msgprint("Link copied to clipboard"));
  } else {
    frappe.msgprint(url);
  }
}

function rdMakeTitle(date, location, actType) {
  if (!date) return "Run";
  const d = new Date(date);
  const h = d.getHours();
  const tod = h < 11 ? "Morning" : h < 14 ? "Midday" : h < 18 ? "Afternoon" : "Evening";
  const act = actType || "Run";
  const loc = location ? ` — ${location.split(",")[0]}` : "";
  return `${tod} ${act}${loc}`;
}

function rdFmtDuration(sec) {
  if (!sec) return "0:00:00";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function rdFmtPace(km, sec) {
  if (!km || !sec) return "--:--";
  return rdFmtPaceSec(sec / km);
}

function rdFmtPaceSec(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function rdParsePoints(raw) {
  if (!raw) return [];
  try {
    const data = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (!Array.isArray(data)) return [];
    return data.map(p => ({
      lat: p.lat ?? p[0],
      lng: p.lng ?? p.lon ?? p[1],
      alt: p.alt ?? p.altitude ?? p.ele ?? p[2] ?? null
    })).filter(p => p.lat != null && p.lng != null);
  } catch { return []; }
}

function rdHaversine(a, b) {
  const R = 6371000, toR = x => x * Math.PI / 180;
  const dLat = toR(b.lat - a.lat), dLon = toR(b.lng - a.lng);
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(toR(a.lat)) * Math.cos(toR(b.lat)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
}