# ♾️ Immortal AI Discord Bot

A world-class, self-improving Discord bot built with `discord.py` 2.4+. Designed for **Zero Data Loss**, **Infinite Memory**, and **Deep Reasoning-powered AI**.

## 🌟 Key Features

### Core Systems
- **Immortal Persistence:** Every state change is written to disk immediately using atomic writes.
- **Deep Reasoning AI:** The AI performs chain-of-thought analysis before executing any server management action.
- **Unlimited Prefix Commands:** Bypasses slash command limits by allowing AI to create dynamic `!` prefix commands.
- **Auto-Documentation:** Every system automatically creates help embeds and `!help <system>` commands.

### AI Systems
- **AI Chat Channels:** Dedicated AI-powered channels with customizable personas (General, Help, RPG, Counselor, Translator, Coding, Creative, Gaming)
- **Multi-AI Providers:** Support for different AI models per channel
- **Web Search:** AI can search the web using Tavily and include results in responses
- **Command Execution:** AI can execute bot commands when needed

### Moderation
- **Anti-Raid:** Automatic raid detection and server locking
- **Moderation:** Strike system with auto-mutes/bans
- **Conflict Resolution:** AI-analyzes server conflicts
- **Appeals System:** DM-based appeals (ban/mute/warn categories, evidence requests, history)

### Economy & Leveling
- **Economy:** Coins system with daily rewards, transfers
- **Leveling:** XP with streak bonuses (up to 2x at 30+ day streaks)
- **XP Multipliers:** Weekend 2x, VIP 1.5x, Event 3x
- **Daily Challenges:** Complete challenges for bonus coins
- **Economy Achievements:** 8 achievements to unlock

### Shop System
- **Premium Shop:** Roles, badges, colors, channels, banners
- **Limited Stock:** Individual item quantities
- **Discounts:** Flash sales (50% off), weekend deals (25% off)
- **Canned Responses:** Auto-replies to common issues

### Tickets & Modmail
- **Advanced Tickets:** Categories, priority, sentiment analysis
- **Ticket Templates:** Bug report, feature request, billing, account, general
- **Modmail:** DM the bot → forwarded to staff channel → staff replies via modal

### Events & Activities
- **Event Scheduler:** Automated trivia, story building, debates
- **Polls:** Create polls with multiple choices
- **Contests:** Submission-based contests
- **Giveaways:** Automated multi-winner giveaways

### Staff Management
- **Staff Applications:** Modal-based with approval workflow
- **Staff Reviews:** Performance tracking
- **Staff Promo/Demo:** Tracking with cooldowns
- **Shift Management:** Track staff activity

### Community
- **Leveling:** XP and leveling up with role rewards
- **Achievements:** Unlock badges for activities
- **Community Health:** AI-analyzes server engagement
- **Welcome/Leave:** Customizable embeds

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

### Setting Up Systems
Use `/setup` to configure:
- Moderation System
- Economy System
- Leveling System
- Ticket System
- Modmail System
- Welcome/Goodbye
- And more...

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
- `modules/`: Specialized logic for each system.
  - `leveling.py`: XP, streaks, multipliers
  - `economy.py`: Coins, challenges, achievements
  - `shop.py`: Limited items, discounts
  - `tickets.py`: Advanced tickets with templates
  - `appeals.py`: DM-based appeals
  - `events.py`: Polls, contests, trivia
  - `chat_channels.py`: AI chat with personas
  - And 25+ more modules...

---

## 💰 AI Tiers (Conceptual)

| Tier | Daily Limit | Features |
|------|-------------|------------|
| Free | 50/day | Basic AI chat |
| Premium | 500/day | +Web search, +Image gen |
| Enterprise | Unlimited | +Custom AI model |

*Note: Premium and Enterprise require users to add their own API keys.*

---

## 🛡️ License
MIT License. Built with pride by **Antigravity**.