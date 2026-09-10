# stt — Apple SpeechAnalyzer CLI

Transcription backend for M5. macOS 26+.

    swiftc -O -o stt main.swift
    ./stt lecture.wav out.json

Emits `[{start, end, text}]` with seconds-resolution timestamps from
`SpeechTranscriber.Result.range`. The speech model self-installs on first run
via `AssetInventory`; there is no model file to manage.

Verified: 2694.32 s of audio in 41.19 s (65.4x realtime), 683 segments.
