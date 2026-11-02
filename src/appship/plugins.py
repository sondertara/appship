"""Plugin loader and capability dispatch.

Two plugin roles are supported:

* **App plugins** provide app-specific metadata/checks (``match``, ``check``,
  ``deb_*``, ``data_dirs``).
* **Backend plugins** provide a package format lifecycle (``BACKEND``,
  ``available``, ``analyze``, ``install``, ``uninstall``, ``rollback``,
  ``adopt``, ``sync_registry``, ``doctor_checks``).

Built-in plugins live under ``appship.plugins_builtin``. User plugins are
``*.py`` files in the XDG plugin directory (or the legacy
``~/local/appman.d/plugins`` directory). A user plugin overrides a built-in
plugin with the same ``NAME``.

Plugins are arbitrary Python code: only install plugins you trust.
"""

import importlib
import importlib.util
import os
import pkgutil
import sys

from . import plugins_builtin
from .config import LEGACY_PLUGINS_DIR, PLUGINS_DIR, TARGET_PLUGINS_DIR
from .plugin_api import API_VERSION as PLUGIN_API_VERSION
from .util import AppShipError

HOOKS = (
    "match",
    "match_file",
    "check",
    "deb_pkgname",
    "deb_pkgver",
    "deb_app_name",
    "deb_depends",
    "deb_package_extra",
    "data_dirs",
    "available",
    "unavailable_reason",
    "analyze",
    "install",
    "uninstall",
    "rollback",
    "adopt",
    "sync_registry",
    "doctor_checks",
)


class PluginError(AppShipError):
    """A plugin failed or violated the plugin API."""


def _install_legacy_aliases():
    """Let pre-rename plugins keep importing ``appman.api``."""
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
        self._availability_error = ""

    @property
    def description(self):
        return getattr(self.module, "DESCRIPTION", "")

    @property
    def backend(self):
        return str(getattr(self.module, "BACKEND", "") or "").lower()

    @property
    def requires_backend(self):
        return str(getattr(self.module, "REQUIRES_BACKEND", "") or "").lower()

    @property
    def priority(self):
        try:
            return int(getattr(self.module, "PRIORITY", 0))
        except (TypeError, ValueError):
            return 0

    @property
    def api_version(self):
        try:
            return int(getattr(self.module, "API_VERSION", PLUGIN_API_VERSION))
        except (TypeError, ValueError):
            return -1

    @property
    def available(self):
        fn = getattr(self.module, "available", None)
        if not callable(fn):
            return True
        try:
            return bool(fn())
        except Exception as exc:
            self._availability_error = repr(exc)
            return False

    @property
    def unavailable_reason(self):
        fn = getattr(self.module, "unavailable_reason", None)
        if callable(fn):
            try:
                reason = str(fn() or "")
                if reason:
                    return reason
            except Exception as exc:
                return repr(exc)
        return self._availability_error

    def hooks(self):
        return [name for name in HOOKS if callable(getattr(self.module, name, None))]

    def call(self, hook, *args, required=False, **kwargs):
        fn = getattr(self.module, hook, None)
        if not callable(fn):
            if required:
                raise PluginError(f"插件 {self.name} 不支持 {hook}")
            return None
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if required:
                raise PluginError(f"插件 {self.name}.{hook} 失败: {exc}") from exc
            print(f"⚠️ 插件 {self.name}.{hook} 出错: {exc!r}", file=sys.stderr)
            return None

    def _call(self, hook, *args):
        return self.call(hook, *args)


class PluginManager:
    def __init__(self, plugins_dirs=None, load_plugins=True):
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
        self._plugin_seq = 0
        self._backends = {}
        self._availability = {}
        self._reasons = {}
        if load_plugins:
            self._load()
        self._resolve()

    def _load(self):
        _install_legacy_aliases()
        seen = {}
        for directory in self.plugins_dirs:
            self._load_dir(directory, seen)
        self._load_builtin(seen)
        self.plugins = list(seen.values())

    def _accept_module(self, name, module):
        api_version = getattr(module, "API_VERSION", PLUGIN_API_VERSION)
        try:
            api_version = int(api_version)
        except (TypeError, ValueError):
            api_version = -1
        if api_version != PLUGIN_API_VERSION:
            self.errors.append((name, PluginError(f"API_VERSION {api_version} != {PLUGIN_API_VERSION}")))
            return False
        return True

    def _load_dir(self, directory, seen):
        if not os.path.isdir(directory):
            return
        for fname in sorted(os.listdir(directory)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            path = os.path.join(directory, fname)
            stem = fname[:-3]
            mod_name = f"appship_plugin_{self._plugin_seq}_{stem}"
            self._plugin_seq += 1
            try:
                spec = importlib.util.spec_from_file_location(mod_name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)
            except Exception as exc:
                self.errors.append((fname, exc))
                sys.modules.pop(mod_name, None)
                continue
            if not self._accept_module(fname, module):
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
            if not self._accept_module(f"builtin:{info.name}", module):
                continue
            name = getattr(module, "NAME", info.name)
            if name in seen:
                continue
            seen[name] = Plugin(name, getattr(module, "__file__", f"builtin:{info.name}"), module, source="builtin")

    def _resolve(self):
        self._backends = {}
        self._availability = {}
        self._reasons = {}
        for plugin in self.plugins:
            if plugin.backend:
                self._backends.setdefault(plugin.backend, plugin)
        for plugin in self.plugins:
            if plugin.backend:
                self._set_availability(plugin)
        for plugin in self.plugins:
            if plugin.backend:
                continue
            required = plugin.requires_backend
            if required:
                backend = self._backends.get(required)
                if backend is None or not self.is_available(backend):
                    self._availability[plugin.name] = False
                    self._reasons[plugin.name] = f"需要 backend {required}（不可用）"
                    continue
            self._set_availability(plugin)

    def _set_availability(self, plugin):
        available = plugin.available
        self._availability[plugin.name] = available
        self._reasons[plugin.name] = "" if available else (plugin.unavailable_reason or "不可用")

    def is_available(self, plugin):
        return self._availability.get(plugin.name, True)

    def availability_reason(self, plugin):
        return self._reasons.get(plugin.name, "")

    def backends(self, only_available=True):
        return [
            plugin for plugin in self.plugins if plugin.backend and (not only_available or self.is_available(plugin))
        ]

    def backend(self, kind):
        backend = self._backends.get(str(kind or "").lower())
        if backend is None or not self.is_available(backend):
            return None
        return backend

    def backend_for_file(self, path, info=None):
        info = info or {}
        for backend in self.backends():
            if backend.call("match_file", path, info):
                return backend
        return None

    def for_app(self, app):
        candidates = [plugin for plugin in self.plugins if not plugin.backend and self.is_available(plugin)]
        for plugin in sorted(candidates, key=lambda p: -p.priority):
            if plugin.call("match", app):
                return plugin
        return None

    def for_file(self, path, info, include_backends=False):
        candidates = [
            plugin for plugin in self.plugins if self.is_available(plugin) and (include_backends or not plugin.backend)
        ]
        for plugin in sorted(candidates, key=lambda p: -p.priority):
            if plugin.call("match_file", path, info):
                return plugin
        return None

    def sync_registry(self, registry):
        changed = False
        for backend in self.backends():
            if backend.call("sync_registry", registry):
                changed = True
        if changed:
            registry.save()

    def doctor_checks(self):
        checks = []
        for backend in self.backends(only_available=False):
            result = backend.call("doctor_checks") or []
            if isinstance(result, list):
                checks.extend(result)
        return checks

    def status(self):
        rows = []
        for plugin in self.plugins:
            rows.append(
                {
                    "name": plugin.name,
                    "description": plugin.description,
                    "source": plugin.source,
                    "path": plugin.path,
                    "hooks": plugin.hooks(),
                    "backend": plugin.backend,
                    "requires_backend": plugin.requires_backend,
                    "available": self.is_available(plugin),
                    "reason": self.availability_reason(plugin),
                    "api_version": plugin.api_version,
                    "priority": plugin.priority,
                }
            )
        return rows

    def warn_errors(self):
        for fname, exc in self.errors:
            print(f"⚠️ 插件 {fname} 加载失败: {exc!r}", file=sys.stderr)

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
            tags = [plugin.source]
            if plugin.backend:
                tags.append(f"backend:{plugin.backend}")
            if plugin.requires_backend:
                tags.append(f"requires:{plugin.requires_backend}")
            if not self.is_available(plugin):
                tags.append(f"unavailable: {self.availability_reason(plugin)}")
            print(f"  • {plugin.name:<14} [{', '.join(tags)}] {plugin.description}")
            print(f"    {'':<14} 钩子: {', '.join(plugin.hooks()) or '无'}  ({os.path.basename(plugin.path)})")
        self.warn_errors()
        print()
