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
