from appship import debpkg


def test_version_helpers():
    assert debpkg.deb_version({"Version": "1:2.3.4-5"}) == "2.3.4"
    assert debpkg.pacman_safe_version("1:2/3-4") == "1.2.3_4"
    assert debpkg.default_pkgname({"Package": "CodeBuddy-CN"}) == "codebuddy-cn"


def test_depends_translation():
    depends = debpkg.translate_depends({"Depends": "libgtk-3-0 (>= 3.0), libnss3 | libfoo, libc6, libsecret-1-0"})
    assert depends == ["gtk3", "nss", "libsecret"]


def test_render_pkgbuild_and_extra():
    text = debpkg.render_pkgbuild(
        "codebuddy-cn",
        "4.12.0",
        1,
        "CodeBuddy",
        "https://example.test/",
        "x86_64",
        ["gtk3"],
        "codebuddy.deb",
        "abc123",
        "data.tar.xz",
        'chmod 4755 "${pkgdir}/usr/share/buddycn/chrome-sandbox"',
    )
    assert "bsdtar -xpf" in text
    assert "chmod 4755" in text
    assert "chrome-sandbox" in text
    assert "pkgver=4.12.0" in text


def test_available_and_match_file():
    available = debpkg.available()
    assert isinstance(available, bool)
    assert debpkg.match_file("/tmp/Foo.deb") == available
    assert debpkg.match_file("/tmp/Foo.AppImage") is False
