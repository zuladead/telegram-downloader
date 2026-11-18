# Telegram Downloader 📁

## Table of Contents

-   [About](#about)
-   [Telethon Version](#telethon_version)
-   [Getting Started](#getting_started)
-   [Usage](#usage)

## About<a name = "about"></a>

This project is a Telegram bot designed for personal use, leveraging the [local Telegram Bot API server](https://github.com/tdlib/telegram-bot-api) to download large video files sent or forwarded to the bot, directly to a specified local directory.

It's a simple solution that doesn't require a desktop or graphical client, and keeps the original file name as part of a descriptive output name. It's particularly useful for forwarding large video files 🎬 to download onto a NAS system.

## Telethon Version<a name = "telethon_version"></a>

I initially rewrote the bot using [Telethon](https://docs.telethon.dev), which offers more control over the download process. With Telethon, you could potentially track download progress or stop downloads midway, but these features aren’t fully implemented yet.

Despite this, I decided to stick with the **Bot API** version for its faster download speeds, even though it means giving up some of that control.

If you’re still interested in exploring the Telethon version, it’s available on the [telethon-migrate branch](https://github.com/avibn/telegram-downloader/tree/telethon-migrate).

## Getting Started<a name = "getting_started"></a>

### Docker Setup

Using Docker is the easiest way to set this up. Follow these steps:

1. Ensure Docker and Docker Compose are installed on your system.

2. Clone this repository and navigate to the project directory.

3. Set up your environment variables. You need to provide values for the following variables:
   | Variable | Description |
   | --- | --- |
   | `TELEGRAM_API_ID` | Your Telegram API ID. You can get this from [my.telegram.org](https://my.telegram.org/auth?to=apps). |
   | `TELEGRAM_API_HASH` | Your Telegram API Hash. You can get this from [my.telegram.org](https://my.telegram.org/auth?to=apps). |
   | `TELEGRAM_LOCAL` | Set this to `true` to use the local Telegram Bot API server. |
   | `BOT_TOKEN` | The token for your Telegram bot. You can create a new bot and get the token from [BotFather](https://t.me/botfather). |
   | `USER_ID` | Your Telegram user ID. This is the user that is allowed to send files to be downloaded with the bot (usually yourself). If you don't know your ID, you can use the `/info` command on the bot. |
   | `CHAT_ID` | The ID of the chat that the bot is allowed to download from. This can be the same as your `USER_ID` if you want to send files directly via DM to the bot. If you don't know the chat ID, you can use the `/info` command on the bot. |
   | `DOWNLOAD_TO_DIR` | The output directory where you want to download the files to (e.g., your downloads folder). |
   | `BOT_API_DIR` | The directory where the local bot API stores its files. You can alternatively edit the docker-compose file to use a Docker volume instead. In my case, I wanted to store it in a specific directory. |

    You can also set the environment variables in a .env file - just make sure to uncomment the `env_file: .env` lines in `docker-compose.prod.yml`.

4. Run the Docker Compose file for production:

    ```bash
    docker compose -f docker-compose.prod.yml up -d
    ```

    This command will start both services (local bot API and the bot).

5. Your bot should now be running. You can check the status of your Docker containers with:

    ```bash
    docker ps
    ```

### Manual Setup

If you prefer a manual setup, follow these steps:

1. You can use `docker-compose.api.yml` to run just the local bot API server. Alternatively, you can build it yourself if you wish.

2. Install the `uv` package manager:

    ```bash
    pip install uv
    ```

3. Install the required dependencies using `uv`:

    ```bash
    uv install
    ```

4. Create a `.env` file in the project root and add your environment variables. You can use the `.example.env` file as a template.

5. Run the bot using `uv`:

    ```bash
    uv run python run.py
    ```

This will start the bot in your local environment, using `uv` to manage your environment and dependencies.

## Usage<a name = "usage"></a>

<p align="center">
  <img src="/public/screenshot.png" alt="Telegram bot screenshot" width="600">
</p>

To use the bot, simply direct message it or add it to a group, depending on what you set as the `CHAT_ID`.

You can use the `/help` command to learn more about how to use the bot.

To download a video file, simply send/forward it to the bot. The bot will then ask you to confirm the download with a 'Yes' or 'No' button. Once you confirm, the bot will download the video file and notify you once the download is complete.

## Persistent queue, notifications, and retry

Media messages are now accepted into a persistent FIFO queue. Two workers run
by default; forwarding 50 or more files does not create 50 simultaneous Python
download jobs. `QUEUE_CAPACITY` limits all accepted unfinished jobs (default
1000). A rejected file receives a batched notice and must be sent again.

| Setting | Default | Meaning |
| --- | --- | --- |
| `DOWNLOAD_CONCURRENCY` | `2` | Workers, including download and final file copy (1–10) |
| `QUEUE_CAPACITY` | `1000` | Maximum accepted queued + running jobs |
| `DOWNLOAD_TASK_TIMEOUT` | `3600` | Seconds for all getFile attempts of one run |
| `NOTIFY_BATCH_SECONDS` | `5` | Collect events before sending a summary |
| `NOTIFY_MIN_INTERVAL` | `3.1` | Minimum interval between summary messages |
| `TASK_DB_PATH` | `./downloads/.telegram-downloader/tasks.sqlite3` | Persistent task history |

The production Compose file stores SQLite under the existing download volume;
keep that directory across upgrades. No additional database container or Python
dependency is needed. Completed history is retained, but this is not persistent
unique-ID/content deduplication. Duplicate active files are rejected; an existing
destination with the expected size is treated as an already published file.

- `/status`: counts and the five most recent failed task IDs/reasons.
- `/retry_failed`: requeue saved failures up to the available queue capacity.
- `/retry TASK_ID`: requeue one saved failed job. Repeating the command does not
  duplicate an active task.
- Restart: queued/interrupted jobs recover automatically; failed jobs wait for
  an explicit retry. Reducing queue capacity never discards an existing backlog.

Each job has a stable `task=...` ID in queue, attempt, download, moving, and final
success/failure logs. Filenames retain the format described below. File copies
use a temporary file and publish only after size verification; the destination
filesystem must support hard links (normal local Linux NAS filesystems do).
Bot tokens in logged exceptions/paths are redacted.

Download notifications use plain text, are combined into bounded messages, and
respect Telegram `RetryAfter` cooldowns. Notification transport errors do not
change task success or prevent downloading. The notification buffer keeps the
latest 500 events per chat; during a prolonged outage some old notices can be
omitted. SQLite and `/status` remain the authoritative task results. Pending
notifications themselves are not persisted across restart.

Local Bot API performs the underlying transfer. An HTTP timeout/cancellation
ends Python's wait and does not guarantee cancellation of that server-side
transfer. The worker limit bounds Python jobs, not orphaned transfers inside
Local Bot API. Five network attempts are followed by a saved failure, with a
separate total waiting deadline. Long-running final file copies retain their
worker slot and finish safely during graceful shutdown.

Old-version failures cannot be recovered from logs alone: the old application
did not persist the Telegram `file_id` needed for a retry (`file_unique_id` is
not a downloadable ID). Forward those files again once after upgrading; later
failures can be retried without forwarding again.

After deploying, verify with a batch of 60 small files, `/status`, and a failed
job followed by `/retry TASK_ID`. Confirm at most two `state=running` jobs,
batched notices, stable IDs across retries, and `state=succeeded` after final
publication. Keep both existing volume mappings.

## Filename format

Documents, videos, and audio files are saved as:

```text
original-stem_source_YYYY-MM-DD_caption_file_unique_id.extension
```

- The original extension and the complete Telegram `file_unique_id` are preserved.
- Source uses the forwarded channel/chat/user name when available, including
  hidden-sender display names. Missing sources use `来源未知`.
- The date uses the original forward date, or the received message date for
  direct uploads, converted to Beijing time (UTC+8).
- Caption uses the media message's own caption. Missing captions use `无说明`.
  Separate text messages and captions on other album items are not combined.
- Invalid filename characters, control characters, and line breaks are cleaned.
  Names are limited to 240 UTF-8 bytes: caption, original stem, then source are
  shortened as needed. The full unique ID and extension are never shortened.
- Files without an original name keep the existing generated-name fallback.

Different Telegram files with identical original names can now be saved
separately. Existing files are not renamed. The task database does not add
persistent unique-ID deduplication or video-content matching. Forwarding the
same file with different source/caption/date metadata can still create another
filename until persistent deduplication is added.

### Local verification

Install the project dependencies, then run tests with dummy configuration:

```bash
BOT_TOKEN=123:test LOCAL_BOT_API_URL=http://localhost:8081 \
BOT_API_DIR=/tmp/bot-api/ DOWNLOAD_TO_DIR=/tmp/downloads/ \
USER_ID=1 CHAT_ID=1 python -m unittest discover -s tests -v
```

### Publish and deploy this stage

The Docker workflow runs manually. After updating `main`, open GitHub Actions,
select **Build and Push Docker Image**, and run it against `main`. Wait for it
to publish `ghcr.io/zuladead/telegram-downloader:main`, then on the NAS run:

```bash
docker compose -f docker-compose.prod.yml pull bot
docker compose -f docker-compose.prod.yml up -d --no-deps bot
docker compose -f docker-compose.prod.yml logs --tail=100 bot
```

Verify two different files sharing the same original name, a channel forward
with a caption, and a direct upload without a caption. Keep the existing
download and Bot API volume mappings.
