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
    cursor: null,
    gx: 0,
    gy: 0,
    idleGaze: null,
    idleTilt: 0,
    curTilt: 0,
    idleBodyX: 0,
    idleBodyZ: 0,
    curBodyX: 0,
    curBodyZ: 0,
    talkEnergy: 0,
    curExpr: null,
    exprTimer: 0,
    persistExprs: [],
    exprDefs: {},
    exprDefsReady: false,
    tempExpr: null,
    idleLevel: 'normal',
    talkFlap: true,
    clickReact: true,
    gazeFollow: true,
    gazeSpeed: 'fast',
    idleActions: true,
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
    head: ['星星眼', '脸红', '爱心眼'],
    face: ['脸红', '吐舌', '泪眼汪汪'],
    body: ['生气', '眩晕', '死鱼眼'],
    legs: ['哭哭', '生气', '脸红'],
    tail: ['爱心眼', '吐舌', '星星眼'],
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

  const EXPR_ALIASES = {
    tongue: ['吐舌'],
    star: ['星星眼'],
    heart: ['爱心眼'],
    cry: ['哭哭'],
    teary: ['泪眼汪汪'],
    dead: ['死鱼眼'],
    angry: ['眼-生气'],
    dizzy: ['眩晕'],
    blush: ['脸红'],
    dark: ['脸黑'],
    calm: ['平静v形'],
    glasses: ['眼镜'],
    cat: ['抱猫'],
    scarf: ['围巾'],
    sweater: ['毛衣'],
    hat: ['帽子隐藏'],
    ears: ['兽耳-隐藏'],
    tail: ['兽尾-隐藏1'],
    eyeshadow: ['眼影隐藏'],
    fringe: ['前发-去掉半透'],
    bun: ['后发-右小啾啾隐藏'],
    longhair: ['后发-长发隐藏'],
  };
  const exprCache = {};

  function refreshExprCache() {
    const map = state.expressions || {};
    Object.keys(EXPR_ALIASES).forEach((key) => {
      exprCache[key] = null;
      for (const id of Object.keys(map)) {
        const name = String(map[id] || '');
        if (EXPR_ALIASES[key].some((sub) => name.indexOf(sub) >= 0)) { exprCache[key] = id; break; }
      }
    });
    console.log('[nurow] expr cache ready:', Object.keys(exprCache).filter((k) => exprCache[k]).join(','));
  }

  function playExpression(id, holdMs) {
    const m = state.model;
    if (!m || !id) return;
    if (state.exprDefsReady) {
      state.tempExpr = { id, until: performance.now() + (holdMs || 1600) };
      return;
    }
    try { m.expression(id); } catch (e) { return; }
    state.curExpr = id;
    clearTimeout(state.exprTimer);
    if (holdMs > 0) {
      state.exprTimer = setTimeout(() => {
        if (!state.model) return;
        try { state.model.expression(); state.curExpr = null; } catch (e) { /* ignore */ }
      }, holdMs);
    }
  }

  // 读取所有 exp3 表情定义（用于多选叠加）
  async function loadExpressionDefs() {
    try {
      const cfg = state.cfg;
      if (!cfg || !cfg.url) return;
      const model3 = await (await fetch(cfg.url)).json();
      const list = (model3.FileReferences && model3.FileReferences.Expressions) || [];
      const base = cfg.url.slice(0, cfg.url.lastIndexOf('/') + 1);
      const defs = {};
      await Promise.all(list.map(async (e) => {
        try {
          const d = await (await fetch(base + e.File)).json();
          defs[e.Name] = d;
        } catch (err) { /* ignore */ }
      }));
      state.exprDefs = defs;
      state.exprDefsReady = Object.keys(defs).length > 0;
      console.log('[nurow] expression defs loaded:', Object.keys(defs).length,
        '| persist:', state.persistExprs.join(',') || 'none');
    } catch (e) { console.warn('[nurow] expr defs failed', e); }
  }

  // 每帧把常驻 + 临时表情叠加到模型参数上（在模型动画更新之后执行）
  function applyExpressionOverrides(core) {
    if (!state.exprDefsReady) return;
    const now = performance.now();
    const active = (state.persistExprs || []).slice();
    if (state.tempExpr) {
      if (now < state.tempExpr.until) active.push(state.tempExpr.id);
      else state.tempExpr = null;
    }
    for (const id of active) {
      const def = state.exprDefs[id];
      if (!def) continue;
      (def.Parameters || []).forEach((p) => {
        if (!p || !p.Id) return;
        const v = Number(p.Value) || 0;
        let cur = 0;
        try { cur = core.getParameterValueById(p.Id); } catch (e) { return; }
        let nv = v;
        if (p.Blend === 'Multiply') nv = cur * v;
        else if (p.Blend === 'Overwrite') nv = v;
        else nv = cur + v;
        try { core.setParameterValueById(p.Id, nv); } catch (e) { /* ignore */ }
      });
      (def.Parts || []).forEach((pt) => {
        if (!pt || !pt.Id) return;
        try { core.setPartOpacityById(pt.Id, Number(pt.Opacity) || 0); } catch (e) { /* ignore */ }
      });
    }
  }

  function playExprByKeywords(kws, holdMs) {
    const map = state.expressions || {};
    const ids = Object.keys(map).filter((id) => kws.some((k) => String(map[id]).indexOf(k) >= 0));
    if (!ids.length) return;
    const pick = ids[Math.floor(Math.random() * ids.length)];
    playExpression(pick, holdMs || 2200);
    console.log('[nurow] expression', pick, map[pick]);
  }

  function playExprForPart(part) {
    playExprByKeywords(PART_EXPR_KEYWORDS[part] || []);
  }

  // 根据她回复的情绪做个表情，显得有反应
  function reactToReply(text) {
    const t = String(text || '');
    let kws = null;
    let hold = 2400;
    if (/哼|才不|讨厌|笨蛋|傻瓜|不理你/.test(t)) { kws = ['生气', '脸红', '吐舌']; hold = 1800; }
    else if (/嘿嘿|好耶|开心|喜欢|最喜欢|真棒|爱你/.test(t)) kws = ['星星眼', '爱心眼', '脸红'];
    else if (/呜|哭|委屈|难过|伤心/.test(t)) { kws = ['哭哭', '泪眼汪汪']; hold = 3000; }
    else if (/害羞|脸红|羞/.test(t)) kws = ['脸红', '吐舌'];
    else if (/困|累|睡觉|睡了/.test(t)) { kws = ['死鱼眼', '平静v形']; hold = 3000; }
    else if (/惊|吓|真的假的|天呐/.test(t)) { kws = ['眩晕']; hold = 1800; }
    else if (/无语|呵呵|随便/.test(t)) kws = ['死鱼眼'];
    if (kws) {
      playExprByKeywords(kws, hold);
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

  const TICK = { last: 0 };

  function proceduralTick() {
    const m = state.model;
    if (!m) return;
    const now = performance.now();
    const dt = Math.min(0.1, (now - (TICK.last || now)) / 1000);
    TICK.last = now;
    const t = now / 1000;

    // 被点之后轻轻弹一下
    if (state.bounce) {
      state.bounce.t += dt * 1000;
      const p = Math.min(1, state.bounce.t / 420);
      m.y = state.baseY - Math.sin(p * Math.PI) * 9;
      if (p >= 1) {
        m.y = state.baseY;
        state.bounce = null;
      }
    } else if (state.gazeSpeed === 'slow') {
      // 原版手感：不加额外浮动，身体动作完全交给模型自带 Idle
      m.y = state.baseY;
    } else {
      // 呼吸上下微动 + 说话时的小点头
      const talkBob = (state.talkEnergy || 0) * Math.sin(t * 9) * 1.2;
      m.y = state.baseY + Math.sin(t * 1.6) * 0.6 + talkBob;
    }

    // 歪头（灵敏=按时间平滑；柔和=原版逐帧）
    if (state.gazeSpeed === 'slow') {
      state.curTilt += (state.idleTilt - state.curTilt) * 0.12;
    } else {
      const tiltK = 1 - Math.exp(-dt / 0.12);
      state.curTilt += (state.idleTilt - state.curTilt) * tiltK;
    }
    m.rotation = Math.abs(state.curTilt) > 0.0005 ? state.curTilt : 0;

    // 身体重心缓慢换边（~350ms 平滑）
    const bodyK = 1 - Math.exp(-dt / 0.35);
    state.curBodyX += (state.idleBodyX - state.curBodyX) * bodyK;
    state.curBodyZ += (state.idleBodyZ - state.curBodyZ) * bodyK;

    // 视线/头部跟随：自己算，响应更快（约 60~80ms 到位）
    // 柔和模式（原版）不接管这些参数，交给 focus
    let gtx = 0;
    let gty = 0;
    if (state.gazeFollow) {
      const src = state.idleGaze || state.cursor;
      if (src) {
        gtx = Math.max(-1, Math.min(1, (src.x - window.innerWidth / 2) / (window.innerWidth * 0.55)));
        gty = Math.max(-1, Math.min(1, (src.y - window.innerHeight * 0.42) / (window.innerHeight * 0.60)));
      }
    }
    // 鼠标跟随时很快；自己发呆乱看时更慢更柔
    // 柔和模式 = 最初版体验（库默认约 0.5s 平滑）
    const slowMode = state.gazeSpeed === 'slow';
    const followTau = slowMode ? 0.50 : 0.09;
    const glanceTau = slowMode ? 0.55 : 0.22;
    const gk = 1 - Math.exp(-dt / (state.idleGaze ? glanceTau : followTau));
    state.gx += (gtx - state.gx) * gk;
    state.gy += (gty - state.gy) * gk;

    // 口型：TTS 音量 + 文字流"说话"的嘴动
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
    state.talkEnergy = (state.talkEnergy || 0) * Math.exp(-dt / 0.5);
    const flap = state.talkFlap
      ? state.talkEnergy * (0.18 + 0.62 * Math.abs(Math.sin(t * 12 + Math.sin(t * 5) * 2.5)))
      : 0;
    const target = Math.max(amp, flap);
    if (state.gazeSpeed === 'slow') {
      state.mouth += (target - state.mouth) * (target > state.mouth ? 0.62 : 0.2);
    } else {
      const mouthK = 1 - Math.exp(-dt * (target > state.mouth ? 20 : 8));
      state.mouth += (target - state.mouth) * mouthK;
    }

    try {
      const core = m.internalModel.coreModel;
      applyExpressionOverrides(core);
      // 在模型自带 Idle 动作参数的基础上叠加，避免动作被完全覆盖后显得"僵"
      const readP = (name, fallback) => {
        try {
          const v = core.getParameterValueById(name);
          return typeof v === 'number' && isFinite(v) ? v : fallback;
        } catch (e) { return fallback; }
      };
      setParam(core, 'ParamMouthOpenY', 'PARAM_MOUTH_OPEN_Y', Math.min(1, readP('ParamMouthOpenY', 0) * 0.35 + state.mouth));
      setParam(core, 'ParamMouthForm', 'PARAM_MOUTH_FORM', readP('ParamMouthForm', 0) * 0.6 + 0.15 + state.mouth * 0.35);
      if (state.gazeSpeed !== 'slow') {
        setParam(core, 'ParamAngleX', 'PARAM_ANGLE_X', readP('ParamAngleX', 0) * 0.55 + state.gx * 20);
        setParam(core, 'ParamAngleY', 'PARAM_ANGLE_Y', readP('ParamAngleY', 0) * 0.55 - state.gy * 18);
        setParam(core, 'ParamAngleZ', 'PARAM_ANGLE_Z', readP('ParamAngleZ', 0) * 0.55 - state.gx * 4);
        setParam(core, 'ParamEyeBallX', 'PARAM_EYE_BALL_X', state.gx);
        setParam(core, 'ParamEyeBallY', 'PARAM_EYE_BALL_Y', -state.gy);
        setParam(core, 'ParamBodyAngleX', 'PARAM_BODY_ANGLE_X', readP('ParamBodyAngleX', 0) * 0.5 + state.curBodyX + state.gx * 5);
        setParam(core, 'ParamBodyAngleZ', 'PARAM_BODY_ANGLE_Z', readP('ParamBodyAngleZ', 0) * 0.5 + state.curBodyZ);
        setParam(core, 'ParamBreath', 'PARAM_BREATH', readP('ParamBreath', 0.5) * 0.65 + 0.35 * (0.5 + 0.5 * Math.sin(t * 1.35)));
      }
    } catch (e) { /* ignore */ }
  }

  // 确定性做法：挂在 internalModel.update 之后执行，保证覆盖模型动画参数
  let proceduralRegistered = false;
  function registerProceduralTick() {
    if (proceduralRegistered) return;
    const model = state.model;
    if (!model || !model.internalModel) return;
    proceduralRegistered = true;
    const im = model.internalModel;
    const origUpdate = im.update.bind(im);
    im.update = function (dt, now) {
      const r = origUpdate(dt, now);
      try { proceduralTick(); } catch (e) { /* ignore */ }
      return r;
    };
    console.log('[nurow] procedural hook installed');
  }

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

  async function loadAppearanceSettings() {
    try {
      const d = await (await fetch('/api/settings')).json();
      const c = (d && d.config) || {};
      state.persistExprs = Array.isArray(c.yuki_expressions)
        ? c.yuki_expressions.map(String).filter((x) => /^expr_\d{2}$/.test(x)) : [];
      // 兼容旧版「换装」单选：如果旧配置里有，自动迁移成对应表情
      const LEGACY_OUTFIT = {
        scarf: 'expr_32', sweater: 'expr_33', cat: 'expr_12', glasses: 'expr_22',
        ears: 'expr_03', tail: 'expr_01', hat: 'expr_10', eyeshadow: 'expr_23',
        fringe: 'expr_04', bun: 'expr_05', longhair: 'expr_07',
      };
      if (!state.persistExprs.length && c.yuki_outfit && LEGACY_OUTFIT[c.yuki_outfit]) {
        state.persistExprs = [LEGACY_OUTFIT[c.yuki_outfit]];
        console.log('[nurow] migrated legacy outfit:', c.yuki_outfit);
      }
      state.idleLevel = String(c.idle_level || 'normal');
      state.talkFlap = c.talk_flap !== false;
      state.clickReact = c.click_react !== false;
      state.gazeFollow = c.gaze_follow !== false;
      state.gazeSpeed = c.gaze_speed === 'slow' ? 'slow' : 'fast';
      state.idleActions = c.idle_actions !== false;
      console.log('[nurow] appearance:', state.persistExprs.join(',') || 'default', state.idleLevel, 'flap:', state.talkFlap);
    } catch (e) { /* 用默认值 */ }
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
      registerProceduralTick();
      loadExpressionDefs();
      try {
        const core = model.internalModel.coreModel;
        const ids = (core._parameterIds || []).filter((id) => /Mouth|Breath|BodyAngle/i.test(id));
        console.log('[nurow] params:', ids.join(','));
      } catch (e) { /* ignore */ }
      if (Object.keys(state.expressions || {}).length) refreshExprCache();
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
        state.talkEnergy = 0.35;
        setBubble('', true);
        break;
      case 'delta':
        state.reply += msg.text || '';
        state.talkEnergy = 1.0;
        setBubble(state.reply, true);
        break;
      case 'done':
        state.talkEnergy = 0.1;
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
        else if (msg.state === 'triggered') { setBubble('我在～', false, 2500); playTap(); playExprByKeywords(['星星眼'], 1600); }
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
      state.cursor = { x: pt.x, y: pt.y };
      if (state.gazeSpeed === 'slow') {
        // 原版行为：交给 Live2D 库自带的 focus 平滑
        try {
          if (state.idleGaze) state.model.focus(state.idleGaze.x, state.idleGaze.y);
          else state.model.focus(pt.x, pt.y);
        } catch (err) { /* ignore */ }
      }
    });
  }

  // 空闲时的小动作：四处看 / 歪头 / 换重心 / 小表情 / 小动作 / 偶尔换装
  const IDLE_EXPR_POOL = ['tongue', 'star', 'heart', 'teary', 'dead', 'dizzy', 'blush', 'angry', 'glasses', 'calm'];

  function runIdleAction() {
    const r = Math.random();
    if (r < 0.30) {
      // 瞄一眼别处
      state.idleGaze = {
        x: Math.random() < 0.5 ? -220 : window.innerWidth + 220,
        y: window.innerHeight * (0.22 + Math.random() * 0.55),
      };
      setTimeout(() => { state.idleGaze = null; }, 500 + Math.random() * 900);
    } else if (r < 0.46) {
      // 歪头
      const dir = Math.random() < 0.5 ? -1 : 1;
      state.idleTilt = dir * (0.010 + Math.random() * 0.022);
      setTimeout(() => { state.idleTilt = 0; }, 900 + Math.random() * 900);
    } else if (r < 0.62) {
      // 重心换到另一边
      const dir = Math.random() < 0.5 ? -1 : 1;
      state.idleBodyX = dir * (5 + Math.random() * 8);
      state.idleBodyZ = dir * (2 + Math.random() * 4);
      setTimeout(() => { state.idleBodyX = 0; state.idleBodyZ = 0; }, 2200 + Math.random() * 2600);
    } else if (r < 0.84) {
      const key = IDLE_EXPR_POOL[Math.floor(Math.random() * IDLE_EXPR_POOL.length)];
      const id = exprCache[key];
      if (id) playExpression(id, 1200 + Math.random() * 1600);
    } else if (r < 0.93) {
      // 小动作（伸伸身子）
      playTap();
      if (Math.random() < 0.5) state.bounce = { t: 0 };
    } else {
      // 轻轻跳一下 / 伸个懒腰
      state.bounce = { t: 0 };
      if (Math.random() < 0.5) playTap();
    }
  }

  function scheduleIdle() {
    const lv = state.idleLevel || 'normal';
    const base = lv === 'quiet' ? 7000 : lv === 'lively' ? 1400 : 2400;
    const span = lv === 'quiet' ? 9000 : lv === 'lively' ? 2600 : 5200;
    setTimeout(() => {
      try {
        if (state.model && state.idleActions) runIdleAction();
      } catch (e) { /* ignore */ }
      scheduleIdle();
    }, base + Math.random() * span);
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
    if (!state.clickReact) return;
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
    .then((d) => {
      state.expressions = (d && d.expressions) || {};
      refreshExprCache();
    })
    .catch(() => {});
  // ------------------------------------------------------------ 右键菜单 --
  let ctxMenu = null;
  function hideCtxMenu() {
    if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; }
  }

  function showCtxMenu(x, y) {
    hideCtxMenu();
    const menu = document.createElement('div');
    menu.id = 'ctx-menu';
    Object.assign(menu.style, {
      position: 'fixed', left: x + 'px', top: y + 'px', zIndex: 999,
      minWidth: '190px', maxHeight: Math.max(200, window.innerHeight - y - 16) + 'px', overflowY: 'auto',
      background: 'rgba(24,27,44,0.97)', border: '1px solid rgba(255,255,255,0.16)',
      borderRadius: '12px', padding: '6px', boxShadow: '0 10px 30px rgba(0,0,0,0.45)',
      fontSize: '13px', color: '#e8eaf6', userSelect: 'none',
      fontFamily: '"Microsoft YaHei UI","Segoe UI",sans-serif',
    });
    const map = state.expressions || {};
    const sep = () => {
      const d = document.createElement('div');
      Object.assign(d.style, { height: '1px', background: 'rgba(255,255,255,0.12)', margin: '5px 4px' });
      menu.appendChild(d);
    };
    const addItem = (label, fn, danger) => {
      const it = document.createElement('div');
      it.textContent = label;
      Object.assign(it.style, { padding: '7px 10px', borderRadius: '8px', cursor: 'pointer', whiteSpace: 'nowrap' });
      if (danger) it.style.color = '#ff9db4';
      it.addEventListener('mouseenter', () => { it.style.background = 'rgba(120,130,255,0.18)'; });
      it.addEventListener('mouseleave', () => { it.style.background = 'transparent'; });
      it.addEventListener('click', (e) => { e.stopPropagation(); hideCtxMenu(); try { fn(); } catch (err) { /* ignore */ } });
      menu.appendChild(it);
      return it;
    };
    const desk = window.nurowDesktop || {};
    addItem('设置…', () => desk.openSettings && desk.openSettings());
    addItem('重置位置', () => desk.resetPosition && desk.resetPosition());
    sep();
    const head = document.createElement('div');
    head.textContent = '快捷表情（点一下显示 5 秒）';
    Object.assign(head.style, { padding: '4px 10px', fontSize: '11px', opacity: '0.62' });
    menu.appendChild(head);
    const ids = Object.keys(map);
    if (!ids.length) {
      const d = document.createElement('div');
      d.textContent = '（表情还没加载好）';
      Object.assign(d.style, { padding: '4px 10px', fontSize: '12px', opacity: '0.5' });
      menu.appendChild(d);
    } else {
      ids.forEach((id) => addItem(map[id] || id, () => playExpression(id, 5000)));
    }
    sep();
    addItem('隐藏 yuki（托盘图标可恢复）', () => desk.hideWindow && desk.hideWindow());
    addItem('退出', () => desk.quit && desk.quit(), true);
    document.body.appendChild(menu);
    ctxMenu = menu;
  }

  els.petzone.addEventListener('contextmenu', (e) => {
    if (e.target && e.target.closest && e.target.closest('#panel')) return;
    e.preventDefault();
    showCtxMenu(e.clientX, e.clientY);
  });
  document.addEventListener('click', hideCtxMenu);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hideCtxMenu();
    if (e.key === 'F2' && window.nurowDesktop && window.nurowDesktop.capturePet) window.nurowDesktop.capturePet();
  });

  document.addEventListener('click', () => { try { ensureAudio(); } catch (e) { /* ignore */ } });

  loadAppearanceSettings().then(() => { initModel(); }).catch(() => { initModel(); });
  scheduleIdle();
  connect();
})();
