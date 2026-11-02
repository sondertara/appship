"""AppShip core package.

Installed code lives in the Python package (or the standalone zipapp); user
state follows XDG:

  ~/.local/share/appship/apps/<id>/   installed app payloads
  ~/.local/share/appship/plugins/     user plugins
  ~/.local/share/appship/apps.json    registry
  ~/.config/appship/                  configuration
  ~/.local/state/appship/             locks and runtime state
  ~/.cache/appship/                   downloads

Legacy ``~/local`` installations are detected automatically and can be moved
with ``appship migrate --apply``.
"""

__version__ = "0.1.0"
