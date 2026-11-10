/* Quiet generative background: a field of slow-drifting dots with faint
   connecting lines. Fixed full-viewport canvas behind all content.
   Honours prefers-reduced-motion by drawing a single static frame. */
(function () {
  var cv = document.getElementById('bg-canvas');
  if (!cv) return;
  var ctx = cv.getContext('2d');
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var W, H, dpr, pts, raf;

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = cv.clientWidth;
    H = cv.clientHeight;
    cv.width = W * dpr;
    cv.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function seed() {
    // Density scales with viewport area, capped at ~46 dots.
    var n = Math.round(Math.min(46, (W * H) / 26000));
    pts = [];
    for (var i = 0; i < n; i++) {
      pts.push({
        x: Math.random() * W,
        y: Math.random() * H,
        vx: (Math.random() - 0.5) * 0.18,
        vy: (Math.random() - 0.5) * 0.18,
        r: 0.8 + Math.random() * 1.4
      });
    }
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    var i, j, p;
    for (i = 0; i < pts.length; i++) {
      p = pts[i];
      if (!reduce) { p.x += p.vx; p.y += p.vy; }
      if (p.x < 0) p.x += W; else if (p.x > W) p.x -= W;
      if (p.y < 0) p.y += H; else if (p.y > H) p.y -= H;
    }
    for (i = 0; i < pts.length; i++) {
      for (j = i + 1; j < pts.length; j++) {
        var a = pts[i], b = pts[j];
        var d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < 130) {
          ctx.strokeStyle = 'rgba(30,25,18,' + (0.05 * (1 - d / 130)).toFixed(3) + ')';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
    }
    for (i = 0; i < pts.length; i++) {
      p = pts[i];
      ctx.fillStyle = 'rgba(30,25,18,0.09)';
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    }
    if (!reduce) raf = requestAnimationFrame(draw);
  }

  function start() {
    if (raf) cancelAnimationFrame(raf);
    resize();
    seed();
    draw();
  }

  window.addEventListener('resize', function () {
    if (raf) cancelAnimationFrame(raf);
    resize();
    seed();
    draw();
  });

  start();
})();
