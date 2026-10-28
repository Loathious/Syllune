// Launch real child processes against the startup transport, without Electron.
// Catch lost handshakes, silent child exits and leaked children on app shutdown.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {startEngine} = require('../engine-launcher');

function options(t, script, extra = {}) {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'syllune-launch-'));
  t.after(() => fs.rmSync(folder, {recursive: true, force: true}));
  return {executable: process.execPath, args: ['-e', script], dataDir: folder,
    shellExecutable: process.execPath, shellPid: process.pid, overlayDir: folder,
    timeoutMs: 5000, ...extra};
}

test('engine handshake preserves the first config message and stop ends only its child', async t => {
  let resolveConnection;
  const connected = new Promise(resolve => { resolveConnection = resolve; });
  const runtime = await startEngine(options(t, `
    const net = require('node:net');
    const socket = net.connect(Number(process.env.SYLLUNE_UI_PORT), '127.0.0.1', () => {
      socket.write(JSON.stringify({type:'hello', token:process.env.SYLLUNE_UI_TOKEN})+'\\n'+
        JSON.stringify({type:'config', position:'middle-left'})+'\\n');
    });
    setInterval(()=>{},1000);
  `, {onConnect(socket, buffered) { resolveConnection({socket, buffered}); },
    onFailure(error) { throw error; }}));
  t.after(() => runtime.stop());
  const {socket, buffered} = await connected;
  assert.equal(JSON.parse(buffered.trim()).position, 'middle-left');
  const exited = new Promise(resolve => runtime.child.once('exit', resolve));
  runtime.stop();
  await exited;
  assert.equal(socket.destroyed, true);
  assert.doesNotThrow(() => process.kill(process.pid, 0));
});

test('a startup crash is reported with an exit code and retained diagnostics', async t => {
  let failure;
  const failed = new Promise(resolve => { failure = resolve; });
  const runtime = await startEngine(options(t, `console.error('missing runtime dependency');process.exit(23)`,
    {onConnect() { assert.fail('crashed engine connected'); }, onFailure: failure}));
  t.after(() => runtime.stop());
  assert.match((await failed).message, /23/);
  assert.match(fs.readFileSync(runtime.logPath, 'utf8'), /missing runtime dependency/);
});

test('ordinary console output is not retained in startup diagnostics', async t => {
  let failure;
  const failed = new Promise(resolve => { failure = resolve; });
  const runtime = await startEngine(options(t, `console.log('private dictated words');process.exit(5)`,
    {onConnect() { assert.fail('exited engine connected'); }, onFailure: failure}));
  t.after(() => runtime.stop());
  await failed;
  assert.doesNotMatch(fs.readFileSync(runtime.logPath, 'utf8'), /private dictated words/);
});

test('an engine that never becomes ready produces a retryable failure and is stopped', async t => {
  let failure;
  const failed = new Promise(resolve => { failure = resolve; });
  const runtime = await startEngine(options(t, 'setInterval(()=>{},1000)',
    {timeoutMs: 100, onConnect() { assert.fail('unready engine connected'); }, onFailure: failure}));
  t.after(() => runtime.stop());
  assert.match((await failed).message, /start|ready/i);
  await new Promise(resolve => runtime.child.exitCode !== null ? resolve() : runtime.child.once('exit', resolve));
  assert.notEqual(runtime.child.exitCode ?? runtime.child.signalCode, null);
});

test('connections without the startup token cannot impersonate the engine', async t => {
  let failure;
  const failed = new Promise(resolve => { failure = resolve; });
  const runtime = await startEngine(options(t, `
    const net = require('node:net');
    const socket = net.connect(Number(process.env.SYLLUNE_UI_PORT), '127.0.0.1', () => {
      socket.write(JSON.stringify({type:'hello',token:'wrong'})+'\\n');
    });
    socket.on('error',()=>{}); setInterval(()=>{},1000);
  `, {timeoutMs: 200, onConnect() { assert.fail('unauthenticated connection'); }, onFailure: failure}));
  t.after(() => runtime.stop());
  assert.match((await failed).message, /start|ready/i);
});
