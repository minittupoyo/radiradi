# radiradi

yt-dlp で取得したストリームを Discord のボイスチャンネルで流すだけのジュークボックス BOT。

## セットアップ

1. `ffmpeg` をインストール (`sudo apt install ffmpeg`)
2. Discord Developer Portal で BOT を作成し、`bot` と `applications.commands` スコープ、
   権限 `Connect` / `Speak` で招待
3. `cp .env.example .env` して `DISCORD_TOKEN` を設定
4. `uv run radiradi`

## Docker で動かす

```
cp .env.example .env   # DISCORD_TOKEN を設定
docker compose up -d --build
docker compose logs -f
```

ffmpeg は、この BOT に必要な機能だけを GitHub Actions
([`.github/workflows/ffmpeg.yml`](.github/workflows/ffmpeg.yml)) でビルドし、
GitHub Release に載せたものを取得して使います (約 3MB、イメージ全体で約 260MB)。
取得時に `SHA256SUMS` で検証し、一致しなければビルドは失敗します。
yt-dlp の EJS 用 JS ランタイムには、軽量な quickjs を使います。
ホスト側に ffmpeg や Python は不要です。

### ffmpeg を更新する

1. `docker/ffmpeg/Dockerfile` の `FFMPEG_VERSION` / `FFMPEG_SHA256` を更新する
   (同じバージョンでビルド設定だけ変えるときは `FFMPEG_REVISION` を上げる)
2. `main` に push すると、ワークフローが `ffmpeg-<version>-r<revision>` のリリースを作る
   (既にあれば何もしない。上書きしたいときは Actions から手動実行して `force` を有効にする)
3. `Dockerfile` の `FFMPEG_RELEASE` を、そのタグに合わせる

ローカルで同じバイナリをビルドするには:

```
docker buildx build --target binary --output type=local,dest=out docker/ffmpeg
```

## コマンド

- `/play <URL or 検索語>` 再生 / キュー追加
- `/skip` スキップ
- `/queue` キュー表示
- `/stop` 停止して退出
