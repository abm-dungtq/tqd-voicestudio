---
title: TQD-Voice v0.1 plan
date: 2026-11-25
---

# TQD-Voice v0.1 plan

Lập kế hoạch TQD-Voice v0.1 (plans/261125-1606-tqd-voice).
- Giữ model gốc 1860k cho production; fine-tune 10k không phát hành (clone kém hơn, điều khoản dataset chưa xác minh).
- Bằng chứng chính: model gốc lỗi 50% với câu 1-4 từ, 2,8% với câu 13-25 từ -> chunker gom 13-25 từ, có A/B văn bản dài với luật quyết định cố định.
- Người dùng chốt: dự án miễn phí cho bạn bè qua GitHub, fork iamdinhthuan/ViZipvoice thành abm-dungtq/tqd-voice, văn bản dưới 3.000 từ, chỉ MCP stdio cục bộ, máy chính là Windows/Linux + NVIDIA, phát hành theo tag.
- Bỏ webhook và job nền khỏi v1. Cài bằng uv tool install --torch-backend=auto; doctor phát hiện bản torch CPU trên máy NVIDIA.
- Đã sửa theo ý kiến phản biện: mkdir trước git mv, autocast trong zipvoice/vizipvoice.py, eval dùng _canon + resample 16k, kiểm tra wheel có zipvoice/models.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
