/* OMEGA front-end: theme, slideshow, reveals, demo, uploads, polling. No dependencies except Lucide icons. */
(function () {
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* theme */
  const root = document.documentElement;
  $$('[data-theme-toggle]').forEach(b => b.addEventListener('click', () => {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next; try { localStorage.setItem('omega-theme', next); } catch (e) {}
  }));

  /* nav */
  const nav = $('.nav'); const burger = $('[data-burger]');
  if (burger) burger.addEventListener('click', () => nav.classList.toggle('open'));

  /* flashes */
  $$('.flash:not(.dev)').forEach(f => setTimeout(() => { f.style.transition = '.6s'; f.style.opacity = 0; f.style.transform = 'translateY(-10px)'; setTimeout(() => f.remove(), 700); }, 6500));

  /* reveal on scroll */
  const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), { threshold: .12 });
  $$('.reveal').forEach(el => io.observe(el));

  /* count up */
  const co = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return; co.unobserve(e.target);
    const el = e.target, to = parseFloat(el.dataset.count), suf = el.dataset.suffix || '', t0 = performance.now(), dur = 1400;
    (function tick(t) { const p = Math.min(1, (t - t0) / dur), v = to * (1 - Math.pow(1 - p, 3)); el.textContent = Math.round(v) + suf; if (p < 1) requestAnimationFrame(tick); })(t0);
  }), { threshold: .6 });
  $$('[data-count]').forEach(el => co.observe(el));

  /* HERO SLIDESHOW */
  const hero = $('[data-hero]');
  if (hero) {
    const slides = $$('.slide', hero), bar = $$('.progress button', hero), DUR = 6500;
    let i = 0, timer = null, startedAt = 0, remaining = DUR;
    hero.style.setProperty('--dur', DUR + 'ms');
    function show(n) {
      i = (n + slides.length) % slides.length;
      slides.forEach((s, k) => s.classList.toggle('is-active', k === i));
      bar.forEach((b, k) => { b.classList.remove('on', 'done'); if (k < i) b.classList.add('done'); });
      void hero.offsetWidth; bar[i].classList.add('on');   // restart CSS progress animation
      remaining = DUR; schedule();
    }
    function schedule() { clearTimeout(timer); if (reduce || hero.classList.contains('paused')) return; startedAt = performance.now(); timer = setTimeout(() => show(i + 1), remaining); }
    function pause() { if (hero.classList.contains('paused')) return; hero.classList.add('paused'); clearTimeout(timer); remaining = Math.max(400, remaining - (performance.now() - startedAt)); }
    function resume() { hero.classList.remove('paused'); schedule(); }
    bar.forEach((b, k) => b.addEventListener('click', () => show(k)));
    $('[data-prev]', hero)?.addEventListener('click', () => show(i - 1));
    $('[data-next]', hero)?.addEventListener('click', () => show(i + 1));
    hero.addEventListener('mouseenter', pause); hero.addEventListener('mouseleave', resume);
    hero.addEventListener('focusin', pause); hero.addEventListener('focusout', resume);
    document.addEventListener('keydown', e => { if (e.target.closest('input,textarea,select')) return; if (e.key === 'ArrowRight') show(i + 1); if (e.key === 'ArrowLeft') show(i - 1); });
    let x0 = null; hero.addEventListener('touchstart', e => x0 = e.touches[0].clientX, { passive: true });
    hero.addEventListener('touchend', e => { if (x0 === null) return; const dx = e.changedTouches[0].clientX - x0; if (Math.abs(dx) > 50) show(i + (dx < 0 ? 1 : -1)); x0 = null; });
    document.addEventListener('visibilitychange', () => document.hidden ? pause() : resume());
    // parallax on the brand art
    if (matchMedia('(hover:hover) and (pointer:fine)').matches) hero.addEventListener('pointermove', e => { const a = $('.brand-art', hero); if (!a || reduce) return; const r = hero.getBoundingClientRect(); a.style.transform = `translateY(-52%) translate(${(e.clientX / r.width - .5) * -18}px,${(e.clientY / r.height - .5) * -14}px)`; });
    show(0);
  }

  /* animated protected-sale demo */
  const demo = $('[data-demo]');
  if (demo) {
    const steps = $$('.demo-step', demo), tags = $$('.tag', demo), stage = $('.demo-stage', demo);
    const tagAt = [0, 1, 2, 3, 3, 4];  // tags revealed up to this count at each step
    let k = -1;
    function go() {
      k = (k + 1) % (steps.length + 1);
      steps.forEach((s, n) => { s.classList.toggle('on', n === k); s.classList.toggle('done', n < k); });
      stage.classList.toggle('scanning', k === 3 || k === 4);
      const show = k >= steps.length ? 4 : tagAt[k]; tags.forEach((t, n) => t.classList.toggle('show', n < show));
    }
    let iv = 0, seen = false;
    const run = on => { clearInterval(iv); iv = 0; if (on && !document.hidden) { if (!seen) { seen = true; go(); } iv = setInterval(go, 2600); } };
    const dobs = new IntersectionObserver(es => es.forEach(e => run(e.isIntersecting)), { threshold: .3 });
    dobs.observe(demo);
    document.addEventListener('visibilitychange', () => run(!document.hidden && demo.getBoundingClientRect().top < innerHeight && demo.getBoundingClientRect().bottom > 0));
  }

  /* uploads: drag/drop + filename + video preview */
  $$('.upload').forEach(box => {
    const inp = $('input[type=file]', box), label = $('.pick', box);
    ['dragenter', 'dragover'].forEach(ev => box.addEventListener(ev, e => { e.preventDefault(); box.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => box.addEventListener(ev, e => { e.preventDefault(); box.classList.remove('drag'); }));
    box.addEventListener('drop', e => { inp.files = e.dataTransfer.files; inp.dispatchEvent(new Event('change')); });
    inp.addEventListener('change', () => {
      const f = [...inp.files]; if (!f.length) return;
      label.textContent = f.length === 1 ? `${f[0].name} · ${(f[0].size / 1048576).toFixed(1)} MB` : `${f.length} files selected`;
      const prev = box.nextElementSibling; if (prev && prev.classList.contains('preview') && f[0].type.startsWith('video')) { prev.src = URL.createObjectURL(f[0]); prev.hidden = false; }
    });
  });
  function busy(f) { const b = $('button[type=submit]', f); if (b && !b.disabled) { b.dataset.t = b.innerHTML; b.disabled = true; b.innerHTML = '<span class="spinner"></span> Working…'; } }
  $$('form[data-busy]').forEach(f => f.addEventListener('submit', e => { setTimeout(() => { if (!e.defaultPrevented) busy(f); }, 0); }));
  addEventListener('pageshow', e => { if (e.persisted) $$('form[data-busy] button[type=submit]').forEach(b => { if (b.dataset.t) { b.innerHTML = b.dataset.t; b.disabled = false; } }); });

  /* poll while AI analysis runs */
  const poll = $('[data-poll]');
  if (poll) {
    const url = poll.dataset.poll, st = poll.dataset.status;
    const t = setInterval(async () => {
      try { const r = await (await fetch(url)).json(); if (!r.pending || r.status !== st) { clearInterval(t); location.reload(); } } catch (e) {}
    }, 3000);
  }

  /* listing form: live price hint */
  const gal = $$('[data-thumb]');
  gal.forEach(b => b.addEventListener('click', () => { const m = $('#mainimg'); if (m) m.src = b.dataset.thumb; }));

  /* app shell: mobile drawer, search toggle, account menu, theme buttons */
  const side = $('#side'), scrim = $('[data-scrim]'), menuBtn = $('[data-menu]');
  function drawer(open) {
    if (!side) return;
    side.classList.toggle('open', open); scrim && scrim.classList.toggle('on', open);
    menuBtn && menuBtn.setAttribute('aria-expanded', String(open)); document.body.style.overflow = open ? 'hidden' : '';
  }
  menuBtn && menuBtn.addEventListener('click', () => drawer(!side.classList.contains('open')));
  scrim && scrim.addEventListener('click', () => drawer(false));
  $$('.side a').forEach(a => a.addEventListener('click', () => drawer(false)));
  const ts = $('[data-search-toggle]'), sf = $('#topsearch');
  ts && sf && ts.addEventListener('click', () => { sf.classList.toggle('open'); if (sf.classList.contains('open')) $('input', sf).focus(); });
  document.addEventListener('click', e => { $$('[data-dd][open]').forEach(d => { if (!d.contains(e.target)) d.removeAttribute('open'); }); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { drawer(false); $$('[data-dd][open]').forEach(d => d.removeAttribute('open')); } });
  const syncTheme = () => $$('[data-set-theme]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.setTheme === root.dataset.theme)));
  $$('[data-set-theme]').forEach(b => b.addEventListener('click', () => { root.dataset.theme = b.dataset.setTheme; try { localStorage.setItem('omega-theme', b.dataset.setTheme); } catch (e) {} syncTheme(); }));
  $$('[data-theme-toggle]').forEach(b => b.addEventListener('click', syncTheme));
  syncTheme();

  /* ---- V3: liveliness ---- */
  // per-card spotlight (mouse/trackpad only; touch devices skip it entirely)
  if (matchMedia('(hover:hover) and (pointer:fine)').matches && !reduce) {
    let raf = 0, ev = null;
    document.addEventListener('pointermove', e => {
      ev = e; if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0; const g = ev.target.closest && ev.target.closest('.glass');
        if (g) { const r = g.getBoundingClientRect(); g.style.setProperty('--mx', (ev.clientX - r.left) + 'px'); g.style.setProperty('--my', (ev.clientY - r.top) + 'px'); }
      });
    }, { passive: true });
  }
  // smooth theme transition
  $$('[data-theme-toggle],[data-set-theme]').forEach(b => b.addEventListener('click', () => { root.classList.add('theme-anim'); setTimeout(() => root.classList.remove('theme-anim'), 600); }));
  // greeting by the visitor's own local time
  $$('[data-greet]').forEach(el => { const h = new Date().getHours(); el.textContent = h < 5 ? 'Working late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'; });
  // count-up for dashboard numbers (keeps $ and thousands separators and decimals)
  function countUp(el) {
    const raw = el.textContent.trim(), m = raw.match(/^(\D*?)([\d,]+(?:\.\d+)?)(\D*)$/); if (!m || reduce) return;
    const to = parseFloat(m[2].replace(/,/g, '')), dec = (m[2].split('.')[1] || '').length, t0 = performance.now(), dur = 1100;
    if (!isFinite(to) || to === 0) return;
    (function tick(t) { const p = Math.min(1, (t - t0) / dur), v = to * (1 - Math.pow(1 - p, 3));
      el.textContent = m[1] + v.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }) + m[3]; if (p < 1) requestAnimationFrame(tick); else el.textContent = raw; })(t0);
  }
  $$('.k-num, [data-count-up], .g-val[data-pct]').forEach(countUp);
  // liquid gauges fill, progress bars grow
  requestAnimationFrame(() => setTimeout(() => { document.body.classList.add('ready'); $$('[data-gauge]').forEach(g => g.classList.add('go')); }, 250));
  // sign up / log in
  $$('[data-eye]').forEach(b => b.addEventListener('click', () => {
    const i = b.parentElement.querySelector('input'), show = i.type === 'password';
    i.type = show ? 'text' : 'password'; b.setAttribute('aria-label', show ? 'Hide password' : 'Show password'); b.setAttribute('aria-pressed', String(show));
    const u = b.querySelector('use'); if (u) u.setAttribute('href', u.getAttribute('href').replace(/#.*$/, show ? '#eye-off' : '#eye'));
  }));
  const si = $('[data-strength-input]'), sb = $('[data-strength]'), sl = $('[data-strength-label]');
  si && si.addEventListener('input', () => {
    const v = si.value; let n = 0; if (v.length >= 8) n++; if (/[A-Z]/.test(v) && /[a-z]/.test(v)) n++; if (/\d/.test(v)) n++; if (/[^A-Za-z0-9]/.test(v) || v.length >= 14) n++;
    if (v.length < 8) n = Math.min(n, 1); if (!v) n = 0; sb.dataset.s = n; sl.textContent = v ? ['', 'Too weak', 'Okay', 'Good', 'Strong'][n] || '' : '';
  });
  $$('[data-role]').forEach(r => r.addEventListener('change', () => { const f = r.closest('[data-role-form]'); if (f) { const seller = $('[data-role][value=seller]', f).checked; f.classList.toggle('show-seller', seller); history.replaceState(null, '', seller ? '?role=seller' : location.pathname); } }));
  // caps-lock hint
  $$('input[type=password]').forEach(i => { const hint = i.closest('.fl')?.nextElementSibling; ['keydown', 'keyup'].forEach(ev => i.addEventListener(ev, e => { if (hint && hint.hasAttribute('data-caps') && e.getModifierState) hint.hidden = !e.getModifierState('CapsLock'); })); i.addEventListener('blur', () => { if (hint && hint.hasAttribute('data-caps')) hint.hidden = true; }); });
  // inline validation (the server still validates everything; this just saves a round trip)
  const rules = {
    email: v => !v ? 'Enter your email address.' : !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v) ? 'That email address doesn\'t look right.' : '',
    name: v => v.trim().length < 2 ? 'Enter your full name.' : '',
    password: (v, f, inp) => !v ? 'Enter your password.' : (inp.closest('[data-new-password]') && v.length < 8) ? 'Use at least 8 characters.' : '',
    paypal_email: v => v && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v) ? 'Enter a valid PayPal email, or leave it blank for now.' : ''
  };
  function check(inp, form) {
    const rule = rules[inp.name]; if (!rule) return true;
    const wrap = inp.closest('.fl'), msg = wrap && wrap.parentElement.querySelector('.fe[data-for="' + inp.name + '"]'); if (!wrap || !msg) return true;
    const err = (inp.closest('.seller-only') && !form.classList.contains('show-seller')) ? '' : rule(inp.value, form, inp); wrap.classList.toggle('bad', !!err); inp.setAttribute('aria-invalid', err ? 'true' : 'false'); msg.textContent = err; msg.hidden = !err;
    return !err;
  }
  $$('form[data-validate]').forEach(f => {
    $$('input', f).forEach(i => { i.addEventListener('blur', () => { if (i.value || i.dataset.touched) check(i, f); i.dataset.touched = 1; }); i.addEventListener('input', () => { if (i.getAttribute('aria-invalid') === 'true') check(i, f); }); });
    f.addEventListener('submit', e => { const bad = $$('input', f).filter(i => !check(i, f)); if (bad.length) { e.preventDefault(); bad[0].focus(); } });
  });
})();
