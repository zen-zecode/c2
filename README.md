# Multi-Node Autonomous AI Agent C2 System

A professional-grade remote management infrastructure using a Cloudflare-native Command & Control architecture.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLOUDFLARE                               │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Cloudflare Worker (API Gateway)                         │    │
│  │  - POST /register        Node registration               │    │
│  │  - GET  /tasks/{node_id} Poll for commands               │    │
│  │  - POST /results/{id}    Submit results                  │    │
│  │  - Admin endpoints for dashboard                         │    │
│  └─────────────────────────────┬───────────────────────────┘    │
│                                │                                 │
│  ┌─────────────────────────────▼───────────────────────────┐    │
│  │  D1 Database                                             │    │
│  │  - nodes (id, hostname, hwid, status, last_ping)         │    │
│  │  - tasks (id, node_id, command, status, output)          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌────────────────────────┐   ┌────────────────────────────┐    │
│  │  Workers AI             │   │  Cloudflare Pages          │    │
│  │  @cf/openai/gpt-oss-120b│   │  React Dashboard           │    │
│  └────────────────────────┘   └────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                            ▲
                            │ HTTPS (X-API-KEY / X-ADMIN-PASSWORD)
                            │
     ┌──────────────────────┼──────────────────────┐
     │                      │                      │
┌────▼────┐           ┌─────▼────┐           ┌────▼────┐
│  Node 1  │           │  Node 2  │           │  Node N  │
│  Python  │           │  Python  │           │  Python  │
│  Agent   │           │  Agent   │           │  Agent   │
└──────────┘           └──────────┘           └──────────┘
     │
     │ File Upload
     ▼
┌──────────┐
│ Telegram │
│ Bot API  │
└──────────┘
```

## Components

### 1. Backend (`Master/backend/`)

Cloudflare Worker + D1 Database API Gateway.

**Setup:**
```bash
cd Master/backend
npm install

# Create D1 database
npm run db:create

# Update wrangler.toml with your database_id
# Then run migrations
npm run db:migrate

# Set secrets
wrangler secret put API_KEY
wrangler secret put ADMIN_PASSWORD
wrangler secret put TELEGRAM_BOT_TOKEN
wrangler secret put TELEGRAM_ADMIN_ID

# Deploy
npm run deploy
```

### 2. Dashboard (`Master/dashboard/`)

React + Vite dashboard for node management.

**Setup:**
```bash
cd Master/dashboard
npm install

# Edit src/App.tsx and set:
# - API_URL: Your Cloudflare Worker URL
# - ADMIN_PASSWORD: Your dashboard password

# Development
npm run dev

# Build for production
npm run build

# Deploy to Cloudflare Pages
npx wrangler pages deploy dist
```

### 3. Node Agent (`node/`)

Python agent for Windows machines.

**Setup:**
```powershell
# Copy files to target machine
# Edit agent.py and configure:
# - API_URL: Your Cloudflare Worker URL
# - API_KEY: Must match Worker secret
# - TELEGRAM_BOT_TOKEN: For file uploads
# - TELEGRAM_ADMIN_ID: Your Telegram user ID

# Run installer as Administrator
powershell -ExecutionPolicy Bypass -File install.ps1
```

## Configuration

### Environment Variables

| Variable | Backend | Dashboard | Node | Description |
|----------|---------|-----------|------|-------------|
| `API_URL` | - | ✓ | ✓ | Cloudflare Worker URL |
| `API_KEY` | ✓ | - | ✓ | Node authentication key |
| `ADMIN_PASSWORD` | ✓ | ✓ | - | Dashboard password |
| `TELEGRAM_BOT_TOKEN` | ✓ | - | ✓ | Telegram bot token |
| `TELEGRAM_ADMIN_ID` | ✓ | - | ✓ | Your Telegram user ID |

## API Endpoints

### Node Endpoints (Requires `X-API-KEY` header)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/register` | Register new node |
| GET | `/tasks/{node_id}` | Poll for pending tasks |
| POST | `/results/{node_id}` | Submit task results |

### Admin Endpoints (Requires `X-ADMIN-PASSWORD` header)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/admin/nodes` | List all nodes |
| POST | `/admin/broadcast` | Send command to all nodes |
| POST | `/admin/task` | Send command to specific node |
| GET | `/admin/logs` | Fetch task logs |
| DELETE | `/admin/node/{id}` | Delete a node |
| POST | `/admin/ai` | AI-powered command processing |

## Features

### Dashboard
- 🔐 Password-protected access
- 📡 Broadcast commands to all nodes
- 🤖 AI-powered natural language commands with reasoning
- 📊 Real-time node status monitoring
- 📋 Task log viewer with output display
- 📎 Telegram file download links
- ⚡ Multi-model AI with automatic fallback
- 🛡️ Destructive command approval system

### Node Agent
- 🔄 10-second polling interval
- 💻 PowerShell command execution
- 📦 Silent software installation
- 📤 File upload to Telegram
- 🔁 Auto-restart on failure
- 👻 Background execution (no console window)
- 📸 Full-screen screenshots with DPI scaling support
- 💣 Self-destruct capability (complete removal)

### AI Commands
Natural language commands are processed by Workers AI:
- "Check disk space on all nodes"
- "Install Chrome on NODE_ID"
- "Upload the hosts file from all machines"
- "Take a screenshot" (handles DPI scaling automatically)
- "Self destruct" (removes agent completely - requires approval)

## Security

- **Node Authentication**: `X-API-KEY` header required for all node requests
- **Admin Authentication**: `X-ADMIN-PASSWORD` header required for dashboard
- **HTTPS Only**: All traffic is encrypted via Cloudflare

## License

MIT
