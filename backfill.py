#!/usr/bin/env python3
"""
One-off backfill: copy a Discord channel's full history to its Stoat mirror.

Usage:
    python backfill.py <discord_channel_id> [--resume]

Reads the same .env as bridge.py and uses the same formatting (nicknames via
masquerade, mention resolution, custom emojis mirrored to the Stoat server,
attachment URLs appended as text). Reactions are copied too, added by the bot
one per emoji – same semantics as live reaction bridging.

--resume continues after the newest Discord message the last run processed
(state in .backfill_state/), so an interrupted run or a gap pass picks up
exactly where it stopped.

Run this BEFORE starting the bridge for a channel pair – messages the bridge
already forwarded live will be duplicated.
"""

import asyncio
import logging
import sys
from pathlib import Path

import aiohttp
import discord
import stoat
from dotenv import load_dotenv

load_dotenv()
import bridge  # noqa: E402 – needs the .env loaded before its module-level config runs

logger = logging.getLogger("backfill")

STATE_DIR = Path(".backfill_state")


async def run_backfill(stoat_client: stoat.Client, discord_client: discord.Client, discord_channel_id: int, after_id: int | None = None) -> None:
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
    last_seen = after_id
    if after_id:
        logger.info(f"resuming after Discord message {after_id}")
    session = aiohttp.ClientSession()
    try:
        async for msg in channel.history(limit=None, oldest_first=True, after=after_id):
            last_seen = msg.id
            if msg.author == discord_client.user or msg.webhook_id in own_webhook_ids:
                continue
            content = await bridge.clean_discord_content(msg.content or "", msg, session)
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
            sent = await ch.send(
                masquerade=stoat.Masquerade(
                    name=msg.author.display_name[:32],
                    avatar=avatar_url,
                ),
                content=content[:2000],
            )
            sent_id = bridge._extract_id(sent)
            if sent_id and msg.reactions:
                await bridge.copy_discord_reactions(
                    msg.reactions, session, stoat_channel_id, sent_id
                )
            count += 1
            if count % 100 == 0:
                logger.info(f"{count} messages backfilled…")
    finally:
        await session.close()
    logger.info(f"Done: {count} messages copied to Stoat channel {stoat_channel_id}")
    STATE_DIR.mkdir(exist_ok=True)
    (STATE_DIR / str(discord_channel_id)).write_text(str(last_seen or 0))


class _BackfillStoat(stoat.Client):
    def __init__(self, discord_client: discord.Client, discord_channel_id: int, after_id: int | None, **kwargs):
        super().__init__(**kwargs)
        self._dc = discord_client
        self._discord_channel_id = discord_channel_id
        self._after_id = after_id

    async def on_ready(self, event, /):
        try:
            await self._dc.wait_until_ready()
            await run_backfill(self, self._dc, self._discord_channel_id, self._after_id)
        finally:
            await self.close()
            await self._dc.close()


async def main() -> None:
    pos_args = [a for a in sys.argv[1:] if not a.startswith("--")]
    resume = "--resume" in sys.argv
    if len(pos_args) != 1:
        raise SystemExit(f"Usage: python {sys.argv[0]} <discord_channel_id> [--resume]")
    discord_channel_id = int(pos_args[0])
    after_id = None
    state_file = STATE_DIR / str(discord_channel_id)
    if resume and state_file.exists() and state_file.read_text().strip() not in ("", "0"):
        after_id = int(state_file.read_text().strip())

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    discord_client = discord.Client(intents=intents)

    stoat_client = _BackfillStoat(
        discord_client,
        discord_channel_id,
        after_id,
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
