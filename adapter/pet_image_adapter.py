"""Route the bundled imagegen CLI through isolated image credentials."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit


DEFAULT_CONFIG = Path(__file__).with_name("adapter.json")


def user_variable(name: str) -> str:
    """Read newly saved Windows User variables without restarting Codex."""
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as registry:
                value, _ = winreg.QueryValueEx(registry, name)
                return str(value).strip()
        except (FileNotFoundError, OSError):
            pass
    return ""


def image_key() -> str:
    # Never fall back to OPENAI_API_KEY: that may belong to the chat group.
    value = os.environ.get("PET_IMAGE_API_KEY", "").strip()
    value = value or user_variable("PET_IMAGE_API_KEY")
    if not value:
        raise ValueError("PET_IMAGE_API_KEY is missing; save the image key locally first.")
    return value


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    base_url = config["base_url"].rstrip("/")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != "/v1"
        or "://" in base_url[len("https://") :]
    ):
        raise ValueError("base_url must be an HTTPS API origin followed by /v1.")
    config["base_url"] = base_url
    if config.get("model") != "gpt-image-2":
        raise ValueError("This adapter is configured for gpt-image-2; model changes must be explicit.")
    config["timeout_seconds"] = float(config.get("timeout_seconds", 240))
    if config["timeout_seconds"] <= 0:
        raise ValueError("timeout_seconds must be positive.")
    return config


def redact(message: str, secret: str = "") -> str:
    if secret:
        message = message.replace(secret, "[REDACTED]")
    message = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", message)
    message = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", message)
    return message[:1000]


@contextmanager
def isolated_environment(key: str, base_url: str):
    updates = {"OPENAI_API_KEY": key, "OPENAI_BASE_URL": base_url}
    previous = {name: os.environ.get(name) for name in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def bundled_cli_path() -> Path:
    codex_dir = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return codex_dir / "skills" / ".system" / "imagegen" / "scripts" / "image_gen.py"


def run_bundled_cli(arguments: list[str], config: dict, key: str) -> int:
    """Keep CLI validation and output handling; replace only its client factory."""
    from openai import OpenAI

    cli_path = bundled_cli_path()
    if not cli_path.is_file():
        raise FileNotFoundError(f"Bundled imagegen CLI missing: {cli_path}")
    spec = importlib.util.spec_from_file_location("_pet_bundled_imagegen", cli_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def create_client():
        return OpenAI(
            api_key=key,
            base_url=config["base_url"],
            max_retries=0,
            timeout=config["timeout_seconds"],
        )

    # Runtime dependency injection only: the installed CLI file stays untouched.
    module._create_client = create_client
    forwarded = [*arguments]
    original_output_paths = module._build_output_paths

    def preflight_output_paths(*args, **kwargs):
        paths = original_output_paths(*args, **kwargs)
        if "--force" not in forwarded:
            existing = next((path for path in paths if path.exists()), None)
            if existing is not None:
                raise FileExistsError(f"Output already exists; no API request submitted: {existing}")
        return paths

    module._build_output_paths = preflight_output_paths
    if "--model" not in forwarded and not any(a.startswith("--model=") for a in forwarded):
        forwarded.extend(["--model", config["model"]])
    old_argv = sys.argv
    with isolated_environment(key, config["base_url"]):
        try:
            sys.argv = [str(cli_path), *forwarded]
            return module.main() or 0
        finally:
            sys.argv = old_argv


def check_models(config: dict, key: str) -> dict:
    from openai import OpenAI

    with OpenAI(
        api_key=key, base_url=config["base_url"], max_retries=0, timeout=30
    ) as client:
        models = [model.id for model in client.models.list().data]
    result = {
        "credential_available": True,
        "models_endpoint_ok": True,
        "requested_model": config["model"],
        "requested_model_listed": config["model"] in models,
        "image_generation_verified": False,
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report", type=Path, help="Write a credential-free result report.")
    parser.add_argument("command", choices=["check", "generate", "edit"])
    args, forwarded = parser.parse_known_args(argv)
    started = time.monotonic()
    report = {"command": args.command, "started_at": datetime.now(timezone.utc).isoformat()}
    key = ""
    exit_code = 1
    try:
        config = load_config(args.config)
        key = image_key()
        report.update(base_url=config["base_url"], model=config["model"], automatic_retries=0)
        if args.command == "check":
            if forwarded:
                raise ValueError("check does not accept image-generation arguments.")
            report.update(check_models(config, key))
            exit_code = 0
        else:
            exit_code = run_bundled_cli([args.command, *forwarded], config, key)
        report["status"] = "ok" if exit_code == 0 else "failed"
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
        report.update(status="ok" if exit_code == 0 else "failed", error_type="CLIExit")
    except Exception as exc:
        message = redact(str(exc), key)
        report.update(status="failed", error_type=type(exc).__name__, message=message)
        status_code = getattr(exc, "status_code", None)
        if status_code is not None:
            report["http_status"] = status_code
        print(f"{type(exc).__name__}: {message}", file=sys.stderr)
        print("No automatic retry was submitted.", file=sys.stderr)
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
