"""The status credential can only publish the fixed context on an exact client SHA."""

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch
from urllib.error import URLError

from scripts import publish_platform_status as status


class PlatformStatusTests(unittest.TestCase):
    def setUp(self):
        self.arguments = dict(
            repository="example/client",
            sha="a" * 40,
            state="pending",
            run_id="123",
            run_attempt="2",
            token="synthetic-token",
        )

    def test_exact_client_sha_context_and_all_states(self):
        for state in status.DESCRIPTIONS:
            opener = Mock(return_value=io.StringIO("{}"))
            with self.subTest(state=state), redirect_stdout(io.StringIO()):
                self.assertTrue(
                    status.publish_status(**{**self.arguments, "state": state}, opener=opener)
                )
            request = opener.call_args.args[0]
            self.assertEqual(
                request.full_url, "https://api.github.com/repos/example/client/statuses/" + "a" * 40
            )
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(
                json.loads(request.data),
                {
                    "context": "Platform Compatibility",
                    "description": status.DESCRIPTIONS[state],
                    "state": state,
                    "target_url": "https://github.com/example/client/actions/runs/123/attempts/2",
                },
            )
            self.assertEqual(opener.call_args.kwargs, {"timeout": 20})
            self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-token")

    def test_invalid_inputs_cannot_send_credentials(self):
        for field, value in (
            ("repository", "example/client/extra"),
            ("sha", "main"),
            ("sha", "a" * 41),
            ("state", "neutral"),
            ("run_id", "0"),
            ("run_attempt", "../3"),
            ("token", ""),
            ("server_url", "https://example.com"),
            ("api_url", "https://example.com"),
        ):
            opener = Mock()
            with self.subTest(field=field), self.assertRaises(status.StatusError):
                status.publish_status(**{**self.arguments, field: value}, opener=opener)
            opener.assert_not_called()
        with self.assertRaises(status.StatusError) as raised:
            status.publish_status(
                **self.arguments, opener=Mock(side_effect=URLError("private-payload"))
            )
        self.assertNotIn("private-payload", str(raised.exception))

    def test_cli_targets_calling_repository(self):
        with (
            patch.dict(
                "os.environ",
                {"GITHUB_REPOSITORY": "example/client", "PLATFORM_STATUS_TOKEN": "synthetic-token"},
                clear=True,
            ),
            patch.object(status, "publish_status") as publish,
        ):
            self.assertEqual(
                status.main(
                    [
                        "--sha",
                        "a" * 40,
                        "--state",
                        "failure",
                        "--run-id",
                        "123",
                        "--run-attempt",
                        "2",
                    ]
                ),
                0,
            )
            self.assertEqual(publish.call_args.kwargs["repository"], "example/client")
            self.assertEqual(publish.call_args.kwargs["state"], "failure")


if __name__ == "__main__":
    unittest.main()
