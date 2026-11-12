"""Private evidence storage. Local disk by default; swap save()/path_for() for S3/Cloudinary signed URLs in production."""
import hashlib, os, uuid
from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature
from PIL import Image

VIDEO_EXT = {"mp4", "mov", "webm", "m4v"}
IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}


class UploadError(Exception):
    pass


def _ext(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _looks_like_video(head):
    return head[4:8] == b"ftyp" or head[:4] == b"\x1aE\xdf\xa3"  # mp4/mov or webm/mkv


def save_upload(fs, subdir, media):
    """Validate + store a werkzeug FileStorage. Returns (relative_path, sha256, size)."""
    if not fs or not fs.filename:
        raise UploadError("No file selected.")
    ext = _ext(fs.filename)
    allowed = VIDEO_EXT if media == "video" else IMAGE_EXT
    if ext not in allowed:
        raise UploadError(f"Unsupported file type .{ext}. Allowed: {', '.join(sorted(allowed))}.")
    root = current_app.config["UPLOAD_ROOT"]
    rel = os.path.join(subdir, f"{uuid.uuid4().hex}.{ext}")
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    h = hashlib.sha256()
    size = 0
    with open(full, "wb") as out:
        while chunk := fs.stream.read(1024 * 1024):
            h.update(chunk); size += len(chunk); out.write(chunk)
    if size == 0:
        os.remove(full); raise UploadError("The file is empty.")
    try:  # content sniffing, not just the extension
        if media == "video":
            with open(full, "rb") as f:
                if not _looks_like_video(f.read(16)):
                    raise UploadError("That file doesn't look like a real video.")
        else:
            with Image.open(full) as im:
                im.verify()
    except UploadError:
        os.remove(full); raise
    except Exception:
        os.remove(full); raise UploadError("That file is corrupted or not a valid image.")
    return rel, h.hexdigest(), size


def abs_path(rel):
    return os.path.join(current_app.config["UPLOAD_ROOT"], rel)


def _ser():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="evidence")


def sign_evidence(evidence_id, user_id):
    return _ser().dumps({"e": evidence_id, "u": user_id})


def verify_evidence_token(token, evidence_id, user_id, max_age=900):
    try:
        d = _ser().loads(token, max_age=max_age)
    except BadSignature:
        return False
    return d.get("e") == evidence_id and d.get("u") == user_id
