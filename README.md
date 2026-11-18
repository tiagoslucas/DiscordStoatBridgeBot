# Discord ↔ Stoat Bridge
A lightweight bidirectional bridge that forwards messages between Discord and Stoat channels using webhooks and masquerade.

## How it works
```
Discord user → Discord Bot → Stoat channel  (via Stoat masquerade)
Stoat user   → Stoat Bot   → Discord channel (via Discord webhook)
```
Messages are forwarded in real time. Usernames and avatars are carried over so it looks native on both platforms.

### Replies
| Direction | Behaviour |
|---|---|
| Discord → Stoat | Native reply when the original message is in cache; quote fallback otherwise |
| Stoat → Discord | Quote fallback (`-# ↩ **Author**: *snippet*`) – Discord webhooks do not support native replies |

The bridge caches the last **500** message ID pairs in memory (configurable). Replies to messages older than that are sent without a reply indicator.

### Files & attachments
| Direction | Behaviour |
|---|---|
| Discord → Stoat | The bare attachment URL is appended to the message text |
| Stoat → Discord | File is downloaded into RAM and re-uploaded as a Discord attachment. Files larger than **25 MiB** are sent as a fallback link instead |

### Mention & emoji resolution
| Direction | What gets resolved |
|---|---|
| Discord → Stoat | `<@id>` → `@Nickname`, `<#id>` → `#channel-name`, `<@&id>` → `@role-name`, `<:n:id>` → `:n:` |
| Stoat → Discord | `<@ULID>` → `@DisplayName`, `:ULID:` → `:emoji-name:` |

---

## Setup

### Requirements
- Python 3.10+
- A Discord bot with the **Message Content**, **Server Members**, **Guilds**, and **Webhooks** intents enabled
- A Stoat bot

### Install dependencies
```bash
pip install discord.py stoat.py aiohttp python-dotenv
```

---

### Local Setup

**1. Clone the repo**
```bash
git clone https://github.com/JMGstudios/DiscordStoatBridgeBot.git
cd DiscordStoatBridgeBot
```

**2. Run the bridge**
```bash
python bridge.py
```

If no `.env` file is found, or if required values are missing, the bridge will automatically start an **interactive setup wizard** on first launch:

```
============================================================
  Stoat ↔ Discord Bridge – First-Time / Repair Setup
============================================================

› Discord Bot Token
  Get it at: https://discord.com/developers/applications
  Discord Bot Token:
  ⌨  Typing is hidden for security – this is normal, just type and press Enter.
  >

› Stoat Bot Token
  ...

› Channel Pairs
  You will now link Discord channels to Stoat channels one pair at a time.

  ── Pair 1 (first pair) ──
  Discord Channel ID for pair 1: 123456789012345678
  Stoat   Channel ID for pair 1: ABCDEFGHIJ1234567890ABCDEF

  ✔  Pair 1 saved: Discord 123456789012345678 ↔ Stoat ABCDEFGHIJ1234567890ABCDEF

  Add another channel pair? [y/N]:

  ✔  Configuration saved to /your/path/.env
============================================================
```

The generated `.env` is saved next to `bridge.py`. On every subsequent start the bridge prints the config path so you always know which file it loaded:
```
Config loaded from: /your/path/.env
```

> **Note on token input:** when entering bot tokens the cursor won't move and nothing will appear on screen. This is intentional — input is hidden for security. Just type normally and press Enter.

**3. Discord bot permissions**

Make sure your bot has the following permissions in the target channel:
- Read Messages
- Send Messages
- Manage Webhooks

---

### Docker Setup

**1. Pull the image**
```bash
docker pull ghcr.io/jmgstudios/discordstoatbridgebot:latest
```

**2. Create a persistent config file on your host**
```bash
mkdir -p ~/.config/bridgebot
```

> **Important:** You must mount only the `.env` file – not the entire directory. Mounting the whole folder would hide `bridge.py` inside the container and the bot won't start.

**3. Start – interactive setup**
```bash
sudo docker run -it -v ~/.config/bridgebot:/config -w /config ghcr.io/jmgstudios/discordstoatbridgebot:latest python /app/bridge.py
```

The setup wizard will guide you through the configuration and save your tokens and channel pairs to `~/.config/bridgebot/.env` on your host machine.

Same command – the bridge will find the existing `.env`, skip the setup wizard, and start immediately.

> The `-it` flag is required for the interactive prompts on first launch. You can keep using it on subsequent starts without any downside.

---

### Backfill existing history

`backfill.py` copies a Discord channel's **entire history** (oldest first) to its Stoat mirror, using the same formatting as live bridging (nicknames via masquerade, mention/emoji resolution, attachments as links).

```bash
python backfill.py <discord_channel_id>
```

The Discord channel ID must be one of the pairs in your `.env`. Run it **before** starting the bridge — messages the bridge already forwarded live will be duplicated. Old replies are not preserved as replies (they land as plain text). The script exits when done.

`--resume` continues after the newest message the last run processed (state in `.backfill_state/`), so an interrupted run or a gap pass picks up exactly where it stopped.

---

### Configuration reference

| Variable | Required | Description |
|---|---|---|
| `DISCORD_BOT_TOKEN` | ✅ | Token from the [Discord Developer Portal](https://discord.com/developers/applications) |
| `STOAT_BOT_TOKEN` | ✅ | Token from your Stoat bot settings |
| `DISCORD_CHANNEL_IDS` | ✅ | Comma-separated Discord channel IDs |
| `STOAT_CHANNEL_IDS` | ✅ | Comma-separated Stoat channel IDs |
| `REVOLT_API_URL` | ➖ | Revolt API base URL. Defaults to `https://api.revolt.chat` |

**Channel pairing:** the IDs are matched by position — the first Discord ID is bridged with the first Stoat ID, the second with the second, and so on. Both lists must have the same length. The setup wizard handles this automatically when configuring pairs one by one.

---

## Notes
- The bridge creates a webhook named `Stoat Bridge` in your Discord channel automatically. If one already exists from a previous run it will be reused.
- Messages originating from the bridge webhook are ignored to prevent forwarding loops.
- **Deletion behaviour (Stoat is the Discord archive):**
  - A message deleted on **Discord** stays on Stoat, struck through (`~~like this~~`), so the archive keeps the content while showing it was deleted.
  - A **Stoat-native** message deleted on Stoat is also deleted on Discord.
  - Deleting a *mirrored* message on Stoat never removes the Discord original.
  - Deletion handling only covers the last 500 bridged messages (the ID cache size); older ones are ignored.
- **Channel lifecycle (Stoat's structure follows Discord's growth):**
  - A text channel created on Discord is automatically created on Stoat, bridged, and persisted to `.env` (so it survives restarts).
  - Channels created on Stoat are **not** created on Discord.
  - Channels deleted on either side are **not** deleted on the other.
- **Startup catch-up:** on start (and after every Stoat reconnect) the bridge reads the newest bot-authored messages from each Stoat mirror, walks the Discord history backwards until it finds a message that is already archived, and forwards everything newer — so messages sent while the bridge was down (crash, reboot, Stoat outage) still reach the archive without duplicates. Matching is by nickname + normalized content; if no overlap is found within 500 messages the channel is skipped with a warning rather than risk duplication.
- Stoat custom emoji names are resolved via the Stoat API and cached in memory for the duration of the process.
- First-time users on both platforms receive a one-time DM explaining what the bridge does and how their messages are handled.


# Contributing is always appreciated! Feel free to open an Issue or a Pull Request! 
