/**
 * SquircleShift — Living background of glowing superellipse shapes.
 *
 * Features:
 *  - Simplex noise drives organic movement across the entire grid
 *  - Cursor interaction (desktop): soft wave of light with momentum
 *  - Autonomous ambient hotspots (mobile): multiple drifting focal points
 *  - Neighboring cells influence each other for a connected feel
 *  - Smooth morphing, pulsing, glow-intensity shifts
 *  - Zero dependencies
 */
const SquircleShift = (() => {
  'use strict';

  /* ══════════════════════════════════════════════════════════════════════
     2D Simplex Noise  (Stefan Gustavson — compact port)
     ══════════════════════════════════════════════════════════════════════ */
  const Simplex2D = (() => {
    const F2 = 0.5 * (Math.sqrt(3) - 1);
    const G2 = (3 - Math.sqrt(3)) / 6;
    const grad3 = [
      [1,1],[-1,1],[1,-1],[-1,-1],
      [1,0],[-1,0],[0,1],[0,-1],
    ];
    const perm = new Uint8Array(512);
    const p = [151,160,137,91,90,15,131,13,201,95,96,53,194,233,7,225,
      140,36,103,30,69,142,8,99,37,240,21,10,23,190,6,148,247,120,234,75,
      0,26,197,62,94,252,219,203,117,35,11,32,57,177,33,88,237,149,56,87,
      174,20,125,136,171,168,68,175,74,165,71,134,139,48,27,166,77,146,
      158,231,83,111,229,122,60,211,133,230,220,105,92,41,55,46,245,40,
      244,102,143,54,65,25,63,161,1,216,80,73,209,76,132,187,208,89,18,
      169,200,196,135,130,116,188,159,86,164,100,109,198,173,186,3,64,
      52,217,226,250,124,123,5,202,38,147,118,126,255,82,85,212,207,206,
      59,227,47,16,58,17,182,189,28,42,223,183,170,213,119,248,152,2,44,
      154,163,70,221,153,101,155,167,43,172,9,129,22,39,253,19,98,108,
      110,79,113,224,232,178,185,112,104,218,246,97,228,251,34,242,193,
      238,210,144,12,191,179,162,241,81,51,145,235,249,14,239,107,49,
      192,214,31,181,199,106,157,184,84,204,176,115,121,50,45,127,4,
      150,254,138,236,205,93,222,114,67,29,24,72,243,141,128,195,78,66,
      215,61,156,180];
    for (let i = 0; i < 256; i++) { perm[i] = perm[i + 256] = p[i]; }

    function noise(xin, yin) {
      let n0, n1, n2;
      const s = (xin + yin) * F2;
      const i = Math.floor(xin + s);
      const j = Math.floor(yin + s);
      const t = (i + j) * G2;
      const X0 = i - t, Y0 = j - t;
      const x0 = xin - X0, y0 = yin - Y0;
      let i1, j1;
      if (x0 > y0) { i1 = 1; j1 = 0; } else { i1 = 0; j1 = 1; }
      const x1 = x0 - i1 + G2, y1 = y0 - j1 + G2;
      const x2 = x0 - 1 + 2 * G2, y2 = y0 - 1 + 2 * G2;
      const ii = i & 255, jj = j & 255;
      let t0 = 0.5 - x0*x0 - y0*y0;
      if (t0 < 0) n0 = 0;
      else { t0 *= t0; const g = grad3[perm[ii + perm[jj]] & 7]; n0 = t0*t0*(g[0]*x0+g[1]*y0); }
      let t1 = 0.5 - x1*x1 - y1*y1;
      if (t1 < 0) n1 = 0;
      else { t1 *= t1; const g = grad3[perm[ii+i1 + perm[jj+j1]] & 7]; n1 = t1*t1*(g[0]*x1+g[1]*y1); }
      let t2 = 0.5 - x2*x2 - y2*y2;
      if (t2 < 0) n2 = 0;
      else { t2 *= t2; const g = grad3[perm[ii+1 + perm[jj+1]] & 7]; n2 = t2*t2*(g[0]*x2+g[1]*y2); }
      return 70 * (n0 + n1 + n2); // −1 … 1
    }
    return { noise };
  })();

  /* ══════════════════════════════════════════════════════════════════════
     Configuration
     ══════════════════════════════════════════════════════════════════════ */
  const defaults = {
    speed:           0.3,
    colorLayers:     3,
    gridFrequency:   20,
    gridIntensity:   1.0,
    lineThickness:   0.06,
    phaseOffset:     10,
    waveSpeed:       0.2,
    waveIntensity:   0.1,
    spiralIntensity: 1.0,
    centerX:         1.0,
    centerY:         1.0,
    falloff:         1.0,
    colorTint:       '#6c8cff',
    darkBackground:  '#0f1117',
    brightness:      1.2,
  };

  /* ══════════════════════════════════════════════════════════════════════
     State
     ══════════════════════════════════════════════════════════════════════ */
  let canvas, ctx, animId, _resizeHandler, _mouseHandler, _touchHandler, _leaveHandler;
  let opts = { ...defaults };
  let dpr = 1, w = 0, h = 0;
  let time = 0;

  // Cursor state — smooth, momentum-based tracking
  let cursorRawX = -9999, cursorRawY = -9999;   // actual pointer
  let cursorX    = -9999, cursorY    = -9999;    // smoothed (momentum)
  let cursorVelX = 0,     cursorVelY = 0;        // velocity for momentum
  let cursorActive = false;                       // has the pointer entered?
  let lastPointerTime = 0;

  // Autonomous ambient hotspots — drift around the canvas
  const hotspots = [];
  const NUM_HOTSPOTS = 4;

  /* ══════════════════════════════════════════════════════════════════════
     Helpers
     ══════════════════════════════════════════════════════════════════════ */
  function hexToRgb(hex) {
    hex = hex.replace('#', '');
    if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
    const n = parseInt(hex, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function lerp(a, b, t) { return a + (b - a) * t; }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  /** Superellipse: |x/a|^n + |y/b|^n = 1 */
  function superellipseXY(cx, cy, rx, ry, n, steps) {
    ctx.beginPath();
    for (let i = 0; i <= steps; i++) {
      const t = (i / steps) * Math.PI * 2;
      const ct = Math.cos(t), st = Math.sin(t);
      const act = Math.abs(ct), ast = Math.abs(st);
      const rr = Math.pow(Math.pow(act, n) + Math.pow(ast, n), -1 / n);
      const x = cx + ct * rr * rx;
      const y = cy + st * rr * ry;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
  }

  /* ── Hotspot init / update ────────────────────────────────────────── */
  function initHotspots() {
    hotspots.length = 0;
    for (let i = 0; i < NUM_HOTSPOTS; i++) {
      hotspots.push({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 40,
        vy: (Math.random() - 0.5) * 40,
        radius: Math.min(w, h) * (0.15 + Math.random() * 0.2),
        phase: Math.random() * 100,
        speed: 0.15 + Math.random() * 0.2,
      });
    }
  }

  function updateHotspots(dt) {
    for (const hs of hotspots) {
      // Organic drift using noise
      const nx = Simplex2D.noise(hs.x * 0.001, hs.y * 0.001 + hs.phase);
      const ny = Simplex2D.noise(hs.x * 0.001 + 50, hs.y * 0.001 + hs.phase);
      hs.vx += nx * 12 * dt;
      hs.vy += ny * 12 * dt;
      // Damping
      hs.vx *= 0.995;
      hs.vy *= 0.995;
      // Speed limit
      const spd = Math.sqrt(hs.vx*hs.vx + hs.vy*hs.vy);
      if (spd > 60) { hs.vx *= 60/spd; hs.vy *= 60/spd; }
      hs.x += hs.vx * dt;
      hs.y += hs.vy * dt;
      // Wrap around
      if (hs.x < -hs.radius) hs.x += w + hs.radius * 2;
      if (hs.x > w + hs.radius) hs.x -= w + hs.radius * 2;
      if (hs.y < -hs.radius) hs.y += h + hs.radius * 2;
      if (hs.y > h + hs.radius) hs.y -= h + hs.radius * 2;
    }
  }

  /* ── Cursor momentum ──────────────────────────────────────────────── */
  function updateCursor(dt) {
    if (!cursorActive) {
      // When no cursor, smoothly drift cursor position toward center
      // so hotspots are the main driver
      cursorX = lerp(cursorX, w * 0.5, 0.01);
      cursorY = lerp(cursorY, h * 0.5, 0.01);
      return;
    }
    // Lerp with momentum — smooth, delayed following
    const smoothing = 0.04; // lower = more lag / momentum
    cursorVelX = lerp(cursorVelX, (cursorRawX - cursorX) * smoothing, 0.08);
    cursorVelY = lerp(cursorVelY, (cursorRawY - cursorY) * smoothing, 0.08);
    cursorX += cursorVelX;
    cursorY += cursorVelY;
  }

  /* ══════════════════════════════════════════════════════════════════════
     Core renderer
     ══════════════════════════════════════════════════════════════════════ */
  function render() {
    if (!canvas || !ctx) return;

    const rawDt = 0.016;
    const dt = rawDt * opts.speed;
    time += dt;

    // ── Update systems ──
    updateCursor(rawDt);
    updateHotspots(rawDt);

    // ── Clear with dark bg ──
    ctx.fillStyle = opts.darkBackground;
    ctx.fillRect(0, 0, w, h);

    const rgb = hexToRgb(opts.colorTint);
    const cx = w / 2;
    const cy = h / 2;

    // ── Grid layout ──
    const cols = Math.max(4, Math.round(opts.gridFrequency));
    const rows = Math.max(3, Math.round(cols * (h / w)));
    const cellW = w / cols;
    const cellH = h / rows;
    const baseR = Math.min(cellW, cellH) * 0.30;

    const steps = 36; // points per superellipse

    // Noise spatial scale
    const noiseScale = 0.0025;
    // Time offsets for noise layers
    const tSlow  = time * 0.08;  // very slow evolution
    const tMed   = time * 0.22;  // medium drift
    const tFast  = time * 0.55;  // quick shimmer

    // ── Render each cell ──
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const baseX = (col + 0.5) * cellW;
        const baseY = (row + 0.5) * cellH;

        // ── 1. Noise-driven displacement (connected, organic flow) ──
        const nDispX = Simplex2D.noise(baseX * noiseScale + tSlow, baseY * noiseScale) * cellW * 1.8;
        const nDispY = Simplex2D.noise(baseX * noiseScale, baseY * noiseScale + tSlow + 100) * cellH * 1.8;

        // ── 2. Cursor influence — soft wave, fades with distance ──
        let cursorInfluence = 0;
        let cursorDispX = 0, cursorDispY = 0;
        if (cursorActive) {
          const dx = baseX - cursorX;
          const dy = baseY - cursorY;
          const dist = Math.sqrt(dx*dx + dy*dy);
          // Influence radius: ~250px, smooth falloff
          const influenceRadius = Math.min(w, h) * 0.35;
          const rawInf = 1 - clamp(dist / influenceRadius, 0, 1);
          // Smooth easing — not linear
          cursorInfluence = rawInf * rawInf * (3 - 2 * rawInf); // smoothstep
          // Push cells outward from cursor (gentle)
          if (dist > 1) {
            cursorDispX = (dx / dist) * cursorInfluence * cellW * 0.6;
            cursorDispY = (dy / dist) * cursorInfluence * cellH * 0.6;
          }
        }

        // ── 3. Hotspot influence — organic focal drift ──
        let hotspotInfluence = 0;
        for (const hs of hotspots) {
          const dx = baseX - hs.x;
          const dy = baseY - hs.y;
          const dist = Math.sqrt(dx*dx + dy*dy);
          const raw = 1 - clamp(dist / hs.radius, 0, 1);
          const eased = raw * raw; // quadratic falloff
          hotspotInfluence += eased;
        }
        hotspotInfluence = clamp(hotspotInfluence, 0, 1.5);

        // ── Combined position ──
        const posX = baseX + nDispX * opts.waveIntensity * 5 + cursorDispX;
        const posY = baseY + nDispY * opts.waveIntensity * 5 + cursorDispY;

        // ── Distance from center for global falloff ──
        const nx = (baseX - cx) / (w * 0.5);
        const ny = (baseY - cy) / (h * 0.5);
        const distCenter = Math.sqrt(nx*nx + ny*ny);
        const globalFalloff = Math.max(0, 1 - distCenter * opts.falloff * 0.55);

        // ── Per-cell brightness from noise ──
        const nBright = Simplex2D.noise(baseX * noiseScale * 2 + tMed, baseY * noiseScale * 2 + 50);
        // Combine: base falloff + hotspot boost + noise variation
        let cellBrightness = globalFalloff * (0.45 + hotspotInfluence * 0.55) * (0.7 + nBright * 0.3);
        // Cursor proximity adds glow
        cellBrightness += cursorInfluence * 0.35;
        cellBrightness = clamp(cellBrightness, 0, 1.2);

        if (cellBrightness < 0.02) continue;

        // ── Morphing exponent per cell (noise-driven, subtle) ──
        const nMorph = Simplex2D.noise(baseX * noiseScale * 1.5 + tFast, baseY * noiseScale * 1.5);
        const morphN = 3.0 + nMorph * 1.5; // oscillates ~2.25–3.75

        // ── Per-cell radius modulation ──
        const nPulse = Simplex2D.noise(baseX * noiseScale + tMed + 200, baseY * noiseScale + tMed);
        const cellR = baseR * (0.75 + nPulse * 0.25 + cursorInfluence * 0.15);

        // ── Slight aspect ratio shift (not perfect circles) ──
        const nAspect = Simplex2D.noise(baseX * noiseScale + 300, baseY * noiseScale + tSlow);
        const rxMod = 1 + nAspect * 0.12;
        const ryMod = 1 - nAspect * 0.12;

        // ── Draw each color layer ──
        for (let layer = 0; layer < opts.colorLayers; layer++) {
          const layerShift = layer * (Math.PI * 2 / opts.colorLayers);
          const layerBright = opts.brightness * (1 - layer * 0.18);

          // Per-channel color variation from noise
          const nCol = Simplex2D.noise(baseX * noiseScale * 3 + tMed + layerShift, baseY * noiseScale * 3);
          const cr = Math.min(255, rgb[0] * layerBright * (0.85 + nCol * 0.15));
          const cg = Math.min(255, rgb[1] * layerBright * (0.85 + nCol * 0.15));
          const cb = Math.min(255, rgb[2] * layerBright * (0.9 + nCol * 0.1));

          // Alpha from brightness + layer
          const layerAlpha = cellBrightness * (0.35 - layer * 0.06);
          if (layerAlpha < 0.008) continue;

          const lr = cellR * (1 + 0.06 * Math.sin(time * 0.4 + layerShift));

          ctx.save();
          ctx.globalAlpha = layerAlpha;

          // ── Fill ──
          superellipseXY(posX, posY, lr * rxMod, lr * ryMod, morphN, steps);
          const fillA = 0.12 + cellBrightness * 0.08;
          ctx.fillStyle = `rgba(${Math.round(cr)},${Math.round(cg)},${Math.round(cb)},${fillA})`;
          ctx.fill();

          // ── Stroke (glowing outline) ──
          superellipseXY(posX, posY, lr * rxMod, lr * ryMod, morphN, steps);
          ctx.strokeStyle = `rgba(${Math.round(cr)},${Math.round(cg)},${Math.round(cb)},0.75)`;
          ctx.lineWidth = opts.lineThickness * cellR * 1.8;
          const glowSize = 4 + cellBrightness * 10;
          ctx.shadowColor = `rgb(${Math.round(cr)},${Math.round(cg)},${Math.round(cb)})`;
          ctx.shadowBlur = glowSize;
          ctx.stroke();

          ctx.restore();
        }
      }
    }

    // ── Vignette overlay (darker edges, keeps center readable) ──
    const vGrad = ctx.createRadialGradient(cx, cy, Math.min(w,h) * 0.25, cx, cy, Math.max(w,h) * 0.7);
    vGrad.addColorStop(0, 'rgba(15,17,23,0)');
    vGrad.addColorStop(1, 'rgba(15,17,23,0.55)');
    ctx.fillStyle = vGrad;
    ctx.fillRect(0, 0, w, h);

    animId = requestAnimationFrame(render);
  }

  /* ══════════════════════════════════════════════════════════════════════
     Public API
     ══════════════════════════════════════════════════════════════════════ */
  function init(canvasId, options = {}) {
    canvas = document.getElementById(canvasId);
    if (!canvas) { console.warn(`[SquircleShift] Canvas "${canvasId}" not found.`); return; }
    ctx = canvas.getContext('2d');
    opts = { ...defaults, ...options };
    dpr = window.devicePixelRatio || 1;

    function resize() {
      const parent = canvas.parentElement || document.body;
      const rect = parent.getBoundingClientRect();
      w = rect.width;
      h = rect.height;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = w + 'px';
      canvas.style.height = h + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      initHotspots();
    }

    // ── Pointer tracking ──
    function onPointerMove(e) {
      const rect = canvas.getBoundingClientRect();
      cursorRawX = e.clientX - rect.left;
      cursorRawY = e.clientY - rect.top;
      if (!cursorActive) {
        cursorActive = true;
        cursorX = cursorRawX;
        cursorY = cursorRawY;
      }
      lastPointerTime = performance.now();
    }
    function onPointerLeave() {
      cursorActive = false;
    }
    function onTouchMove(e) {
      if (e.touches.length > 0) {
        const rect = canvas.getBoundingClientRect();
        cursorRawX = e.touches[0].clientX - rect.left;
        cursorRawY = e.touches[0].clientY - rect.top;
        if (!cursorActive) { cursorActive = true; cursorX = cursorRawX; cursorY = cursorRawY; }
        lastPointerTime = performance.now();
      }
    }

    _resizeHandler = resize;
    _mouseHandler = onPointerMove;
    _leaveHandler = onPointerLeave;
    _touchHandler = onTouchMove;

    resize();
    window.addEventListener('resize', _resizeHandler);
    canvas.addEventListener('mousemove', _mouseHandler, { passive: true });
    canvas.addEventListener('mouseleave', _leaveHandler);
    canvas.addEventListener('touchmove', _touchHandler, { passive: true });
    canvas.addEventListener('touchend', _leaveHandler);

    if (animId) cancelAnimationFrame(animId);
    animId = requestAnimationFrame(render);
  }

  function destroy() {
    if (animId) cancelAnimationFrame(animId);
    animId = null;
    window.removeEventListener('resize', _resizeHandler);
    if (canvas) {
      canvas.removeEventListener('mousemove', _mouseHandler);
      canvas.removeEventListener('mouseleave', _leaveHandler);
      canvas.removeEventListener('touchmove', _touchHandler);
      canvas.removeEventListener('touchend', _leaveHandler);
    }
  }

  function setOptions(o) { Object.assign(opts, o); }

  return { init, destroy, setOptions };
})();
