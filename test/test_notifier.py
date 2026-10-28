from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import requests

from dualis_client import DualisError
from dualis_notifier import main, run_once
from notifications import deliver_pending
from storage import StateError, StateStore, run_lock
from test_client import response

HTML = (Path(__file__).parent / "fixtures" / "grades.html").read_text()


class NotifierTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = {"user": "s123456", "passwd": "synthetic", "semester_id": "",
                       "hook_url": "https://example.invalid/synthetic", "user_agent": "Tests",
                       "data_dir": Path(temporary.name), "timeout": (1, 2), "interval": 900}
        self.store = StateStore(self.config["data_dir"], "s123456", "")
        self.client_patch = patch("dualis_notifier.DualisClient")
        self.client = self.client_patch.start().return_value.__enter__.return_value
        self.addCleanup(self.client_patch.stop)
        self.client.get_grades.return_value = HTML

    def baseline(self):
        with patch("notifications.requests.post") as post:
            self.assertTrue(run_once(self.config))
            post.assert_not_called()

    def test_failed_webhook_is_retried_after_restart(self):
        for failure in (response(429, {"retry_after": 120}), response(500),
                        requests.ConnectionError("synthetic")):
            with self.subTest(failure=type(failure).__name__):
                self.store.path.unlink(missing_ok=True)
                self.client.get_grades.return_value = HTML
                self.baseline()
                self.client.get_grades.return_value = HTML.replace("1,7", "1,3")
                kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch("notifications.time.time", return_value=1000), patch("notifications.requests.post", **kwargs) as post:
                    self.assertFalse(run_once(self.config))
                    self.assertEqual(post.call_count, 1)
                grades, pending = self.store.load()
                self.assertEqual(grades.loc[0, "Endnote"], "1,3")
                self.assertEqual(len(pending), 1)
                with patch("notifications.time.time", return_value=2000), patch("notifications.requests.post", return_value=response()) as post:
                    self.assertTrue(run_once(self.config))
                    self.assertEqual(post.call_count, 1)
                self.assertEqual(self.store.load()[1], [])

    def test_partial_batch_only_retries_unacknowledged_events(self):
        self.baseline()
        self.client.get_grades.return_value = HTML.replace("1,7", "1,3").replace("<td></td><td>5", "<td>2,0</td><td>5")
        with patch("notifications.time.time", return_value=1000), patch("notifications.requests.post", side_effect=[response(), response(500)]) as post:
            self.assertFalse(run_once(self.config))
            self.assertEqual(post.call_count, 2)
        self.assertEqual([event["name"] for event in self.store.load()[1]], ["Algorithmen"])
        with patch("notifications.time.time", return_value=2000), patch("notifications.requests.post", return_value=response()) as post:
            self.assertTrue(run_once(self.config))
            self.assertEqual(post.call_count, 1)
            self.assertIn("Algorithmen", post.call_args.kwargs["json"]["embeds"][0]["title"])

    def test_rate_limit_is_respected_without_sleep_or_extra_attempt(self):
        self.baseline()
        self.client.get_grades.return_value = HTML.replace("1,7", "1,3")
        with patch("notifications.time.time", return_value=1000), patch("notifications.requests.post", return_value=response(429, {"retry_after": 1800})):
            self.assertFalse(run_once(self.config))
        with patch("notifications.time.time", return_value=2000), patch("notifications.requests.post") as post:
            self.assertFalse(run_once(self.config))
            post.assert_not_called()
        self.assertEqual(len(self.store.load()[1]), 1)

    def test_pending_send_still_runs_when_dualis_is_down(self):
        self.baseline()
        grades, _ = self.store.load()
        pending = [{"id": "one", "name": "Mathematik", "type": "changed", "attempts": 0, "next_attempt_at": 0}]
        self.store.save(grades, pending)
        self.client.login.side_effect = DualisError("synthetic")
        with patch("notifications.requests.post", return_value=response()) as post:
            with self.assertRaises(DualisError):
                run_once(self.config)
            self.assertEqual(post.call_count, 1)
        self.assertEqual(self.store.load()[1], [])

    def test_invalid_html_keeps_previous_state(self):
        self.baseline()
        before = self.store.path.read_bytes()
        self.client.get_grades.return_value = "<html>Anmeldung</html>"
        with self.assertRaises(ValueError):
            run_once(self.config)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_ack_write_failure_leaves_event_for_at_least_once_delivery(self):
        self.baseline()
        grades, _ = self.store.load()
        pending = [{"id": "one", "name": "Mathematik", "type": "changed", "attempts": 0, "next_attempt_at": 0}]
        self.store.save(grades, pending)
        with patch("notifications.notify_server"), patch.object(self.store, "save", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                deliver_pending(self.store, grades, pending, "synthetic")
        self.assertEqual(len(self.store.load()[1]), 1)

    def test_existing_csv_can_be_imported_once(self):
        legacy = self.config["data_dir"] / "legacy.csv"
        pd.DataFrame([{"Nr.": "M-101", "Name": "Mathematik", "Endnote": "1,7",
                       "Credits": "5", "Status": "bestanden"},
                      {"Nr.": "M-102", "Name": "Algorithmen", "Endnote": "",
                       "Credits": "5", "Status": "offen"}]).to_csv(legacy)
        with patch("notifications.requests.post") as post:
            self.assertTrue(run_once(self.config, import_cache=legacy))
            post.assert_not_called()
        with self.assertRaises(StateError):
            run_once(self.config, import_cache=legacy)
        self.assertTrue(legacy.exists())

    def test_concurrent_run_skips_network(self):
        with run_lock(self.store.lock_path):
            self.assertTrue(run_once(self.config))
        self.client.login.assert_not_called()

    def test_cli_errors_have_nonzero_exit_code(self):
        with patch("dualis_notifier.load_config", return_value=self.config), patch("dualis_notifier.logging.basicConfig"):
            self.client.login.side_effect = DualisError("synthetic failure")
            self.assertEqual(main([]), 1)

    def test_watch_continues_after_transient_error_then_handles_sigterm(self):
        handlers = {}
        attempts = []
        def check(*args, **kwargs):
            attempts.append(kwargs.get("import_cache"))
            if len(attempts) == 1:
                raise DualisError("synthetic transient failure")
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            return True
        with patch("dualis_notifier.load_config", return_value={**self.config, "interval": 0.001}), \
             patch("dualis_notifier.logging.basicConfig"), \
             patch("dualis_notifier.signal.signal", side_effect=lambda number, handler: handlers.update({number: handler})), \
             patch("dualis_notifier.run_once", side_effect=check):
            self.assertEqual(main(["--watch"]), 0)
        self.assertEqual(attempts, [None, None])

    def test_import_cannot_be_silently_skipped_by_another_cli_mode(self):
        for mode in ("--watch", "--status"):
            with self.subTest(mode=mode), patch("sys.stderr"), self.assertRaises(SystemExit) as result:
                main([mode, "--import-cache", "legacy.csv"])
            self.assertEqual(result.exception.code, 2)

    def test_status_does_not_contact_external_services(self):
        with patch("dualis_notifier.load_config", return_value=self.config), \
             patch("dualis_notifier.logging.basicConfig"), patch("builtins.print") as output:
            self.assertEqual(main(["--status"]), 0)
        self.client.login.assert_not_called()
        self.assertIn("Ausstehend: 0", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
