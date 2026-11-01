# End-to-end playback test

Runs the **real** app and a **real** Chromium against locally generated video, with YouTube
faked (the extractor and a stand-in "googlevideo" that serves local files with Range support).
It checks DASH playback through our manifest + Range proxy, seeking, chapters, keyboard
shortcuts, quality switching, captions, resume position, the muxed fallback, and that the page
never contacts any host except this server.

It cannot test YouTube itself; use `newtube selftest` for that.

```bash
e2e/make_media.sh                                   # needs ffmpeg
(cd frontend && npm run build)
.venv/bin/python e2e/server.py e2e/media 18788 &     # fake-YouTube app on :18788
npm i --no-save playwright && npx playwright install chromium   # one-time; downloads a browser
node e2e/playback.cjs                               # prints PASS/FAIL lines
```

Playwright's bundled Chromium plays H.264/AAC. Stop the server afterwards (`kill %1`).
