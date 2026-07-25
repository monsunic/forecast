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

    setActive(localStorage.getItem('nw_section') || 'map');


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
        sst: 'Sea Temperature',
        sss: 'Sea Salinity',
        ssh: 'Sea Surface Height',
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
    };

    const CATEGORY_ORDER = ['Wind and Waves', 'Atmosphere', 'Ocean'];

    // Preferred parameter order within a category (unlisted keys keep discovery order).
    const PARAM_ORDER = {
        'Wind and Waves': ['surface_wind', 'swh', 'swell'],
        Atmosphere: ['rain_rh700', 'mslp_wind', 'sfc_temp', 'rh'],
        Ocean: ['sst', 'sss', 'ssh'],
    };

    const MODEL_DATASET = {
        GFS: 'gfswave',
        WW3: 'gfswave',
    };

    const STATIC_MAP = 'assets/maps/staticmap.png';

    let playInterval = null;
    let pendingMapSrc = null;
    let preloadGen = 0;
    const preloadCache = new Set();

    function showStaticMap() {
        pendingMapSrc = null;
        mapImage.src = STATIC_MAP;
    }

    mapImage.addEventListener('error', () => {
        const failed = pendingMapSrc || mapImage.getAttribute('src');
        if (!failed || failed.includes('staticmap')) return;
        console.warn('Map image missing, falling back to overview:', failed);
        pendingMapSrc = null;
        mapImage.src = STATIC_MAP;
    });

    // Keep width/height attrs in sync so the browser can reserve space accurately.
    mapImage.addEventListener('load', () => {
        if (mapImage.naturalWidth > 0) {
            mapImage.width = mapImage.naturalWidth;
            mapImage.height = mapImage.naturalHeight;
        }
    });

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

    function formatTimeLabel(fLabel) {
        const m = /^F(\d+)$/.exec(fLabel || '');
        if (!m) return fLabel;
        const fh = parseInt(m[1], 10);
        const cycle =
            forecastMeta()?.cycle ||
            CONFIG?.cycles?.[forecastMeta()?.dataset] ||
            CONFIG?.cycle ||
            CONFIG?.updated;
        if (cycle && /^\d{10}$/.test(String(cycle))) {
            const c = String(cycle);
            const valid = new Date(Date.UTC(
                +c.slice(0, 4),
                +c.slice(4, 6) - 1,
                +c.slice(6, 8),
                +c.slice(8, 10) + fh
            ));
            const months = [
                'January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November', 'December',
            ];
            const month = months[valid.getUTCMonth()];
            const day = valid.getUTCDate();
            const year = valid.getUTCFullYear();
            const hh = String(valid.getUTCHours()).padStart(2, '0');
            const mm = String(valid.getUTCMinutes()).padStart(2, '0');
            return `${month} ${day}, ${year} at ${hh}:${mm} UTC`;
        }
        return fh === 0 ? 't+0h (analysis)' : `t+${fh}h`;
    }

    function relabelTimes() {
        [...timeSelect.options].forEach(opt => {
            if (timeIndexFromLabel(opt.value)) opt.textContent = formatTimeLabel(opt.value);
        });
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
            populate(timeSelect, ["Time (Select a region first)"]);

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
            populate(timeSelect, ['Time (Select type first)']);
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
            populate(timeSelect, ['Time (Select type first)']);
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
            populate(timeSelect, ['Time (Select parameter first)']);
            updateMap();
            return;
        }
        const paramTimes = meta.parameters?.[param];
        const times = Array.isArray(paramTimes) && paramTimes.length
            ? paramTimes.filter(t => timeIndexFromLabel(t))
            : (meta.timestamps || []).filter(t => timeIndexFromLabel(t));
        populate(
            timeSelect,
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
        });

    document.querySelectorAll('.service-card').forEach(card => {
        card.addEventListener('click', () => {
            const target = card.dataset.target;
            localStorage.setItem('nw_section', target);
            setActive(target);
        });
    });

})();
