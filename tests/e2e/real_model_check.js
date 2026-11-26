/* Manual real-model smoke driver. Usage: node real_model_check.js */
const fs = require('fs');
const path = require('path');
const { chromium } = require('@playwright/test');

(async () => {
  const baseURL = process.env.REAL_UI_URL || 'http://127.0.0.1:8014';
  const shot = process.env.REAL_UI_SHOT || path.join(__dirname, 'artifacts', 'real-doc.png');
  const wavPath = process.env.REAL_UI_WAV || 'D:/TTS/tmp/h4/real-ui.wav';
  fs.mkdirSync(path.dirname(shot), { recursive: true });
  fs.mkdirSync(path.dirname(wavPath), { recursive: true });

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(baseURL + '/');
  await page.waitForFunction(() => document.querySelectorAll('#voiceSelect option').length > 0, null, { timeout: 120000 });
  await page.fill('#inputText', 'Xin chào, đây là thử nghiệm giao diện web.');
  const responsePromise = page.waitForResponse(
    (r) => r.url().endsWith('/v1/audio/speech') && r.status() === 200,
    { timeout: 120000 },
  );
  await page.click('#speakBtn');
  const response = await responsePromise;
  const body = await response.body();
  fs.writeFileSync(wavPath, body);
  await page.waitForFunction(() => {
    const audio = document.querySelector('#resultAudio');
    return audio && audio.src.startsWith('blob:') && audio.duration > 0;
  }, null, { timeout: 30000 });
  await page.screenshot({ path: shot, fullPage: true });
  console.log(JSON.stringify({
    screenshot: shot,
    wav: wavPath,
    wavBytes: body.length,
    riff: body.subarray(0, 4).toString('ascii'),
    historyId: response.headers()['x-tqd-history-id'] || '',
    cache: response.headers()['x-tqd-cache'] || '',
  }));
  await browser.close();
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
