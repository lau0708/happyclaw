#!/usr/bin/env node
/**
 * supervise.mjs — 零依赖进程守护（演示/长期挂机用）
 *
 * 为什么需要它：PulseOps 要同时跑四个进程（后端 3000 / 前端 5173 / MES API 8002 /
 * 工况模拟器）。手工起四个终端窗口的问题不是麻烦，而是**谁挂了没人知道**：
 * 演示中途模拟器退出，页面上曲线就停在那儿不动；后端 OOM 被杀，飞书就静默失联。
 *
 * 本脚本做的事，只有三件：
 *   1. 托管一组子进程，退出即按指数退避重启（1s → 2s → … → 60s，稳定后归零）
 *   2. 对带健康探针的服务定时探活，连续失败视为僵死，主动杀掉交给重启逻辑
 *   3. 收到 SIGINT/SIGTERM 时按进程组整体优雅退出，不留孤儿
 *
 * 用法：
 *   node scripts/supervise.mjs               # 全部服务（后端+前端+MES+模拟器）
 *   node scripts/supervise.mjs backend web   # 只跑指定服务
 *   node scripts/supervise.mjs --list        # 列出可用服务名
 *
 * 日志：logs/<name>.log（追加写），同时按 `时间 [name] 行` 前缀打到 stdout。
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LOG_DIR = path.join(ROOT, 'logs');
const PID_FILE = path.join(LOG_DIR, 'supervise.pid');

const WEB_PORT = process.env.WEB_PORT || '3000';
const FRONTEND_PORT = process.env.FRONTEND_PORT || '5173';
const MES_PORT = process.env.MES_PORT || '8002';

/** 服务清单。health 为 HTTP 探针（127.0.0.1，绕过系统代理）。 */
const SERVICES = [
  {
    name: 'backend',
    label: '后端',
    cmd: 'npx',
    args: ['tsx', 'src/index.ts'],
    cwd: ROOT,
    health: `http://127.0.0.1:${WEB_PORT}/api/health`,
  },
  {
    name: 'web',
    label: '前端',
    cmd: 'npm',
    args: ['run', 'dev'],
    cwd: path.join(ROOT, 'web'),
    health: `http://127.0.0.1:${FRONTEND_PORT}/`,
    // vite 首次冷启动要预构建依赖，给它更长的宽限
    startGraceMs: 60_000,
  },
  {
    name: 'mes-api',
    label: 'MES API',
    cmd: 'python3',
    // -u：不走 stdout 块缓冲，启动横幅和异常能立刻出现在日志里
    args: ['-u', 'api.py', '--strict-port'],
    cwd: path.join(ROOT, 'mes-mcp-server'),
    health: `http://127.0.0.1:${MES_PORT}/api/equipment`,
    startGraceMs: 8_000,
  },
  {
    name: 'mes-sim',
    label: '工况模拟器',
    cmd: 'python3',
    args: [
      '-u',
      'simulator.py',
      '--backfill',
      '24',
      '--backfill-interval',
      '300',
      '--interval',
      '10',
    ],
    cwd: path.join(ROOT, 'mes-mcp-server'),
    // 常驻采样进程，无对外端口，只做进程存活监护
    health: null,
    // 回填 24h 历史要几秒，别在回填期判活
    startGraceMs: 30_000,
    // 模拟器一启动就往外报，API 没起来会连着刷 502 并触发退避重启
    dependsOn: 'mes-api',
  },
];

const args = process.argv.slice(2);
if (args.includes('--list')) {
  for (const s of SERVICES) console.log(`${s.name.padEnd(10)} ${s.label}`);
  process.exit(0);
}
const requested = args.filter((a) => !a.startsWith('-'));
const selected = requested.length
  ? SERVICES.filter((s) => requested.includes(s.name))
  : SERVICES;
if (!selected.length) {
  console.error(`未匹配到服务：${requested.join(', ')}（可用：${SERVICES.map((s) => s.name).join(', ')}）`);
  process.exit(2);
}

fs.mkdirSync(LOG_DIR, { recursive: true });
fs.writeFileSync(PID_FILE, `${process.pid}\n`);

const PROBE_INTERVAL_MS = 5_000;
const PROBE_TIMEOUT_MS = 4_000;
const PROBE_FAILURES_BEFORE_KILL = 3;
const BACKOFF_START_MS = 1_000;
const BACKOFF_MAX_MS = 60_000;
const STABLE_UPTIME_MS = 60_000;

/** @type {Map<string, {svc: object, proc: import('node:child_process').ChildProcess|null, restarts: number, backoffMs: number, startedAt: number, stopping: boolean, probeFailures: number, timer: NodeJS.Timeout|null, healthy: boolean}>} */
const runtime = new Map();

let shuttingDown = false;

const LOOPBACK_NO_PROXY = ['127.0.0.1', 'localhost', '::1'];

/** 把回环地址并入 NO_PROXY，避免本地服务被宿主代理截成 502。 */
function cleanNoProxy(existing) {
  const parts = String(existing || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  for (const host of LOOPBACK_NO_PROXY) {
    if (!parts.includes(host)) parts.push(host);
  }
  return parts.join(',');
}

function stamp() {
  return new Date().toISOString().replace('T', ' ').slice(0, 23);
}

function log(name, line) {
  const text = `${stamp()} [${name}] ${line}`;
  console.log(text);
  try {
    fs.appendFileSync(path.join(LOG_DIR, `${name}.log`), `${text}\n`);
  } catch {
    /* 日志写失败不能反过来拖垮守护进程 */
  }
}

function logSystem(line) {
  console.log(`${stamp()} [supervise] ${line}`);
}

function probe(url) {
  return new Promise((resolve) => {
    const req = http.get(
      url,
      { timeout: PROBE_TIMEOUT_MS, agent: false, headers: { 'User-Agent': 'pulseops-supervise' } },
      (res) => {
        res.resume();
        resolve(res.statusCode !== undefined && res.statusCode < 500);
      },
    );
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
    req.on('error', () => resolve(false));
  });
}

function killGroup(state, signal) {
  const pid = state.proc?.pid;
  if (!pid) return;
  try {
    // 子进程以 detached 启动，独占一个进程组；负号 pid 才能连 npm/npx 外壳一起收掉
    process.kill(-pid, signal);
  } catch {
    try {
      process.kill(pid, signal);
    } catch {
      /* 已退出 */
    }
  }
}

function scheduleRestart(state, reason) {
  if (shuttingDown || state.stopping) return;
  state.restarts += 1;
  const delay = state.backoffMs;
  state.backoffMs = Math.min(state.backoffMs * 2, BACKOFF_MAX_MS);
  log(
    state.svc.name,
    `${reason}，${Math.round(delay / 1000)}s 后重启（累计第 ${state.restarts} 次）`,
  );
  // 这里刻意不 unref：子进程全挂掉时，只有这个定时器还在把事件循环撑住，
  // unref 掉会让守护进程跟着一起退出，重启逻辑就永远等不到了。
  state.timer = setTimeout(() => {
    state.timer = null;
    start(state);
  }, delay);
}

function start(state) {
  if (shuttingDown) return;
  const { svc } = state;

  // 依赖门控：模拟器一启动就往外报指标，MES API 没起来就会连续 502 并触发退避。
  // 这里等依赖项探活通过再拉起，避免开机时白刷几轮重启。
  if (svc.dependsOn) {
    const dep = runtime.get(svc.dependsOn);
    if (dep && dep.proc && !dep.healthy) {
      if (!state.waitingLogged) {
        log(svc.name, `等待依赖 ${svc.dependsOn} 就绪后再启动`);
        state.waitingLogged = true;
      }
      state.timer = setTimeout(() => {
        state.timer = null;
        start(state);
      }, 2_000);
      return;
    }
  }
  state.waitingLogged = false;

  const child = spawn(svc.cmd, svc.args, {
    cwd: svc.cwd,
    // 宿主若配置了 HTTP_PROXY，子进程会把 127.0.0.1 也塞进代理，拿到 502。
    // 本地服务一律绕过代理，与 `curl --noproxy '*'` 同一口径。
    env: {
      ...process.env,
      NO_PROXY: cleanNoProxy(process.env.NO_PROXY || process.env.no_proxy),
      no_proxy: cleanNoProxy(process.env.no_proxy || process.env.NO_PROXY),
    },
    detached: true, // 独占进程组，便于整体收掉 npm/npx/tsx 这几层
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  state.proc = child;
  state.startedAt = Date.now();
  state.probeFailures = 0;
  state.healthy = false;
  log(svc.name, `启动 ${svc.cmd} ${svc.args.join(' ')} (pid ${child.pid})`);

  const pipe = (stream, level) => {
    let buf = '';
    stream.setEncoding('utf8');
    stream.on('data', (chunk) => {
      buf += chunk;
      let idx;
      while ((idx = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, idx).replace(/\r$/, '');
        buf = buf.slice(idx + 1);
        if (line.trim()) log(svc.name, level ? `! ${line}` : line);
      }
      if (buf.length > 8192) {
        log(svc.name, `! ${buf.slice(0, 8192)}`);
        buf = '';
      }
    });
  };
  pipe(child.stdout, false);
  pipe(child.stderr, true);

  child.on('exit', (code, signal) => {
    const uptime = Date.now() - state.startedAt;
    state.proc = null;
    if (state.stopping || shuttingDown) {
      log(svc.name, `已停止（code=${code ?? '-'} signal=${signal ?? '-'}）`);
      return;
    }
    if (uptime > STABLE_UPTIME_MS) state.backoffMs = BACKOFF_START_MS;
    scheduleRestart(
      state,
      `进程退出（code=${code ?? '-'} signal=${signal ?? '-'}，存活 ${Math.round(uptime / 1000)}s）`,
    );
  });

  child.on('error', (err) => {
    state.proc = null;
    scheduleRestart(state, `启动失败：${err.message}`);
  });
}

async function watchdog() {
  for (const state of runtime.values()) {
    if (shuttingDown || state.stopping || !state.proc) continue;
    const grace = state.svc.startGraceMs ?? 20_000;
    if (Date.now() - state.startedAt < grace) continue;
    if (!state.svc.health) {
      state.healthy = true;
      continue;
    }
    const ok = await probe(state.svc.health);
    if (ok) {
      state.probeFailures = 0;
      if (!state.healthy) {
        state.healthy = true;
        log(state.svc.name, `健康检查通过 ${state.svc.health}`);
      }
      continue;
    }
    state.probeFailures += 1;
    state.healthy = false;
    log(
      state.svc.name,
      `健康检查失败 ${state.probeFailures}/${PROBE_FAILURES_BEFORE_KILL}：${state.svc.health}`,
    );
    if (state.probeFailures >= PROBE_FAILURES_BEFORE_KILL) {
      state.stopping = true;
      log(state.svc.name, '连续探活失败，判定僵死，强制重启');
      killGroup(state, 'SIGKILL');
      state.stopping = false;
      // exit 回调会接着走退避重启，这里只负责把僵死的进程打死
    }
  }
}

function shutdown(reason) {
  if (shuttingDown) return;
  shuttingDown = true;
  logSystem(`收到 ${reason}，正在停止 ${runtime.size} 个服务...`);
  for (const state of runtime.values()) {
    state.stopping = true;
    if (state.timer) {
      clearTimeout(state.timer);
      state.timer = null;
    }
    killGroup(state, 'SIGTERM');
  }
  const deadline = Date.now() + 8_000;
  const tick = setInterval(() => {
    const alive = [...runtime.values()].filter((s) => s.proc);
    if (!alive.length || Date.now() > deadline) {
      clearInterval(tick);
      for (const s of alive) killGroup(s, 'SIGKILL');
      try {
        fs.unlinkSync(PID_FILE);
      } catch {
        /* ignore */
      }
      logSystem('已全部停止');
      process.exit(0);
    }
  }, 200);
  tick.unref?.();
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('uncaughtException', (err) => {
  logSystem(`守护进程异常：${err?.stack || err}`);
});

logSystem(`工作目录 ${ROOT}`);
logSystem(`托管服务：${selected.map((s) => s.name).join(', ')}`);
logSystem(`日志目录：${LOG_DIR}`);

for (const svc of selected) {
  const state = {
    svc,
    proc: null,
    restarts: 0,
    backoffMs: BACKOFF_START_MS,
    startedAt: 0,
    stopping: false,
    probeFailures: 0,
    timer: null,
    healthy: false,
    waitingLogged: false,
  };
  runtime.set(svc.name, state);
  start(state);
}

setInterval(() => {
  watchdog().catch((err) => logSystem(`探活异常：${err?.message || err}`));
}, PROBE_INTERVAL_MS);
