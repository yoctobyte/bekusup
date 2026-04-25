import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from bekusup import flyover


class TestFlyover(unittest.TestCase):
    def test_parse_ssh_uri_with_user_port(self):
        info = flyover.parse_ssh_uri("ssh://rene@example.test:2222")

        self.assertEqual(info["host"], "example.test")
        self.assertEqual(info["port"], "2222")
        self.assertEqual(info["remote"], "rene@example.test")
        self.assertIsNone(info["password"])

    def test_parse_ssh_uri_with_password(self):
        info = flyover.parse_ssh_uri("ssh://rene:secret@example.test")

        self.assertEqual(info["host"], "example.test")
        self.assertEqual(info["port"], "22")
        self.assertEqual(info["remote"], "rene@example.test")
        self.assertEqual(info["password"], "secret")

    def test_local_path_size_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(b"abc")
            path = handle.name
        try:
            size, error = flyover.local_path_size(path)
        finally:
            os.remove(path)

        self.assertEqual(size, 3)
        self.assertIsNone(error)

    @patch("bekusup.flyover.run_timed")
    def test_password_uri_skips_ssh_command_probe(self, mock_run_timed):
        host = MagicMock()
        host.uri = "ssh://user:pass@example.test"
        mock_run_timed.return_value = (MagicMock(returncode=0), 0.1, None)

        results, info = flyover.check_ssh_host(host)

        self.assertEqual(info["password"], "pass")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[1].label, "ssh-auth")
        self.assertFalse(results[1].ok)


if __name__ == "__main__":
    unittest.main()
