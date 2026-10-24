import json
import os
import stat
import uuid
from pathlib import Path
from .errors import BaseError


def state_home():
    return Path(os.environ.get("CONTENTBASE_STATE_DIR", str(Path.home() / ".local/state/content-company-base"))).expanduser().resolve()


def load_config(path=None):
    path = Path(path or state_home() / "config.json").expanduser()
    if path.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise BaseError("configuration_must_be_outside_source")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BaseError("configuration_required") from None
    if not isinstance(config, dict):
        raise BaseError("invalid_configuration")
    f = config.get("feishu", {})
    if not isinstance(f, dict) or not isinstance(config.get("yxer"), dict):
        raise BaseError("invalid_configuration")
    secret_keys = {"apikey", "clientsecret", "password", "accesstoken", "refreshtoken", "authorization", "cookie", "oauthsecret"}
    def validate_public_settings(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).lower().replace("_", "").replace("-", "") in secret_keys:
                    raise BaseError("credentials_belong_in_vendor_cli")
                validate_public_settings(nested)
        elif isinstance(value, list):
            for nested in value:
                validate_public_settings(nested)
    validate_public_settings(config)
    for key in ("profile", "base_token", "table_id"):
        value = f.get(key)
        if not isinstance(value, str) or not value or value.startswith("YOUR_") or len(value) > 256 or value != value.strip() or any(ord(c) < 32 for c in value):
            raise BaseError("feishu_binding_required")
    required = {"title", "body", "stage", "published_url", "metrics", "review"}
    if not isinstance(f.get("fields"), dict) or not required <= f["fields"].keys():
        raise BaseError("feishu_field_mapping_required")
    if any(not isinstance(value, str) or not value.strip() or len(value) > 200 for value in f["fields"].values()):
        raise BaseError("feishu_field_mapping_invalid")
    if len(set(f["fields"].values())) != len(f["fields"]):
        raise BaseError("field_mapping_collision")
    stages = f.get("stages", {})
    if not isinstance(stages, dict) or any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in stages.values()):
        raise BaseError("invalid_stage_mapping")
    for vendor in ("feishu", "yxer"):
        section = config.get(vendor)
        if not isinstance(section, dict):
            raise BaseError("invalid_configuration")
        executable = section.get("cli", "lark-cli" if vendor == "feishu" else "yxer")
        if not isinstance(executable, str) or not executable or any(ord(c) < 32 for c in executable):
            raise BaseError("invalid_cli_path")
        section["cli"] = os.path.expanduser(executable)
    return config


def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    except (TypeError, ValueError):
        raise BaseError("invalid_private_json") from None
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = "." + path.name + "." + uuid.uuid4().hex + ".tmp"
    try:
        try:
            old = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(old.st_mode):
                raise BaseError("private_path_not_regular")
        except FileNotFoundError:
            pass
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
