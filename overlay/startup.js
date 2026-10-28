// The renderer shows only fixed startup state and the engine's bounded error.
// It cannot run processes, choose files or navigate outside this local page.
window.startup.onStatus(status => {
  document.getElementById('title').textContent = status.error ? 'Syllune could not start' : 'Starting Syllune…';
  document.getElementById('message').textContent = status.error || 'Preparing your microphone and local speech engine.';
  document.getElementById('recovery').hidden = !status.error;
});
document.getElementById('retry').addEventListener('click', () => window.startup.retry());
document.getElementById('logs').addEventListener('click', () => window.startup.logs());
document.getElementById('quit').addEventListener('click', () => window.startup.quit());
