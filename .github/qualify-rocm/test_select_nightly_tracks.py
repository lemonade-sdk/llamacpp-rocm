import unittest
from select_nightly_tracks import select_tracks

PAIRS = [("ubuntu", "gfx1151"), ("windows", "gfx1151")]


def files(version, oses=("linux", "windows")):
    return [f"therock-dist-{os}-gfx1151-{version}.tar.gz" for os in oses]


class SelectionTests(unittest.TestCase):
    def test_next_two_tracks_use_newest_sdk_common_to_all_requested_platforms(self):
        names = (
            files("10.1.0a20261104")
            + files("10.2.0a20261021")
            + files("10.2.0a20261022")
            + files("10.3.0a20261103")
            + files("10.3.0a20261104", ("linux",))
            + files("10.10.0a20261104")
        )
        self.assertEqual(
            select_tracks(["10.0", "10.1"], names, PAIRS),
            {
                "stable_anchor": "10.1",
                "builds": [
                    {
                        "track": "10.2",
                        "rocm_version": "10.2.0a20261022",
                        "create_release": False,
                    },
                    {
                        "track": "10.3",
                        "rocm_version": "10.3.0a20261103",
                        "create_release": True,
                    },
                ],
            },
        )

    def test_zero_or_one_available_track(self):
        self.assertEqual(select_tracks(["10.1"], [], PAIRS)["builds"], [])
        self.assertEqual(
            select_tracks(["10.1"], files("10.3.0a20261104"), PAIRS)["builds"],
            [
                {
                    "track": "10.3",
                    "rocm_version": "10.3.0a20261104",
                    "create_release": True,
                }
            ],
        )

    def test_rollover_and_incomplete_track_do_not_invent_sdk(self):
        names = (
            files("10.2.0a20261022")
            + files("10.3.0a20261104", ("linux",))
            + files("10.4.0a20261104")
        )
        self.assertEqual(
            select_tracks(["10.1", "10.2"], names, PAIRS)["builds"],
            [
                {
                    "track": "10.4",
                    "rocm_version": "10.4.0a20261104",
                    "create_release": True,
                }
            ],
        )

    def test_missing_stable_evidence_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "stable"):
            select_tracks([], files("10.3.0a20261104"), PAIRS)


if __name__ == "__main__":
    unittest.main()
