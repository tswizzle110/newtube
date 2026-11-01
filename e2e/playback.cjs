const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://127.0.0.1:18788';
let failed = 0; const T0 = Date.now();
const check = (name, ok, extra = '') => { console.log((ok ? 'PASS ' : 'FAIL ') + name + (extra ? '  ' + extra : '')); if (!ok) failed++; };
const V = (p, fn, arg) => p.evaluate(fn, arg);
const state = (p) => V(p, () => { const v = document.querySelector('video'); return { t: v.currentTime, paused: v.paused, w: v.videoWidth, rate: v.playbackRate, ready: v.readyState }; });

(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1400, height: 900 } });
  const p = await ctx.newPage();
  const problems = [];
  p.on('pageerror', e => problems.push('pageerror: ' + e.message));
  p.on('console', m => { if (m.type() === 'error' && !/status of 404/.test(m.text())) problems.push('console: ' + m.text()); });
  p.on('response', async r => { if (r.url().endsWith('/api/events')) console.log('EVENTS', r.status(), (r.status()!==200 ? (await r.text()).slice(0,300) + ' | body: ' + (r.request().postData()||'').slice(0,400) : (r.request().postData()||'').match(/"watch":\[[^\]]*\]/)?.[0]?.slice(0,160) ?? '')); });
  p.on('request', r => { if (!r.url().startsWith(BASE)) problems.push('EXTERNAL: ' + r.url()); });

  await p.goto(BASE + '/');
  await p.fill('input[type=password]', 'e2e-password'); await p.click('button.primary');
  await p.waitForSelector('header');

  // ---- DASH playback
  await p.goto(BASE + '/watch?v=e2eVIDEO001&src=subs');
  await p.waitForFunction(() => document.querySelector('video')?.currentTime > 1.5, null, { timeout: 20000 }).catch(() => {});
  let s = await state(p);
  check('dash: plays and advances', s.t > 1.5 && !s.paused, JSON.stringify(s));
  check('dash: decoded 640px wide (auto picks 360p)', s.w === 640);
  check('chapters listed', (await p.locator('.chapters li').count()) === 3);
  check('seek bar has 3 chapter segments', (await p.locator('.bar .seg').count()) === 3);
  await p.screenshot({ path: require('os').tmpdir() + '/newtube-watch.png' });

  // ---- keyboard
  let before = (await state(p)).t;
  await p.keyboard.press('ArrowRight'); await p.waitForTimeout(400);
  check('ArrowRight seeks ~+5s', (await state(p)).t - before > 4, `${before.toFixed(1)} -> ${(await state(p)).t.toFixed(1)}`);
  await p.keyboard.press('k'); await p.waitForTimeout(300);
  check('k pauses', (await state(p)).paused);
  await p.keyboard.press('k'); await p.waitForTimeout(300);
  check('k resumes', !(await state(p)).paused);
  await p.keyboard.press('>'); await p.waitForTimeout(200);
  check('> raises speed to 1.25', (await state(p)).rate === 1.25);
  await p.keyboard.press('<'); await p.waitForTimeout(200);

  // ---- chapters
  await p.click('.chapters li:nth-child(2) button'); await p.waitForTimeout(800);
  const ct = (await state(p)).t;
  check('chapter click seeks to 8s', ct >= 8 && ct < 10.5, ct.toFixed(1));
  check('current chapter highlighted', await p.locator('.chapters li.current').innerText().then(x => x.includes('Middle part')));
  await p.keyboard.press(']'); await p.waitForTimeout(600);
  check('] jumps to next chapter (16s)', (await state(p)).t >= 16);

  // ---- quality
  await p.hover('.player'); await p.click('button[title=Quality]');
  await p.click('.menu button:has-text("180p")'); await p.waitForTimeout(2500);
  check('quality 180p -> decoded width 320', (await state(p)).w === 320, 'w=' + (await state(p)).w);
  await p.hover('.player'); await p.click('button[title=Quality]'); await p.click('.menu button:has-text("Auto")');

  // ---- audio language: default = original, dubs only on request, choice survives a reload
  const streams = [];
  p.on('request', r => { const m = r.url().match(/\/api\/stream\/e2eVIDEO001\/(\S+)$/); if (m) streams.push(m[1]); });
  await p.hover('.player');
  check('audio menu offered (video has 2 languages)', (await p.locator('button[title="Audio language"]').count()) === 1);
  await p.click('button[title="Audio language"]');
  const items = await p.locator('.menu button').allInnerTexts();
  check('original listed first and labelled', /English.*original/i.test(items[0] ?? ''), JSON.stringify(items));
  await p.click('.menu button:has-text("Spanish")'); await p.waitForTimeout(2500);
  check('switching to Spanish fetches the Spanish stream', streams.includes('140-1'), JSON.stringify([...new Set(streams)]));
  check('video keeps playing after the switch', (await state(p)).t > ct && !(await state(p)).paused);
  check('choice remembered', (await V(p, () => localStorage.getItem('audioLang'))) === 'es-US');
  check('DRC copy never used', !streams.includes('140-drc'));
  await p.hover('.player'); await p.click('button[title="Audio language"]');
  await p.click('.menu button:has-text("original")'); await p.waitForTimeout(1500);
  check('back to original', (await V(p, () => localStorage.getItem('audioLang'))) === 'original');

  // ---- captions
  await p.hover('.player'); await p.click('button[title^=Captions]'); await p.click('.menu button:has-text("English")');
  await p.waitForTimeout(1500);
  const cap = await V(p, () => { const t = [...document.querySelector('video').textTracks][0]; return t && { mode: t.mode, cues: t.cues?.length }; });
  check('captions load via proxy and show', cap && cap.mode === 'showing' && cap.cues === 2, JSON.stringify(cap));

  // ---- logging + resume (jump to ~40% first: the short test video would otherwise finish and count as watched)
  await p.keyboard.press('4'); await p.waitForTimeout(1500);
  // ---- logging + resume
  await p.waitForTimeout(500);
  const pos = (await state(p)).t;
  await p.goto(BASE + '/');                     // leaving flushes the session
  await p.waitForSelector('header'); await p.waitForTimeout(1200);
  const info = await p.evaluate(async () => (await fetch('/api/video/e2eVIDEO001')).json());
  check('resume position saved', info.resume_position > 5, 'resume=' + info.resume_position + ' last=' + pos.toFixed(1));
  await p.goto(BASE + '/watch?v=e2eVIDEO001');
  await p.waitForFunction(() => document.querySelector('video')?.readyState >= 3, null, { timeout: 20000 }).catch(() => {});
  const resumed = (await state(p)).t;
  check('reopening resumes near saved position', Math.abs(resumed - info.resume_position) < 3, resumed.toFixed(1));

  // ---- muxed fallback
  await p.goto(BASE + '/watch?v=e2eMUXED001');
  await p.waitForFunction(() => document.querySelector('video')?.currentTime > 1, null, { timeout: 20000 }).catch(() => {});
  s = await state(p);
  check('muxed fallback plays when DASH is unavailable', s.t > 1 && s.w === 640, JSON.stringify(s));

  const hits = await (await ctx.request.get(BASE + '/__hits')).json();
  const gv = hits.filter(h => /^\/(v360|v180|aud|mux)$/.test(h[0]));
  check('video bytes only via Range requests', gv.length > 0 && gv.every(h => h[1] && h[1].startsWith('bytes=')), gv.length + ' requests');
  check('no external requests / console errors', problems.length === 0, JSON.stringify(problems));
  await b.close();
  console.log(failed ? `\n${failed} FAILED` : '\nALL PASSED');
  process.exit(failed ? 1 : 0);
})();
