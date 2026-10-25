'use strict';

/**
 * nurow 桌面壳
 * - 无边框透明置顶窗口
 * - 自动拉起 Python 后端（server/app.py）
 * - 托盘菜单 / 快捷键 / 穿透模式
 */

const {
  app, BrowserWindow, Tray, Menu, nativeImage, screen,
  globalShortcut, shell, ipcMain, session,
} = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

const BASE = __dirname;

// 用项目内的 profile 目录，避免 %APPDATA% 权限/单实例锁问题
const PROFILE_DIR = path.join(BASE, 'data', 'electron-profile');
try {
  fs.mkdirSync(PROFILE_DIR, { recursive: true });
  app.setPath('userData', PROFILE_DIR);
  app.setPath('sessionData', PROFILE_DIR);
} catch (err) {
  console.error('profile setup failed', err);
}

// 以管理员身份运行时，Chromium 沙箱会直接崩掉（exit 0x80000003 STATUS_BREAKPOINT）。
// 这台机器就是管理员会话，所以必须关沙箱；本地桌宠应用可以接受。
app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');

const DATA_DIR = path.join(BASE, 'data');
const LOG_DIR = path.join(DATA_DIR, 'logs');
const WINDOW_STATE = path.join(DATA_DIR, 'window.json');

function loadWindowState() {
  try {
    const st = JSON.parse(fs.readFileSync(WINDOW_STATE, 'utf8'));
    if (typeof st.x === 'number' && typeof st.y === 'number') return st;
  } catch (_) { /* 没有就用默认 */ }
  return null;
}

function saveWindowState(bounds) {
  try {
    fs.mkdirSync(DATA_DIR, { recursive: true });
    fs.writeFileSync(WINDOW_STATE, JSON.stringify({ x: bounds.x, y: bounds.y }), 'utf8');
  } catch (_) { /* ignore */ }
}
const PORT = Number(process.env.NUROW_PORT || 8765);
const APP_URL = `http://127.0.0.1:${PORT}/`;

let win = null;
let tray = null;
let serverProc = null;
let clickThrough = false;
let quitting = false;
let settingsWin = null;

function logLine(...args) {
  const line = `[${new Date().toISOString()}] ${args.map((a) => String(a)).join(' ')}`;
  console.log(line);
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    fs.appendFileSync(path.join(LOG_DIR, 'electron.log'), line + '\n');
  } catch (_) { /* ignore */ }
}

logLine('main.js loaded; argv=', JSON.stringify(process.argv));
process.on('uncaughtException', (err) => {
  logLine('uncaughtException:', err && err.stack ? err.stack : String(err));
});
process.on('unhandledRejection', (err) => {
  logLine('unhandledRejection:', err && err.stack ? err.stack : String(err));
});

function pyExe() {
  const portable = path.join(BASE, 'runtime', 'portable', 'python', 'pythonw.exe');
  if (fs.existsSync(portable)) return portable;
  const portableExe = path.join(BASE, 'runtime', 'portable', 'python', 'python.exe');
  if (fs.existsSync(portableExe)) return portableExe;
  const pyw = path.join(BASE, '.venv', 'Scripts', 'pythonw.exe');
  if (fs.existsSync(pyw)) return pyw;
  const py = path.join(BASE, '.venv', 'Scripts', 'python.exe');
  if (fs.existsSync(py)) return py;
  return 'python';
}

// 读取服务端配置（窗口相关设置用）
function serverConfig() {
  try {
    return JSON.parse(fs.readFileSync(path.join(BASE, 'server', 'config.json'), 'utf8')) || {};
  } catch (_) {
    return {};
  }
}

// 应用窗口相关设置：置顶 / 开机自启
function applyWindowSettings() {
  const cfg = serverConfig();
  const onTop = cfg.always_on_top !== false;
  if (win && !win.isDestroyed()) win.setAlwaysOnTop(onTop, 'floating');
  try {
    setCursorInterval(cfg.gaze_speed === 'slow' ? 70 : 30);
  } catch (_) { /* ignore */ }
  try {
    const autoStart = cfg.auto_start === true;
    const launcher = path.join(BASE, 'yuki.exe');
    if (fs.existsSync(launcher)) {
      app.setLoginItemSettings({ openAtLogin: autoStart, path: launcher, args: [] });
    } else {
      app.setLoginItemSettings({ openAtLogin: autoStart, path: process.execPath, args: ['--no-sandbox', BASE] });
    }
    logLine('window settings applied: onTop=', onTop, 'autoStart=', autoStart);
  } catch (e) {
    logLine('apply window settings failed:', e && e.message);
  }
  refreshTrayMenu();
}

// 根据 config.json 里的 avatar 选择渲染页：yuki(Live2D) / hina3d(MMD 3D)
function avatarPage() {
  if (process.env.NUROW_START_PAGE !== undefined) return process.env.NUROW_START_PAGE;
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(BASE, 'server', 'config.json'), 'utf8'));
    return cfg.avatar === 'hina3d' ? 'hina3d.html' : '';
  } catch (_) {
    return '';
  }
}


function openSettings() {
  if (settingsWin && !settingsWin.isDestroyed()) {
    settingsWin.show();
    settingsWin.focus();
    return;
  }
  settingsWin = new BrowserWindow({
    width: 760,
    height: 860,
    minWidth: 560,
    minHeight: 520,
    title: 'yuki 设置',
    backgroundColor: '#14161f',
    autoHideMenuBar: true,
    icon: fs.existsSync(path.join(BASE, 'yuki.ico')) ? path.join(BASE, 'yuki.ico') : undefined,
    webPreferences: {
      preload: path.join(BASE, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  settingsWin.loadURL(APP_URL + 'settings.html');
  settingsWin.on('closed', () => { settingsWin = null; });
  logLine('settings window opened');
}

function startServer() {
  if (serverProc) return;
  fs.mkdirSync(LOG_DIR, { recursive: true });
  const out = fs.openSync(path.join(LOG_DIR, 'server.out.log'), 'a');
  const env = { ...process.env, NUROW_PORT: String(PORT) };
  delete env.ELECTRON_RUN_AS_NODE;
  serverProc = spawn(pyExe(), [path.join(BASE, 'server', 'app.py')], {
    cwd: path.join(BASE, 'server'),
    env,
    stdio: ['ignore', out, out],
    windowsHide: true,
  });
  serverProc.on('exit', (code) => { logLine('server exited with', code); serverProc = null; });
  logLine('server spawned, pid', serverProc.pid);
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

let cursorTimer = null;
let cursorIntervalMs = 30;
function broadcastCursor() {
  try {
    if (!win || win.isDestroyed() || !win.isVisible()) return;
    if (!win.webContents || win.webContents.isDestroyed()) return;
    const p = screen.getCursorScreenPoint();
    const b = win.getBounds();
    win.webContents.send('cursor', { x: p.x - b.x, y: p.y - b.y, sx: p.x, sy: p.y });
  } catch (_) { /* ignore */ }
}
function setCursorInterval(ms) {
  cursorIntervalMs = ms;
  if (cursorTimer) { clearInterval(cursorTimer); cursorTimer = null; }
  cursorTimer = setInterval(broadcastCursor, cursorIntervalMs);
}
function startCursorBroadcast() {
  if (cursorTimer) return;
  setCursorInterval(cursorIntervalMs);
}

function httpJson(pathname) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port: PORT, path: pathname, timeout: 1500 }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (d) => { body += d; });
      res.on('end', () => {
        try { resolve({ status: res.statusCode, json: JSON.parse(body) }); } catch (_) { resolve({ status: res.statusCode }); }
      });
    });
    req.on('error', () => resolve(null));
    req.on('timeout', () => { req.destroy(); resolve(null); });
  });
}

async function waitHealth(deadline) {
  while (Date.now() < deadline) {
    const r = await httpJson('/api/health');
    if (r && r.status === 200) return r.json;
    await sleep(400);
  }
  return null;
}

function apiPost(pathname, payload) {
  return new Promise((resolve) => {
    const data = Buffer.from(JSON.stringify(payload || {}), 'utf8');
    const req = http.request(
      {
        host: '127.0.0.1',
        port: PORT,
        path: pathname,
        method: 'POST',
        timeout: 3000,
        headers: { 'Content-Type': 'application/json', 'Content-Length': data.length },
      },
      (res) => {
        let body = '';
        res.setEncoding('utf8');
        res.on('data', (d) => { body += d; });
        res.on('end', () => {
          try { resolve(JSON.parse(body)); } catch (_) { resolve({ raw: body }); }
        });
      }
    );
    req.on('error', () => resolve(null));
    req.on('timeout', () => { try { req.destroy(); } catch (_) {} resolve(null); });
    req.write(data);
    req.end();
  });
}

function createWindow() {
  const wa = screen.getPrimaryDisplay().workArea;
  const cfg0 = serverConfig();
  const onTop = cfg0.always_on_top !== false;
  const width = 380;
  const height = Math.min(700, Math.max(520, wa.height - 80));
  const savedPos = loadWindowState();
  const pos = savedPos && screen.getAllDisplays().some((d) => {
    const a = d.workArea;
    return savedPos.x >= a.x - 50 && savedPos.x < a.x + a.width - 100 && savedPos.y >= a.y - 50 && savedPos.y < a.y + a.height - 100;
  }) ? { x: savedPos.x, y: savedPos.y } : { x: wa.x + wa.width - width - 18, y: wa.y + wa.height - height - 10 };
  win = new BrowserWindow({
    width,
    height,
    x: pos.x,
    y: pos.y,
    transparent: true,
    frame: false,
    resizable: false,
    maximizable: false,
    minimizable: false,
    fullscreenable: false,
    alwaysOnTop: onTop,
    skipTaskbar: true,
    hasShadow: false,
    title: 'yuki',
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(BASE, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });
  win.setAlwaysOnTop(onTop, 'floating');
  let moveSaveTimer = null;
  const rememberBounds = () => {
    clearTimeout(moveSaveTimer);
    moveSaveTimer = setTimeout(() => {
      try { saveWindowState(win.getBounds()); } catch (_) {}
    }, 500);
  };
  win.on('move', rememberBounds);
  win.on('moved', rememberBounds);
  win.setMenuBarVisibility(false);
  win.webContents.on('console-message', (event, level, message, line, sourceId) => {
    logLine('renderer:', message, sourceId ? '(' + sourceId + ':' + line + ')' : '');
  });
  win.webContents.on('did-finish-load', () => logLine('page loaded ok'));
  const startPage = avatarPage();
  win.loadURL(APP_URL + startPage);
  win.webContents.on('did-fail-load', () => {
    logLine('page load failed, retrying...');
    if (!quitting) setTimeout(() => { try { win && win.loadURL(APP_URL + startPage); } catch (_) {} }, 1500);
  });
  win.once('ready-to-show', () => {
    win.show();
    logLine('window shown');
    if (process.env.NUROW_SHOT) {
      setTimeout(async () => {
        try {
          const img = await win.webContents.capturePage();
          const p = path.join(LOG_DIR, 'shot.png');
          fs.writeFileSync(p, img.toPNG());
          logLine('screenshot saved:', p);
        } catch (err) {
          logLine('screenshot failed:', err);
        }
        if (process.env.NUROW_SHOT_EXIT) {
          setTimeout(() => { quitting = true; app.quit(); }, 800);
        }
      }, 6500);
    }
    if (process.env.NUROW_MIC_TEST) {
      setTimeout(async () => {
        try {
          const res = await win.webContents.executeJavaScript(
            "(async () => { try { if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return 'ERR no mediaDevices';"
            + " const s = await navigator.mediaDevices.getUserMedia({ audio: true });"
            + " const labels = s.getAudioTracks().map(t => t.label + '/' + t.readyState);"
            + " s.getTracks().forEach(t => t.stop()); return 'OK ' + JSON.stringify(labels); }"
            + " catch (e) { return 'ERR ' + e.name + ': ' + e.message; } })()"
          );
          logLine('mic test result:', res);
        } catch (err) {
          logLine('mic test failed:', err);
        }
        if (process.env.NUROW_MIC_TEST_EXIT) {
          setTimeout(() => { quitting = true; app.quit(); }, 500);
        }
      }, 4000);
    }
  });
  win.on('closed', () => { win = null; });
}

function createTray() {
  let icon = nativeImage.createEmpty();
  try {
    const candidates = [
      path.join(BASE, 'web', 'yuki_tray_32.png'),
      path.join(BASE, 'web', 'yuki_tray.png'),
      path.join(BASE, 'assets', 'icon.png'),
    ];
    for (const p of candidates) {
      if (!fs.existsSync(p)) continue;
      const name = path.basename(p);
      const img = nativeImage.createFromPath(p);
      if (img.isEmpty()) continue;
      icon = name === 'yuki_tray_32.png' ? img : img.resize({ width: 32, height: 32 });
      break;
    }
  } catch (_) { /* ignore */ }
  tray = new Tray(icon);
  tray.setToolTip('yuki — 点我打开菜单');
  refreshTrayMenu();
  tray.on('click', () => toggleWindow());
}

function refreshTrayMenu() {
  if (!tray) return;
  const menu = Menu.buildFromTemplate([
    { label: '显示 / 隐藏', click: toggleWindow },
    { label: clickThrough ? '关闭穿透模式' : '穿透模式（暂时点不到她）', click: toggleClickThrough },
    { type: 'separator' },
    { label: '设置…', click: openSettings },
    { label: '变成黑yuki', click: () => { apiPost('/api/persona/switch', { mode: 'dark' }); } },
    { label: '变回普通 yuki', click: () => { apiPost('/api/persona/switch', { mode: 'normal' }); } },
    { type: 'separator' },
    { label: '打开日志', click: () => shell.openPath(LOG_DIR) },
    { label: '打开模型文件夹', click: () => shell.openPath(path.join(BASE, 'models')) },
    { label: '开发者工具', click: () => { if (win) win.webContents.openDevTools({ mode: 'detach' }); } },
    { type: 'separator' },
    { label: '退出', click: () => { quitting = true; app.quit(); } },
  ]);
  tray.setContextMenu(menu);
}

function toggleWindow() {
  if (!win) { createWindow(); return; }
  if (win.isVisible()) win.hide(); else { win.show(); win.focus(); }
}

function toggleClickThrough() {
  clickThrough = !clickThrough;
  if (win) {
    win.setIgnoreMouseEvents(clickThrough, { forward: true });
    logLine('click-through =', clickThrough);
  }
  refreshTrayMenu();
}

function resetPosition() {
  if (!win) return;
  const wa = screen.getPrimaryDisplay().workArea;
  const b = win.getBounds();
  win.setPosition(wa.x + wa.width - b.width - 18, wa.y + wa.height - b.height - 10);
  saveWindowState(win.getBounds());
}

ipcMain.on('quit', () => { quitting = true; app.quit(); });
ipcMain.on('open-logs', () => shell.openPath(LOG_DIR));
ipcMain.on('open-models', () => shell.openPath(path.join(BASE, 'models')));
ipcMain.on('open-settings', openSettings);
ipcMain.on('apply-window-settings', applyWindowSettings);
ipcMain.on('hide-window', () => { if (win) win.hide(); });
ipcMain.on('capture-pet', () => {
  if (!win || win.isDestroyed()) return;
  win.capturePage().then((img) => {
    try {
      fs.mkdirSync(LOG_DIR, { recursive: true });
      fs.writeFileSync(path.join(LOG_DIR, 'pet_capture.png'), img.toPNG());
      logLine('pet captured');
    } catch (e) { logLine('capture failed:', e && e.message); }
  }).catch((e) => logLine('capture error:', e && e.message));
});
ipcMain.on('reload-avatar', () => {
  if (win && !win.isDestroyed()) win.loadURL(APP_URL + avatarPage());
});

let dragState = null;
let dragTimer = null;
ipcMain.on('drag-start', () => {
  if (!win) return;
  const p = screen.getCursorScreenPoint();
  const b = win.getBounds();
  dragState = { offsetX: p.x - b.x, offsetY: p.y - b.y };
  if (dragTimer) return;
  dragTimer = setInterval(() => {
    try {
      if (!dragState || !win || win.isDestroyed()) return;
      const c = screen.getCursorScreenPoint();
      const wa = screen.getPrimaryDisplay().workArea;
      const bb = win.getBounds();
      let x = Math.round(c.x - dragState.offsetX);
      let y = Math.round(c.y - dragState.offsetY);
      x = Math.max(wa.x, Math.min(wa.x + wa.width - bb.width, x));
      y = Math.max(wa.y, Math.min(wa.y + wa.height - bb.height, y));
      if (x !== bb.x || y !== bb.y) win.setPosition(x, y);
    } catch (_) { /* ignore */ }
  }, 16);
});
ipcMain.on('drag-end', () => {
  dragState = null;
  if (dragTimer) { clearInterval(dragTimer); dragTimer = null; }
});
ipcMain.on('toggle-clickthrough', toggleClickThrough);
ipcMain.on('reset-position', resetPosition);

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) { win.show(); win.focus(); }
  });

  app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');

  app.whenReady().then(async () => {
    logLine('electron', process.versions.electron, 'chromium', process.versions.chrome);
    // 麦克风权限：本地可信应用，直接放行
    try {
      session.defaultSession.setPermissionRequestHandler((wc, permission, callback) => {
        callback(permission === 'media' || permission === 'audioCapture' || permission === 'videoCapture');
      });
      session.defaultSession.setPermissionCheckHandler((wc, permission) => (
        permission === 'media' || permission === 'audioCapture' || permission === 'videoCapture'
      ));
    } catch (err) {
      logLine('permission handler failed:', err);
    }
    startServer();
    const health = await waitHealth(Date.now() + 60000);
    logLine('health:', JSON.stringify(health || null));
    createWindow();
    startCursorBroadcast();
    createTray();
    applyWindowSettings();
    try {
      globalShortcut.register('Alt+N', toggleWindow);
      globalShortcut.register('CommandOrControl+Shift+Y', toggleWindow);
      globalShortcut.register('CommandOrControl+Shift+U', toggleClickThrough);
    } catch (e) { logLine('shortcut register failed', e); }
  });

  app.on('window-all-closed', () => { /* keep alive in tray */ });
  app.on('before-quit', (e) => {
    if (quitting) return;
    quitting = true;
    e.preventDefault();
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      try { globalShortcut.unregisterAll(); } catch (_) {}
      if (serverProc) { try { serverProc.kill(); } catch (_) {} serverProc = null; }
      if (cursorTimer) { clearInterval(cursorTimer); cursorTimer = null; }
      setTimeout(() => app.exit(0), 200);
    };
    // 先让后端把 Minecraft 子进程一起关掉，再退出
    try {
      const req = http.request(
        { host: '127.0.0.1', port: PORT, path: '/api/mc/stop', method: 'POST', timeout: 1500 },
        (res) => { res.resume(); finish(); }
      );
      req.on('error', finish);
      req.on('timeout', () => { try { req.destroy(); } catch (_) {} finish(); });
      req.end();
      setTimeout(finish, 2000);
    } catch (_) {
      finish();
    }
  });
}
