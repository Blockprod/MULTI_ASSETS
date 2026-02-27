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
      out_file: "../logs/pm2-out.log",
      error_file: "../logs/pm2-error.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      env: {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1"
      },
      // Nettoyage du cache avant chaque redémarrage
      exec_mode: "fork",
      max_memory_restart: "500M"
    }
  ]
}
