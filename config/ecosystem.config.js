module.exports = {
  apps: [
    {
      name: "MULTI_SYMBOLS",
      script: "MULTI_SYMBOLS.py",
      cwd: "C:/Users/averr/MULTI_ASSETS/code/src",
      interpreter: "C:/Users/averr/MULTI_ASSETS/.venv/Scripts/pythonw.exe",
      args: ["-B"],  // Flag -B: pas de fichiers .pyc
      watch: false,
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 10,           // Prevent infinite crash loops
      min_uptime: "30s",          // Must run 30s to count as "started"
      kill_timeout: 15000,        // 15s for graceful shutdown before SIGKILL
      listen_timeout: 10000,      // 10s startup timeout
      out_file: "../logs/pm2-out.log",
      error_file: "../logs/pm2-error.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      env: {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1"
      },
      exec_mode: "fork",
      max_memory_restart: "500M"
    }
  ]
}
