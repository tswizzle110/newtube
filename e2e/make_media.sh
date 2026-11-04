#!/usr/bin/env bash
# Generates tiny fragmented-MP4 test streams (H.264 + AAC, with a sidx index) like YouTube's DASH files.
set -euo pipefail
out="${1:-e2e/media}"; mkdir -p "$out"; cd "$out"
v() { ffmpeg -loglevel error -y -f lavfi -i "testsrc2=size=$1:rate=25:duration=24" -c:v libx264 -profile:v main -level 3.0 \
  -pix_fmt yuv420p -g 25 -keyint_min 25 -sc_threshold 0 \
  -movflags +frag_keyframe+empty_moov+default_base_moof+global_sidx -f mp4 "$2"; }
v 640x360 v360.mp4; v 320x180 v180.mp4
ffmpeg -loglevel error -y -f lavfi -i "sine=frequency=440:duration=24" -c:a aac -b:a 64k \
  -movflags +frag_keyframe+empty_moov+default_base_moof+global_sidx -f mp4 a.m4a
ffmpeg -loglevel error -y -f lavfi -i "testsrc2=size=640x360:rate=25:duration=24" -f lavfi -i "sine=frequency=330:duration=24" \
  -c:v libx264 -profile:v main -level 3.0 -pix_fmt yuv420p -c:a aac -movflags +faststart -shortest muxed.mp4
printf 'WEBVTT\n\n00:00:00.000 --> 00:00:10.000\nHello from the e2e captions\n\n00:00:10.000 --> 00:00:20.000\nSecond cue\n' > en.vtt
# A 6-minute stream: too long to be buffered in one go, so cutting the network really stalls it (offline tests)
ffmpeg -loglevel error -y -f lavfi -i "testsrc2=size=640x360:rate=25:duration=360" -c:v libx264 -profile:v main -level 3.0 -pix_fmt yuv420p \
  -b:v 150k -g 25 -keyint_min 25 -sc_threshold 0 -movflags +frag_keyframe+empty_moov+default_base_moof+global_sidx -f mp4 long360.mp4
ffmpeg -loglevel error -y -f lavfi -i "sine=frequency=550:duration=360" -c:a aac -b:a 48k \
  -movflags +frag_keyframe+empty_moov+default_base_moof+global_sidx -f mp4 longa.m4a
# VP9 in WebM with the Cues up front, like YouTube's DASH files (stand-ins for the 1440p/2160p streams)
vp9() { ffmpeg -loglevel error -y -f lavfi -i "testsrc2=size=$1:rate=25:duration=24" -c:v libvpx-vp9 -deadline realtime -cpu-used 8 \
  -g 25 -b:v 300k -pix_fmt yuv420p -f webm -dash 1 -cues_to_front 1 "$2"; }
vp9 640x360 vp9a.webm; vp9 320x180 vp9b.webm
