#!/usr/bin/env python3
import os
import sqlite3
import threading
import time
import hmac
import base64
import hashlib
import mimetypes
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import uvicorn
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
import yaml

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.yaml"
DB_PATH = HERE / "media.db"

VIDEO_EXTS = {".mp4", ".m4v", ".mkv", ".mov", ".avi", ".wmv", ".mpg", ".mpeg"}
SUB_EXTS = [".srt", ".vtt"]
ART_EXTS = [".png", ".jpg", ".jpeg", ".webp"]
DEFAULT_HIDDEN_DIRS = ["images"]

class Settings(BaseModel):
    # Required
    library_root: str
    secret_key: str
    admin_key: str
    # Optional
    host: str = "0.0.0.0"
    port: int = 8008
    folder_token_hours: int = 24
    parental_pin: str = "0000"  # simple default; change in config.yaml
    # NEW: classify / hide
    default_is_kids: bool = True
    kids_paths: List[str] = []           # (kept for backwards-compat)
    adult_paths: List[str] = []          # NEW: explicit adult-only paths
    hidden_dir_names: List[str] = DEFAULT_HIDDEN_DIRS

def load_settings() -> Settings:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Missing config file: {CONFIG_PATH}")
    data = yaml.safe_load(CONFIG_PATH.read_text("utf-8")) or {}
    return Settings(**data)

SETTINGS = load_settings()
LIB_ROOT = Path(SETTINGS.library_root).resolve()

# ------------------------------------------------------------
# Database
# ------------------------------------------------------------

DB_LOCK = threading.RLock()

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with DB_LOCK, db() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                parent_id INTEGER,
                is_kids INTEGER DEFAULT 1,
                poster TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                folder_id INTEGER NOT NULL
            )
        """)
        conn.commit()

def is_video(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXTS

def find_subtitle(p: Path) -> Optional[Path]:
    stem = p.with_suffix("")
    for ext in SUB_EXTS:
        cand = stem.with_suffix(ext)
        if cand.exists():
            return cand
    return None

def upsert_folder(conn: sqlite3.Connection, p: Path, parent_id: Optional[int]) -> int:
    p = p.resolve()
    name = p.name if p != LIB_ROOT else p.name or "/"
    cur = conn.cursor()
    cur.execute("SELECT id FROM folders WHERE path=?", (str(p),))
    row = cur.fetchone()
    if row:
        fid = int(row["id"])
        # keep name synced
        cur.execute("UPDATE folders SET name=?, parent_id=? WHERE id=?", (name, parent_id, fid))
        conn.commit()
        return fid
    cur.execute(
        "INSERT INTO folders(path, name, parent_id, is_kids) VALUES (?, ?, ?, ?)",
        (str(p), name, parent_id, 1 if SETTINGS.default_is_kids else 0)
    )
    fid = cur.lastrowid
    conn.commit()
    return fid

def upsert_media(conn: sqlite3.Connection, p: Path, folder_id: int) -> int:
    p = p.resolve()
    name = p.name
    cur = conn.cursor()
    cur.execute("SELECT id FROM media WHERE path=?", (str(p),))
    row = cur.fetchone()
    if row:
        mid = int(row["id"])
        cur.execute("UPDATE media SET name=?, folder_id=? WHERE id=?", (name, folder_id, mid))
        conn.commit()
        return mid
    cur.execute("INSERT INTO media(path, name, folder_id) VALUES (?, ?, ?)", (str(p), name, folder_id))
    mid = cur.lastrowid
    conn.commit()
    return mid

def _is_hidden_name(name: str) -> bool:
    return name.lower() in {n.lower() for n in SETTINGS.hidden_dir_names}

def _in_path_list(path: Path, patterns: List[str]) -> bool:
    """Return True if path is equal to or inside any of the listed relative patterns."""
    try:
        rel = path.resolve().relative_to(LIB_ROOT.resolve()).as_posix().lower()
    except Exception:
        return False
    for pat in patterns or []:
        p = pat.strip("/").lower()
        if not p:
            continue
        if rel == p or rel.startswith(p + "/"):
            return True
    return False

def _is_kids_path(path: Path) -> bool:
    """
    Determine if a folder is kids-safe.

    Precedence:
      1) If adult_paths provided: kids-safe iff NOT in adult_paths
      2) Else if kids_paths provided: kids-safe iff IN kids_paths
      3) Else: fallback to default_is_kids
    """
    if len(SETTINGS.adult_paths) > 0:
        return not _in_path_list(path, SETTINGS.adult_paths)
    if len(SETTINGS.kids_paths) > 0:
        return _in_path_list(path, SETTINGS.kids_paths)
    return SETTINGS.default_is_kids

def find_poster(d: Path) -> Optional[str]:
    """Optional folder poster lookup (not required by Roku app)."""
    for name in ("poster.jpg", "poster.png", "folder.jpg", "folder.png"):
        cand = d / name
        if cand.exists():
            return str(cand)
    return None

def scan_library(full_rescan: bool = False) -> int:
    if not LIB_ROOT.exists():
        raise SystemExit(f"Library root does not exist: {LIB_ROOT}")

    count = 0
    with DB_LOCK, db() as conn:
        c = conn.cursor()
        if full_rescan:
            c.execute("DELETE FROM media")
            c.execute("DELETE FROM folders")
            conn.commit()

        root_id = upsert_folder(conn, LIB_ROOT, None)
        c.execute("UPDATE folders SET is_kids=? WHERE id=?", (1 if _is_kids_path(LIB_ROOT) else 0, root_id))

        path_to_id: Dict[Path, int] = {LIB_ROOT: root_id}

        for dirpath, dirnames, filenames in os.walk(LIB_ROOT):
            # hide any directory names listed (e.g., "images")
            dirnames[:] = [dn for dn in dirnames if not _is_hidden_name(dn)]

            d = Path(dirpath).resolve()

            # ensure parent folder row exists
            if d not in path_to_id:
                parent = d.parent
                parent_id = path_to_id.get(parent)
                if parent_id is None:
                    parent_id = upsert_folder(conn, parent, None)
                    path_to_id[parent] = parent_id
                fid = upsert_folder(conn, d, parent_id)
                path_to_id[d] = fid
            else:
                fid = path_to_id[d]

            # set classification based on config
            c.execute("UPDATE folders SET is_kids=? WHERE id=?", (1 if _is_kids_path(d) else 0, fid))

            # optional folder poster
            c.execute("UPDATE folders SET poster=? WHERE id=?", (find_poster(d), fid))

            # media rows
            for fn in filenames:
                p = d / fn
                if is_video(p):
                    upsert_media(conn, p, fid)
                    count += 1

        conn.commit()
    return count

# ------------------------------------------------------------
# Auth token (simple HMAC)
# ------------------------------------------------------------

def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")

def _unb64url(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)

def make_folder_token(folder_id: int, hours: int) -> str:
    exp = int(time.time()) + int(hours) * 3600
    payload = f"{folder_id}.{exp}".encode("utf-8")
    sig = hmac.new(SETTINGS.secret_key.encode("utf-8"), payload, hashlib.sha256).digest()
    return _b64url(payload) + "." + _b64url(sig)

def verify_folder_token(token: str, folder_id: int) -> bool:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _unb64url(payload_b64)
        sig = _unb64url(sig_b64)
        expect_sig = hmac.new(SETTINGS.secret_key.encode("utf-8"), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expect_sig):
            return False
        s = payload.decode("utf-8")
        fid_s, exp_s = s.split(".", 1)
        if int(fid_s) != int(folder_id):
            return False
        if int(exp_s) < int(time.time()):
            return False
        return True
    except Exception:
        return False

def folder_requires_auth(folder_id: int) -> bool:
    with DB_LOCK, db() as conn:
        c = conn.cursor()
        c.execute("SELECT is_kids FROM folders WHERE id=?", (folder_id,))
        row = c.fetchone()
        if row is None:
            return False
        is_kids = int(row["is_kids"])
        # Require auth for NON-kids folders
        return is_kids == 0

# ------------------------------------------------------------
# Tolerant token extraction & request auth
# ------------------------------------------------------------
    
def extract_bearer_token(request: Request) -> Optional[str]:
    """
    Accept token from Authorization header OR ?token= query parameter.
    This is critical for Roku Video node which may drop headers on range requests.
    """
    # Header
    auth = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth:
        parts = auth.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            t = parts[1].strip()
            if t:
                return t
    # Query string
    t = request.query_params.get("token")
    if t:
        return t.strip()
    return None

def check_request_authorized(request: Request, folder_id: int) -> bool:
    token = extract_bearer_token(request)
    if not token:
        return False
    return verify_folder_token(token, folder_id)

# ------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------

app = FastAPI(title="Local Media Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local LAN
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------
# Models for requests
# ------------------------------------------------------------

class AuthFolderReq(BaseModel):
    dir_id: int
    pin: str

class RescanReq(BaseModel):
    full: bool = False

# ------------------------------------------------------------
# Browse / Stream / Subtitle
# ------------------------------------------------------------

def folder_row(conn: sqlite3.Connection, folder_id: int) -> Optional[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute("SELECT id, path, name, parent_id, is_kids FROM folders WHERE id=?", (folder_id,))
    return cur.fetchone()

@app.get("/api/browse")
def api_browse(dir_id: Optional[int] = None, request: Request = None):
    with DB_LOCK, db() as conn:
        c = conn.cursor()

        # determine root id if none provided
        if dir_id is None:
            c.execute("SELECT id FROM folders WHERE path=?", (str(LIB_ROOT),))
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "Library not initialized")
            dir_id = int(row["id"])

        # auth gate for non-kids folders
        if folder_requires_auth(dir_id):
            if not check_request_authorized(request, dir_id):
                return {"authorized": False}

        row = folder_row(conn, dir_id)
        if not row:
            raise HTTPException(404, "Folder not found")

        # subdirs
        c.execute("SELECT id, name FROM folders WHERE parent_id=? ORDER BY name COLLATE NOCASE ASC", (dir_id,))
        subdirs = [{"id": int(r["id"]), "name": r["name"]} for r in c.fetchall()]

        # media
        c.execute("SELECT id, name FROM media WHERE folder_id=? ORDER BY name COLLATE NOCASE ASC", (dir_id,))
        media = [{"id": int(r["id"]), "name": r["name"]} for r in c.fetchall()]

        return {
            "authorized": True,
            "dir": {"id": int(row["id"]), "name": row["name"], "is_kids": bool(row["is_kids"])},
            "subdirs": subdirs,
            "media": media,
        }

@app.get("/api/stream")
def api_stream(media_id: int, request: Request = None):
    with DB_LOCK, db() as conn:
        c = conn.cursor()
        c.execute("SELECT m.path, m.folder_id FROM media m WHERE m.id=?", (media_id,))
        r = c.fetchone()
        if not r:
            raise HTTPException(404, "Media not found")
        folder_id = int(r["folder_id"])
        if folder_requires_auth(folder_id) and not check_request_authorized(request, folder_id):
            raise HTTPException(401, "Unauthorized")
        p = Path(r["path"])
        if not p.exists():
            raise HTTPException(404, "File missing on disk")
        mt, _ = mimetypes.guess_type(p.name)
        return FileResponse(str(p), media_type=mt or "video/mp4")

@app.get("/api/subtitle")
def api_subtitle(media_id: int, request: Request = None):
    with DB_LOCK, db() as conn:
        c = conn.cursor()
        c.execute("SELECT m.path, m.folder_id FROM media m WHERE m.id=?", (media_id,))
        r = c.fetchone()
        if not r:
            raise HTTPException(404, "Media not found")
        folder_id = int(r["folder_id"])
        if folder_requires_auth(folder_id) and not check_request_authorized(request, folder_id):
            raise HTTPException(401, "Unauthorized")
        p = Path(r["path"])
        sub = find_subtitle(p)
        if not sub:
            raise HTTPException(404, "Subtitle not found")
        mt, _ = mimetypes.guess_type(sub.name)
        return FileResponse(str(sub), media_type=mt or "text/plain")

# ------------------------------------------------------------
# Artwork: /images/<name>.(png|jpg|jpeg|webp)
# ------------------------------------------------------------

@app.get("/api/art")
def api_art(name: str):
    base = os.path.basename(name).strip()
    if not base:
        raise HTTPException(400, "Missing name")
    images_dir = LIB_ROOT / "images"
    candidates: List[Path] = []

    # if client passed extension, try exact
    cand_exact = images_dir / base
    candidates.append(cand_exact)

    # stem + known art extensions
    stem = Path(base).stem
    for ext in ART_EXTS:
        candidates.append(images_dir / f"{stem}{ext}")

    for cand in candidates:
        if cand.exists() and cand.is_file():
            mt, _ = mimetypes.guess_type(cand.name)
            return FileResponse(str(cand), media_type=mt or "image/png")
    raise HTTPException(404, "Artwork not found")

# ------------------------------------------------------------
# Auth: PIN -> bearer token for folder
# ------------------------------------------------------------

@app.post("/api/auth/folder")
def api_auth_folder(req: AuthFolderReq):
    # Simple single PIN for all restricted folders (matches Roku prompt → token)
    if req.pin != SETTINGS.parental_pin:
        raise HTTPException(401, "Invalid PIN")
    token = make_folder_token(req.dir_id, SETTINGS.folder_token_hours)
    return {"token": token}

# ------------------------------------------------------------
# Admin rescan
# ------------------------------------------------------------

@app.post("/api/admin/rescan")
def api_admin_rescan(req: RescanReq, x_admin_key: Optional[str] = Header(None)):
    if x_admin_key != SETTINGS.admin_key:
        raise HTTPException(401, "Unauthorized")
    count = scan_library(full_rescan=bool(req.full))
    return {"ok": True, "count": count, "full": bool(req.full)}

# ------------------------------------------------------------
# Startup
# ------------------------------------------------------------

def ensure_root_row():
    with DB_LOCK, db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM folders WHERE path=?", (str(LIB_ROOT),))
        if not c.fetchone():
            root_id = upsert_folder(conn, LIB_ROOT, None)
            c.execute("UPDATE folders SET is_kids=? WHERE id=?", (1 if _is_kids_path(LIB_ROOT) else 0, root_id))
            conn.commit()

def main():
    init_db()
    ensure_root_row()
    # quick incremental scan on boot (change to full_rescan=True if you prefer)
    scan_library(full_rescan=False)
    uvicorn.run(
        "server:app",
        host=SETTINGS.host,
        port=SETTINGS.port,
        reload=False,
        log_level="info",
    )

if __name__ == "__main__":
    main()

