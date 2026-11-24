from scripts.install_rail_archives import APPROVED, _safe_members


def test_archive_installer_is_strictly_allow_listed():
    assert set(APPROVED) == {
        "CIF_ALL_FULL_DAILY_toc-full.json.gz", "NWR_GTCL20261026.zip",
        "fares_full.zip", "NWR_TrackModel20261026.zip",
        "Darwin Timetable Files.zip", "TPS_Data.tar.gz",
    }


def test_archive_member_path_traversal_is_rejected():
    try:
        _safe_members(["../outside.txt"])
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe archive member was accepted")
