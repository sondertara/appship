"""AppMan core package.

Installed code lives in the Python package (or the standalone zipapp); user
state follows XDG:

  ~/.local/share/appman/apps/<id>/   installed app payloads
  ~/.local/share/appman/plugins/     user plugins
  ~/.local/share/appman/apps.json    registry
  ~/.config/appman/                  configuration
  ~/.local/state/appman/             locks and runtime state
  ~/.cache/appman/                   downloads

Legacy ``~/local`` installations are detected automatically and can be moved
with ``appman migrate --apply``.
"""

__version__ = "3.1.0"
