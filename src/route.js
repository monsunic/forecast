/*
 * Route Forecast — time-dependent A* over the vessel lane graph.
 *
 * The pipeline publishes assets/routes/forecast.json (lane geometry + per-edge
 * conditions on the F000…F072 axis) and assets/routes/vessels.json. Everything
 * below runs in the browser so departure time, vessel, and optimization mode
 * can be changed without a backend.
 *
 * The pure planning functions are exported and DOM-free so they can be unit
 * tested under Node; the UI half only wires up when a document exists.
 */

const EARTH_RADIUS_NM = 3440.065;
const DEG = Math.PI / 180;

// Currents can push a vessel over its calm-water speed, so the A* heuristic
// must assume this much help to stay admissible.
const MAX_CURRENT_ASSIST_KT = 3.0;

// Hazard weighting per mode: cost = hours * (1 + weight * hazard).
// Fastest ignores weather exposure (waves still slow the ship via speed).
// Balanced / safest pay increasing penalties for rough water so tracks diverge.
export const MODE_WEIGHTS = {
    fastest: 0,
    balanced: 2.0,
    safest: 9.0,
};

export function haversineNm(lat1, lon1, lat2, lon2) {
    const p1 = lat1 * DEG;
    const p2 = lat2 * DEG;
    const dPhi = p2 - p1;
    const dLam = (lon2 - lon1) * DEG;
    const a = Math.sin(dPhi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dLam / 2) ** 2;
    return 2 * EARTH_RADIUS_NM * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function bearingDeg(lat1, lon1, lat2, lon2) {
    const p1 = lat1 * DEG;
    const p2 = lat2 * DEG;
    const dLam = (lon2 - lon1) * DEG;
    const y = Math.sin(dLam) * Math.cos(p2);
    const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dLam);
    return (Math.atan2(y, x) / DEG + 360) % 360;
}

/** Signed component of a vector along a course, in the vector's own units. */
function alongCourse(speed, dirDeg, courseDeg) {
    if (speed == null || dirDeg == null) return 0;
    return speed * Math.cos((dirDeg - courseDeg) * DEG);
}

/**
 * Speed over ground on a leg.
 *
 * Hull speed drops with wave height (quadratic added resistance) and headwind,
 * bounded below by the vessel's minimum steerage speed. Current is then applied
 * as a ground-speed offset, which is why a favourable set can beat calm speed.
 */
export function effectiveSpeedKt(profile, conditions, courseDeg) {
    const calm = profile.calm_speed_kt;
    const swh = conditions.swh ?? 0;
    // wind_dir is the meteorological FROM bearing: equal to the course means
    // the wind is dead ahead.
    const headwind = alongCourse(conditions.wind_speed, conditions.wind_dir, courseDeg);
    // current_dir is the oceanographic TOWARD bearing: equal to the course
    // means the current is pushing the vessel forward.
    const setAlong = alongCourse(conditions.current, conditions.current_dir, courseDeg);

    const hull = Math.min(
        calm,
        Math.max(
            profile.min_speed_kt,
            calm - profile.wave_coeff * swh * swh - profile.wind_coeff * headwind
        )
    );
    return Math.max(profile.min_speed_kt * 0.5, hull + profile.current_coeff * setAlong);
}

/**
 * Exposure of a leg to metocean stress.
 *
 * Continuous from calm water (not only past the comfort threshold) so safest /
 * balanced prefer quieter tracks even when nothing is formally unsafe. That is
 * what makes the three optimize-for modes produce different routes.
 */
export function hazardScore(profile, conditions) {
    const swh = conditions.swh_max ?? conditions.swh ?? 0;
    const wind = conditions.wind_speed ?? 0;
    const wave = swh / Math.max(0.5, profile.comfort_swh_m);
    const windH = wind / Math.max(1, profile.max_wind_kt * 0.55);
    return wave * wave + 0.4 * windH * windH;
}

export function isUnsafe(profile, conditions) {
    const swh = conditions.swh_max ?? conditions.swh ?? 0;
    const wind = conditions.wind_speed ?? 0;
    return swh > profile.max_swh_m || wind > profile.max_wind_kt;
}

/** Forecast lead hour of a label such as "F018". */
export function hourLead(label) {
    const n = parseInt(String(label).replace(/^[Ff]/, ''), 10);
    return Number.isFinite(n) ? n : 0;
}

/** Index the published grid field into the shape the planner walks. */
export function buildFieldIndex(field) {
    const g = field.grid || {};
    const nlon = g.nlon | 0;
    const nlat = g.nlat | 0;

    const nodes = new Map();
    const ports = [];
    (field.ports || []).forEach(p => {
        nodes.set(p.id, p);
        ports.push(p.id);
    });

    const leads = (field.hours || []).map(hourLead);
    const baseIso = (field.valid_times || []).find(Boolean) || null;

    return {
        grid: g,
        nlon,
        nlat,
        ncell: nlat * nlon,
        sea: field.sea_mask || [],
        vars: field.vars || {},
        leads,
        baseTimeMs: baseIso ? Date.parse(baseIso) : null,
        horizonHours: leads.length ? leads[leads.length - 1] : 0,
        nodes,
        ports,
        source: 'grid',
    };
}

/** Backwards-compatible alias (older callers used buildRouteIndex). */
export const buildRouteIndex = buildFieldIndex;

function cellLat(index, iy) { return index.grid.lat_min + iy * index.grid.dlat; }
function cellLon(index, ix) { return index.grid.lon_min + ix * index.grid.dlon; }

function cellCenter(index, cell) {
    const iy = Math.floor(cell / index.nlon);
    const ix = cell - iy * index.nlon;
    return [cellLat(index, iy), cellLon(index, ix)];
}

function isSea(index, cell) {
    return cell >= 0 && cell < index.ncell && index.sea[cell] === 1;
}

/** Nearest navigable cell to a coordinate, spiralling out from the port. */
function snapToSea(index, lat, lon) {
    const { nlat, nlon } = index;
    let iy = Math.round((lat - index.grid.lat_min) / index.grid.dlat);
    let ix = Math.round((lon - index.grid.lon_min) / index.grid.dlon);
    iy = Math.min(nlat - 1, Math.max(0, iy));
    ix = Math.min(nlon - 1, Math.max(0, ix));
    const c0 = iy * nlon + ix;
    if (isSea(index, c0)) return c0;

    const maxR = Math.max(nlat, nlon);
    for (let r = 1; r <= maxR; r++) {
        let best = -1;
        let bestD = Infinity;
        for (let dy = -r; dy <= r; dy++) {
            for (let dx = -r; dx <= r; dx++) {
                if (Math.max(Math.abs(dy), Math.abs(dx)) !== r) continue;
                const jy = iy + dy;
                const jx = ix + dx;
                if (jy < 0 || jy >= nlat || jx < 0 || jx >= nlon) continue;
                const c = jy * nlon + jx;
                if (!isSea(index, c)) continue;
                const [clat, clon] = cellCenter(index, c);
                const d = haversineNm(lat, lon, clat, clon);
                if (d < bestD) { bestD = d; best = c; }
            }
        }
        if (best >= 0) return best;
    }
    return -1;
}

/**
 * Conditions in one grid cell at an arbitrary lead time, linearly interpolated
 * between the bracketing forecast hours and clamped at both ends.
 */
export function fieldConditions(index, cell, leadHours) {
    const leads = index.leads;
    const out = {};
    if (!leads.length) return out;
    const t = Math.max(leads[0], Math.min(leads[leads.length - 1], leadHours));
    let hi = leads.findIndex(l => l >= t);
    if (hi < 0) hi = leads.length - 1;
    const lo = Math.max(0, hi - 1);
    const span = leads[hi] - leads[lo];
    const f = span > 0 ? (t - leads[lo]) / span : 0;

    for (const key of Object.keys(index.vars)) {
        const series = index.vars[key];
        const aArr = series[lo];
        const bArr = series[hi];
        const a = aArr ? aArr[cell] : null;
        const b = bArr ? bArr[cell] : null;
        if (a == null && b == null) out[key] = null;
        else if (a == null || b == null) out[key] = a == null ? b : a;
        else if (key.endsWith('_dir')) {
            const delta = ((b - a + 540) % 360) - 180;
            out[key] = (a + delta * f + 360) % 360;
        } else {
            out[key] = a + (b - a) * f;
        }
    }
    // One grid cell is a point, so worst-case wave equals the mean here.
    out.swh_max = out.swh;
    return out;
}

// 8-connected neighbourhood offsets.
const GRID_NEIGHBORS = [
    [-1, 0], [1, 0], [0, -1], [0, 1],
    [-1, -1], [-1, 1], [1, -1], [1, 1],
];

/** Navigable neighbours of a cell, refusing to cut across land corners. */
function seaNeighbors(index, cell) {
    const { nlat, nlon } = index;
    const iy = Math.floor(cell / nlon);
    const ix = cell - iy * nlon;
    const out = [];
    for (const [dy, dx] of GRID_NEIGHBORS) {
        const jy = iy + dy;
        const jx = ix + dx;
        if (jy < 0 || jy >= nlat || jx < 0 || jx >= nlon) continue;
        const c = jy * nlon + jx;
        if (!isSea(index, c)) continue;
        if (dy !== 0 && dx !== 0) {
            // Both orthogonal cells must be sea, else the diagonal clips land.
            if (!isSea(index, iy * nlon + jx)) continue;
            if (!isSea(index, jy * nlon + ix)) continue;
        }
        out.push(c);
    }
    return out;
}

/** Minimal binary heap; the graph is small but A* pops a lot. */
class MinHeap {
    constructor() { this.items = []; }
    get size() { return this.items.length; }
    push(priority, value) {
        const items = this.items;
        items.push({ priority, value });
        let i = items.length - 1;
        while (i > 0) {
            const parent = (i - 1) >> 1;
            if (items[parent].priority <= items[i].priority) break;
            [items[parent], items[i]] = [items[i], items[parent]];
            i = parent;
        }
    }
    pop() {
        const items = this.items;
        const top = items[0];
        const last = items.pop();
        if (items.length) {
            items[0] = last;
            let i = 0;
            for (;;) {
                const l = 2 * i + 1;
                const r = l + 1;
                let small = i;
                if (l < items.length && items[l].priority < items[small].priority) small = l;
                if (r < items.length && items[r].priority < items[small].priority) small = r;
                if (small === i) break;
                [items[small], items[i]] = [items[i], items[small]];
                i = small;
            }
        }
        return top;
    }
}

/**
 * Time-dependent grid A*.
 *
 * State is (cell, arrival lead time): each step is costed with the weather it
 * will actually meet, so a later departure across the same water can be
 * cheaper. The heuristic is remaining great-circle distance over the best speed
 * the vessel could make, which never overestimates and keeps the search tight.
 */
function searchGrid(index, profile, startCell, goalCell, departLead, mode, options = {}) {
    const prune = options.prune !== false;
    const weight = MODE_WEIGHTS[mode] ?? 0;
    const [glat, glon] = cellCenter(index, goalCell);
    const bestSpeed = profile.calm_speed_kt + MAX_CURRENT_ASSIST_KT;
    const heuristic = (cell) => {
        const [la, lo] = cellCenter(index, cell);
        return haversineNm(la, lo, glat, glon) / bestSpeed;
    };

    const bestCost = new Map([[startCell, 0]]);
    const arrival = new Map([[startCell, departLead]]);
    const cameFrom = new Map();
    const open = new MinHeap();
    open.push(heuristic(startCell), startCell);

    while (open.size) {
        const { value: cell } = open.pop();
        if (cell === goalCell) break;
        const cost = bestCost.get(cell);
        if (cost == null) continue;
        const time = arrival.get(cell);
        const [clat, clon] = cellCenter(index, cell);

        for (const nb of seaNeighbors(index, cell)) {
            const [nbLat, nbLon] = cellCenter(index, nb);
            const conditions = fieldConditions(index, nb, time);
            if (prune && isUnsafe(profile, conditions)) continue;
            const course = bearingDeg(clat, clon, nbLat, nbLon);
            const dist = haversineNm(clat, clon, nbLat, nbLon);
            const speed = effectiveSpeedKt(profile, conditions, course);
            const hours = dist / Math.max(0.5, speed);
            const nextCost = cost + hours * (1 + weight * hazardScore(profile, conditions));
            if (nextCost < (bestCost.get(nb) ?? Infinity)) {
                bestCost.set(nb, nextCost);
                arrival.set(nb, time + hours);
                cameFrom.set(nb, cell);
                open.push(nextCost + heuristic(nb), nb);
            }
        }
    }

    if (!bestCost.has(goalCell)) return null;
    const cells = [goalCell];
    let cur = goalCell;
    while (cameFrom.has(cur)) {
        cur = cameFrom.get(cur);
        cells.push(cur);
    }
    cells.reverse();
    return { cells };
}

/**
 * Plan a voyage over the grid field with dynamic, weather-aware A*.
 *
 * `departure` is a Date/ISO string; conditions are read relative to the first
 * valid time in the field. Returns null when the ports are unknown or no
 * navigable water connects them.
 */
export function planRoute({ field, forecast, index, profile, origin, destination, departure, mode = 'balanced' }) {
    const idx = index || buildFieldIndex(field || forecast);
    const o = idx.nodes.get(origin);
    const d = idx.nodes.get(destination);
    if (!o || !d || origin === destination) return null;

    const startCell = snapToSea(idx, o.lat, o.lon);
    const goalCell = snapToSea(idx, d.lat, d.lon);
    if (startCell < 0 || goalCell < 0) return null;

    const departMs = departure instanceof Date ? departure.getTime() : Date.parse(departure);
    const departLead = idx.baseTimeMs != null && Number.isFinite(departMs)
        ? (departMs - idx.baseTimeMs) / 3600000
        : 0;

    const warnings = [];
    let result = searchGrid(idx, profile, startCell, goalCell, departLead, mode, { prune: true });
    let withinLimits = true;
    if (!result) {
        result = searchGrid(idx, profile, startCell, goalCell, departLead, mode, { prune: false });
        withinLimits = false;
        if (result) {
            warnings.push(
                `No track stays within the ${profile.name} limits (${profile.max_swh_m} m / ${profile.max_wind_kt} kt). Showing the least severe option — review before sailing.`
            );
        }
    }
    if (!result) return null;

    // Vertices: exact origin port → snapped grid cells → exact destination port.
    const vertices = [[o.lat, o.lon]];
    result.cells.forEach(c => {
        const [la, lo] = cellCenter(idx, c);
        vertices.push([la, lo]);
    });
    vertices.push([d.lat, d.lon]);

    // Re-walk each micro-leg from its real departure time so conditions and
    // speed match where the vessel actually is.
    let lead = departLead;
    let distanceNm = 0;
    const coordinates = [vertices[0]];
    const stepUnsafe = [];
    const samples = [];
    const microLegs = [];

    for (let i = 0; i < vertices.length - 1; i++) {
        const [aLat, aLon] = vertices[i];
        const [bLat, bLon] = vertices[i + 1];
        const dist = haversineNm(aLat, aLon, bLat, bLon);
        if (dist < 1e-6) continue;
        const cell = snapToSea(idx, bLat, bLon);
        const conditions = cell >= 0 ? fieldConditions(idx, cell, lead) : {};
        const course = bearingDeg(aLat, aLon, bLat, bLon);
        const speed = effectiveSpeedKt(profile, conditions, course);
        const hours = dist / Math.max(0.5, speed);
        const unsafe = isUnsafe(profile, conditions);

        lead += hours;
        distanceNm += dist;
        coordinates.push([bLat, bLon]);
        stepUnsafe.push(unsafe);
        microLegs.push({ bLat, bLon, dist, hours, speed, conditions, unsafe, arriveLead: lead });
        samples.push({
            lead,
            arriveIso: leadToIso(idx, lead),
            swh: conditions.swh_max ?? conditions.swh ?? null,
            wind_speed: conditions.wind_speed ?? null,
            current: conditions.current ?? null,
            sog: speed,
        });
    }

    const totalHours = lead - departLead;

    // Collapse the many grid micro-legs into a readable table (~12 rows).
    const legs = [];
    const groupSize = Math.max(1, Math.ceil(microLegs.length / 12));
    for (let i = 0; i < microLegs.length; i += groupSize) {
        const chunk = microLegs.slice(i, i + groupSize);
        if (!chunk.length) continue;
        const dist = chunk.reduce((s, l) => s + l.dist, 0);
        const hours = chunk.reduce((s, l) => s + l.hours, 0);
        const last = chunk[chunk.length - 1];
        const swhMax = Math.max(...chunk.map(l => l.conditions.swh_max ?? l.conditions.swh ?? 0));
        const windMax = Math.max(...chunk.map(l => l.conditions.wind_speed ?? 0));
        const curVals = chunk.map(l => l.conditions.current).filter(v => v != null);
        const curAvg = curVals.length ? curVals.reduce((s, v) => s + v, 0) / curVals.length : null;
        legs.push({
            label: fmtLatLon(last.bLat, last.bLon),
            distanceNm: dist,
            speedKt: dist / Math.max(1e-6, hours),
            conditions: { swh_max: swhMax, swh: swhMax, wind_speed: windMax, current: curAvg },
            arriveIso: leadToIso(idx, last.arriveLead),
            unsafe: chunk.some(l => l.unsafe),
        });
    }
    if (legs.length) legs[legs.length - 1].label = d.name;

    if (lead > idx.horizonHours) {
        warnings.push(
            `The voyage runs past the ${idx.horizonHours} h forecast horizon; conditions after that are held at the last forecast hour.`
        );
    }
    const unsafeCount = stepUnsafe.filter(Boolean).length;
    if (unsafeCount) {
        warnings.push(
            `${unsafeCount} leg${unsafeCount === 1 ? '' : 's'} exceed the vessel limits — the plan takes the least severe water available.`
        );
    }

    return {
        mode,
        profileId: profile.id,
        origin,
        destination,
        coordinates,
        stepUnsafe,
        legs,
        samples,
        distanceNm,
        totalHours,
        departIso: leadToIso(idx, departLead),
        etaIso: leadToIso(idx, lead),
        avgSpeedKt: totalHours > 0 ? distanceNm / totalHours : 0,
        withinLimits,
        warnings,
    };
}

function fmtLatLon(lat, lon) {
    const la = `${Math.abs(lat).toFixed(1)}°${lat >= 0 ? 'N' : 'S'}`;
    const lo = `${Math.abs(lon).toFixed(1)}°${lon >= 0 ? 'E' : 'W'}`;
    return `${la} ${lo}`;
}

function leadToIso(index, lead) {
    if (index.baseTimeMs == null) return null;
    return new Date(index.baseTimeMs + lead * 3600000).toISOString().replace(/\.\d+Z$/, 'Z');
}

/* ------------------------------------------------------------------
 * UI
 * ------------------------------------------------------------------ */

const ROUTE_ASSETS = {
    field: 'assets/routes/field.json',
    vessels: 'assets/routes/vessels.json',
};

function formatUtc(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    return `${d.getUTCDate()} ${months[d.getUTCMonth()]} ${hh}:${mm}Z`;
}

function formatDuration(hours) {
    if (!Number.isFinite(hours)) return '—';
    const total = Math.round(hours * 60);
    const d = Math.floor(total / 1440);
    const h = Math.floor((total % 1440) / 60);
    const m = total % 60;
    if (d > 0) return `${d}d ${h}h`;
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function initRouteUi() {
    const section = document.getElementById('route');
    if (!section) return;

    const originSelect = document.getElementById('routeOrigin');
    const destSelect = document.getElementById('routeDestination');
    const departInput = document.getElementById('routeDeparture');
    const vesselSelect = document.getElementById('routeVessel');
    const modeSelect = document.getElementById('routeMode');
    const planBtn = document.getElementById('routePlanBtn');
    const summaryEl = document.getElementById('routeSummary');
    const warningsEl = document.getElementById('routeWarnings');
    const legsEl = document.getElementById('routeLegs');
    const mapEl = document.getElementById('routeMap');
    const chartEl = document.getElementById('routeChart');

    let forecast = null;
    let index = null;
    let vessels = [];
    let dataPromise = null;
    let countries = null;
    let countriesPromise = null;
    let canvas = null;
    let chart = null;
    let chartJsPromise = null;
    let currentPlan = null;
    let resizeObserver = null;

    // Domain used when no voyage is selected yet (matches the routing grid).
    const DOMAIN = { latMin: -9, latMax: 23, lonMin: 98, lonMax: 123 };

    function loadChartJs() {
        if (window.Chart) return Promise.resolve(window.Chart);
        if (chartJsPromise) return chartJsPromise;
        chartJsPromise = new Promise((resolve, reject) => {
            const s = document.createElement('script');
            s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.8/dist/chart.umd.min.js';
            s.async = true;
            s.onload = () => resolve(window.Chart);
            s.onerror = () => reject(new Error('Failed to load Chart.js'));
            document.head.appendChild(s);
        });
        return chartJsPromise;
    }

    function loadCountries() {
        if (countries) return Promise.resolve(countries);
        if (countriesPromise) return countriesPromise;
        countriesPromise = fetch('assets/maps/site_countries.geojson')
            .then(r => (r.ok ? r.json() : null))
            .then(doc => {
                countries = doc;
                return countries;
            })
            .catch(() => {
                countries = null;
                return null;
            });
        return countriesPromise;
    }

    function loadData() {
        if (dataPromise) return dataPromise;
        dataPromise = Promise.all([
            fetch(ROUTE_ASSETS.field).then(r => {
                if (!r.ok) throw new Error(`Route field HTTP ${r.status}`);
                return r.json();
            }),
            fetch(ROUTE_ASSETS.vessels).then(r => {
                if (!r.ok) throw new Error(`Vessel profiles HTTP ${r.status}`);
                return r.json();
            }),
        ]).then(([fieldDoc, vesselDoc]) => {
            forecast = fieldDoc;
            index = buildFieldIndex(fieldDoc);
            vessels = vesselDoc.profiles || [];
            return { forecast, vessels };
        });
        return dataPromise;
    }

    function populateControls() {
        const ports = (index.ports || [])
            .map(id => index.nodes.get(id))
            .filter(Boolean)
            .sort((a, b) => a.name.localeCompare(b.name));

        [originSelect, destSelect].forEach((sel, i) => {
            if (!sel) return;
            sel.innerHTML = '';
            ports.forEach(p => {
                const o = document.createElement('option');
                o.value = p.id;
                o.textContent = p.name;
                sel.appendChild(o);
            });
            if (ports.length > 1) sel.value = ports[i === 0 ? 0 : 1].id;
        });

        if (vesselSelect) {
            vesselSelect.innerHTML = '';
            vessels.forEach(v => {
                const o = document.createElement('option');
                o.value = v.id;
                o.textContent = v.name;
                vesselSelect.appendChild(o);
            });
        }

        if (departInput) {
            const base = index.baseTimeMs != null ? new Date(index.baseTimeMs) : new Date();
            departInput.value = base.toISOString().slice(0, 16);
            departInput.min = base.toISOString().slice(0, 16);
            if (index.horizonHours) {
                departInput.max = new Date(index.baseTimeMs + index.horizonHours * 3600000)
                    .toISOString()
                    .slice(0, 16);
            }
        }
    }

    function ensureCanvas() {
        if (!mapEl) return null;
        if (canvas && mapEl.contains(canvas)) return canvas;
        mapEl.innerHTML = '';
        canvas = document.createElement('canvas');
        canvas.className = 'route-map-canvas';
        canvas.setAttribute('aria-label', 'Planned route map');
        mapEl.appendChild(canvas);
        if (!resizeObserver && typeof ResizeObserver !== 'undefined') {
            resizeObserver = new ResizeObserver(() => paintMap(currentPlan));
            resizeObserver.observe(mapEl);
        }
        return canvas;
    }

    function viewBoxFor(plan) {
        if (plan?.coordinates?.length) {
            const lats = plan.coordinates.map(c => c[0]);
            const lons = plan.coordinates.map(c => c[1]);
            const pad = 1.5;
            return {
                latMin: Math.min(...lats) - pad,
                latMax: Math.max(...lats) + pad,
                lonMin: Math.min(...lons) - pad,
                lonMax: Math.max(...lons) + pad,
            };
        }
        return { ...DOMAIN };
    }

    /** Web Mercator northing (unitless), so shapes stay conformal. */
    function mercatorY(lat) {
        const clamped = Math.max(-85, Math.min(85, lat));
        return Math.log(Math.tan(Math.PI / 4 + (clamped * DEG) / 2));
    }

    function inverseMercatorY(y) {
        return (2 * Math.atan(Math.exp(y)) - Math.PI / 2) / DEG;
    }

    /**
     * Fit a lat/lon box to the canvas at one uniform Mercator scale.
     *
     * Both axes use radians: x = lon·π/180, y = mercatorY(lat). Mixing
     * degree-longitude with radian-northing previously collapsed the map
     * vertically. The shorter axis of the box is then widened so the frame
     * fills without letterboxing or stretching.
     */
    function fitView(box, width, height, pad = 18) {
        const availW = Math.max(1, width - 2 * pad);
        const availH = Math.max(1, height - 2 * pad);

        let x0 = box.lonMin * DEG;
        let x1 = box.lonMax * DEG;
        let y0 = mercatorY(box.latMin);
        let y1 = mercatorY(box.latMax);
        const spanX = Math.max(1e-6, x1 - x0);
        const spanY = Math.max(1e-6, y1 - y0);

        if (spanX / spanY > availW / availH) {
            const grow = (spanX * availH) / availW - spanY;
            y0 -= grow / 2;
            y1 += grow / 2;
        } else {
            const grow = (spanY * availW) / availH - spanX;
            x0 -= grow / 2;
            x1 += grow / 2;
        }

        const scale = availW / (x1 - x0);
        const fitted = {
            lonMin: x0 / DEG,
            lonMax: x1 / DEG,
            latMin: inverseMercatorY(y0),
            latMax: inverseMercatorY(y1),
        };
        const view = {
            scale,
            project(lat, lon) {
                return [
                    pad + (lon * DEG - x0) * scale,
                    pad + (y1 - mercatorY(lat)) * scale,
                ];
            },
        };
        return { box: fitted, view };
    }

    function drawPolygonRings(ctx, rings, view) {
        if (!rings?.length) return;
        ctx.beginPath();
        rings.forEach(ring => {
            if (!ring?.length) return;
            ring.forEach((pt, i) => {
                const [x, y] = view.project(pt[1], pt[0]);
                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            });
            ctx.closePath();
        });
        ctx.fill('evenodd');
        ctx.stroke();
    }

    function drawCountries(ctx, view) {
        if (!countries?.features) return;
        ctx.fillStyle = '#e8eef6';
        ctx.strokeStyle = '#8aa0bb';
        ctx.lineWidth = 1;
        countries.features.forEach(feat => {
            const geom = feat.geometry;
            if (!geom) return;
            if (geom.type === 'Polygon') {
                drawPolygonRings(ctx, geom.coordinates, view);
            } else if (geom.type === 'MultiPolygon') {
                geom.coordinates.forEach(poly => drawPolygonRings(ctx, poly, view));
            }
        });
    }

    /** Latitude/longitude graticule with a spacing suited to the zoom level. */
    function drawGraticule(ctx, box, view, width, height) {
        const span = Math.max(box.lonMax - box.lonMin, box.latMax - box.latMin);
        const step = span > 24 ? 10 : span > 12 ? 5 : span > 6 ? 2 : 1;

        ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
        ctx.lineWidth = 1;
        ctx.fillStyle = 'rgba(11, 35, 64, 0.55)';
        ctx.font = '500 10px Manrope, sans-serif';

        const firstLon = Math.ceil(box.lonMin / step) * step;
        for (let lon = firstLon; lon <= box.lonMax; lon += step) {
            const [x] = view.project(0, lon);
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, height);
            ctx.stroke();
            ctx.fillText(`${Math.abs(lon)}°${lon < 0 ? 'W' : 'E'}`, x + 3, height - 6);
        }

        const firstLat = Math.ceil(box.latMin / step) * step;
        for (let lat = firstLat; lat <= box.latMax; lat += step) {
            const [, y] = view.project(lat, 0);
            ctx.beginPath();
            ctx.moveTo(0, y);
            ctx.lineTo(width, y);
            ctx.stroke();
            ctx.fillText(`${Math.abs(lat)}°${lat < 0 ? 'S' : 'N'}`, 4, y - 3);
        }
    }

    /** Scale bar sized to a round nautical-mile distance at the view centre. */
    function drawScaleBar(ctx, box, view, width, height) {
        const midLat = (box.latMin + box.latMax) / 2;
        const [xa] = view.project(midLat, box.lonMin);
        const [xb] = view.project(midLat, box.lonMax);
        const spanNm = haversineNm(midLat, box.lonMin, midLat, box.lonMax);
        const pxPerNm = Math.abs(xb - xa) / Math.max(1e-6, spanNm);

        const target = spanNm / 4;
        const magnitude = 10 ** Math.floor(Math.log10(Math.max(1, target)));
        const nice = [1, 2, 5, 10].map(m => m * magnitude).find(v => v >= target) || magnitude * 10;
        const barPx = nice * pxPerNm;
        if (!Number.isFinite(barPx) || barPx < 20 || barPx > width * 0.6) return;

        const x = 12;
        const y = height - 16;
        ctx.strokeStyle = 'rgba(11, 35, 64, 0.75)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + barPx, y);
        ctx.moveTo(x, y - 4);
        ctx.lineTo(x, y + 4);
        ctx.moveTo(x + barPx, y - 4);
        ctx.lineTo(x + barPx, y + 4);
        ctx.stroke();
        ctx.fillStyle = 'rgba(11, 35, 64, 0.85)';
        ctx.font = '600 10px Manrope, sans-serif';
        ctx.fillText(`${nice.toLocaleString()} nm`, x, y - 7);
    }

    function paintMap(plan) {
        if (!mapEl || !index) return;
        const c = ensureCanvas();
        if (!c) return;

        const rect = mapEl.getBoundingClientRect();
        const width = Math.max(280, Math.floor(rect.width) || 640);
        const height = Math.max(240, Math.floor(rect.height) || 420);
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        c.width = Math.round(width * dpr);
        c.height = Math.round(height * dpr);
        c.style.width = `${width}px`;
        c.style.height = `${height}px`;

        const ctx = c.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);

        // Ocean wash
        ctx.fillStyle = '#a9c8e8';
        ctx.fillRect(0, 0, width, height);

        const { box, view } = fitView(viewBoxFor(plan), width, height);

        drawCountries(ctx, view);
        drawGraticule(ctx, box, view, width, height);

        // Port dots
        (index.ports || []).forEach(id => {
            const n = index.nodes.get(id);
            if (!n) return;
            const [x, y] = view.project(n.lat, n.lon);
            ctx.beginPath();
            ctx.arc(x, y, 4, 0, Math.PI * 2);
            ctx.fillStyle = '#ffffff';
            ctx.fill();
            ctx.strokeStyle = '#0B2340';
            ctx.lineWidth = 1.5;
            ctx.stroke();
        });

        drawScaleBar(ctx, box, view, width, height);

        if (!plan?.coordinates?.length) return;

        // Planned route: draw every grid step, colouring unsafe segments red.
        const unsafe = plan.stepUnsafe || [];
        for (let i = 0; i < plan.coordinates.length - 1; i++) {
            const a = plan.coordinates[i];
            const b = plan.coordinates[i + 1];
            if (!a || !b) continue;
            const [x1, y1] = view.project(a[0], a[1]);
            const [x2, y2] = view.project(b[0], b[1]);
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.strokeStyle = unsafe[i] ? '#D64545' : '#0B74DE';
            ctx.lineWidth = unsafe[i] ? 4 : 3.25;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.stroke();
        }

        // Endpoints
        const ends = [
            { pt: plan.coordinates[0], fill: '#0B2340', label: 'Dep' },
            { pt: plan.coordinates[plan.coordinates.length - 1], fill: '#0B74DE', label: 'Arr' },
        ];
        ends.forEach(({ pt, fill, label }) => {
            if (!pt) return;
            const [x, y] = view.project(pt[0], pt[1]);
            ctx.beginPath();
            ctx.arc(x, y, 6.5, 0, Math.PI * 2);
            ctx.fillStyle = fill;
            ctx.fill();
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 2;
            ctx.stroke();
            ctx.fillStyle = '#0B2340';
            ctx.font = '600 11px Manrope, sans-serif';
            ctx.fillText(label, x + 9, y + 4);
        });
    }

    function renderChart(plan) {
        if (!chartEl) return;
        loadChartJs().then(Chart => {
            if (chart) {
                chart.destroy();
                chart = null;
            }
            const points = plan.samples || [];
            const labels = points.map(s => formatUtc(s.arriveIso));
            chart = new Chart(chartEl, {
                type: 'line',
                data: {
                    labels,
                    datasets: [
                        {
                            label: 'SWH (m)',
                            data: points.map(s => s.swh),
                            borderColor: '#0B2340',
                            backgroundColor: 'rgba(11,35,64,0.08)',
                            yAxisID: 'y',
                            tension: 0.3,
                            spanGaps: true,
                            pointRadius: 0,
                        },
                        {
                            label: 'Wind (kt)',
                            data: points.map(s => s.wind_speed),
                            borderColor: '#0B74DE',
                            yAxisID: 'y1',
                            tension: 0.3,
                            spanGaps: true,
                            pointRadius: 0,
                        },
                        {
                            label: 'Speed over ground (kt)',
                            data: points.map(s => s.sog),
                            borderColor: '#2E9E63',
                            borderDash: [5, 4],
                            yAxisID: 'y1',
                            tension: 0.3,
                            pointRadius: 0,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    scales: {
                        y: { position: 'left', title: { display: true, text: 'SWH (m)' }, beginAtZero: true },
                        y1: {
                            position: 'right',
                            title: { display: true, text: 'kt' },
                            beginAtZero: true,
                            grid: { drawOnChartArea: false },
                        },
                        x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
                    },
                },
            });
        }).catch(err => console.error('Route chart failed:', err));
    }

    function renderPlan(plan) {
        currentPlan = plan;
        if (!plan) {
            if (summaryEl) summaryEl.textContent = 'No navigable sea route connects those ports. Try another pair.';
            if (legsEl) legsEl.innerHTML = '';
            if (warningsEl) warningsEl.innerHTML = '';
            paintMap(null);
            return;
        }

        if (summaryEl) {
            summaryEl.innerHTML = `
                <span class="route-stat"><strong>${formatDuration(plan.totalHours)}</strong><span>passage</span></span>
                <span class="route-stat"><strong>${Math.round(plan.distanceNm).toLocaleString()} nm</strong><span>distance</span></span>
                <span class="route-stat"><strong>${plan.avgSpeedKt.toFixed(1)} kt</strong><span>avg speed</span></span>
                <span class="route-stat"><strong>${formatUtc(plan.etaIso)}</strong><span>ETA</span></span>`;
        }

        if (warningsEl) {
            warningsEl.innerHTML = plan.warnings.length
                ? `<ul>${plan.warnings.map(w => `<li>${w}</li>`).join('')}</ul>`
                : '';
            warningsEl.hidden = plan.warnings.length === 0;
        }

        if (legsEl) {
            const rows = plan.legs.map(leg => `
                <tr class="${leg.unsafe ? 'route-leg--unsafe' : ''}">
                    <td>${leg.label}</td>
                    <td>${Math.round(leg.distanceNm)}</td>
                    <td>${leg.speedKt.toFixed(1)}</td>
                    <td>${(leg.conditions.swh_max ?? leg.conditions.swh) != null ? (leg.conditions.swh_max ?? leg.conditions.swh).toFixed(1) : '—'}</td>
                    <td>${leg.conditions.wind_speed != null ? leg.conditions.wind_speed.toFixed(0) : '—'}</td>
                    <td>${leg.conditions.current != null ? leg.conditions.current.toFixed(1) : '—'}</td>
                    <td>${formatUtc(leg.arriveIso)}</td>
                </tr>`).join('');
            legsEl.innerHTML = `
                <table class="route-table">
                    <thead>
                        <tr>
                            <th>Waypoint</th><th>nm</th><th>SOG kt</th><th>SWH m</th>
                            <th>Wind kt</th><th>Current kt</th><th>Arrive (UTC)</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>`;
        }

        renderChart(plan);
        loadCountries().then(() => paintMap(plan));
    }

    function plan() {
        if (!index) return;
        const profile = vessels.find(v => v.id === vesselSelect?.value) || vessels[0];
        if (!profile) return;
        const departure = departInput?.value ? `${departInput.value}:00Z` : new Date().toISOString();
        renderPlan(
            planRoute({
                index,
                field: forecast,
                profile,
                origin: originSelect?.value,
                destination: destSelect?.value,
                departure,
                mode: modeSelect?.value || 'balanced',
            })
        );
    }

    let ready = false;
    function activate() {
        if (ready) {
            paintMap(currentPlan);
            return;
        }
        loadData().then(() => {
            ready = true;
            populateControls();
            [originSelect, destSelect, vesselSelect, modeSelect, departInput].forEach(el => {
                el?.addEventListener('change', plan);
            });
            planBtn?.addEventListener('click', plan);
            loadCountries().then(() => plan());
        }).catch(err => {
            console.error('Route forecast unavailable:', err);
            if (summaryEl) {
                summaryEl.textContent = 'Route data is not published yet — it appears after the next pipeline run.';
            }
        });
    }

    if (section.classList.contains('active')) activate();
    new MutationObserver(() => {
        if (section.classList.contains('active')) activate();
    }).observe(section, { attributes: true, attributeFilter: ['class'] });
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initRouteUi);
    } else {
        initRouteUi();
    }
}
