(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const SAMPLE_TEXT = 'Xin chào, đây là giọng đọc mẫu của TQD Voice Studio.';
  const state = {
    key: localStorage.getItem('tqd.apiKey') || '',
    voices: [],
    libraryVoices: [],
    music: [],
    roles: [],
    lexicon: {},
    historyOffset: 0,
    historyLimit: 10,
    historyLastCount: 0,
    resultBlob: null,
    resultType: 'audio/wav',
    resultExt: 'wav',
    resultSrt: '',
    resultVtt: '',
    lastHistoryId: '',
    jobsTimer: null,
    cacheHits: new Set(JSON.parse(localStorage.getItem('tqd.cacheHits') || '[]')),
  };

  class ApiError extends Error {
    constructor(message, status = 0, body = null) {
      super(message);
      this.status = status;
      this.body = body;
    }
  }

  function showBanner(text, isError = false) {
    const b = $('banner');
    b.textContent = text;
    b.classList.toggle('error', isError);
    b.classList.remove('hidden');
  }

  function hideBanner() {
    $('banner').classList.add('hidden');
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function errorDetail(data, fallback) {
    if (data && typeof data === 'object' && 'detail' in data) {
      return typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail, null, 2);
    }
    if (typeof data === 'string' && data) return data;
    return fallback;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (state.key) headers.set('Authorization', `Bearer ${state.key}`);
    let body = options.body;
    if (Object.prototype.hasOwnProperty.call(options, 'json')) {
      headers.set('Content-Type', 'application/json');
      body = JSON.stringify(options.json);
    }
    let res;
    try {
      res = await fetch(path, { method: options.method || 'GET', headers, body });
    } catch (err) {
      showBanner('Không kết nối được server. Kiểm tra server đang chạy và mạng cục bộ.', true);
      throw new ApiError('Không kết nối được server', 0, err);
    }
    if (res.status === 401) {
      showKeyPanel(true);
      throw new ApiError('API key không đúng hoặc còn thiếu. Nhập key để tiếp tục.', 401);
    }
    if (res.status === 503) {
      showBanner('Server đang nạp model, vui lòng chờ. Giao diện sẽ tự thử lại.');
      throw new ApiError('server đang nạp model', 503);
    }
    if (!res.ok) {
      let data = null;
      const ct = res.headers.get('content-type') || '';
      try { data = ct.includes('json') ? await res.json() : await res.text(); } catch (_) { /* ignore */ }
      const detail = errorDetail(data, `Lỗi server (${res.status})`);
      if (res.status >= 500) showBanner(`Server báo lỗi ${res.status}: ${detail}`, true);
      throw new ApiError(detail, res.status, data);
    }
    if (options.blob) return { blob: await res.blob(), res };
    const ct = res.headers.get('content-type') || '';
    const data = ct.includes('json') ? await res.json() : await res.text();
    return { data, res };
  }

  function persistCacheHits() {
    localStorage.setItem('tqd.cacheHits', JSON.stringify([...state.cacheHits].slice(-300)));
  }

  function noteCacheHeader(res) {
    const hid = res.headers.get('x-tqd-history-id');
    if (hid) state.lastHistoryId = hid;
    if (res.headers.get('x-tqd-cache') === 'hit' && hid) {
      state.cacheHits.add(hid);
      persistCacheHits();
    }
  }

  function showKeyPanel(show) {
    $('keyPanel').classList.toggle('hidden', !show);
    if (show) $('apiKeyInput').value = state.key;
  }

  function setServerState(text, cls = '') {
    const el = $('serverState');
    el.textContent = text;
    el.className = `pill ${cls}`;
  }

  function wordCount(text) {
    return (text.trim().match(/\S+/g) || []).length;
  }

  function updateCounts() {
    const text = $('inputText').value;
    $('wordCount').textContent = `${wordCount(text)} từ`;
    $('charCount').textContent = `${text.length} ký tự`;
  }

  function voiceLabel(v) {
    const bits = [];
    if (v.gender) bits.push(v.gender === 'female' ? 'Nữ' : 'Nam');
    if (v.region) bits.push(v.region);
    if (v.kind) bits.push(v.kind === 'preset' ? 'có sẵn' : 'clone');
    return `${v.name}${bits.length ? ` — ${bits.join(' · ')}` : ''}`;
  }

  function fillVoiceSelect(select, voices, keepValue = true) {
    const old = keepValue ? select.value : '';
    select.innerHTML = '';
    for (const v of voices) {
      const opt = document.createElement('option');
      opt.value = v.name;
      opt.textContent = voiceLabel(v);
      select.appendChild(opt);
    }
    if (!voices.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'Không có giọng phù hợp';
      select.appendChild(opt);
    }
    if (old && voices.some((v) => v.name === old)) select.value = old;
  }

  function voiceQuery(genderEl, regionEl, kindEl, qEl = null) {
    const qs = new URLSearchParams();
    if (genderEl.value) qs.set('gender', genderEl.value);
    if (regionEl.value) qs.set('region', regionEl.value);
    if (kindEl.value) qs.set('kind', kindEl.value);
    if (qEl && qEl.value.trim()) qs.set('q', qEl.value.trim());
    const s = qs.toString();
    return s ? `?${s}` : '';
  }

  async function loadVoices() {
    const q = voiceQuery($('filterGender'), $('filterRegion'), $('filterKind'));
    const { data } = await api(`/v1/voices${q}`);
    state.voices = data.data || [];
    fillVoiceSelect($('voiceSelect'), state.voices);
    renderRoles();
  }

  async function loadLibrary() {
    const q = voiceQuery($('libraryGender'), $('libraryRegion'), $('libraryKind'), $('librarySearch'));
    const { data } = await api(`/v1/voices${q}`);
    state.libraryVoices = data.data || [];
    renderLibrary();
  }

  async function playSample(voice, audioEl) {
    audioEl.classList.remove('hidden');
    audioEl.removeAttribute('src');
    const { blob, res } = await api('/v1/audio/speech', {
      method: 'POST',
      blob: true,
      json: {
        input: SAMPLE_TEXT,
        voice,
        response_format: 'wav',
        sample_rate: 24000,
        speed: 1,
        style: 'auto',
      },
    });
    noteCacheHeader(res);
    audioEl.src = URL.createObjectURL(blob);
    audioEl.play().catch(() => {});
  }

  function renderLibrary() {
    const tbody = $('libraryBody');
    tbody.innerHTML = '';
    for (const v of state.libraryVoices) {
      const tr = document.createElement('tr');
      const kind = v.kind === 'preset' ? 'Có sẵn' : 'Tự clone';
      tr.innerHTML = `<td><strong></strong></td><td></td><td></td><td>${kind}</td><td></td><td class="actions"></td>`;
      tr.children[0].firstChild.textContent = v.name;
      tr.children[1].textContent = v.gender === 'female' ? 'Nữ' : (v.gender === 'male' ? 'Nam' : '—');
      tr.children[2].textContent = v.region || '—';
      tr.children[4].textContent = v.description || '';
      const actions = tr.children[5];
      const play = document.createElement('button');
      play.type = 'button'; play.className = 'ghost'; play.textContent = 'Nghe mẫu';
      play.addEventListener('click', () => playSample(v.name, $('libraryAudio')).catch((e) => showBanner(e.message, true)));
      actions.appendChild(play);
      if (v.kind === 'custom') {
        const del = document.createElement('button');
        del.type = 'button'; del.className = 'ghost danger'; del.textContent = 'Xóa';
        del.addEventListener('click', async () => {
          if (!confirm(`Xóa giọng ${v.name}?`)) return;
          try {
            await api(`/v1/voices/${encodeURIComponent(v.name)}`, { method: 'DELETE' });
            await Promise.all([loadVoices(), loadLibrary()]);
          } catch (e) { showBanner(e.message, true); }
        });
        actions.appendChild(del);
      }
      tbody.appendChild(tr);
    }
    if (!state.libraryVoices.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="muted">Không có giọng phù hợp.</td></tr>';
    }
  }

  function updateSliderLabels() {
    $('speedValue').textContent = Number($('speedInput').value).toFixed(2);
    $('musicVolumeValue').textContent = $('musicVolume').value;
    $('musicDuckValue').textContent = $('musicDuck').value;
    $('musicFadeValue').textContent = Number($('musicFade').value).toFixed(1);
  }

  function renderRoles() {
    const box = $('rolesList');
    box.innerHTML = '';
    if (!$('rolesToggle').checked) return;
    for (const role of state.roles) {
      const row = document.createElement('div');
      row.className = 'role-row';
      const label = document.createElement('strong');
      label.textContent = role;
      const select = document.createElement('select');
      select.dataset.role = role;
      fillVoiceSelect(select, state.voices, false);
      row.append(label, select);
      box.appendChild(row);
    }
  }

  async function analyzeRoles() {
    const input = $('inputText').value;
    const { data } = await api('/v1/script/roles', { method: 'POST', json: { input } });
    state.roles = data.roles || [];
    renderRoles();
    if (!state.roles.length) $('rolesList').innerHTML = '<div class="muted">Không tìm thấy vai nào (cần dạng “Vai: lời thoại”).</div>';
  }

  function collectCast() {
    if (!$('rolesToggle').checked) return null;
    const cast = {};
    document.querySelectorAll('#rolesList select[data-role]').forEach((sel) => {
      if (sel.value) cast[sel.dataset.role] = sel.value;
    });
    return Object.keys(cast).length ? cast : null;
  }

  function buildPayload() {
    const payload = {
      input: $('inputText').value,
      voice: $('voiceSelect').value,
      response_format: $('formatSelect').value,
      sample_rate: 24000,
      speed: Number($('speedInput').value),
      style: 'auto',
      normalize: $('normalizeInput').checked,
      trim_silence: $('trimInput').checked,
      cache: !$('cacheInput').checked,
      quality_check: $('qualityInput').checked,
    };
    const cast = collectCast();
    if (cast) payload.cast = cast;
    if ($('musicEnabled').checked && $('musicSelect').value) {
      payload.music = {
        id: $('musicSelect').value,
        volume_db: Number($('musicVolume').value),
        duck_db: Number($('musicDuck').value),
        fade_s: Number($('musicFade').value),
      };
    }
    return payload;
  }

  function blobFromBase64(b64, type) {
    const raw = atob(b64);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
    return new Blob([bytes], { type });
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function updateQcInfo(res) {
    const cer = res.headers.get('x-qc-max-cer');
    const retries = res.headers.get('x-qc-retries');
    $('qcInfo').textContent = cer !== null
      ? `QC: CER cao nhất ${cer}, số lần đọc lại ${retries}`
      : ($('qualityInput').checked ? 'QC: server không trả header X-QC-* (ASR có thể chưa bật).' : '');
  }

  function showResult(blob, type, ext, res, timed = null) {
    state.resultBlob = blob;
    state.resultType = type;
    state.resultExt = ext;
    state.resultSrt = timed && timed.srt ? timed.srt : '';
    state.resultVtt = timed && timed.vtt ? timed.vtt : '';
    noteCacheHeader(res);
    updateQcInfo(res);
    const audio = $('resultAudio');
    audio.src = URL.createObjectURL(blob);
    $('resultPanel').classList.remove('hidden');
    $('downloadSrtBtn').classList.toggle('hidden', !state.resultSrt);
    $('downloadVttBtn').classList.toggle('hidden', !state.resultVtt);
    $('ratingStatus').textContent = '';
  }

  function switchTab(name) {
    document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
    document.querySelectorAll('.tab-panel').forEach((p) => p.classList.toggle('active', p.id === `tab-${name}`));
    if (name === 'jobs') refreshJobs().catch((e) => showBanner(e.message, true));
    if (name === 'history') refreshHistory().catch((e) => showBanner(e.message, true));
    if (name === 'library') loadLibrary().catch((e) => showBanner(e.message, true));
    if (name === 'settings') loadSettings().catch((e) => showBanner(e.message, true));
  }

  async function submitSpeech() {
    const text = $('inputText').value.trim();
    if (!text) {
      $('speakError').textContent = 'Hãy nhập văn bản trước.';
      return;
    }
    $('speakError').textContent = '';
    const payload = buildPayload();
    const asJob = $('backgroundJobInput').checked || wordCount(text) > 300;
    const started = performance.now();
    $('speakSpinner').classList.remove('hidden');
    $('speakBtn').disabled = true;
    const timer = setInterval(() => {
      $('elapsed').textContent = `${((performance.now() - started) / 1000).toFixed(1)}s`;
    }, 100);
    try {
      if (asJob) {
        const body = { ...payload, subtitles: $('subtitlesInput').checked };
        const hook = $('webhookUrl').value.trim();
        if (hook) body.webhook_url = hook;
        const { data } = await api('/v1/jobs', { method: 'POST', json: body });
        switchTab('jobs');
        showBanner(`Đã gửi job ${data.id}. Theo dõi tiến độ trong tab Job.`);
      } else if ($('subtitlesInput').checked) {
        const { data, res } = await api('/v1/audio/speech/timed', {
          method: 'POST',
          json: { ...payload, subtitle_format: 'both' },
        });
        const type = data.media_type || 'audio/wav';
        showResult(blobFromBase64(data.audio_base64, type), type, payload.response_format, res, data);
      } else {
        const { blob, res } = await api('/v1/audio/speech', { method: 'POST', json: payload, blob: true });
        showResult(blob, blob.type || 'audio/wav', payload.response_format, res);
      }
    } catch (e) {
      $('speakError').textContent = e.message;
    } finally {
      clearInterval(timer);
      $('speakSpinner').classList.add('hidden');
      $('speakBtn').disabled = false;
      $('elapsed').textContent = `xong sau ${((performance.now() - started) / 1000).toFixed(1)}s`;
    }
  }

  async function importFile() {
    const file = $('importFile').files[0];
    if (!file) {
      $('importStatus').textContent = 'Chọn file .txt, .md, .docx hoặc .pdf trước.';
      return;
    }
    const fd = new FormData();
    fd.append('file', file, file.name);
    try {
      const { data } = await api('/v1/import', { method: 'POST', body: fd });
      $('inputText').value = data.text;
      updateCounts();
      $('importStatus').textContent = `${data.title}: đã nhập ${data.words} từ từ ${data.source}`;
    } catch (e) {
      $('importStatus').textContent = e.message;
    }
  }

  async function importUrl() {
    const url = $('importUrl').value.trim();
    if (!url) {
      $('importStatus').textContent = 'Nhập URL trước.';
      return;
    }
    try {
      const { data } = await api('/v1/import', { method: 'POST', json: { url } });
      $('inputText').value = data.text;
      updateCounts();
      $('importStatus').textContent = `${data.title}: đã nhập ${data.words} từ từ ${data.source}`;
    } catch (e) {
      $('importStatus').textContent = e.message;
    }
  }

  async function submitRating(rating, ids = {}, statusEl = null) {
    try {
      const note = $('ratingNote') ? $('ratingNote').value.trim() : '';
      await api('/v1/ratings', { method: 'POST', json: { ...ids, rating, note: note || undefined } });
      if (statusEl) statusEl.textContent = 'Đã gửi đánh giá.';
    } catch (e) {
      if (statusEl) statusEl.textContent = e.message;
      else showBanner(e.message, true);
    }
  }

  function renderProgress(job) {
    return `<div class="progress" aria-label="Tiến độ"><i style="width:${Math.round((job.progress || 0) * 100)}%"></i></div>`;
  }

  async function fetchAudioInto(url, audio) {
    const { blob } = await api(url, { blob: true });
    audio.src = URL.createObjectURL(blob);
    audio.classList.remove('hidden');
    audio.play().catch(() => {});
  }

  function jobStatusBadge(status) {
    const cls = status === 'done' ? 'ok' : (status === 'failed' ? 'err' : 'warn');
    return `<span class="badge ${cls}">${status}</span>`;
  }

  function renderJobs(jobs) {
    const box = $('jobsList');
    box.innerHTML = '';
    if (!jobs.length) {
      box.innerHTML = '<div class="muted">Chưa có job nào.</div>';
      return;
    }
    for (const j of jobs) {
      const item = document.createElement('div');
      item.className = 'item job-item';
      item.dataset.jobId = j.id;
      item.innerHTML = `<div class="item-head"><div><div class="item-title">Job ${j.id}</div><div class="muted">${j.segments_done}/${j.segments_total} đoạn · tiến độ ${Math.round((j.progress || 0) * 100)}%</div></div>${jobStatusBadge(j.status)}</div>${renderProgress(j)}<div class="item-actions"></div><audio controls class="hidden"></audio><div class="muted job-note"></div>`;
      if (j.error) item.querySelector('.job-note').textContent = j.error;
      const actions = item.querySelector('.item-actions');
      if (j.status === 'queued' || j.status === 'running') {
        const cancel = document.createElement('button');
        cancel.type = 'button'; cancel.textContent = 'Hủy'; cancel.dataset.action = 'cancel';
        actions.appendChild(cancel);
      }
      if (j.status === 'done') {
        const play = document.createElement('button');
        play.type = 'button'; play.textContent = 'Phát audio'; play.dataset.action = 'play';
        const down = document.createElement('button');
        down.type = 'button'; down.textContent = 'Tải audio'; down.dataset.action = 'download';
        actions.append(play, down);
        if (j.srt_url) {
          const srt = document.createElement('button');
          srt.type = 'button'; srt.textContent = 'Tải .srt'; srt.dataset.action = 'srt';
          actions.appendChild(srt);
        }
        if (j.vtt_url) {
          const vtt = document.createElement('button');
          vtt.type = 'button'; vtt.textContent = 'Tải .vtt'; vtt.dataset.action = 'vtt';
          actions.appendChild(vtt);
        }
        const up = document.createElement('button');
        up.type = 'button'; up.textContent = '👍'; up.dataset.action = 'rate-up';
        const downvote = document.createElement('button');
        downvote.type = 'button'; downvote.textContent = '👎'; downvote.dataset.action = 'rate-down';
        actions.append(up, downvote);
      }
      box.appendChild(item);
    }
  }

  async function refreshJobs() {
    const { data } = await api('/v1/jobs');
    const jobs = data.data || [];
    renderJobs(jobs);
    const active = jobs.some((j) => j.status === 'queued' || j.status === 'running');
    if (state.jobsTimer) {
      clearInterval(state.jobsTimer);
      state.jobsTimer = null;
    }
    if (active) state.jobsTimer = setInterval(() => refreshJobs().catch(() => {}), 2000);
  }

  function renderHistory(items) {
    const box = $('historyList');
    box.innerHTML = '';
    if (!items.length) {
      box.innerHTML = '<div class="muted">Chưa có lịch sử.</div>';
      return;
    }
    for (const h of items) {
      const item = document.createElement('div');
      item.className = 'item history-item';
      item.dataset.historyId = h.id;
      const badge = state.cacheHits.has(h.id) ? '<span class="badge ok">cache hit</span>' : '';
      item.innerHTML = `<div class="item-head"><div><div class="item-title"></div><div class="muted">${h.voice || ''} · ${h.response_format} · ${h.duration_s || 0}s ${badge}</div></div></div><div class="item-actions"></div><audio controls class="hidden"></audio>`;
      item.querySelector('.item-title').textContent = (h.text || '').slice(0, 140) || h.id;
      const actions = item.querySelector('.item-actions');
      for (const [label, action] of [['Phát', 'play'], ['Tải', 'download'], ['Xóa', 'delete']]) {
        const btn = document.createElement('button');
        btn.type = 'button'; btn.textContent = label; btn.dataset.action = action;
        actions.appendChild(btn);
      }
      box.appendChild(item);
    }
  }

  async function refreshHistory() {
    const qs = `?limit=${state.historyLimit}&offset=${state.historyOffset}`;
    const { data } = await api(`/v1/history${qs}`);
    const items = data.data || [];
    state.historyLastCount = items.length;
    renderHistory(items);
    $('historyPage').textContent = `Trang ${Math.floor(state.historyOffset / state.historyLimit) + 1}`;
    $('historyPrev').disabled = state.historyOffset <= 0;
    $('historyNext').disabled = items.length < state.historyLimit;
  }

  function renderLexicon() {
    const tbody = $('lexiconBody');
    tbody.innerHTML = '';
    for (const [word, pron] of Object.entries(state.lexicon)) {
      addLexiconRow(word, pron, false);
    }
  }

  function addLexiconRow(word = '', pron = '', focus = true) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td><input class="lex-word" type="text"></td><td><input class="lex-pron" type="text"></td><td><button type="button" class="ghost lex-del">Xóa</button></td>';
    tr.querySelector('.lex-word').value = word;
    tr.querySelector('.lex-pron').value = pron;
    tr.querySelector('.lex-del').addEventListener('click', () => tr.remove());
    $('lexiconBody').appendChild(tr);
    if (focus) tr.querySelector('.lex-word').focus();
  }

  async function loadLexicon() {
    const { data } = await api('/v1/lexicon');
    state.lexicon = data.entries || {};
    renderLexicon();
  }

  async function saveLexicon() {
    const entries = {};
    document.querySelectorAll('#lexiconBody tr').forEach((tr) => {
      const w = tr.querySelector('.lex-word').value.trim();
      const p = tr.querySelector('.lex-pron').value.trim();
      if (w) entries[w] = p;
    });
    const { data } = await api('/v1/lexicon', { method: 'PUT', json: { entries } });
    state.lexicon = data.entries || {};
    renderLexicon();
    $('lexiconStatus').textContent = ` Đã lưu ${data.updated} mục.`;
  }

  function renderMusicList() {
    const box = $('musicList');
    box.innerHTML = '';
    for (const m of state.music) {
      const item = document.createElement('div');
      item.className = 'item';
      item.innerHTML = `<div class="item-head"><div><div class="item-title"></div><div class="muted">${m.duration}s · ${m.id}</div></div></div><div class="item-actions"><button type="button">Xóa</button></div>`;
      item.querySelector('.item-title').textContent = m.name;
      item.querySelector('button').addEventListener('click', async () => {
        try { await api(`/v1/music/${encodeURIComponent(m.id)}`, { method: 'DELETE' }); await loadMusic(); }
        catch (e) { showBanner(e.message, true); }
      });
      box.appendChild(item);
    }
    if (!state.music.length) box.innerHTML = '<div class="muted">Chưa có nhạc nền.</div>';
    const sel = $('musicSelect');
    sel.innerHTML = '';
    for (const m of state.music) {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = `${m.name} (${m.duration}s)`;
      sel.appendChild(opt);
    }
    if (!state.music.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'Chưa có nhạc';
      sel.appendChild(opt);
    }
  }

  async function loadMusic() {
    const { data } = await api('/v1/music');
    state.music = data.data || [];
    renderMusicList();
  }

  async function uploadMusic() {
    const file = $('musicUploadFile').files[0];
    if (!file) {
      showBanner('Chọn file nhạc trước.', true);
      return;
    }
    const fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('name', $('musicUploadName').value.trim());
    await api('/v1/music', { method: 'POST', body: fd });
    $('musicUploadFile').value = '';
    $('musicUploadName').value = '';
    await loadMusic();
  }

  async function loadHealth() {
    try {
      const { data } = await api('/health');
      $('serverInfo').textContent = JSON.stringify(data, null, 2);
      setServerState('Server sẵn sàng', 'ok');
      return data;
    } catch (e) {
      if (e.status === 503) setServerState('Đang nạp model', 'warn');
      throw e;
    }
  }

  async function loadSettings() {
    await Promise.all([loadLexicon(), loadMusic(), loadHealth()]);
  }

  async function waitForServerReady() {
    for (let i = 0; i < 60; i += 1) {
      try {
        await loadHealth();
        hideBanner();
        return true;
      } catch (e) {
        if (e.status !== 503) throw e;
        await sleep(1500);
      }
    }
    showBanner('Server vẫn đang nạp model. Hãy thử tải lại trang sau.', true);
    return false;
  }

  async function boot() {
    setServerState('Đang kiểm tra…');
    try {
      if (!(await waitForServerReady())) return;
      await loadVoices();
      showKeyPanel(false);
      hideBanner();
      await Promise.allSettled([loadLibrary(), loadMusic(), refreshJobs(), refreshHistory(), loadLexicon(), loadHealth()]);
    } catch (e) {
      if (e.status === 401) showKeyPanel(true);
      else showBanner(e.message, true);
    }
  }

  function bindEvents() {
    document.querySelectorAll('.tab').forEach((btn) => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));
    $('keyBtn').addEventListener('click', () => showKeyPanel(!$('keyPanel').classList.contains('hidden') ? false : true));
    $('saveKeyBtn').addEventListener('click', () => {
      state.key = $('apiKeyInput').value.trim();
      localStorage.setItem('tqd.apiKey', state.key);
      boot();
    });
    $('forgetKeyBtn').addEventListener('click', () => {
      state.key = '';
      localStorage.removeItem('tqd.apiKey');
      $('apiKeyInput').value = '';
      showKeyPanel(true);
    });

    $('inputText').addEventListener('input', updateCounts);
    for (const id of ['filterGender', 'filterRegion', 'filterKind']) $(id).addEventListener('change', () => loadVoices().catch((e) => showBanner(e.message, true)));
    for (const id of ['libraryGender', 'libraryRegion', 'libraryKind']) $(id).addEventListener('change', () => loadLibrary().catch((e) => showBanner(e.message, true)));
    $('librarySearch').addEventListener('input', () => loadLibrary().catch(() => {}));
    $('reloadVoicesBtn').addEventListener('click', () => Promise.all([loadVoices(), loadLibrary()]).catch((e) => showBanner(e.message, true)));
    $('sampleVoiceBtn').addEventListener('click', () => {
      if ($('voiceSelect').value) playSample($('voiceSelect').value, $('sampleAudio')).catch((e) => showBanner(e.message, true));
    });

    for (const id of ['speedInput', 'musicVolume', 'musicDuck', 'musicFade']) $(id).addEventListener('input', updateSliderLabels);
    $('musicEnabled').addEventListener('change', () => $('musicControls').classList.toggle('hidden', !$('musicEnabled').checked));
    $('rolesToggle').addEventListener('change', () => {
      $('analyzeRolesBtn').classList.toggle('hidden', !$('rolesToggle').checked);
      if (!$('rolesToggle').checked) {
        state.roles = [];
        renderRoles();
      }
    });
    $('analyzeRolesBtn').addEventListener('click', () => analyzeRoles().catch((e) => showBanner(e.message, true)));

    $('insertPauseBtn').addEventListener('click', () => {
      const ta = $('inputText');
      const sec = Math.min(10, Math.max(0.1, Number($('pauseSeconds').value || 1)));
      const tag = `[pause:${Number(sec.toFixed(1))}]`;
      const start = ta.selectionStart ?? ta.value.length;
      const end = ta.selectionEnd ?? start;
      ta.value = `${ta.value.slice(0, start)}${tag}${ta.value.slice(end)}`;
      ta.focus();
      ta.selectionStart = ta.selectionEnd = start + tag.length;
      updateCounts();
    });

    $('importFileBtn').addEventListener('click', importFile);
    $('importUrlBtn').addEventListener('click', importUrl);
    $('speakBtn').addEventListener('click', submitSpeech);
    $('downloadResultBtn').addEventListener('click', () => {
      if (state.resultBlob) downloadBlob(state.resultBlob, `tqd-voicestudio.${state.resultExt}`);
    });
    $('downloadSrtBtn').addEventListener('click', () => downloadBlob(new Blob([state.resultSrt], { type: 'application/x-subrip;charset=utf-8' }), 'tqd-voicestudio.srt'));
    $('downloadVttBtn').addEventListener('click', () => downloadBlob(new Blob([state.resultVtt], { type: 'text/vtt;charset=utf-8' }), 'tqd-voicestudio.vtt'));
    $('rateUpBtn').addEventListener('click', () => state.lastHistoryId && submitRating(1, { history_id: state.lastHistoryId }, $('ratingStatus')));
    $('rateDownBtn').addEventListener('click', () => state.lastHistoryId && submitRating(-1, { history_id: state.lastHistoryId }, $('ratingStatus')));

    $('cloneConsent').addEventListener('change', () => { $('cloneSubmitBtn').disabled = !$('cloneConsent').checked; });
    $('cloneSubmitBtn').addEventListener('click', async () => {
      const file = $('cloneFile').files[0];
      const name = $('cloneName').value.trim();
      if (!file || !name) {
        $('cloneStatus').textContent = 'Cần chọn file audio và nhập tên giọng.';
        return;
      }
      const fd = new FormData();
      fd.append('file', file, file.name);
      fd.append('name', name);
      fd.append('gender', $('cloneGender').value);
      fd.append('region', $('cloneRegion').value);
      fd.append('prompt_text', $('cloneTranscript').value.trim());
      fd.append('consent', 'true');
      $('cloneSubmitBtn').disabled = true;
      try {
        const { data } = await api('/v1/voices', { method: 'POST', body: fd });
        $('cloneStatus').textContent = `Đã tạo giọng ${data.name}.`;
        await Promise.all([loadVoices(), loadLibrary()]);
      } catch (e) {
        $('cloneStatus').textContent = e.message;
      } finally {
        $('cloneSubmitBtn').disabled = !$('cloneConsent').checked;
      }
    });

    $('refreshJobsBtn').addEventListener('click', () => refreshJobs().catch((e) => showBanner(e.message, true)));
    $('jobsList').addEventListener('click', async (ev) => {
      const btn = ev.target.closest('button[data-action]');
      if (!btn) return;
      const item = btn.closest('.job-item');
      const jid = item.dataset.jobId;
      const note = item.querySelector('.job-note');
      try {
        if (btn.dataset.action === 'cancel') await api(`/v1/jobs/${jid}/cancel`, { method: 'POST' });
        if (btn.dataset.action === 'play') await fetchAudioInto(`/v1/jobs/${jid}/audio`, item.querySelector('audio'));
        if (btn.dataset.action === 'download') {
          const { blob } = await api(`/v1/jobs/${jid}/audio`, { blob: true });
          downloadBlob(blob, `job-${jid}.wav`);
        }
        if (btn.dataset.action === 'srt' || btn.dataset.action === 'vtt') {
          const ext = btn.dataset.action;
          const { blob } = await api(`/v1/jobs/${jid}/subtitles.${ext}`, { blob: true });
          downloadBlob(blob, `job-${jid}.${ext}`);
        }
        if (btn.dataset.action === 'rate-up') await submitRating(1, { job_id: jid }, note);
        if (btn.dataset.action === 'rate-down') await submitRating(-1, { job_id: jid }, note);
        if (btn.dataset.action === 'cancel') await refreshJobs();
      } catch (e) { note.textContent = e.message; }
    });

    $('refreshHistoryBtn').addEventListener('click', () => refreshHistory().catch((e) => showBanner(e.message, true)));
    $('historyPrev').addEventListener('click', () => { state.historyOffset = Math.max(0, state.historyOffset - state.historyLimit); refreshHistory().catch(() => {}); });
    $('historyNext').addEventListener('click', () => { state.historyOffset += state.historyLimit; refreshHistory().catch(() => {}); });
    $('historyList').addEventListener('click', async (ev) => {
      const btn = ev.target.closest('button[data-action]');
      if (!btn) return;
      const item = btn.closest('.history-item');
      const hid = item.dataset.historyId;
      try {
        if (btn.dataset.action === 'play') await fetchAudioInto(`/v1/history/${hid}/audio`, item.querySelector('audio'));
        if (btn.dataset.action === 'download') {
          const { blob } = await api(`/v1/history/${hid}/audio`, { blob: true });
          downloadBlob(blob, `history-${hid}.wav`);
        }
        if (btn.dataset.action === 'delete') {
          await api(`/v1/history/${hid}`, { method: 'DELETE' });
          await refreshHistory();
        }
      } catch (e) { showBanner(e.message, true); }
    });

    $('addLexRowBtn').addEventListener('click', () => {
      addLexiconRow($('lexWord').value.trim(), $('lexPron').value.trim());
      $('lexWord').value = '';
      $('lexPron').value = '';
    });
    $('saveLexiconBtn').addEventListener('click', () => saveLexicon().catch((e) => { $('lexiconStatus').textContent = ` ${e.message}`; }));
    $('uploadMusicBtn').addEventListener('click', () => uploadMusic().catch((e) => showBanner(e.message, true)));
    $('exportRatingsBtn').addEventListener('click', async () => {
      try {
        const { blob } = await api('/v1/ratings?format=jsonl', { blob: true });
        downloadBlob(blob, 'tqd-ratings.jsonl');
      } catch (e) { showBanner(e.message, true); }
    });
  }

  bindEvents();
  updateCounts();
  updateSliderLabels();
  if (state.key) $('apiKeyInput').value = state.key;
  boot();
})();
