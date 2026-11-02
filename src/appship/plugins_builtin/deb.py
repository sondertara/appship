"""deb → pacman backend.

This is an Arch-family backend: it uses ``makepkg`` to repack the deb's
data.tar as a local pacman package. It is unavailable elsewhere.
"""

from .. import debpkg

NAME = "deb"
BACKEND = "deb"
DESCRIPTION = "deb → pacman backend（Arch 系；需要 pacman/makepkg/fakeroot）"
API_VERSION = 1


def available():
    return debpkg.available()


def unavailable_reason():
    return debpkg.unavailable_reason()


def match_file(path, info):
    return debpkg.match_file(path, info)


def analyze(installer, path):
    return debpkg.analyze(installer, path)


def install(installer, path, meta=None, plugin=None, dry_run=False, auto_yes=False, consume=False):
    return debpkg.install(
        installer,
        path,
        meta=meta,
        plugin=plugin,
        dry_run=dry_run,
        auto_yes=auto_yes,
        consume=consume,
    )


def uninstall(installer, app, dry_run=False, keep_data=False, auto_yes=False):
    return debpkg.uninstall(installer, app, dry_run=dry_run, keep_data=keep_data, auto_yes=auto_yes)


def rollback(installer, app, auto_yes=False, dry_run=False):
    return debpkg.rollback(installer, app, auto_yes=auto_yes, dry_run=dry_run)


def adopt(installer, pkgname, plugin_name=""):
    return debpkg.adopt(installer, pkgname, plugin_name)


def sync_registry(registry):
    return debpkg.sync_registry(registry)


def doctor_checks():
    return debpkg.doctor_checks()
