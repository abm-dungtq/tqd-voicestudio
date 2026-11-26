---
title: TQD-Voice v0.1 cook
date: 2026-11-26
---

# TQD-Voice v0.1 cook

Phases 1-6 (except fork/push/tag/release) done on tqd/v0.1 in worktree D:\TTS\tqd-voice; production 8001 on tqd/baseline untouched, pinned TQD_CHUNKER=legacy.
Chunker A/B: v2 bad_char_share 0.017->0, char_cer 0.0525->0.0512, misses 0.005 rule -> KEEP_LEGACY.
Clean-install bugs found only by real uv tool install: hf-hub pin conflict with soe-vinorm (transformers held at 4.x), hub 0.36 symlink probe race on Windows (max_workers=1), tensorboard/piper module-level imports, MCP client Job Object killing daemon (WMI spawn), install.ps1 exit closing iex shell.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
