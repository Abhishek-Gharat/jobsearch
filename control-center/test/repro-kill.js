const { spawn, execSync } = require('child_process');

const child = spawn('cmd.exe', ['/d', '/s', '/c', 'node -e "setInterval(()=>{},1000)"']);
console.log('child pid', child.pid);

setTimeout(() => {
  // Do NOT call child.kill(). Use taskkill directly, ignore errors.
  try {
    execSync(`taskkill /PID ${child.pid} /T /F`, { windowsHide: true });
    console.log('taskkill /T /F sent');
  } catch (e) {
    console.log('taskkill error (ignored):', e.message.split('\n')[0]);
  }
}, 500);

setTimeout(() => {
  try {
    const out = execSync('tasklist /FI "IMAGENAME eq node.exe" /FO CSV /NH', { encoding: 'utf8' });
    console.log('node processes after taskkill attempt:', out.trim() || '(none)');
  } catch (e) {
    console.log('tasklist err', e.message);
  }
  process.exit(0);
}, 2000);
