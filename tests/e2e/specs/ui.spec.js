const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const artifacts = path.join(__dirname, '..', 'artifacts');
fs.mkdirSync(artifacts, { recursive: true });

async function unlock(page) {
  await page.goto('/');
  await expect(page.locator('#keyPanel')).toBeVisible();
  await page.fill('#apiKeyInput', 'test-key');
  await page.click('#saveKeyBtn');
  await page.waitForFunction(() => document.querySelectorAll('#voiceSelect option').length > 0, null, { timeout: 15000 });
  await expect(page.locator('#keyPanel')).toBeHidden();
}

function wavBuffer(seconds = 0.6, freq = 220, sampleRate = 24000) {
  const n = Math.floor(seconds * sampleRate);
  const dataSize = n * 2;
  const buf = Buffer.alloc(44 + dataSize);
  buf.write('RIFF', 0);
  buf.writeUInt32LE(36 + dataSize, 4);
  buf.write('WAVE', 8);
  buf.write('fmt ', 12);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);
  buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(sampleRate, 24);
  buf.writeUInt32LE(sampleRate * 2, 28);
  buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34);
  buf.write('data', 36);
  buf.writeUInt32LE(dataSize, 40);
  for (let i = 0; i < n; i++) {
    const sample = Math.round(Math.sin(2 * Math.PI * freq * i / sampleRate) * 8000);
    buf.writeInt16LE(sample, 44 + i * 2);
  }
  return buf;
}

test.describe.serial('TQD-VoiceStudio Web UI', () => {
  test('key prompt unlocks and voices load', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#keyPanel')).toBeVisible();
    await page.fill('#apiKeyInput', 'test-key');
    await page.click('#saveKeyBtn');
    await page.waitForFunction(() => document.querySelectorAll('#voiceSelect option').length > 0, null, { timeout: 15000 });
    await expect(page.locator('#voiceSelect')).toContainText('Alpha');
    await expect(page.locator('#keyPanel')).toBeHidden();
  });

  test('sync synthesize creates blob audio, positive duration, and rating posts 201', async ({ page }) => {
    await unlock(page);
    await page.fill('#inputText', 'Xin chào, đây là thử nghiệm đồng bộ.');
    const speechResponse = page.waitForResponse((r) => r.url().endsWith('/v1/audio/speech') && r.status() === 200);
    await page.click('#speakBtn');
    const resp = await speechResponse;
    expect(resp.headers()['x-tqd-history-id']).toBeTruthy();
    await expect(page.locator('#resultPanel')).toBeVisible();
    const src = await page.locator('#resultAudio').getAttribute('src');
    expect(src).toMatch(/^blob:/);
    await page.waitForFunction(() => document.querySelector('#resultAudio').duration > 0);
    const ratingResponse = page.waitForResponse((r) => r.url().endsWith('/v1/ratings') && r.status() === 201);
    await page.click('#rateUpBtn');
    await ratingResponse;
    await expect(page.locator('#ratingStatus')).toContainText('Đã gửi');
  });

  test('normalize trim cache quality and music fields are present in request payload', async ({ page }) => {
    await unlock(page);
    await page.waitForFunction(() => [...document.querySelectorAll('#musicSelect option')].some((o) => o.value));
    await page.fill('#inputText', 'Kiểm tra các trường xử lý âm thanh.');
    await page.check('#normalizeInput');
    await page.check('#trimInput');
    await page.check('#cacheInput');
    await page.check('#qualityInput');
    await page.check('#musicEnabled');
    const reqPromise = page.waitForRequest((r) => r.url().endsWith('/v1/audio/speech') && r.method() === 'POST');
    await page.click('#speakBtn');
    const payload = reqPromise.then((r) => r.postDataJSON());
    expect(await payload).toMatchObject({
      normalize: true,
      trim_silence: true,
      cache: false,
      quality_check: true,
    });
    const body = await payload;
    expect(body.music).toBeTruthy();
    expect(body.music.id).toBeTruthy();
    expect(body.music.volume_db).toBe(-20);
    expect(body.music.duck_db).toBe(-12);
    expect(body.music.fade_s).toBe(2);
    await expect(page.locator('#resultPanel')).toBeVisible();
  });

  test('roles flow analyzes roles and sends cast', async ({ page }) => {
    await unlock(page);
    await page.fill('#inputText', 'Lan: Xin chào bạn.\nNam: Chào Lan, hôm nay thế nào?');
    await page.check('#rolesToggle');
    await page.click('#analyzeRolesBtn');
    await expect(page.locator('.role-row')).toHaveCount(2);
    await page.locator('.role-row select').nth(1).selectOption('Beta');
    const reqPromise = page.waitForRequest((r) => r.url().endsWith('/v1/audio/speech') && r.method() === 'POST');
    await page.click('#speakBtn');
    const body = (await reqPromise).postDataJSON();
    expect(body.cast).toEqual({ Lan: 'Alpha', Nam: 'Beta' });
    await expect(page.locator('#resultPanel')).toBeVisible();
  });

  test('subtitles mode uses timed endpoint and exposes srt vtt download buttons', async ({ page }) => {
    await unlock(page);
    await page.fill('#inputText', 'Câu có phụ đề để kiểm tra.');
    await page.check('#subtitlesInput');
    const timedResponse = page.waitForResponse((r) => r.url().endsWith('/v1/audio/speech/timed') && r.status() === 200);
    await page.click('#speakBtn');
    await timedResponse;
    await expect(page.locator('#downloadSrtBtn')).toBeVisible();
    await expect(page.locator('#downloadVttBtn')).toBeVisible();
  });

  test('pause insert adds tag exactly at cursor', async ({ page }) => {
    await unlock(page);
    await page.fill('#inputText', 'abc def');
    await page.locator('#inputText').evaluate((el) => el.setSelectionRange(4, 4));
    await page.fill('#pauseSeconds', '1.5');
    await page.click('#insertPauseBtn');
    await expect(page.locator('#inputText')).toHaveValue('abc [pause:1.5]def');
  });

  test('markdown import fills textarea and word count', async ({ page }) => {
    await unlock(page);
    await page.locator('.import-box summary').click();
    await page.setInputFiles('#importFile', {
      name: 'bai.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from('# Tiêu đề thử\n\nMột đoạn có [liên kết](https://example.com) và `mã`.\n', 'utf8'),
    });
    await page.click('#importFileBtn');
    await page.waitForFunction(() => document.querySelector('#inputText').value.includes('liên kết'));
    const text = await page.locator('#inputText').inputValue();
    expect(text).not.toContain('https://example.com');
    await expect(page.locator('#wordCount')).not.toHaveText('0 từ');
    await expect(page.locator('#importStatus')).toContainText('đã nhập');
  });

  test('job submit shows progress then done with playable audio and subtitles', async ({ page }) => {
    await unlock(page);
    await page.fill('#inputText', 'Đoạn một của job nền.\n\nĐoạn hai của job nền.');
    await page.check('#subtitlesInput');
    await page.check('#backgroundJobInput');
    await page.click('#speakBtn');
    await expect(page.locator('#tab-jobs')).toHaveClass(/active/);
    await expect(page.locator('.job-item').first()).toBeVisible();
    await expect(page.locator('.job-item .progress').first()).toBeVisible();
    await expect(page.locator('.job-item .badge').first()).toHaveText('done', { timeout: 15000 });
    await expect(page.locator('.job-item button[data-action="srt"]').first()).toBeVisible();
    await expect(page.locator('.job-item button[data-action="vtt"]').first()).toBeVisible();
    await page.locator('.job-item button[data-action="play"]').first().click();
    await page.waitForFunction(() => {
      const a = document.querySelector('.job-item audio');
      return a && a.src.startsWith('blob:') && a.duration > 0;
    });
  });

  test('history item appears and repeated synthesis is marked cache hit', async ({ page }) => {
    await unlock(page);
    await page.fill('#inputText', 'Câu kiểm tra cache trong lịch sử.');
    let response = page.waitForResponse((r) => r.url().endsWith('/v1/audio/speech') && r.status() === 200);
    await page.click('#speakBtn');
    await response;
    await expect(page.locator('#resultPanel')).toBeVisible();
    response = page.waitForResponse((r) => r.url().endsWith('/v1/audio/speech') && r.status() === 200);
    await page.click('#speakBtn');
    const second = await response;
    expect(second.headers()['x-tqd-cache']).toBe('hit');
    await page.click('button[data-tab="history"]');
    await expect(page.locator('.history-item').first()).toBeVisible();
    await expect(page.locator('.history-item .badge').first()).toHaveText('cache hit');
  });

  test('lexicon row add and save fires PUT with entries', async ({ page }) => {
    await unlock(page);
    await page.click('button[data-tab="settings"]');
    await page.fill('#lexWord', 'TQD');
    await page.fill('#lexPron', 'tê quy dê');
    await page.click('#addLexRowBtn');
    const reqPromise = page.waitForRequest((r) => r.url().endsWith('/v1/lexicon') && r.method() === 'PUT');
    await page.click('#saveLexiconBtn');
    const body = (await reqPromise).postDataJSON();
    expect(body.entries.TQD).toBe('tê quy dê');
    await expect(page.locator('#lexiconStatus')).toContainText('Đã lưu');
  });

  test('clone submit stays disabled until consent then succeeds', async ({ page }) => {
    await unlock(page);
    await page.click('button[data-tab="clone"]');
    await expect(page.locator('#cloneSubmitBtn')).toBeDisabled();
    await page.setInputFiles('#cloneFile', { name: 'mau.wav', mimeType: 'audio/wav', buffer: wavBuffer() });
    await page.fill('#cloneName', 'vi-bac-nu-playwright-tunhien-v1');
    await page.selectOption('#cloneGender', 'female');
    await page.selectOption('#cloneRegion', 'Bắc');
    await page.fill('#cloneTranscript', 'Xin chào, đây là mẫu giọng thử nghiệm.');
    await expect(page.locator('#cloneSubmitBtn')).toBeDisabled();
    await page.check('#cloneConsent');
    await expect(page.locator('#cloneSubmitBtn')).toBeEnabled();
    const created = page.waitForResponse((r) => r.url().endsWith('/v1/voices') && r.status() === 201);
    await page.click('#cloneSubmitBtn');
    await created;
    await expect(page.locator('#cloneStatus')).toContainText('Đã tạo giọng');
  });

  test('screenshots of every tab', async ({ page }) => {
    await unlock(page);
    for (const tab of ['read', 'clone', 'library', 'jobs', 'history', 'settings']) {
      await page.click(`button[data-tab="${tab}"]`);
      await page.waitForTimeout(500);
      await page.screenshot({ path: path.join(artifacts, `tab-${tab}.png`), fullPage: true });
    }
  });

  test('390px mobile viewport has no horizontal document overflow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await unlock(page);
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(width).toBeLessThanOrEqual(395);
    await page.screenshot({ path: path.join(artifacts, 'mobile-390.png'), fullPage: true });
  });
});
