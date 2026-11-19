"""Lógica compartida: Apple, Firebase, progreso y migración de un usuario."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Iterator

import jwt
import requests

from apple_migration_settings import (
    APPLY,
    BUNDLE_ID,
    FIREBASE_SERVICE_ACCOUNT_PATH,
    KEY_ID,
    MAX_RETRIES,
    ONLY_FIREBASE_UID,
    PAUSE_SECONDS,
    P8_PATH,
    RETRY_BACKOFF_SECONDS,
    SERVICES_CLIENT_ID,
    TEAM_ID,
)

APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_MIGRATION_URL = "https://appleid.apple.com/auth/usermigrationinfo"
APPLE_AUDIENCE = "https://appleid.apple.com"
RELAY_SUFFIX = "@privaterelay.appleid.com"


class AppleError(Exception):
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Apple HTTP {status_code}: {json.dumps(body, ensure_ascii=False)}")


class FirebaseWriteError(Exception):
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self.body = body
        super().__init__(
            f"Firebase HTTP {status_code}: {json.dumps(body, ensure_ascii=False)}"
        )


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def bundle_safe() -> str:
    return BUNDLE_ID.replace("/", "_").replace(" ", "_")


def resolve_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_file():
        return candidate
    from_script = script_dir() / path
    if from_script.is_file():
        return from_script
    raise SystemExit(f"No es un archivo: {path}")


def apple_client_id() -> str:
    return (SERVICES_CLIENT_ID or BUNDLE_ID).strip()


def require_constants() -> None:
    required = {
        "BUNDLE_ID": BUNDLE_ID,
        "TEAM_ID": TEAM_ID,
        "KEY_ID": KEY_ID,
        "P8_PATH": P8_PATH,
        "FIREBASE_SERVICE_ACCOUNT_PATH": FIREBASE_SERVICE_ACCOUNT_PATH,
    }
    missing = [name for name, value in required.items() if not value or value == "REEMPLAZAR"]
    if missing:
        raise SystemExit("Faltan constantes: " + ", ".join(missing))
    resolve_path(P8_PATH)
    resolve_path(FIREBASE_SERVICE_ACCOUNT_PATH)


def manifest_path(custom_basename: str = "") -> Path:
    if custom_basename:
        return script_dir() / custom_basename
    return script_dir() / f"apple_manifest_{bundle_safe()}.jsonl"


def progress_file(shard_id: int | None = None) -> Path:
    base = f"apple_migration_{bundle_safe()}"
    if shard_id is None:
        return script_dir() / f"{base}.json"
    return script_dir() / f"{base}.shard-{shard_id:02d}.json"


def progress_shard_glob() -> list[Path]:
    pattern = f"apple_migration_{bundle_safe()}.shard-*.json"
    return sorted(script_dir().glob(pattern))


def load_progress(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_progress(path: Path, progress: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(progress, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def shard_for_uid(firebase_uid: str, num_shards: int) -> int:
    digest = hashlib.sha256(firebase_uid.encode("utf-8")).hexdigest()
    return int(digest, 16) % num_shards


def apple_client_secret(team_id: str, key_id: str, p8_path: str) -> str:
    private_key = resolve_path(p8_path).read_text(encoding="utf-8")
    client_id = apple_client_id()
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": team_id,
            "iat": now,
            "exp": now + 1800,
            "aud": APPLE_AUDIENCE,
            "sub": client_id,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": key_id, "alg": "ES256"},
    )
    if isinstance(token, bytes):
        return token.decode("utf-8")
    return token


def apple_post(url: str, data: dict, access_token: str | None = None, label: str = "") -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    response = requests.post(url, data=data, headers=headers, timeout=60)
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:500]}
    if response.status_code >= 400:
        hint = ""
        if body.get("error") == "invalid_client":
            hint = (
                " Revisa TEAM_ID, KEY_ID, .p8 del equipo destino, y que apple_client_id() "
                f"(ahora '{apple_client_id()}') coincida con el App ID o Services ID en Apple Developer."
            )
        if label:
            hint = f" ({label}){hint}"
        raise AppleError(response.status_code, {**body, "hint": hint.strip()})
    return body


class AppleTeam:
    def __init__(self, team_id: str, key_id: str, p8_path: str):
        self.team_id = team_id
        self.key_id = key_id
        self.p8_path = p8_path
        self._access_token = ""
        self._expires_at = 0.0

    def access_token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        secret = apple_client_secret(self.team_id, self.key_id, self.p8_path)
        client_id = apple_client_id()
        body = apple_post(
            APPLE_TOKEN_URL,
            {
                "grant_type": "client_credentials",
                "scope": "user.migration",
                "client_id": client_id,
                "client_secret": secret,
            },
            label="auth/token (user.migration)",
        )
        self._access_token = body["access_token"]
        self._expires_at = time.time() + int(body.get("expires_in", 3600))
        return self._access_token

    def client_secret(self) -> str:
        return apple_client_secret(self.team_id, self.key_id, self.p8_path)


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def is_retryable_error(error: Exception) -> bool:
    if isinstance(error, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(error, AppleError):
        if error.status_code in (429, 500, 502, 503, 504):
            return True
        err = error.body.get("error") if isinstance(error.body, dict) else None
        if error.status_code == 400 and err in ("invalid_request",):
            return True
        if err == "invalid_client":
            return False
    if isinstance(error, FirebaseWriteError):
        if error.status_code in (429, 500, 502, 503, 504):
            return True
    return False


def call_with_retry(action: Callable[[], object], label: str) -> object:
    last: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return action()
        except Exception as error:
            last = error
            if not is_retryable_error(error) or attempt >= MAX_RETRIES - 1:
                raise
            wait = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
            print(f"[retry] {label} intento {attempt + 2}/{MAX_RETRIES} en {wait}s: {error}", file=__import__("sys").stderr)
            time.sleep(wait)
    assert last is not None
    raise last


def exchange_identifiers(team: AppleTeam, old_sub: str) -> dict:
    def generate_transfer() -> dict:
        return apple_post(
            APPLE_MIGRATION_URL,
            {
                "sub": old_sub,
                "target": TEAM_ID,
                "client_id": apple_client_id(),
                "client_secret": team.client_secret(),
            },
            access_token=team.access_token(),
            label="usermigrationinfo (generar transfer_sub)",
        )

    transfer = call_with_retry(generate_transfer, f"transfer_sub sub={old_sub[:20]}...")
    transfer_sub = transfer.get("transfer_sub")
    if not transfer_sub:
        raise AppleError(200, transfer)

    def redeem() -> dict:
        return apple_post(
            APPLE_MIGRATION_URL,
            {
                "transfer_sub": transfer_sub,
                "client_id": apple_client_id(),
                "client_secret": team.client_secret(),
            },
            access_token=team.access_token(),
            label="usermigrationinfo (canjear sub nuevo)",
        )

    exchanged = call_with_retry(redeem, f"canje transfer_sub={transfer_sub[:24]}...")
    new_sub = exchanged.get("sub")
    if not new_sub:
        raise AppleError(200, exchanged)
    return {
        "transfer_sub": transfer_sub,
        "new_sub": new_sub,
        "new_email": exchanged.get("email") or "",
        "is_private_email": as_bool(exchanged.get("is_private_email", False)),
    }


def should_replace_email(current_email: str | None, old_provider_email: str | None, new_email: str) -> bool:
    if not new_email:
        return False
    current = (current_email or "").strip()
    old = (old_provider_email or "").strip()
    if current == new_email:
        return False
    if current == "":
        return True
    if old and current == old:
        return True
    return current.lower().endswith(RELAY_SUFFIX)


class FirebaseAuthAdmin:
    def __init__(self, service_account_path: str):
        import google.oauth2.service_account
        from google.auth.transport.requests import AuthorizedSession

        info = json.loads(Path(service_account_path).read_text(encoding="utf-8"))
        self.project_id = info["project_id"]
        credentials = google.oauth2.service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        self.session = AuthorizedSession(credentials)
        self.base = f"https://identitytoolkit.googleapis.com/v1/projects/{self.project_id}"

    def update(self, payload: dict) -> dict:
        response = self.session.post(f"{self.base}/accounts:update", json=payload, timeout=60)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:500]}
        if response.status_code >= 400 or body.get("error"):
            raise FirebaseWriteError(response.status_code, body)
        return body

    def replace_apple_provider(
        self,
        firebase_uid: str,
        old_sub: str,
        old_email: str | None,
        new_sub: str,
        provider_email: str | None,
    ) -> None:
        self.update({"localId": firebase_uid, "deleteProvider": ["apple.com"]})
        link = {"providerId": "apple.com", "rawId": new_sub}
        if provider_email:
            link["email"] = provider_email
        try:
            self.update({"localId": firebase_uid, "linkProviderUserInfo": link})
        except Exception:
            restore = {"providerId": "apple.com", "rawId": old_sub}
            if old_email:
                restore["email"] = old_email
            try:
                self.update({"localId": firebase_uid, "linkProviderUserInfo": restore})
            except Exception as restore_error:
                raise FirebaseWriteError(
                    0,
                    {
                        "message": "apple.com desvinculado; no se pudo enlazar sub nuevo ni restaurar el viejo.",
                        "firebase_uid": firebase_uid,
                        "restore_error": str(restore_error),
                    },
                ) from restore_error
            raise

    def update_email(self, firebase_uid: str, email: str) -> None:
        self.update(
            {
                "localId": firebase_uid,
                "email": email,
                "emailVerified": True,
            }
        )


def apple_provider(user):
    for provider in user.provider_data:
        if provider.provider_id == "apple.com":
            return provider
    return None


def iter_apple_users_from_firebase() -> Iterator[tuple[object, object]]:
    from firebase_admin import auth

    page = auth.list_users()
    while page:
        for user in page.users:
            provider = apple_provider(user)
            if provider is None:
                continue
            if ONLY_FIREBASE_UID and user.uid != ONLY_FIREBASE_UID:
                continue
            yield user, provider
        page = page.get_next_page()


def manifest_row(user, provider) -> dict:
    return {
        "firebase_uid": user.uid,
        "old_sub": provider.uid,
        "old_provider_email": provider.email or "",
        "account_email": user.email or "",
    }


def write_manifest_row(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def iter_manifest_rows(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def conflicting_user(new_email: str, firebase_uid: str):
    if not new_email:
        return None
    from firebase_admin import auth

    try:
        other = auth.get_user_by_email(new_email)
    except auth.UserNotFoundError:
        return None
    if other.uid == firebase_uid:
        return None
    return other.uid


def record(progress: dict, path: Path, firebase_uid: str, **fields) -> dict:
    current = progress.get(firebase_uid, {})
    current.update(fields)
    progress[firebase_uid] = current
    save_progress(path, progress)
    return current


def migrate_one_user(
    firebase_uid: str,
    old_sub: str,
    old_provider_email: str,
    account_email: str,
    team: AppleTeam,
    firebase_writer: FirebaseAuthAdmin,
    progress: dict,
    progress_path: Path,
    apply: bool,
) -> str:
    """Retorna: skip | dry-run | done | conflict | error"""
    previous = progress.get(firebase_uid, {})

    if previous.get("status") == "done" and previous.get("new_sub"):
        return "skip"

    if previous.get("new_sub") and previous.get("transfer_sub"):
        mapping = {
            "transfer_sub": previous["transfer_sub"],
            "new_sub": previous["new_sub"],
            "new_email": previous.get("new_email") or "",
            "is_private_email": bool(previous.get("is_private_email")),
        }
    else:
        mapping = exchange_identifiers(team, old_sub)
        record(
            progress,
            progress_path,
            firebase_uid,
            status="mapped",
            old_sub=old_sub,
            old_provider_email=old_provider_email,
            account_email=account_email,
            **mapping,
        )

    new_sub = mapping["new_sub"]
    new_email = mapping["new_email"]

    if old_sub == new_sub:
        record(progress, progress_path, firebase_uid, status="done", note="sub sin cambio")
        return "done"

    other_uid = conflicting_user(new_email, firebase_uid)
    if other_uid:
        record(
            progress,
            progress_path,
            firebase_uid,
            status="conflict",
            error=f"El email nuevo ya pertenece al uid {other_uid}.",
        )
        return "conflict"

    if not apply:
        record(progress, progress_path, firebase_uid, status="mapped")
        return "dry-run"

    from firebase_admin import auth

    provider_email = new_email or old_provider_email or None

    def apply_firebase():
        firebase_writer.replace_apple_provider(
            firebase_uid,
            old_sub,
            old_provider_email or None,
            new_sub,
            provider_email,
        )
        refreshed = auth.get_user(firebase_uid)
        if refreshed.uid != firebase_uid:
            raise FirebaseWriteError(0, {"message": "El uid de Firebase cambió"})
        linked = apple_provider(refreshed)
        if linked is None or linked.uid != new_sub:
            raise FirebaseWriteError(0, {"message": "apple.com no quedó con el sub nuevo"})
        email_updated = False
        if should_replace_email(refreshed.email, old_provider_email, new_email):
            firebase_writer.update_email(firebase_uid, new_email)
            email_updated = True
        record(
            progress,
            progress_path,
            firebase_uid,
            status="done",
            email_updated=email_updated,
            error=None,
            **mapping,
        )
        return email_updated

    call_with_retry(apply_firebase, f"firebase uid={firebase_uid}")
    return "done"


def merge_progress(shard_paths: list[Path], output_path: Path) -> dict:
    priority = {"done": 4, "mapped": 3, "conflict": 2, "error": 1}

    def better(existing: dict, incoming: dict) -> bool:
        e = priority.get(existing.get("status", ""), 0)
        i = priority.get(incoming.get("status", ""), 0)
        if i != e:
            return i > e
        return bool(incoming.get("new_sub")) and not existing.get("new_sub")

    merged: dict = {}
    collisions = []
    for shard_path in shard_paths:
        chunk = load_progress(shard_path)
        for uid, row in chunk.items():
            if uid in merged and better(merged[uid], row) is False and merged[uid] != row:
                collisions.append(uid)
            if uid not in merged or better(merged[uid], row):
                merged[uid] = row

    save_progress(output_path, merged)
    stats: dict[str, int] = {}
    for row in merged.values():
        status = row.get("status") or "sin_status"
        stats[status] = stats.get(status, 0) + 1
    return {"merged_count": len(merged), "stats": stats, "collisions": collisions, "output": str(output_path)}