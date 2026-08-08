from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_release_bundle as release


class PrepareReleaseBundleTest(unittest.TestCase):
    def test_checksums_are_sorted_and_exclude_checksum_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "z.txt").write_text("z", encoding="utf-8")
            (root / "a.txt").write_text("a", encoding="utf-8")

            release.write_checksums(root)
            first = (root / "SHA256SUMS").read_text(encoding="utf-8")
            release.write_checksums(root)

            self.assertEqual(first, (root / "SHA256SUMS").read_text(encoding="utf-8"))
            self.assertEqual(["a.txt", "z.txt"], [line.split("  ")[1] for line in first.splitlines()])

    def test_existing_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "release"
            destination.mkdir()

            with self.assertRaisesRegex(release.ReleaseBundleError, "already exists"):
                release.build_bundle(
                    root / "missing.apk",
                    root / "ndk",
                    root / "apksigner",
                    destination,
                    "v1",
                    False,
                )

    def test_llvm_notice_must_be_unambiguous(self):
        with tempfile.TemporaryDirectory() as temporary:
            ndk = Path(temporary)
            notice = ndk / "toolchains/llvm/prebuilt/linux-x86_64/NOTICE"
            notice.parent.mkdir(parents=True)
            notice.write_text("notice", encoding="utf-8")

            self.assertEqual(notice, release.llvm_notice(ndk))

    def test_apk_must_embed_current_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            apk = Path(temporary) / "app.apk"
            commit = "a" * 40
            with zipfile.ZipFile(apk, "w") as bundle:
                bundle.writestr("classes.dex", b"prefix" + commit[:12].encode() + b"suffix")

            self.assertEqual(commit[:12], release.verify_embedded_commit(apk, commit))
            with self.assertRaisesRegex(release.ReleaseBundleError, "does not embed"):
                release.verify_embedded_commit(apk, "b" * 40)


if __name__ == "__main__":
    unittest.main()
