from appship.cli import build_parser, main


def test_parser_subcommands():
    parser = build_parser()
    args = parser.parse_args(["check", "antigravity"])
    assert args.command == "check"
    assert args.app == "antigravity"
    args = parser.parse_args(["migrate", "--dry-run"])
    assert args.command == "migrate"
    assert args.apply is False
    args = parser.parse_args(["upgrade", "--dry-run"])
    assert args.command == "upgrade"
    assert args.dry_run is True


def test_version_command(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip().startswith("appship ")


def test_no_args_prints_help(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "COMMAND" in out
    assert "migrate" in out


def test_removed_short_flags_fail_cleanly(capsys):
    assert main(["-u", "x"]) == 2
    assert "已移除" in capsys.readouterr().err
    assert main(["-U", "x"]) == 2
    assert "已移除" in capsys.readouterr().err


def test_install_requires_a_file(capsys):
    parser = build_parser()
    try:
        parser.parse_args(["install"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("install without FILE should fail")
    assert "FILE" in capsys.readouterr().err
