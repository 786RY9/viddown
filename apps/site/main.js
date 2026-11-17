/*
 * viddown landing page. No libraries, no build step.
 *  1. Frame bar demo in the hero (the one animated moment on the page).
 *  2. Repo links, latest release assets and OS detection for downloads.
 *  3. Live stats line (hidden unless the server answers).
 *  4. Contact form: POST JSON to the server, mailto fallback.
 */
(function () {
  "use strict";

  var cfg = window.VIDDOWN_CONFIG || {};
  var REPO = cfg.repo || "786RY9/viddown";
  var API = (cfg.api || "").replace(/\/+$/, "");
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function storageGet(key) {
    try { return window.sessionStorage.getItem(key); } catch (e) { return null; }
  }
  function storageSet(key, value) {
    try { window.sessionStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }

  function fetchJSON(url, opts, timeoutMs) {
    var ctrl = "AbortController" in window ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, timeoutMs || 6000) : null;
    var init = Object.assign({}, opts || {}, ctrl ? { signal: ctrl.signal } : {});
    return fetch(url, init).finally(function () { if (timer) clearTimeout(timer); });
  }

  /* ------------------------------------------------------------------ */
  /* 1. Frame bar demo                                                   */
  /* ------------------------------------------------------------------ */

  // Same phase weights as packages/core phaseSegments("video"):
  // video 0.85 and audio 0.15 of the download share, merge takes the last 4%.
  var POST = 0.04;
  var PHASES = [
    { label: "Video", weight: 0.85 * (1 - POST), status: "Downloading video" },
    { label: "Audio", weight: 0.15 * (1 - POST), status: "Downloading audio" },
    { label: "Merge", weight: POST, status: "Merging into one MP4" },
  ];
  var TOTAL_MB = 412.6;
  // Demo timeline in ms: where each phase ends.
  var TIMELINE = [4600, 5900, 6900];

  var bar = $("#framebar");
  var demo = bar && {
    frames: [],
    runs: [],
    total: 0,
    fraction: 0,
    width: 0,
    raf: 0,
    started: false,
  };

  function cssPx(el, name, fallback) {
    var v = parseFloat(getComputedStyle(el).getPropertyValue(name));
    return isNaN(v) ? fallback : v;
  }

  function buildBar() {
    var width = Math.floor(bar.clientWidth);
    if (!width || width === demo.width) return;
    demo.width = width;

    var fw = cssPx(bar, "--fw", 4);
    var fg = cssPx(bar, "--fg", 2);
    var seg = cssPx(bar, "--seg", 8);
    var usable = width - seg * (PHASES.length - 1);
    var total = Math.max(PHASES.length, Math.floor((usable + fg) / (fw + fg)));
    var counts = PHASES.map(function (p) { return Math.max(1, Math.round(total * p.weight)); });
    // Run gaps eat a frame's worth of space per run; trim the biggest run so
    // nothing gets clipped by rounding.
    var sum = counts.reduce(function (a, b) { return a + b; }, 0);
    var available = Math.floor((usable + fg * PHASES.length) / (fw + fg));
    while (sum > available) { counts[0] -= 1; sum -= 1; }

    bar.textContent = "";
    bar.classList.add("has-labels");
    demo.frames = [];
    demo.runs = [];
    PHASES.forEach(function (p, r) {
      var run = document.createElement("div");
      run.className = "run";
      var strip = document.createElement("div");
      strip.className = "run-frames";
      for (var k = 0; k < counts[r]; k++) {
        var f = document.createElement("span");
        f.className = "frame";
        strip.appendChild(f);
        demo.frames.push({ el: f, o: 0 });
      }
      var label = document.createElement("span");
      label.className = "run-label";
      label.textContent = p.label;
      run.appendChild(strip);
      run.appendChild(label);
      bar.appendChild(run);
      demo.runs.push(run);
    });
    demo.total = demo.frames.length;
    paintBar(demo.fraction, true);
  }

  function paintBar(fraction, force) {
    var filled = fraction * demo.total;
    for (var i = 0; i < demo.frames.length; i++) {
      var o = Math.max(0, Math.min(1, filled - i));
      o = Math.round(o * 20) / 20;
      var fr = demo.frames[i];
      if (force || fr.o !== o) {
        fr.o = o;
        fr.el.style.setProperty("--o", String(o));
      }
    }
  }

  function phaseAt(fraction) {
    var acc = 0;
    for (var i = 0; i < PHASES.length; i++) {
      acc += PHASES[i].weight;
      if (fraction < acc - 1e-9) return i;
    }
    return PHASES.length - 1;
  }

  function fmtClock(sec) {
    sec = Math.max(0, Math.round(sec));
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  var figs = {
    status: $("#job-status"),
    pct: $("#job-pct"),
    bytes: $("#job-bytes"),
    speed: $("#job-speed"),
    eta: $("#job-eta"),
    replay: $("#job-replay"),
  };
  var speedNow = 11.4;
  var lastSpeedAt = 0;

  function paintFigures(fraction, now) {
    var done = fraction >= 1;
    var phase = phaseAt(fraction);
    var dlShare = 1 - POST;
    var mb = Math.min(fraction, dlShare) / dlShare * TOTAL_MB;

    if (now - lastSpeedAt > 280) {
      lastSpeedAt = now;
      speedNow = 10.6 + Math.random() * 1.9;
    }

    figs.pct.textContent = Math.floor(fraction * 100) + "%";
    demo.runs.forEach(function (run, i) { run.classList.toggle("is-active", !done && i === phase); });

    if (done) {
      figs.status.textContent = "Saved to Downloads";
      figs.bytes.textContent = TOTAL_MB.toFixed(1) + " MB";
      figs.speed.hidden = true;
      figs.eta.hidden = true;
      return;
    }
    figs.status.textContent = PHASES[phase].status;
    figs.bytes.textContent = mb.toFixed(1) + " of " + TOTAL_MB.toFixed(1) + " MB";
    if (phase === PHASES.length - 1) {
      figs.speed.hidden = true;
      figs.eta.textContent = "a few seconds left";
    } else {
      figs.speed.hidden = false;
      figs.speed.textContent = speedNow.toFixed(1) + " MB/s";
      figs.eta.textContent = fmtClock((TOTAL_MB - mb) / speedNow + 2) + " left";
    }
    figs.eta.hidden = false;
  }

  function fractionAt(t) {
    var start = 0;
    var base = 0;
    for (var i = 0; i < PHASES.length; i++) {
      var end = TIMELINE[i];
      if (t < end) {
        var local = (t - start) / (end - start);
        // A touch of unevenness so it reads like a network, not a tween.
        var wobble = i === 0 ? Math.sin(local * Math.PI * 3) * 0.015 : 0;
        local = Math.max(0, Math.min(1, local + wobble));
        return base + PHASES[i].weight * local;
      }
      base += PHASES[i].weight;
      start = end;
    }
    return 1;
  }

  function play() {
    cancelAnimationFrame(demo.raf);
    figs.replay.hidden = true;
    var t0 = performance.now();
    function step(now) {
      var f = fractionAt(now - t0);
      demo.fraction = f;
      paintBar(f, false);
      paintFigures(f, now);
      if (f < 1) {
        demo.raf = requestAnimationFrame(step);
      } else {
        figs.replay.hidden = false;
      }
    }
    demo.raf = requestAnimationFrame(step);
  }

  function showStill() {
    // Reduced motion: a single, legible mid-download frame.
    cancelAnimationFrame(demo.raf);
    demo.fraction = 0.62;
    paintBar(demo.fraction, true);
    speedNow = 11.4;
    lastSpeedAt = Infinity;
    paintFigures(demo.fraction, 0);
    figs.replay.hidden = true;
  }

  function startDemo() {
    if (demo.started) return;
    demo.started = true;
    if (reduceMotion.matches) showStill();
    else play();
  }

  if (demo) {
    buildBar();
    if ("ResizeObserver" in window) {
      new ResizeObserver(function () { buildBar(); }).observe(bar);
    } else {
      window.addEventListener("resize", buildBar);
    }
    figs.replay.addEventListener("click", function () {
      if (reduceMotion.matches) { showStill(); return; }
      play();
    });
    reduceMotion.addEventListener && reduceMotion.addEventListener("change", function () {
      if (reduceMotion.matches) showStill();
    });

    var kick = function () { setTimeout(startDemo, 350); };
    var whenVisible = function () {
      if (!("IntersectionObserver" in window)) { kick(); return; }
      var io = new IntersectionObserver(function (entries) {
        if (entries.some(function (e) { return e.isIntersecting; })) { io.disconnect(); kick(); }
      }, { threshold: 0.4 });
      io.observe(bar);
    };
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(whenVisible);
    else whenVisible();
  }

  /* ------------------------------------------------------------------ */
  /* 2. Downloads: repo links, release assets, OS detection             */
  /* ------------------------------------------------------------------ */

  $all("[data-repo-link]").forEach(function (a) {
    var suffix = a.getAttribute("data-repo-link");
    a.href = "https://github.com/" + REPO + (suffix ? "/" + suffix : "");
  });

  var OS_NAMES = { windows: "Windows", mac: "macOS", linux: "Linux", android: "Android" };

  function detectOS() {
    var ua = navigator.userAgent || "";
    var plat = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    if (/android/i.test(ua)) return "android";
    if (/iphone|ipad|ipod/i.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "ios";
    if (/win/i.test(plat) || /Windows/.test(ua)) return "windows";
    if (/mac/i.test(plat) || /Mac OS X/.test(ua)) return "mac";
    if (/linux|x11|cros/i.test(plat + " " + ua)) return "linux";
    return null;
  }

  function detectArch() {
    var uad = navigator.userAgentData;
    if (!uad || !uad.getHighEntropyValues) return Promise.resolve(null);
    return uad.getHighEntropyValues(["architecture"])
      .then(function (v) { return v.architecture || null; })
      .catch(function () { return null; });
  }

  var os = detectOS();
  var arch = null;

  var ASSET_PATTERNS = {
    "win-exe": [/(x64|x86_64|amd64).*setup\.exe$/i, /setup\.exe$/i, /\.exe$/i],
    "win-msi": [/(x64|x86_64|amd64).*\.msi$/i, /\.msi$/i],
    "mac-arm": [/(aarch64|arm64|universal).*\.dmg$/i],
    "mac-intel": [/(x64|x86_64|intel|universal).*\.dmg$/i],
    "linux-appimage": [/(amd64|x86_64).*\.AppImage$/i, /\.AppImage$/i],
    "linux-deb": [/(amd64|x86_64).*\.deb$/i, /\.deb$/i],
    "android-apk": [/arm64-v8a.*\.apk$/i, /universal.*\.apk$/i, /\.apk$/i],
  };
  var ARM_LINUX = {
    "linux-appimage": [/(aarch64|arm64).*\.AppImage$/i],
    "linux-deb": [/(aarch64|arm64).*\.deb$/i],
  };

  function pickAsset(assets, key) {
    var patterns = (arch === "arm" && ARM_LINUX[key] ? ARM_LINUX[key] : []).concat(ASSET_PATTERNS[key] || []);
    for (var i = 0; i < patterns.length; i++) {
      for (var j = 0; j < assets.length; j++) {
        if (patterns[i].test(assets[j].name)) return assets[j];
      }
    }
    return null;
  }

  function fmtSize(bytes) {
    if (!bytes) return "";
    if (bytes >= 1e9) return (bytes / 1e9).toFixed(2) + " GB";
    return (bytes / 1e6).toFixed(1) + " MB";
  }

  function fmtDate(iso) {
    try {
      return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
    } catch (e) { return ""; }
  }

  var heroCta = $("#hero-cta");
  var heroNote = $("#hero-note");
  var heroOther = $("#hero-other");

  function currentRow() { return os ? $('.platform[data-os="' + os + '"]') : null; }

  function applyOS() {
    $all(".platform").forEach(function (li) { li.classList.remove("is-current"); });

    if (os === "ios") {
      $("#ios-note").hidden = false;
      heroNote.textContent = "There's no iPhone version. Get viddown for your computer or Android phone.";
      return;
    }
    var row = currentRow();
    if (!row) return;
    row.classList.add("is-current");

    // On an Intel Mac, put the Intel build first so it gets the highlight.
    if (os === "mac" && arch === "x86") {
      var files = $(".platform-files", row);
      var intel = $('[data-asset="mac-intel"]', files);
      if (intel && files.firstElementChild !== intel) {
        files.insertBefore(intel, files.firstElementChild);
        intel.classList.remove("btn-quiet");
        $('[data-asset="mac-arm"]', files).classList.add("btn-quiet");
      }
    }

    var primary = $(".platform-files .btn", row);
    heroCta.textContent = "Download for " + OS_NAMES[os];
    heroCta.href = primary.getAttribute("data-resolved") ? primary.href : "#download";
    heroOther.textContent = "Other platforms";
    var others = Object.keys(OS_NAMES).filter(function (k) { return k !== os; }).map(function (k) { return OS_NAMES[k]; });
    var size = primary.getAttribute("data-size");
    var version = document.documentElement.getAttribute("data-version");
    var lead = version ? "Version " + version + (size ? ", " + size : "") + ". " : "";
    heroNote.textContent = lead + "Also for " + others.slice(0, -1).join(", ") + " and " + others[others.length - 1] + ".";
  }

  function applyRelease(rel) {
    if (!rel || !Array.isArray(rel.assets)) return;
    $all("[data-asset]").forEach(function (a) {
      var asset = pickAsset(rel.assets, a.getAttribute("data-asset"));
      if (!asset) return;
      a.href = asset.browser_download_url;
      a.setAttribute("data-resolved", "1");
      var size = fmtSize(asset.size);
      a.setAttribute("data-size", size);
      var slot = $(".size", a);
      if (slot) slot.textContent = size;
    });

    var version = String(rel.tag_name || "").replace(/^v/i, "");
    if (version) {
      document.documentElement.setAttribute("data-version", version);
      var line = $("#release-line");
      line.textContent = "";
      line.appendChild(document.createTextNode("Version "));
      var v = document.createElement("span");
      v.className = "fig";
      v.textContent = version;
      line.appendChild(v);
      var date = rel.published_at ? fmtDate(rel.published_at) : "";
      line.appendChild(document.createTextNode(date ? ", released " + date + ". " : ". "));
      var notes = document.createElement("a");
      notes.href = rel.html_url || ("https://github.com/" + REPO + "/releases/latest");
      notes.textContent = "Release notes";
      line.appendChild(notes);
    }
  }

  function loadRelease() {
    var key = "viddown:release:" + REPO;
    var cached = storageGet(key);
    if (cached) {
      try {
        var c = JSON.parse(cached);
        if (Date.now() - c.at < 60 * 60 * 1000) return Promise.resolve(c.rel);
      } catch (e) { /* ignore */ }
    }
    // The list endpoint answers 200 with [] before the first release, where
    // /releases/latest would log a 404 in the console.
    return fetchJSON("https://api.github.com/repos/" + REPO + "/releases?per_page=10", {
      headers: { Accept: "application/vnd.github+json" },
    }, 6000).then(function (res) {
      if (!res.ok) throw new Error("releases " + res.status);
      return res.json();
    }).then(function (list) {
      var rel = (Array.isArray(list) ? list : []).filter(function (r) { return !r.draft && !r.prerelease; })[0];
      if (!rel) throw new Error("no release yet");
      storageSet(key, JSON.stringify({ at: Date.now(), rel: {
        tag_name: rel.tag_name,
        published_at: rel.published_at,
        html_url: rel.html_url,
        assets: (rel.assets || []).map(function (a) {
          return { name: a.name, size: a.size, browser_download_url: a.browser_download_url };
        }),
      } }));
      return rel;
    });
  }

  applyOS();
  Promise.all([
    detectArch().then(function (a) { arch = a; }),
    loadRelease().then(applyRelease).catch(function () { /* links stay on releases/latest */ }),
  ]).then(applyOS, applyOS);

  /* ------------------------------------------------------------------ */
  /* 3. Live stats                                                       */
  /* ------------------------------------------------------------------ */

  function pickNumber(obj, keys) {
    for (var i = 0; i < keys.length; i++) {
      var v = obj && obj[keys[i]];
      if (typeof v === "number" && isFinite(v) && v > 0) return v;
    }
    return 0;
  }

  if (API) {
    fetchJSON(API + "/v1/stats", { headers: { Accept: "application/json" } }, 4000)
      .then(function (res) { if (!res.ok) throw new Error("stats"); return res.json(); })
      .then(function (s) {
        // Server shape (server/viddown_server/store.py):
        // { totals: { download, cloud_download, install, launch }, last30d: {...}, installsByPlatform: {...} }
        var t = (s && s.totals) || {};
        var downloads = (pickNumber(t, ["download"]) || 0) + (pickNumber(t, ["cloud_download"]) || 0);
        var installs = pickNumber(t, ["install"]);
        if (!downloads && !installs) return;
        var nf = new Intl.NumberFormat();
        var el = $("#stats");
        el.textContent = "";
        function num(n) {
          var span = document.createElement("span");
          span.className = "fig";
          span.textContent = nf.format(n);
          return span;
        }
        if (downloads) {
          el.appendChild(num(downloads));
          el.appendChild(document.createTextNode(downloads === 1 ? " video downloaded" : " videos downloaded"));
        }
        if (downloads && installs) el.appendChild(document.createTextNode(" on "));
        if (installs) {
          el.appendChild(num(installs));
          el.appendChild(document.createTextNode(installs === 1 ? " device" : " devices"));
        }
        el.appendChild(document.createTextNode(" so far."));
        el.hidden = false;
      })
      .catch(function () { /* stay hidden */ });
  }

  /* ------------------------------------------------------------------ */
  /* 4. Credits: optional sponsor line                                   */
  /* ------------------------------------------------------------------ */

  // Shown only once a GitHub Sponsors page is set in config.js.
  var sponsor = $("#sponsor");
  var sponsorLink = $("#sponsor-link");
  if (sponsor && sponsorLink && /^https:[/][/]github[.]com[/]sponsors[/]/.test(cfg.sponsors || "")) {
    sponsorLink.href = cfg.sponsors;
    sponsor.hidden = false;
  }
})();
