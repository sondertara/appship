from appship.util import clean_version, compare_versions, version_from_filename, version_from_tag


def test_compare_versions_basic():
    assert compare_versions("2.28.2", "2.28.1") == 1
    assert compare_versions("2.28.1", "2.28.1") == 0
    assert compare_versions("2.28.0", "2.28.1") == -1


def test_unknown_sorts_lowest():
    assert compare_versions("unknown", "1.0.0") == -1
    assert compare_versions("1.0.0", "unknown") == 1


def test_date_versions_are_normalized():
    assert compare_versions("2026.9.24", "2026.09.24") == 0


def test_clean_version_strips_arch_and_os():
    assert clean_version("v1.2.3-linux-x86_64") == "1.2.3"
    assert clean_version("1.2.3.AppImage") == "1.2.3"


def test_versions_from_names():
    assert version_from_filename("Antigravity-2.28.1.AppImage") == "2.28.1"
    assert version_from_filename("App-x86_64.AppImage") == "unknown"
    assert version_from_tag("v1.4.2") == "1.4.2"
