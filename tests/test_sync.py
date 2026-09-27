import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "lib",
                                "openmediavault-lightroomsync"))

from omv_lightroomsync import auth, imaging, naming  # noqa: E402
from omv_lightroomsync.api import LightroomClient, parse_json  # noqa: E402
from omv_lightroomsync.config import Config, ConfigError  # noqa: E402
from omv_lightroomsync.state import State  # noqa: E402
from omv_lightroomsync.sync import Syncer, SyncError  # noqa: E402

from fake_lightroom import FakeLightroom, jpeg, make_asset  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.join(self.tmp.name, "share")
        os.mkdir(self.base)
        self.target = os.path.join(self.base, "Lightroom")
        self.fake = FakeLightroom()
        self.tokens = auth.TokenStore(os.path.join(self.tmp.name, "auth.json"),
                                      transport=self.fake)
        self.tokens.complete("cid", "secret", "https://localhost/",
                             "https://localhost/?code=the-code&state=x")
        self.client = LightroomClient(self.tokens, transport=self.fake,
                                      sleep=lambda s: None)
        self.state = State(os.path.join(self.tmp.name, "state.db"))
        self.addCleanup(self.state.close)

    def config(self, **kw):
        kw.setdefault("owner", "")
        kw.setdefault("group", "")
        return Config(base_dir=self.base, target_dir=self.target, **kw).validate()

    def sync(self, **kw):
        opts = {k: kw.pop(k) for k in ("dry_run", "allow_mass_delete") if k in kw}
        clock = iter(range(0, 10**6, 5))
        syncer = Syncer(self.config(**kw), self.client, self.state,
                        sleep=lambda s: None, clock=lambda: next(clock), **opts)
        return syncer.run()

    def files(self):
        out = []
        for root, _, names in os.walk(self.target):
            out += [os.path.relpath(os.path.join(root, n), self.target) for n in names]
        return sorted(out)


class AuthTest(Base):
    def test_extract_code(self):
        self.assertEqual(auth.extract_code("https://localhost/?code=abc&x=1"), "abc")
        self.assertEqual(auth.extract_code("  abc  "), "abc")
        with self.assertRaisesRegex(auth.AuthError, "access_denied"):
            auth.extract_code("https://localhost/?error=access_denied")

    def test_authorize_url_has_scopes(self):
        url = auth.authorize_url("cid", "https://localhost/")
        self.assertIn("lr_partner_rendition_apis", url)
        self.assertIn("offline_access", url)

    def test_token_file_is_private_and_refreshes(self):
        path = self.tokens.path
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(self.tokens.access_token(), "at-1")
        self.tokens.data["access_expires_at"] = 0
        self.assertEqual(self.tokens.access_token(), "at-2")
        self.assertEqual(auth.TokenStore(path).data["access_token"], "at-2")

    def test_401_triggers_refresh(self):
        self.fake.fail_once["/v2/catalog"] = 401
        self.assertEqual(self.client.catalog_id(), "cat1")
        self.assertEqual(self.tokens.data["access_token"], "at-2")

    def test_json_prefix(self):
        self.assertEqual(parse_json(b"while (1) {}\n{\"a\": 1}"), {"a": 1})


class NamingTest(unittest.TestCase):
    def test_default_templates(self):
        tokens = naming.asset_tokens(make_asset("abcdef123456"))
        rel = naming.render_relpath("{year}/{year}-{month}",
                                    "{year}{month}{day}_{hour}{minute}{second}_{name}",
                                    tokens)
        self.assertEqual(rel, "2024/2024-03/20240305_141516_IMG_0001.jpg")

    def test_unknown_capture_date_falls_back(self):
        asset = make_asset("a1", capture="0000-00-00T00:00:00")
        self.assertEqual(naming.capture_datetime(asset).year, 2024)

    def test_sanitizes_and_drops_empty_segments(self):
        tokens = naming.asset_tokens(make_asset("a1", filename="../../etc/passwd"))
        rel = naming.render_relpath("{album}/../{year}", "{name}", tokens)
        self.assertEqual(rel, "2024/_.._etc_passwd.jpg")

    def test_albumpath_nests(self):
        tokens = naming.asset_tokens(make_asset("a1"), "Kids", "Family/Kids")
        self.assertEqual(naming.render_relpath("{albumpath}", "{name}", tokens),
                         "Family/Kids/IMG_0001.jpg")

    def test_unknown_token_rejected(self):
        with self.assertRaises(ConfigError):
            naming.validate_template("{year}/{camera}", "folder template")


class SyncTest(Base):
    def test_downloads_all_pages(self):
        for i in range(5):
            self.fake.add(make_asset(f"a{i}", filename=f"IMG_{i}.jpg"))
        stats = self.sync()
        self.assertEqual(stats.listed, 5)
        self.assertEqual(stats.downloaded, 5)
        self.assertEqual(len(self.files()), 5)
        # Second run: nothing to do.
        stats = self.sync()
        self.assertEqual((stats.downloaded, stats.unchanged), (0, 5))

    def test_mtime_set_to_capture_date(self):
        self.fake.add(make_asset("a1"))
        self.sync()
        path = os.path.join(self.target, self.files()[0])
        self.assertEqual(os.path.getmtime(path),
                         naming.capture_datetime(make_asset("a1")).timestamp())

    def test_name_collisions_get_suffix(self):
        self.fake.add(make_asset("a1"))
        self.fake.add(make_asset("a2"))  # same name and capture time
        self.sync()
        self.assertEqual(self.files(), ["2024/2024-03/20240305_141516_IMG_0001.jpg",
                                        "2024/2024-03/20240305_141516_IMG_0001_2.jpg"])

    def test_never_overwrites_foreign_files(self):
        self.fake.add(make_asset("a1"))
        os.makedirs(os.path.join(self.target, "2024"))
        with open(os.path.join(self.target, "2024", "IMG_0001.jpg"), "w") as fh:
            fh.write("mine")
        self.sync(folder_template="{year}", filename_template="{name}")
        self.assertEqual(self.files(), ["2024/IMG_0001.jpg", "2024/IMG_0001_2.jpg"])
        with open(os.path.join(self.target, "2024", "IMG_0001.jpg")) as fh:
            self.assertEqual(fh.read(), "mine")

    def test_edited_photo_is_redownloaded(self):
        asset = make_asset("a1")
        self.fake.add(asset)
        self.sync()
        asset["updated"] = "2025-01-01T00:00:00Z"
        self.assertEqual(self.sync().updated, 1)
        asset["updated"] = "2025-02-01T00:00:00Z"
        self.assertEqual(self.sync(redownload_edited=False).updated, 0)

    def test_template_change_renames_without_download(self):
        self.fake.add(make_asset("a1"))
        self.sync()
        stats = self.sync(folder_template="{year}", filename_template="{name}")
        self.assertEqual((stats.moved, stats.downloaded), (1, 0))
        self.assertEqual(self.files(), ["2024/IMG_0001.jpg"])

    def test_swapped_paths_never_clobber_files(self):
        a = make_asset("a1", filename="one.jpg")
        b = make_asset("a2", filename="two.jpg")
        self.fake.add(a)
        self.fake.add(b)
        self.sync(folder_template="", filename_template="{name}")
        # a1 is edited and b's new name is a1's old name.
        a["updated"] = "2025-01-01T00:00:00Z"
        a["payload"]["importSource"]["fileName"] = "new.jpg"
        b["payload"]["importSource"]["fileName"] = "one.jpg"
        self.sync(folder_template="", filename_template="{name}")
        entries = {e.asset_id: e.relpath for e in self.state.all().values()}
        self.assertEqual(entries, {"a1": "new.jpg", "a2": "one_2.jpg"})
        self.assertEqual(self.files(), ["new.jpg", "one_2.jpg"])

    def test_deleted_photo_modes(self):
        for i in range(3):
            self.fake.add(make_asset(f"a{i}", filename=f"IMG_{i}.jpg"))
        self.sync()
        self.fake.assets.pop(0)
        self.assertEqual(self.sync(delete_mode="keep").deleted, 0)
        self.assertEqual(len(self.files()), 3)
        stats = self.sync(delete_mode="trash")
        self.assertEqual(stats.trashed, 1)
        self.assertTrue(any(f.startswith(".lightroom-trash/") for f in self.files()))
        self.fake.assets.pop(0)
        self.assertEqual(self.sync(delete_mode="delete").deleted, 1)

    def test_mass_delete_guard(self):
        for i in range(30):
            self.fake.add(make_asset(f"a{i}", filename=f"IMG_{i}.jpg"))
        self.sync()
        self.fake.assets.clear()
        with self.assertRaises(SyncError):
            self.sync(delete_mode="delete")
        self.assertEqual(len(self.files()), 30)
        self.assertEqual(self.sync(delete_mode="delete", allow_mass_delete=True).deleted, 30)

    def test_dry_run_writes_nothing(self):
        self.fake.add(make_asset("a1"))
        stats = self.sync(dry_run=True)
        self.assertEqual(stats.downloaded, 1)
        self.assertEqual(self.files(), [])
        self.assertEqual(self.state.all(), {})

    def test_missing_shared_folder_is_not_created(self):
        os.rmdir(self.base)
        with self.assertRaisesRegex(SyncError, "mounted"):
            self.sync()
        self.assertFalse(os.path.exists(self.base))

    def test_captured_after_filter(self):
        self.fake.add(make_asset("old", capture="2020-01-01T00:00:00"))
        self.fake.add(make_asset("new", capture="2024-01-01T00:00:00"))
        self.assertEqual(self.sync(captured_after="2023-01-01").downloaded, 1)


class RenditionTest(Base):
    def test_fullsize_generated_on_demand(self):
        self.fake.add(make_asset("a1"))
        self.fake.generatable = {"fullsize"}
        stats = self.sync(rendition="fullsize")
        self.assertEqual(dict(stats.renditions), {"fullsize": 1})
        methods = [m for m, u, _ in self.fake.requests if "renditions" in u]
        self.assertIn("POST", methods)

    def test_falls_back_when_generation_denied(self):
        self.fake.add(make_asset("a1"))
        self.fake.add(make_asset("a2", filename="b.jpg"))
        self.fake.deny_generation = True
        stats = self.sync(rendition="fullsize", concurrency=1)
        self.assertEqual(dict(stats.renditions), {"2048": 2})
        # After the first 403 no more generation requests are sent.
        posts = [u for m, u, _ in self.fake.requests if m == "POST" and "renditions" in u]
        self.assertEqual(len(posts), 1)

    def test_no_fallback_fails_asset(self):
        self.fake.add(make_asset("a1"))
        stats = self.sync(rendition="2560", rendition_fallback=False)
        self.assertEqual((stats.failed, stats.downloaded), (1, 0))

    def test_retries_server_errors(self):
        self.fake.add(make_asset("a1"))
        self.fake.fail_once["renditions/2048"] = 503
        self.assertEqual(self.sync().downloaded, 1)


class AlbumTest(Base):
    def setUp(self):
        super().setUp()
        self.fake.add(make_asset("a1", filename="one.jpg"))
        self.fake.add(make_asset("a2", filename="two.jpg"))
        self.fake.add(make_asset("a3", filename="three.jpg"))
        self.fake.albums = {
            "set": {"name": "Family", "assets": []},
            "kids": {"name": "Kids", "parent": "set", "assets": ["a1", "a2"]},
            "trip": {"name": "Trip", "assets": ["a2"]},
        }

    def test_album_sync_uses_album_paths(self):
        self.sync(source="albums", albums=["kids", "trip"],
                  folder_template="{albumpath}", filename_template="{name}")
        self.assertEqual(self.files(), ["Family/Kids/one.jpg", "Family/Kids/two.jpg",
                                        "Trip/two.jpg"])

    def test_unknown_album_aborts(self):
        with self.assertRaisesRegex(SyncError, "no longer exists"):
            self.sync(source="albums", albums=["gone"])


class ImagingTest(unittest.TestCase):
    def test_untouched_by_default(self):
        data = jpeg()
        self.assertIs(imaging.process(data, 0, 90, True), data)

    def test_downscales_long_edge(self):
        from PIL import Image
        out = imaging.process(jpeg(400, 200), 100, 85, True)
        self.assertEqual(Image.open(io.BytesIO(out)).size, (100, 50))

    def test_small_images_not_upscaled(self):
        data = jpeg(50, 40)
        self.assertIs(imaging.process(data, 100, 85, True), data)


if __name__ == "__main__":
    unittest.main()
