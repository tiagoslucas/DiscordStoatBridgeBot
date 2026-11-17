#!/usr/bin/env python3
"""
One-off backfill: copy a Discord channel's full history to its Stoat mirror.

Usage:
    python backfill.py <discord_channel_id>

Reads the same .env as bridge.py and uses the same formatting (nicknames via
masquerade, mention/emoji resolution, attachment URLs appended as text).

Run this BEFORE starting the bridge for a channel pair – messages the bridge
already forwarded live will be duplicated.
"""

import asyncio
import logging
import sys

import discord
import stoat
from dotenv import load_dotenv

load_dotenv()
import bridge  # noqa: E402 – needs the .env loaded before its module-level config runs

logger = logging.getLogger("backfill")


async def run_backfill(stoat_client: stoat.Client, discord_client: discord.Client, discord_channel_id: int) -> None:
    stoat_channel_id = bridge.DISCORD_TO_STOAT.get(discord_channel_id)
    if stoat_channel_id is None:
        raise SystemExit(
            f"Discord channel {discord_channel_id} is not in DISCORD_CHANNEL_IDS/STOAT_CHANNEL_IDS"
        )

    channel = (
        discord_client.get_channel(discord_channel_id)
        or await discord_client.fetch_channel(discord_channel_id)
    )
    ch = await stoat_client.fetch_channel(stoat_channel_id)

    # Skip messages created by the bridge itself (bot user or its webhooks).
    own_webhook_ids = {
        wh.id for wh in await channel.webhooks() if wh.user == discord_client.user
    }

    count = 0
    async for msg in channel.history(limit=None, oldest_first=True):
        if msg.author == discord_client.user or msg.webhook_id in own_webhook_ids:
            continue
        content = await bridge.clean_discord_content(msg.content or "", msg)
        for att in msg.attachments:
            content += f" {att.url}"
        if not content.strip():
            continue
        avatar_url = (
            str(msg.author.avatar.url)
            if msg.author.avatar
            else str(msg.author.default_avatar.url)
        )
        # shortcut: replies are not preserved as replies, they land as plain text
        await ch.send(
            masquerade=stoat.Masquerade(
                name=msg.author.display_name[:32],
                avatar=avatar_url,
            ),
            content=content[:2000],
        )
        count += 1
        if count % 100 == 0:
            logger.info(f"{count} messages backfilled…")
    logger.info(f"Done: {count} messages copied to Stoat channel {stoat_channel_id}")


class _BackfillStoat(stoat.Client):
    def __init__(self, discord_client: discord.Client, discord_channel_id: int, **kwargs):
        super().__init__(**kwargs)
        self._dc = discord_client
        self._discord_channel_id = discord_channel_id

    async def on_ready(self, event, /):
        try:
            await self._dc.wait_until_ready()
            await run_backfill(self, self._dc, self._discord_channel_id)
        finally:
            await self.close()
            await self._dc.close()


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: python {sys.argv[0]} <discord_channel_id>")
    discord_channel_id = int(sys.argv[1])

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    discord_client = discord.Client(intents=intents)

    stoat_client = _BackfillStoat(
        discord_client,
        discord_channel_id,
        token=bridge.STOAT_BOT_TOKEN,
        http_base=bridge.REVOLT_API_URL,
        websocket_base=bridge.REVOLT_WS_URL,
    )

    await asyncio.gather(
        stoat_client.start(),
        discord_client.start(bridge.DISCORD_BOT_TOKEN),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Backfill stopped")
