#!/usr/bin/env python3
"""
Discord / Stoat Bridge (Bidirectional & One-Way)

     ██╗ ███╗   ███╗  ██████╗
     ██║ ████╗ ████║ ██╔════╝
     ██║ ██╔████╔██║ ██║  ███╗
██   ██║ ██║╚██╔╝██║ ██║   ██║
╚█████╔╝ ██║ ╚═╝ ██║ ╚██████╔╝
 ╚════╝  ╚═╝     ╚═╝  ╚═════╝

 Thank you for downloading!

 Please refer to readme.md for more info.

 GitHub: https://github.com/JMGstudios/DiscordStoatBridgeBot
 
 Support stoat server: https://stt.gg/FH10z8eP

 Support discord server: https://discord.gg/QTVRxUDSMq

"""

import asyncio
import io
import json
import logging
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv, set_key, dotenv_values
import stoat
import stoat.shard

# ──────────────────────────────────────────────────────────────────────────────
#  INTERACTIVE .env SETUP
# ──────────────────────────────────────────────────────────────────────────────

ENV_FILE = Path(".env")


def _prompt(label: str, secret: bool = False, allow_empty: bool = False) -> str:
    """Prompt the user on the terminal until a non-empty value is entered."""
    import getpass
    while True:
        try:
            if secret:
                print(f"  {label}:")
                print("  ⌨  Typing is hidden for security – this is normal, just type and press Enter.")
                value = getpass.getpass("  > ").strip()
            else:
                value = input(f"  {label}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSetup aborted.")
            raise SystemExit(1)
        if value or allow_empty:
            return value
        print("    ⚠  Value cannot be empty, please try again.")


def _prompt_channel_pairs() -> tuple[str, str, str]:
    print("  You will now link Discord channels to Stoat channels one pair at a time.")
    print("  Each pair bridges one Discord channel with one Stoat channel.\n")

    discord_ids: list[str] = []
    stoat_ids:   list[str] = []
    directions:  list[str] = []
    pair_num = 1

    _DIR_LABELS = {"bi": "↔  Bidirectional", "discord": "→  Discord → Stoat only", "stoat": "←  Stoat → Discord only"}

    while True:
        print(f"  ── Pair {pair_num} {'(first pair)' if pair_num == 1 else ''} ──")
        d_id = _prompt(f"  Discord Channel ID for pair {pair_num}")
        s_id = _prompt(f"  Stoat   Channel ID for pair {pair_num}")
        discord_ids.append(d_id.strip())
        stoat_ids.append(s_id.strip())

        # ── Direction prompt ──────────────────────────────────────────────────
        print(f"\n  Bridge direction for pair {pair_num}:")
        print(f"\n  Choose wether your bridge should be bidirectional or only transfer messages in one direction")
        print("    [1]  Bidirectional   (Discord ↔ Stoat)")
        print("    [2]  Discord → Stoat only")
        print("    [3]  Stoat → Discord only")
        while True:
            try:
                dir_choice = input("  Choice [1/2/3]: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nSetup aborted.")
                raise SystemExit(1)
            if dir_choice == "1":
                direction = "bi"
                break
            elif dir_choice == "2":
                direction = "discord"
                break
            elif dir_choice == "3":
                direction = "stoat"
                break
            print("    ⚠  Please enter 1, 2, or 3.")
        directions.append(direction)

        print(f"\n  ✔  Pair {pair_num} saved: Discord {d_id.strip()} {_DIR_LABELS[direction]} Stoat {s_id.strip()}")

        if pair_num >= 2:
            print(f"\n  Current pairs:")
            for i, (d, s, dr) in enumerate(zip(discord_ids, stoat_ids, directions), 1):
                print(f"    {i}. Discord {d} {_DIR_LABELS[dr]} Stoat {s}")

        print()
        try:
            again = input("  Add another channel pair? [y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nSetup aborted.")
            raise SystemExit(1)

        if again in ("y", "yes"):
            pair_num += 1
        else:
            break

    return ",".join(discord_ids), ",".join(stoat_ids), ",".join(directions)


def _validate_channel_pairs(discord_raw: str, stoat_raw: str) -> bool:
    d = [x.strip() for x in discord_raw.split(",") if x.strip()]
    s = [x.strip() for x in stoat_raw.split(",")   if x.strip()]
    return bool(d) and bool(s) and len(d) == len(s)


def interactive_env_setup() -> None:
    """
    - .env does not exist → create it from scratch via prompts
    - .env exists but is corrupted / unparseable → prompt for all values
    - .env exists but specific keys are missing or empty → prompt only for those
    - .env exists but DISCORD_CHANNEL_IDS / STOAT_CHANNEL_IDS are mismatched → re-prompt for both
    """

    existing: dict[str, str] = {}
    if ENV_FILE.exists():
        try:
            existing = {k: (v or "") for k, v in dotenv_values(ENV_FILE).items()}
        except Exception as exc:
            print(f"⚠  Could not parse {ENV_FILE}: {exc}")
            print("   Starting fresh – you will be asked for all values.\n")


    needs_discord_token = not existing.get("DISCORD_BOT_TOKEN", "").strip()
    needs_stoat_token   = not existing.get("STOAT_BOT_TOKEN",   "").strip()

    discord_raw  = existing.get("DISCORD_CHANNEL_IDS", "")
    stoat_raw    = existing.get("STOAT_CHANNEL_IDS",   "")
    needs_channels = not _validate_channel_pairs(discord_raw, stoat_raw)

    # Directions must exist and match pair count; if not, re-prompt channels too.
    _dir_raw   = existing.get("CHANNEL_DIRECTIONS", "")
    _dir_list  = [x.strip() for x in _dir_raw.split(",") if x.strip()]
    _pair_count = len([x for x in discord_raw.split(",") if x.strip()])
    _valid_dirs = {"bi", "discord", "stoat"}
    if not needs_channels and (
        len(_dir_list) != _pair_count
        or any(d not in _valid_dirs for d in _dir_list)
    ):
        needs_channels = True   # re-prompt pairs + directions together
    needs_revolt_url = (
        not existing.get("REVOLT_API_URL", "").strip()
        or not existing.get("REVOLT_WS_URL", "").strip()
        or not existing.get("REVOLT_CDN_URL", "").strip()
    )

    anything_missing = any([
        needs_discord_token, needs_stoat_token, needs_channels, needs_revolt_url,
    ])

    if not anything_missing:
        load_dotenv(ENV_FILE)
        print(f"Config loaded from: {ENV_FILE.resolve()}")
        return  # All set, setup isnt needed

    banner = "=" * 60
    print(f"\n{banner}")
    print("  Stoat ↔ Discord Bridge – First-Time / Repair Setup")
    print(banner)

    if not ENV_FILE.exists():
        print("  ℹ  No .env file found – let's create one.")
    else:
        missing_keys = []
        if needs_discord_token: missing_keys.append("DISCORD_BOT_TOKEN")
        if needs_stoat_token:   missing_keys.append("STOAT_BOT_TOKEN")
        if needs_channels:      missing_keys.append("DISCORD_CHANNEL_IDS / STOAT_CHANNEL_IDS / CHANNEL_DIRECTIONS")
        if needs_revolt_url:    missing_keys.append("REVOLT_API_URL / REVOLT_WS_URL / REVOLT_CDN_URL")
        print(f"  ⚠  Missing or invalid keys: {', '.join(missing_keys)}")
    print("  Press Ctrl-C at any time to abort.\n")

    # Discord bot token
    if needs_discord_token:
        print("› Discord Bot Token")
        print("  Get it at: https://discord.com/developers/applications")
        existing["DISCORD_BOT_TOKEN"] = _prompt("Discord Bot Token", secret=True)
        print()

    # Stoat bot token
    if needs_stoat_token:
        print("› Stoat Bot Token")
        print("  Get it in your Stoat server's bot settings.")
        existing["STOAT_BOT_TOKEN"] = _prompt("Stoat Bot Token", secret=True)
        print()

    # Channel pairs – collected one pair at a time
    if needs_channels:
        print("› Channel Pairs")
        d_raw, s_raw, dir_raw = _prompt_channel_pairs()
        existing["DISCORD_CHANNEL_IDS"]  = d_raw
        existing["STOAT_CHANNEL_IDS"]    = s_raw
        existing["CHANNEL_DIRECTIONS"]   = dir_raw
        print()

    # Revolt API URL + WebSocket URL (both optional – sensible defaults)
    if needs_revolt_url:
        print("› Revolt / Stoat API URL (HTTP)")
        print("  Press Enter to accept the default!")
        print("  Only change this if you are using a self-hosted instance.")
        url = _prompt("API URL  [https://api.revolt.chat]", allow_empty=True)
        existing["REVOLT_API_URL"] = url or "https://api.revolt.chat"
        print()
        print()

        print("› Revolt / Stoat WebSocket URL")
        print("  Press Enter to accept the default!")
        print("  Only change this if you are using a self-hosted instance.")
        ws_url = _prompt("WS URL  [wss://ws.revolt.chat]", allow_empty=True)
        existing["REVOLT_WS_URL"] = ws_url or "wss://ws.revolt.chat"
        print()

        print("› Revolt / Stoat CDN URL (for avatars & attachments)")
        print("  Press Enter to accept the default!")
        print("  Only change this if you are using a self-hosted instance.")
        print("  This is the URL of your Autumn (file server) instance.")
        cdn_url = _prompt("CDN URL  [https://autumn.revolt.chat]", allow_empty=True)
        existing["REVOLT_CDN_URL"] = cdn_url or "https://autumn.revolt.chat"
        print()

    # ── 4. Write / update the .env file ─────────────────────────────────────
    ENV_FILE.touch(exist_ok=True)
    for key, value in existing.items():
        set_key(str(ENV_FILE), key, value)

    print(f"  ✔  Configuration saved to {ENV_FILE.resolve()}")
    print(f"{banner}\n")

    # ── 5. Re-load so os.getenv() picks up the freshly written values ────────
    load_dotenv(ENV_FILE, override=True)


if __name__ == "__main__":
    interactive_env_setup()

# ──────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
STOAT_BOT_TOKEN   = os.getenv("STOAT_BOT_TOKEN", "")

_discord_raw = os.getenv("DISCORD_CHANNEL_IDS", "")
_stoat_raw   = os.getenv("STOAT_CHANNEL_IDS", "")

DISCORD_CHANNEL_IDS: list[int] = [int(x.strip()) for x in _discord_raw.split(",") if x.strip()]
STOAT_CHANNEL_IDS:   list[str] = [x.strip()      for x in _stoat_raw.split(",")   if x.strip()]

REVOLT_API_URL = os.getenv("REVOLT_API_URL", "https://api.revolt.chat").rstrip("/")
REVOLT_WS_URL  = os.getenv("REVOLT_WS_URL",  "wss://ws.revolt.chat").rstrip("/")
REVOLT_CDN_URL = os.getenv("REVOLT_CDN_URL", "https://autumn.revolt.chat").rstrip("/")

if len(DISCORD_CHANNEL_IDS) != len(STOAT_CHANNEL_IDS):
    raise RuntimeError(
        f"Channel list length mismatch: "
        f"{len(DISCORD_CHANNEL_IDS)} Discord IDs vs {len(STOAT_CHANNEL_IDS)} Stoat IDs."
    )

PAIR_COUNT = len(DISCORD_CHANNEL_IDS)

# ── Per-pair bridge direction ─────────────────────────────────────────────────
#   "bi"      → bidirectional (default)
#   "discord" → Discord → Stoat only
#   "stoat"   → Stoat → Discord only
_directions_raw = os.getenv("CHANNEL_DIRECTIONS", "")
CHANNEL_DIRECTIONS: list[str] = [x.strip() for x in _directions_raw.split(",") if x.strip()]
if len(CHANNEL_DIRECTIONS) != PAIR_COUNT:
    # Backward-compat: if key is missing, treat every pair as bidirectional.
    CHANNEL_DIRECTIONS = ["bi"] * PAIR_COUNT

STOAT_TO_DISCORD: dict[str, int] = {
    s: d
    for d, s, dir_ in zip(DISCORD_CHANNEL_IDS, STOAT_CHANNEL_IDS, CHANNEL_DIRECTIONS)
    if dir_ in ("bi", "stoat")
}
DISCORD_TO_STOAT: dict[int, str] = {
    d: s
    for d, s, dir_ in zip(DISCORD_CHANNEL_IDS, STOAT_CHANNEL_IDS, CHANNEL_DIRECTIONS)
    if dir_ in ("bi", "discord")
}

# ──────────────────────────────────────────────────────────────────────────────
#  WELCOME DM TOGGLE
# ──────────────────────────────────────────────────────────────────────────────

SEND_WELCOME_DMS = False #  Set to False to disable welcome DMs on both Discord and Stoat.

# 25MB file size limit due to discord's restrictions
MAX_FILE_SIZE  = 25 * 1024 * 1024

# The amount of messages that are being stored in cache for reply support
MSG_CACHE_SIZE = 500

# ──────────────────────────────────────────────────────────────────────────────
#  LOGGING - SET YOUR LOGGING LEVEL HERE
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bridge")

# ──────────────────────────────────────────────────────────────────────────────
#  FIRST-TIME USER NOTIFICATION  (persisted to JSON)
# ──────────────────────────────────────────────────────────────────────────────

NOTIFIED_USERS_FILE = ENV_FILE.parent / "notified_users.json"

_notified_users: dict[str, list[str]] = {"discord": [], "stoat": []}


def _load_notified_users() -> None:
    global _notified_users
    if NOTIFIED_USERS_FILE.exists():
        try:
            with NOTIFIED_USERS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                _notified_users["discord"] = list(data.get("discord", []))
                _notified_users["stoat"]   = list(data.get("stoat", []))
            logger.info(
                f"Loaded {len(_notified_users['discord'])} Discord "
                f"and {len(_notified_users['stoat'])} Stoat notified users."
            )
        except Exception as exc:
            logger.error(f"Could not load {NOTIFIED_USERS_FILE}: {exc}")


def _save_notified_users() -> None:
    try:
        with NOTIFIED_USERS_FILE.open("w", encoding="utf-8") as f:
            json.dump(_notified_users, f, indent=2)
    except Exception as exc:
        logger.error(f"Could not save {NOTIFIED_USERS_FILE}: {exc}")


def _is_notified(platform: str, uid: str) -> bool:
    return uid in _notified_users[platform]


def _mark_notified(platform: str, uid: str) -> None:
    if uid not in _notified_users[platform]:
        _notified_users[platform].append(uid)
        _save_notified_users()


# DM texts ─────────────────────────────────────────────────────────────────────

DISCORD_WELCOME_DM = """\
👋 **Hey! You just used the Stoat↔Discord Bridge Bot.**

This bot connects a Discord channel to a channel on **Stoat** (https://stoat.chat), \
forwarding messages between both platforms in real time.

**What happens to your messages?**
• Your **display name** and **profile picture** are shown on the other platform.
• The **content** of your messages (text and attachments) is transferred to the other platform.
• Attachments are briefly buffered in the bot's memory for forwarding and discarded immediately afterwards.
• **No** messages are stored permanently on the bot's server.

**Deletion:**
If you delete your message on Stoat, it is also deleted on Discord. Messages deleted on Discord stay on Stoat, marked with a strikethrough.

If you don't want to use the bridge / your messages to be transfered, simply stop writing in the bridged channel — \
your messages will not be forwarded.
"""

STOAT_WELCOME_DM = """\
👋 **Hey! You just used the Stoat↔Discord Bridge Bot.**

This bot connects a Stoat channel to a channel on **Discord** (https://discord.gg), \
forwarding messages between both platforms in real time.

**What happens to your messages?**
• Your **display name** and **profile picture** are shown on the other platform.
• The **content** of your messages (text and attachments) is transferred to the other platform.
• Attachments are briefly buffered in the bot's memory for forwarding and discarded immediately afterwards.
• **No** messages are stored permanently on the bot's server.

**Deletion:**
If you delete your message here on Discord, it stays on Stoat but is marked with a strikethrough there. Messages written on Stoat are deleted on Discord when deleted on Stoat.

If you don't want to use the bridge / your messages to be transfered, simply stop writing in the bridged channel — \
your messages will not be forwarded.
"""

# ──────────────────────────────────────────────────────────────────────────────
#  SHARED STATE
# ──────────────────────────────────────────────────────────────────────────────

discord_webhooks: dict[int, discord.Webhook] = {}
stoat_channels:  dict[str, object]           = {}

_d2s: OrderedDict[int, str] = OrderedDict()   # discord_msg_id → stoat_msg_id (replies + strikethrough)
_s2d: OrderedDict[str, int] = OrderedDict()   # stoat_msg_id   → discord_msg_id (deletion mirroring)

# Discord message IDs created by our webhooks, i.e. mirrors of Stoat-native
# messages. Only these may be deleted on Discord when the Stoat original goes.
_webhook_discord_ids: set[int] = set()


def _cache_pair(discord_id: int, stoat_id: str, *, from_webhook: bool = False) -> None:
    for cache, key, val in ((_d2s, discord_id, stoat_id), (_s2d, stoat_id, discord_id)):
        if key in cache:
            cache.move_to_end(key)
        cache[key] = val
        if len(cache) > MSG_CACHE_SIZE:
            cache.popitem(last=False)
    if from_webhook:
        _webhook_discord_ids.add(discord_id)


def _extract_id(obj) -> str | None:
    """Pull an ID string from a raw dict, object, or plain string."""
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj or None
    if isinstance(obj, dict):
        v = obj.get("_id") or obj.get("id")
        return str(v) if v else None
    v = getattr(obj, "_id", None) or getattr(obj, "id", None)
    return str(v) if v else None


def _stoat_asset_url(asset) -> str | None:
    if asset is None:
        return None
    asset_id = getattr(asset, "_id", None) or getattr(asset, "id", None)
    tag      = getattr(asset, "tag", None)
    if asset_id and tag:
        return f"{REVOLT_CDN_URL}/{tag}/{asset_id}"
    # Fallback: use whatever the library returns
    url_attr = getattr(asset, "url", None)
    try:
        return url_attr() if callable(url_attr) else str(url_attr)
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
#  FILE HELPERS  (Stoat → Discord direction only)
# ──────────────────────────────────────────────────────────────────────────────


async def fetch_bytes(session: aiohttp.ClientSession, url: str) -> tuple[bytes, str] | None:
    """Download url into RAM. Returns (data, filename) or None on any error."""
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning(f"File fetch {url} -> HTTP {resp.status}")
                return None
            cl = resp.headers.get("Content-Length")
            if cl and int(cl) > MAX_FILE_SIZE:
                logger.warning(f"Skipping oversized file ({cl} B): {url}")
                return None
            data = await resp.read()
            if len(data) > MAX_FILE_SIZE:
                logger.warning(f"Skipping oversized file ({len(data)} B) after download")
                return None
            filename = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or "file"
            return data, filename
    except Exception as exc:
        logger.error(f"File fetch error for {url}: {exc}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
#  REVOLT MESSAGE FETCH  (for reply quotes)
# ──────────────────────────────────────────────────────────────────────────────


async def fetch_stoat_message(
    channel_id: str,
    message_id: str,
    stoat_client: "StoatBot",
) -> SimpleNamespace | None:

    def _build(raw: dict) -> SimpleNamespace:
        masquerade = raw.get("masquerade") or {}
        display_name = (
            masquerade.get("name")
            or _nested_get(raw, "author", "display_name")
            or _nested_get(raw, "author", "username")
            or "unknown"
        )
        return SimpleNamespace(
            content=raw.get("content") or "",
            author=SimpleNamespace(display_name=display_name),
        )

    def _nested_get(d, *keys):
        cur = d
        for k in keys:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur

    ch = stoat_channels.get(channel_id)
    if ch is not None:
        for attr in ("fetch_message", "get_message"):
            method = getattr(ch, attr, None)
            if not method:
                continue
            try:
                result = await method(message_id)
                if result is None:
                    continue
                if not isinstance(result, dict):
                    masq = getattr(result, "masquerade", None)
                    masq_name = (
                        masq.get("name") if isinstance(masq, dict)
                        else getattr(masq, "name", None)
                    ) if masq else None
                    author = getattr(result, "author", None)
                    display_name = (
                        masq_name
                        or getattr(author, "display_name", None)
                        or getattr(author, "name", None)
                        or "unknown"
                    )
                    return SimpleNamespace(
                        content=getattr(result, "content", "") or "",
                        author=SimpleNamespace(display_name=display_name),
                    )
                return _build(result)
            except Exception as exc:
                logger.debug(f"fetch_stoat_message via ch.{attr}: {exc}")

    http = getattr(stoat_client, "http", None)
    if http is not None:
        request_fn = getattr(http, "request", None)
        if request_fn:
            try:
                data = await request_fn("GET", f"/channels/{channel_id}/messages/{message_id}")
                if isinstance(data, dict):
                    return _build(data)
            except Exception as exc:
                logger.debug(f"fetch_stoat_message via http.request: {exc}")

    logger.warning(f"fetch_stoat_message: could not fetch {channel_id}/{message_id}")
    return None


def _stoat_msg_from_raw(raw: dict, user: dict) -> SimpleNamespace:
    """Wrap a REST message dict in the shape _bridge_stoat_message expects."""
    avatar = user.get("avatar")
    return SimpleNamespace(
        id=str(raw["_id"]),
        channel=SimpleNamespace(id=raw.get("channel")),
        content=raw.get("content") or "",
        author=SimpleNamespace(
            display_name=user.get("display_name") or user.get("username") or "unknown",
            avatar=SimpleNamespace(_id=avatar.get("_id"), tag=avatar.get("tag")) if avatar else None,
        ),
        attachments=[SimpleNamespace(**a) for a in raw.get("attachments") or []],
        replies=raw.get("replies") or [],
    )


async def _resolve_stoat_user(
    session: aiohttp.ClientSession, cache: dict, user_id: str
) -> dict:
    """Stoat user info by id, cached per catch-up run."""
    if user_id not in cache:
        try:
            async with session.get(
                f"{REVOLT_API_URL}/users/{user_id}",
                headers={"x-bot-token": STOAT_BOT_TOKEN},
            ) as resp:
                cache[user_id] = (await resp.json()) if resp.status == 200 else {}
        except Exception:
            cache[user_id] = {}
    return cache[user_id]


# ──────────────────────────────────────────────────────────────────────────────
#  STOAT MESSAGE EDIT HELPER
# ──────────────────────────────────────────────────────────────────────────────


async def edit_stoat_message(channel_id: str, message_id: str, content: str, stoat_client: "StoatBot") -> bool:
    """Edit a bot-sent message on Stoat via REST (used to strike through deleted Discord messages)."""
    session = stoat_client._http_session
    if session is None:
        logger.warning("edit_stoat_message: HTTP session not ready")
        return False
    try:
        async with session.patch(
            f"{REVOLT_API_URL}/channels/{channel_id}/messages/{message_id}",
            headers={"x-bot-token": STOAT_BOT_TOKEN},
            json={"content": content[:2000]},
        ) as resp:
            if resp.status in (200, 204):
                return True
            body = await resp.text()
            logger.warning(
                f"edit_stoat_message: HTTP {resp.status} for "
                f"{channel_id}/{message_id} – {body[:200]}"
            )
            return False
    except Exception as exc:
        logger.error(f"edit_stoat_message: {channel_id}/{message_id}: {exc}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
#  MENTION / EMOJI HELPERS
# ──────────────────────────────────────────────────────────────────────────────

_RE_DISCORD_USER    = re.compile(r"<@!?(\d+)>")
_RE_DISCORD_CHANNEL = re.compile(r"<#(\d+)>")
_RE_DISCORD_ROLE    = re.compile(r"<@&(\d+)>")
_RE_DISCORD_EMOJI   = re.compile(r"<(a?):([A-Za-z0-9_]+):(\d+)>")

_RE_REVOLT_USER       = re.compile(r"<@([A-Z0-9]{26})>")
_RE_REVOLT_CUSTOM_EMO = re.compile(r":([A-Z0-9]{26}):")

_emoji_name_cache: dict[str, str] = {}


async def resolve_revolt_emoji(emoji_id: str, session: aiohttp.ClientSession, token: str) -> str:
    if emoji_id in _emoji_name_cache:
        return _emoji_name_cache[emoji_id]
    try:
        async with session.get(
            f"{REVOLT_API_URL}/custom/emoji/{emoji_id}",
            headers={"x-bot-token": token},
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                name = data.get("name") or emoji_id
                _emoji_name_cache[emoji_id] = name
                return name
    except Exception as exc:
        logger.debug(f"Could not resolve Stoat emoji {emoji_id}: {exc}")
    return emoji_id


# ──────────────────────────────────────────────────────────────────────────────
#  EMOJI SYNC  (custom emojis mirrored between the Discord guild and the Stoat
#  server, so they render natively in messages and reactions on both sides)
#
#  The id map is derived from the two servers by NAME – there is no state file.
#  Storage on overlayfs hosts is RAM-only and wiped on every reboot; the
#  servers themselves are the source of truth, so a lost map just re-links.
# ──────────────────────────────────────────────────────────────────────────────

_emoji_d2s: dict[str, str] = {}   # discord emoji id → stoat emoji id
_emoji_s2d: dict[str, str] = {}   # stoat emoji id → discord emoji id (as str)
_emoji_lock_d2s = asyncio.Lock()  # serialize creation: two uses of a brand-new
_emoji_lock_s2d = asyncio.Lock()  # emoji must not upload it twice
_stoat_server_id: str | None = None


def _link_emoji(discord_id: int | str, stoat_id: str) -> None:
    _emoji_d2s[str(discord_id)] = stoat_id
    _emoji_s2d[stoat_id] = str(discord_id)


def _find_guild_emoji(guild: "discord.Guild | None", name: str):
    """Case-insensitive name match: Discord 'LUL' must re-link to Stoat 'lul'."""
    if guild is None:
        return None
    return discord.utils.find(lambda e: e.name.lower() == name.lower(), guild.emojis)


async def _get_stoat_server_id(session: aiohttp.ClientSession) -> str | None:
    """The Stoat server hosting the bridged channels (parent for created emojis)."""
    global _stoat_server_id
    if _stoat_server_id:
        return _stoat_server_id
    if session is None or not STOAT_CHANNEL_IDS:
        return None
    try:
        async with session.get(
            f"{REVOLT_API_URL}/channels/{STOAT_CHANNEL_IDS[0]}",
            headers={"x-bot-token": STOAT_BOT_TOKEN},
        ) as resp:
            if resp.status == 200:
                _stoat_server_id = (await resp.json()).get("server")
    except Exception as exc:
        logger.debug(f"Could not resolve Stoat server id: {exc}")
    return _stoat_server_id


def _stoat_emoji_name(name: str) -> str:
    """Stoat emoji names must be lowercase [a-z0-9_] (create rejects e.g. 'LUL')."""
    return re.sub(r"[^a-z0-9_]", "_", name.lower())[:32] or "emoji"


async def ensure_stoat_emoji(
    name: str, discord_emoji_id: int, animated: bool, session: aiohttp.ClientSession
) -> str | None:
    """Discord custom emoji → Stoat emoji id, uploading it to the Stoat server on first use."""
    key = str(discord_emoji_id)
    if key in _emoji_d2s:
        return _emoji_d2s[key]
    if session is None:
        return None
    async with _emoji_lock_d2s:
        if key in _emoji_d2s:  # created while we waited on the lock
            return _emoji_d2s[key]
        try:
            server_id = await _get_stoat_server_id(session)
            if not server_id:
                return None

            # Reuse an existing Stoat emoji with the same name. The map file
            # lives next to .env, which is RAM-only on overlayfs hosts and
            # wiped on reboot – name matching re-links instead of duplicating.
            stoat_name = _stoat_emoji_name(name)
            async with session.get(
                f"{REVOLT_API_URL}/servers/{server_id}/emojis",
                headers={"x-bot-token": STOAT_BOT_TOKEN},
            ) as resp:
                if resp.status == 200:
                    for e in await resp.json():
                        if e.get("name") == stoat_name:
                            stoat_id = _extract_id(e)
                            if stoat_id:
                                _link_emoji(key, stoat_id)
                                return stoat_id

            ext = "gif" if animated else "png"
            fetched = await fetch_bytes(
                session, f"https://cdn.discordapp.com/emojis/{discord_emoji_id}.{ext}?size=64"
            )
            if fetched is None:
                return None
            form = aiohttp.FormData()
            form.add_field("file", fetched[0], filename=f"{name}.{ext}")
            # the CDN upload endpoint requires the bot token (401 without it)
            async with session.post(
                f"{REVOLT_CDN_URL}/emojis",
                headers={"x-bot-token": STOAT_BOT_TOKEN},
                data=form,
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.warning(f"Emoji: Stoat upload of :{name}: failed: HTTP {resp.status} – {body[:200]}")
                    return None
                attachment_id = (await resp.json()).get("id")
            if not attachment_id:
                return None
            async with session.put(
                f"{REVOLT_API_URL}/custom/emoji/{attachment_id}",
                headers={"x-bot-token": STOAT_BOT_TOKEN},
                json={"name": stoat_name, "parent": {"type": "Server", "id": server_id}},
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.warning(f"Emoji: Stoat create of :{name}: failed: HTTP {resp.status} – {body[:200]}")
                    return None
                stoat_id = _extract_id(await resp.json())
            if not stoat_id:
                return None
            _link_emoji(key, stoat_id)
            logger.info(f"Emoji: mirrored Discord :{name}: to Stoat as {stoat_id}")
            return stoat_id
        except Exception as exc:
            logger.warning(f"Emoji: could not mirror Discord :{name}: to Stoat: {exc}")
            return None


async def ensure_discord_emoji(
    stoat_emoji_id: str, session: aiohttp.ClientSession, guild: "discord.Guild | None"
) -> int | None:
    """Stoat custom emoji → Discord emoji id, creating it in the bridged guild on first use."""
    if stoat_emoji_id in _emoji_s2d:
        return int(_emoji_s2d[stoat_emoji_id])
    if guild is None:
        return None
    async with _emoji_lock_s2d:
        if stoat_emoji_id in _emoji_s2d:  # created while we waited on the lock
            return int(_emoji_s2d[stoat_emoji_id])
        try:
            name = await resolve_revolt_emoji(stoat_emoji_id, session, STOAT_BOT_TOKEN)
            # Reuse by name first – same reboot-resilience as the Stoat side.
            existing = _find_guild_emoji(guild, name)
            if existing is None:
                fetched = await fetch_bytes(session, f"{REVOLT_CDN_URL}/emojis/{stoat_emoji_id}")
                if fetched is None:
                    return None
                safe_name = re.sub(r"[^A-Za-z0-9_]", "_", name)[:32] or "emoji"
                existing = await guild.create_custom_emoji(name=safe_name, image=fetched[0])
                logger.info(f"Emoji: mirrored Stoat :{name}: to Discord as {existing.id}")
            _link_emoji(existing.id, stoat_emoji_id)
            return existing.id
        except Exception as exc:
            logger.warning(f"Emoji: could not mirror Stoat emoji {stoat_emoji_id} to Discord: {exc}")
            return None


# ──────────────────────────────────────────────────────────────────────────────
#  REACTION REST HELPERS  (the bot's own reaction is the mirror – the APIs
#  offer no masquerade for reactions, so attribution and counts are not carried)
# ──────────────────────────────────────────────────────────────────────────────


async def _stoat_set_reaction(
    session: aiohttp.ClientSession, channel_id: str, message_id: str, emoji: str, *, add: bool
) -> None:
    """Add/remove the bot's own reaction on a Stoat message. emoji is a unicode emoji or a bare Stoat emoji id."""
    url = (
        f"{REVOLT_API_URL}/channels/{channel_id}/messages/{message_id}"
        f"/reactions/{quote(emoji, safe='')}"
    )
    try:
        async with (session.put if add else session.delete)(
            url, headers={"x-bot-token": STOAT_BOT_TOKEN}
        ) as resp:
            if resp.status not in (200, 204):
                body = await resp.text()
                logger.warning(f"Stoat reaction HTTP {resp.status} for {emoji}: {body[:150]}")
    except Exception as exc:
        logger.warning(f"Stoat reaction error for {emoji}: {exc}")


async def _stoat_fetch_reactions(
    session: aiohttp.ClientSession, channel_id: str, message_id: str
) -> dict[str, list[str]]:
    """Current reactions of a Stoat message: {emoji: [user ids]}."""
    if session is None:
        return {}
    try:
        async with session.get(
            f"{REVOLT_API_URL}/channels/{channel_id}/messages/{message_id}",
            headers={"x-bot-token": STOAT_BOT_TOKEN},
        ) as resp:
            if resp.status == 200:
                return (await resp.json()).get("reactions") or {}
    except Exception as exc:
        logger.debug(f"Could not fetch Stoat reactions for {message_id}: {exc}")
    return {}


async def copy_discord_reactions(
    reactions, session: aiohttp.ClientSession, stoat_channel_id: str, stoat_msg_id: str
) -> None:
    """Mirror a Discord message's reactions onto its Stoat mirror (backfill helper).

    One reaction per emoji, added as the bot – same semantics as live bridging.
    """
    for reaction in reactions:
        emoji = reaction.emoji
        if getattr(emoji, "id", None):
            stoat_emoji = await ensure_stoat_emoji(
                emoji.name, emoji.id, emoji.animated, session
            )
            if stoat_emoji is None:
                continue  # custom emoji that could not be mirrored
            emoji_str = stoat_emoji
        else:
            emoji_str = str(emoji)
        await _stoat_set_reaction(session, stoat_channel_id, stoat_msg_id, emoji_str, add=True)
        await asyncio.sleep(0.1)  # stay under the bot rate limit on long backfills


_RE_STOAT_EMOJI_ID = re.compile(r"[A-Z0-9]{26}")


async def clean_discord_content(
    content: str, message: discord.Message, session: aiohttp.ClientSession | None = None
) -> str:
    """Resolve Discord markup to plain text before forwarding to Stoat."""
    guild  = message.guild
    result = content

    for m in reversed(list(_RE_DISCORD_USER.finditer(result))):
        uid  = int(m.group(1))
        name = f"@user{uid}"
        if guild:
            member = guild.get_member(uid)
            if member is None:
                try:
                    member = await guild.fetch_member(uid)
                except Exception:
                    member = None
            if member:
                name = f"@{member.display_name}"
        result = result[: m.start()] + name + result[m.end() :]

    def _channel(m: re.Match) -> str:
        ch = guild.get_channel(int(m.group(1))) if guild else None
        return f"#{ch.name}" if ch else "#channel"

    result = _RE_DISCORD_CHANNEL.sub(_channel, result)

    def _role(m: re.Match) -> str:
        role = guild.get_role(int(m.group(1))) if guild else None
        return f"@{role.name}" if role else "@role"

    result = _RE_DISCORD_ROLE.sub(_role, result)

    # custom emojis: mirror to Stoat so they render natively (:name: text is
    # the fallback when the emoji could not be mirrored)
    matches = list(_RE_DISCORD_EMOJI.finditer(result))
    if matches:
        stoat_ids = await asyncio.gather(
            *[
                ensure_stoat_emoji(m.group(2), int(m.group(3)), m.group(1) == "a", session)
                for m in matches
            ]
        )
        for m, sid in zip(reversed(matches), reversed(stoat_ids)):
            repl = f":{sid}:" if sid else f":{m.group(2)}:"
            result = result[: m.start()] + repl + result[m.end():]

    return result


async def clean_stoat_content(
    content: str,
    session: aiohttp.ClientSession,
    token: str,
    guild: "discord.Guild | None" = None,
) -> str:
    """Resolve Stoat markup to plain text before forwarding to Discord."""
    result = content

    for m in reversed(list(_RE_REVOLT_USER.finditer(result))):
        uid  = m.group(1)
        name = "@user"
        try:
            async with session.get(
                f"{REVOLT_API_URL}/users/{uid}",
                headers={"x-bot-token": token},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    name = "@" + (data.get("display_name") or data.get("username") or uid)
        except Exception as exc:
            logger.debug(f"Could not resolve Revolt user {uid}: {exc}")
        result = result[: m.start()] + name + result[m.end() :]

    matches = list(_RE_REVOLT_CUSTOM_EMO.finditer(result))
    if matches:
        names = await asyncio.gather(
            *[resolve_revolt_emoji(m.group(1), session, token) for m in matches]
        )
        d_ids = await asyncio.gather(
            *[ensure_discord_emoji(m.group(1), session, guild) for m in matches]
        )
        for m, name, d_id in zip(reversed(matches), reversed(names), reversed(d_ids)):
            repl = f"<:{name}:{d_id}>" if d_id else f":{name}:"
            result = result[: m.start()] + repl + result[m.end() :]

    return result


# ──────────────────────────────────────────────────────────────────────────────
#  STOAT BOT
# ──────────────────────────────────────────────────────────────────────────────


class StoatBot(stoat.Client):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._http_session: aiohttp.ClientSession | None = None
        self._discord_bot: "DiscordBot | None" = None  # set in main(); triggers catch-up
        self._healthpulse: bool = False
        self._health_msg_id: str | None = None
        self._planned_restart: bool = False  # set by _do_restart(); read by the supervisor in main()
        self._s2d_catchup_running = False    # Stoat→Discord catch-up (see _run_s2d_catchup)
        self._recently_live: OrderedDict[str, None] = OrderedDict()  # stoat msg ids forwarded live

    async def on_ready(self, event, /):
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = aiohttp.ClientSession()

        logger.info(f"Stoat: on_ready fired as {self.me} – running health check…")

        stoat_channels.clear()
        for stoat_id in STOAT_CHANNEL_IDS:
            try:
                ch = await self.fetch_channel(stoat_id)
                stoat_channels[stoat_id] = ch
            except Exception as exc:
                logger.error(f"Stoat: could not fetch channel {stoat_id} - {exc}")

        await self._run_health_check()

        # After (re)connecting, catch up on Discord messages missed while the
        # Stoat session was down.
        if self._discord_bot is not None and not self._planned_restart:
            self._discord_bot._maybe_start_catchup()
            self._discord_bot._maybe_sync_emojis()
            self._start_s2d_catchup()

    async def _run_health_check(self) -> None:
        """
        This is due to problems in stoat's API
        Send a message into stoat channel
        If on_message triggers: Succesfull, message deleted again
        If on_message doesnt trigger: Not successfull, restart and message deleted
        """
        if not STOAT_CHANNEL_IDS:
            logger.warning("Stoat: health check skipped – no channels configured")
            self._log_channels_ready()
            return

        first_id = STOAT_CHANNEL_IDS[0]
        ch = stoat_channels.get(first_id)
        if ch is None:
            logger.warning("Stoat: health check skipped – channel not fetched")
            return

        self._healthpulse   = False
        self._health_msg_id = None

        try:
            sent = await ch.send(content="🔧")
            self._health_msg_id = _extract_id(sent)
            logger.info("Stoat: health check message sent, waiting 5s…")
        except Exception as exc:
            logger.error(f"Stoat: health check send failed: {exc} - restarting")
            self._do_restart()
            return

        await asyncio.sleep(5)

        # Always delete the test message — regardless of outcome
        if self._health_msg_id:
            await self._delete_health_message(first_id, self._health_msg_id)
            self._health_msg_id = None

        if self._healthpulse:
            logger.info("Stoat: ✓ health check passed")
            self._log_channels_ready()
        else:
            logger.error(
                "Stoat: ✗ health check FAILED"
                "Restarting process…"
            )
            self._do_restart()

    async def _delete_health_message(self, channel_id: str, message_id: str) -> None:
        if self._http_session is None or self._http_session.closed:
            return
        try:
            async with self._http_session.delete(
                f"{REVOLT_API_URL}/channels/{channel_id}/messages/{message_id}",
                headers={"x-bot-token": STOAT_BOT_TOKEN},
            ) as resp:
                if resp.status in (200, 204):
                    logger.debug("Stoat: health check message deleted")
                else:
                    logger.debug(f"Stoat: health message delete returned HTTP {resp.status}")
        except Exception as exc:
            logger.debug(f"Stoat: could not delete health message: {exc}")

    def _log_channels_ready(self) -> None:
        for stoat_id, ch in stoat_channels.items():
            logger.info(f"Stoat: ✓ listening in #{ch.name} (id={stoat_id})")

    def _do_restart(self) -> None:
        """
        Now a soft restart instead of yeeting away the whole process. The supervisor in main() will see the _planned_restart flag and restart the bot.
        """
        logger.warning("Stoat: closing session for a scheduled restart…")
        self._planned_restart = True
        asyncio.create_task(self.close())

    async def on_error(self, event, /):
        logger.error(f"Stoat: ⚠ Error event from gateway: {event!r}")

    # ── Send a DM on Stoat ────────────────────────────────────────────────────

    async def _try_send_stoat_dm(self, user_id: str) -> None:
        """Send a welcome DM to a Stoat user."""
        session = self._http_session
        if session is None:
            return
        try:
            # Open (or fetch existing) DM channel with the user
            async with session.get(
                f"{REVOLT_API_URL}/users/{user_id}/dm",
                headers={"x-bot-token": STOAT_BOT_TOKEN},
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"Stoat: open DM channel failed for {user_id}: HTTP {resp.status}")
                    return
                dm_data = await resp.json()

            dm_channel_id = dm_data.get("_id") or dm_data.get("id")
            if not dm_channel_id:
                logger.debug(f"Stoat: no channel ID in DM response for {user_id}")
                return

            # Send the welcome message into the DM channel
            async with session.post(
                f"{REVOLT_API_URL}/channels/{dm_channel_id}/messages",
                headers={"x-bot-token": STOAT_BOT_TOKEN},
                json={"content": STOAT_WELCOME_DM[:2000]},
            ) as resp:
                if resp.status in (200, 201):
                    logger.info(f"Stoat: sent welcome DM to user {user_id}")
                else:
                    body = await resp.text()
                    logger.debug(f"Stoat: DM send failed for {user_id}: HTTP {resp.status} – {body[:200]}")

        except Exception as exc:
            logger.debug(f"Stoat: could not DM user {user_id}: {exc}")

    async def on_message_create(self, event: stoat.MessageCreateEvent, /):
        msg = event.message

        if msg.author_id == self.me.id:
            self._healthpulse = True   # health check: own message received
            return

        stoat_id = msg.channel.id
        if stoat_id not in STOAT_TO_DISCORD:
            return

        # ── First-time DM ─────────────────────────────────────────────────────
        uid = str(msg.author_id)
        if SEND_WELCOME_DMS and not _is_notified("stoat", uid):
            _mark_notified("stoat", uid)
            asyncio.create_task(self._try_send_stoat_dm(uid))

        # Remember live-forwarded ids so the Stoat→Discord catch-up can never
        # re-send a message that arrived while it was walking the history.
        self._remember_live(str(msg.id))
        await self._bridge_stoat_message(msg)

    def _remember_live(self, stoat_msg_id: str) -> None:
        self._recently_live[stoat_msg_id] = None
        while len(self._recently_live) > MSG_CACHE_SIZE:
            self._recently_live.popitem(last=False)

    async def _bridge_stoat_message(self, msg) -> None:
        """Forward one Stoat-native message to its Discord mirror (live or catch-up)."""
        stoat_id   = msg.channel.id
        discord_id = STOAT_TO_DISCORD[stoat_id]
        webhook    = discord_webhooks.get(discord_id)
        if webhook is None:
            logger.warning(f"Stoat -> Discord: webhook for {discord_id} not ready, dropped")
            return

        # ── Clean content ─────────────────────────────────────────────────────
        content = await clean_stoat_content(
            msg.content or "", self._http_session, STOAT_BOT_TOKEN, guild=webhook.guild
        )

        # ── Reply → quote fallback ────────────────────────────────────────────
        replies_raw = getattr(msg, "replies", None) or []
        reply_id: str | None = None
        if replies_raw:
            first    = replies_raw[0]
            reply_id = _extract_id(first) or (str(first) if isinstance(first, str) else None)

        if reply_id:
            orig = await fetch_stoat_message(stoat_id, reply_id, self)
            if orig is not None:
                orig_author  = orig.author.display_name[:50]
                orig_snippet = (orig.content or "")[:80].replace("\n", " ")
                content = f"-# ↩ **{orig_author}**: *{orig_snippet}*\n{content}"
            else:
                logger.warning(f"Stoat -> Discord: could not fetch reply target '{reply_id}'")

        # ── Attachments ───────────────────────────────────────────────────────
        discord_files: list[discord.File] = []
        for att in getattr(msg, "attachments", None) or []:
            url      = _stoat_asset_url(att)
            filename = getattr(att, "filename", None) or "file"
            if not url:
                continue
            result = await fetch_bytes(self._http_session, url)
            if result is None:
                content += f"\n{url}"
                continue
            data, fname = result
            discord_files.append(discord.File(io.BytesIO(data), filename=filename or fname))
            del data

        if not content.strip() and not discord_files:
            return

        author_name = (
            getattr(msg.author, "display_name", None)
            or getattr(msg.author, "name", None)
            or "unknown"
        )[:80]
        avatar_url = _stoat_asset_url(getattr(msg.author, "avatar", None))

        try:
            sent = await webhook.send(
                content    = content[:2000] if content.strip() else discord.utils.MISSING,
                username   = author_name,
                avatar_url = avatar_url,
                files      = discord_files or discord.utils.MISSING,
                wait       = True,
            )
            _cache_pair(sent.id, str(msg.id), from_webhook=True)
            logger.debug(f"Stoat -> Discord: ✓ forwarded message from {author_name}")
        except Exception as exc:
            logger.error(f"Stoat -> Discord (channel {discord_id}): {exc}")
        finally:
            for f in discord_files:
                f.fp.close()

    # ── Message deletion: Stoat → delete on Discord ───────────────────────────

    async def on_message_delete(self, event, /):
        """A Stoat-native message was deleted → remove its Discord mirror.

        Mirrors of Discord-user messages are never touched from the Stoat side:
        Stoat is the archive, the Discord original must stay.
        """
        try:
            msg_id = _extract_id(
                getattr(event, "message_id", None) or getattr(event, "id", None)
            )
            if msg_id is None:
                msg_id = _extract_id(getattr(event, "message", None))
            if msg_id is None:
                return

            discord_msg_id = _s2d.get(str(msg_id))
            if discord_msg_id is None:
                return  # not a bridged message
            if discord_msg_id not in _webhook_discord_ids:
                return  # Discord-user original mirrored on Stoat – hands off

            for wh in discord_webhooks.values():
                try:
                    await wh.delete_message(discord_msg_id)
                    break
                except discord.NotFound:
                    break  # already gone
                except Exception:
                    continue  # try the next webhook
            _webhook_discord_ids.discard(discord_msg_id)
        except Exception as exc:
            logger.error(f"on_message_delete (Stoat): unexpected error: {exc}")

    # ── Reactions: Stoat → Discord ─────────────────────────────────────────────

    async def on_message_react(self, event, /):
        if event.user_id == self.me.id:
            return  # our own Discord→Stoat mirror, don't loop
        discord_id, discord_msg_id = self._reaction_target(event)
        if discord_id is None:
            return
        channel = self._discord_bot.get_channel(discord_id) if self._discord_bot else None
        if channel is None:
            return
        emoji = await self._discord_reaction_emoji(event.emoji, channel.guild)
        if emoji is None:
            return  # unmapped custom emoji – nothing to react with
        try:
            await channel.get_partial_message(discord_msg_id).add_reaction(emoji)
        except discord.HTTPException as exc:
            logger.warning(f"Stoat -> Discord: reaction add failed: {exc}")

    async def on_message_unreact(self, event, /):
        if event.user_id == self.me.id:
            return  # triggered by our own Discord→Stoat mirror removal
        discord_id, discord_msg_id = self._reaction_target(event)
        if discord_id is None:
            return
        # keep the Discord mirror while anyone on Stoat still reacts with it
        users = (
            await _stoat_fetch_reactions(
                self._http_session, str(event.channel_id), str(event.message_id)
            )
        ).get(event.emoji) or []
        if users:
            return
        await self._remove_discord_reaction(event, discord_id, discord_msg_id)

    async def on_message_remove_reaction(self, event, /):
        # a moderator removed a reaction – same reconcile as a plain unreact
        await self.on_message_unreact(event)

    async def on_message_clear_reaction(self, event, /):
        discord_id, discord_msg_id = self._reaction_target(event)
        if discord_id is None:
            return
        await self._remove_discord_reaction(event, discord_id, discord_msg_id)

    def _reaction_target(self, event) -> tuple[int | None, int]:
        """(discord channel id, discord message id) for a Stoat reaction event, or (None, 0)."""
        discord_id = STOAT_TO_DISCORD.get(str(event.channel_id))
        discord_msg_id = _s2d.get(str(event.message_id))
        if discord_id is None or discord_msg_id is None:
            return None, 0  # not a bridged message, or older than the ID cache
        return discord_id, discord_msg_id

    async def _remove_discord_reaction(self, event, discord_id: int, discord_msg_id: int) -> None:
        channel = self._discord_bot.get_channel(discord_id) if self._discord_bot else None
        if channel is None or self._discord_bot is None:
            return
        emoji = await self._discord_reaction_emoji(event.emoji, channel.guild, create=False)
        if emoji is None:
            return
        try:
            await channel.get_partial_message(discord_msg_id).remove_reaction(
                emoji, self._discord_bot.user
            )
        except discord.NotFound:
            pass  # we never mirrored that one
        except discord.HTTPException as exc:
            logger.warning(f"Stoat -> Discord: reaction remove failed: {exc}")

    async def _discord_reaction_emoji(
        self, stoat_emoji: str, guild: "discord.Guild | None", create: bool = True
    ):
        """Map a Stoat reaction emoji (unicode or custom ULID) to a Discord emoji."""
        if not _RE_STOAT_EMOJI_ID.fullmatch(stoat_emoji):
            return stoat_emoji  # unicode emoji, passes straight through
        name = await resolve_revolt_emoji(stoat_emoji, self._http_session, STOAT_BOT_TOKEN)
        d_id: int | None = None
        if create:
            d_id = await ensure_discord_emoji(stoat_emoji, self._http_session, guild)
        else:
            mapped = _emoji_s2d.get(stoat_emoji)
            if mapped:
                d_id = int(mapped)
            else:
                existing = _find_guild_emoji(guild, name)
                d_id = existing.id if existing else None
        return discord.PartialEmoji(name=name, id=d_id) if d_id else None

    # ── Catch-up: Stoat → Discord ──────────────────────────────────────────────

    def _start_s2d_catchup(self) -> None:
        """After (re)connecting, forward Stoat-native messages missed while down."""
        if self._s2d_catchup_running:
            return
        self._s2d_catchup_running = True
        asyncio.create_task(self._run_s2d_catchup())

    async def _run_s2d_catchup(self) -> None:
        """Forward Stoat-native messages sent while the Stoat session was down.

        They are APPENDED as new webhook messages – Discord history is never
        rewritten. Fail-safe like the Discord→Stoat catch-up: each channel's
        newest mirrors on Discord are fingerprinted, the Stoat history is
        walked backwards until a fingerprint matches, and everything newer is
        forwarded; with no overlap found the channel is skipped rather than
        risk duplicate messages on Discord.
        """
        try:
            dbot   = self._discord_bot
            session = self._http_session
            if dbot is None or session is None:
                return
            headers  = {"x-bot-token": STOAT_BOT_TOKEN}
            me_id    = _extract_id(self.me)
            our_whs  = {wh.id for wh in discord_webhooks.values()}

            for stoat_id, discord_id in STOAT_TO_DISCORD.items():
                channel = dbot.get_channel(discord_id)
                webhook = discord_webhooks.get(discord_id)
                if channel is None or webhook is None:
                    continue

                # 1. fingerprints of our newest mirrors on Discord
                mirror_fps: set[tuple[str, str, int]] = set()
                async for m in channel.history(limit=200):
                    if m.webhook_id in our_whs:
                        mirror_fps.add(
                            (m.author.name, dbot._s2d_fp(m.content or ""), len(m.attachments))
                        )
                if not mirror_fps:
                    # nothing mirrored yet – no baseline to match against
                    logger.debug(f"S2D catch-up: no mirrors in Discord channel {discord_id} – skipped")
                    continue

                # 2. walk Stoat history backwards from the newest message; live
                #    messages arriving now have newer ids and stay outside the
                #    walk (plus _recently_live guards the connect race)
                user_cache: dict[str, dict] = {}
                missed: list[tuple[dict, dict]] = []
                before: str | None = None
                matched = False
                walked  = 0
                while walked < 500:
                    url = (
                        f"{REVOLT_API_URL}/channels/{stoat_id}/messages"
                        f"?limit=100&sort=Latest&include_users=false"
                    )
                    if before:
                        url += "&before=" + before
                    try:
                        async with session.get(url, headers=headers) as resp:
                            if resp.status != 200:
                                logger.warning(
                                    f"S2D catch-up: fetch HTTP {resp.status} in {stoat_id}"
                                )
                                break
                            batch = await resp.json()
                    except Exception as exc:
                        logger.warning(f"S2D catch-up: fetch error in {stoat_id}: {exc}")
                        break
                    if not isinstance(batch, list) or not batch:
                        break

                    for raw in batch:
                        walked += 1
                        if raw.get("author") == me_id:
                            continue  # our own Discord→Stoat mirror
                        if str(raw["_id"]) in self._recently_live:
                            matched = True  # the live path took over from here
                            break
                        user = await _resolve_stoat_user(session, user_cache, raw.get("author"))
                        name = (
                            user.get("display_name") or user.get("username") or "unknown"
                        )[:80]
                        content = await clean_stoat_content(
                            raw.get("content") or "", session, STOAT_BOT_TOKEN
                        )
                        fp = (name, dbot._s2d_fp(content[:2000]), len(raw.get("attachments") or []))
                        if fp in mirror_fps:
                            matched = True
                            break
                        missed.append((raw, user))
                    if matched or len(batch) < 100:
                        break
                    before = str(batch[-1]["_id"])

                if not matched:
                    if missed:
                        # fail safe: a miss is better than duplicate messages
                        logger.warning(
                            f"S2D catch-up: no overlap found in Stoat channel {stoat_id} "
                            f"within 500 messages – nothing forwarded"
                        )
                    continue

                # 3. forward what was missed, oldest first
                for raw, user in reversed(missed):
                    await self._bridge_stoat_message(_stoat_msg_from_raw(raw, user))
                if missed:
                    logger.info(
                        f"S2D catch-up: forwarded {len(missed)} Stoat message(s) "
                        f"to Discord channel {discord_id}"
                    )
        finally:
            self._s2d_catchup_running = False


# ──────────────────────────────────────────────────────────────────────────────
#  DISCORD BOT
# ──────────────────────────────────────────────────────────────────────────────


class DiscordBot(commands.Bot):

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds          = True
        intents.webhooks        = True
        intents.members         = True
        super().__init__(command_prefix="!", intents=intents)

        # Set by the Stoat supervisor in main(); used to edit Stoat messages
        self._stoat_bot: StoatBot | None = None

        # Startup catch-up state (see run_catchup)
        self._catching_up = False
        self._catchup_running = False
        self._pending: list[discord.Message] = []
        self._catchup_highwater: dict[int, int] = {}

        self._emoji_sync_done = False  # bulk emoji mirror runs once per boot

    def _bridged_guilds(self) -> set[discord.Guild]:
        """The Discord guilds hosting the bridged channels."""
        return {
            c.guild
            for c in (self.get_channel(i) for i in DISCORD_CHANNEL_IDS)
            if isinstance(c, discord.TextChannel)
        }

    def _maybe_sync_emojis(self) -> None:
        """Bulk-mirror all guild emojis once both sides are connected."""
        if self._emoji_sync_done:
            return
        stoat_bot = self._stoat_bot
        if stoat_bot is None or stoat_bot._http_session is None or not self.is_ready():
            return
        self._emoji_sync_done = True
        asyncio.create_task(self._sync_all_emojis(stoat_bot._http_session))

    async def _sync_all_emojis(self, session: aiohttp.ClientSession) -> None:
        """Mirror every custom emoji of the bridged guilds to the Stoat server.

        Already-mirrored emojis re-link by name (no state file – see the emoji
        sync section); only genuinely new ones are uploaded.
        """
        total = ok = 0
        for guild in self._bridged_guilds():
            for emoji in guild.emojis:
                total += 1
                if await ensure_stoat_emoji(emoji.name, emoji.id, emoji.animated, session):
                    ok += 1
                else:
                    logger.warning(
                        f"Emoji: bulk sync could not mirror :{emoji.name}: – it will retry on next use or reboot"
                    )
                await asyncio.sleep(0.5)  # stay clear of the bot rate limit
        logger.info(f"Emoji: bulk sync done – {ok}/{total} guild emoji(s) available on Stoat")

    async def on_guild_emojis_update(self, guild, before, after):
        """New custom emojis on Discord are mirrored to Stoat right away."""
        stoat_bot = self._stoat_bot
        if stoat_bot is None or stoat_bot._http_session is None:
            return
        if guild not in self._bridged_guilds():
            return
        for emoji in after:
            if emoji in before:
                continue  # unchanged or deleted
            await ensure_stoat_emoji(
                emoji.name, emoji.id, emoji.animated, stoat_bot._http_session
            )

    async def setup_hook(self):
        self.loop.create_task(self._setup_webhooks())

    async def _setup_webhooks(self):
        await self.wait_until_ready()
        for discord_id in DISCORD_CHANNEL_IDS:
            try:
                channel = self.get_channel(discord_id) or await self.fetch_channel(discord_id)
                for wh in await channel.webhooks():
                    if wh.user == self.user:
                        discord_webhooks[discord_id] = wh
                        logger.info(f"Discord: reusing webhook '{wh.name}' for channel {discord_id}")
                        break
                else:
                    wh = await channel.create_webhook(name="Stoat Bridge")
                    discord_webhooks[discord_id] = wh
                    logger.info(f"Discord: created webhook for channel {discord_id}")
            except Exception as exc:
                logger.error(f"Discord: could not set up webhook for channel {discord_id} - {exc}")

    async def on_ready(self):
        logger.info(f"Discord: connected as {self.user}")
        logger.info(f"Discord: bridging {PAIR_COUNT} channel pair(s)")
        self._maybe_start_catchup()
        self._maybe_sync_emojis()

    # ── Send a DM on Discord ──────────────────────────────────────────────────

    async def _try_send_discord_dm(self, user: discord.User | discord.Member) -> None:
        """Send a welcome DM to a Discord user."""
        try:
            embed = discord.Embed(
                title="📡 Stoat ↔ Discord Bridge",
                description=DISCORD_WELCOME_DM,
                colour=discord.Colour.from_str("#FF6B35"),
            )
            embed.set_footer(text="This message was sent once because you wrote in a bridged channel.")
            await user.send(embed=embed)
            logger.info(f"Discord: sent welcome DM to {user} ({user.id})")
        except discord.Forbidden:
            logger.debug(f"Discord: DMs disabled for {user} ({user.id})")
        except Exception as exc:
            logger.debug(f"Discord: could not DM {user}: {exc}")

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        if message.webhook_id is not None:
            if message.webhook_id in {wh.id for wh in discord_webhooks.values()}:
                return

        discord_id = message.channel.id
        if discord_id not in DISCORD_TO_STOAT:
            return

        # Queue live messages while a catch-up pass runs, so the walk and the
        # queue can never forward the same message twice.
        if self._catching_up:
            self._pending.append(message)
            return

        await self._bridge_message(message)

    async def _bridge_message(self, message: discord.Message):
        """Forward one Discord message to its Stoat mirror (live or catch-up)."""
        discord_id = message.channel.id

        # ── First-time DM ─────────────────────────────────────────────────────
        uid = str(message.author.id)
        if SEND_WELCOME_DMS and not _is_notified("discord", uid):
            _mark_notified("discord", uid)
            asyncio.create_task(self._try_send_discord_dm(message.author))

        stoat_id = DISCORD_TO_STOAT[discord_id]
        ch       = stoat_channels.get(stoat_id)
        if ch is None:
            logger.warning(f"Discord -> Stoat: channel {stoat_id} not ready, dropped")
            return

        # ── Content ───────────────────────────────────────────────────────────
        stoat_session = self._stoat_bot._http_session if self._stoat_bot else None
        content = await clean_discord_content(message.content or "", message, stoat_session)

        # ── Reply ─────────────────────────────────────────────────────────────
        stoat_replies: list = []
        if message.reference and message.reference.message_id:
            ref_discord_id  = message.reference.message_id
            cached_stoat_id = _d2s.get(ref_discord_id)

            if cached_stoat_id:
                stoat_replies = [SimpleNamespace(id=cached_stoat_id, mention=False)]
                logger.debug(
                    f"Discord -> Stoat: native reply to stoat_id={cached_stoat_id} "
                    f"(from discord ref={ref_discord_id})"
                )
            else:
                logger.debug(
                    f"Discord -> Stoat: reply ref={ref_discord_id} not in cache, using quote"
                )
                try:
                    ref_msg = (
                        message.reference.resolved
                        if isinstance(message.reference.resolved, discord.Message)
                        else await message.channel.fetch_message(ref_discord_id)
                    )
                    ref_author  = ref_msg.author.display_name[:50]
                    ref_snippet = (ref_msg.content or "")[:80].replace("\n", " ")
                    content = f"-# ↩ **{ref_author}**: *{ref_snippet}*\n{content}"
                except Exception as exc:
                    logger.debug(f"Could not fetch Discord reply target {ref_discord_id}: {exc}")

        # ── Attachments ───────────────────────────────────────────────────────
        for att in message.attachments:
            content += f" {att.url}"

        if not content.strip():
            return

        avatar_url = (
            str(message.author.avatar.url)
            if message.author.avatar
            else str(message.author.default_avatar.url)
        )

        send_kwargs: dict = {
            "masquerade": stoat.Masquerade(
                name=message.author.display_name[:32],
                avatar=avatar_url,
            ),
            "content": content[:2000],
        }
        if stoat_replies:
            send_kwargs["replies"] = stoat_replies

        try:
            sent = await ch.send(**send_kwargs)

            sent_id = _extract_id(sent)
            if sent_id:
                _cache_pair(message.id, sent_id)
                logger.debug(f"Discord -> Stoat: ✓ forwarded message from {message.author.display_name}")
            else:
                logger.warning(
                    f"Discord -> Stoat: could not extract ID from sent object "
                    f"type={type(sent).__name__!r} repr={sent!r:.200}"
                )
        except Exception as exc:
            logger.error(f"Discord -> Stoat (channel {stoat_id}): {exc}")

    # ── Startup catch-up: Discord → Stoat ─────────────────────────────────────

    def _maybe_start_catchup(self) -> None:
        """Start a catch-up pass once both sides are connected."""
        if self._catchup_running or self._catching_up:
            return
        stoat_bot = self._stoat_bot
        if (
            stoat_bot is None
            or stoat_bot._http_session is None
            or not stoat_channels
            or not self.is_ready()
        ):
            return
        self._catchup_running = True
        asyncio.create_task(self.run_catchup(stoat_bot))

    @staticmethod
    def _fp_normalize(content: str) -> str:
        """Normalize message content for catch-up matching: drop the
        reply-quote line (native replies and quote fallbacks must compare
        equal) and URL query strings (Discord attachment URLs are signed and
        differ between fetches)."""
        if content.startswith("-# ↩ "):
            content = content.split("\n", 1)[1] if "\n" in content else ""
        return " ".join(
            t.split("?", 1)[0] if t.startswith("http") else t
            for t in content.split()
        )

    @staticmethod
    def _s2d_fp(content: str) -> str:
        """Stoat→Discord catch-up fingerprint: _fp_normalize plus custom
        emojis collapsed to :name: — Discord mirrors carry <:name:id> while
        the Stoat originals carry :ULID:, and both must compare equal without
        depending on emoji mirroring having succeeded."""
        content = DiscordBot._fp_normalize(content)
        content = re.sub(r"<a?:([A-Za-z0-9_]+):\d+>", r":\1:", content)
        return content

    async def _fingerprint(self, message: discord.Message, session=None) -> tuple[str, str]:
        """Stable (name, content) fingerprint of a Discord message as archived."""
        content = await clean_discord_content(message.content or "", message, session)
        for att in message.attachments:
            content += f" {att.url}"
        return message.author.display_name[:32], self._fp_normalize(content)[:2000]

    async def run_catchup(self, stoat_bot: "StoatBot") -> None:
        """Forward Discord messages sent while the bridge (or Stoat) was down.

        The newest bot-authored Stoat messages are the record of what is
        already archived; walk the Discord history backwards until a message
        matches one, then bridge everything newer, oldest first. Reading the
        watermark from Stoat itself (not a local file) keeps this correct
        across crashes and reboots.
        """
        self._catching_up = True
        self._catchup_highwater = {}
        logger.info("Catch-up: scanning for messages missed while the bridge was down")
        try:
            session = stoat_bot._http_session
            headers = {"x-bot-token": STOAT_BOT_TOKEN}
            for stoat_id, discord_id in DISCORD_TO_STOAT.items():
                channel = self.get_channel(discord_id)
                ch = stoat_channels.get(stoat_id)
                if channel is None or ch is None:
                    continue

                # 1. fingerprints of what is already on the Stoat mirror
                known: set[tuple[str, str]] = set()
                try:
                    async with session.get(
                        f"{REVOLT_API_URL}/channels/{stoat_id}/messages?limit=100&sort=Latest",
                        headers=headers,
                    ) as resp:
                        if resp.status == 200:
                            for m in await resp.json():
                                masq = m.get("masquerade") or {}
                                if masq.get("name") is not None:
                                    known.add(
                                        (masq["name"], self._fp_normalize(m.get("content") or ""))
                                    )
                except Exception as exc:
                    logger.error(f"Catch-up: could not read Stoat channel {stoat_id}: {exc}")
                    continue
                if not known:
                    # shortcut: empty mirror (new pair) or unreadable – forwarding
                    # blind would duplicate a healthy archive, so do nothing
                    logger.warning(
                        f"Catch-up: no recent bot messages in Stoat channel {stoat_id} – skipped"
                    )
                    continue

                # 2. walk Discord history backwards until an archived message matches.
                #    shortcut: webhook messages are never matched or forwarded (ours
                #    would loop); a foreign webhook message sent during downtime is
                #    missed – upgrade to per-webhook filtering if that matters
                missed: list[discord.Message] = []
                highwater = 0
                matched = False
                async for msg in channel.history(limit=500):
                    if highwater == 0:
                        highwater = msg.id
                    if msg.author == self.user or msg.webhook_id is not None:
                        continue
                    name, content = await self._fingerprint(msg, session)
                    if (name, content) in known:
                        matched = True
                        break
                    if not content.strip():
                        continue
                    missed.append(msg)
                self._catchup_highwater[discord_id] = highwater

                if not matched:
                    if missed:
                        # fail safe: a miss is better than mass duplication
                        logger.warning(
                            f"Catch-up: no overlap found in #{channel.name} within 500 "
                            f"messages – nothing forwarded; run a manual "
                            f"backfill --resume if the gap is real"
                        )
                    continue

                # 3. forward what was missed, oldest first
                for msg in reversed(missed):
                    await self._bridge_message(msg)
                if missed:
                    logger.info(f"Catch-up: #{channel.name}: forwarded {len(missed)} missed message(s)")
        finally:
            self._catchup_running = False
            # flush live messages queued while the walks ran; anything the walk
            # already covered (id <= highwater) is skipped
            while self._pending:
                message = self._pending.pop(0)
                if message.id > self._catchup_highwater.get(message.channel.id, 0):
                    try:
                        await self._bridge_message(message)
                    except Exception as exc:
                        logger.error(f"Catch-up flush: {message.id}: {exc}")
            self._catching_up = False
            logger.info("Catch-up: done, live bridging resumed")

    # ── Message deletion: Discord → strikethrough on Stoat ────────────────────

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        """A Discord message was deleted → strike through its Stoat mirror.

        Stoat is the archive: the content stays, marked as deleted.
        """
        if payload.channel_id not in DISCORD_TO_STOAT:
            return
        stoat_msg_id = _d2s.get(payload.message_id)
        if stoat_msg_id is None:
            return  # not a bridged message, or older than the ID cache

        if self._stoat_bot is None:
            logger.warning("Discord -> Stoat: _stoat_bot reference not set, cannot strikethrough")
            return

        stoat_ch_id = DISCORD_TO_STOAT[payload.channel_id]
        orig = await fetch_stoat_message(stoat_ch_id, stoat_msg_id, self._stoat_bot)
        content = (orig.content if orig else "") or "🗑 deleted on Discord"
        # per line: markdown strikethrough does not span paragraphs
        struck = "\n".join(
            f"~~{line}~~" if line.strip() else line for line in content.split("\n")
        )
        await edit_stoat_message(stoat_ch_id, stoat_msg_id, struck, self._stoat_bot)

    # ── Reactions: Discord → Stoat ─────────────────────────────────────────────

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._mirror_reaction_to_stoat(payload, add=True)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        # only drop the Stoat mirror once nobody on Discord reacts with it anymore
        try:
            channel = self.get_channel(payload.channel_id)
            if channel is not None:
                msg = await channel.fetch_message(payload.message_id)
                reaction = discord.utils.get(msg.reactions, emoji=payload.emoji)
                if reaction is not None and reaction.count > 0:
                    return
        except discord.HTTPException:
            pass  # message gone or unreadable – mirror the removal anyway
        await self._mirror_reaction_to_stoat(payload, add=False)

    async def on_raw_reaction_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent):
        await self._mirror_reaction_to_stoat(payload, add=False)

    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent):
        stoat_bot = self._stoat_bot
        stoat_ch_id = DISCORD_TO_STOAT.get(payload.channel_id)
        stoat_msg_id = _d2s.get(payload.message_id)
        if stoat_bot is None or stoat_bot._http_session is None:
            return
        if stoat_ch_id is None or stoat_msg_id is None:
            return
        # drop every one of the bot's own reactions on the Stoat mirror
        me = _extract_id(stoat_bot.me)
        for emoji, users in (
            await _stoat_fetch_reactions(stoat_bot._http_session, stoat_ch_id, stoat_msg_id)
        ).items():
            if me in users:
                await _stoat_set_reaction(
                    stoat_bot._http_session, stoat_ch_id, stoat_msg_id, emoji, add=False
                )

    async def _mirror_reaction_to_stoat(self, payload, *, add: bool) -> None:
        if getattr(payload, "user_id", None) == self.user.id:
            return  # our own Stoat→Discord mirror, don't loop
        stoat_ch_id = DISCORD_TO_STOAT.get(payload.channel_id)
        stoat_msg_id = _d2s.get(payload.message_id)
        if stoat_ch_id is None or stoat_msg_id is None:
            return  # not a bridged message, or older than the ID cache
        stoat_bot = self._stoat_bot
        if stoat_bot is None or stoat_bot._http_session is None:
            return
        emoji = payload.emoji
        if emoji.id:
            stoat_emoji = await ensure_stoat_emoji(
                emoji.name, emoji.id, emoji.animated, stoat_bot._http_session
            )
            if stoat_emoji is None:
                return  # can't react with an unmapped custom emoji
            emoji_str = stoat_emoji
        else:
            emoji_str = emoji.name
        await _stoat_set_reaction(
            stoat_bot._http_session, stoat_ch_id, stoat_msg_id, emoji_str, add=add
        )

    # ── Channel lifecycle: Discord → Stoat ────────────────────────────────────
    #  Channels created on Stoat are NOT created on Discord, and channels
    #  deleted on either side are NOT deleted on the other: Stoat is the
    #  archive, its structure only follows Discord's growth.

    async def on_guild_channel_create(self, channel):
        """A text channel created on Discord is mirrored and bridged on Stoat."""
        if not isinstance(channel, discord.TextChannel):
            return
        if channel.id in DISCORD_TO_STOAT:
            return
        stoat_bot = self._stoat_bot
        if stoat_bot is None or stoat_bot._http_session is None or not STOAT_CHANNEL_IDS:
            logger.warning("Discord -> Stoat: channel create ignored – Stoat not ready yet")
            return
        # Only mirror channels in the guild we are bridging.
        if channel.guild not in self._bridged_guilds():
            return

        session = stoat_bot._http_session
        headers = {"x-bot-token": STOAT_BOT_TOKEN}
        try:
            # The Stoat server that hosts our bridged channels.
            async with session.get(
                f"{REVOLT_API_URL}/channels/{STOAT_CHANNEL_IDS[0]}", headers=headers
            ) as resp:
                resp.raise_for_status()
                server_id = (await resp.json()).get("server")
            if not server_id:
                logger.error("Discord -> Stoat: could not determine the Stoat server id")
                return
            async with session.post(
                f"{REVOLT_API_URL}/servers/{server_id}/channels",
                headers=headers,
                json={"name": channel.name, "description": f"Mirror of Discord #{channel.name}"},
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.error(f"Discord -> Stoat: channel create failed: HTTP {resp.status} – {body[:200]}")
                    return
                new_id = (await resp.json()).get("_id")
            if not new_id:
                logger.error("Discord -> Stoat: no channel id in create response")
                return
        except Exception as exc:
            logger.error(f"Discord -> Stoat: channel create error: {exc}")
            return

        # Wire the new pair into the live bridge.
        try:
            stoat_channels[new_id] = await stoat_bot.fetch_channel(new_id)
        except Exception as exc:
            logger.error(f"Discord -> Stoat: could not fetch new Stoat channel {new_id}: {exc}")
            return
        DISCORD_CHANNEL_IDS.append(channel.id)
        STOAT_CHANNEL_IDS.append(new_id)
        CHANNEL_DIRECTIONS.append("bi")
        DISCORD_TO_STOAT[channel.id] = new_id
        STOAT_TO_DISCORD[new_id] = channel.id
        try:
            discord_webhooks[channel.id] = await channel.create_webhook(name="Stoat Bridge")
        except Exception as exc:
            logger.error(f"Discord -> Stoat: webhook for new channel #{channel.name} failed: {exc}")

        # Persist the pair so it survives a restart.
        try:
            set_key(str(ENV_FILE), "DISCORD_CHANNEL_IDS", ", ".join(str(i) for i in DISCORD_CHANNEL_IDS))
            set_key(str(ENV_FILE), "STOAT_CHANNEL_IDS",   ", ".join(STOAT_CHANNEL_IDS))
            set_key(str(ENV_FILE), "CHANNEL_DIRECTIONS",  ", ".join(CHANNEL_DIRECTIONS))
        except Exception as exc:
            logger.error(f"Discord -> Stoat: could not persist the new pair to {ENV_FILE}: {exc}")

        logger.info(f"Discord -> Stoat: mirrored #{channel.name} as Stoat channel {new_id} and bridged it")


# ──────────────────────────────────────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────────────────────────────────────


STOAT_RECONNECT_MIN_DELAY = 5    # seconds, first retry after an unplanned drop
STOAT_RECONNECT_MAX_DELAY = 300  # seconds, cap for exponential backoff


async def _run_stoat_supervisor(discord_bot: "DiscordBot") -> None:
    """
    Stoat connection loop
    """
    delay = STOAT_RECONNECT_MIN_DELAY

    while True:
        stoat_bot = StoatBot(
            token=STOAT_BOT_TOKEN,
            http_base=REVOLT_API_URL,
            websocket_base=REVOLT_WS_URL,
        )
        stoat_bot._discord_bot = discord_bot
        discord_bot._stoat_bot = stoat_bot

        planned = False
        try:
            await stoat_bot.start()
            planned = stoat_bot._planned_restart
            if planned:
                logger.info("Stoat: session closed for a scheduled restart – reconnecting…")
            else:
                logger.warning("Stoat: start() returned unexpectedly – reconnecting…")
        except stoat.shard.Reconnect:
            logger.warning("Stoat: gateway requested a reconnect – restarting Stoat session…")
        except asyncio.CancelledError:
            logger.info("Stoat: supervisor cancelled – shutting down")
            raise
        except Exception as exc:
            logger.error(f"Stoat: connection lost ({exc!r}) – restarting Stoat session…")

        # clean up potential old sessions
        try:
            if stoat_bot._http_session and not stoat_bot._http_session.closed:
                await stoat_bot._http_session.close()
        except Exception:
            pass

        delay = STOAT_RECONNECT_MIN_DELAY if planned else min(delay * 2, STOAT_RECONNECT_MAX_DELAY)
        logger.info(f"Stoat: reconnecting in {delay}s…")
        await asyncio.sleep(delay)


async def main():
    if not all([DISCORD_BOT_TOKEN, STOAT_BOT_TOKEN, DISCORD_CHANNEL_IDS, STOAT_CHANNEL_IDS]):
        raise RuntimeError("Missing configuration – check your .env file.")

    _load_notified_users()

    logger.info(f"Bridge starting with {PAIR_COUNT} channel pair(s)...")
    _dir_arrow = {"bi": "<->", "discord": "-->", "stoat": "<--"}
    for i, (d, s, dir_) in enumerate(zip(DISCORD_CHANNEL_IDS, STOAT_CHANNEL_IDS, CHANNEL_DIRECTIONS), 1):
        logger.info(f"  Pair {i}: Discord {d} {_dir_arrow.get(dir_, '<->')} Stoat {s}  [{dir_}]")

    discord_bot = DiscordBot()

    await asyncio.gather(
        _run_stoat_supervisor(discord_bot),
        discord_bot.start(DISCORD_BOT_TOKEN),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bridge stopped")