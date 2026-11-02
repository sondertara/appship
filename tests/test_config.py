import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _config_env(tmp_path):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("APPMAN_"):
            env.pop(key)
    env.update(
        {
            "HOME": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path / "share"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "PYTHONPATH": str(REPO / "src"),
        }
    )
    return env


def _read_config(env):
    code = (
        "from appman import config\n"
        "print(int(config.USE_LEGACY_LAYOUT))\n"
        "print(config.LOCAL_DIR)\n"
        "print(config.VERSIONS_DB_PATH)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=REPO,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    return bool(int(out[0])), out[1], out[2]


def test_xdg_is_default(tmp_path):
    env = _config_env(tmp_path)
    legacy, local_dir, db_path = _read_config(env)
    assert legacy is False
    assert local_dir == str(tmp_path / "share" / "appman" / "apps")
    assert db_path == str(tmp_path / "share" / "appman" / "apps.json")


def test_legacy_is_detected(tmp_path):
    env = _config_env(tmp_path)
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / ".apps.json").write_text("{}")
    legacy, local_dir, db_path = _read_config(env)
    assert legacy is True
    assert local_dir == str(tmp_path / "local")
    assert db_path == str(tmp_path / "local" / ".apps.json")


def test_target_registry_wins_over_legacy(tmp_path):
    env = _config_env(tmp_path)
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / ".apps.json").write_text("{}")
    target = tmp_path / "share" / "appman"
    target.mkdir(parents=True)
    (target / "apps.json").write_text("{}")
    legacy, _local_dir, db_path = _read_config(env)
    assert legacy is False
    assert db_path == str(target / "apps.json")
