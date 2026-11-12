# radiradi

yt-dlp で取得したストリームを Discord のボイスチャンネルで流すだけのジュークボックス BOT。

## セットアップ

1. `ffmpeg` をインストール (`sudo apt install ffmpeg`)
2. Discord Developer Portal で BOT を作成し、`bot` と `applications.commands` スコープ、
   権限 `Connect` / `Speak` で招待
3. `cp .env.example .env` して `DISCORD_TOKEN` を設定
4. `uv run radiradi`

## コマンド

- `/play <URL or 検索語>` 再生 / キュー追加
- `/skip` スキップ
- `/queue` キュー表示
- `/stop` 停止して退出
