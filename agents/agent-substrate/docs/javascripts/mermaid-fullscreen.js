/* Pan / zoom / reset controls + fullscreen for every Mermaid diagram.
   Ports the behaviour of ravi-ui's <Mermaid> component to vanilla JS:
   a 3x3 control pad (pan arrows + zoom in/out + reset), drag-to-pan,
   pinch-to-zoom on touch, and a fullscreen toggle. We transform the
   .mermaid element itself, so we never need to find the rendered <svg>. */
(function () {
  var ZOOM_STEP = 0.25,
    PAN_STEP = 60,
    MIN = 0.4,
    MAX = 4;

  var IC = {
    expand:
      '<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3m13-5v3a2 2 0 0 1-2 2h-3"/>',
    collapse:
      '<path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3M3 16h3a2 2 0 0 1 2 2v3m13-5h-3a2 2 0 0 0-2 2v3"/>',
    zoomIn:
      '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>',
    zoomOut:
      '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/><line x1="8" y1="11" x2="14" y2="11"/>',
    reset:
      '<polyline points="3 4 3 9 8 9"/><path d="M4.5 9.5A7.5 7.5 0 1 1 3.5 14"/>',
    up: '<polyline points="18 15 12 9 6 15"/>',
    down: '<polyline points="6 9 12 15 18 9"/>',
    left: '<polyline points="15 18 9 12 15 6"/>',
    right: '<polyline points="9 18 15 12 9 6"/>',
  };

  function icon(inner) {
    return (
      '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
      'stroke-linejoin="round">' +
      inner +
      "</svg>"
    );
  }

  function makeBtn(cls, title, inner) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.title = title;
    b.setAttribute("aria-label", title);
    b.innerHTML = icon(inner);
    return b;
  }

  function wire(mermaidEl) {
    if (mermaidEl.dataset.fsWired) return;
    var parent = mermaidEl.parentNode;
    if (
      parent &&
      parent.classList &&
      parent.classList.contains("mermaid-viewport")
    ) {
      mermaidEl.dataset.fsWired = "1";
      return;
    }
    mermaidEl.dataset.fsWired = "1";

    var wrap = document.createElement("div");
    wrap.className = "mermaid-fs-wrap";
    var view = document.createElement("div");
    view.className = "mermaid-viewport";
    parent.insertBefore(wrap, mermaidEl);
    wrap.appendChild(view);
    view.appendChild(mermaidEl);

    var scale = 1,
      tx = 0,
      ty = 0;
    /* Material re-renders by replacing the .mermaid node, so always target the
       live element currently inside the viewport rather than a stale ref. */
    function apply() {
      var el = view.querySelector(".mermaid") || mermaidEl;
      el.style.transform =
        "translate(" + tx + "px," + ty + "px) scale(" + scale + ")";
    }
    function setScale(s) {
      scale = Math.min(MAX, Math.max(MIN, +s.toFixed(2)));
      apply();
    }
    function reset() {
      scale = 1;
      tx = 0;
      ty = 0;
      apply();
    }

    /* 3x3 control pad */
    var pad = document.createElement("div");
    pad.className = "mermaid-controls";
    function spacer() {
      var d = document.createElement("span");
      d.className = "mermaid-ctl-spacer";
      pad.appendChild(d);
    }
    function ctl(title, inner, fn) {
      var b = makeBtn("mermaid-ctl-btn", title, inner);
      b.addEventListener("click", function (e) {
        e.stopPropagation();
        fn();
      });
      pad.appendChild(b);
    }
    spacer();
    ctl("Pan up", IC.up, function () { ty += PAN_STEP; apply(); });
    ctl("Zoom in", IC.zoomIn, function () { setScale(scale + ZOOM_STEP); });
    ctl("Pan left", IC.left, function () { tx += PAN_STEP; apply(); });
    ctl("Reset", IC.reset, reset);
    ctl("Pan right", IC.right, function () { tx -= PAN_STEP; apply(); });
    spacer();
    ctl("Pan down", IC.down, function () { ty -= PAN_STEP; apply(); });
    ctl("Zoom out", IC.zoomOut, function () { setScale(scale - ZOOM_STEP); });
    wrap.appendChild(pad);

    /* Fullscreen toggle */
    var fsBtn = makeBtn("mermaid-fs-btn", "Open fullscreen", IC.expand);
    fsBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (!document.fullscreenElement) {
        wrap.requestFullscreen && wrap.requestFullscreen();
      } else {
        document.exitFullscreen && document.exitFullscreen();
      }
    });
    wrap.appendChild(fsBtn);
    document.addEventListener("fullscreenchange", function () {
      var on = document.fullscreenElement === wrap;
      fsBtn.innerHTML = icon(on ? IC.collapse : IC.expand);
      fsBtn.title = on ? "Exit fullscreen" : "Open fullscreen";
    });

    /* Drag-to-pan (mouse) + pinch-to-zoom (touch) via Pointer Events */
    var pts = {},
      pinchDist = 0,
      mid = null,
      dragStart = null;

    view.addEventListener("pointerdown", function (e) {
      pts[e.pointerId] = { x: e.clientX, y: e.clientY };
      var ids = Object.keys(pts);
      if (e.pointerType === "mouse" && ids.length === 1) {
        view.setPointerCapture(e.pointerId);
        dragStart = { x: e.clientX - tx, y: e.clientY - ty };
        view.classList.add("grabbing");
      } else if (ids.length === 2) {
        var a = pts[ids[0]],
          b = pts[ids[1]];
        pinchDist = Math.hypot(a.x - b.x, a.y - b.y);
        mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        dragStart = null;
      }
    });

    view.addEventListener("pointermove", function (e) {
      if (!pts[e.pointerId]) return;
      pts[e.pointerId] = { x: e.clientX, y: e.clientY };
      var ids = Object.keys(pts);
      if (ids.length === 2) {
        e.preventDefault();
        var a = pts[ids[0]],
          b = pts[ids[1]];
        var d = Math.hypot(a.x - b.x, a.y - b.y);
        var m = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        if (pinchDist > 0) setScale(scale * (d / pinchDist));
        if (mid) {
          tx += m.x - mid.x;
          ty += m.y - mid.y;
        }
        pinchDist = d;
        mid = m;
        apply();
      } else if (dragStart && e.pointerType === "mouse") {
        tx = e.clientX - dragStart.x;
        ty = e.clientY - dragStart.y;
        apply();
      }
    });

    function release(e) {
      delete pts[e.pointerId];
      var ids = Object.keys(pts);
      if (ids.length < 2) {
        pinchDist = 0;
        mid = null;
      }
      if (ids.length === 0) {
        dragStart = null;
        view.classList.remove("grabbing");
      }
    }
    view.addEventListener("pointerup", release);
    view.addEventListener("pointercancel", release);

    apply();
  }

  function scan() {
    document.querySelectorAll(".mermaid").forEach(wire);
  }

  var t = null;
  var observer = new MutationObserver(function () {
    clearTimeout(t);
    t = setTimeout(scan, 120);
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  scan();
  setTimeout(scan, 500);
})();
