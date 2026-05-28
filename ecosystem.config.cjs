module.exports = {
  apps: [
    {
      name: "jobsim-server",
      cwd: __dirname,
      script: "npm",
      args: "start",
      exec_mode: "fork",
      instances: 1,
      watch: false,
      max_memory_restart: "512M",
      env: {
        NODE_ENV: "production",
        PORT: 8080,
        OPENAI_EVAL_MODEL: "gpt-5-nano",
        EVALUATE_RATE_LIMIT_MAX: 10,
        EVALUATE_RATE_LIMIT_WINDOW_MS: 60000,
        EVALUATION_LOG_RETENTION_DAYS: 14
      }
    }
  ]
};
