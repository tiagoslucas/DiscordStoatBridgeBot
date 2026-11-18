#!/usr/bin/env python3
"""
Maintenance tool: delete every bot-authored message from a Stoat mirror
channel, leaving other users' messages untouched.

Usage:
    python clean_stoat.py <discord_channel_id> [<discord_channel_id> ...]

Reads the same .env as bridge.py and resolves each Discord channel to its
Stoat mirror, so only Discord channel IDs are needed. The bot's own user is
looked up from the Stoat token – nothing is hardcoded.

Useful before re-running a backfill for a channel: backfill + clean = a
fresh mirror with no duplicates.
"""

import asyncio
import logging
import sys

import aiohttp
from dotenv import load_dotenv

load_dotenv()
import bridge  # noqa: E402 – needs the .env loaded before its module-level config runs

logger = logging.getLogger("clean_stoat")


async def clean_channel(
    h: aiohttp.ClientSession, headers: dict, bot_id: str, stoat_channel_id: str
) -> None:
    # Collect the bot's own message ids, newest first, paginated.
    ids: list[str] = []
    before = None
    while True:
        url = (
            f"{bridge.REVOLT_API_URL}/channels/{stoat_channel_id}/messages"
            f"?limit=100&sort=Latest&include_users=false"
        )
        if before:
            url += "&before=" + before
        async with h.get(url, headers=headers) as r:
            if r.status != 200:
                logger.error(f"{stoat_channel_id}: fetch HTTP {r.status}")
                break
            batch = await r.json()
        if not isinstance(batch, list) or not batch:
            break
        ids += [m["_id"] for m in batch if m.get("author") == bot_id]
        before = batch[-1]["_id"]
        if len(batch) < 100:
            break

    logger.info(f"{stoat_channel_id}: {len(ids)} bot message(s) to delete")
    done = 0
    for mid in ids:
        while True:
            async with h.delete(
                f"{bridge.REVOLT_API_URL}/channels/{stoat_channel_id}/messages/{mid}",
                headers=headers,
            ) as r:
                if r.status in (200, 204):
                    done += 1
                    break
                if r.status == 429:
                    await asyncio.sleep((await r.json()).get("retry_after", 9000) / 1000 + 0.5)
                    continue
                logger.warning(f"{stoat_channel_id}: delete HTTP {r.status} for {mid}")
                break
    logger.info(f"{stoat_channel_id}: deleted {done}/{len(ids)}")


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(f"Usage: python {sys.argv[0]} <discord_channel_id> [...]")

    async with aiohttp.ClientSession() as h:
        headers = {"x-bot-token": bridge.STOAT_BOT_TOKEN}

        # Resolve the bot's own user from the token – the author to clean.
        async with h.get(f"{bridge.REVOLT_API_URL}/users/@me", headers=headers) as r:
            if r.status != 200:
                raise SystemExit(f"Stoat token check failed: HTTP {r.status}")
            me = await r.json()
            bot_id = me.get("_id") or me.get("id")
        if not bot_id:
            raise SystemExit("Could not determine the bot's user id from /users/@me")

        for arg in sys.argv[1:]:
            try:
                discord_id = int(arg)
            except ValueError:
                logger.warning(f"{arg!r} is not a Discord channel id - skipped")
                continue
            stoat_channel_id = bridge.DISCORD_TO_STOAT.get(discord_id)
            if stoat_channel_id is None:
                logger.warning(f"{arg} is not a bridged Discord channel - skipped")
                continue
            await clean_channel(h, headers, bot_id, stoat_channel_id)


if __name__ == "__main__":
    asyncio.run(main())
