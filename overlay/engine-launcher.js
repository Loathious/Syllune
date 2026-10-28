// Own the installed engine until it connects, fails or the shell quits.
// A local authenticated socket keeps startup visible and repeat launches in one
// GUI process. The child never inherits developer Python or Node dependencies.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');

async function startEngine(options) {
  const {executable, args = [], dataDir, shellExecutable, shellPid, overlayDir,
    onConnect, onFailure, timeoutMs = 45000} = options;
  fs.mkdirSync(dataDir, {recursive: true});
  const logPath = path.join(dataDir, 'launcher.log');
  if (fs.existsSync(logPath) && fs.statSync(logPath).size > 1000000) fs.renameSync(logPath, logPath + '.old');
  const log = text => fs.appendFileSync(logPath, `${new Date().toISOString()} ${text}\n`);
  const token = crypto.randomBytes(32).toString('hex');
  const server = net.createServer();
  const sockets = new Set();
  let child, connection, timer, stopped = false, failed = false;
  const stop = () => {
    stopped = true;
    clearTimeout(timer);
    server.close();
    for (const socket of sockets) socket.destroy();
    if (child && child.exitCode === null && !child.killed) child.kill();
  };
  const fail = error => {
    if (stopped || failed) return;
    failed = true;
    log(error.message);
    stop();
    onFailure(error);
  };
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  server.on('error', fail);
  server.on('connection', socket => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
    socket.on('error', () => socket.destroy());
    let received = '';
    const hello = chunk => {
      received += chunk.toString('utf8');
      if (received.length > 8192) { socket.destroy(); return; }
      const end = received.indexOf('\n');
      if (end < 0) return;
      let message;
      try { message = JSON.parse(received.slice(0, end)); } catch { socket.destroy(); return; }
      if (connection || message.type !== 'hello' || message.token !== token) { socket.destroy(); return; }
      connection = socket;
      clearTimeout(timer);
      socket.pause();
      socket.removeListener('data', hello);
      server.close();
      log('Engine connected');
      onConnect(socket, received.slice(end + 1));
      socket.resume();
    };
    socket.on('data', hello);
  });
  const env = {...process.env, ...options.env,
    SYLLUNE_UI_PORT: String(server.address().port), SYLLUNE_UI_TOKEN: token,
    SYLLUNE_UI_PID: String(shellPid), SYLLUNE_ELECTRON_EXE: shellExecutable,
    SYLLUNE_OVERLAY_DIR: overlayDir};
  // Bundled children must never be accidentally treated as Node or inherit a
  // host Python path. Nothing here changes the user's persistent environment.
  for (const key of ['ELECTRON_RUN_AS_NODE', 'NODE_OPTIONS', 'PYTHONHOME', 'PYTHONPATH']) delete env[key];
  try {
    child = spawn(executable, args, {cwd: path.dirname(executable), env,
      windowsHide: true, stdio: ['ignore', 'ignore', 'pipe']});
    // Engine console output can include dictated words. Keep diagnostics to
    // stderr and lifecycle events; the engine's normal log controls privacy.
    child.stderr.on('data', chunk => log(chunk.toString('utf8').trimEnd()));
    child.on('error', fail);
    child.on('exit', (code, signal) => fail(new Error(`The speech engine stopped (${code ?? signal}). Open logs for details, then retry.`)));
    timer = setTimeout(() => fail(new Error('The speech engine did not become ready. Open logs for details, then retry.')), timeoutMs);
    log('Starting bundled engine');
  } catch (error) { fail(error); }
  return {child, logPath, stop};
}

module.exports = {startEngine};
