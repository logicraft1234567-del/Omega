/* OMEGA intro: the product story in ~10 seconds.
   trusting a stranger -> payment held -> seller films the item -> shipped & delivered -> buyer films the unboxing
   -> AI compares both videos -> release only when every check passes -> OMEGA -> the site.
   Pure canvas, no assets, no figures. It illustrates the process; it shows no data. ?intro=0 skips it. */
(() => {
  const root = document.getElementById('intro');
  if (!root) return;
  const html = document.documentElement;
  const q = new URLSearchParams(location.search).get('intro');
  const cv = root.querySelector('canvas'), ctx = cv.getContext('2d');
  const cap = root.querySelector('.intro-cap'), steps = [...root.querySelectorAll('.intro-steps li')];
  const brand = root.querySelector('.intro-brand'), skip = root.querySelector('.intro-skip'), bar = root.querySelector('.intro-bar i');
  let raf = 0, done = false;

  function finish(instant) {
    if (done) return; done = true; cancelAnimationFrame(raf);
    if (instant) { html.classList.remove('intro-on'); root.remove(); return; }
    root.classList.add('out');
    setTimeout(() => { html.classList.remove('intro-on'); root.remove(); }, 900);
  }
  if (q === '0' || !html.classList.contains('intro-on') || matchMedia('(prefers-reduced-motion: reduce)').matches) return finish(true);
  skip.addEventListener('click', () => finish());
  addEventListener('keydown', e => { if (e.key === 'Escape') finish(); });

  // ---- timeline (seconds). Each scene: caption + which step pill is "current" ----
  const S = [
    { t: 0.0, text: 'Buying online means trusting a stranger.', step: -1 },
    { t: 1.4, text: 'Omega holds the payment first.', step: 0 },
    { t: 2.6, text: 'The seller films the real item.', step: 1 },
    { t: 3.8, text: 'It ships, and the buyer confirms delivery.', step: 2 },
    { t: 5.0, text: 'The buyer films the unboxing.', step: 3 },
    { t: 6.2, text: 'AI compares both videos and reports what it sees.', step: 4 },
    { t: 7.6, text: 'Money is released only when every check passes.', step: 5 },
  ];
  const BRAND = 8.9, END = 10.9;

  // ---- helpers ----
  const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
  const prog = (a, b, t) => clamp((t - a) / (b - a));
  const ease = t => t * t * (3 - 2 * t);
  const easeOut = t => 1 - Math.pow(1 - t, 3);
  const lerp = (a, b, t) => a + (b - a) * t;
  const BLUE = '#3984f5', SKY = '#55adf6', ICE = '#deeaf7', OK = '#2ee6a6', AMBER = '#ffc857';

  // ---- layout: a 1000x600 stage (landscape) or 600x1000 (portrait); P(a,b) maps stage coords to screen ----
  let W, H, sc, cx, cy, portrait, dpr;
  function size() {
    dpr = Math.min(devicePixelRatio || 1, 2); W = innerWidth; H = innerHeight; portrait = H > W * 1.05;
    cv.width = W * dpr; cv.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    sc = portrait ? Math.min(W / 520, H * .5 / 900) : Math.min(W / 900, H * .72 / 560);
    cx = W / 2; cy = portrait ? H * .45 : H * .44;
  }
  addEventListener('resize', size); size();
  const P = (a, b) => portrait ? [cx + b * sc, cy + a * sc] : [cx + a * sc, cy + b * sc];
  const SELLER = -340, BUYER = 340;

  function rr(x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }

  // ---- drawing pieces ----
  function background(t) {
    const g = ctx.createRadialGradient(cx, cy, 20, cx, cy, Math.max(W, H) * .8);
    g.addColorStop(0, '#143773'); g.addColorStop(.55, '#0b2047'); g.addColorStop(1, '#090e19');
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = 'rgba(108,165,247,.07)'; ctx.lineWidth = 1; ctx.beginPath();
    const step = 48 * Math.max(.6, sc), off = (t * 8) % step;
    for (let x = -off; x < W; x += step) { ctx.moveTo(x, 0); ctx.lineTo(x, H); }
    for (let y = -off; y < H; y += step) { ctx.moveTo(0, y); ctx.lineTo(W, y); }
    ctx.stroke();
  }
  function person(a, label, alpha) {
    const [x, y] = P(a, 0), r = 46 * sc;
    ctx.save(); ctx.globalAlpha = alpha;
    ctx.fillStyle = 'rgba(49,111,211,.22)'; ctx.strokeStyle = 'rgba(127,180,248,.55)'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill(); ctx.stroke();
    ctx.fillStyle = ICE; ctx.beginPath(); ctx.arc(x, y - r * .22, r * .26, 0, 7); ctx.fill();
    ctx.beginPath(); ctx.ellipse(x, y + r * .56, r * .5, r * .36, 0, Math.PI, 0); ctx.fill();
    ctx.fillStyle = 'rgba(222,234,247,.85)'; ctx.font = `700 ${13 * Math.max(sc, .8)}px Sora, Manrope, sans-serif`; ctx.textAlign = 'center';
    const ly = portrait ? y + r + 22 * Math.max(sc, .8) : y + r + 24 * Math.max(sc, .8);
    ctx.fillText(label.toUpperCase(), x, ly); ctx.restore();
  }
  function link(t, a0, a1, mode, alpha) {  // mode 0: dashed + "?", 1: solid
    const [x0, y0] = P(a0, 0), [x1, y1] = P(a1, 0);
    ctx.save(); ctx.globalAlpha = alpha; ctx.lineWidth = 2 * Math.max(sc, .8);
    if (mode === 0) { ctx.setLineDash([8, 10]); ctx.lineDashOffset = -t * 30; ctx.strokeStyle = 'rgba(183,202,232,.5)'; }
    else { ctx.strokeStyle = 'rgba(85,173,246,.7)'; ctx.shadowColor = SKY; ctx.shadowBlur = 12; }
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke(); ctx.restore();
  }
  function questionMark(t, alpha) {
    const [x, y] = P(0, 0), s = 44 * Math.max(sc, .8), wob = Math.sin(t * 4) * .06;
    ctx.save(); ctx.globalAlpha = alpha; ctx.translate(x, y); ctx.rotate(wob);
    ctx.fillStyle = AMBER; ctx.font = `800 ${s}px Sora, sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('?', 0, 0); ctx.restore();
  }
  function phone(a, b, rot, alpha, sMul = 1) {
    const [x, y] = P(a, b), w = 50 * sc * sMul, h = 100 * sc * sMul;
    ctx.save(); ctx.translate(x, y); ctx.rotate(rot + (portrait ? 0 : 0)); ctx.globalAlpha = alpha;
    ctx.shadowColor = 'rgba(0,0,0,.5)'; ctx.shadowBlur = 24; ctx.fillStyle = '#dadee4'; rr(-w / 2, -h / 2, w, h, 10 * sc); ctx.fill(); ctx.shadowBlur = 0;
    ctx.fillStyle = '#151f31'; rr(-w / 2 + 4 * sc, -h / 2 + 4 * sc, w - 8 * sc, h - 8 * sc, 7 * sc); ctx.fill();
    ctx.fillStyle = '#a1b2ca'; ctx.beginPath(); ctx.arc(-w / 2 + 14 * sc, -h / 2 + 14 * sc, 5 * sc, 0, 7); ctx.arc(-w / 2 + 14 * sc, -h / 2 + 28 * sc, 5 * sc, 0, 7); ctx.fill();
    ctx.restore();
  }
  function recFrame(a, b, t, alpha, label) {  // viewfinder brackets + blinking REC dot, optional scan line
    const [x, y] = P(a, b), w = 96 * sc, h = 150 * sc, L = 22 * sc;
    ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = SKY; ctx.lineWidth = 3 * Math.max(sc, .8); ctx.lineCap = 'round';
    [[-1, -1], [1, -1], [1, 1], [-1, 1]].forEach(([sx, sy]) => { ctx.beginPath(); ctx.moveTo(x + sx * w / 2, y + sy * (h / 2 - L)); ctx.lineTo(x + sx * w / 2, y + sy * h / 2); ctx.lineTo(x + sx * (w / 2 - L), y + sy * h / 2); ctx.stroke(); });
    const k = (t * 1.1) % 1, sy = y - h / 2 + h * (k < .5 ? k * 2 : 2 - k * 2);
    const g = ctx.createLinearGradient(x - w / 2, 0, x + w / 2, 0); g.addColorStop(0, 'rgba(85,173,246,0)'); g.addColorStop(.5, 'rgba(127,196,248,.95)'); g.addColorStop(1, 'rgba(85,173,246,0)');
    ctx.fillStyle = g; ctx.shadowColor = SKY; ctx.shadowBlur = 16; ctx.fillRect(x - w / 2 + 6, sy - 1.5, w - 12, 3); ctx.shadowBlur = 0;
    if (Math.floor(t * 2.2) % 2 === 0) { ctx.fillStyle = '#ff5b6b'; ctx.beginPath(); ctx.arc(x - w / 2 + 12 * sc, y - h / 2 - 16 * sc, 5 * Math.max(sc, .8), 0, 7); ctx.fill(); }
    ctx.fillStyle = ICE; ctx.font = `700 ${11 * Math.max(sc, .85)}px Sora, sans-serif`; ctx.textAlign = 'left'; ctx.fillText(label, x - w / 2 + 22 * sc, y - h / 2 - 12 * sc);
    ctx.restore();
  }
  function core(t, appear, locked, openAmt, checkAmt) {
    if (appear <= 0) return;
    const [x, y] = P(0, 0), R = 86 * sc * easeOut(appear);
    ctx.save(); ctx.translate(x, y); ctx.globalAlpha = clamp(appear * 1.4);
    const gl = ctx.createRadialGradient(0, 0, R * .2, 0, 0, R * 2.2); gl.addColorStop(0, 'rgba(57,132,245,.55)'); gl.addColorStop(1, 'rgba(57,132,245,0)');
    ctx.fillStyle = gl; ctx.beginPath(); ctx.arc(0, 0, R * 2.2, 0, 7); ctx.fill();
    ctx.lineWidth = 2 * Math.max(sc, .8);
    for (let i = 0; i < 3; i++) {
      ctx.save(); ctx.rotate(t * (.5 + i * .25) * (i % 2 ? -1 : 1) + i); ctx.strokeStyle = `rgba(255,175,110,${.55 - i * .12})`; ctx.setLineDash([R * (.9 - i * .2), R * (.35 + i * .1)]);
      ctx.beginPath(); ctx.arc(0, 0, R * (1 + i * .22), 0, 7); ctx.stroke(); ctx.restore();
    }
    ctx.fillStyle = 'rgba(11,31,67,.92)'; ctx.strokeStyle = 'rgba(136,188,249,.7)'; ctx.beginPath(); ctx.arc(0, 0, R * .82, 0, 7); ctx.fill(); ctx.stroke();
    // shield
    const s = R * .5; ctx.beginPath(); ctx.moveTo(0, -s * 1.05); ctx.bezierCurveTo(s * .9, -s * .85, s * .95, -s * .45, s * .85, s * .1); ctx.bezierCurveTo(s * .7, s * .7, s * .2, s * .95, 0, s * 1.1);
    ctx.bezierCurveTo(-s * .2, s * .95, -s * .7, s * .7, -s * .85, s * .1); ctx.bezierCurveTo(-s * .95, -s * .45, -s * .9, -s * .85, 0, -s * 1.05); ctx.closePath();
    const sg = ctx.createLinearGradient(0, -s, 0, s); sg.addColorStop(0, checkAmt > 0 ? '#38d9a0' : '#55adf6'); sg.addColorStop(1, checkAmt > 0 ? '#12a878' : '#2f71e1');
    ctx.fillStyle = sg; ctx.globalAlpha *= .95; ctx.fill(); ctx.globalAlpha = clamp(appear * 1.4);
    ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 4 * Math.max(sc, .8); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    if (checkAmt > 0) { ctx.beginPath(); ctx.moveTo(-s * .38, s * .02); ctx.lineTo(-s * .08, s * .34); ctx.lineTo(s * .42, -s * .28); ctx.setLineDash([s * 2, s * 2]); ctx.lineDashOffset = -s * 2 * (1 - checkAmt) + 0; ctx.stroke(); ctx.setLineDash([]); }
    else {   // padlock
      const lw = s * .6, lh = s * .46, ly = s * .08; ctx.fillStyle = '#ffffff'; rr(-lw / 2, ly - lh / 2 + s * .1, lw, lh, 5 * sc); ctx.fill();
      const lift = openAmt * s * .22; ctx.beginPath(); ctx.arc(0, ly - lh / 2 + s * .1 - lift, lw * .3, Math.PI, 0); ctx.moveTo(lw * .3, ly - lh / 2 + s * .1 - lift); ctx.lineTo(lw * .3, ly - lh / 2 + s * .1); ctx.stroke();
    }
    ctx.restore();
  }
  function coin(a, b, alpha, spin) {
    const [x, y] = P(a, b), r = 15 * Math.max(sc, .8);
    ctx.save(); ctx.globalAlpha = alpha; ctx.translate(x, y); ctx.scale(Math.abs(Math.cos(spin)) * .85 + .15, 1);
    const g = ctx.createLinearGradient(-r, -r, r, r); g.addColorStop(0, '#90b5f9'); g.addColorStop(1, '#2a75e6');
    ctx.shadowColor = 'rgba(89,148,246,.8)'; ctx.shadowBlur = 16; ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, r, 0, 7); ctx.fill(); ctx.shadowBlur = 0;
    ctx.strokeStyle = 'rgba(8,64,142,.5)'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(0, 0, r * .66, 0, 7); ctx.stroke(); ctx.restore();
  }
  // two video frames with matching feature points, compared line by line
  const PTS = [[-.3, -.28], [.1, -.35], [.32, -.1], [-.36, .05], [0, .02], [.28, .3], [-.12, .32]];
  function compare(t, u, alpha) {
    const fw = 150 * sc, fh = 108 * sc, off = 122;
    const L = P(-off - 20, -205), R = P(off + 20, -205);
    ctx.save(); ctx.globalAlpha = alpha;
    [[L, 'SELLER VIDEO'], [R, 'BUYER VIDEO']].forEach(([[x, y], lab]) => {
      ctx.fillStyle = 'rgba(11,31,67,.85)'; ctx.strokeStyle = 'rgba(136,188,249,.6)'; ctx.lineWidth = 2; rr(x - fw / 2, y - fh / 2, fw, fh, 10 * sc); ctx.fill(); ctx.stroke();
      ctx.fillStyle = 'rgba(222,234,247,.8)'; ctx.font = `700 ${10 * Math.max(sc, .85)}px Sora, sans-serif`; ctx.textAlign = 'center'; ctx.fillText(lab, x, y - fh / 2 - 8 * Math.max(sc, .85));
      ctx.fillStyle = 'rgba(164,192,231,.18)'; rr(x - fw * .18, y - fh * .34, fw * .36, fh * .68, 6 * sc); ctx.fill();   // silhouette of the item
    });
    const n = Math.floor(u * (PTS.length + 1));
    PTS.forEach((p, i) => {
      const a = [L[0] + p[0] * fw, L[1] + p[1] * fh], b = [R[0] + p[0] * fw, R[1] + p[1] * fh], on = i < n;
      ctx.fillStyle = on ? OK : 'rgba(222,234,247,.6)';
      [a, b].forEach(q => { ctx.beginPath(); ctx.arc(q[0], q[1], 3.4 * Math.max(sc, .85), 0, 7); ctx.fill(); });
      if (on) { ctx.strokeStyle = 'rgba(46,230,166,.55)'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
    });
    ctx.restore();
  }

  // ---- main loop ----
  let t = 0, last = performance.now(), sceneIdx = -2;
  function setScene(i) {
    sceneIdx = i; cap.classList.remove('in');
    setTimeout(() => { if (done) return; cap.textContent = S[i].text; cap.classList.add('in'); }, 140);
    steps.forEach((li, k) => { li.classList.toggle('done', k < S[i].step); li.classList.toggle('cur', k === S[i].step); });
  }
  function frame(now) {
    if (done) return;
    t += Math.min((now - last) / 1000, .05); last = now;
    let i = 0; for (let k = 0; k < S.length; k++) if (t >= S[k].t) i = k;
    if (t < BRAND && i !== sceneIdx) setScene(i);
    bar.style.transform = `scaleX(${clamp(t / BRAND)})`;

    const fadeAll = 1 - prog(BRAND - .25, BRAND + .35, t);
    ctx.save(); ctx.clearRect(0, 0, W, H);
    background(t);
    // slow push-in
    ctx.translate(cx, cy); const z = 1 + .035 * clamp(t / END) + .5 * ease(prog(BRAND, END, t)) * 0; ctx.scale(z, z); ctx.translate(-cx, -cy);

    const a = fadeAll;
    // people
    person(SELLER, 'Seller', a); person(BUYER, 'Buyer', a);
    // links
    if (t < S[1].t) { link(t, SELLER + 60, BUYER - 60, 0, a); questionMark(t, a * (1 - prog(S[1].t - .3, S[1].t, t))); }
    else { const l = ease(prog(S[1].t, S[1].t + .6, t)); link(t, SELLER + 60, BUYER - 60, 1, a * l * .9); }
    // core (Omega): appears with the payment hold
    const appear = prog(S[1].t + .15, S[1].t + .8, t), release = prog(S[6].t, S[6].t + .7, t), checks = prog(S[6].t + .3, S[6].t + .9, t);
    core(t, appear * a, true, release, checks);

    // coin: buyer -> core (hold), later core -> seller (release)
    if (t >= S[1].t && t < S[1].t + .9) { const k = easeOut(prog(S[1].t, S[1].t + .8, t)); coin(lerp(BUYER - 70, 28, k), -4 * Math.sin(k * Math.PI), a, t * 9); }
    if (t >= S[6].t + .8) { const k = ease(prog(S[6].t + .8, S[6].t + 1.4, t)); coin(lerp(40, SELLER + 70, k), -8 * Math.sin(k * Math.PI), a * (1 - prog(S[6].t + 1.3, S[6].t + 1.4, t)), t * 9); }

    // seller phone: idle at seller, filmed, then travels to the buyer
    const ship = ease(prog(S[3].t, S[4].t - .1, t));
    const pa = lerp(SELLER + 18, BUYER - 18, ship);
    const idle = t < S[3].t ? Math.sin(t * 2) * 3 : -Math.sin(ship * Math.PI) * 24;
    phone(pa, -122 + idle * .4 + (ship > 0 && ship < 1 ? -idle * .6 : 0) * 0, (portrait ? 0 : 0) + (t < S[3].t ? 0 : Math.sin(ship * Math.PI) * .25), a);
    if (t >= S[2].t && t < S[3].t) recFrame(SELLER + 18, -122, t, a * prog(S[2].t, S[2].t + .3, t) * (1 - prog(S[3].t - .25, S[3].t, t)), 'REC · SELLER');
    if (t >= S[3].t && t < S[4].t) { /* parcel trail */ const [x0, y0] = P(SELLER + 18, -122), [x1, y1] = P(pa, -122); ctx.save(); ctx.globalAlpha = a * .5; ctx.setLineDash([4, 8]); ctx.strokeStyle = SKY; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke(); ctx.restore(); }
    if (t >= S[4].t && t < S[5].t) recFrame(BUYER - 18, -122, t, a * prog(S[4].t, S[4].t + .3, t) * (1 - prog(S[5].t - .25, S[5].t, t)), 'REC · BUYER');
    // comparison
    if (t >= S[5].t) compare(t, prog(S[5].t + .15, S[6].t - .1, t), a * ease(prog(S[5].t, S[5].t + .35, t)) * (1 - prog(S[6].t + .8, S[6].t + 1.3, t)));
    ctx.restore();

    if (t >= BRAND && !brand.classList.contains('show')) { brand.classList.add('show'); cap.classList.remove('in'); steps.forEach(li => { li.classList.remove('cur'); li.classList.add('done'); }); }
    if (t >= END) return finish();
    raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);
})();
