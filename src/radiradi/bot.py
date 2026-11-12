"""yt-dlp でストリームを取得して VC に流すだけのジュークボックス BOT。"""

from __future__ import annotations

import asyncio
import ctypes.util
import logging
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field

import discord
import yt_dlp
from discord import app_commands
from dotenv import load_dotenv

log = logging.getLogger("radiradi")

YDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",  # IPv4 を使う (IPv6 でブロックされがち)
    # YouTube のチャレンジ解決 (EJS) 用。yt-dlp[default] が yt-dlp-ejs を同梱する。
    # deno / node / bun / quickjs のどれかが PATH にあれば使う。
    "js_runtimes": {"deno": {}, "node": {}, "bun": {}, "quickjs": {}},
}
FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}
# ストリーム URL は期限切れになるので、これより古ければ再取得する
STREAM_TTL = 30 * 60


@dataclass
class Track:
    title: str
    page_url: str
    stream_url: str
    headers: dict[str, str] = field(default_factory=dict)
    resolved_at: float = field(default_factory=time.monotonic)
    requester: str = ""
    thumbnail: str | None = None
    duration: int | None = None  # 秒。ライブ配信などは None
    uploader: str | None = None
    announced: bool = False  # 「再生中」メッセージを送信済みか


def _extract(query: str) -> Track:
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(query, download=False)
    if "entries" in info:  # 検索結果
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise yt_dlp.utils.DownloadError("見つかりませんでした")
        info = entries[0]
    return Track(
        title=info.get("title", "unknown"),
        page_url=info.get("webpage_url") or info["url"],
        stream_url=info["url"],
        headers=dict(info.get("http_headers") or {}),
        thumbnail=info.get("thumbnail"),
        duration=int(info["duration"]) if info.get("duration") else None,
        uploader=info.get("uploader") or info.get("channel"),
    )


async def extract(query: str) -> Track:
    return await asyncio.to_thread(_extract, query)


def make_source(track: Track) -> discord.FFmpegPCMAudio:
    before = FFMPEG_OPTS["before_options"]
    if track.headers:
        # yt-dlp が要求する HTTP ヘッダ (UA など) を ffmpeg にも渡す。無いと 403 になることがある
        header_str = "".join(f"{k}: {v}\r\n" for k, v in track.headers.items())
        before = f"-headers {shlex.quote(header_str)} {before}"
    return discord.FFmpegPCMAudio(
        track.stream_url,
        before_options=before,
        options=FFMPEG_OPTS["options"],
        stderr=subprocess.PIPE,  # 失敗時にエラー内容が playback error に載る
    )


COLOR_PLAY = discord.Color.from_rgb(88, 101, 242)
COLOR_OK = discord.Color.from_rgb(87, 242, 135)
COLOR_WARN = discord.Color.from_rgb(254, 231, 92)
COLOR_ERR = discord.Color.from_rgb(237, 66, 69)


def fmt_duration(sec: int | None) -> str:
    if sec is None:
        return "🔴 LIVE"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def clip(text: str, n: int = 80) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def track_embed(track: Track, *, heading: str, color: discord.Color) -> discord.Embed:
    e = discord.Embed(title=clip(track.title, 250), url=track.page_url, color=color)
    e.set_author(name=heading)
    if track.thumbnail:
        e.set_thumbnail(url=track.thumbnail)
    e.add_field(name="長さ", value=fmt_duration(track.duration), inline=True)
    if track.uploader:
        e.add_field(name="投稿者", value=clip(track.uploader, 60), inline=True)
    if track.requester:
        e.set_footer(text=f"リクエスト: {track.requester}")
    return e


def message_embed(text: str, color: discord.Color = COLOR_OK) -> discord.Embed:
    return discord.Embed(description=text, color=color)


class GuildPlayer:
    def __init__(self, guild: discord.Guild) -> None:
        self.guild = guild
        self.queue: asyncio.Queue[Track] = asyncio.Queue()
        self.current: Track | None = None
        self.task: asyncio.Task | None = None
        self.text_channel: discord.abc.Messageable | None = None  # お知らせの投稿先
        self._next = asyncio.Event()
        self._failed = False

    async def say(self, embed: discord.Embed) -> None:
        if self.text_channel is None:
            return
        try:
            await self.text_channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("failed to send message")

    def ensure_running(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._loop())
            self.task.add_done_callback(self._log_task_result)

    @staticmethod
    def _log_task_result(task: asyncio.Task) -> None:
        if not task.cancelled() and (exc := task.exception()):
            log.error("player loop crashed", exc_info=exc)

    async def _loop(self) -> None:
        while True:
            try:
                track = await asyncio.wait_for(self.queue.get(), timeout=300)
            except asyncio.TimeoutError:
                # 5 分放置されたら退出
                if self.guild.voice_client:
                    await self.guild.voice_client.disconnect()
                return

            vc = self.guild.voice_client
            if vc is None:
                return
            # DAVE の準備を待ってから ffmpeg を起動する (待っている間にストリームが切れないように)
            await self._wait_dave_ready(vc)
            try:
                if time.monotonic() - track.resolved_at > STREAM_TTL:
                    fresh = await extract(track.page_url)
                    track.stream_url, track.headers = fresh.stream_url, fresh.headers
                source = make_source(track)
            except Exception:
                log.exception("failed to prepare %s", track.title)
                await self.say(message_embed(f"❌ **{clip(track.title)}** を再生できませんでした。", COLOR_ERR))
                continue

            self.current = track
            self._next.clear()
            loop = asyncio.get_running_loop()
            try:
                vc.play(source, after=lambda e: loop.call_soon_threadsafe(self._on_done, e))
            except Exception:
                log.exception("vc.play failed for %s", track.title)
                self.current = None
                await self.say(message_embed(f"❌ **{clip(track.title)}** を再生できませんでした。", COLOR_ERR))
                continue
            if not track.announced:
                track.announced = True
                await self.say(track_embed(track, heading="▶ 再生中", color=COLOR_PLAY))
            conn = vc._connection  # type: ignore[attr-defined]
            log.info(
                "now playing: %s (connected=%s dave_version=%s dave_ready=%s can_encrypt=%s)",
                track.title,
                vc.is_connected(),
                conn.dave_protocol_version,
                getattr(conn.dave_session, "ready", None),
                conn.can_encrypt,
            )
            started = time.monotonic()
            await self._next.wait()
            elapsed = time.monotonic() - started
            log.info("finished after %.1fs: %s", elapsed, track.title)
            if self._failed and elapsed < 3:
                await self.say(message_embed(f"❌ **{clip(track.title)}** の再生中にエラーが発生しました。", COLOR_ERR))
            self._failed = False
            self.current = None

    @staticmethod
    async def _wait_dave_ready(vc: discord.VoiceClient, timeout: float = 15.0) -> None:
        """DAVE (E2EE) の鍵交換が終わるまで待つ。未完了で送ると音声は捨てられる。"""
        conn = vc._connection  # type: ignore[attr-defined]
        deadline = time.monotonic() + timeout
        # dave_protocol_version == 0 なら E2EE 不要なので待たない
        while conn.dave_protocol_version != 0 and not conn.can_encrypt:
            if time.monotonic() > deadline:
                sess = conn.dave_session
                log.warning(
                    "DAVE not ready after %.0fs; playing anyway "
                    "(version=%s session=%s status=%s epoch=%s ready=%s members=%s)",
                    timeout,
                    conn.dave_protocol_version,
                    sess is not None,
                    getattr(sess, "status", None),
                    getattr(sess, "epoch", None),
                    getattr(sess, "ready", None),
                    [m.display_name for m in vc.channel.members],
                )
                return
            await asyncio.sleep(0.1)

    def _on_done(self, error: Exception | None) -> None:
        if error:
            log.error("playback error: %s", error)
            self._failed = True
        self._next.set()

    def clear(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()

    def stop(self) -> None:
        self.clear()
        if self.task:
            self.task.cancel()
        self.current = None


class Bot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()  # voice_states を含む
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.players: dict[int, GuildPlayer] = {}

    def player(self, guild: discord.Guild) -> GuildPlayer:
        if guild.id not in self.players:
            self.players[guild.id] = GuildPlayer(guild)
        return self.players[guild.id]

    async def setup_hook(self) -> None:
        await self.tree.sync()


bot = Bot()


def find_voice_channel(guild: discord.Guild, user_id: int) -> discord.VoiceChannel | discord.StageChannel | None:
    # interaction.user の Member には voice 情報が入らないため、ギルドのキャッシュから探す
    for ch in (*guild.voice_channels, *guild.stage_channels):
        if user_id in ch.voice_states:
            return ch
    return None


@bot.tree.command(name="play", description="URL またはキーワードで曲を再生・キュー追加")
@app_commands.describe(query="YouTube などの URL、または検索ワード")
@app_commands.guild_only()
async def play(interaction: discord.Interaction, query: str) -> None:
    member = interaction.user
    assert isinstance(member, discord.Member) and interaction.guild
    channel = find_voice_channel(interaction.guild, member.id)
    if channel is None:
        await interaction.response.send_message(
            embed=message_embed("先にボイスチャンネルに入ってください。", COLOR_WARN), ephemeral=True
        )
        return

    await interaction.response.defer()
    try:
        track = await extract(query)
    except Exception as e:
        await interaction.followup.send(embed=message_embed(f"❌ 取得に失敗しました\n```{clip(str(e), 500)}```", COLOR_ERR))
        return
    track.requester = member.display_name

    guild = interaction.guild
    if guild.voice_client is None:
        await channel.connect()
    elif guild.voice_client.channel != channel:
        await guild.voice_client.move_to(channel)

    player = bot.player(guild)
    player.text_channel = interaction.channel
    idle = player.current is None and player.queue.empty()
    position = player.queue.qsize() + (0 if player.current is None else 1)
    await player.queue.put(track)
    player.ensure_running()
    if idle:
        track.announced = True  # この応答自体が「再生中」のお知らせ
        embed = track_embed(track, heading="▶ 再生を開始します", color=COLOR_PLAY)
    else:
        embed = track_embed(track, heading=f"➕ キューに追加 (再生まであと {position} 曲)", color=COLOR_OK)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="skip", description="現在の曲をスキップ")
@app_commands.guild_only()
async def skip(interaction: discord.Interaction) -> None:
    vc = interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message(embed=message_embed("⏭ スキップしました。"))
    else:
        await interaction.response.send_message(embed=message_embed("再生中の曲がありません。", COLOR_WARN), ephemeral=True)


@bot.tree.command(name="queue", description="再生キューを表示")
@app_commands.guild_only()
async def queue(interaction: discord.Interaction) -> None:
    assert interaction.guild
    player = bot.player(interaction.guild)
    upcoming: list[Track] = list(player.queue._queue)  # type: ignore[attr-defined]
    if not player.current and not upcoming:
        await interaction.response.send_message(embed=message_embed("キューは空です。", COLOR_WARN))
        return

    embed = discord.Embed(title="🎶 再生キュー", color=COLOR_PLAY)
    if player.current:
        c = player.current
        embed.add_field(
            name="再生中",
            value=f"[{clip(c.title)}]({c.page_url}) `{fmt_duration(c.duration)}`",
            inline=False,
        )
        if c.thumbnail:
            embed.set_thumbnail(url=c.thumbnail)
    if upcoming:
        lines = [
            f"`{i}.` [{clip(t.title, 60)}]({t.page_url}) `{fmt_duration(t.duration)}`"
            for i, t in enumerate(upcoming[:10], 1)
        ]
        if len(upcoming) > 10:
            lines.append(f"…ほか {len(upcoming) - 10} 曲")
        embed.add_field(name="次に再生", value="\n".join(lines), inline=False)
        total = sum(t.duration or 0 for t in upcoming)
        embed.set_footer(text=f"待機 {len(upcoming)} 曲 / 合計 {fmt_duration(total)}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="stop", description="再生を止めてキューを空にし、退出")
@app_commands.guild_only()
async def stop(interaction: discord.Interaction) -> None:
    assert interaction.guild
    vc = interaction.guild.voice_client
    bot.player(interaction.guild).stop()
    if vc:
        await vc.disconnect()
    await interaction.response.send_message(embed=message_embed("⏹ 停止してボイスチャンネルから退出しました。"))


def main() -> None:
    load_dotenv()
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("環境変数 DISCORD_TOKEN が設定されていません (.env に書けます)")
    logging.basicConfig(level=logging.INFO)
    if os.environ.get("DEBUG"):
        logging.getLogger("discord").setLevel(logging.DEBUG)
    if not discord.opus.is_loaded():
        discord.opus.load_opus(ctypes.util.find_library("opus") or "libopus.so.0")
    log.info("opus loaded: %s", discord.opus.is_loaded())
    bot.run(token, log_handler=None)
