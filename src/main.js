(function () {

    /* ------------------------------------------------------------
     * SPA Navigation
     * ------------------------------------------------------------ */
    const sections = document.querySelectorAll('.section');
    const buttons = document.querySelectorAll('.menu button');

    function setActive(id) {
        sections.forEach(s => s.classList.remove('active'));
        const el = document.getElementById(id);
        if (el) el.classList.add('active');

        buttons.forEach(b =>
            b.classList.toggle('active', b.dataset.section === id)
        );
        if (id === 'map') {
            requestAnimationFrame(fitMapToViewport);
        }
        if (id === 'site') {
            // Site state is declared later in this IIFE. Deferring also lets
            // config.json finish populating the site catalog before rendering.
            requestAnimationFrame(() => ensureSiteForecastReady());
        }
    }

    function goToSection(id) {
        setActive(id);
        localStorage.setItem('nw_section', id);
        if (window.innerWidth <= 768) {
            document.getElementById('sidebar').classList.remove('open');
        }
    }

    buttons.forEach(b => {
        b.addEventListener('click', () => {
            goToSection(b.dataset.section);
        });
    });

    document.getElementById('hamburger').onclick = () =>
        document.getElementById('sidebar').classList.toggle('open');

    document.querySelectorAll('.logo-link').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            goToSection(link.dataset.section || 'home');
        });
    });

    const initialSection = localStorage.getItem('nw_section') || 'home';
    setActive(initialSection === 'observation' ? 'home' : initialSection);
    if (initialSection === 'observation') {
        localStorage.setItem('nw_section', 'home');
    }

    /* ------------------------------------------------------------
     * CONFIG + SELECT ELEMENTS
     * ------------------------------------------------------------ */
    let CONFIG = null;

    const regionSelect = document.getElementById('regionSelect');
    const forecastSelect = document.getElementById('forecastSelect');
    const parameterSelect = document.getElementById('parameterSelect');
    const modelSelect = document.getElementById('modelSelect');
    const timeSelect = document.getElementById('timeSelect');
    const mapImage = document.getElementById('mapImage');
    const prevBtn = document.getElementById('prevBtn');
    const playBtn = document.getElementById('playBtn');
    const nextBtn = document.getElementById('nextBtn');
    const timeline = document.getElementById('timeline');
    const overlay = document.getElementById('regionOverlay');
    const regionHighlight = document.getElementById('regionHighlight');
    const regionLabel = document.getElementById('regionLabel');

    // UI parameter labels -> backend file slugs
    const PARAM_SLUGS = {
        surface_wind: 'wind',
        swh: 'swh',
        swell: 'swell',
        sfc_temp: 'temp',
        rh: 'relhum',
        mslp_wind: 'mslp_wind',
        rain_rh700: 'rain_rh700',
        sst: 'seatemp',
        sss: 'seasalt',
        ssh: 'ssh',
        current: 'seacurrent',
    };

    // Human-readable dropdown labels (from docs/PRODUCT_CATALOG.md display names)
    const PARAM_LABELS = {
        surface_wind: 'Surface Wind',
        swh: 'Significant Wave Height',
        swell: 'Primary Swell',
        sfc_temp: 'Surface Air Temperature',
        rh: 'Surface Relative Humidity',
        mslp_wind: 'Surface Wind + MSLP',
        rain_rh700: 'Rainfall + 700 hPa Humidity',
        sst: 'Surface Sea Temperature',
        sss: 'Surface Sea Salinity',
        ssh: 'Sea Surface Height',
        current: 'Surface Sea Current',
    };

    // Forecast-type category each parameter belongs to (grouped by dataset family)
    const PARAM_CATEGORY = {
        surface_wind: 'Wind and Waves',
        swh: 'Wind and Waves',
        swell: 'Wind and Waves',
        sfc_temp: 'Atmosphere',
        rh: 'Atmosphere',
        mslp_wind: 'Atmosphere',
        rain_rh700: 'Atmosphere',
        sst: 'Ocean',
        sss: 'Ocean',
        ssh: 'Ocean',
        current: 'Ocean',
    };

    const CATEGORY_ORDER = ['Wind and Waves', 'Atmosphere', 'Ocean'];

    // Preferred parameter order within a category (unlisted keys keep discovery order).
    const PARAM_ORDER = {
        'Wind and Waves': ['surface_wind', 'swh', 'swell'],
        Atmosphere: ['rain_rh700', 'mslp_wind', 'sfc_temp', 'rh'],
        Ocean: ['sst', 'sss', 'current', 'ssh'],
    };

    const MODEL_DATASET = {
        GFS: 'gfswave',
        WW3: 'gfswave',
    };

    const STATIC_MAP = 'assets/maps/staticmap.png';
    const mapContainer = document.querySelector('.map-container');
    const mapSection = document.getElementById('map');

    let playInterval = null;
    let pendingMapSrc = null;
    let preloadGen = 0;
    const preloadCache = new Set();
    let fitRaf = 0;

    function showStaticMap() {
        pendingMapSrc = null;
        mapImage.src = STATIC_MAP;
    }

    // Fit the map image inside the visible content area without cropping.
    // Landscape → limited by width; portrait → limited by available height.
    function fitMapToViewport() {
        if (!mapImage || !mapContainer || !mapSection?.classList.contains('active')) {
            return;
        }

        const nw = mapImage.naturalWidth;
        const nh = mapImage.naturalHeight;
        if (!nw || !nh) return;

        const content = document.querySelector('.content');
        if (!content) return;

        const contentStyle = getComputedStyle(content);
        const padX =
            parseFloat(contentStyle.paddingLeft) +
            parseFloat(contentStyle.paddingRight);
        const containerPad = 16; // .map-container padding 8px × 2
        const availW = Math.max(160, content.clientWidth - padX - containerPad);

        const controls = mapSection.querySelector('.controls');
        const topEdge = controls
            ? controls.getBoundingClientRect().bottom
            : mapSection.getBoundingClientRect().top;
        const bottomPad = window.innerWidth <= 768 ? 12 : 20;
        const availH = Math.max(180, window.innerHeight - topEdge - bottomPad - containerPad);

        const scale = Math.min(availW / nw, availH / nh);
        const dispW = Math.max(1, Math.floor(nw * scale));
        const dispH = Math.max(1, Math.floor(nh * scale));

        mapImage.style.width = `${dispW}px`;
        mapImage.style.height = `${dispH}px`;
        mapImage.width = dispW;
        mapImage.height = dispH;

        mapContainer.classList.toggle('is-portrait', nh > nw);
        mapContainer.classList.toggle('is-landscape', nw >= nh);
    }

    function scheduleFitMap() {
        if (fitRaf) cancelAnimationFrame(fitRaf);
        fitRaf = requestAnimationFrame(() => {
            fitRaf = 0;
            fitMapToViewport();
        });
    }

    mapImage.addEventListener('error', () => {
        const failed = pendingMapSrc || mapImage.getAttribute('src');
        if (!failed || failed.includes('staticmap')) return;
        console.warn('Map image missing, falling back to overview:', failed);
        pendingMapSrc = null;
        mapImage.src = STATIC_MAP;
    });

    mapImage.addEventListener('load', scheduleFitMap);
    window.addEventListener('resize', scheduleFitMap);

    function mapFrameUrl(dataset, region, paramSlug, timeIndex) {
        return `assets/maps/${dataset}/${region}/${paramSlug}_${timeIndex}.webp`;
    }

    // Prefetch all forecast hours for the current region/param so play is smooth.
    function preloadFrames() {
        const region = regionSelect.value;
        const param = parameterSelect.value;
        const model = modelSelect.value;
        if (isRegionPlaceholder(region) || isPlaceholderOption(param)) return;

        const meta = forecastMeta();
        const paramSlug = PARAM_SLUGS[param] || param;
        const dataset = meta.dataset || MODEL_DATASET[model] || 'gfswave';
        const paramTimes = meta.parameters?.[param];
        const times = Array.isArray(paramTimes) && paramTimes.length
            ? paramTimes.filter(t => timeIndexFromLabel(t))
            : (meta.timestamps || []).filter(t => timeIndexFromLabel(t));

        const gen = ++preloadGen;
        times.forEach(label => {
            const idx = timeIndexFromLabel(label);
            if (!idx) return;
            const src = mapFrameUrl(dataset, region, paramSlug, idx);
            if (preloadCache.has(src)) return;
            const img = new Image();
            img.onload = () => {
                if (gen === preloadGen) preloadCache.add(src);
            };
            img.src = src;
        });
    }


    /* ------------------------------------------------------------
     * Helper
     * ------------------------------------------------------------ */
    function populate(sel, items, labelFn, preferred) {
        sel.innerHTML = "";
        items.forEach(v => {
            const o = document.createElement('option');
            o.value = v;
            o.textContent = labelFn ? labelFn(v) : v;
            sel.appendChild(o);
        });
        if (preferred && items.includes(preferred)) {
            sel.value = preferred;
        }
    }

    // Show friendly parameter names while keeping the slug as the option value.
    function relabelParameters() {
        [...parameterSelect.options].forEach(opt => {
            if (PARAM_LABELS[opt.value]) opt.textContent = PARAM_LABELS[opt.value];
        });
    }

    // Re-organise a region's forecast types into dataset-family categories.
    // Driven entirely by config.json, so only parameters that actually have
    // deployed maps ever appear in the dropdowns.
    function regroupRegion(regionMeta) {
        const srcTypes = regionMeta?.forecast_types || {};
        const grouped = {};

        for (const typeMeta of Object.values(srcTypes)) {
            const params = typeMeta?.parameters || {};
            for (const [pkey, times] of Object.entries(params)) {
                const cat = PARAM_CATEGORY[pkey] || 'Other';
                if (!grouped[cat]) {
                    grouped[cat] = {
                        parameters: {},
                        models: typeMeta.models || ['GFS'],
                        timestamps: typeMeta.timestamps || [],
                        dataset: typeMeta.dataset || 'gfswave',
                        cycle: typeMeta.cycle || null,
                    };
                }
                grouped[cat].parameters[pkey] = times;
                // Prefer a non-empty cycle if another typeMeta supplies one.
                if (typeMeta.cycle && !grouped[cat].cycle) {
                    grouped[cat].cycle = typeMeta.cycle;
                }
                if (typeMeta.dataset) {
                    grouped[cat].dataset = typeMeta.dataset;
                }
            }
        }

        // Preserve a stable category order, with any unknown groups last.
        const ordered = {};
        CATEGORY_ORDER.forEach(c => { if (grouped[c]) ordered[c] = grouped[c]; });
        Object.keys(grouped).forEach(c => { if (!ordered[c]) ordered[c] = grouped[c]; });

        // Stable parameter order within each category.
        for (const [cat, meta] of Object.entries(ordered)) {
            const preferred = PARAM_ORDER[cat] || [];
            const params = meta.parameters || {};
            const sorted = {};
            preferred.forEach(key => {
                if (params[key]) sorted[key] = params[key];
            });
            Object.keys(params).forEach(key => {
                if (!sorted[key]) sorted[key] = params[key];
            });
            meta.parameters = sorted;
        }

        return Object.assign({}, regionMeta, { forecast_types: ordered });
    }


    /* ------------------------------------------------------------
     * Update Map Image
     * ------------------------------------------------------------ */
    function forecastMeta() {
        const region = regionSelect.value;
        const type = forecastSelect.value;
        return CONFIG?.regions?.[region]?.forecast_types?.[type] || {};
    }

    function timeIndexFromLabel(label) {
        const m = /^F(\d+)$/.exec(label || '');
        return m ? m[1].padStart(3, '0') : null;
    }

    const MONTHS = [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December',
    ];
    const WEEKDAYS_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

    // Resolve an "F###" label into its lead hour and (when the cycle is known)
    // the UTC valid time of that frame.
    function validTimeInfo(fLabel) {
        const m = /^F(\d+)$/.exec(fLabel || '');
        if (!m) return null;
        const fh = parseInt(m[1], 10);
        const meta = forecastMeta();
        const cycle =
            meta?.cycle ||
            CONFIG?.cycles?.[meta?.dataset] ||
            CONFIG?.cycle ||
            CONFIG?.updated;
        if (!cycle || !/^\d{10}$/.test(String(cycle))) return { fh, valid: null };
        const c = String(cycle);
        const valid = new Date(Date.UTC(
            +c.slice(0, 4),
            +c.slice(4, 6) - 1,
            +c.slice(6, 8),
            +c.slice(8, 10) + fh
        ));
        return { fh, valid };
    }

    function formatTimeLabel(fLabel) {
        const info = validTimeInfo(fLabel);
        if (!info) return fLabel;
        if (!info.valid) return info.fh === 0 ? 't+0h (analysis)' : `t+${info.fh}h`;
        const v = info.valid;
        const hh = String(v.getUTCHours()).padStart(2, '0');
        const mm = String(v.getUTCMinutes()).padStart(2, '0');
        return `${MONTHS[v.getUTCMonth()]} ${v.getUTCDate()}, ${v.getUTCFullYear()} at ${hh}:${mm} UTC`;
    }

    function relabelTimes() {
        [...timeSelect.options].forEach(opt => {
            if (timeIndexFromLabel(opt.value)) opt.textContent = formatTimeLabel(opt.value);
        });
    }

    function populateTimes(items, labelFn, preferred) {
        populate(timeSelect, items, labelFn, preferred);
        renderTimeline();
    }

    function isPlaceholderOption(value) {
        if (!value) return true;
        return /^(Type|Parameter|Model|Time) \(/i.test(String(value));
    }

    function isStaticMapMode() {
        return isRegionPlaceholder(regionSelect.value);
    }

    function setRegionOverlayEnabled(enabled) {
        if (!overlay) return;
        overlay.style.pointerEvents = enabled ? 'auto' : 'none';
        overlay.style.cursor = enabled ? 'pointer' : 'default';
        if (!enabled) {
            regionHighlight.style.display = 'none';
            regionLabel.style.display = 'none';
        }
    }

    function updateMap() {
        const region = regionSelect.value;
        const param = parameterSelect.value;
        const model = modelSelect.value;
        const ts = timeSelect.value;

        setRegionOverlayEnabled(isStaticMapMode());

        if (isRegionPlaceholder(region) || isPlaceholderOption(param) || isPlaceholderOption(ts)) {
            showStaticMap();
            return;
        }

        const meta = forecastMeta();
        if (!meta.parameters || !Object.keys(meta.parameters).length) {
            showStaticMap();
            return;
        }

        const paramSlug = PARAM_SLUGS[param] || param;
        const timeIndex = timeIndexFromLabel(ts);
        if (!timeIndex) {
            showStaticMap();
            return;
        }

        const dataset = meta.dataset || MODEL_DATASET[model] || 'gfswave';
        pendingMapSrc = mapFrameUrl(dataset, region, paramSlug, timeIndex);
        mapImage.src = pendingMapSrc;
        syncTimeline();
    }


    /* ------------------------------------------------------------
     * Dropdown Chain Loaders
     * ------------------------------------------------------------ */
    function isRegionPlaceholder(value) {
        if (!value) return true;

        const clean = value.toLowerCase().replace(/\s+/g, '');
        return clean.includes("selectregion") || clean.includes("selectregion(orclickonmap)");
    }
    function loadForecastTypes() {
        const region = regionSelect.value;
        // Keep the current product selection when switching regions.
        const prev = {
            type: forecastSelect.value,
            param: parameterSelect.value,
            model: modelSelect.value,
            time: timeSelect.value,
        };
        if (isRegionPlaceholder(region)) {

            populate(forecastSelect, ["Type (Select a region first)"]);
            populate(parameterSelect, ["Parameter (Select a region first)"]);
            populate(modelSelect, ["Model (Select a region first)"]);
            populateTimes(["Time (Select a region first)"]);

            setRegionOverlayEnabled(true);
            showStaticMap();
            return;
        }
        const types = Object.keys(CONFIG.regions[region]?.forecast_types || {});
        populate(forecastSelect, types.length ? types : ['Type (no data)'], null, prev.type);
        loadParameters(prev);
    }

    function loadParameters(prev) {
        const region = regionSelect.value;
        const type = forecastSelect.value;
        if (isPlaceholderOption(type)) {
            populate(parameterSelect, ['Parameter (Select type first)']);
            populate(modelSelect, ['Model (Select type first)']);
            populateTimes(['Time (Select type first)']);
            updateMap();
            return;
        }
        const meta = CONFIG.regions[region]?.forecast_types?.[type];
        const params = Object.keys(meta?.parameters || {});
        populate(
            parameterSelect,
            params.length ? params : ['Parameter (no data)'],
            null,
            prev?.param
        );
        relabelParameters();
        loadModels(prev);
    }

    function loadModels(prev) {
        const meta = forecastMeta();
        if (isPlaceholderOption(forecastSelect.value)) {
            populate(modelSelect, ['Model (Select type first)']);
            populateTimes(['Time (Select type first)']);
            updateMap();
            return;
        }
        const models = meta.models || [];
        populate(modelSelect, models.length ? models : ['GFS'], null, prev?.model);
        loadTimes(prev);
    }

    function loadTimes(prev) {
        const meta = forecastMeta();
        const param = parameterSelect.value;
        if (isPlaceholderOption(param)) {
            populateTimes(['Time (Select parameter first)']);
            updateMap();
            return;
        }
        const paramTimes = meta.parameters?.[param];
        const times = Array.isArray(paramTimes) && paramTimes.length
            ? paramTimes.filter(t => timeIndexFromLabel(t))
            : (meta.timestamps || []).filter(t => timeIndexFromLabel(t));
        populateTimes(
            times.length ? times : ['Time (no data)'],
            formatTimeLabel,
            prev?.time
        );
        updateMap();
        preloadFrames();
    }

    function validTimeOptions() {
        return [...timeSelect.options].filter(o => timeIndexFromLabel(o.value));
    }


    /* ------------------------------------------------------------
     * Clickable Time Strip
     * ------------------------------------------------------------ */
    function selectTime(value) {
        const opt = [...timeSelect.options].find(o => o.value === value);
        if (!opt) return;
        opt.selected = true;
        updateMap();
    }

    // Keep the active tick visible without scrolling the page.
    function revealTick(tick) {
        const strip = timeline.getBoundingClientRect();
        const box = tick.getBoundingClientRect();
        if (box.left < strip.left + 8) {
            timeline.scrollLeft += box.left - strip.left - 24;
        } else if (box.right > strip.right - 8) {
            timeline.scrollLeft += box.right - strip.right + 24;
        }
    }

    function syncTimeline() {
        if (!timeline) return;
        const current = timeSelect.value;
        let active = null;
        timeline.querySelectorAll('.tl-tick').forEach(tick => {
            const on = tick.dataset.value === current;
            tick.classList.toggle('active', on);
            tick.setAttribute('aria-pressed', on ? 'true' : 'false');
            if (on) active = tick;
        });
        if (active) revealTick(active);
    }

    // One tick per forecast hour, grouped under the day it is valid for.
    function renderTimeline() {
        if (!timeline) return;
        timeline.innerHTML = '';
        const opts = validTimeOptions();
        timeline.classList.toggle('is-empty', opts.length <= 1);
        if (opts.length <= 1) return;

        let dayKey = null;
        let hoursEl = null;

        opts.forEach(opt => {
            const info = validTimeInfo(opt.value);
            const v = info?.valid || null;
            const key = v ? v.toISOString().slice(0, 10) : 'lead';

            if (key !== dayKey) {
                dayKey = key;
                const group = document.createElement('div');
                group.className = 'tl-day';

                const label = document.createElement('span');
                label.className = 'tl-day-label';
                label.textContent = v
                    ? `${WEEKDAYS_SHORT[v.getUTCDay()]} ${v.getUTCDate()} ${MONTHS[v.getUTCMonth()].slice(0, 3)} UTC`
                    : 'Lead time';

                hoursEl = document.createElement('div');
                hoursEl.className = 'tl-hours';

                group.append(label, hoursEl);
                timeline.appendChild(group);
            }

            const tick = document.createElement('button');
            tick.type = 'button';
            tick.className = 'tl-tick';
            tick.dataset.value = opt.value;
            tick.textContent = v
                ? `${String(v.getUTCHours()).padStart(2, '0')}h`
                : `+${info.fh}h`;
            tick.title = formatTimeLabel(opt.value);
            tick.setAttribute('aria-label', tick.title);
            tick.addEventListener('click', () => selectTime(opt.value));
            hoursEl.appendChild(tick);
        });

        syncTimeline();
    }

    function stepTime(delta) {
        const opts = validTimeOptions();
        if (opts.length <= 1) return;
        const current = timeSelect.options[timeSelect.selectedIndex];
        let idx = opts.indexOf(current);
        if (idx < 0) idx = 0;
        idx += delta;
        if (idx < 0 || idx >= opts.length) return;
        opts[idx].selected = true;
        updateMap();
    }

    function togglePlay() {
        if (playInterval) {
            clearInterval(playInterval);
            playInterval = null;
            playBtn.textContent = '▶️';
            return;
        }
        const opts = validTimeOptions();
        if (opts.length <= 1) return;
        playBtn.textContent = '⏸️';
        playInterval = setInterval(() => {
            const list = validTimeOptions();
            const current = timeSelect.options[timeSelect.selectedIndex];
            let idx = list.indexOf(current);
            if (idx < 0) idx = 0;
            if (idx >= list.length - 1) {
                list[0].selected = true;
            } else {
                list[idx + 1].selected = true;
            }
            updateMap();
        }, 800);
    }

    if (prevBtn) prevBtn.addEventListener('click', () => stepTime(-1));
    if (nextBtn) nextBtn.addEventListener('click', () => stepTime(1));
    if (playBtn) playBtn.addEventListener('click', togglePlay);

    const overviewBtn = document.getElementById('overviewBtn');
    if (overviewBtn) {
        overviewBtn.addEventListener('click', () => {
            if (playInterval) togglePlay();
            regionSelect.value = 'Select Region (or Click on Map)';
            loadForecastTypes();
        });
    }


    /* ------------------------------------------------------------
     * Clickable Static Map Regions (lon/lat-based)
     * ------------------------------------------------------------ */

    // FULL MAP EXTENTS (these match your "Southeast Asia" extent)
    const MAP_LON_MIN = 90.0;
    const MAP_LON_MAX = 150.0;
    const MAP_LAT_MIN = -20.0;
    const MAP_LAT_MAX = 25.0;

    // region definitions: keys are the region identifiers (used as regionSelect.value)
    // bounds = [lon_min, lon_max, lat_min, lat_max]  (from your list)
    const regionDefs = [
        { id: "malacca_strait", display: "Malacca Strait", bounds: [95, 105, 0, 6] },
        { id: "south_china_sea", display: "South China Sea", bounds: [105.5, 121, 6, 25] },
        { id: "philippines", display: "Philippines", bounds: [116, 130, 6, 20] },
        { id: "andaman_gulf_thailand", display: "Andaman Sea & Gulf of Thailand", bounds: [90, 105.5, 6, 18] },
        { id: "java_nusa_tenggara", display: "Java - Nusa Tenggara", bounds: [103, 128, -13, -3] },
        { id: "western_indo", display: "Western Indo", bounds: [95, 120, -13, 6] },
        { id: "eastern_indo", display: "Eastern Indo", bounds: [120, 141, -13, 6] },
        { id: "indonesia", display: "Indonesia", bounds: [90, 141, -13, 6] },
        { id: "southeast_asia", display: "Southeast Asia", bounds: [90, 150, -20, 25] },
    ];

    function regionArea(bounds) {
        const [lonMin, lonMax, latMin, latMax] = bounds;
        return (lonMax - lonMin) * (latMax - latMin);
    }

    // Smallest bbox wins when regions overlap (e.g. Indonesia vs Java).
    const regionsBySpecificity = [...regionDefs].sort(
        (a, b) => regionArea(a.bounds) - regionArea(b.bounds)
    );

    function pickRegionAt(lon, lat) {
        for (const r of regionsBySpecificity) {
            const [lonMin, lonMax, latMin, latMax] = r.bounds;
            if (lon >= lonMin && lon <= lonMax && lat >= latMin && lat <= latMax) {
                return r;
            }
        }
        return null;
    }

    function regionSelectOptions() {
        const placeholder = "Select Region (or Click on Map)";
        const ordered = regionDefs.map(r => r.id);
        const extras = Object.keys(CONFIG?.regions || {}).filter(
            k => k !== placeholder && !ordered.includes(k)
        );
        return [placeholder, ...ordered, ...extras];
    }

    const DEFAULT_REGION = 'southeast_asia';

    function populateRegionSelect() {
        populate(regionSelect, regionSelectOptions());
        [...regionSelect.options].forEach(opt => {
            const def = regionDefs.find(d => d.id === opt.value);
            if (def) opt.textContent = def.display;
        });
    }

    function selectDefaultRegion() {
        const options = [...regionSelect.options].map(o => o.value);
        if (options.includes(DEFAULT_REGION)) {
            regionSelect.value = DEFAULT_REGION;
        }
    }

    // overlay elements for static map region picking

    // Compute lon/lat from click position on overlay
    function clickToLonLat(clickX, clickY) {
        const rect = overlay.getBoundingClientRect();
        const fracX = clickX / rect.width;   // 0..1 across image
        const fracY = clickY / rect.height;  // 0..1 down image

        const lon = MAP_LON_MIN + fracX * (MAP_LON_MAX - MAP_LON_MIN);
        // note: y fraction increases downward, while lat increases upward
        const lat = MAP_LAT_MAX - fracY * (MAP_LAT_MAX - MAP_LAT_MIN);

        return { lon, lat };
    }

    // click handler — only active on static overview map
    overlay.addEventListener("click", function (e) {
        if (!isStaticMapMode()) return;

        const rect = overlay.getBoundingClientRect();
        const clickX = e.clientX - rect.left;
        const clickY = e.clientY - rect.top;

        const { lon, lat } = clickToLonLat(clickX, clickY);

        const match = pickRegionAt(lon, lat);
        if (match) {
            regionSelect.value = match.id;
            loadForecastTypes();
            return;
        }
    });

    /* ------------------------------------------------------------
    * Hover Highlight + Label (static map only)
    * ------------------------------------------------------------ */

    // Convert lon/lat box → pixel box on screen
    function regionBoxToPixels(bounds) {
        const [lonMin, lonMax, latMin, latMax] = bounds;

        // convert to fractional
        const fracX1 = (lonMin - MAP_LON_MIN) / (MAP_LON_MAX - MAP_LON_MIN);
        const fracX2 = (lonMax - MAP_LON_MIN) / (MAP_LON_MAX - MAP_LON_MIN);
        const fracY1 = (MAP_LAT_MAX - latMax) / (MAP_LAT_MAX - MAP_LAT_MIN);
        const fracY2 = (MAP_LAT_MAX - latMin) / (MAP_LAT_MAX - MAP_LAT_MIN);

        const w = overlay.clientWidth;
        const h = overlay.clientHeight;

        return {
            x: fracX1 * w,
            y: fracY1 * h,
            width: (fracX2 - fracX1) * w,
            height: (fracY2 - fracY1) * h
        };
    }

    overlay.addEventListener("mousemove", function(e) {
        if (!isStaticMapMode()) return;

        const rect = overlay.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const { lon, lat } = clickToLonLat(x, y);
        const found = pickRegionAt(lon, lat);

        if (!found) {
            regionHighlight.style.display = "none";
            regionLabel.style.display = "none";
            return;
        }

        // convert region bounds → pixel highlight box
        const box = regionBoxToPixels(found.bounds);

        regionHighlight.style.display = "block";
        regionHighlight.style.left = box.x + "px";
        regionHighlight.style.top = box.y + "px";
        regionHighlight.style.width = box.width + "px";
        regionHighlight.style.height = box.height + "px";

        regionLabel.style.display = "block";
        regionLabel.textContent = found.display;
        regionLabel.style.left = (x + 12) + "px";
        regionLabel.style.top = (y + 12) + "px";
    });

    overlay.addEventListener("mouseleave", function() {
        regionHighlight.style.display = "none";
        regionLabel.style.display = "none";
    });


    /* ------------------------------------------------------------
     * Site Forecast (tile-free Leaflet map → lazy Chart.js + JSON)
     * ------------------------------------------------------------ */
    const siteSelect = document.getElementById('siteSelect');
    const sitePanel = document.getElementById('sitePanel');
    const siteSummary = document.getElementById('siteSummary');
    const siteDownloadBtn = document.getElementById('siteDownloadBtn');
    const siteOverview = document.getElementById('siteOverview');
    const siteOverviewBtn = document.getElementById('siteOverviewBtn');
    const siteMapEl = document.getElementById('siteMap');

    const siteCache = new Map();
    let siteAbort = null;
    let siteCharts = [];
    let siteDocCurrent = null;
    let siteStaticChartUrl = null;
    let chartJsPromise = null;
    let leafletPromise = null;
    let siteMapPromise = null;
    let siteMapInstance = null;
    const siteMapMarkers = new Map();
    let siteUiReady = false;
    let siteCatalog = [];
    const SITE_PLACEHOLDER = 'Select a port (or click map)';
    const SITE_MAP_BOUNDS = [[-11, 95], [24, 128]];

    const SITE_CHART_GROUPS = [
        {
            id: 'waves',
            title: 'Waves & wind',
            series: [
                // dirConvention: wind/wave dirs are FROM; arrows drawn TO (propagation).
                { key: 'wind_speed', label: 'Wind', color: '#0B74DE', yAxisID: 'y', dirConvention: 'from' },
                { key: 'swh', label: 'SWH', color: '#0B2340', yAxisID: 'y1', dirConvention: 'from' },
                { key: 'swell', label: 'Swell', color: '#5B8DEF', yAxisID: 'y1', dash: [5, 4], dirConvention: 'from' },
            ],
        },
        {
            id: 'ocean',
            title: 'Ocean',
            series: [
                { key: 'sst', label: 'SST', color: '#C45C26', yAxisID: 'y' },
                { key: 'current', label: 'Current', color: '#1F7A5C', yAxisID: 'y1', dirConvention: 'to' },
            ],
        },
        {
            id: 'weather',
            title: 'Weather',
            series: [
                { key: 'rain', label: 'Rain', color: '#6BA3D6', yAxisID: 'y', type: 'bar' },
                { key: 'temp', label: 'Temp', color: '#C0392B', yAxisID: 'y1' },
                { key: 'rh', label: 'RH', color: '#7D3C98', yAxisID: 'y2', dash: [2, 3] },
            ],
        },
        {
            id: 'tide',
            title: 'Tide (astronomical, hourly)',
            series: [
                { key: 'tide', label: 'Tide', color: '#1B4F72', yAxisID: 'y' },
            ],
        },
    ];

    const SITE_CARDINALS = [
        'N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
        'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW',
    ];

    function displaySiteUnit(unit) {
        if (!unit) return '';
        return unit === 'degC' ? '°C' : unit;
    }

    function degToCardinal(deg) {
        if (deg == null || !Number.isFinite(deg)) return null;
        const idx = Math.round((((deg % 360) + 360) % 360) / 22.5) % 16;
        return SITE_CARDINALS[idx];
    }

    function formatSiteDir(deg, convention) {
        if (deg == null || !Number.isFinite(deg)) return null;
        const rounded = Math.round(deg);
        const card = degToCardinal(deg);
        if (convention === 'to') return `toward ${card} (${rounded}°)`;
        return `from ${card} (${rounded}°)`;
    }

    /** Compact UTC axis tick from ISO valid_time, e.g. "25 Jul 18Z". */
    function formatSiteAxisLabel(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return String(iso);
        const day = d.getUTCDate();
        const mon = MONTHS[d.getUTCMonth()].slice(0, 3);
        const hh = String(d.getUTCHours()).padStart(2, '0');
        return `${day} ${mon} ${hh}Z`;
    }

    function siteChartLabels(doc, seriesKey = null) {
        const entry = seriesKey ? doc.series?.[seriesKey] : null;
        const hours = (entry?.hours?.length ? entry.hours : doc.hours) || [];
        const times = (entry?.valid_times?.length ? entry.valid_times : doc.valid_times) || [];
        const n = Math.max(hours.length, times.length);
        const labels = [];
        for (let i = 0; i < n; i += 1) {
            const label = formatSiteAxisLabel(times[i]);
            labels.push(label || hours[i] || '');
        }
        return labels;
    }

    /** Chart.js plugin: draw flow/propagation arrows at points with dir_deg. */
    function siteDirectionArrowsPlugin(group, doc) {
        return {
            id: 'siteDirArrows',
            afterDatasetsDraw(chart) {
                const { ctx } = chart;
                group.series.forEach(spec => {
                    if (!spec.dirConvention) return;
                    const dirs = doc.series?.[spec.key]?.dir_deg;
                    if (!Array.isArray(dirs)) return;
                    const dsIndex = chart.data.datasets.findIndex(
                        d => d._siteKey === spec.key
                    );
                    if (dsIndex < 0) return;
                    const meta = chart.getDatasetMeta(dsIndex);
                    if (!meta || meta.hidden) return;
                    const step = Math.max(1, Math.floor(dirs.length / 14));
                    ctx.save();
                    ctx.fillStyle = spec.color;
                    ctx.strokeStyle = spec.color;
                    ctx.lineWidth = 1.6;
                    meta.data.forEach((pt, i) => {
                        if (!pt || pt.skip) return;
                        if (i % step !== 0 && i !== meta.data.length - 1) return;
                        const deg = dirs[i];
                        if (deg == null || !Number.isFinite(deg)) return;
                        // Arrow points TO (propagation / flow).
                        const toDeg = spec.dirConvention === 'from' ? deg + 180 : deg;
                        const rad = (toDeg * Math.PI) / 180;
                        const len = 11;
                        const x = pt.x;
                        const y = pt.y;
                        const tx = x + Math.sin(rad) * len;
                        const ty = y - Math.cos(rad) * len;
                        ctx.beginPath();
                        ctx.moveTo(x - Math.sin(rad) * 4.5, y + Math.cos(rad) * 4.5);
                        ctx.lineTo(tx, ty);
                        ctx.stroke();
                        // Arrowhead
                        const hx = Math.sin(rad + Math.PI * 0.8) * 5.5;
                        const hy = -Math.cos(rad + Math.PI * 0.8) * 5.5;
                        const hx2 = Math.sin(rad - Math.PI * 0.8) * 5.5;
                        const hy2 = -Math.cos(rad - Math.PI * 0.8) * 5.5;
                        ctx.beginPath();
                        ctx.moveTo(tx, ty);
                        ctx.lineTo(tx + hx, ty + hy);
                        ctx.lineTo(tx + hx2, ty + hy2);
                        ctx.closePath();
                        ctx.fill();
                    });
                    ctx.restore();
                });
            },
        };
    }

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

    function loadLeaflet() {
        if (window.L) return Promise.resolve(window.L);
        if (leafletPromise) return leafletPromise;

        if (!document.getElementById('leaflet-css')) {
            const css = document.createElement('link');
            css.id = 'leaflet-css';
            css.rel = 'stylesheet';
            css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
            css.crossOrigin = '';
            document.head.appendChild(css);
        }

        leafletPromise = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
            script.crossOrigin = '';
            script.async = true;
            script.onload = () => resolve(window.L);
            script.onerror = () => reject(new Error('Failed to load Leaflet'));
            document.head.appendChild(script);
        });
        return leafletPromise;
    }

    function destroySiteCharts() {
        siteCharts.forEach(c => {
            try { c.destroy(); } catch (_) { /* ignore */ }
        });
        siteCharts = [];
    }

    function formatSiteCoord(lat, lon) {
        if (lat == null || lon == null) return '';
        const ns = lat >= 0 ? 'N' : 'S';
        const ew = lon >= 0 ? 'E' : 'W';
        return `${Math.abs(lat).toFixed(4)}°${ns}, ${Math.abs(lon).toFixed(4)}°${ew}`;
    }

    let leafletApi = null;

    function destroySiteMap() {
        if (siteMapInstance) {
            try { siteMapInstance.remove(); } catch (_) { /* ignore */ }
            siteMapInstance = null;
        }
        siteMapMarkers.clear();
        siteMapPromise = null;
        if (siteMapEl) {
            siteMapEl.innerHTML = '<p class="site-map-loading">Loading port map…</p>';
        }
    }

    function siteDomainCenter() {
        return [
            (SITE_MAP_BOUNDS[0][0] + SITE_MAP_BOUNDS[1][0]) / 2,
            (SITE_MAP_BOUNDS[0][1] + SITE_MAP_BOUNDS[1][1]) / 2,
        ];
    }

    function fitSiteMapToDomain() {
        if (!siteMapInstance) return;
        const map = siteMapInstance;
        map.invalidateSize({ animate: false });
        const size = map.getSize();
        if (!size || size.x < 40 || size.y < 40) {
            setTimeout(fitSiteMapToDomain, 80);
            return;
        }
        // Explicit overview first — avoids a sticky bad zoom from fitting
        // while the Site section was still layout-sizing.
        map.setView(siteDomainCenter(), 4, { animate: false });
        if (leafletApi) {
            map.fitBounds(leafletApi.latLngBounds(SITE_MAP_BOUNDS), {
                padding: [24, 24],
                maxZoom: 5,
                animate: false,
            });
        }
    }

    function showSiteOverview() {
        destroySiteCharts();
        if (siteAbort) {
            siteAbort.abort();
            siteAbort = null;
        }
        if (sitePanel) {
            sitePanel.hidden = true;
            sitePanel.innerHTML = '';
        }
        if (siteOverview) siteOverview.hidden = false;
        if (siteDownloadBtn) siteDownloadBtn.hidden = true;
        if (siteSelect) siteSelect.value = '';
        siteDocCurrent = null;
        siteStaticChartUrl = null;
        if (siteSummary) {
            siteSummary.textContent = siteCatalog.some(s => s.has_data)
                ? 'Click a port marker to open its forecast.'
                : 'Port map ready — site data appears after the forecast pipeline runs.';
        }
        highlightSiteMarker(null);
        localStorage.removeItem('nw_site');
        // Recreate so Port map always returns to the full domain, even if the
        // user had panned/zoomed or a prior fit happened on a zero-size pane.
        destroySiteMap();
        ensureInteractiveSiteMap({ resetView: true });
    }

    function showSiteChartsMode() {
        if (siteOverview) siteOverview.hidden = true;
        if (sitePanel) sitePanel.hidden = false;
    }

    function populateSiteSelect(cfg) {
        if (!siteSelect) return;
        siteCatalog = (cfg?.sites || []).filter(s => s && s.id);
        const withData = siteCatalog.filter(s => s.has_data);
        siteSelect.innerHTML = '';
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = withData.length
            ? SITE_PLACEHOLDER
            : 'No site data yet (next pipeline run)';
        siteSelect.appendChild(placeholder);
        withData.forEach(s => {
            const o = document.createElement('option');
            o.value = s.id;
            o.textContent = s.name || s.id;
            siteSelect.appendChild(o);
        });
    }

    function highlightSiteMarker(siteId) {
        siteMapMarkers.forEach((marker, id) => {
            const active = id === siteId;
            marker.setStyle({
                radius: active ? 9 : 7,
                fillColor: active ? '#0B2340' : '#0B74DE',
                color: '#ffffff',
                weight: active ? 3 : 2,
                fillOpacity: 0.95,
            });
            if (active) marker.bringToFront();
        });
    }

    function isSiteSectionActive() {
        return document.getElementById('site')?.classList.contains('active');
    }

    function ensureInteractiveSiteMap(opts = {}) {
        const resetView = !!opts.resetView;
        if (!siteMapEl || siteOverview?.hidden || !isSiteSectionActive()) return;
        if (siteMapInstance && resetView) {
            destroySiteMap();
        }
        if (siteMapInstance) {
            requestAnimationFrame(() => {
                siteMapInstance.invalidateSize({ animate: false });
            });
            return;
        }
        if (siteMapPromise) return;

        siteMapPromise = Promise.all([
            loadLeaflet(),
            fetch('assets/maps/site_countries.geojson').then(r => {
                if (!r.ok) throw new Error(`Country boundaries HTTP ${r.status}`);
                return r.json();
            }),
        ]).then(([L, countries]) => {
            // Section may have changed while Leaflet/GeoJSON were loading.
            if (!isSiteSectionActive() || siteOverview?.hidden) {
                siteMapPromise = null;
                return;
            }
            leafletApi = L;
            siteMapEl.innerHTML = '';
            const map = L.map(siteMapEl, {
                zoomControl: true,
                attributionControl: false,
                minZoom: 3,
                maxZoom: 12,
                zoomSnap: 0.25,
                wheelDebounceTime: 60,
            });
            siteMapInstance = map;
            map.setMaxBounds(L.latLngBounds(SITE_MAP_BOUNDS).pad(0.35));
            map.setView(siteDomainCenter(), 4, { animate: false });

            L.geoJSON(countries, {
                style: {
                    color: '#607485',
                    weight: 1,
                    fillColor: '#D8E5C1',
                    fillOpacity: 1,
                },
                interactive: false,
            }).addTo(map);

            const markerSites = siteCatalog.filter(
                s => s.lat != null && s.lon != null
            );
            markerSites.forEach(site => {
                    const marker = L.circleMarker([site.lat, site.lon], {
                        radius: 7,
                        color: '#ffffff',
                        weight: 2,
                        fillColor: site.has_data ? '#0B74DE' : '#9AA8B8',
                        fillOpacity: site.has_data ? 0.95 : 0.65,
                        bubblingMouseEvents: false,
                    }).addTo(map);
                    marker.bindTooltip(
                        site.has_data ? site.name : `${site.name} — no data yet`,
                        { direction: 'top', offset: [0, -8], opacity: 0.96 }
                    );
                    if (site.has_data) {
                        marker.on('click', () => selectSite(site.id));
                        marker.on('mouseover', () => marker.bringToFront());
                    }
                    siteMapMarkers.set(site.id, marker);
                });

            requestAnimationFrame(() => fitSiteMapToDomain());
        }).catch(err => {
            console.error('Interactive site map failed:', err);
            siteMapEl.innerHTML =
                '<p class="site-map-error">Port map could not load. Choose a port from the list.</p>';
            siteMapPromise = null;
        });
    }

    function buildChartConfig(group, doc) {
        const axisKey = group.id === 'tide' ? 'tide' : null;
        const labels = siteChartLabels(doc, axisKey);
        const n = labels.length;
        const axisHours = axisKey
            ? (doc.series?.tide?.hours || [])
            : (doc.hours || []);
        const axisTimes = axisKey
            ? (doc.series?.tide?.valid_times || [])
            : (doc.valid_times || []);
        const datasets = [];
        const units = {};
        group.series.forEach(spec => {
            const entry = doc.series?.[spec.key];
            if (!entry || !Array.isArray(entry.values)) return;
            let values = entry.values.slice(0, n);
            while (values.length < n) values.push(null);
            if (!values.some(v => v != null)) return;
            const unit = displaySiteUnit(entry.unit);
            units[spec.yAxisID] = unit;
            datasets.push({
                type: spec.type || 'line',
                label: `${spec.label}${unit ? ` (${unit})` : ''}`,
                data: values,
                borderColor: spec.color,
                backgroundColor: spec.type === 'bar' ? spec.color + '99' : spec.color,
                borderDash: spec.dash || [],
                yAxisID: spec.yAxisID,
                tension: 0.25,
                pointRadius: 0,
                pointHoverRadius: 3,
                borderWidth: 1.8,
                fill: false,
                spanGaps: true,
                _siteKey: spec.key,
            });
        });
        if (!datasets.length) return null;

        const scales = {
            x: {
                ticks: { maxRotation: 45, autoSkip: true, maxTicksLimit: 12, font: { size: 10 } },
                grid: { color: 'rgba(11,35,64,0.06)' },
                title: { display: true, text: 'Valid time (UTC)', font: { size: 10 } },
            },
            y: {
                type: 'linear',
                position: 'left',
                title: { display: !!units.y, text: units.y || '', font: { size: 10 } },
                grid: { color: 'rgba(11,35,64,0.08)' },
                ticks: { font: { size: 10 } },
            },
        };
        if (datasets.some(d => d.yAxisID === 'y1')) {
            scales.y1 = {
                type: 'linear',
                position: 'right',
                title: { display: !!units.y1, text: units.y1 || '', font: { size: 10 } },
                grid: { drawOnChartArea: false },
                ticks: { font: { size: 10 } },
            };
        }
        if (datasets.some(d => d.yAxisID === 'y2')) {
            scales.y2 = {
                type: 'linear',
                position: 'right',
                title: { display: !!units.y2, text: units.y2 || '', font: { size: 10 } },
                grid: { drawOnChartArea: false },
                ticks: { font: { size: 10 } },
                // Offset second right axis so it doesn't sit on top of y1.
                weight: 1,
            };
        }

        const rightPad = datasets.some(d => d.yAxisID === 'y2') ? 48 : 8;

        return {
            type: 'line',
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: { padding: { right: rightPad } },
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { labels: { boxWidth: 12, font: { size: 11 } } },
                    tooltip: {
                        callbacks: {
                            title(items) {
                                const idx = items[0]?.dataIndex;
                                if (idx == null) return '';
                                const iso = axisTimes[idx];
                                if (!iso) return labels[idx] || '';
                                const d = new Date(iso);
                                if (Number.isNaN(d.getTime())) return labels[idx] || '';
                                const wd = WEEKDAYS_SHORT[d.getUTCDay()];
                                const day = d.getUTCDate();
                                const mon = MONTHS[d.getUTCMonth()].slice(0, 3);
                                const hh = String(d.getUTCHours()).padStart(2, '0');
                                const mm = String(d.getUTCMinutes()).padStart(2, '0');
                                const lead = axisHours[idx] ? ` · ${axisHours[idx]}` : '';
                                return `${wd} ${day} ${mon} ${hh}:${mm} UTC${lead}`;
                            },
                            afterBody(items) {
                                const idx = items[0]?.dataIndex;
                                if (idx == null) return '';
                                const lines = [];
                                group.series.forEach(spec => {
                                    if (!spec.dirConvention) return;
                                    const dir = doc.series?.[spec.key]?.dir_deg?.[idx];
                                    const text = formatSiteDir(dir, spec.dirConvention);
                                    if (text) lines.push(`${spec.label} ${text}`);
                                });
                                return lines;
                            },
                        },
                    },
                },
                scales,
            },
            plugins: [siteDirectionArrowsPlugin(group, doc)],
        };
    }

    function downloadBlob(blob, filename) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
    }

    /** Stitch on-screen Chart.js canvases so download matches the web view.

     * Canonical download path: live canvas export (what the user sees).
     * Static assets/sites/{id}/charts.webp is a pipeline fallback for cold
     * loads / when Chart.js has not rendered yet — keep matplotlib layout in
     * sync with SITE_CHART_GROUPS when changing axes or series.
     */
    async function downloadLiveSiteCharts() {
        const site = siteDocCurrent?.site || {};
        const filename = `${site.id || 'site'}_charts.webp`;
        const blocks = [...(sitePanel?.querySelectorAll('.site-chart-block') || [])];
        if (!blocks.length || !siteCharts.length) {
            if (siteStaticChartUrl) {
                downloadBlob(
                    await fetch(siteStaticChartUrl).then(r => r.blob()),
                    filename
                );
            }
            return;
        }

        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const panelW = Math.max(
            ...blocks.map(b => b.querySelector('canvas')?.clientWidth || 0),
            640
        );
        const titleH = 36;
        const blockGap = 18;
        const noteH = 36;
        const blockHeights = blocks.map(b => {
            const title = b.querySelector('.site-chart-title');
            const canvas = b.querySelector('canvas');
            return (title?.offsetHeight || 22) + 8 + (canvas?.clientHeight || 220);
        });
        const totalH =
            titleH + blockHeights.reduce((a, b) => a + b, 0) +
            blockGap * Math.max(0, blocks.length - 1) + noteH + 24;

        const out = document.createElement('canvas');
        out.width = Math.round(panelW * dpr);
        out.height = Math.round(totalH * dpr);
        const ctx = out.getContext('2d');
        ctx.scale(dpr, dpr);
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, panelW, totalH);

        ctx.fillStyle = '#0B2340';
        ctx.font = '600 16px system-ui, sans-serif';
        ctx.fillText(
            `${site.name || site.id || 'Site'} — Site Forecast`,
            8,
            24
        );

        let y = titleH + 4;
        blocks.forEach((block, i) => {
            const title = block.querySelector('.site-chart-title');
            const canvas = block.querySelector('canvas');
            if (title) {
                ctx.fillStyle = '#0B2340';
                ctx.font = '600 13px system-ui, sans-serif';
                ctx.fillText(title.textContent || '', 8, y + 14);
                y += (title.offsetHeight || 22) + 8;
            }
            if (canvas && canvas.width > 0) {
                const h = canvas.clientHeight || 220;
                const w = canvas.clientWidth || panelW;
                ctx.drawImage(canvas, 0, y, w, h);
                y += h;
            }
            if (i < blocks.length - 1) y += blockGap;
        });

        ctx.fillStyle = '#5a6d82';
        ctx.font = '11px system-ui, sans-serif';
        const note =
            'Arrows show where wind/waves propagate and where current flows. ' +
            'Wind/waves = from; current = toward. Tide is astronomical (no surge).';
        ctx.fillText(note, 8, y + 20);

        const blob = await new Promise(resolve => {
            if (out.toBlob) {
                out.toBlob(b => resolve(b), 'image/webp', 0.92);
            } else {
                resolve(null);
            }
        });
        if (blob) {
            downloadBlob(blob, filename);
            return;
        }
        // Fallback for older engines
        const dataUrl = out.toDataURL('image/png');
        const a = document.createElement('a');
        a.href = dataUrl;
        a.download = filename.replace(/\.webp$/, '.png');
        document.body.appendChild(a);
        a.click();
        a.remove();
    }

    function bindSiteDownload() {
        if (!siteDownloadBtn || siteDownloadBtn._nwBound) return;
        siteDownloadBtn.addEventListener('click', e => {
            // Prefer live canvas export so download matches the on-screen charts.
            if (siteCharts.length) {
                e.preventDefault();
                downloadLiveSiteCharts().catch(err => {
                    console.error('Live chart download failed:', err);
                    if (siteStaticChartUrl) {
                        siteDownloadBtn.href = siteStaticChartUrl;
                        // allow default on retry
                    }
                });
            }
        });
        siteDownloadBtn._nwBound = true;
    }

    function renderSiteDoc(doc) {
        if (!sitePanel) return;
        destroySiteCharts();
        showSiteChartsMode();

        const site = doc.site || {};
        const cycles = doc.cycles || {};
        const cycleBits = Object.entries(cycles).map(([k, v]) => `${k} ${v}`).join(' · ');
        const gridBits = Object.entries(doc.grid_points || {}).map(([k, g]) => {
            if (!g || g.lat == null) return null;
            return `${k} grid ${formatSiteCoord(g.lat, g.lon)}`;
        }).filter(Boolean).join(' · ');

        const firstValid = (doc.valid_times || []).find(Boolean);
        const lastValid = [...(doc.valid_times || [])].reverse().find(Boolean);
        let range = '';
        if (firstValid && lastValid) {
            range = `${formatUtcStamp(new Date(firstValid))} → ${formatUtcStamp(new Date(lastValid))}`;
        }

        if (siteSummary) {
            const parts = [
                site.name,
                formatSiteCoord(site.lat, site.lon),
                range,
                cycleBits,
                gridBits,
            ].filter(Boolean);
            siteSummary.textContent = parts.join('  ·  ');
        }

        if (siteDownloadBtn) {
            bindSiteDownload();
            siteDocCurrent = doc;
            if (doc._hasChart !== false) {
                siteStaticChartUrl = `assets/sites/${site.id}/charts.webp`;
                siteDownloadBtn.hidden = false;
                siteDownloadBtn.href = siteStaticChartUrl;
                siteDownloadBtn.download = `${site.id}_charts.webp`;
            } else {
                siteStaticChartUrl = null;
                siteDownloadBtn.hidden = true;
            }
        } else {
            siteDocCurrent = doc;
        }

        highlightSiteMarker(site.id);

        const blocks = SITE_CHART_GROUPS.map(group => {
            const cfg = buildChartConfig(group, doc);
            if (!cfg) return '';
            return `<div class="site-chart-block">
                <h3 class="site-chart-title">${group.title}</h3>
                <div class="site-chart-wrap"><canvas id="siteChart_${group.id}" aria-label="${group.title} chart"></canvas></div>
              </div>`;
        }).filter(Boolean);

        if (!blocks.length) {
            sitePanel.innerHTML = '<p class="site-placeholder">No series available for this site yet.</p>';
            return;
        }

        sitePanel.innerHTML =
            blocks.join('') +
            '<p class="site-dir-note">Arrows show where wind/waves propagate and where current flows. Hover a point for compass direction (wind/waves = from, current = toward). Tide is astronomical only (no surge).</p>';

        loadChartJs().then(Chart => {
            SITE_CHART_GROUPS.forEach(group => {
                const canvas = document.getElementById(`siteChart_${group.id}`);
                if (!canvas) return;
                const cfg = buildChartConfig(group, doc);
                if (!cfg) return;
                siteCharts.push(new Chart(canvas, cfg));
            });
        }).catch(err => {
            console.error(err);
            sitePanel.insertAdjacentHTML(
                'beforeend',
                '<p class="site-placeholder">Charts could not load. You can still use Download chart.</p>'
            );
        });
    }

    function selectSite(id) {
        if (!id) {
            showSiteOverview();
            return;
        }
        if (siteSelect && [...siteSelect.options].some(o => o.value === id)) {
            siteSelect.value = id;
        }
        loadSelectedSite();
    }

    function loadSelectedSite() {
        if (!siteSelect || !sitePanel) return;
        const id = siteSelect.value;
        if (!id) {
            showSiteOverview();
            return;
        }
        localStorage.setItem('nw_site', id);
        highlightSiteMarker(id);

        if (siteCache.has(id)) {
            renderSiteDoc(siteCache.get(id));
            return;
        }

        if (siteAbort) siteAbort.abort();
        siteAbort = new AbortController();
        showSiteChartsMode();
        sitePanel.innerHTML = '<p class="site-placeholder">Loading forecast…</p>';
        if (siteDownloadBtn) siteDownloadBtn.hidden = true;

        fetch(`assets/sites/${id}/forecast.json`, { signal: siteAbort.signal })
            .then(r => {
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                return r.json();
            })
            .then(doc => {
                const chartUrl = `assets/sites/${id}/charts.webp`;
                return fetch(chartUrl, { method: 'HEAD' })
                    .then(h => {
                        doc._hasChart = h.ok;
                        return doc;
                    })
                    .catch(() => {
                        doc._hasChart = false;
                        return doc;
                    });
            })
            .then(doc => {
                siteCache.set(id, doc);
                renderSiteDoc(doc);
            })
            .catch(err => {
                if (err.name === 'AbortError') return;
                console.error('Site forecast load failed:', err);
                sitePanel.innerHTML = '<p class="site-placeholder">Could not load forecast for this site.</p>';
            });
    }

    function initSiteForecast(cfg) {
        populateSiteSelect(cfg);
        if (siteSelect && !siteSelect._nwBound) {
            siteSelect.addEventListener('change', () => selectSite(siteSelect.value));
            siteSelect._nwBound = true;
        }
        if (siteOverviewBtn && !siteOverviewBtn._nwBound) {
            siteOverviewBtn.addEventListener('click', showSiteOverview);
            siteOverviewBtn._nwBound = true;
        }
        siteUiReady = true;
        // Default: port map overview (not auto-open charts).
        showSiteOverview();
    }

    function ensureSiteForecastReady() {
        if (!siteUiReady) return;
        if (siteOverview && !siteOverview.hidden) {
            // Fit the full domain after the Site section becomes visible —
            // creating/fitting while display:none yields a broken zoom.
            ensureInteractiveSiteMap({ resetView: true });
        }
    }


    /* ------------------------------------------------------------
     * Status panel (data update freshness from config.json)
     * ------------------------------------------------------------ */
    const statusPanel = document.getElementById('statusPanel');

    // Max age (hours) before a dataset cycle is flagged stale in the UI.
    const FRESHNESS_HOURS = {
        gfswave: 18,
        gfsatmos: 18,
        hycom: 48,
        cmems: 48,
        default: 24,
    };

    const MONTHS_SHORT = [
        'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];

    function parseCycleUtc(cycle) {
        if (!cycle || !/^\d{10}$/.test(String(cycle))) return null;
        const c = String(cycle);
        return new Date(Date.UTC(
            +c.slice(0, 4),
            +c.slice(4, 6) - 1,
            +c.slice(6, 8),
            +c.slice(8, 10)
        ));
    }

    function formatUtcStamp(d) {
        if (!d || Number.isNaN(d.getTime())) return '—';
        const hh = String(d.getUTCHours()).padStart(2, '0');
        const mm = String(d.getUTCMinutes()).padStart(2, '0');
        return `${MONTHS_SHORT[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()} ${hh}:${mm} UTC`;
    }

    function formatAgeHours(hours) {
        if (hours == null || Number.isNaN(hours)) return '—';
        if (hours < 1) return '<1h';
        if (hours < 48) return `${Math.round(hours)}h`;
        return `${(hours / 24).toFixed(1)}d`;
    }

    function freshnessForDataset(dsKey, cycle, deployState) {
        if (deployState === 'unavailable') {
            return { key: 'unavailable', label: 'Unavailable' };
        }
        const init = parseCycleUtc(cycle);
        if (!init) {
            return { key: 'unknown', label: 'Unknown' };
        }
        const ageH = (Date.now() - init.getTime()) / 3600000;
        const limit = FRESHNESS_HOURS[dsKey] ?? FRESHNESS_HOURS.default;
        if (ageH <= limit) {
            return { key: 'fresh', label: 'Fresh', ageH };
        }
        return { key: 'stale', label: 'Stale', ageH };
    }

    function serviceStateLabel(state) {
        if (state === 'operational') return 'Operational';
        if (state === 'planned') return 'Planned';
        if (state === 'degraded') return 'Degraded';
        return state || '—';
    }

    function renderStatusPanel(cfg) {
        if (!statusPanel) return;

        const status = cfg?.status;
        if (!status) {
            // Fallback: derive a minimal view from top-level cycles.
            const cycles = cfg?.cycles || {};
            if (!Object.keys(cycles).length && !cfg?.cycle) {
                statusPanel.innerHTML =
                    '<p class="status-empty">No update metadata yet. Run the forecast pipeline to populate status.</p>';
                return;
            }
            const rows = Object.entries(cycles).map(([ds, cycle]) => {
                const fresh = freshnessForDataset(ds, cycle, 'operational');
                const init = parseCycleUtc(cycle);
                return `<tr>
                    <td>${ds}</td>
                    <td><code>${cycle || '—'}</code></td>
                    <td>${formatUtcStamp(init)}</td>
                    <td>${formatAgeHours(fresh.ageH)}</td>
                    <td><span class="status-badge status-badge--${fresh.key}">${fresh.label}</span></td>
                    <td>—</td>
                </tr>`;
            }).join('');
            statusPanel.innerHTML = `
                <p class="status-meta">Pipeline metadata incomplete — showing cycle stamps only.</p>
                <div class="status-table-wrap">
                  <table class="status-table">
                    <thead>
                      <tr>
                        <th>Dataset</th><th>Cycle</th><th>Initial time</th>
                        <th>Age</th><th>Freshness</th><th>Coverage</th>
                      </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                  </table>
                </div>`;
            return;
        }

        const generated = status.generated_at
            ? formatUtcStamp(new Date(status.generated_at))
            : '—';
        const fc = status.forecast || {};
        const forecastLine = (fc.max_hours != null && fc.hour_step != null)
            ? `Forecast window F000…F${String(fc.max_hours).padStart(3, '0')} step ${fc.hour_step}h`
            : '';

        const serviceCards = (status.services || []).map(svc => `
            <div class="status-service">
              <span class="status-service-label">${svc.label || svc.id}</span>
              <span class="status-badge status-badge--${svc.state || 'planned'}">${serviceStateLabel(svc.state)}</span>
            </div>`).join('');

        const dsEntries = Object.entries(status.datasets || {});
        const dsRows = dsEntries.map(([ds, meta]) => {
            const cycle = meta.cycle || cfg?.cycles?.[ds] || null;
            const fresh = freshnessForDataset(ds, cycle, meta.state);
            const init = parseCycleUtc(cycle);
            const lead = (meta.hours_first && meta.hours_last)
                ? `${meta.hours_first}–${meta.hours_last}`
                : '—';
            const coverage = meta.state === 'unavailable'
                ? 'No maps'
                : `${meta.regions || 0} region${meta.regions === 1 ? '' : 's'}, ${meta.hour_count || 0} frame${meta.hour_count === 1 ? '' : 's'} (${lead})`;
            const products = (meta.products || []).length
                ? meta.products.join(', ')
                : '—';
            return `<tr>
                <td>
                  <div class="status-ds-name">${meta.label || ds}</div>
                  <div class="status-ds-source">${meta.source || ds}</div>
                </td>
                <td><code>${cycle || '—'}</code></td>
                <td>${formatUtcStamp(init)}</td>
                <td>${formatAgeHours(fresh.ageH)}</td>
                <td><span class="status-badge status-badge--${fresh.key}">${fresh.label}</span></td>
                <td>
                  <div>${coverage}</div>
                  <div class="status-ds-products">${products}</div>
                </td>
              </tr>`;
        }).join('');

        statusPanel.innerHTML = `
            <div class="status-meta-row">
              <p class="status-meta"><strong>Last pipeline update:</strong> ${generated}</p>
              ${forecastLine ? `<p class="status-meta">${forecastLine}</p>` : ''}
            </div>
            <h3 class="status-heading">Services</h3>
            <div class="status-services">${serviceCards}</div>
            <h3 class="status-heading">Data sources</h3>
            <div class="status-table-wrap">
              <table class="status-table">
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Cycle</th>
                    <th>Initial time</th>
                    <th>Age</th>
                    <th>Freshness</th>
                    <th>Coverage</th>
                  </tr>
                </thead>
                <tbody>${dsRows || '<tr><td colspan="6">No datasets reported.</td></tr>'}</tbody>
              </table>
            </div>
            <p class="status-footnote">GFS cycles are considered fresh within ~18h of initial time (pipeline runs ~5h after each synoptic cycle). HYCOM surface fields often lag ~1 day — up to ~48h is expected.</p>`;
    }


    /* ------------------------------------------------------------
     * Load CONFIG.json
     * ------------------------------------------------------------ */
    fetch("assets/config/config.json")
        .then(r => r.json())
        .then(json => {
            CONFIG = json;

            // If config does not include the region keys we use, add them as placeholders:
            // (so dropdown will show our region IDs)
            const cfgRegions = Object.assign({}, CONFIG.regions || {});
            for (const r of regionDefs) {
                if (!cfgRegions.hasOwnProperty(r.id)) {
                    cfgRegions[r.id] = {
                        forecast_types: {
                            'Wind and Waves': {
                                parameters: {
                                    surface_wind: [],
                                    swh: [],
                                    swell: [],
                                },
                                models: ['GFS'],
                                timestamps: [],
                                dataset: 'gfswave',
                            },
                        },
                    };
                }
            }
            // Re-group each region's parameters into dataset-family categories.
            for (const key of Object.keys(CONFIG.regions)) {
                if (isRegionPlaceholder(key)) continue;
                CONFIG.regions[key] = regroupRegion(CONFIG.regions[key]);
            }

            populateRegionSelect();
            selectDefaultRegion();

            regionSelect.onchange = loadForecastTypes;
            forecastSelect.onchange = loadParameters;
            parameterSelect.onchange = loadModels;
            modelSelect.onchange = loadTimes;
            timeSelect.onchange = updateMap;

            loadForecastTypes();
            renderStatusPanel(CONFIG);
            initSiteForecast(CONFIG);
        })
        .catch(err => {
            console.error("Failed to load config.json:", err);
            // still populate region dropdown from our regionDefs to allow clicks to work
            const tmp = {};
            regionDefs.forEach(d => tmp[d.id] = {});
            CONFIG = { regions: tmp };
            populateRegionSelect();
            selectDefaultRegion();
            regionSelect.onchange = loadForecastTypes;
            loadForecastTypes();
            renderStatusPanel(CONFIG);
            initSiteForecast(CONFIG);
        });

    document.querySelectorAll('.home-service').forEach(row => {
        row.addEventListener('click', () => {
            const target = row.dataset.target;
            localStorage.setItem('nw_section', target);
            setActive(target);
        });
    });

})();
