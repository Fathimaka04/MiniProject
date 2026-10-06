/* GazeAssist caregiver board — shared page behaviour. */
(function () {
  "use strict";

  /* ------------------------------------------------------------------
     Small helpers
     ------------------------------------------------------------------ */

  var TOAST_ICONS = {
    success: "bi-check-circle-fill",
    info: "bi-info-circle-fill",
    warning: "bi-exclamation-triangle-fill",
    danger: "bi-x-octagon-fill"
  };

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function htmlToElement(html) {
    var template = document.createElement("template");
    template.innerHTML = html.trim();
    return template.content.firstElementChild;
  }

  /** Same look as the Django-messages toasts in partials/_messages.html. */
  function showToast(text, level) {
    level = level || "success";
    var container = document.getElementById("toast-container");
    if (!container) return;
    var el = htmlToElement(
      '<div class="toast ga-toast ga-toast-' + level + '" role="' + (level === "danger" ? "alert" : "status") +
      '" aria-live="' + (level === "danger" ? "assertive" : "polite") + '" aria-atomic="true" data-bs-delay="6000">' +
      '<div class="d-flex align-items-start">' +
      '<span class="ga-toast-icon"><i class="bi ' + TOAST_ICONS[level] + '" aria-hidden="true"></i></span>' +
      '<div class="toast-body">' + escapeHtml(text) + "</div>" +
      '<button type="button" class="btn-close me-2 mt-2" data-bs-dismiss="toast" aria-label="Close"></button>' +
      "</div></div>"
    );
    container.appendChild(el);
    el.addEventListener("hidden.bs.toast", function () { el.remove(); });
    bootstrap.Toast.getOrCreateInstance(el).show();
  }

  /** POST a form with fetch and return its JSON (the CSRF token travels in the form data). */
  function postForm(form) {
    return fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { "Accept": "application/json", "X-Requested-With": "fetch" },
      credentials: "same-origin"
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var err = new Error(data.error || "HTTP " + res.status);
          err.data = data;
          throw err;
        }
        return data;
      });
    });
  }

  /* ------------------------------------------------------------------
     Toasts, filters, modals, uploads
     ------------------------------------------------------------------ */

  function initToasts() {
    document.querySelectorAll(".toast").forEach(function (el) {
      bootstrap.Toast.getOrCreateInstance(el).show();
    });
  }

  /**
   * Client-side filter.
   * <input data-filter-input="#list" data-filter-empty="#empty-msg">
   * Items inside #list with a data-filter-text attribute are shown/hidden.
   */
  function initFilters() {
    document.querySelectorAll("[data-filter-input]").forEach(function (input) {
      var scope = document.querySelector(input.dataset.filterInput);
      var empty = input.dataset.filterEmpty ? document.querySelector(input.dataset.filterEmpty) : null;
      if (!scope) return;

      input.addEventListener("input", function () {
        var query = input.value.trim().toLowerCase();
        var shown = 0;
        scope.querySelectorAll("[data-filter-text]").forEach(function (item) {
          var match = item.dataset.filterText.indexOf(query) !== -1;
          item.hidden = !match;
          if (match) shown += 1;
        });
        if (empty) empty.hidden = shown !== 0;
      });
    });
  }

  /** Delete confirmation modal: copies data-delete-url / data-delete-name from the clicked button. */
  function initDeleteModal() {
    var modal = document.getElementById("delete-modal");
    if (!modal) return;
    var form = modal.querySelector("[data-delete-form]");
    var label = modal.querySelector("[data-delete-label]");

    modal.addEventListener("show.bs.modal", function (event) {
      var trigger = event.relatedTarget;
      if (!trigger) return;
      form.action = trigger.dataset.deleteUrl;
      label.textContent = trigger.dataset.deleteName || "this item";
    });
  }

  /** File drop zone: show the chosen file name and highlight on drag. */
  function initFileDrops() {
    document.querySelectorAll("[data-file-drop]").forEach(function (zone) {
      var input = zone.querySelector("input[type=file]");
      var nameEl = zone.querySelector("[data-file-name]");
      if (!input) return;

      function showName() {
        var file = input.files && input.files[0];
        if (!nameEl) return;
        if (file) {
          var sizeMb = (file.size / (1024 * 1024)).toFixed(2);
          nameEl.textContent = "Selected: " + file.name + " (" + sizeMb + " MB)";
          zone.classList.add("has-file");
        } else {
          nameEl.textContent = "";
          zone.classList.remove("has-file");
        }
      }

      input.addEventListener("change", showName);
      ["dragenter", "dragover"].forEach(function (type) {
        zone.addEventListener(type, function () { zone.classList.add("is-dragging"); });
      });
      ["dragleave", "drop"].forEach(function (type) {
        zone.addEventListener(type, function () { zone.classList.remove("is-dragging"); });
      });
    });
  }

  /* ------------------------------------------------------------------
     Live updates (polling with fetch)
     ------------------------------------------------------------------ */

  var MAX_BACKOFF_MS = 30000;
  var pollers = [];

  /** Top-bar pill: "Live", or "Reconnecting…" while requests fail. */
  function setLiveState(state) {
    var pill = document.querySelector("[data-live-indicator]");
    if (!pill) return;
    var text = pill.querySelector("[data-live-indicator-text]");
    pill.hidden = false;
    pill.classList.toggle("is-retrying", state === "retry");
    text.textContent = state === "retry" ? "Reconnecting…" : "Live";
  }

  /**
   * Call `url` every `interval` ms and pass the JSON to `onData`.
   * On errors the delay doubles (up to 30 s) and resets after a success.
   * If the session has ended (401, or a redirect to the login page), reload.
   * Returns { now() } to poll immediately (e.g. right after acknowledging).
   */
  function startPolling(url, interval, getParams, onData) {
    var delay = interval;
    var timer = null;
    var inFlight = false;
    var again = false;

    function schedule(ms) {
      window.clearTimeout(timer);
      timer = window.setTimeout(tick, ms);
    }

    function tick() {
      if (inFlight) { again = true; return; }
      inFlight = true;
      var query = new URLSearchParams(getParams()).toString();
      fetch(url + (query ? "?" + query : ""), {
        headers: { "Accept": "application/json", "X-Requested-With": "fetch" },
        credentials: "same-origin",
        cache: "no-store"
      })
        .then(function (res) {
          var isJson = (res.headers.get("content-type") || "").indexOf("application/json") !== -1;
          if (res.status === 401 || res.status === 403 || res.status === 404 || res.redirected || !isJson) {
            window.location.reload();
            throw new Error("reload");
          }
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.json();
        })
        .then(function (data) {
          onData(data);
          setLiveState("live");
          delay = interval;
        })
        .catch(function (err) {
          if (err && err.message === "reload") return;
          setLiveState("retry");
          delay = Math.min(delay * 2, MAX_BACKOFF_MS);
        })
        .then(function () {
          inFlight = false;
          if (again) { again = false; schedule(0); } else { schedule(delay); }
        });
    }

    setLiveState("live");
    schedule(interval);
    var poller = { now: function () { schedule(0); } };
    pollers.push(poller);
    return poller;
  }

  function pollNow() {
    pollers.forEach(function (p) { p.now(); });
  }

  /** Replace an element's HTML only when it changed (avoids needless screen-reader announcements). */
  function setHtml(el, html) {
    if (el && el.dataset.liveHtml !== html) {
      el.innerHTML = html;
      el.dataset.liveHtml = html;
    }
  }

  /** Update every element showing an alert count (hiding "0" badges). */
  function setCount(selector, count) {
    document.querySelectorAll(selector).forEach(function (el) {
      el.textContent = count;
      if (el.hasAttribute("data-live-hide-zero")) el.hidden = count === 0;
    });
  }

  function setNavAlerts(count) {
    var badge = document.querySelector("[data-live-nav-alerts]");
    if (!badge || typeof count !== "number") return;
    badge.firstChild.textContent = count;
    badge.hidden = count === 0;
  }

  /* ------------------------------------------------------------------
     SOS banner + alarm (Phase 4)
     ------------------------------------------------------------------ */

  var Sos = (function () {
    var REPEAT_MS = 20000;
    var banner, body, unlockBtn, toggleBtn;
    var knownIds = null;
    var audioCtx = null;
    var muted = false;
    var pendingSound = false;
    var repeatTimer = null;
    var titleTimer = null;
    var baseTitle = document.title;

    function storeMuted(value) {
      try { window.localStorage.setItem("ga-sos-muted", value ? "1" : "0"); } catch (e) { /* private mode */ }
    }

    function loadMuted() {
      try { return window.localStorage.getItem("ga-sos-muted") === "1"; } catch (e) { return false; }
    }

    /** Two-tone alarm made with the Web Audio API (no sound file needed). */
    function playTones() {
      var t0 = audioCtx.currentTime;
      for (var i = 0; i < 3; i++) {
        var start = t0 + i * 0.45;
        var osc = audioCtx.createOscillator();
        var gain = audioCtx.createGain();
        osc.type = "square";
        osc.frequency.setValueAtTime(880, start);
        osc.frequency.setValueAtTime(660, start + 0.2);
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(0.12, start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.4);
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.start(start);
        osc.stop(start + 0.42);
      }
    }

    function alarm() {
      if (muted || !audioCtx || banner.hidden) return;
      if (audioCtx.state !== "running") {
        // Browsers block sound until the user has clicked or pressed a key on the page.
        pendingSound = true;
        unlockBtn.hidden = false;
        return;
      }
      playTones();
    }

    function unlock() {
      if (!audioCtx || audioCtx.state === "running") return;
      audioCtx.resume().then(function () {
        unlockBtn.hidden = true;
        if (pendingSound) {
          pendingSound = false;
          alarm();
        }
      });
    }

    function renderToggle() {
      toggleBtn.setAttribute("aria-pressed", muted ? "false" : "true");
      toggleBtn.querySelector("[data-sound-label]").textContent = muted ? "Sound off" : "Sound on";
      toggleBtn.querySelector("i").className = "bi " + (muted ? "bi-volume-mute-fill" : "bi-volume-up-fill");
      if (muted) unlockBtn.hidden = true;
    }

    function flashTitle(active, count) {
      window.clearInterval(titleTimer);
      titleTimer = null;
      document.title = baseTitle;
      if (!active) return;
      var on = false;
      titleTimer = window.setInterval(function () {
        on = !on;
        document.title = on ? "🔴 SOS (" + count + ") · " + baseTitle : baseTitle;
      }, 1000);
    }

    function apply(ids, count) {
      var active = count > 0;
      banner.hidden = !active;
      document.body.classList.toggle("has-sos", active);

      var hasNew = active && ids.some(function (id) { return !knownIds || knownIds.indexOf(id) === -1; });
      knownIds = ids;
      if (hasNew) {
        alarm();
        if (navigator.vibrate) navigator.vibrate([300, 150, 300, 150, 600]);
      }

      if (active && !repeatTimer) repeatTimer = window.setInterval(alarm, REPEAT_MS);
      if (!active && repeatTimer) { window.clearInterval(repeatTimer); repeatTimer = null; }
      flashTitle(active, count);
    }

    function init() {
      banner = document.querySelector("[data-sos-banner]");
      if (!banner) return false;
      body = banner.querySelector("[data-sos-body]");
      unlockBtn = banner.querySelector("[data-sound-unlock]");
      toggleBtn = banner.querySelector("[data-sound-toggle]");
      muted = loadMuted();
      renderToggle();

      var AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) audioCtx = new AudioCtx();

      // Any click or key press on the page is enough to allow sound.
      ["pointerdown", "keydown"].forEach(function (type) {
        document.addEventListener(type, unlock, { passive: true });
      });
      unlockBtn.addEventListener("click", unlock);
      toggleBtn.addEventListener("click", function () {
        muted = !muted;
        storeMuted(muted);
        renderToggle();
        if (!muted) alarm();
      });

      var ids = Array.prototype.map.call(body.querySelectorAll("[data-sos-id]"), function (el) {
        return parseInt(el.dataset.sosId, 10);
      });
      apply(ids, ids.length);
      return true;
    }

    function update(data) {
      if (!banner || typeof data.sos_html !== "string") return;
      setHtml(body, data.sos_html);
      apply(data.sos_ids || [], data.sos_count || 0);
    }

    return { init: init, update: update };
  })();

  /** Shared by all live endpoints: SOS banner + sidebar badge. */
  function handleCommon(data) {
    Sos.update(data);
    setNavAlerts(data.nav_open_alerts);
  }

  /* ------------------------------------------------------------------
     Page-specific live updates
     ------------------------------------------------------------------ */

  /** Dashboard: stats, patient card statuses and the open-alerts list. */
  function initDashboardLive() {
    var root = document.querySelector("[data-live-dashboard]");
    if (!root) return false;
    var interval = parseInt(root.dataset.interval, 10) || 3000;

    startPolling(root.dataset.liveDashboard, interval, function () { return {}; }, function (data) {
      Object.keys(data.stats).forEach(function (key) {
        var value = root.querySelector('[data-live-stat="' + key + '"] .stat-value');
        if (value) value.textContent = data.stats[key];
      });

      data.patients.forEach(function (p) {
        var card = root.querySelector('[data-live-patient-card="' + p.id + '"]');
        if (!card) return;
        card.classList.toggle("has-alert", p.unack_alerts > 0);
        var dot = card.querySelector("[data-live-dot]");
        if (dot) dot.classList.toggle("is-online", p.online);
        setHtml(card.querySelector("[data-live-status]"), p.status_html);
        setHtml(card.querySelector("[data-live-alerts]"), p.alerts_html);
      });

      setHtml(root.querySelector("[data-live-alerts-list]"), data.alerts_html);
      setCount("[data-live-open-alerts]", data.stats.unacknowledged);

      var welcome = root.querySelector("[data-live-welcome]");
      var count = data.stats.unacknowledged;
      if (welcome && String(count) !== welcome.dataset.count) {
        welcome.dataset.count = count;
        welcome.innerHTML = count
          ? '<span class="text-alert fw-semibold">' + count + " alert" + (count === 1 ? " needs" : "s need") + " your attention.</span>"
          : "Here is how your patients are doing.";
      }
      handleCommon(data);
    });
    return true;
  }

  /** Insert new feed items at the top of a feed list. */
  function prependFeedItems(feed, items) {
    if (!feed || !items.length) return;
    var grouped = feed.hasAttribute("data-grouped");
    var anchor = feed.firstElementChild;

    if (grouped) {
      var today = feed.querySelector("[data-today]");
      if (!today) {
        today = htmlToElement('<li class="feed-date" data-today><span>Today</span></li>');
        feed.insertBefore(today, feed.firstElementChild);
      }
      anchor = today.nextElementSibling;
    }

    // items arrive newest first; insert oldest first so the newest ends on top.
    items.slice().reverse().forEach(function (item) {
      if (!item.matches_filter || feed.querySelector('[data-request-id="' + item.id + '"]')) return;
      var el = htmlToElement(item.html);
      el.classList.add("is-new");
      feed.insertBefore(el, anchor);
      anchor = el;
    });

    var limit = parseInt(feed.dataset.limit, 10);
    if (limit) {
      var entries = feed.querySelectorAll(".feed-item");
      for (var i = limit; i < entries.length; i++) entries[i].remove();
    }

    feed.hidden = false;
    var empty = feed.parentElement.querySelector("[data-live-feed-empty]");
    if (empty) empty.hidden = true;
  }

  /** Refresh items whose state changed (e.g. acknowledged), or remove them if filtered out. */
  function refreshFeedItems(items) {
    items.forEach(function (item) {
      document.querySelectorAll('[data-live-feed] [data-request-id="' + item.id + '"]').forEach(function (old) {
        if (!item.matches_filter) {
          old.remove();
        } else {
          old.replaceWith(htmlToElement(item.html));
        }
      });
    });
  }

  /** Patient pages: header status, alert counts and (if present) the live feed. */
  function initPatientLive() {
    var root = document.querySelector("[data-live-patient]");
    if (!root) return false;
    var interval = parseInt(root.dataset.interval, 10) || 3000;
    var state = { after: root.dataset.latestId || "0", since: root.dataset.since || "" };
    var feed = document.querySelector("[data-live-feed]");

    function params() {
      var p = { after: state.after, since: state.since };
      if (feed && feed.dataset.filter) p.filter = feed.dataset.filter;
      return p;
    }

    startPolling(root.dataset.livePatient, interval, params, function (data) {
      var dot = root.querySelector("[data-live-dot]");
      if (dot) dot.classList.toggle("is-online", data.online);
      setHtml(root.querySelector("[data-live-status]"), data.status_html);

      setCount("[data-live-open-alerts]", data.open_alerts);
      var plural = root.querySelector("[data-live-open-alerts-plural]");
      if (plural) plural.textContent = data.open_alerts === 1 ? "" : "s";
      var alertLink = root.querySelector("[data-live-alert-link]");
      if (alertLink) alertLink.hidden = data.open_alerts === 0;

      if (feed) {
        prependFeedItems(feed, data.items);
        refreshFeedItems(data.updated);
      }
      handleCommon(data);

      state.after = String(data.latest_id);
      state.since = data.server_time;
    });
    return true;
  }

  /** Any other signed-in page: poll just the SOS banner. */
  function initAlertsLive() {
    var banner = document.querySelector("[data-sos-banner]");
    if (!banner) return;
    var interval = parseInt(banner.dataset.interval, 10) || 3000;
    startPolling(banner.dataset.alertsUrl, interval, function () { return {}; }, handleCommon);
  }

  /* ------------------------------------------------------------------
     Acknowledge buttons and messages (Phase 4)
     ------------------------------------------------------------------ */

  /** Every [data-ack-form] (SOS banner, feeds, dashboard) is sent with fetch, no page reload. */
  function initAckForms() {
    document.addEventListener("submit", function (event) {
      var form = event.target.closest("[data-ack-form]");
      if (!form) return;
      event.preventDefault();
      var button = form.querySelector("button[type=submit]");
      if (button.disabled) return;
      var label = button.innerHTML;
      button.disabled = true;
      button.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span> Saving…';

      postForm(form)
        .then(function (data) {
          showToast(data.message, data.already ? "info" : "success");
          document.querySelectorAll('[data-sos-id="' + data.id + '"]').forEach(function (el) { el.remove(); });
          form.remove();
          pollNow();
        })
        .catch(function () {
          button.disabled = false;
          button.innerHTML = label;
          showToast("Could not save. Check the connection and try again.", "danger");
        });
    });
  }

  /** Message form: quick replies, character counter and sending without reload. */
  function initMessageForm() {
    var form = document.querySelector("[data-message-form]");
    if (!form) return;
    var textarea = form.querySelector("textarea");
    var counter = form.querySelector("[data-char-count]");
    var list = document.querySelector("[data-message-list]");
    var max = parseInt(textarea.getAttribute("maxlength"), 10) || 280;

    function updateCount() {
      var len = textarea.value.length;
      counter.textContent = len + " / " + max;
      counter.classList.toggle("text-alert", len > max - 20);
    }

    function setError(text) {
      var box = form.querySelector("[data-message-error]");
      if (!text) {
        textarea.classList.remove("is-invalid");
        textarea.removeAttribute("aria-invalid");
        if (box) box.remove();
        return;
      }
      textarea.classList.add("is-invalid");
      textarea.setAttribute("aria-invalid", "true");
      if (!box) {
        box = document.createElement("div");
        box.className = "invalid-feedback d-block";
        box.id = "message-error";
        box.setAttribute("data-message-error", "");
        textarea.closest(".form-floating").after(box);
        textarea.setAttribute("aria-describedby", "message-error");
      }
      box.innerHTML = '<i class="bi bi-exclamation-circle" aria-hidden="true"></i> ' + escapeHtml(text);
    }

    document.querySelectorAll("[data-quick-reply]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        textarea.value = chip.dataset.quickReply;
        updateCount();
        setError("");
        textarea.focus();
      });
    });
    textarea.addEventListener("input", function () { updateCount(); setError(""); });
    updateCount();

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (!textarea.value.trim()) {
        setError("Write a short message first.");
        textarea.focus();
        return;
      }
      var button = form.querySelector("button[type=submit]");
      var label = button.innerHTML;
      button.disabled = true;
      button.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span> Sending…';

      postForm(form)
        .then(function (data) {
          var empty = list.querySelector("[data-message-empty]");
          if (empty) empty.remove();
          list.insertBefore(htmlToElement(data.html), list.firstElementChild);
          textarea.value = "";
          updateCount();
          showToast(data.message, "success");
        })
        .catch(function (err) {
          if (err.data && err.data.error) {
            setError(err.data.error);
          } else {
            showToast("Could not send. Check the connection and try again.", "danger");
          }
        })
        .then(function () {
          button.disabled = false;
          button.innerHTML = label;
        });
    });
  }

  /* ------------------------------------------------------------------
     Gaze app pairing page + confirm prompts
     ------------------------------------------------------------------ */

  /** <form data-confirm="Are you sure…?"> asks before submitting. */
  function initConfirmForms() {
    document.addEventListener("submit", function (event) {
      var form = event.target.closest("form[data-confirm]");
      if (form && !window.confirm(form.dataset.confirm)) event.preventDefault();
    }, true);
  }

  /** Code page: countdown to expiry, and turn green once the gaze app has connected. */
  function initPairing() {
    var box = document.querySelector("[data-pairing]");
    if (!box) return;
    var expires = Date.parse(box.dataset.expires);
    var before = box.dataset.pairedBefore ? Date.parse(box.dataset.pairedBefore) : 0;
    var countdown = box.querySelector("[data-pairing-countdown]");
    var done = document.querySelector("[data-pairing-done]");
    var expiredBox = document.querySelector("[data-pairing-expired]");
    var finished = false;

    function finish() {
      finished = true;
      window.clearInterval(clock);
      window.clearInterval(poller);
    }

    function tick() {
      var left = Math.max(0, Math.round((expires - Date.now()) / 1000));
      var mins = Math.floor(left / 60);
      var secs = String(left % 60).padStart(2, "0");
      countdown.textContent = left ? "Expires in " + mins + ":" + secs : "Expired";
      if (!left && !finished) {
        finish();
        box.hidden = true;
        expiredBox.hidden = false;
      }
    }

    function poll() {
      fetch(box.dataset.statusUrl, { headers: { "Accept": "application/json" }, credentials: "same-origin", cache: "no-store" })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (data) {
          if (finished || !data || !data.connected || !data.paired_at) return;
          if (Date.parse(data.paired_at) > before) {
            finish();
            box.hidden = true;
            done.querySelector("[data-pairing-device]").textContent =
              "The gaze app on " + (data.device_name || "the patient's computer") + " is now connected.";
            done.hidden = false;
            showToast("Gaze app connected.", "success");
          }
        })
        .catch(function () { /* try again on the next poll */ });
    }

    var clock = window.setInterval(tick, 1000);
    var poller = window.setInterval(poll, 2000);
    tick();
  }

  document.addEventListener("DOMContentLoaded", function () {
    initConfirmForms();
    initPairing();
    initToasts();
    initFilters();
    initDeleteModal();
    initFileDrops();
    Sos.init();
    initAckForms();
    initMessageForm();
    var pagePolls = initDashboardLive();
    pagePolls = initPatientLive() || pagePolls;
    if (!pagePolls) initAlertsLive();
  });
})();
