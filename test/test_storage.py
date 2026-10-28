import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from storage import StateError, StateStore, atomic_write, run_lock


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = StateStore(Path(self.temporary.name), "s123456", "")
        self.grades = pd.DataFrame([{"Name": "Mathematik", "Endnote": "1,7"}])
        self.pending = [{"id": "event-1", "type": "changed", "name": "Mathematik",
                         "attempts": 0, "next_attempt_at": 0}]

    def test_restart_retains_grades_and_pending_together(self):
        self.store.save(self.grades, self.pending)
        restarted = StateStore(Path(self.temporary.name), "s123456", "")
        grades, pending = restarted.load()
        pd.testing.assert_frame_equal(grades, self.grades)
        self.assertEqual(pending, self.pending)

    def test_failed_replace_preserves_old_state(self):
        self.store.save(self.grades, [])
        before = self.store.path.read_bytes()
        with patch("storage.os.replace", side_effect=OSError("synthetic full disk")):
            with self.assertRaises(OSError):
                self.store.save(self.grades.assign(Endnote="1,3"), self.pending)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.store.directory.iterdir()), [self.store.path])

    def test_corrupt_state_is_never_silently_reset(self):
        atomic_write(self.store.path, "{broken")
        with self.assertRaises(StateError):
            self.store.load()
        self.assertEqual(self.store.path.read_text(), "{broken")

    def test_account_and_semester_have_separate_state(self):
        other_user = StateStore(Path(self.temporary.name), "s654321", "")
        other_semester = StateStore(Path(self.temporary.name), "s123456", "-N123")
        self.assertEqual(len({self.store.path, other_user.path, other_semester.path}), 3)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_state_permissions_are_private(self):
        self.store.save(self.grades, self.pending)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_lock_excludes_another_process_and_releases(self):
        code = (
            "from pathlib import Path; from storage import run_lock, AlreadyRunning\n"
            "import sys\n"
            "try:\n"
            "    with run_lock(Path(sys.argv[1])): pass\n"
            "except AlreadyRunning: sys.exit(7)\n"
        )
        with run_lock(self.store.lock_path):
            result = subprocess.run([sys.executable, "-c", code, str(self.store.lock_path)], timeout=10)
            self.assertEqual(result.returncode, 7)
        result = subprocess.run([sys.executable, "-c", code, str(self.store.lock_path)], timeout=10)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
