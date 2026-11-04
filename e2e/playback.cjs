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
  p.on('console', m => { if (m.type() === 'error' && !/status of (400|404|409)|ERR_INTERNET_DISCONNECTED|ERR_CONNECTION_RESET|ERR_CONNECTION_REFUSED/.test(m.text())) problems.push('console: ' + m.text()); });
  p.on('response', async r => { if (r.url().endsWith('/api/events')) console.log('EVENTS', r.status(), (r.status()!==200 ? (await r.text()).slice(0,300) + ' | body: ' + (r.request().postData()||'').slice(0,400) : (r.request().postData()||'').match(/"watch":\[[^\]]*\]/)?.[0]?.slice(0,160) ?? '')); });
  p.on('request', r => { if (!r.url().startsWith(BASE)) problems.push('EXTERNAL: ' + r.url()); });

  await p.goto(BASE + '/');
  await p.fill('input[type=password]', 'e2e-password'); await p.click('button.primary');
  await p.waitForSelector('aside.side');

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
  await p.keyboard.press('}'); await p.waitForTimeout(600);
  check('} (Shift+]) jumps to next chapter (16s)', (await state(p)).t >= 16);

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
  await p.waitForSelector('aside.side'); await p.waitForTimeout(1200);
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

  // ---- SponsorBlock
  const put = (body) => p.evaluate(async (b) => (await fetch('/api/settings/sponsorblock', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(b) })).status, body);
  const CH = 'UC' + 'a'.repeat(22);
  await put({ categories: {}, channels: {}, min_length: 1, show_toast: true, contribute: false });   // back to defaults
  await p.setViewportSize({ width: 1400, height: 900 });
  await p.goto(BASE + '/watch?v=e2eSPONSOR1');
  await p.waitForFunction(() => document.querySelector('video')?.currentTime > 0.3, null, { timeout: 20000 });
  await p.evaluate(() => { window.__ts = []; setInterval(() => window.__ts.push(document.querySelector('video').currentTime), 50); });
  check('seek bar shows the 4 segments, sponsor in the usual green', (await p.locator('.sbmark').count()) === 4 &&
    (await p.locator('.sbmark').first().evaluate(e => getComputedStyle(e).backgroundColor)) === 'rgb(0, 212, 0)');
  check('whole-video label is shown (not skipped)', (await p.locator('.fulllabel').innerText()).includes('Unpaid / self promotion'));
  check('highlight offers a jump button before it', (await p.locator('.sbskip:has-text("Jump to highlight")').count()) === 1);
  await p.waitForSelector('.sbtoast', { timeout: 10000 });
  const ts = await V(p, () => window.__ts);
  check('sponsor segment is skipped automatically', !ts.some(x => x > 4.4 && x < 5.8) && ts.some(x => x >= 5.9), `max seen ${Math.max(...ts).toFixed(1)}`);
  check('toast names the category', (await p.locator('.sbtoast').innerText()).includes('Skipped Sponsor'));
  await p.click('.sbtoast button');                                   // Undo
  await p.waitForTimeout(300);
  const back = (await state(p)).t;
  check('Undo returns to the start of the segment', back >= 3.7 && back < 4.9, back.toFixed(1));
  await p.waitForTimeout(2600);
  const after = (await state(p)).t;
  check('after Undo it plays through and does not skip again', after > 6.2 && after < 7.8, after.toFixed(1));

  await p.keyboard.press('4');                                         // ~9.6 s: just before the intro [10,12]
  await p.waitForSelector('.sbskip:has-text("Skip Intermission")', { timeout: 6000 });
  await p.click('.sbskip:has-text("Skip Intermission")');
  await p.waitForTimeout(300);
  check('"button" categories wait for a click, then skip', (await state(p)).t >= 11.9);

  await p.keyboard.press('6');                                         // ~14.4 s: inside the mark-only segment
  await p.waitForTimeout(1800);
  check('"mark" categories never skip or show a skip button', (await state(p)).t > 15.5 && (await p.locator('.sbskip:has-text("Skip")').count()) === 0);
  await p.click('.sbskip:has-text("Jump to highlight")').catch(() => {});

  await p.waitForTimeout(5500);                                        // let the tracker flush
  const sbHits = (await (await ctx.request.get(BASE + '/__hits')).json()).filter(h => h[0].startsWith('/api/skipSegments'));
  check('SponsorBlock was asked with a 4-char hash prefix only, never the video id',
    sbHits.length >= 1 && sbHits.every(h => /^\/api\/skipSegments\/[0-9a-f]{4}$/.test(h[0])), JSON.stringify(sbHits.slice(0, 2)));
  const sbEvents = (await (await ctx.request.get(BASE + '/__events')).json()).filter(e => e.type.startsWith('sb_'));
  check('skips and undos are logged locally for the recommender',
    sbEvents.some(e => e.type === 'sb_skip' && JSON.parse(e.payload).uuid.startsWith('sp1')) &&
    sbEvents.some(e => e.type === 'sb_undo') && sbEvents.some(e => e.type === 'sb_manual_skip' && JSON.parse(e.payload).uuid.startsWith('in1')),
    sbEvents.map(e => e.type).join(','));

  // Earlier visits leave a resume position (correctly), so rewind to the start after each reload.
  const fresh = async () => {
    await p.goto(BASE + '/watch?v=e2eSPONSOR1');
    await p.waitForFunction(() => { const v = document.querySelector('video'); return v && v.readyState >= 3 && v.currentTime > 0.2; }, null, { timeout: 20000 });
    await p.waitForTimeout(800);        // let the player finish starting up: it may still seek to the resume point
    await p.keyboard.press('Home');
    await p.evaluate(() => { window.__ts = []; setInterval(() => window.__ts.push(document.querySelector('video').currentTime), 50); });
  };
  // settings: ignore a category -> no mark, no skip
  check('settings accept an "ignore"', (await put({ categories: { sponsor: 'ignore' } })) === 200);
  await fresh();
  await p.waitForFunction(() => document.querySelector('video').currentTime > 6.2, null, { timeout: 15000 });
  check('ignored category: no mark and no skip', (await p.locator('.sbmark').count()) === 3 &&
    (await V(p, () => window.__ts)).some(x => x > 4.4 && x < 5.8));
  // per-channel override beats the global setting
  check('channel override accepted', (await put({ categories: { sponsor: 'ignore' }, channels: { [CH]: { sponsor: 'auto' } } })) === 200);
  await fresh();
  const reenabled = await p.waitForSelector('.sbtoast', { timeout: 12000 }).then(() => true).catch(() => false);
  check('a per-channel override re-enables skipping for that channel', reenabled && (await p.locator('.sbmark').count()) === 4);
  await p.hover('.player');
  await p.click('button[title^="SponsorBlock for this video"]');
  check('the SB switch turns SponsorBlock off for this video', (await p.locator('.sbmark').count()) === 0);

  // Settings page
  await put({ categories: {}, channels: { [CH]: { intro: 'ignore' } } });
  await p.goto(BASE + '/settings'); await p.waitForSelector('.cats');
  check('settings page lists every category and the contribute opt-in is off by default',
    (await p.locator('.cats tr').count()) === 10 && !(await p.locator('text=Allow submitting segments and voting').locator('input').isChecked()));
  check('settings page shows the privacy warning', (await p.locator('.warn').innerText()).includes("which video you mean"));
  check('channel overrides are listed and removable', (await p.locator('.over li').count()) === 1);
  await p.click('.over li button'); await p.waitForTimeout(500);
  check('removing an override saves', (await V(p, async () => Object.keys((await (await fetch('/api/settings/sponsorblock')).json()).channels).length)) === 0);
  await put({ categories: {}, channels: {}, min_length: 1, show_toast: true, contribute: false });

  // ---- SponsorBlock: contributing is opt-in
  const sbPosts = async () => (await ctx.request.get(BASE + '/__sbposts')).json();
  await put({ categories: {}, channels: {}, contribute: false });
  await fresh();
  await p.waitForSelector('.sbtoast', { timeout: 10000 });
  check('with contributing off the skip toast has no vote buttons', (await p.locator('.sbtoast .vote').count()) === 0);
  await p.keyboard.press('[');
  check('[ says contributing is off', (await p.locator('.toast').innerText()).includes('Contributing is off'));
  check('...and nothing was sent to SponsorBlock', (await sbPosts()).length === 0);

  await put({ categories: {}, channels: {}, contribute: true });
  await fresh();
  await p.keyboard.press('[');
  await p.waitForTimeout(1300);
  await p.keyboard.press(']');
  await p.waitForSelector('.sbdialog');
  check('dialog opens, pauses the video and says what submitting reveals', (await state(p)).paused && (await p.locator('.sbdialog .note').innerText()).includes('which video'));
  await p.selectOption('.sbdialog select', 'interaction');
  await p.click('.sbdialog .go');
  await p.waitForFunction(() => document.querySelector('.toast')?.innerText.includes('Submitted'), null, { timeout: 8000 });
  const first = (await sbPosts())[0];
  check('submission reaches SponsorBlock with the right body',
    first && first.path === '/api/skipSegments' && first.body.videoID === 'e2eSPONSOR1' && first.body.segments[0].category === 'interaction' &&
    first.body.segments[0].actionType === 'skip' && first.body.segments[0].segment[1] - first.body.segments[0].segment[0] > 1, JSON.stringify(first?.body));
  check('anonymous random user id (64 hex chars)', /^[0-9a-f]{64}$/.test(first?.body.userID ?? ''));
  check('no viewed-ping or other call was made to SponsorBlock', (await sbPosts()).every(x => ['/api/skipSegments', '/api/voteOnSponsorTime'].includes(x.path)));

  await p.keyboard.press('k');                                         // the dialog paused it; carry on to the sponsor skip
  await p.waitForSelector('.sbtoast .vote', { timeout: 10000 });
  await p.click('.sbtoast .vote[title^="Good skip"]');
  await p.waitForFunction(() => document.querySelector('.toast')?.innerText.includes('Upvoted'), null, { timeout: 5000 });
  await p.keyboard.press('Home');                                      // rewind: the segment arms again
  await p.waitForSelector('.sbtoast .vote', { timeout: 10000 });
  await p.click('.sbtoast .vote[title^="Wrong skip"]');
  await p.waitForFunction(() => document.querySelector('.toast')?.innerText.includes('Downvoted'), null, { timeout: 5000 });
  const votes = (await sbPosts()).filter(x => x.path === '/api/voteOnSponsorTime');
  check('votes are sent as up=1 / down=0 for the skipped segment', votes.length === 2 && votes[0].body.type === 1 && votes[1].body.type === 0 &&
    votes.every(v => v.body.UUID.startsWith('sp1') && v.body.UUID.length === 64 && v.body.userID === first.body.userID), JSON.stringify(votes.map(v => v.body.type)));
  await put({ categories: {}, channels: {}, min_length: 1, show_toast: true, contribute: false });

  // ---- 1440p / 4K: VP9 in WebM, played by the real browser through our manifest + index ranges
  await p.evaluate(async () => fetch('/api/settings/playback', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ codec: 'auto', max_height: 0 }) }));
  await p.goto(BASE + '/watch?v=e2eVP9VID01');
  await p.waitForFunction(() => { const v = document.querySelector('video'); return v && v.readyState >= 3 && v.currentTime > 1.2; }, null, { timeout: 25000 });
  const mpd = await p.evaluate(async () => (await fetch('/api/dash/e2eVP9VID01.mpd')).text());
  check('manifest offers VP9 (WebM) video with H.264 left out', mpd.includes('video/webm') && mpd.includes('vp09') && !mpd.includes('avc1'));
  check('real VP9/WebM plays (decoded 640px)', (await state(p)).w === 640 && (await state(p)).t > 1.2, JSON.stringify(await state(p)));
  await p.hover('.player'); await p.click('button[title=Quality]');
  const qopts = await p.locator('.menu button').allInnerTexts();
  check('quality menu names 4K and 2K, highest first', qopts[1] === '2160p (4K)' && qopts[2] === '1440p60 (2K)', JSON.stringify(qopts));
  await p.click('.menu button:has-text("1440p60")'); await p.waitForTimeout(2500);
  check('switching WebM representations works (decoded 320px)', (await state(p)).w === 320 && !(await state(p)).paused, 'w=' + (await state(p)).w);
  const tt0 = (await state(p)).t; await p.waitForTimeout(1500);
  check('...and keeps playing', (await state(p)).t > tt0 + 1, '');
  // the Settings page controls what is offered
  await p.evaluate(async () => fetch('/api/settings/playback', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ max_height: 1080 }) }));
  const mpd2 = await p.evaluate(async () => (await fetch('/api/dash/e2eVP9VID01.mpd')).text());
  check('a 1080p limit falls back to H.264', mpd2.includes('avc1') && !mpd2.includes('vp09'));
  await p.goto(BASE + '/settings'); await p.waitForSelector('select');
  check('Playback section on the settings page', (await p.locator('h2:has-text("Playback")').count()) === 1 && (await p.locator('select').nth(1).inputValue()) === 'auto');         // (the first select is the start page)
  await p.evaluate(async () => fetch('/api/settings/playback', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ codec: 'auto', max_height: 0 }) }));

  // ---- comments: lazy, sortable, replies capped at the first 10, timestamps seek
  const commentReqs = [];
  p.on('request', r => { if (r.url().includes('/api/comments/')) commentReqs.push(new URL(r.url()).pathname + new URL(r.url()).search); });
  await p.setViewportSize({ width: 1400, height: 600 });      // short window: the comments start below the fold
  await p.goto(BASE + '/watch?v=e2eVIDEO001'); await p.waitForSelector('.comments');
  await p.waitForTimeout(1500);
  check('comments are NOT requested until scrolled into view', commentReqs.length === 0, JSON.stringify(commentReqs));
  await p.locator('.comments').scrollIntoViewIfNeeded();
  await p.waitForSelector('li.comment');
  check('first 20 comments load, with estimate in the header', (await p.locator('li.comment').count()) === 20 && (await p.locator('.comments h2').innerText()).includes('4.3K'));
  check('pinned comment shows its badge', await p.locator('li.comment').first().locator('.badge[title=Pinned]').count() === 1);
  await p.locator('li.comment').first().locator('button:has-text("Replies")').click();
  await p.waitForSelector('.reply');
  check('replies expand under the comment', (await p.locator('.reply').count()) === 2);
  check('only the first 10 comments offer replies', (await p.locator('li.comment > .main button:has-text("Replies"), li.comment > .main button:has-text("Hide replies")').count()) === 10);
  await p.click('.comments button:has-text("Load more")'); await p.waitForFunction(() => document.querySelectorAll('li.comment').length === 40);
  check('load more adds 20 and keeps opened replies', (await p.locator('.reply').count()) === 2);
  await p.click('.sorts button:has-text("Newest")'); await p.waitForFunction(() => document.querySelector('li.comment .text')?.innerText.startsWith('new comment 0'));
  check('sort switches to newest and resets to 20', (await p.locator('li.comment').count()) === 20);
  await p.locator('li.comment').first().locator('.ts').click(); await p.waitForTimeout(1200);
  const tt = (await state(p)).t;
  check('clicking a timestamp in a comment seeks the video', tt >= 9.5 && tt < 14, tt.toFixed(1));
  check('links in comments open safely in a new tab', (await p.locator('li.comment a[target=_blank][rel*=noreferrer]').count()) >= 1);
  const bad = await p.evaluate(async () => (await fetch('/api/comments/e2eVIDEO001/replies/Ugx0000011?sort=new')).status);
  check('replies beyond the first 10 are refused (409)', bad === 409, String(bad));
  await p.waitForTimeout(5500);
  const evs2 = await (await ctx.request.get(BASE + '/__events')).json();
  check('comments_opened was logged once', evs2.filter(e => e.type === 'comments_opened').length >= 1);

  await p.setViewportSize({ width: 1400, height: 900 });
  // ---- search, channel and playlist pages
  await p.goto(BASE + '/'); await p.waitForSelector('aside.side');
  await p.keyboard.press('/');
  check('/ focuses the search box', await V(p, () => document.activeElement?.getAttribute('aria-label') === 'Search'));
  await p.keyboard.type('zoo'); await p.keyboard.press('Enter');
  await p.waitForSelector('.card');
  check('search shows video results', p.url().includes('/search?q=zoo') && (await p.locator('.card .title').first().innerText()).includes('Search result video'));
  await p.waitForTimeout(500);
  // if the sharp thumbnail cannot be had (an older server, a missing size...) cards fall back to the small one, never blank
  await p.route('**/api/thumb/*/best', (r) => r.fulfill({ status: 400, contentType: 'application/json', body: '{"detail":"bad thumbnail name"}' }));
  await p.goto(BASE + '/search?q=zoo&kind=video'); await p.waitForSelector('.card img');
  const fellBack = await p.waitForFunction(() => { const i = document.querySelector('.card img'); return !!i && i.complete && i.naturalWidth > 0 && i.src.includes('mqdefault'); }, null, { timeout: 8000 }).then(() => true).catch(() => false);
  check('thumbnails fall back to the small picture when the sharp one fails, instead of going blank', fellBack);
  await p.unroute('**/api/thumb/*/best');
  await p.goto(BASE + '/search?q=zoo&kind=video'); await p.waitForSelector('.card img');
  check('result thumbnails load through our proxy', await V(p, () => { const i = document.querySelector('.card img'); return !!i && i.complete && i.naturalWidth > 0; }));
  await p.click('.tabs a:has-text("Channels")'); await p.waitForSelector('main .row');
  await p.waitForFunction(() => { const i = document.querySelector('main .row img'); return !!i && i.complete && i.naturalWidth > 0; }, null, { timeout: 8000 }).catch(() => {});
  check('channel result avatar loads via /api/img (protocol-relative url handled)', await V(p, () => { const i = document.querySelector('main .row img'); return !!i && i.complete && i.naturalWidth > 0 && i.src.includes('/api/img'); }));
  await p.click('main .row'); await p.waitForSelector('.head');
  check('channel page: header and uploads', (await p.locator('.head h1').innerText()) === 'E2E Channel' && (await p.locator('.grid .card').count()) === 3);
  await p.click('.head button'); await p.waitForFunction(() => document.querySelector('.head button')?.innerText.includes('Subscribed'));
  const subs = await V(p, async () => (await (await fetch('/api/subscriptions')).json()));
  check('Subscribe button subscribes', subs.some(x => x.id === 'UC' + 'a'.repeat(22)));
  await p.goto(BASE + '/search?q=zoo&kind=playlist'); await p.waitForSelector('.card');
  await p.click('.card'); await p.waitForSelector('.grid .card');
  check('playlist result opens the playlist page', p.url().includes('/playlist?list=PLe2eplaylist00') && (await p.locator('.grid .card').count()) === 2);
  await p.click('a:has-text("Play all")'); await p.waitForSelector('.plist');
  check('watch page shows playlist position', (await p.locator('.plist h2').innerText()).includes('1 / 2'));
  await p.waitForFunction(() => document.querySelector('video')?.readyState >= 3, null, { timeout: 20000 });
  await p.keyboard.press('9');                          // jump to 90%: the video ends in a couple of seconds
  const advanced = await p.waitForURL(/v=e2eMUXED001/, { timeout: 15000 }).then(() => true).catch(() => false);
  check('playlist autoplays the next video when one ends', advanced, p.url());
  await p.evaluate(() => navigator.sendBeacon('/api/events', new Blob(['{}'], { type: 'application/json' }))); // flush path
  await p.waitForTimeout(300);
  await p.goto(BASE + '/search?q=zoo'); await p.waitForSelector('.card');
  await p.click('.card .title'); await p.waitForURL(/\/watch/);
  await p.waitForTimeout(6000);                                   // tracker batches for 5 s
  const evs = await (await ctx.request.get(BASE + '/__events')).json();
  const searches = evs.filter(e => e.type === 'search' && JSON.parse(e.payload).q === 'zoo');
  const sclick = evs.filter(e => e.type === 'search_click' && e.video_id === 'e2eVIDEO001');
  check('search queries are logged (server side, per kind)', searches.length >= 2, searches.map(e => JSON.parse(e.payload).kind).join(','));
  check('search_click logged with query and position', sclick.length >= 1 && JSON.parse(sclick[0].payload).q === 'zoo' && JSON.parse(sclick[0].payload).position === 0, JSON.stringify(sclick[0]?.payload));

  // ---- sidebar: subscriptions list under the Subscriptions button, channel pages with sorting
  await p.evaluate(() => { try { localStorage.removeItem('subsOpen'); localStorage.removeItem('sidebarCollapsed'); } catch {} });
  await p.setViewportSize({ width: 1400, height: 900 });
  await p.goto(BASE + '/watch?v=e2eMUXED001');                         // somewhere other than the feed
  await p.waitForSelector('aside.side');
  check('the navigation is a left sidebar and there is no top bar', (await p.locator('aside.side').count()) === 1 &&
    (await p.evaluate(() => document.querySelector('aside.side').getBoundingClientRect().left)) === 0 && (await p.locator('body > div > header, #app > header').count()) === 0);
  check('subscribed channels are NOT listed until you click Subscriptions', (await p.locator('ul.subs').count()) === 0);
  await p.click('aside.side .row .label');                             // the "Subscriptions" button
  await p.waitForSelector('ul.subs li a');
  const listed = await p.locator('ul.subs li a .name').allInnerTexts();
  // (the fake data names this channel differently on different pages, so check the entry, not its spelling)
  check('clicking Subscriptions opens the channel list under it', listed.length === 1, JSON.stringify(listed));
  check('...and takes you to the subscriptions feed', new URL(p.url()).pathname === '/subscriptions');
  check('the channel list shows an avatar (served by our proxy)', await V(p, () => { const i = document.querySelector('ul.subs img'); return !!i && i.src.includes('/api/avatar/'); }));
  await p.click('aside.side .chev'); await p.waitForTimeout(150);
  check('the arrow collapses the list without leaving the page', (await p.locator('ul.subs').count()) === 0 && new URL(p.url()).pathname === '/subscriptions');
  await p.click('aside.side .chev'); await p.waitForSelector('ul.subs li a');
  await p.goto(BASE + '/'); await p.waitForSelector('aside.side');
  check('the open/closed state is remembered', (await p.locator('ul.subs').count()) === 1);
  await p.click('ul.subs li a');                                       // click the channel
  await p.waitForSelector('.head');
  check('clicking a channel opens its page, highlighted in the list', p.url().includes('/channel/UC') && (await p.locator('ul.subs a.active').count()) === 1);
  const titles = async () => (await p.locator('.grid .card .title').allInnerTexts()).join(',');
  check('default order is newest first', (await titles()) === 'Upload A,Upload B,Upload C' && (await p.locator('.sorts a.on').innerText()) === 'Newest');
  const allTitles = async () => await p.locator('.grid .card .title').allInnerTexts();
  const cards = () => p.locator('.grid .card').count();
  const toBottom = async (n) => {            // scroll down until n videos are loaded (the page loads more by itself)
    await p.waitForFunction((n) => { window.scrollTo(0, document.body.scrollHeight); return document.querySelectorAll('.grid .card').length >= n; }, n, { timeout: 20000, polling: 150 });
  };
  await p.click('.sorts a:has-text("Popular")');
  await p.waitForSelector('.scan', { timeout: 5000 });
  const sawProgress = await p.waitForFunction(() => /of 130 videos/.test(document.querySelector('.scan')?.innerText ?? ''), null, { timeout: 6000 }).then(() => true).catch(() => false);
  const scanText = await p.locator('.scan').innerText().catch(() => '(finished)');
  check('the first Popular click shows scan progress ("N of 130") instead of freezing', sawProgress, scanText.slice(0, 60).replace(/\n/g, ' '));
  await p.waitForFunction(() => document.querySelector('.grid .card .title')?.innerText === 'Upload B', null, { timeout: 15000 });
  check('Popular ranks the WHOLE channel by views (5000, 300, 100, ...) and the URL records it', (await titles()).startsWith('Upload B,Upload C,Upload A') && p.url().includes('sort=popular'));
  check('...and starts with one page of 60', (await cards()) === 60);
  await toBottom(130);
  check('scrolling loads the rest automatically, with no repeats', (await cards()) === 130 && new Set(await allTitles()).size === 130);
  check('the list says how many it ranked and offers a rescan', (await p.locator('.small:has-text("130 videos")').count()) === 1 && (await p.locator('.small button:has-text("Rescan")').count()) === 1);

  await p.click('.sorts a:has-text("Oldest")');
  await p.waitForFunction(() => document.querySelector('.grid .card .title')?.innerText === 'Filler 126', null, { timeout: 8000 });
  check('Oldest needs no second scan: it appears instantly, oldest first', (await p.locator('.scan').count()) === 0 && (await cards()) === 60 && p.url().includes('sort=oldest'));
  await toBottom(130);
  const oldest = await allTitles();
  check('Oldest goes all the way back to the very first uploads', oldest.length === 130 && oldest.slice(-3).join(',') === 'Upload C,Upload B,Upload A' && new Set(oldest).size === 130);
  await p.reload(); await p.waitForSelector('.sorts a.on:has-text("Oldest")');
  await p.waitForFunction(() => document.querySelector('.grid .card .title')?.innerText === 'Filler 126', null, { timeout: 8000 });
  check('a reload keeps the chosen sort (and the remembered list)', (await p.locator('.scan').count()) === 0);
  await p.click('.small button:has-text("Rescan")');
  await p.waitForSelector('.scan', { timeout: 5000 });
  check('Rescan reads the uploads again', (await p.locator('.scan').count()) === 1);
  await p.waitForFunction(() => document.querySelector('.grid .card .title')?.innerText === 'Filler 126', null, { timeout: 15000 });
  await p.click('.sorts a:has-text("Newest")');
  await p.waitForFunction(() => document.querySelector('.grid .card .title')?.innerText === 'Upload A');
  await p.click('.head button');                                       // unsubscribe from the channel page
  await p.waitForFunction(() => document.querySelector('.head button')?.innerText.includes('Subscribe') && !document.querySelector('.head button').innerText.includes('✓'));
  check('unsubscribing removes the channel from the sidebar straight away', (await p.locator('ul.subs li a').count()) === 0);
  await p.click('.head button'); await p.waitForFunction(() => document.querySelectorAll('ul.subs li a').length === 1);
  check('subscribing adds it back', (await p.locator('ul.subs li a .name').innerText()) === 'E2E Channel');
  // ---- Search filters: choose upload date / length / sort; they are part of the address and reach the server
  await p.goto(BASE + '/search?q=filtertest&kind=video'); await p.waitForSelector('.filters');
  check('a video search offers date, length and sort filters (not for channels)', (await p.locator('.filters select').count()) === 3);
  await p.selectOption('select[aria-label="Duration"]', 'long');
  await p.waitForFunction(() => location.search.includes('duration=long'));
  await p.selectOption('select[aria-label="Upload date"]', 'week');
  await p.waitForFunction(() => location.search.includes('date=week') && location.search.includes('duration=long'));
  await p.selectOption('select[aria-label="Sort by"]', 'views');
  await p.waitForSelector('.grid .card');
  check('the chosen filters are kept in the address and shown selected after the page reloads',
    new URL(p.url()).search.includes('sort=views') && (await p.locator('select[aria-label="Duration"]').inputValue()) === 'long' &&
    (await p.locator('select[aria-label="Upload date"]').inputValue()) === 'week' && (await p.locator('select[aria-label="Sort by"]').inputValue()) === 'views');
  const fEvents = await (await ctx.request.get(BASE + '/__events')).json();
  const fSearch = fEvents.filter(e => e.type === 'search' && JSON.parse(e.payload).q === 'filtertest').map(e => JSON.parse(e.payload).filters).filter(Boolean).pop();
  check('...and reach the server (and the local search log)', JSON.stringify(fSearch) === JSON.stringify({ date: 'week', duration: 'long', sort: 'views' }), JSON.stringify(fSearch));
  await p.goto(BASE + '/search?q=filtertest&kind=channel'); await p.waitForSelector('.tabs');
  check('channel searches have no video filters', (await p.locator('.filters').count()) === 0);
  await p.goto(BASE + '/search?q=filtertest&kind=video&date=decade&sort=bogus'); await p.waitForSelector('.filters');
  check('nonsense filter values in the address are ignored, not an error', (await p.locator('.error').count()) === 0 && (await p.locator('select[aria-label="Sort by"]').inputValue()) === '');

  // ---- Up next: related videos beside the player (or under it on a narrow window)
  await p.goto(BASE + '/watch?v=e2eVIDEO001'); await p.waitForSelector('.related .card', { timeout: 15000 });
  check('Up next lists the related videos', (await p.locator('.related .card').count()) === 2 &&
    (await p.locator('.related .card .title').first().innerText()) === 'Related one');
  const box = async (sel) => p.evaluate((s) => { const r = document.querySelector(s).getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom }; }, sel);
  const vb = await box('.stage video'), rb = await box('.related');
  check('on a wide window Up next is a column beside the video', rb.l >= vb.r - 1 && rb.t < vb.b, JSON.stringify({ vb, rb }));
  const wideW = vb.r - vb.l;
  await p.evaluate(() => { try { localStorage.removeItem('theater'); } catch {} });
  await p.keyboard.press('t'); await p.waitForSelector('.watch.theater');
  const vbT = await box('.stage video'), rbT = await box('.related');
  check('t switches to theater mode: a wider video with Up next below it', (vbT.r - vbT.l) > wideW + 100 && rbT.t >= vbT.b - 1, JSON.stringify({ wideW, vbT, rbT }));
  check('the theater button shows it is on', (await p.locator('.player button.theater[aria-pressed="true"]').count()) === 1);
  await p.reload(); await p.waitForSelector('.watch.theater');
  check('theater mode is remembered', (await p.locator('.watch.theater').count()) === 1);
  await p.locator('.player').hover(); await p.click('.player button.theater');
  await p.waitForFunction(() => !document.querySelector('.watch.theater'));
  check('the button switches back to the default view', (await p.locator('.player button.theater[aria-pressed="false"]').count()) === 1);
  await p.setViewportSize({ width: 800, height: 900 }); await p.waitForTimeout(300);
  const vb2 = await box('.stage video'), rb2 = await box('.related');
  check('on a narrow window it moves below the video', rb2.t >= vb2.b - 1, JSON.stringify({ vb2, rb2 }));
  await p.setViewportSize({ width: 1400, height: 900 });
  await p.click('.related .card .title >> nth=0'); await p.waitForURL(/v=e2eREL00001/);
  check('clicking one plays it and records where you came from', p.url().includes('src=related'));

  // ---- Mini-player: leaving the video page keeps the video playing in a corner box
  await p.goto(BASE + '/watch?v=e2eVIDEO001&src=subs'); await p.waitForSelector('.stage video');
  check('the video page is just the video page (no stray "Not found" message above it)', (await p.locator('main :text("Not found")').count()) === 0);
  await p.goto(BASE + '/watch?v=e2eVIDEO001&src=subs');
  await p.waitForFunction(() => document.querySelector('video')?.currentTime > 1.5, null, { timeout: 20000 });
  await p.evaluate(() => { document.querySelector('video').dataset.mark = 'orig'; });
  await p.click('aside.side a:has-text("Settings")');                    // an ordinary in-app navigation
  await p.waitForSelector('.watchhost.mini');
  const miniState = () => p.evaluate(() => { const v = document.querySelector('.watchhost video'); const r = document.querySelector('.watchhost').getBoundingClientRect();
    return { t: v.currentTime, paused: v.paused, mark: v.dataset.mark, n: document.querySelectorAll('video').length, r: r.right, b: r.bottom, w: r.width, vw: innerWidth, vh: innerHeight }; });
  const m0 = await miniState(); await p.waitForTimeout(1800); const m1 = await miniState();
  check('leaving the video page turns it into a mini-player that keeps playing (same video, no restart)',
    m1.mark === 'orig' && m1.n === 1 && !m1.paused && m1.t > m0.t + 1, JSON.stringify({ m0, m1 }));
  check('...in the bottom-right corner, small, with the page you went to intact', m1.r <= m1.vw && m1.b <= m1.vh && m1.vw - m1.r < 40 && m1.w <= 400 &&
    (await p.locator('h2:has-text("Start page")').count()) === 1 && (await p.locator('.watchhost.mini .related').isVisible()) === false);
  await p.evaluate(() => document.activeElement?.blur()); await p.keyboard.press(' '); await p.waitForTimeout(400);
  check('the mini-player ignores the keyboard (Space still belongs to the page)', !(await miniState()).paused);
  await p.hover('.watchhost.mini'); await p.click('.minibar button[aria-label="Open the video page"]');
  await p.waitForURL(/\/watch\?v=e2eVIDEO001/); await p.waitForTimeout(300);
  const expanded0 = await p.evaluate(() => { const v = document.querySelector('video'); return { mark: v.dataset.mark, paused: v.paused, n: document.querySelectorAll('video').length, mini: !!document.querySelector('.watchhost.mini') }; });
  check('expanding returns to the full page with the same video still playing', expanded0.mark === 'orig' && !expanded0.paused && expanded0.n === 1 && !expanded0.mini, JSON.stringify(expanded0));
  check('...and its keyboard shortcuts work again', await (async () => { await p.keyboard.press('k'); await p.waitForTimeout(300); const paused = await p.evaluate(() => document.querySelector('video').paused); await p.keyboard.press('k'); return paused; })());
  await p.click('aside.side a:has-text("Settings")'); await p.waitForSelector('.watchhost.mini');
  await p.hover('.watchhost.mini'); await p.click('.minibar button[aria-label="Close the mini player"]');
  await p.waitForFunction(() => !document.querySelector('video'));
  check('Close removes the mini-player and its video', (await p.locator('.watchhost').count()) === 0);
  // a different video replaces the mini-player (and is the page, not a second player)
  await p.goto(BASE + '/watch?v=e2eMUXED001&src=subs');
  await p.waitForFunction(() => document.querySelector('video')?.currentTime > 1, null, { timeout: 20000 });
  await p.evaluate(() => { document.querySelector('video').dataset.mark = 'muxed'; });
  await p.fill('aside.side input[aria-label="Search"]', 'zoo'); await p.keyboard.press('Enter');
  await p.waitForSelector('.page .grid .card'); await p.waitForSelector('.watchhost.mini');
  await p.click('.page .grid .card a.title >> nth=0'); await p.waitForURL(/\/watch\?v=e2eVIDEO001/);
  await p.waitForFunction(() => document.querySelector('video') && !document.querySelector('video').dataset.mark);
  check('opening another video replaces the mini-player: one player, the new video', (await p.locator('video').count()) === 1 && (await p.locator('.watchhost.mini').count()) === 0);

  // ---- Like / Watch later, and the lists they build
  await p.goto(BASE + '/watch?v=e2eMUXED001'); await p.waitForSelector('.byline .actions');
  check('the video page has Like and Watch later, both off to start with',
    (await p.locator('.byline button[aria-pressed="false"]').count()) === 2);
  await p.click('.byline button:has-text("Like")');
  await p.waitForSelector('.byline button[aria-pressed="true"]:has-text("Liked")');
  await p.click('.byline button:has-text("Watch later")');
  await p.waitForSelector('.byline button[aria-pressed="true"]:has-text("In Watch later")');
  await p.reload(); await p.waitForSelector('.byline .actions');
  check('...and they stay on after a reload', (await p.locator('.byline button[aria-pressed="true"]').count()) === 2);
  check('the sidebar Library shows the lists with their counts',
    (await p.locator('aside.side a:has-text("Liked videos") .count').innerText()) === '1' &&
    (await p.locator('aside.side a:has-text("Watch later") .count').innerText()) === '1');
  await p.click('aside.side a:has-text("Liked videos")'); await p.waitForSelector('.grid .card');
  check('Liked videos lists it, with a Remove action', (await p.locator('.grid .card').count()) === 1 &&
    (await p.locator('.page h1').innerText()) === 'Liked videos' && (await p.locator('.card .act:has-text("Remove")').count()) === 1);
  await p.click('a:has-text("Play all")'); await p.waitForSelector('.plist');
  check('Play all plays from the list (and shows it under the video)', p.url().includes('mylist=liked') && (await p.locator('.plist h2').innerText()).startsWith('Liked videos'));
  await p.goto(BASE + '/list/liked'); await p.waitForSelector('.grid .card');
  await p.click('.card .act:has-text("Remove")');
  await p.waitForFunction(() => !document.querySelector('.grid .card'));
  check('removing takes it off the list, the count and the video page',
    (await p.locator('p:has-text("Nothing here yet")').count()) === 1 && (await p.locator('aside.side a:has-text("Liked videos") .count').count()) === 0);
  await p.goto(BASE + '/watch?v=e2eMUXED001'); await p.waitForSelector('.byline .actions');
  check('...the video page agrees', (await p.locator('.byline button[aria-pressed="true"]').count()) === 1);
  await p.click('.byline button:has-text("In Watch later")');
  await p.waitForSelector('.byline button[aria-pressed="false"]:has-text("Watch later")');
  check('toggling Watch later off works from the video page too', (await p.locator('.byline button[aria-pressed="true"]').count()) === 0);
  await p.goto(BASE + '/list/nope'); await p.waitForSelector('.error');
  check('an unknown list says so instead of breaking', (await p.locator('.error').innerText()).includes('no such list'));

  // ---- Your own playlists: create from the Save menu, fill, reorder, rename, delete
  await p.goto(BASE + '/watch?v=e2eMUXED001'); await p.waitForSelector('.byline .actions');
  await p.click('.byline button:has-text("Save")');
  check('the Save menu starts with no playlists', (await p.locator('.menu p:has-text("No playlists yet")').count()) === 1);
  await p.fill('input[aria-label="New playlist name"]', 'E2E mix'); await p.click('.menu button:has-text("Create")');
  await p.waitForSelector('.byline button:has-text("In 1 playlist")');
  check('Create & save makes the playlist and puts the video in it', (await p.locator('.menu input[type=checkbox]:checked').count()) === 1);
  check('...and it appears in the sidebar Library', (await p.locator('aside.side a:has-text("E2E mix")').count()) === 1);
  await p.keyboard.press('Escape');
  await p.goto(BASE + '/watch?v=e2eVIDEO001'); await p.waitForSelector('.byline .actions');
  await p.click('.byline button:has-text("Save")');
  await p.click('.menu label:has-text("E2E mix") input');
  await p.waitForSelector('.byline button:has-text("In 1 playlist")');
  await p.click('aside.side a:has-text("E2E mix")'); await p.waitForSelector('.grid .card');
  const ids = () => p.locator('.grid .card a.title').evaluateAll((as) => as.map((a) => new URL(a.href).searchParams.get('v')));
  const order1 = await ids();
  check('the playlist lists both videos in the order added', order1.join() === 'e2eMUXED001,e2eVIDEO001', order1.join());
  await p.click('.grid .card:first-child .act[aria-label="Move later"]');
  await p.waitForFunction((first) => new URL(document.querySelector('.grid .card a.title').href).searchParams.get('v') !== first, order1[0]);
  await p.reload(); await p.waitForSelector('.grid .card');
  const order2 = await ids();
  check('Move later reorders it, and the new order survives a reload', order2.join() === order1.slice().reverse().join(), order2.join());
  await p.click('a:has-text("Play all")'); await p.waitForSelector('.plist');
  check('Play all starts with the first video of the new order', new URL(p.url()).searchParams.get('v') === order2[0] && p.url().includes('mylist='));
  await p.goto(BASE + '/list/' + (await p.evaluate(() => 0).then(() => '3'))); await p.waitForSelector('.page h1');
  await p.click('button:has-text("Rename")'); await p.fill('input[aria-label="Playlist name"]', 'E2E renamed'); await p.click('.rename button:has-text("Save")');
  await p.waitForFunction(() => document.querySelector('.page h1')?.innerText === 'E2E renamed');
  check('Rename changes the title and the sidebar', (await p.locator('aside.side a:has-text("E2E renamed")').count()) === 1);
  p.once('dialog', (d) => d.accept());
  await p.click('button:has-text("Delete playlist")');
  await p.waitForFunction(() => location.pathname === '/');
  check('Delete playlist removes it from the sidebar and returns home', (await p.locator('aside.side a:has-text("E2E renamed")').count()) === 0);
  await p.goto(BASE + '/list/liked'); await p.waitForSelector('.page h1');
  check('Liked videos cannot be renamed or deleted', (await p.locator('button:has-text("Rename")').count()) === 0 && (await p.locator('button:has-text("Delete playlist")').count()) === 0);

  // ---- History says what actually happened: finished, stopped part-way, or only "opened" (imported)
  const beat = (id, pos, dur, ivs) => ctx.request.post(BASE + '/api/events', { data: { watch: [{ session_id: 's' + id, video_id: id, source: 'direct', position: pos, duration: dur, intervals: ivs, ended: true }] } });
  await beat('e2eHALF0001', 600, 1200, [[0, 600]]);          // half of a 20-minute video
  await beat('e2eDONE0001', 1195, 1200, [[0, 1195]]);        // all of it
  await ctx.request.get(BASE + '/__seed_import_watch');
  await p.goto(BASE + '/history'); await p.waitForSelector('.grid .card');
  const noteOf = async (id) => p.locator('.card', { has: p.locator(`a.title[href*="${id}"]`) }).locator('.meta .muted.small').last().innerText();
  const half = await noteOf('e2eHALF0001'), done = await noteOf('e2eDONE0001'), imp = await noteOf('e2eIMPORT01');
  check('History: a half-watched video says where you stopped, not "watched"', /^Stopped at 10:00 of 20:00 \(50%\)/.test(half), half);
  check('History: a video you finished says so', /^Finished/.test(done), done);
  check('History: an imported entry only claims it was opened', /^Opened/.test(imp), imp);
  check('History: no "Watched before" tags (every entry is in your history)', (await p.locator('.card .seen').count()) === 0);

  // ---- Flaky networks: what happens while the laptop is offline is kept and delivered once, with its real time
  const refreshes = async () => (await (await ctx.request.get(BASE + '/__events')).json()).filter((e) => e.type === 'feed_refresh');
  const waiting = () => p.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('nt_outbox:')).length);
  await p.goto(BASE + '/'); await p.waitForSelector('button:has-text("Refresh")'); await p.waitForTimeout(1800);
  const r0 = (await refreshes()).length;
  await ctx.setOffline(true);
  const clickedAt = Date.now() / 1000;
  await p.click('button:has-text("Refresh")');
  await p.waitForTimeout(6500);                                      // the 5 s flush tries, fails, and keeps the batch
  check('offline: the event is kept (in memory and on disk) instead of being dropped', (await waiting()) >= 1, String(await waiting()));
  await ctx.setOffline(false);
  await p.waitForFunction(() => !Object.keys(localStorage).some((k) => k.startsWith('nt_outbox:')), null, { timeout: 15000 }).catch(() => {});
  const r1 = await refreshes();
  check('offline: when the network returns it is delivered, once', r1.length === r0 + 1 && (await waiting()) === 0, JSON.stringify([r0, r1.length, await waiting()]));
  const stamped = r1.at(-1).ts;
  check('offline: ...filed under when it HAPPENED, not when it got through', Math.abs(stamped - clickedAt) < 3 && Date.now() / 1000 - stamped > 5, JSON.stringify([stamped - clickedAt]));
  // the reply is lost on the way back (the server HAS it): the resend must not be counted again
  let dropped = false;
  await p.route('**/api/events', async (route) => { if (!dropped) { dropped = true; await route.fetch(); await route.abort('connectionreset'); } else await route.continue(); });
  await p.click('button:has-text("Refresh")');
  await p.waitForTimeout(6500);                                      // the 5 s flush sends it; the reply is cut
  await p.waitForFunction(() => !Object.keys(localStorage).some((k) => k.startsWith('nt_outbox:')), null, { timeout: 25000 }).catch(() => {});
  await p.unroute('**/api/events');
  const r2 = await refreshes();
  check('a lost reply makes the browser resend, and the server counts it once', dropped && r2.length === r0 + 2 && (await waiting()) === 0, JSON.stringify([dropped, r0, r2.length]));
  // the tab is closed while offline: the next page delivers what the last one could not
  await ctx.setOffline(true);
  await p.click('button:has-text("Refresh")'); await p.waitForTimeout(300);
  await p.goto('about:blank');
  await ctx.setOffline(false);
  const again = await ctx.newPage();
  await again.goto(BASE + '/'); await again.waitForSelector('aside.side');
  await again.waitForFunction(() => !Object.keys(localStorage).some((k) => k.startsWith('nt_outbox:')), null, { timeout: 15000 }).catch(() => {});
  const r3 = await refreshes();
  check('closing the tab while offline loses nothing: the next page sends it', r3.length === r0 + 3, JSON.stringify([r0, r3.length]));
  await again.close();
  await p.goto(BASE + '/'); await p.waitForSelector('aside.side');

  // ---- Playback survives the connection dropping (wifi switched, server restarted, a captive-portal network...)
  await p.goto(BASE + '/watch?v=e2eLONG0001&src=direct');
  await p.waitForFunction(() => document.querySelector('video')?.currentTime > 1, null, { timeout: 25000 });
  await p.evaluate(() => { document.querySelector('video').dataset.mark = 'long'; });
  await ctx.setOffline(true);
  await p.evaluate(() => { document.querySelector('video').currentTime = 200; });        // not buffered: with no network it can only stall
  await p.waitForSelector('.netnote', { timeout: 25000 });
  const offNote = await p.locator('.netnote').innerText();
  check('offline: the player says so instead of freezing silently', /offline|reconnect/i.test(offNote), offNote);
  await p.waitForTimeout(3000);
  await ctx.setOffline(false);
  const back1 = await p.waitForFunction(() => { const v = document.querySelector('video'); return !!v && v.currentTime > 202 && !v.paused && v.dataset.mark === 'long'; }, null, { timeout: 45000 }).then(() => true).catch(() => false);
  const st1 = await p.evaluate(() => { const v = document.querySelector('video'); return v ? { t: v.currentTime, paused: v.paused, mark: v.dataset.mark, ready: v.readyState } : null; });
  check('offline: playback carries on by itself when the network returns (same player, no reload)', back1, JSON.stringify(st1));
  await p.waitForFunction(() => !document.querySelector('.netnote'), null, { timeout: 10000 }).catch(() => {});
  check('...and the notice goes away', (await p.locator('.netnote').count()) === 0);
  // "online" but nothing gets through (the server restarting, a hotel wifi login page): same recovery
  await p.route('**/api/dash/**', (r) => r.abort('connectionrefused'));
  await p.route('**/api/stream/**', (r) => r.abort('connectionrefused'));
  await p.evaluate(() => { document.querySelector('video').currentTime = 300; });
  await p.waitForSelector('.netnote', { timeout: 25000 });
  check('stuck while "online": the player notices that too', /reconnect|problem/i.test(await p.locator('.netnote').innerText()), await p.locator('.netnote').innerText());
  await p.unroute('**/api/dash/**'); await p.unroute('**/api/stream/**');
  const back2 = await p.waitForFunction(() => { const v = document.querySelector('video'); return !!v && v.currentTime > 302 && !v.paused && v.dataset.mark === 'long'; }, null, { timeout: 60000 }).then(() => true).catch(() => false);
  check('...and resumes once requests get through again', back2, JSON.stringify(await p.evaluate(() => { const v = document.querySelector('video'); return v ? { t: v.currentTime, paused: v.paused } : null; })));
  await p.goto(BASE + '/'); await p.waitForSelector('aside.side');

  // ---- Watch time: totals and days from real playback, from a start date you control
  const statsNow = async () => (await (await ctx.request.get(BASE + '/api/stats/watch?tz=UTC')).json());
  const statsBefore = await statsNow();
  await beat('e2eSTAT0001', 600, 1200, [[0, 600]]);                      // exactly ten more minutes, today
  const statsAfter = await statsNow();
  check('stats: ten minutes of playback adds exactly ten minutes (no double counting)', statsAfter.total_seconds - statsBefore.total_seconds === 600, JSON.stringify([statsBefore.total_seconds, statsAfter.total_seconds]));
  await p.goto(BASE + '/stats'); await p.waitForSelector('.hero .big');
  const shown = await p.locator('.hero .big').innerText();
  const spentTxt = (sec) => { sec = Math.round(sec); const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60; return h ? `${h}h ${String(m).padStart(2, '0')}m` : m ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`; };
  const apiTz = await (await ctx.request.get(BASE + '/api/stats/watch?tz=' + encodeURIComponent(await p.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)))).json();
  check('stats: the page total is the API total, formatted', shown === spentTxt(apiTz.total_seconds), shown + ' vs ' + spentTxt(apiTz.total_seconds));
  check('stats: today is listed as "Today" with the time, and the days add up to the total',
    (await p.locator('.days tbody tr:first-child th').innerText()) === 'Today' &&
    (await p.locator('.days tfoot .num').innerText()) === shown && (await p.locator('aside.side a.active:has-text("Watch time")').count()) === 1);
  const todayIso = await p.locator('input[aria-label="Counting from"]').getAttribute('max');
  await p.fill('input[aria-label="Counting from"]', todayIso);
  await p.click('.since .btn:has-text("Use this date")');
  await p.waitForSelector('.since .btn:has-text("Back to automatic")');
  check('stats: choosing a start date is remembered and shown as set by you', (await p.locator('.since .small').innerText()).startsWith('Set by you') && (await p.locator('.hero .muted').innerText()).includes('since'));
  await p.reload(); await p.waitForSelector('.since');
  check('stats: ...and survives a reload', (await p.locator('input[aria-label="Counting from"]').inputValue()) === todayIso && (await p.locator('.since .btn:has-text("Back to automatic")').count()) === 1);
  await p.click('.since .btn:has-text("Back to automatic")');
  await p.waitForFunction(() => /^Automatic/.test(document.querySelector('.since .small')?.innerText ?? ''));
  check('stats: "Back to automatic" counts from your first watch again', (await p.locator('.hero .big').innerText()) === shown);

  // ---- History: what you watched, searchable, removable, and it stays removed
  await p.goto(BASE + '/history'); await p.waitForSelector('.grid .card');
  const hBefore = await p.locator('.grid .card').count();
  check('History lists what you watched, newest under a Today heading', hBefore >= 1 && (await p.locator('h2').first().innerText()) === 'Today' &&
    (await p.locator('aside.side a.active:has-text("History")').count()) === 1);
  await p.fill('input[aria-label="Search your history"]', 'zzz-nothing-matches'); await p.click('form.find button');
  await p.waitForFunction(() => /matches/.test(document.body.innerText));
  check('searching narrows it (and says so when nothing matches)', (await p.locator('.grid .card').count()) === 0);
  await p.fill('input[aria-label="Search your history"]', ''); await p.click('form.find button');
  await p.waitForSelector('.grid .card');
  const removedTitle = await p.locator('.grid .card .title').first().innerText();
  await p.click('.card .act:has-text("Remove from history")');
  await p.waitForFunction((n) => document.querySelectorAll('.grid .card').length === n, hBefore - 1);
  await p.reload(); await p.waitForSelector('.page h1');
  await p.waitForTimeout(500);
  const titlesAfter = await p.locator('.grid .card .title').allInnerTexts();
  check('removing takes it off the history, and it stays off after a reload', titlesAfter.length === hBefore - 1, removedTitle);

  // ---- For you: recommendations, with a visible reason for each and a way to say no
  await ctx.request.get(BASE + '/__seed_recommendable');
  await p.goto(BASE + '/'); await p.waitForSelector('.grid .card');
  // The two seeded uploads are NEW uploads from a subscription, so they sit on the shelf at the top; the ranked list
  // below holds the rest (here the related videos), each with its reasons.
  const card = (title) => p.locator('.grid .card', { has: p.locator(`a.title[title="${title}"]`) });
  const gridTitles = async () => p.locator('.grid .card a.title').evaluateAll((as) => as.map((a) => a.title));
  const shelfTitles = async () => p.locator('.shelf .card a.title').evaluateAll((as) => as.map((a) => a.title));
  const shelf = await shelfTitles(), ranked = await gridTitles();
  check('Home is the first tab; new uploads from subscriptions are on a shelf, recommendations below',
    (await p.locator('nav.tabs a.active').innerText()) === 'Home' && shelf.includes('Recommended 1') && shelf.includes('Recommended 2') &&
    ranked.includes('Related two') && !ranked.includes('Recommended 1'), JSON.stringify({ shelf, ranked }));
  await card('Related two').locator('.act:has-text("Why this?")').click();
  const why = await p.locator('.why li').allInnerTexts();
  check('"Why this?" lists each reason with its points, then the total', why.length >= 3 && why.some(t => /Related to videos you have watched/.test(t)) &&
    why.slice(0, -1).every(t => /[+-]\d+\.\d\d/.test(t)) && why.at(-1).startsWith('Score'), JSON.stringify(why));
  await card('Related two').locator('.act:has-text("Not interested")').click();
  await p.waitForFunction(() => ![...document.querySelectorAll('.grid .card a.title')].some((a) => a.title === 'Related two'));
  await p.waitForTimeout(6000);                                  // tracker batches for 5 s
  const recEvts = await (await ctx.request.get(BASE + '/__events')).json();
  check('Not interested removes the card and is logged for the recommender', recEvts.some(e => e.type === 'not_interested' && e.video_id === 'e2eREL00002'));
  await p.reload(); await p.waitForSelector('.shelf .card');
  check('...and the video stays gone after a reload', !(await gridTitles()).includes('Related two'), JSON.stringify(await gridTitles()));
  await p.screenshot({ path: require('os').tmpdir() + '/newtube-foryou.png' });
  check('the Home page also has a shelf of new uploads from your subscriptions', (await p.locator('section[aria-label="New from your subscriptions"]').count()) === 1);
  await p.goto(BASE + '/subscriptions'); await p.waitForSelector('nav.tabs');
  check('Subscriptions is its own tab and page', (await p.locator('nav.tabs a.active').innerText()) === 'Subscriptions' && (await p.locator('aside.side .row.active').count()) === 1);
  await p.goto(BASE + '/for-you'); await p.waitForSelector('nav.tabs');
  check('the old /for-you address lands on Home', new URL(p.url()).pathname === '/' && (await p.locator('nav.tabs a.active').innerText()) === 'Home');
  await p.goto(BASE + '/settings'); await p.waitForSelector('select[aria-label="Open newtube on"]');
  await p.selectOption('select[aria-label="Open newtube on"]', 'subscriptions');
  await p.goto(BASE + '/'); await p.waitForURL(/\/subscriptions$/);
  check('"Open newtube on: Subscriptions" starts there', new URL(p.url()).pathname === '/subscriptions');
  await p.click('nav.tabs a:has-text("Home")'); await p.waitForSelector('h2:has-text("Recommended for you")');
  check('...but Home is still one click away and is not redirected away from', new URL(p.url()).pathname === '/');
  await p.goto(BASE + '/settings'); await p.selectOption('select[aria-label="Open newtube on"]', 'home');

  // hide / show the sidebar, and `/` brings it back with the search box focused
  await p.click('aside.side .icon');
  check('the sidebar can be hidden', (await p.locator('aside.side').count()) === 0 && (await p.locator('.reopen').count()) === 1);
  await p.keyboard.press('/');
  await p.waitForSelector('aside.side');
  check('/ shows the sidebar again with the search box focused', await V(p, () => document.activeElement?.getAttribute('aria-label') === 'Search'));
  await p.keyboard.press('Escape');

  // ---- On a phone (390x844, touch): menu drawer, no sideways scrolling, tap-friendly player, install files
  const mctx = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true });
  const m = await mctx.newPage();
  m.on('request', r => { if (!r.url().startsWith(BASE)) problems.push('EXTERNAL (phone): ' + r.url()); });
  m.on('pageerror', e => problems.push('pageerror (phone): ' + e.message));
  await m.goto(BASE + '/'); await m.fill('input[type=password]', 'e2e-password'); await m.click('button.primary');
  await m.waitForSelector('.reopen');
  const overflow = () => m.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  check('phone: the menu starts closed and the page does not scroll sideways', (await m.locator('aside.side').count()) === 0 && (await overflow()) <= 0, String(await overflow()));
  await m.tap('.reopen'); await m.waitForSelector('aside.side');
  const drawer = await m.evaluate(() => ({ pos: getComputedStyle(document.querySelector('aside.side')).position, w: document.querySelector('aside.side').getBoundingClientRect().width,
    mainW: document.querySelector('main').getBoundingClientRect().width, vw: innerWidth, backdrop: getComputedStyle(document.querySelector('.backdrop')).display }));
  check('phone: ☰ slides the menu over the page (the page keeps its full width) with a backdrop', drawer.pos === 'fixed' && drawer.w <= 300 && drawer.mainW === drawer.vw && drawer.backdrop === 'block', JSON.stringify(drawer));
  await m.tap('aside.side a:has-text("History")'); await m.waitForURL(/\/history/);
  await m.waitForFunction(() => !document.querySelector('aside.side'));
  check('phone: choosing something closes the menu', (await m.locator('aside.side').count()) === 0);
  await m.tap('.reopen'); await m.waitForSelector('aside.side'); await m.tap('.backdrop', { position: { x: 360, y: 400 } });
  await m.waitForFunction(() => !document.querySelector('aside.side'));
  check('phone: tapping outside the menu closes it', (await m.locator('aside.side').count()) === 0);
  for (const path of ['/', '/subscriptions', '/search?q=zoo&kind=video', '/settings', '/history', '/list/liked']) {
    await m.goto(BASE + path); await m.waitForSelector('.page'); await m.waitForTimeout(500);
    const o = await overflow();
    check(`phone: ${path} fits the screen`, o <= 0, String(o));
  }
  await m.goto(BASE + '/watch?v=e2eVIDEO001&src=subs');
  await m.waitForFunction(() => document.querySelector('video')?.currentTime > 1, null, { timeout: 20000 });
  const mw = await m.evaluate(() => ({ player: document.querySelector('.player').getBoundingClientRect().width, vw: innerWidth,
    relTop: document.querySelector('.related').getBoundingClientRect().top, stageBottom: document.querySelector('.stage').getBoundingClientRect().bottom }));
  check('phone: the video fills the width and Up next is below it', mw.player === mw.vw && mw.relTop >= mw.stageBottom - 1 && (await overflow()) <= 0, JSON.stringify(mw));
  await m.waitForSelector('.player.idle', { timeout: 8000 });                   // controls hide themselves while playing
  await m.tap('.player video'); await m.waitForTimeout(200);
  const afterTap = await m.evaluate(() => ({ idle: document.querySelector('.player').classList.contains('idle'), paused: document.querySelector('video').paused }));
  check('phone: the first tap on a playing video shows the controls instead of pausing it', !afterTap.idle && !afterTap.paused, JSON.stringify(afterTap));
  await m.tap('.player video'); await m.waitForTimeout(200);
  check('phone: the next tap pauses', await m.evaluate(() => document.querySelector('video').paused));
  const targets = await m.evaluate(() => [...document.querySelectorAll('.player .row button')].filter((x) => x.offsetParent).map((x) => Math.round(x.getBoundingClientRect().height)));
  check('phone: player buttons are big enough to tap (at least 40px tall)', targets.length > 4 && targets.every((h) => h >= 40), JSON.stringify(targets));
  await m.tap('.reopen'); await m.waitForSelector('aside.side'); await m.tap('aside.side a:has-text("Settings")');
  await m.waitForSelector('.watchhost.mini');
  const mm = await m.evaluate(() => { const r = document.querySelector('.watchhost').getBoundingClientRect(); return { w: r.width, right: r.right, bottom: r.bottom, vw: innerWidth, vh: innerHeight,
    bar: getComputedStyle(document.querySelector('.minibar')).opacity }; });
  check('phone: the mini-player fits in the corner and its buttons are always visible', mm.w <= 320 && mm.right <= mm.vw && mm.bottom <= mm.vh && mm.bar === '1', JSON.stringify(mm));
  const unauth = await require('playwright').request.newContext({ baseURL: BASE });
  const man = await unauth.get('/manifest.webmanifest'), icon = await unauth.get('/icon-192.png');
  check('install: the manifest and icons load without logging in', man.status() === 200 && (await man.json()).display === 'standalone' && icon.status() === 200 && (await icon.body())[1] === 0x50);
  check('install: the page links its manifest', (await m.locator('link[rel="manifest"]').count()) === 1 && (await m.locator('meta[name="theme-color"]').count()) === 1);
  await unauth.dispose(); await mctx.close();

  const hits = await (await ctx.request.get(BASE + '/__hits')).json();
  const gv = hits.filter(h => /^\/(v360|v180|aud|mux)$/.test(h[0]));
  check('video bytes only via Range requests', gv.length > 0 && gv.every(h => h[1] && h[1].startsWith('bytes=')), gv.length + ' requests');
  check('no external requests / console errors', problems.length === 0, JSON.stringify(problems));
  await b.close();
  console.log(failed ? `\n${failed} FAILED` : '\nALL PASSED');
  process.exit(failed ? 1 : 0);
})();
