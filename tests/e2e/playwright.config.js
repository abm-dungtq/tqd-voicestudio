const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './specs',
  timeout: 45000,
  workers: 1,
  fullyParallel: false,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:8013',
    browserName: 'chromium',
    viewport: { width: 1280, height: 900 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'D:/TTS/ViZipvoice/.venv/Scripts/python.exe fake_server.py',
    cwd: __dirname,
    url: 'http://127.0.0.1:8013/health',
    reuseExistingServer: false,
    timeout: 30000,
    env: {
      ...process.env,
      PYTHONPATH: 'D:/TTS/tqd-voice',
      PYTHONUTF8: '1',
      HF_HOME: 'D:/TTS/.hf-cache',
      TQD_E2E_HOME: 'D:/TTS/tmp/h4/e2e-home',
    },
  },
});
