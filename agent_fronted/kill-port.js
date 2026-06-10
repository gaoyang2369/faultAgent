import { exec } from 'child_process';

console.log('Checking if port 9005 is occupied...');

// 查找并终止占用 9005 端口的进程
const command = 'netstat -ano | findstr :9005';

exec(command, (error, stdout, stderr) => {
  if (error) {
    console.log('Port 9005 is free.');
    process.exit(0);
  }

  const lines = stdout.trim().split('\n');
  
  for (const line of lines) {
    // 检查是否是监听状态的行
    if (line.includes('LISTENING')) {
      const parts = line.trim().split(/\s+/);
      if (parts.length >= 5) {
        const pid = parts[parts.length - 1];
        console.log(`Killing process ${pid} occupying port 9005...`);
        
        exec(`taskkill /PID ${pid} /F`, (killError, killStdout, killStderr) => {
          if (killError) {
            console.log(`Failed to kill process ${pid}: ${killError.message}`);
          } else {
            console.log(`Successfully killed process ${pid}`);
          }
          process.exit(0);
        });
        return;
      }
    }
  }
  
  console.log('No process found occupying port 9005');
  process.exit(0);
});