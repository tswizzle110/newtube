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
  p.on('console', m => { if (m.type() === 'error' && !/status of (404|409)/.test(m.text())) problems.push('console: ' + m.text()); });
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
  check('Playback section on the settings page', (await p.locator('h2:has-text("Playback")').count()) === 1 && (await p.locator('select').first().inputValue()) === 'auto');
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
  check('...and takes you to the subscriptions feed', new URL(p.url()).pathname === '/');
  check('the channel list shows an avatar (served by our proxy)', await V(p, () => { const i = document.querySelector('ul.subs img'); return !!i && i.src.includes('/api/avatar/'); }));
  await p.click('aside.side .chev'); await p.waitForTimeout(150);
  check('the arrow collapses the list without leaving the page', (await p.locator('ul.subs').count()) === 0 && new URL(p.url()).pathname === '/');
  await p.click('aside.side .chev'); await p.waitForSelector('ul.subs li a');
  await p.goto(BASE + '/'); await p.waitForSelector('aside.side');
  check('the open/closed state is remembered', (await p.locator('ul.subs').count()) === 1);
  await p.click('ul.subs li a');                                       // click the channel
  await p.waitForSelector('.head');
  check('clicking a channel opens its page, highlighted in the list', p.url().includes('/channel/UC') && (await p.locator('ul.subs a.active').count()) === 1);
  const titles = async () => (await p.locator('.grid .card .title').allInnerTexts()).join(',');
  check('default order is newest first', (await titles()) === 'Upload A,Upload B,Upload C' && (await p.locator('.sorts a.on').innerText()) === 'Newest');
  await p.click('.sorts a:has-text("Popular")'); await p.waitForSelector('.sorts a.on:has-text("Popular")');
  await p.waitForFunction(() => document.querySelector('.grid .card .title')?.innerText === 'Upload B');
  check('Popular ranks by view count (5000, 300, 100) and the URL records it', (await titles()) === 'Upload B,Upload C,Upload A' && p.url().includes('sort=popular'));
  await p.click('.sorts a:has-text("Oldest")');
  await p.waitForFunction(() => document.querySelector('.grid .card .title')?.innerText === 'Upload C' && document.querySelectorAll('.grid .card .title')[1]?.innerText === 'Upload B');
  check('Oldest re-orders them again', (await titles()) === 'Upload C,Upload B,Upload A' && p.url().includes('sort=oldest'));
  await p.reload(); await p.waitForSelector('.sorts a.on:has-text("Oldest")');
  check('a reload keeps the chosen sort', (await titles()) === 'Upload C,Upload B,Upload A');
  await p.click('.sorts a:has-text("Newest")');
  await p.waitForFunction(() => document.querySelector('.grid .card .title')?.innerText === 'Upload A');
  await p.click('.head button');                                       // unsubscribe from the channel page
  await p.waitForFunction(() => document.querySelector('.head button')?.innerText.includes('Subscribe') && !document.querySelector('.head button').innerText.includes('✓'));
  check('unsubscribing removes the channel from the sidebar straight away', (await p.locator('ul.subs li a').count()) === 0);
  await p.click('.head button'); await p.waitForFunction(() => document.querySelectorAll('ul.subs li a').length === 1);
  check('subscribing adds it back', (await p.locator('ul.subs li a .name').innerText()) === 'E2E Channel');
  // hide / show the sidebar, and `/` brings it back with the search box focused
  await p.click('aside.side .icon');
  check('the sidebar can be hidden', (await p.locator('aside.side').count()) === 0 && (await p.locator('.reopen').count()) === 1);
  await p.keyboard.press('/');
  await p.waitForSelector('aside.side');
  check('/ shows the sidebar again with the search box focused', await V(p, () => document.activeElement?.getAttribute('aria-label') === 'Search'));
  await p.keyboard.press('Escape');

  const hits = await (await ctx.request.get(BASE + '/__hits')).json();
  const gv = hits.filter(h => /^\/(v360|v180|aud|mux)$/.test(h[0]));
  check('video bytes only via Range requests', gv.length > 0 && gv.every(h => h[1] && h[1].startsWith('bytes=')), gv.length + ' requests');
  check('no external requests / console errors', problems.length === 0, JSON.stringify(problems));
  await b.close();
  console.log(failed ? `\n${failed} FAILED` : '\nALL PASSED');
  process.exit(failed ? 1 : 0);
})();
