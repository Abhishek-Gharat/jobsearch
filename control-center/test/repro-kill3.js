const { spawn, execSync } = require('child_process');
const path = require('path');

const child = spawn('cmd.exe', ['/d', '/s', '/c', path.join(__dirname, 'fake-kilo.cmd'), 'run', '--auto', 'hi', '--format', 'json', '--title', 'test']);
console.log('child pid', child.pid);

child.stdout.on('data', d => process.stdout.write('STDOUT: ' + d));
child.stderr.on('data', d => process.stdout.write('STDERR: ' + d));

setTimeout(() => {
  console.log('sending taskkill /T /F to child pid', child.pid);
  try {
    execSync(`taskkill /PID ${child.pid} /T /F`, { windowsHide: true });
    console.log('taskkill succeeded');
  } catch (e) {
    console.log('taskkill error:', e.message.split('\n')[0]);
  }
}, 800);

setTimeout(() => {
  try {
    const out = execSync('tasklist /FI "IMAGENAME eq node.exe" /FO CSV /NH', { encoding: 'utf8' });
    const lines = out.trim().split('\n').filter(l => l.includes('node'));
    console.log('node processes after taskkill:', lines.length, 'found');
  } catch (e) {
    console.log('tasklist err', e.message);
  }
  process.exit(0);
}, 2500);
