/**
 * SquircleShift — Pure Canvas recreation of React Bits Pro's Squircle Shift.
 *
 * Renders a grid of small superellipse (squircle) shapes with wave displacement,
 * spiral rotation, color channel layering, and glow effects. No dependencies.
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
    if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
    const n = parseInt(hex, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  /**
   * Draws a single superellipse path (squircle) at (cx, cy) with radius r.
   * n controls the shape: n=2 circle, n=4 squircle, n→∞ square.
   */
  function drawSuperellipse(cx, cy, r, n, steps) {
    ctx.beginPath();
    for (let i = 0; i <= steps; i++) {
      const t = (i / steps) * Math.PI * 2;
      const ct = Math.cos(t), st = Math.sin(t);
      const act = Math.abs(ct), ast = Math.abs(st);
      const rr = Math.pow(Math.pow(act, n) + Math.pow(ast, n), -1 / n);
      const x = cx + ct * rr * r;
      const y = cy + st * rr * r;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
  }

  /* ── Core renderer ──────────────────────────────────────────────────── */

  function render() {
    if (!canvas || !ctx) return;

    const dt = opts.speed * 0.016;
    time += dt;

    // Clear
    ctx.fillStyle = opts.darkBackground;
    ctx.fillRect(0, 0, w, h);

    const rgb = hexToRgb(opts.colorTint);
    const cx = w / 2;
    const cy = h / 2;

    // Grid layout
    const cols = Math.max(4, Math.round(opts.gridFrequency));
    const rows = Math.max(3, Math.round(cols * (h / w)));
    const cellW = w / cols;
    const cellH = h / rows;
    const shapeR = Math.min(cellW, cellH) * 0.32; // radius of each small squircle

    // Morphing exponent: oscillates between ~2 (circle) and ~5 (squircle)
    const morphN = 2.0 + 3.0 * (0.5 + 0.5 * Math.sin(time * 0.4));

    // Spiral rotation angle (for wave patterns)
    const spiralAngle = time * 0.15 * opts.spiralIntensity;

    const steps = 40; // points per superellipse

    // For each cell in the grid
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const baseX = (col + 0.5) * cellW;
        const baseY = (row + 0.5) * cellH;

        // Normalized position from center (-1 to 1)
        const nx = (baseX - cx) / (w * 0.5);
        const ny = (baseY - cy) / (h * 0.5);

        // Distance from center (0 at center, 1 at edges)
        const distFromCenter = Math.sqrt(nx * nx + ny * ny);

        // Distance falloff — dim shapes farther from center
        const falloff = Math.max(0, 1 - distFromCenter * opts.falloff * 0.7);

        // Wave displacement — organic flowing movement
        const waveX = Math.sin(
          ny * opts.gridFrequency * 0.3 + time * opts.waveSpeed * 2 + spiralAngle
        ) * opts.waveIntensity * cellW * 1.5 * opts.centerX;

        const waveY = Math.cos(
          nx * opts.gridFrequency * 0.3 + time * opts.waveSpeed * 2 + spiralAngle * 0.7
        ) * opts.waveIntensity * cellH * 1.5 * opts.centerY;

        const posX = baseX + waveX;
        const posY = baseY + waveY;

        // Per-cell color channel intensity
        // Each cell gets slightly different R/G/B based on position + time
        const channelPhase = time * opts.phaseOffset * 0.05;

        // Per-cell radius modulation (breathing)
        const breathe = 1 + 0.12 * Math.sin(time * 0.6 + col * 0.4 + row * 0.3);
        const cellR = shapeR * breathe;

        // Draw each color layer
        for (let layer = 0; layer < opts.colorLayers; layer++) {
          const layerShift = layer * (Math.PI * 2 / opts.colorLayers);
          const layerBright = opts.brightness * (1 - layer * 0.15);

          // Per-channel color with phase offset per layer
          const cr = Math.min(255, rgb[0] * layerBright * (0.8 + 0.2 * Math.sin(channelPhase + layerShift + nx * 2)));
          const cg = Math.min(255, rgb[1] * layerBright * (0.8 + 0.2 * Math.cos(channelPhase + layerShift + ny * 2)));
          const cb = Math.min(255, rgb[2] * layerBright);

          // Alpha based on falloff + wave animation
          const waveAlpha = 0.3 + 0.3 * Math.sin(time * 0.8 + col * 0.5 + row * 0.4 + layer);
          const alpha = falloff * waveAlpha * opts.gridIntensity;

          if (alpha < 0.01) continue;

          // Slight per-layer size variation
          const lr = cellR * (1 + 0.08 * Math.sin(time * 0.3 + layerShift));

          ctx.save();
          ctx.globalAlpha = alpha;

          // Fill shape
          drawSuperellipse(posX, posY, lr, morphN, steps);
          const fillAlpha = 0.15 + 0.1 * Math.sin(time + col + row);
          ctx.fillStyle = `rgba(${Math.round(cr)},${Math.round(cg)},${Math.round(cb)},${fillAlpha})`;
          ctx.fill();

          // Stroke shape (the glowing outline — key visual)
          drawSuperellipse(posX, posY, lr, morphN, steps);
          ctx.strokeStyle = `rgba(${Math.round(cr)},${Math.round(cg)},${Math.round(cb)},0.8)`;
          ctx.lineWidth = opts.lineThickness * cellR * 2;
          ctx.shadowColor = `rgb(${Math.round(cr)},${Math.round(cg)},${Math.round(cb)})`;
          ctx.shadowBlur = 8 + 6 * Math.sin(time + col * 0.3 + row * 0.2);
          ctx.stroke();

          ctx.restore();
        }
      }
    }

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
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
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
