// pm2 start ecosystem.config.js — agentflow's MCP server and the chat layer (Telegram bot).
// If pm2 can't find uv, replace "uv" with the full path from `which uv`.
module.exports = {
  apps: [
    {
      name: "agentflow-mcp",
      cwd: __dirname,
      script: "uv",
      args: "run agentflow-mcp --http",
      interpreter: "none",
      autorestart: true,
    },
    {
      name: "agentflow-chat",
      cwd: __dirname + "/chat",
      script: "uv",
      args: "run agentflow-chat",
      interpreter: "none",
      autorestart: true,
      // One instance only: Telegram allows one long-polling client per token (a second gets 409).
      instances: 1,
      restart_delay: 5000,
    },
  ],
};
