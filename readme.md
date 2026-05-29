# Social Media Auto-Upload Pipeline

Automatically picks a random video from Google Drive and uploads it to **YouTube Shorts** and/or **Instagram Reels** on a schedule via GitHub Actions. Supports **multiple accounts** through a simple `LOOP` variable.


py get_refresh_token.py --scope drive



---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Repository Structure](#repository-structure)
3. [Quick Start](#quick-start)
4. [Secret Reference](#secret-reference)
   - [Shared Google Drive secrets](#shared-google-drive-secrets)
   - [Per-account secrets](#per-account-secrets)
5. [How to Get Each Credential](#how-to-get-each-credential)
   - [Google Drive OAuth (shared)](#1-google-drive-oauth-shared)
   - [YouTube Data API](#2-youtube-data-api)
   - [Instagram Graph API](#3-instagram-graph-api)
   - [Drive Folder IDs](#4-drive-folder-ids)
6. [Multi-Account Expansion](#multi-account-expansion)
7. [Metadata Files](#metadata-files)
8. [Schedule (IST)](#schedule-ist)
9. [Logs](#logs)
10. [Local Testing](#local-testing)
11. [FAQ / Troubleshooting](#faq--troubleshooting)

---

## How It Works

```
GitHub Actions (cron)
       │
       ▼
upload_pipeline.py
       │
       ├── Authenticate with Google Drive (shared OAuth)
       │
       └── For each account slot 1 → LOOP:
               │
               ├── List videos in GDRIVE_VIDEOS_FOLDER_ID_{n}
               ├── Pick one at random
               ├── Download it to /tmp/
               ├── Load matching <video_name>.json from metadata folder (or use defaults)
               │
               ├── Upload to YouTube Shorts  (if YT credentials present)
               ├── Upload to Instagram Reels (if IG credentials present)
               │
               ├── Trash video + metadata from Drive (if any upload succeeded)
               └── Upload run log to GDRIVE_LOGS_FOLDER_ID_{n}
```

Each account is completely independent. If one fails the others still run.

---

## Repository Structure

```
your-repo/
├── upload_pipeline.py     # Main pipeline
├── requirements.txt       # Stdlib only; no installs needed
└── .github/
    └── workflows/
        └── upload.yml     # GitHub Actions schedule + secrets wiring
```

---

## Quick Start

1. Fork or create a private GitHub repository.
2. Copy all three files into it (`upload_pipeline.py`, `requirements.txt`, `.github/workflows/upload.yml`).
3. Set up credentials (see [Secret Reference](#secret-reference) and [How to Get Each Credential](#how-to-get-each-credential)).
4. Add all secrets to **GitHub → Settings → Secrets and variables → Actions → New repository secret**.
5. Set the `LOOP` secret to `"1"` (or however many accounts you have).
6. Push. The pipeline runs automatically on schedule, or trigger it manually from **Actions → Social Media Auto-Upload → Run workflow**.

---

## Secret Reference

### Shared Google Drive secrets

These are the same for every account. Set them once.

| Secret name          | Description |
|----------------------|-------------|
| `GDRIVE_CLIENT_ID`     | OAuth 2.0 Client ID from Google Cloud Console |
| `GDRIVE_CLIENT_SECRET` | OAuth 2.0 Client Secret from Google Cloud Console |
| `GDRIVE_REFRESH_TOKEN` | Long-lived refresh token for your Google account |
| `LOOP`                 | Number of account slots to process (e.g. `1`, `2`, `3`) |

### Per-account secrets

Replace `{n}` with the account number (`1`, `2`, `3`, …).

| Secret name                    | Description |
|--------------------------------|-------------|
| `GDRIVE_VIDEOS_FOLDER_ID_{n}`   | Drive folder ID containing videos to post |
| `GDRIVE_METADATA_FOLDER_ID_{n}` | Drive folder ID containing `<video>.json` metadata files |
| `GDRIVE_LOGS_FOLDER_ID_{n}`     | Drive folder ID where run logs will be saved |
| `YT_CLIENT_ID_{n}`              | YouTube OAuth 2.0 Client ID |
| `YT_CLIENT_SECRET_{n}`          | YouTube OAuth 2.0 Client Secret |
| `YT_REFRESH_TOKEN_{n}`          | YouTube refresh token |
| `IG_ACCESS_TOKEN_{n}`           | Instagram long-lived access token |
| `IG_ACCOUNT_ID_{n}`             | Instagram Business/Creator Account numeric ID |

> **Tip:** Leave YouTube or Instagram secrets empty/unset for an account and that platform will simply be skipped. You do not need both platforms for every account.

---

## How to Get Each Credential

### 1. Google Drive OAuth (shared)

This single OAuth app authenticates Drive access for all accounts.

**Step 1 — Create a Google Cloud project**

1. Go to [console.cloud.google.com](https://console.cloud.google.com/).
2. Click **Select a project → New Project**, name it anything (e.g. `social-upload`).
3. Select the project.

**Step 2 — Enable the Drive API**

1. In the left menu: **APIs & Services → Library**.
2. Search for `Google Drive API` → click it → **Enable**.

**Step 3 — Create OAuth 2.0 credentials**

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. If prompted, configure the **OAuth consent screen** first:
   - User type: **External** (or Internal if you use Google Workspace)
   - Fill in App name, support email, developer email.
   - Scopes: add `https://www.googleapis.com/auth/drive`
   - Add your Google account as a **Test user** (important!).
3. Back in Create Credentials:
   - Application type: **Desktop app**
   - Name it anything.
4. Download the JSON — copy `client_id` → `GDRIVE_CLIENT_ID`, `client_secret` → `GDRIVE_CLIENT_SECRET`.

**Step 4 — Get the refresh token**

Run this one-time locally (Python must be installed):

```bash
pip install google-auth-oauthlib
```

```python
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json",          # the JSON you downloaded above
    scopes=["https://www.googleapis.com/auth/drive"],
)
creds = flow.run_local_server(port=0)
print("REFRESH TOKEN:", creds.refresh_token)
```

Copy the printed token → `GDRIVE_REFRESH_TOKEN`.

---

### 2. YouTube Data API

Each YouTube account needs its own OAuth app and refresh token.

**Step 1 — Enable YouTube Data API v3**

1. In Google Cloud Console (same project or a new one per account — your choice).
2. **APIs & Services → Library → YouTube Data API v3 → Enable**.

**Step 2 — Create OAuth credentials**

Same process as Drive above, but:
- Scope to add: `https://www.googleapis.com/auth/youtube.upload`
- Application type: **Desktop app**

Copy `client_id` → `YT_CLIENT_ID_{n}`, `client_secret` → `YT_CLIENT_SECRET_{n}`.

**Step 3 — Get the refresh token**

```python
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "yt_client_secret.json",
    scopes=["https://www.googleapis.com/auth/youtube.upload"],
)
creds = flow.run_local_server(port=0)
print("YT REFRESH TOKEN:", creds.refresh_token)
```

Copy → `YT_REFRESH_TOKEN_{n}`.

> **YouTube quota note:** The YouTube Data API has a daily quota of 10,000 units. One video upload costs ~1,600 units, so you can upload roughly 6 videos per day per project. If you need more, create separate Google Cloud projects for each YouTube account.

---

### 3. Instagram Graph API

Instagram uploads require a **Business** or **Creator** account connected to a **Facebook Page**, and a Facebook Developer App.

**Step 1 — Prerequisites**

- Your Instagram account must be set to **Professional** (Business or Creator).
  - Instagram app → Profile → Settings → Account → Switch to Professional Account.
- Connect it to a **Facebook Page** you manage.
  - Instagram → Settings → Account → Linked Accounts → Facebook.

**Step 2 — Create a Facebook Developer App**

1. Go to [developers.facebook.com](https://developers.facebook.com/).
2. **My Apps → Create App**.
3. Choose **Business** type.
4. Fill in the app name.

**Step 3 — Add Instagram Graph API product**

1. In your app dashboard: **Add a Product → Instagram Graph API → Set Up**.
2. Under **Instagram Graph API → Getting Started**, follow the prompts to connect your Instagram account.

**Step 4 — Get your Instagram Account ID**

1. In the app dashboard: **Tools → Graph API Explorer**.
2. Select your app in the top right.
3. Set permissions: `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`.
4. Click **Generate Access Token** and log in.
5. In the query box run:
   ```
   GET /me/accounts
   ```
6. Find your Facebook Page ID, then run:
   ```
   GET /{page-id}?fields=instagram_business_account
   ```
7. The `id` field inside `instagram_business_account` is your **Instagram Account ID** → `IG_ACCOUNT_ID_{n}`.

**Step 5 — Get a long-lived access token**

Short-lived tokens expire in 1 hour. Convert to long-lived (60 days):

```bash
curl -i -X GET "https://graph.facebook.com/v19.0/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id={app-id}
  &client_secret={app-secret}
  &fb_exchange_token={short-lived-token}"
```

Copy the returned token → `IG_ACCESS_TOKEN_{n}`.

> **Important:** Long-lived tokens last 60 days and can be refreshed. Set a calendar reminder to refresh them, or build a separate refresh workflow. Calling the same endpoint with your long-lived token as `fb_exchange_token` resets the 60-day timer.

---

### 4. Drive Folder IDs

You need three folders in Google Drive per account: one for videos, one for metadata, one for logs. You can reuse or share folders across accounts if you want the same content going everywhere.

**To get a folder ID:**
1. Open the folder in Google Drive in your browser.
2. The URL looks like: `https://drive.google.com/drive/folders/1A2B3C4D5E6F7G8H9I0J`
3. The long alphanumeric string at the end is the **Folder ID**.

Copy it to the matching secret:
- Videos folder → `GDRIVE_VIDEOS_FOLDER_ID_{n}`
- Metadata folder → `GDRIVE_METADATA_FOLDER_ID_{n}`
- Logs folder → `GDRIVE_LOGS_FOLDER_ID_{n}`

---

## Multi-Account Expansion

To add a new account:

1. **Prepare the credentials** for the new account (YouTube and/or Instagram).
2. **Add Drive folders** for it (or reuse existing ones).
3. **Add secrets** to GitHub with the next number suffix. For account 3, add:
   - `GDRIVE_VIDEOS_FOLDER_ID_3`
   - `GDRIVE_METADATA_FOLDER_ID_3`
   - `GDRIVE_LOGS_FOLDER_ID_3`
   - `YT_CLIENT_ID_3`, `YT_CLIENT_SECRET_3`, `YT_REFRESH_TOKEN_3`
   - `IG_ACCESS_TOKEN_3`, `IG_ACCOUNT_ID_3`
4. **Update the `LOOP` secret** from `2` to `3`.
5. **Add the account block** to `upload.yml` (copy the Account 2 block, change `_2` → `_3`). This only needs to be done once per new account number; the YAML must declare the env vars explicitly.
6. Commit and push. Done.

The pipeline will now run all three accounts on every scheduled trigger.

---

## Metadata Files

For each video in your Drive videos folder, you can create an optional JSON file in the metadata folder with the **exact same name** but `.json` extension.

Example: video is `beach_sunset.mp4` → metadata file is `beach_sunset.json`

```json
{
  "youtube_title": "🌅 Golden Hour Beach Sunset #Shorts",
  "youtube_description": "Watch the sun dip below the horizon in this stunning 60-second clip.\n\n#Shorts #Sunset #Beach #Nature",
  "instagram_caption": "🌅 Golden hour magic ✨\n\n#Reels #Sunset #BeachVibes #Nature #GoldenHour"
}
```

| Field                 | Used for   | Max length |
|-----------------------|------------|------------|
| `youtube_title`       | YouTube    | 100 chars  |
| `youtube_description` | YouTube    | 5000 chars |
| `instagram_caption`   | Instagram  | 2200 chars |

If no metadata file is found, these defaults are used:

```
YouTube title:       ✨ Watch This! #Shorts
YouTube description: Amazing short video! Don't forget to like and subscribe. #Shorts #Viral #Trending
Instagram caption:   ✨ Check this out! 🔥 #Reels #Viral #Trending
```

---

## Schedule (IST)

The pipeline runs four times daily. All times are **IST (UTC+5:30)**:

| IST Time  | UTC Time |
|-----------|----------|
| 8:00 PM   | 14:30    |
| 10:00 PM  | 16:30    |
| 12:00 AM  | 18:30    |
| 1:00 AM   | 19:30    |

To change the schedule, edit the `cron:` entries in `upload.yml`. Use [crontab.guru](https://crontab.guru/) to build expressions. Remember GitHub Actions cron is always UTC.

> **Note:** GitHub Actions scheduled workflows can be delayed by up to ~15 minutes during high-load periods.

---

## Logs

Every run produces a log file named `acct{n}_{video_stem}_{timestamp}.txt`.

Logs are saved in two places:
1. **Google Drive** — uploaded to `GDRIVE_LOGS_FOLDER_ID_{n}` for that account.
2. **GitHub Actions Artifacts** — retained for 30 days. Find them in **Actions → the run → Artifacts**.

Log format:
```
[2025-01-15T14:30:01Z] [INFO] Pipeline starting — LOOP=2 account(s)
[2025-01-15T14:30:02Z] [INFO] Account slot 1 — started at 2025-01-15T14:30:02Z
[2025-01-15T14:30:04Z] [INFO] Selected 'beach_sunset.mp4'  (Drive ID: 1A2B...)
[2025-01-15T14:31:55Z] [INFO] YouTube upload done. Video ID: dQw4w9WgXcQ
[2025-01-15T14:31:55Z] [INFO] YouTube Short live → https://youtube.com/shorts/dQw4w9WgXcQ
[2025-01-15T14:35:10Z] [INFO] Instagram Reel live. Media ID: 17854360229135492
[2025-01-15T14:35:11Z] [INFO] Trashed video 'beach_sunset.mp4'.
```

---

## Local Testing

Create a `.env` file (never commit this):

```bash
# Shared Drive
GDRIVE_CLIENT_ID=your_client_id
GDRIVE_CLIENT_SECRET=your_client_secret
GDRIVE_REFRESH_TOKEN=your_refresh_token

# Account 1
GDRIVE_VIDEOS_FOLDER_ID_1=folder_id_here
GDRIVE_METADATA_FOLDER_ID_1=folder_id_here
GDRIVE_LOGS_FOLDER_ID_1=folder_id_here
YT_CLIENT_ID_1=...
YT_CLIENT_SECRET_1=...
YT_REFRESH_TOKEN_1=...
IG_ACCESS_TOKEN_1=...
IG_ACCOUNT_ID_1=...

LOOP=1
```

Then run:

```bash
pip install python-dotenv   # one-time
python -c "from dotenv import load_dotenv; load_dotenv()" && python upload_pipeline.py

# Or more simply:
export $(cat .env | xargs) && python upload_pipeline.py
```

---

## FAQ / Troubleshooting

**The pipeline runs but no video is uploaded.**
Check the log artifact in GitHub Actions. The most common causes are: Drive folder is empty, credentials have expired (especially Instagram tokens), or API quota is exhausted.

**YouTube upload fails with 403.**
Your OAuth consent screen app is in "Testing" mode and your account is not listed as a test user. Add your Google account to **OAuth consent screen → Test users**, or publish the app.

**Instagram returns "No upload URI".**
Your account is likely not a Business/Creator account, or it is not connected to a Facebook Page. Complete the prerequisites in step 3 above.

**Instagram token expired.**
Long-lived tokens last 60 days. Refresh by calling the exchange endpoint with your current long-lived token as `fb_exchange_token` (same call as Step 5 above). Update `IG_ACCESS_TOKEN_{n}` in GitHub secrets.

**Videos are not being trashed after upload.**
This happens when both YouTube and Instagram uploads fail. Check the log for the specific error. The video stays in Drive so you can fix the issue and it will be picked up again.

**I want to post the same video to all accounts.**
Put a copy of the video in each account's videos folder. There is intentionally no cross-account video sharing to keep accounts independent.

**Can I run only one platform (skip the other)?**
Yes. Simply don't set the YouTube secrets (or set them to empty strings) for an account slot and YouTube will be skipped. Same for Instagram.

**How do I add a 4th account?**
Follow [Multi-Account Expansion](#multi-account-expansion). Add `_4` suffixed secrets, add an Account 4 block in `upload.yml`, and set `LOOP=4`.