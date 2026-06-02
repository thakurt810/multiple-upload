#!/usr/bin/env python3
"""
Social Media Auto-Upload Pipeline
Uploads videos from Google Drive to YouTube Shorts and/or Instagram Reels.

Multi-account support: set LOOP to process N accounts in one run.
Each account uses numbered secrets: GDRIVE_VIDEOS_FOLDER_ID_1, YT_CLIENT_ID_1, etc.
Shared secrets (no suffix): GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN.

Status tracking: uploaded video/meta Drive IDs are recorded in status.json in the repo,
keyed by ACCOUNT_KEY_N (e.g. "vegetable", "dad_joke") with sub-keys _video and _meta.
Videos already in status.json are skipped — no trashing ever happens.
"""

import os
import json
import random
import time
import datetime
import sys
import traceback
import base64
from pathlib import Path
import urllib.request
import urllib.parse
import urllib.error


# ─────────────────────────────────────────────
# HARDCODED LOOP — change this directly in code
# ─────────────────────────────────────────────
LOOP = 3

# ─────────────────────────────────────────────
# SHARED GDRIVE CREDENTIALS  (no suffix — same for all accounts)
# ─────────────────────────────────────────────
GDRIVE_CLIENT_ID      = os.environ.get("GDRIVE_CLIENT_ID", "")
GDRIVE_CLIENT_SECRET  = os.environ.get("GDRIVE_CLIENT_SECRET", "")
GDRIVE_REFRESH_TOKEN  = os.environ.get("GDRIVE_REFRESH_TOKEN", "")

# ─────────────────────────────────────────────
# GITHUB CONFIG  (for reading/writing status.json)
# ─────────────────────────────────────────────
GIT_PAT           = os.environ.get("GIT_PAT", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")   # e.g. "username/repo"
STATUS_FILE_PATH  = "status.json"                              # path inside the repo

MAX_RETRIES  = 3
RETRY_DELAY  = 5   # seconds between retries

# ─────────────────────────────────────────────
# PER-ACCOUNT CONFIG LOADER
# ─────────────────────────────────────────────
def load_account_config(n: int) -> dict:
    s = str(n)
    return {
        "account_key":            os.environ.get(f"ACCOUNT_KEY_{s}", f"account{s}"),
        "gdrive_videos_folder":   os.environ.get(f"GDRIVE_VIDEOS_FOLDER_ID_{s}", ""),
        "gdrive_metadata_folder": os.environ.get(f"GDRIVE_METADATA_FOLDER_ID_{s}", ""),
        "gdrive_logs_folder":     os.environ.get(f"GDRIVE_LOGS_FOLDER_ID_{s}", ""),
        "yt_client_id":      os.environ.get(f"YT_CLIENT_ID_{s}", ""),
        "yt_client_secret":  os.environ.get(f"YT_CLIENT_SECRET_{s}", ""),
        "yt_refresh_token":  os.environ.get(f"YT_REFRESH_TOKEN_{s}", ""),
        "ig_access_token":  os.environ.get(f"IG_ACCESS_TOKEN_{s}", ""),
        "ig_account_id":    os.environ.get(f"IG_ACCOUNT_ID_{s}", ""),
    }

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
def make_logger():
    lines = []

    def log(level, msg):
        ts   = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"[{ts}] [{level.upper()}] {msg}"
        print(line, flush=True)
        lines.append(line)

    def info(m):  log("INFO",    m)
    def warn(m):  log("WARNING", m)
    def error(m): log("ERROR",   m)

    return info, warn, error, lines

# ─────────────────────────────────────────────
# RETRY HELPER
# ─────────────────────────────────────────────
def with_retries(fn, label, log_info, log_error):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = fn()
            log_info(f"{label} succeeded (attempt {attempt}).")
            return True, result

        except urllib.error.HTTPError as e:
            log_error(
                f"{label} attempt {attempt}/{MAX_RETRIES} failed: HTTP {e.code}"
            )
            try:
                body = e.read().decode("utf-8")
                log_error(body)
            except Exception:
                pass
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

        except Exception as e:
            log_error(
                f"{label} attempt {attempt}/{MAX_RETRIES} failed: {e}"
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    log_error(f"{label} failed after {MAX_RETRIES} attempts.")
    return False, None

# ─────────────────────────────────────────────
# STATUS.JSON  —  read & write via GitHub API
# ─────────────────────────────────────────────
def _github_api_request(method, path, payload=None):
    """Raw GitHub REST API call. Returns parsed JSON body."""
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{path}"
    data = json.dumps(payload).encode() if payload else None
    req  = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {GIT_PAT}",
            "Accept":        "application/vnd.github+json",
            "Content-Type":  "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def load_status(log_info, log_warn) -> tuple[dict, str | None]:
    """
    Returns (status_dict, file_sha).
    file_sha is None if the file doesn't exist yet.
    status_dict shape:
      {
        "vegetable_video": ["driveId1", "driveId2", ...],
        "vegetable_meta":  ["driveId3", ...],
        "dad_joke_video":  [...],
        "dad_joke_meta":   [...],
        ...
      }
    """
    try:
        data    = _github_api_request("GET", STATUS_FILE_PATH)
        content = base64.b64decode(data["content"]).decode("utf-8")
        sha     = data["sha"]
        status  = json.loads(content)
        log_info(f"Loaded status.json (sha={sha[:7]}): {len(status)} key(s) tracked.")
        return status, sha
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log_warn("status.json not found in repo — will create fresh.")
            return {}, None
        raise


def save_status(status: dict, sha: str | None, commit_message: str, log_info, log_error) -> bool:
    """
    Commits the updated status dict back to the repo.
    sha must be the current file SHA (or None to create).
    """
    if not GIT_PAT or not GITHUB_REPOSITORY:
        log_error("GIT_PAT or GITHUB_REPOSITORY not set — cannot save status.json.")
        return False

    content_b64 = base64.b64encode(
        json.dumps(status, indent=2).encode("utf-8")
    ).decode("utf-8")

    payload = {
        "message": commit_message,
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha

    try:
        _github_api_request("PUT", STATUS_FILE_PATH, payload)
        log_info(f"status.json committed: {commit_message}")
        return True
    except Exception as e:
        log_error(f"Failed to commit status.json: {e}")
        return False

# ─────────────────────────────────────────────
# GOOGLE DRIVE
# ─────────────────────────────────────────────
def get_gdrive_access_token():
    payload = urllib.parse.urlencode({
        "client_id":     GDRIVE_CLIENT_ID,
        "client_secret": GDRIVE_CLIENT_SECRET,
        "refresh_token": GDRIVE_REFRESH_TOKEN,
        "grant_type":    "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload, method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]


def gdrive_list_files(folder_id, token):
    query = urllib.parse.quote(f"'{folder_id}' in parents and trashed=false")
    url   = (
        f"https://www.googleapis.com/drive/v3/files"
        f"?q={query}&fields=files(id,name)&pageSize=100"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read()).get("files", [])


def gdrive_download_file(file_id, dest, token):
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
        while chunk := resp.read(1024 * 1024):
            f.write(chunk)


def gdrive_upload_text(folder_id, filename, content, token):
    boundary = "==gdrive_boundary_42=="
    metadata = json.dumps({"name": filename, "parents": [folder_id]})
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: text/plain; charset=UTF-8\r\n\r\n"
        f"{content}\r\n"
        f"--{boundary}--"
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  f"multipart/related; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req):
        pass

# ─────────────────────────────────────────────
# METADATA
# ─────────────────────────────────────────────
DEFAULT_METADATA = {
    "youtube_title":       "✨ Watch This! #Shorts",
    "youtube_description": "Amazing short video! Don't forget to like and subscribe.\n\n#Shorts #Viral #Trending",
    "instagram_caption":   "✨ Check this out! 🔥\n\n#Reels #Viral #Trending",
}


def load_metadata(meta_files, video_stem, token, log_info, log_warn):
    target = f"{video_stem}.json"
    for mf in meta_files:
        if mf["name"].lower() == target.lower():
            tmp = "/tmp/meta_tmp.json"
            gdrive_download_file(mf["id"], tmp, token)
            with open(tmp) as f:
                data = json.load(f)
            log_info(f"Loaded metadata: {target}")
            return data, mf["id"]
    log_warn(f"No metadata file for '{video_stem}', using defaults.")
    return DEFAULT_METADATA.copy(), None

# ─────────────────────────────────────────────
# YOUTUBE UPLOAD
# ─────────────────────────────────────────────
def get_youtube_access_token(cfg):
    payload = urllib.parse.urlencode({
        "client_id":     cfg["yt_client_id"],
        "client_secret": cfg["yt_client_secret"],
        "refresh_token": cfg["yt_refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload, method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]


def upload_to_youtube(video_path, metadata, cfg, log_info):
    token       = get_youtube_access_token(cfg)
    title       = metadata.get("youtube_title",       DEFAULT_METADATA["youtube_title"])[:100]
    description = metadata.get("youtube_description", DEFAULT_METADATA["youtube_description"])[:5000]
    file_size   = os.path.getsize(video_path)

    init_meta = json.dumps({
        "snippet": {
            "title":       title,
            "description": description,
            "tags":        ["Shorts"],
            "categoryId":  "22",
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }).encode()

    init_req = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status",
        data=init_meta, method="POST",
        headers={
            "Authorization":           f"Bearer {token}",
            "Content-Type":            "application/json; charset=UTF-8",
            "X-Upload-Content-Type":   "video/*",
            "X-Upload-Content-Length": str(file_size),
        },
    )
    with urllib.request.urlopen(init_req) as r:
        upload_url = r.headers["Location"]

    with open(video_path, "rb") as f:
        video_data = f.read()
    upload_req = urllib.request.Request(
        upload_url, data=video_data, method="PUT",
        headers={"Content-Type": "video/*", "Content-Length": str(file_size)},
    )
    with urllib.request.urlopen(upload_req) as r:
        result = json.loads(r.read())

    video_id = result["id"]
    log_info(f"YouTube upload done. Video ID: {video_id}")
    return video_id

# ─────────────────────────────────────────────
# INSTAGRAM REEL UPLOAD
# ─────────────────────────────────────────────
def upload_to_instagram(video_path, metadata, cfg, log_info):
    token      = cfg["ig_access_token"]
    account_id = cfg["ig_account_id"]
    caption    = metadata.get("instagram_caption", DEFAULT_METADATA["instagram_caption"])
    file_size  = os.path.getsize(video_path)

    # Step 1 — create media container (resumable)
    params = urllib.parse.urlencode({
        "media_type":   "REELS",
        "caption":      caption,
        "access_token": token,
        "upload_type":  "resumable",
    }).encode()
    req = urllib.request.Request(
        f"https://graph.facebook.com/v23.0/{account_id}/media",
        data=params, method="POST"
    )
    with urllib.request.urlopen(req) as r:
        init_data = json.loads(r.read())

    container_id = init_data.get("id") or init_data.get("video_id")
    upload_url   = init_data.get("uri")

    if not upload_url:
        raise ValueError(f"No upload URI from Instagram: {init_data}")

    # Step 2 — upload bytes
    with open(video_path, "rb") as f:
        video_data = f.read()
    upload_req = urllib.request.Request(
        upload_url, data=video_data, method="POST",
        headers={
            "Authorization": f"OAuth {token}",
            "Content-Type":  "application/octet-stream",
            "offset":        "0",
            "file_size":     str(file_size),
        },
    )
    with urllib.request.urlopen(upload_req):
        pass

    # Step 3 — poll until FINISHED
    status_url = (
        f"https://graph.facebook.com/v23.0/{container_id}"
        f"?fields=status_code&access_token={token}"
    )
    for _ in range(20):
        time.sleep(10)
        with urllib.request.urlopen(status_url) as r:
            status_data = json.loads(r.read())
        status_code = status_data.get("status_code", "")
        log_info(f"Instagram container status: {status_code}")
        if status_code == "FINISHED":
            break
        if status_code == "ERROR":
            raise RuntimeError("Instagram container processing failed.")
    else:
        raise TimeoutError("Instagram container never reached FINISHED state.")

    # Step 4 — publish
    pub_params = urllib.parse.urlencode({
        "creation_id":  container_id,
        "access_token": token,
    }).encode()
    pub_req = urllib.request.Request(
        f"https://graph.facebook.com/v23.0/{account_id}/media_publish",
        data=pub_params, method="POST"
    )
    with urllib.request.urlopen(pub_req) as r:
        pub_data = json.loads(r.read())

    media_id = pub_data.get("id")
    log_info(f"Instagram publish done. Media ID: {media_id}")
    return media_id

# ─────────────────────────────────────────────
# SINGLE ACCOUNT PIPELINE
# ─────────────────────────────────────────────
def run_account(n: int, gdrive_token: str, status: dict, status_sha: str | None) -> tuple[bool, dict, str | None]:
    """
    Returns (any_success, updated_status, updated_sha).
    updated_status and updated_sha reflect the committed state after this account runs
    (so the next account gets a fresh sha to avoid conflicts).
    """
    log_info, log_warn, log_error, log_lines = make_logger()

    run_start = datetime.datetime.utcnow()
    log_info("=" * 60)
    log_info(f"Account slot {n} — started at {run_start.strftime('%Y-%m-%dT%H:%M:%SZ')}")

    cfg         = load_account_config(n)
    account_key = cfg["account_key"]
    video_key   = f"{account_key}_video"
    meta_key    = f"{account_key}_meta"

    log_info(f"Account {n}: key='{account_key}' → tracking '{video_key}' and '{meta_key}'")

    gdrive_ok    = all([cfg["gdrive_videos_folder"], cfg["gdrive_metadata_folder"], cfg["gdrive_logs_folder"]])
    youtube_ok   = all([cfg["yt_client_id"],    cfg["yt_client_secret"],  cfg["yt_refresh_token"]])
    instagram_ok = all([cfg["ig_access_token"], cfg["ig_account_id"]])

    if not gdrive_ok:
        log_error(f"Account {n}: Google Drive folder IDs missing. Skipping.")
        return False, status, status_sha

    if not youtube_ok:
        log_warn(f"Account {n}: YouTube credentials not set — YouTube upload will be skipped.")
    if not instagram_ok:
        log_warn(f"Account {n}: Instagram credentials not set — Instagram upload will be skipped.")

    if not youtube_ok and not instagram_ok:
        log_error(f"Account {n}: Neither YouTube nor Instagram credentials provided. Nothing to do.")
        return False, status, status_sha

    # ── Already-uploaded Drive IDs for this account ──────────────
    uploaded_video_ids = set(status.get(video_key, []))
    uploaded_meta_ids  = set(status.get(meta_key,  []))
    log_info(f"Account {n}: {len(uploaded_video_ids)} video(s) already uploaded, skipping those.")

    # 1. List videos
    ok, video_files = with_retries(
        lambda: gdrive_list_files(cfg["gdrive_videos_folder"], gdrive_token),
        f"[{n}] List video files", log_info, log_error,
    )
    if not ok or not video_files:
        log_error(f"Account {n}: Could not list videos or folder is empty.")
        return False, status, status_sha

    video_exts  = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    video_files = [f for f in video_files if Path(f["name"]).suffix.lower() in video_exts]

    # Filter out already-uploaded videos
    fresh_videos = [f for f in video_files if f["id"] not in uploaded_video_ids]
    log_info(f"Account {n}: {len(video_files)} video(s) in Drive, {len(fresh_videos)} not yet uploaded.")

    if not fresh_videos:
        log_error(f"Account {n}: All videos have already been uploaded. Nothing to do.")
        return False, status, status_sha

    # 2. Pick & download video
    chosen      = random.choice(fresh_videos)
    video_name  = chosen["name"]
    video_stem  = Path(video_name).stem
    local_video = f"/tmp/acct{n}_{video_name}"
    log_info(f"Account {n}: Selected '{video_name}'  (Drive ID: {chosen['id']})")

    ok, _ = with_retries(
        lambda: gdrive_download_file(chosen["id"], local_video, gdrive_token),
        f"[{n}] Download video", log_info, log_error,
    )
    if not ok:
        log_error(f"Account {n}: Failed to download video.")
        return False, status, status_sha

    # 3. Load metadata
    _, meta_files = with_retries(
        lambda: gdrive_list_files(cfg["gdrive_metadata_folder"], gdrive_token),
        f"[{n}] List metadata files", log_info, log_error,
    )
    meta_files = meta_files or []
    metadata, meta_file_id = load_metadata(meta_files, video_stem, gdrive_token, log_info, log_warn)

    # 4. Upload to YouTube
    youtube_success  = False
    youtube_video_id = None
    if youtube_ok:
        ok, yt_result = with_retries(
            lambda: upload_to_youtube(local_video, metadata, cfg, log_info),
            f"[{n}] YouTube upload", log_info, log_error,
        )
        youtube_success  = ok
        youtube_video_id = yt_result if ok else None
        if ok:
            log_info(f"Account {n}: YouTube Short live → https://youtube.com/shorts/{youtube_video_id}")
    else:
        log_info(f"Account {n}: Skipping YouTube (no credentials).")

    # 5. Upload to Instagram
    instagram_success  = False
    instagram_media_id = None
    if instagram_ok:
        ok, ig_result = with_retries(
            lambda: upload_to_instagram(local_video, metadata, cfg, log_info),
            f"[{n}] Instagram upload", log_info, log_error,
        )
        instagram_success  = ok
        instagram_media_id = ig_result if ok else None
        if ok:
            log_info(f"Account {n}: Instagram Reel live. Media ID: {instagram_media_id}")
    else:
        log_info(f"Account {n}: Skipping Instagram (no credentials).")

    # 6. Record Drive IDs in status.json if any upload succeeded
    any_success = youtube_success or instagram_success
    if any_success:
        log_info(f"Account {n}: Upload succeeded — recording Drive IDs in status.json.")

        # Append video Drive ID
        current_video_ids = status.get(video_key, [])
        if chosen["id"] not in current_video_ids:
            current_video_ids.append(chosen["id"])
        status[video_key] = current_video_ids

        # Append meta Drive ID (only if a meta file existed on Drive)
        if meta_file_id:
            current_meta_ids = status.get(meta_key, [])
            if meta_file_id not in current_meta_ids:
                current_meta_ids.append(meta_file_id)
            status[meta_key] = current_meta_ids
            log_info(f"Account {n}: Recorded meta Drive ID '{meta_file_id}' under '{meta_key}'.")

        log_info(f"Account {n}: Recorded video Drive ID '{chosen['id']}' under '{video_key}'.")

        # Commit status.json now (so next account loop gets fresh sha)
        commit_msg = (
            f"chore: mark {account_key} video '{video_name}' as uploaded "
            f"[{datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}]"
        )
        committed = save_status(status, status_sha, commit_msg, log_info, log_error)

        if committed:
            # Re-fetch sha so the next account's save doesn't conflict
            try:
                data        = _github_api_request("GET", STATUS_FILE_PATH)
                status_sha  = data["sha"]
                log_info(f"Account {n}: Refreshed status.json sha → {status_sha[:7]}")
            except Exception as e:
                log_warn(f"Account {n}: Could not refresh sha after commit: {e}")
    else:
        log_warn(f"Account {n}: All uploads failed — status.json not updated.")

    # 7. Clean up local temp file
    try:
        os.remove(local_video)
    except Exception:
        pass

    # 8. Upload status log to Drive
    run_end      = datetime.datetime.utcnow()
    upload_dt    = run_end.strftime("%Y%m%d_%H%M%S")
    log_filename = f"acct{n}_{video_stem}_{upload_dt}.txt"
    log_info(f"Account {n}: Finished at {run_end.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    log_content  = "\n".join(log_lines)

    local_log = f"/tmp/{log_filename}"
    with open(local_log, "w", encoding="utf-8") as lf:
        lf.write(log_content)
        lf.flush()
        os.fsync(lf.fileno())

    try:
        with_retries(
            lambda: gdrive_upload_text(cfg["gdrive_logs_folder"], log_filename, log_content, gdrive_token),
            f"[{n}] Upload log", log_info, log_error,
        )
    except Exception as e:
        log_warn(f"Account {n}: Could not upload log: {e}")

    return any_success, status, status_sha

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def run_all():
    root_log_info, root_log_warn, root_log_error, _ = make_logger()

    root_log_info(f"Pipeline starting — LOOP={LOOP} account(s)")

    if not all([GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN]):
        root_log_error("Shared Google Drive credentials (GDRIVE_CLIENT_ID / SECRET / REFRESH_TOKEN) are missing.")
        return False

    if not GIT_PAT or not GITHUB_REPOSITORY:
        root_log_error("GIT_PAT or GITHUB_REPOSITORY env vars are missing — cannot track status.json.")
        return False

    try:
        gdrive_token = get_gdrive_access_token()
        root_log_info("Shared GDrive token obtained.")
    except Exception as e:
        root_log_error(f"Could not obtain shared GDrive token: {e}")
        return False

    # Load status.json once; each account updates it and re-fetches the sha
    status, status_sha = load_status(root_log_info, root_log_warn)

    results = {}
    for n in range(1, LOOP + 1):
        root_log_info(f"─── Starting account slot {n} of {LOOP} ───")
        try:
            ok, status, status_sha = run_account(n, gdrive_token, status, status_sha)
            results[n] = ok
        except Exception as e:
            root_log_error(f"Account {n} raised unhandled exception: {e}")
            root_log_error(traceback.format_exc())
            results[n] = False

    root_log_info("=" * 60)
    root_log_info("All accounts processed. Summary:")
    for n, ok in results.items():
        status_str = "✓ SUCCESS" if ok else "✗ FAILED"
        root_log_info(f"  Account {n}: {status_str}")

    return any(results.values())


if __name__ == "__main__":
    try:
        success = run_all()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"[FATAL] Unhandled exception: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        sys.exit(1)
