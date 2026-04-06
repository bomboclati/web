# ♾️ Immortal AI Discord Bot

A world-class, self-improving Discord bot built with `discord.py` 2.4+. Designed for **Zero Data Loss**, **Infinite Memory**, and **Deep Reasoning-powered AI**.

## 🌟 Key Features

- **Immortal Persistence:** Every state change is written to disk immediately using atomic writes.
- **Deep Reasoning AI:** The AI performs chain-of-thought analysis before executing any server management action.
- **Unlimited Prefix Commands:** Bypasses slash command limits by allowing AI to create dynamic `!` prefix commands.
- **Super Cool Systems:**
  - **Apply Staff:** Complete modal-based application system with staff approval logs.
  - **Economy & Leveling:** Dual currency (Coins/Gems), XP progression, and a premium shop.
  - **Appeals System:** Automated DM appeals for moderation actions.
  - **Verification:** One-click button verification.
  - **Trigger Roles:** Keyword-based role assignment (e.g., for picture permissions).
- **Auto-Documentation:** Every system automatically creates help embeds and `!help <system>` commands.

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+
- Discord Bot Token (from [Discord Developer Portal](https://discord.com/developers/applications))
- AI API Key (OpenRouter, Gemini, or OpenAI)
- (Optional) Tavily API Key for Web Search
- (Optional) Tenor/GIPHY API Keys for GIFs

### 2. Installation
```powershell
# 1. Clone or download the files
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your tokens
```

### 3. Run the Bot
```powershell
python bot.py
```

---

## 🛠️ Usage

### Primary Commands
- `/bot <text>`: **The main AI portal.** Tell the AI what you want to build or do (e.g., "Build a staff application system" or "Setup a car shop").
- `/status`: Check system health and memory depth.
- `/help`: List all utility commands.
- `/list`: See all active automations (custom commands, triggers).
- `/config`: Adjust AI provider and model settings.
- `/undo`: Reverses the latest administrative actions.

### AI Action Logic
When you use `/bot`, the AI will:
1.  **Reason**: Analyze your request and plan the steps.
2.  **Walkthrough**: Present a bulleted plan and ask for confirmation.
3.  **Execute**: Perform the actions (create channels, roles, prefix commands) once you click **Proceed**.

---

## 📂 Architecture

- `bot.py`: Central client logic and command sync.
- `data_manager.py`: Atomic JSON writes for zero data loss.
- `history_manager.py`: Infinite per-user conversation memory.
- `ai_client.py`: Deep reasoning, web search, and retry logic.
- `actions.py`: Bridge between AI JSON and Discord API.
- `modules/`: Specialized logic for staff, economy, leveling, and appeals.

---

## 🛡️ License
MIT License. Built with pride by **Antigravity**.
