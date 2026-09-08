/**
 * SquircleShift — Pure Canvas recreation of React Bits Pro's Squircle Shift.
 *
 * Draws a morphing superellipse (squircle) with grid overlay, wave displacement,
 * spiral rotation, and color tinting. No dependencies.
 *
 * Usage:
 *   <canvas id="squircle-canvas"></canvas>
 *   <script src="squircle-shift.js"></script>
 *   <script>
 *     SquircleShift.init('squircle-canvas', { colorTint: '#6c8cff' });
 *   </script>
 */
const SquircleShift = (() => {
  'use strict';

  const defaults = {
    speed: 0.3,
    colorLayers: 3,
    gridFrequency: 25,
    gridIntensity: 1.0,
    lineThickness: 0.06,
    phaseOffset: 10,
    waveSpeed: 0.2,
    waveIntensity: 0.1,
    spiralIntensity: 1.0,
    centerX: 1.0,
    centerY: 1.0,
    falloff: 1.0,
    colorTint: '#c084fc',
    darkBackground: '#000000',
    brightness: 1.5,
  };

  let canvas, ctx, animId, _resizeHandler;
  let time = 0;
  let opts = { ...defaults };
  let dpr = 1;
  let w = 0, h = 0;

  /* ── Helpers ────────────────────────────────────────────────────────── */

  function hexToRgb(hex) {
    hex = hex.replace('#', '');
    if (hex.length === 3) hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
    const n = parseInt(hex, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function superellipse(t, n) {
    const ct = Math.cos(t), st = Math.sin(t);
    const ct2 = Math.abs(ct), st2 = Math.abs(st);
    const r = Math.pow(Math.pow(ct2, n) + Math.pow(st2, n), -1 / n);
    return [ct * r, st * r];
  }

  function lerp(a, b, t) { return a + (b - a) * t; }

  /* ── Core renderer ──────────────────────────────────────────────────── */

  function render(timestamp) {
    if (!canvas || !ctx) return;

    const dt = opts.speed * 0.016;
    time += dt;

    const cx = w / 2;
    const cy = h / 2;
    const baseR = Math.min(w, h) * 0.32;

    // Morphing exponent: oscillates between ~2 (circle) and ~5 (squircle)
    const morphN = 2.0 + 3.0 * (0.5 + 0.5 * Math.sin(time * 0.4));

    // Clear
    ctx.fillStyle = opts.darkBackground;
    ctx.fillRect(0, 0, w, h);

    const rgb = hexToRgb(opts.colorTint);
    const steps = 200;

    // ── Grid pattern overlay ──
    const gridFreq = opts.gridFrequency;
    const gridSpacing = w / gridFreq;

    ctx.save();
    ctx.globalAlpha = opts.gridIntensity * 0.08;
    ctx.strokeStyle = `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
    ctx.lineWidth = 0.5;

    // Vertical grid lines
    for (let gx = 0; gx < w; gx += gridSpacing) {
      const waveOff = Math.sin(gx * 0.01 + time * opts.waveSpeed * 2) * opts.waveIntensity * 30;
      ctx.beginPath();
      ctx.moveTo(gx + waveOff, 0);
      ctx.lineTo(gx - waveOff, h);
      ctx.stroke();
    }
    // Horizontal grid lines
    for (let gy = 0; gy < h; gy += gridSpacing) {
      const waveOff = Math.cos(gy * 0.01 + time * opts.waveSpeed * 2) * opts.waveIntensity * 30;
      ctx.beginPath();
      ctx.moveTo(0, gy + waveOff);
      ctx.lineTo(w, gy - waveOff);
      ctx.stroke();
    }
    ctx.restore();

    // ── Color layers (channel iterations) ──
    const layerCount = Math.max(1, Math.min(3, opts.colorLayers));

    for (let layer = 0; layer < layerCount; layer++) {
      const layerPhase = layer * (Math.PI * 2 / layerCount) + time * opts.phaseOffset * 0.05;
      const layerR = baseR * (1 + 0.15 * Math.sin(time * 0.3 + layerPhase));
      const layerBright = opts.brightness * (1 - layer * 0.2);

      // Channel color shift per layer
      const cr = Math.min(255, rgb[0] * layerBright);
      const cg = Math.min(255, rgb[1] * layerBright);
      const cb = Math.min(255, rgb[2] * layerBright);

      ctx.save();
      ctx.globalAlpha = 0.6 - layer * 0.12;

      // ── Draw squircle shape ──
      ctx.beginPath();
      for (let i = 0; i <= steps; i++) {
        const t = (i / steps) * Math.PI * 2;

        // Superellipse coordinates
        let [sx, sy] = superellipse(t, morphN);

        // Spiral rotation
        const spiralAngle = time * 0.15 * opts.spiralIntensity;
        const spiralR = 1 + 0.05 * Math.sin(time * 0.5 + t * 3);
        const cosS = Math.cos(spiralAngle + t * 0.3);
        const sinS = Math.sin(spiralAngle + t * 0.3);
        const rx = (sx * spiralR) * cosS - (sy * spiralR) * sinS;
        const ry = (sx * spiralR) * sinS + (sy * spiralR) * cosS;

        // Wave displacement
        const waveDisp = Math.sin(t * gridFreq * 0.1 + time * opts.waveSpeed) * opts.waveIntensity * baseR * 0.15;

        // Final position
        const px = cx + (rx * layerR + waveDisp * Math.cos(t)) * opts.centerX;
        const py = cy + (ry * layerR + waveDisp * Math.sin(t)) * opts.centerY;

        // Distance falloff
        const dist = Math.sqrt((px - cx) ** 2 + (py - cy) ** 2) / baseR;
        const falloff = Math.max(0, 1 - dist * opts.falloff * 0.5);

        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();

      // Fill with gradient
      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, layerR * 1.2);
      grad.addColorStop(0, `rgba(${cr},${cg},${cb},0.25)`);
      grad.addColorStop(0.6, `rgba(${cr},${cg},${cb},0.08)`);
      grad.addColorStop(1, `rgba(${cr},${cg},${cb},0)`);
      ctx.fillStyle = grad;
      ctx.fill();

      // Stroke
      ctx.strokeStyle = `rgba(${cr},${cg},${cb},${0.5 - layer * 0.1})`;
      ctx.lineWidth = opts.lineThickness * baseR;
      ctx.stroke();

      ctx.restore();
    }

    // ── Inner glow ring ──
    ctx.save();
    ctx.globalAlpha = 0.3 + 0.15 * Math.sin(time * 0.6);
    const glowR = baseR * (0.85 + 0.1 * Math.sin(time * 0.4));
    ctx.beginPath();
    for (let i = 0; i <= steps; i++) {
      const t = (i / steps) * Math.PI * 2;
      let [sx, sy] = superellipse(t, morphN);
      const px = cx + sx * glowR * opts.centerX;
      const py = cy + sy * glowR * opts.centerY;
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.closePath();
    ctx.strokeStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.35)`;
    ctx.lineWidth = 1.5;
    ctx.shadowColor = `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
    ctx.shadowBlur = 20;
    ctx.stroke();
    ctx.restore();

    // ── Dotted grid points inside squircle ──
    ctx.save();
    const dotSpacing = gridSpacing * 0.8;
    for (let dx = 0; dx < w; dx += dotSpacing) {
      for (let dy = 0; dy < h; dy += dotSpacing) {
        // Check if point is inside the squircle
        const nx = (dx - cx) / baseR;
        const ny = (dy - cy) / baseR;
        const val = Math.pow(Math.abs(nx), morphN) + Math.pow(Math.abs(ny), morphN);
        if (val > 1.05) continue;

        const dist = Math.sqrt(nx * nx + ny * ny);
        const alpha = Math.max(0, (1 - dist) * 0.3 * opts.gridIntensity);
        const pulse = 0.5 + 0.5 * Math.sin(dx * 0.02 + dy * 0.02 + time * 2);

        ctx.beginPath();
        ctx.arc(dx, dy, 1.2 + pulse * 0.8, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha * pulse})`;
        ctx.fill();
      }
    }
    ctx.restore();

    // ── Floating particles ──
    ctx.save();
    for (let i = 0; i < 30; i++) {
      const seed = i * 137.5;
      const angle = seed + time * (0.1 + (i % 5) * 0.05);
      const dist = baseR * (0.4 + 0.6 * ((Math.sin(seed) + 1) / 2));
      const px = cx + Math.cos(angle) * dist;
      const py = cy + Math.sin(angle) * dist;
      const size = 1 + Math.sin(time * 2 + seed) * 0.8;
      const alpha = 0.15 + 0.15 * Math.sin(time + seed);

      ctx.beginPath();
      ctx.arc(px, py, size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha})`;
      ctx.fill();
    }
    ctx.restore();

    animId = requestAnimationFrame(render);
  }

  /* ── Public API ─────────────────────────────────────────────────────── */

  function init(canvasId, options = {}) {
    canvas = document.getElementById(canvasId);
    if (!canvas) {
      console.warn(`[SquircleShift] Canvas "${canvasId}" not found.`);
      return;
    }
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
      ctx.scale(dpr, dpr);
    }

    _resizeHandler = resize;
    resize();
    window.addEventListener('resize', _resizeHandler);

    if (animId) cancelAnimationFrame(animId);
    animId = requestAnimationFrame(render);
  }

  function destroy() {
    if (animId) cancelAnimationFrame(animId);
    animId = null;
    window.removeEventListener('resize', _resizeHandler);
  }

  function setOptions(newOpts) {
    Object.assign(opts, newOpts);
  }

  return { init, destroy, setOptions };
})();
