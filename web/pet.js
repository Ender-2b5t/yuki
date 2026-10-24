(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const els = {
    stage: $('stage'),
    bubble: $('bubble'),
    bubbleText: $('bubble-text'),
    input: $('input'),
    send: $('send'),
    mic: $('mic'),
    handsfree: $('handsfree'),
    settings: $('settings'),
    mute: $('mute'),
    status: $('status'),
    petzone: $('petzone'),
    dragbar: $('dragbar'),
  };

  const state = {
    model: null,
    cfg: null,
    ws: null,
    connected: false,
    muted: false,
    reply: '',
    bubbleTimer: 0,
    audioCtx: null,
    analyser: null,
    mouth: 0,
    queue: [],
    playing: false,
    timeData: new Uint8Array(256),
    greeted: false,
    expressions: {},
    baseY: 0,
    bounce: null,
    gaze: null,
    gazeX: 0,
    gazeY: 0,
    idleGaze: null,
    idleTilt: 0,
    curTilt: 0,
  };

  // ------------------------------------------------------------------ PIXI --
  const app = new PIXI.Application({
    resizeTo: window,
    backgroundAlpha: 0,
    antialias: true,
    autoDensity: true,
    resolution: Math.min(2, window.devicePixelRatio || 1),
  });
  els.stage.appendChild(app.view);

  function setParam(core, cubism4Name, cubism2Name, value) {
    try {
      if (core.setParameterValueById) core.setParameterValueById(cubism4Name, value);
    } catch (e) { /* ignore */ }
    try {
      if (core.setParamFloat) core.setParamFloat(cubism2Name, value);
    } catch (e) { /* ignore */ }
  }

  function layout() {
    const m = state.model;
    if (!m) return;
    const W = window.innerWidth;
    const H = window.innerHeight;
    const im = m.internalModel || {};
    const mw = im.width || m.width || 1000;
    const mh = im.height || m.height || 1000;
    const scale = Math.min((W * 0.98) / mw, (H * 0.96) / mh) * ((state.cfg && state.cfg.scale) || 1);
    m.anchor.set(0.5, 1);
    m.scale.set(scale);
    m.x = W / 2 + ((state.cfg && state.cfg.offsetX) || 0) * W;
    m.y = H - 52 + ((state.cfg && state.cfg.offsetY) || 0) * H;
    state.baseY = m.y;
  }

  function playIdle() {
    const m = state.model;
    if (!m) return;
    try { m.motion('Idle'); } catch (e) { /* ignore */ }
  }

  function playTap() {
    const m = state.model;
    if (!m) return;
    try { m.motion('TapBody'); } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------------ 点击部位 / 表情 --
  const PART_EXPR_KEYWORDS = {
    head: ['星星眼', '脸红'],
    face: ['脸红', '吐舌'],
    body: ['生气', '晕'],
    legs: ['哭', '生气'],
    tail: ['爱心眼', '吐舌'],
  };

  function hitPart(clientX, clientY) {
    const m = state.model;
    if (!m) return null;
    const b = m.getBounds();
    const rx = (clientX - b.x) / Math.max(1, b.width);
    const ry = (clientY - b.y) / Math.max(1, b.height);
    if (rx < -0.05 || rx > 1.05 || ry < -0.05 || ry > 1.05) return null;
    if (rx > 0.70 && ry > 0.32 && ry < 0.86) return 'tail';
    if (ry < 0.30) return 'head';
    if (ry < 0.44) return 'face';
    if (ry < 0.66) return 'body';
    return 'legs';
  }

  function playExprByKeywords(kws) {
    const map = state.expressions || {};
    const ids = Object.keys(map).filter((id) => kws.some((k) => String(map[id]).indexOf(k) >= 0));
    if (!ids.length) return;
    const pick = ids[Math.floor(Math.random() * ids.length)];
    try { state.model.expression(pick); } catch (e) { /* ignore */ }
    console.log('[nurow] expression', pick, map[pick]);
  }

  function playExprForPart(part) {
    playExprByKeywords(PART_EXPR_KEYWORDS[part] || []);
  }

  // 根据她回复的情绪做个表情，显得有反应
  function reactToReply(text) {
    const t = String(text || '');
    let kws = null;
    if (/哼|才不|讨厌|笨蛋|傻瓜/.test(t)) kws = ['生气', '脸红'];
    else if (/嘿嘿|好耶|开心|喜欢|最喜欢|真棒/.test(t)) kws = ['星星眼', '爱心眼'];
    else if (/呜|哭|委屈|难过/.test(t)) kws = ['哭'];
    else if (/害羞|脸红|羞/.test(t)) kws = ['脸红'];
    if (kws) {
      playExprByKeywords(kws);
      state.bounce = { t: 0 };
    }
  }

  // 注视（交给 Live2D 自带的 focus 平滑处理）
  function applyGazeUnused(dt) {
    const m = state.model;
    if (!m) return;
    const W = window.innerWidth;
    const H = window.innerHeight;
    let tx = 0;
    let ty = 0;
    if (state.gaze) {
      const cx = W / 2;
      const cy = H * 0.40;
      tx = Math.max(-1, Math.min(1, (state.gaze.x - cx) / (W * 0.52)));
      ty = Math.max(-1, Math.min(1, (state.gaze.y - cy) / (H * 0.55)));
    }
    const k = 1 - Math.exp(-Math.max(0.001, dt) / 0.055); // 约 55ms 响应
    state.gazeX += (tx - state.gazeX) * k;
    state.gazeY += (ty - state.gazeY) * k;
    try {
      const core = m.internalModel.coreModel;
      setParam(core, 'ParamAngleX', 'PARAM_ANGLE_X', state.gazeX * 30);
      setParam(core, 'ParamAngleY', 'PARAM_ANGLE_Y', -state.gazeY * 26);
      setParam(core, 'ParamAngleZ', 'PARAM_ANGLE_Z', -state.gazeX * 6);
      setParam(core, 'ParamBodyAngleX', 'PARAM_BODY_ANGLE_X', state.gazeX * 10);
      setParam(core, 'ParamEyeBallX', 'PARAM_EYE_BALL_X', state.gazeX);
      setParam(core, 'ParamEyeBallY', 'PARAM_EYE_BALL_Y', -state.gazeY);
    } catch (e) { /* ignore */ }
  }

  app.ticker.add(() => {
    const m = state.model;
    if (!m) return;
    // 被点之后的轻轻弹一下
    if (state.bounce) {
      state.bounce.t += app.ticker.deltaMS;
      const p = Math.min(1, state.bounce.t / 420);
      m.y = state.baseY - Math.sin(p * Math.PI) * 9;
      if (p >= 1) {
        m.y = state.baseY;
        state.bounce = null;
      }
    }
    // 小幅歪头，增加"活着"的感觉
    state.curTilt += (state.idleTilt - state.curTilt) * 0.12;
    if (Math.abs(state.curTilt) > 0.0005) m.rotation = state.curTilt;
    else m.rotation = 0;
    let amp = 0;
    if (state.playing && state.analyser) {
      state.analyser.getByteTimeDomainData(state.timeData);
      let sum = 0;
      for (let i = 0; i < state.timeData.length; i++) {
        const v = (state.timeData[i] - 128) / 128;
        sum += v * v;
      }
      amp = Math.min(1, Math.sqrt(sum / state.timeData.length) * 2.6);
    }
    const speed = amp > state.mouth ? 0.62 : 0.2;
    state.mouth += (amp - state.mouth) * speed;
    try {
      const core = m.internalModel.coreModel;
      setParam(core, 'ParamMouthOpenY', 'PARAM_MOUTH_OPEN_Y', state.mouth);
      setParam(core, 'ParamMouthForm', 'PARAM_MOUTH_FORM', 0.2 + state.mouth * 0.45);
    } catch (e) { /* ignore */ }
  });

  function showNoModel(cfg, extra) {
    if (document.getElementById('no-model')) return;
    const div = document.createElement('div');
    div.id = 'no-model';
    const lines = [];
    lines.push('没找到 Live2D 模型');
    if (extra) lines.push(extra);
    lines.push('把模型文件夹（含 .model3.json）放进：');
    lines.push('nurow\\models\\');
    const avail = (cfg && cfg.available && cfg.available.model3) || [];
    if (avail.length) lines.push('已有：' + avail.slice(0, 4).join('、'));
    lines.forEach((line, i) => {
      const p = document.createElement('div');
      p.textContent = line;
      if (i > 0) p.style.fontSize = '12px';
      if (i > 0) p.style.opacity = '0.82';
      div.appendChild(p);
    });
    document.body.appendChild(div);
  }

  async function initModel() {
    let cfg = null;
    try {
      cfg = await (await fetch('/api/model')).json();
    } catch (e) {
      console.warn('fetch /api/model failed', e);
    }
    if (!cfg || !cfg.url) {
      showNoModel(cfg);
      return;
    }
    state.cfg = cfg;
    if (cfg.cubism === 2) {
      showNoModel(cfg, '这是 Cubism 2 格式的模型，暂时不支持；先用示例模型吧。');
      return;
    }
    try {
      const model = await PIXI.live2d.Live2DModel.from(cfg.url, { autoInteract: false });
      state.model = model;
      app.stage.addChild(model);

      layout();
      playIdle();
      setInterval(playIdle, 15000);
      console.log('[nurow] model loaded:', cfg.url);
    } catch (e) {
      console.error('[nurow] model load failed', e);
      showNoModel(cfg, '模型加载失败：' + (e && e.message ? e.message : String(e)));
    }
  }

  // ----------------------------------------------------------------- audio --
  function ensureAudio() {
    if (!state.audioCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      state.audioCtx = new AC();
      state.analyser = state.audioCtx.createAnalyser();
      state.analyser.fftSize = 512;
      state.analyser.connect(state.audioCtx.destination);
    }
    if (state.audioCtx.state === 'suspended') {
      state.audioCtx.resume().catch(() => {});
    }
    return state.audioCtx;
  }

  function drainAudio() {
    if (state.playing) return;
    if (!state.audioCtx || !state.queue.length) return;
    const ctx = state.audioCtx;
    const buffer = state.queue.shift();
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(state.analyser);
    state.playing = true;
    src.onended = () => {
      state.playing = false;
      if (state.queue.length) drainAudio();
      else state.mouth = 0;
    };
    src.start();
  }

  async function enqueueAudioB64(b64) {
    const ctx = ensureAudio();
    try {
      const bin = atob(b64);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) arr[i] = bin.charCodeAt(i);
      const buf = await ctx.decodeAudioData(arr.buffer);
      state.queue.push(buf);
      drainAudio();
    } catch (e) {
      console.warn('[nurow] decode audio failed', e);
    }
  }

  // ----------------------------------------------------------------- 麦克风 --
  const TOOL_LABELS = {
    open_url: '打开网页',
    web_search: '搜索',
    open_app: '打开软件',
    open_path: '打开文件',
    activate_window: '切窗口',
    clipboard_read: '看剪贴板',
    clipboard_write: '写剪贴板',
    wait_seconds: '等一下',
    set_volume: '调音量',
    mute_volume: '静音',
    press_media_key: '按媒体键',
    set_timer: '设提醒',
    take_screenshot: '截屏',
    get_time: '看时间',
    write_memo: '记笔记',
    lock_pc: '锁屏',
    sing: '唱歌',
    look_screen: '看屏幕',
    mouse_move: '移鼠标',
    mouse_click: '点鼠标',
    mouse_scroll: '滚轮',
    mouse_drag: '拖拽',
    mouse_pos: '找鼠标',
    type_text: '打字',
    press_keys: '按快捷键',
    switch_persona: '换人格',
  };

  const micState = { active: false, mode: 'push', stream: null, ctx: null, node: null, src: null };

  function pcmToB64(i16) {
    const bytes = new Uint8Array(i16.buffer);
    let bin = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(bin);
  }

  async function startMic(mode) {
    if (micState.active) stopMic();
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
      setBubble('（还没连上，等一下再说话）', false, 2500);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const ctx = new AudioContext({ sampleRate: 16000 });
      await ctx.resume();
      const src = ctx.createMediaStreamSource(stream);
      const node = ctx.createScriptProcessor(2048, 1, 1);
      micState.active = true;
      micState.mode = mode;
      node.onaudioprocess = (ev) => {
        if (!micState.active) return;
        const f32 = ev.inputBuffer.getChannelData(0);
        const i16 = new Int16Array(f32.length);
        for (let i = 0; i < f32.length; i += 1) {
          const v = Math.max(-1, Math.min(1, f32[i]));
          i16[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
        }
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
          const payload = { type: 'audio_in', data: pcmToB64(i16), sampleRate: ctx.sampleRate };
          if (mode === 'wake') payload.mode = 'wake';
          state.ws.send(JSON.stringify(payload));
        }
      };
      src.connect(node);
      node.connect(ctx.destination);
      micState.stream = stream;
      micState.ctx = ctx;
      micState.node = node;
      micState.src = src;
      if (mode === 'wake') {
        state.ws.send(JSON.stringify({ type: 'wake_start', sampleRate: ctx.sampleRate }));
        els.handsfree.classList.add('handsfree-on');
        setBubble('免提开着啦～喊我「yuki」就行', false, 4000);
      } else {
        state.ws.send(JSON.stringify({ type: 'audio_start', sampleRate: ctx.sampleRate }));
        els.mic.classList.add('recording');
        setBubble('我在听…', false);
      }
      console.log('[nurow] mic started', mode, ctx.sampleRate);
    } catch (err) {
      console.warn('[nurow] mic failed', err);
      setBubble('（麦克风打不开：' + (err && err.message ? err.message : String(err)) + '）', false, 5000);
    }
  }

  function stopMic() {
    if (!micState.active) return;
    const mode = micState.mode;
    micState.active = false;
    try {
      if (micState.node) micState.node.disconnect();
      if (micState.src) micState.src.disconnect();
      if (micState.stream) micState.stream.getTracks().forEach((t) => t.stop());
      if (micState.ctx) micState.ctx.close();
    } catch (e) { /* ignore */ }
    micState.stream = null;
    micState.ctx = null;
    micState.node = null;
    micState.src = null;
    els.mic.classList.remove('recording');
    els.handsfree.classList.remove('handsfree-on');
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: mode === 'wake' ? 'wake_stop' : 'audio_end' }));
    }
    console.log('[nurow] mic stopped', mode);
  }

  function toggleMic() {
    if (micState.active && micState.mode === 'push') stopMic();
    else startMic('push');
  }

  function toggleHandsfree() {
    if (micState.active && micState.mode === 'wake') stopMic();
    else startMic('wake');
  }

  // ------------------------------------------------------------------- UI --
  function setBubble(text, thinking, hideAfter) {
    clearTimeout(state.bubbleTimer);
    els.bubbleText.textContent = text || '';
    els.bubble.classList.toggle('thinking', !!thinking && !text);
    if (!text && !thinking) {
      els.bubble.classList.remove('show');
      return;
    }
    els.bubble.classList.add('show');
    if (hideAfter) {
      state.bubbleTimer = setTimeout(() => els.bubble.classList.remove('show'), hideAfter);
    }
  }

  function setMuted(v) {
    state.muted = v;
    els.mute.textContent = v ? '🔇' : '🔊';
  }

  function setConnected(v) {
    state.connected = v;
    els.status.classList.toggle('on', v);
    els.status.title = v ? '已连接' : '未连接';
  }

  // -------------------------------------------------------------- 人格切换 --
  function applyPersona(msg) {
    const dark = (msg && msg.mode) === 'dark';
    document.body.classList.toggle('dark', dark);
    if (msg && msg.greeting) {
      setBubble(msg.greeting, false, 9000);
    }
    playTap();
    console.log('[nurow] persona:', msg && msg.mode, msg && msg.display_name);
  }

  function sendChat() {
    const text = (els.input.value || '').trim();
    if (!text) return;
    els.input.value = '';
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
      setBubble('（还没连上我的脑子…等一下）', false, 3000);
      return;
    }
    state.reply = '';
    setBubble('', true);
    state.ws.send(JSON.stringify({ type: 'chat', text }));
  }

  function handleMessage(msg) {
    if (!msg || !msg.type) return;
    switch (msg.type) {
      case 'hello':
        if (!state.greeted && msg.persona && msg.persona.greeting) {
          state.greeted = true;
          setBubble(msg.persona.greeting, false, 14000);
        }
        break;
      case 'start':
        state.reply = '';
        setBubble('', true);
        break;
      case 'delta':
        state.reply += msg.text || '';
        setBubble(state.reply, true);
        break;
      case 'done':
        setBubble(state.reply || '……', false, 9000);
        reactToReply(state.reply);
        if (Math.random() < 0.5) playTap();
        break;
      case 'audio':
        enqueueAudioB64(msg.data);
        break;
      case 'sing':
        setBubble('🎵 《' + (msg.song || '小星星') + '》\n' + (msg.lyrics || ''), false, 14000);
        enqueueAudioB64(msg.data);
        playTap();
        break;
      case 'stt_state':
        if (msg.state === 'listening') setBubble('我在听…', false);
        else if (msg.state === 'idle' && !state.reply) els.bubble.classList.remove('show');
        break;
      case 'wake':
        if (msg.state === 'armed') setBubble('免提开着：喊「yuki」叫我', false, 3000);
        else if (msg.state === 'triggered') { setBubble('我在～', false, 2500); playTap(); }
        else if (msg.state === 'idle') { /* 等她说话 */ }
        else if (msg.state === 'off') setBubble('免提关啦', false, 1500);
        break;
      case 'stt_partial':
        setBubble('🎤 ' + (msg.text || ''), false);
        break;
      case 'stt_final':
        setBubble('🎤 ' + (msg.text || ''), false, 1500);
        break;
      case 'user_text':
        setBubble('你：' + (msg.text || ''), false, 1600);
        break;
      case 'tool_call':
        setBubble('（' + (TOOL_LABELS[msg.name] || msg.name) + '…）', false, 2600);
        break;
      case 'persona':
        applyPersona(msg);
        break;
      case 'react_text':
        setBubble(msg.text || '', false, 3500);
        break;
      case 'settings_changed':
        setBubble('（设置更新啦）', false, 1500);
        setTimeout(() => window.location.reload(), 700);
        break;
      case 'info':
        setBubble(msg.text || '', false, 4000);
        break;
      case 'muted':
        setMuted(!!msg.value);
        break;
      default:
        break;
    }
  }

  // ------------------------------------------------------------------- WS --
  let reconnectTimer = 0;

  function connect() {
    clearTimeout(reconnectTimer);
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    let ws;
    try {
      ws = new WebSocket(proto + '://' + location.host + '/ws');
    } catch (e) {
      reconnectTimer = setTimeout(connect, 1500);
      return;
    }
    state.ws = ws;
    ws.onopen = () => { console.log('[nurow] ws open'); setConnected(true); };
    ws.onmessage = (ev) => {
      let msg = null;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type && msg.type !== 'delta' && msg.type !== 'audio') console.log('[nurow] ws <-', msg.type);
      handleMessage(msg);
    };
    ws.onclose = (e) => {
      console.log('[nurow] ws closed', e.code, e.reason || '');
      setConnected(false);
      reconnectTimer = setTimeout(connect, 1500);
    };
    ws.onerror = () => { console.log('[nurow] ws error'); try { ws.close(); } catch (e) { /* ignore */ } };
  }

  // ----------------------------------------------------------------- init --
  els.send.addEventListener('click', sendChat);
  els.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      sendChat();
    }
  });
  els.mute.addEventListener('click', () => {
    const value = !state.muted;
    setMuted(value);
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: 'mute', value }));
    }
  });
  els.mic.addEventListener('click', toggleMic);
  els.handsfree.addEventListener('click', toggleHandsfree);
  els.settings.addEventListener('click', () => {
    if (window.nurowDesktop && window.nurowDesktop.openSettings) window.nurowDesktop.openSettings();
  });
  window.addEventListener('resize', () => { if (state.model) layout(); });
  // 全局鼠标追踪：鼠标在屏幕任何地方，她的视线/头都会跟过去（库自带平滑）
  if (window.nurowDesktop && window.nurowDesktop.onCursor) {
    window.nurowDesktop.onCursor((pt) => {
      if (!state.model) return;
      try {
        if (state.idleGaze) state.model.focus(state.idleGaze.x, state.idleGaze.y);
        else state.model.focus(pt.x, pt.y);
      } catch (err) { /* ignore */ }
    });
  }

  // 空闲时的小动作：瞄一眼别处 / 歪头 / 轻轻弹一下
  function scheduleIdle() {
    setTimeout(() => {
      try {
        if (!state.model) { scheduleIdle(); return; }
        const r = Math.random();
        if (r < 0.45) {
          state.idleGaze = {
            x: Math.random() < 0.5 ? -160 : window.innerWidth + 160,
            y: window.innerHeight * (0.22 + Math.random() * 0.55),
          };
          setTimeout(() => { state.idleGaze = null; }, 650 + Math.random() * 550);
        } else if (r < 0.72) {
          const dir = Math.random() < 0.5 ? -1 : 1;
          state.idleTilt = dir * (0.008 + Math.random() * 0.018);
          setTimeout(() => { state.idleTilt = 0; }, 900 + Math.random() * 700);
        } else {
          state.bounce = { t: 0 };
        }
      } catch (e) { /* ignore */ }
      scheduleIdle();
    }, 5500 + Math.random() * 8000);
  }
  // 按住拖动窗口；松开时没怎么动就当作"点击"
  let dragStart = null;
  let dragMoved = false;
  els.petzone.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    dragStart = { x: e.screenX, y: e.screenY };
    dragMoved = false;
    if (window.nurowDesktop && window.nurowDesktop.startDrag) window.nurowDesktop.startDrag();
  });
  document.addEventListener('mousemove', (e) => {
    if (!dragStart) return;
    if (Math.hypot(e.screenX - dragStart.x, e.screenY - dragStart.y) > 6) {
      dragMoved = true;
      els.petzone.style.cursor = 'grabbing';
    }
  });
  document.addEventListener('mouseup', () => {
    if (!dragStart) return;
    dragStart = null;
    els.petzone.style.cursor = '';
    if (window.nurowDesktop && window.nurowDesktop.endDrag) window.nurowDesktop.endDrag();
  });
  // 点击不同部位 → 不同反应
  els.petzone.addEventListener('click', (e) => {
    if (dragMoved) { dragMoved = false; return; }
    const part = hitPart(e.clientX, e.clientY);
    console.log('[nurow] tap @', e.clientX, e.clientY, '->', part);
    if (!part) return;
    playExprForPart(part);
    state.bounce = { t: 0 };
    playTap();
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: 'react', part }));
    }
  });
  // 拉取表情中文名映射（点到部位时随机放对应表情）
  fetch('/api/model/expressions')
    .then((r) => r.json())
    .then((d) => { state.expressions = (d && d.expressions) || {}; })
    .catch(() => {});
  document.addEventListener('click', () => { try { ensureAudio(); } catch (e) { /* ignore */ } });

  initModel();
  scheduleIdle();
  connect();
})();
