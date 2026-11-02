"""Plugin loader.

Built-in plugins are modules under ``appship.plugins_builtin``. User plugins are
``*.py`` files in the XDG plugin directory (or the legacy ``~/local/appman.d/plugins``
directory). User plugins override built-ins by ``NAME``.

Every plugin may implement these optional hooks:

  NAME = 'codebuddy'                       # defaults to the module/file name
  DESCRIPTION = '...'

  def match(app) -> bool                   # registered apps
  def match_file(path, info) -> bool       # incoming file
  def check(app) -> Update | None          # None = fall back to the generic checker
  def deb_pkgname(fields) -> str
  def deb_pkgver(fields, filename) -> str
  def deb_app_name(fields) -> str
  def deb_depends(fields, depends) -> list
  def deb_package_extra(fields) -> str
  def data_dirs(app) -> list[str]

Plugins should only import from ``appship.api``. They are arbitrary Python code:
only install plugins you trust.
"""

import importlib
import importlib.util
import os
import pkgutil
import sys

from . import plugins_builtin
from .config import LEGACY_PLUGINS_DIR, PLUGINS_DIR, TARGET_PLUGINS_DIR


def _install_legacy_aliases():
    """Let pre-rename plugins keep importing ``appman.api``.

    This is migration compatibility only; new plugins should import
    ``appship.api``.
    """
    import appship

    try:
        import appship.api  # noqa: F401
    except Exception:
        return
    sys.modules.setdefault("appman", appship)
    for name in (
        "api",
        "appimage",
        "checks",
        "config",
        "debpkg",
        "desktop",
        "installer",
        "migrate",
        "plugins",
        "registry",
        "repair",
        "util",
    ):
        try:
            module = importlib.import_module(f"appship.{name}")
        except Exception:
            continue
        sys.modules.setdefault(f"appman.{name}", module)


class Plugin:
    def __init__(self, name, path, module, source="user"):
        self.name = name
        self.path = path
        self.module = module
        self.source = source

    @property
    def description(self):
        return getattr(self.module, "DESCRIPTION", "")

    def hooks(self):
        names = (
            "match",
            "match_file",
            "check",
            "deb_pkgname",
            "deb_pkgver",
            "deb_app_name",
            "deb_depends",
            "deb_package_extra",
            "data_dirs",
        )
        return [n for n in names if callable(getattr(self.module, n, None))]

    def _call(self, hook, *args):
        fn = getattr(self.module, hook, None)
        if not callable(fn):
            return None
        try:
            return fn(*args)
        except Exception as exc:
            print(f"⚠️ 插件 {self.name}.{hook} 出错: {exc!r}", file=sys.stderr)
            return None


class PluginManager:
    def __init__(self, plugins_dirs=None):
        if plugins_dirs is None:
            dirs = []
            for directory in (TARGET_PLUGINS_DIR, PLUGINS_DIR, LEGACY_PLUGINS_DIR):
                directory = os.path.abspath(os.path.expanduser(directory))
                if directory and directory not in dirs:
                    dirs.append(directory)
            plugins_dirs = dirs
        self.plugins_dirs = [os.path.abspath(os.path.expanduser(d)) for d in plugins_dirs]
        self.plugins_dir = self.plugins_dirs[0] if self.plugins_dirs else ""
        self.plugins = []
        self.errors = []
        self._load()

    def _load(self):
        _install_legacy_aliases()
        seen = {}
        for directory in self.plugins_dirs:
            self._load_dir(directory, seen)
        self._load_builtin(seen)
        self.plugins = list(seen.values())

    def _load_dir(self, directory, seen):
        if not os.path.isdir(directory):
            return
        for index, fname in enumerate(sorted(os.listdir(directory))):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            path = os.path.join(directory, fname)
            stem = fname[:-3]
            mod_name = f"appship_plugin_{index}_{stem}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)
            except Exception as exc:
                self.errors.append((fname, exc))
                sys.modules.pop(mod_name, None)
                continue
            name = getattr(module, "NAME", stem)
            if name in seen:
                continue
            seen[name] = Plugin(name, path, module, source="user")

    def _load_builtin(self, seen):
        for info in pkgutil.iter_modules(plugins_builtin.__path__):
            if info.name.startswith("_"):
                continue
            try:
                module = importlib.import_module(f"{plugins_builtin.__name__}.{info.name}")
            except Exception as exc:
                self.errors.append((f"builtin:{info.name}", exc))
                continue
            name = getattr(module, "NAME", info.name)
            if name in seen:
                continue
            seen[name] = Plugin(name, getattr(module, "__file__", f"builtin:{info.name}"), module, source="builtin")

    def warn_errors(self):
        for fname, exc in self.errors:
            print(f"⚠️ 插件 {fname} 加载失败: {exc!r}", file=sys.stderr)

    def for_app(self, app):
        for plugin in self.plugins:
            if plugin._call("match", app):
                return plugin
        return None

    def for_file(self, path, info):
        for plugin in self.plugins:
            if plugin._call("match_file", path, info):
                return plugin
        return None

    def get(self, name):
        return next((p for p in self.plugins if p.name == name), None)

    def list(self):
        print("\n🧩 插件目录:")
        for directory in self.plugins_dirs:
            print(f"  - {directory}")
        if not self.plugins and not self.errors:
            print("  (没有插件)\n")
            return
        for plugin in self.plugins:
            print(f"  • {plugin.name:<14} [{plugin.source}] {plugin.description}")
            print(f"    {'':<14} 钩子: {', '.join(plugin.hooks()) or '无'}  ({os.path.basename(plugin.path)})")
        self.warn_errors()
        print()
