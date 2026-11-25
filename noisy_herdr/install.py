"""Link the plugin, back up Herdr config, and prevent duplicate built-in sounds."""
import datetime
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import PLUGIN_ID
from .storage import herdr_config_dir, update_settings


def mute_builtin(text):
    """Change only ui.sound.enabled in ordinary table-form TOML."""
    section = re.search(r"(?m)^[ \t]*\[[ \t]*ui[ \t]*\.[ \t]*sound[ \t]*\][ \t]*(?:#[^\n]*)?$", text)
    if section:
        start = section.end()
        next_table = re.search(r"(?m)^[ \t]*\[", text[start:])
        end = start + next_table.start() if next_table else len(text)
        body = text[start:end]
        enabled = re.compile(r"(?m)^([ \t]*enabled[ \t]*=[ \t]*)(?:true|false)([ \t]*(?:#[^\n]*)?)$")
        if enabled.search(body):
            body = enabled.sub(r"\g<1>false\2", body)
        elif re.search(r"(?m)^\s*enabled\s*=", body):
            raise ValueError("Cannot safely edit ui.sound.enabled; set it to false in Herdr Settings > Sound")
        else:
            body = "\nenabled = false\n" + body.lstrip("\n")
        return text[:start] + body + text[end:]
    # Inline tables and quoted/dotted keys need a full TOML editor; don't guess.
    if re.search(r"(?m)^\s*(?:sound|ui\.sound)(?:\.|\s*=)", text):
        raise ValueError("Set ui.sound.enabled = false manually, then install with --keep-built-in")
    return text.rstrip() + "\n\n# Noisy Herdr handles sounds, including the focused tab.\n[ui.sound]\nenabled = false\n"


def install(keep_builtin=False):
    root = Path(__file__).resolve().parent.parent
    binary = os.environ.get("HERDR_BIN_PATH", "herdr")
    config = Path(os.environ.get("HERDR_CONFIG_PATH", herdr_config_dir() / "config.toml")).expanduser().resolve()
    candidate = None
    original = config.read_text(encoding="utf-8-sig") if config.exists() else ""
    replacement = original if keep_builtin else mute_builtin(original)
    if replacement != original:
        # Herdr itself validates the candidate before any live configuration changes.
        with tempfile.NamedTemporaryFile("w", suffix=".toml", encoding="utf-8", delete=False) as stream:
            candidate = Path(stream.name)
            stream.write(replacement)
        try:
            env = dict(os.environ, HERDR_CONFIG_PATH=str(candidate))
            subprocess.run([binary, "config", "check"], env=env, check=True,
                           capture_output=True, text=True, timeout=10)
        finally:
            candidate.unlink(missing_ok=True)
    subprocess.run([binary, "plugin", "link", str(root)], check=True, timeout=15,
                   capture_output=True, text=True)
    update_settings()  # Materialize the defaults without overwriting existing choices.
    if replacement != original:
        config.parent.mkdir(parents=True, exist_ok=True)
        if config.exists():
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = config.with_name(config.name + ".before-noisy-herdr-" + stamp)
            shutil.copy2(config, backup)
            print("Backed up Herdr config to " + str(backup))
        fd, path = tempfile.mkstemp(prefix=".noisy-herdr-", dir=str(config.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(replacement)
            os.replace(path, config)
        finally:
            if os.path.exists(path):
                os.unlink(path)
        result = subprocess.run([binary, "server", "reload-config"], capture_output=True,
                                text=True, timeout=10)
        if result.returncode:
            print("Config saved. Reload Herdr config from its menu when the server is available.")
    from .events import seed
    try:
        seed()
    except (OSError, ValueError, subprocess.SubprocessError):
        pass  # Offline installation is supported; the startup hook seeds next time.
    from .cli import open_panel
    try:
        opened = open_panel(focus=False) == 0
    except (OSError, subprocess.SubprocessError):
        opened = False
    print("Noisy Herdr installed.")
    if opened:
        print("Click the Noisy Herdr tab for on/off, sounds, and volume.")
    else:
        print("Open the settings tab when Herdr is running:")
        print("  herdr plugin action invoke noisy-herdr.settings")
    return 0
