import importlib
import json
import os
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

buddy_routes = importlib.import_module("main.server.buddy_routes")
spa_routes = importlib.import_module("main.server.spa_routes")


class _Request:
    def __init__(self, body=None, query=None):
        self._body = body
        self.query = query or {}

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def enabled(value=True):
    return patch.object(
        buddy_routes.buddy_store, "get_enabled", new=AsyncMock(return_value=value),
    )


class PrefsRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_requires_auth(self):
        with (
            patch.object(buddy_routes, "get_user", return_value=None),
            patch.object(buddy_routes.gemini, "available", return_value=True),
        ):
            response = await buddy_routes.buddy_prefs_get(_Request())
        self.assertEqual(response.status, 401)

    async def test_get_404_when_gemini_not_configured(self):
        with patch.object(buddy_routes.gemini, "available", return_value=False):
            response = await buddy_routes.buddy_prefs_get(_Request())
        self.assertEqual(response.status, 404)

    async def test_get_returns_flag_default_off(self):
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            enabled(False),
        ):
            response = await buddy_routes.buddy_prefs_get(_Request())
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), {"enabled": False})

    async def test_post_sets_flag(self):
        set_enabled = AsyncMock(return_value=True)
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            patch.object(buddy_routes.buddy_store, "set_enabled", set_enabled),
        ):
            response = await buddy_routes.buddy_prefs_set(_Request({"enabled": True}))
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), {"ok": True, "enabled": True})
        set_enabled.assert_awaited_once_with(7, True)

    async def test_post_503_when_store_unavailable(self):
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            patch.object(
                buddy_routes.buddy_store, "set_enabled", new=AsyncMock(return_value=False),
            ),
        ):
            response = await buddy_routes.buddy_prefs_set(_Request({"enabled": True}))
        self.assertEqual(response.status, 503)


class ChatRouteTest(unittest.IsolatedAsyncioTestCase):
    def _enter(self, stack, *, context=None, gemini_reply="canned reply", history=None):
        """Enter the happy-path patches, returning the entered mocks."""
        data = (
            {"candidates": [{"content": {"parts": [{"text": gemini_reply}]}}]}
            if gemini_reply is not None else None
        )
        stack.enter_context(patch.object(buddy_routes, "get_user", return_value={"sub": 7}))
        stack.enter_context(patch.object(buddy_routes.gemini, "available", return_value=True))
        stack.enter_context(patch.object(
            buddy_routes.buddy_store, "get_enabled", new=AsyncMock(return_value=True),
        ))
        stack.enter_context(patch.object(
            buddy_routes.buddy_store, "consume_daily", new=AsyncMock(return_value=True),
        ))
        return {
            "resolve": stack.enter_context(patch.object(
                buddy_routes.buddy_context, "resolve_context",
                new=AsyncMock(return_value=context),
            )),
            "history": stack.enter_context(patch.object(
                buddy_routes.buddy_store, "get_history",
                new=AsyncMock(return_value=history or []),
            )),
            "prompt": stack.enter_context(patch.object(
                buddy_routes.buddy_context, "build_prompt",
                return_value=("sys rules", [{"role": "user", "parts": [{"text": "x"}]}]),
            )),
            "generate": stack.enter_context(patch.object(
                buddy_routes.gemini, "generate_content", new=AsyncMock(return_value=data),
            )),
            "append": stack.enter_context(patch.object(
                buddy_routes.buddy_store, "append_exchange", new=AsyncMock(return_value=True),
            )),
            "token": stack.enter_context(patch.object(buddy_routes, "_take_token", return_value=True)),
        }

    async def test_401_unauthenticated(self):
        with (
            patch.object(buddy_routes, "get_user", return_value=None),
            patch.object(buddy_routes.gemini, "available", return_value=True),
        ):
            response = await buddy_routes.buddy_chat(_Request({"message": "hi"}))
        self.assertEqual(response.status, 401)

    async def test_tool_call_loop_executes_and_feeds_back(self):
        """A functionCall turn is answered server-side; the final text lands."""
        with ExitStack() as stack:
            call_data = {"candidates": [{"content": {"parts": [
                {"functionCall": {"name": "where_was_i", "args": {}}},
            ]}}]}
            final_data = {"candidates": [{"content": {"parts": [{"text": "You're on S01E02."}]}}]}
            patches = self._enter(stack, gemini_reply=None)
            patches["generate"].side_effect = [call_data, final_data]
            with patch.object(buddy_routes.buddy_tools, "where_was_i",
                              new=AsyncMock(return_value={"inProgress": [], "recentlyFinished": []})):
                response = await buddy_routes.buddy_chat(_Request({"message": "where am I?"}))

            self.assertEqual(response.status, 200)
            body = json.loads(response.text)
            self.assertEqual(body["reply"], "You're on S01E02.")
            # Two generate calls: tool turn, then follow-up with the functionResponse.
            self.assertEqual(patches["generate"].await_count, 2)
            second_contents = patches["generate"].await_args_list[1].args[0] if patches["generate"].await_args_list[1].args else patches["generate"].await_args_list[1].kwargs["contents"]
            fr = second_contents[-1]["parts"][0]["functionResponse"]
            self.assertEqual(fr["name"], "where_was_i")

    async def test_tool_loop_is_capped_per_turn(self):
        """The loop never spins past max_calls_per_turn on a hostile model."""
        with ExitStack() as stack:
            call_data = {"candidates": [{"content": {"parts": [
                {"functionCall": {"name": "search_catalogue", "args": {"query": "x"}}},
            ]}}]}
            patches = self._enter(stack, gemini_reply=None)
            patches["generate"].side_effect = [call_data] * 99
            with patch.object(buddy_routes.buddy_tools, "search_catalogue", return_value={"results": []}):
                response = await buddy_routes.buddy_chat(_Request({"message": "search"}))

            self.assertEqual(patches["generate"].await_count, buddy_routes.buddy_tools.max_calls_per_turn() + 1)
            # No final text ever arrived → 502.
            self.assertEqual(response.status, 502)

    async def test_404_when_gemini_not_configured(self):
        with patch.object(buddy_routes.gemini, "available", return_value=False):
            response = await buddy_routes.buddy_chat(_Request({"message": "hi"}))
        self.assertEqual(response.status, 404)

    async def test_403_when_flag_off(self):
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            enabled(False),
        ):
            response = await buddy_routes.buddy_chat(_Request({"message": "hi"}))
        self.assertEqual(response.status, 403)

    async def test_429_after_burst_with_retry_after(self):
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            enabled(True),
            patch.object(
                buddy_routes.buddy_store, "consume_daily", new=AsyncMock(return_value=True),
            ),
            # Keep the drain hermetic: no key in the test env, so a real call
            # would 502 anyway — mock it to avoid any network attempt.
            patch.object(
                buddy_routes.gemini, "generate_content", new=AsyncMock(return_value=None),
            ),
        ):
            # Drain the 8-token burst; each attempt burns a token then 502s.
            for _ in range(8):
                response = await buddy_routes.buddy_chat(_Request({"message": "hi"}))
                self.assertEqual(response.status, 502)
            response = await buddy_routes.buddy_chat(_Request({"message": "hi"}))
        self.assertEqual(response.status, 429)
        self.assertEqual(response.headers.get("Retry-After"), "10")

    async def test_429_when_daily_quota_exceeded(self):
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            enabled(True),
            patch.object(
                buddy_routes.buddy_store, "consume_daily", new=AsyncMock(return_value=False),
            ) as quota,
            patch.object(buddy_routes, "_take_token") as take_token,
            patch.object(
                buddy_routes.gemini, "generate_content", new=AsyncMock(),
            ) as generate,
        ):
            response = await buddy_routes.buddy_chat(_Request({"message": "hi"}))
        self.assertEqual(response.status, 429)
        # Quota 429 tells the client when the UTC day rolls over.
        self.assertGreater(int(response.headers.get("Retry-After")), 0)
        quota.assert_awaited_once_with(7, buddy_routes.Var.BUDDY_DAILY_LIMIT)
        # The burst bucket and Gemini are untouched when the quota is spent.
        take_token.assert_not_called()
        generate.assert_not_awaited()

    async def test_flag_check_precedes_daily_quota(self):
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            enabled(False),
            patch.object(
                buddy_routes.buddy_store, "consume_daily", new=AsyncMock(return_value=False),
            ) as quota,
        ):
            response = await buddy_routes.buddy_chat(_Request({"message": "hi"}))
        self.assertEqual(response.status, 403)
        quota.assert_not_awaited()

    async def test_400_empty_and_overlong_message(self):
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            enabled(True),
            patch.object(
                buddy_routes.buddy_store, "consume_daily", new=AsyncMock(return_value=True),
            ),
            patch.object(buddy_routes, "_take_token", return_value=True),
        ):
            empty = await buddy_routes.buddy_chat(_Request({"message": "   "}))
            overlong = await buddy_routes.buddy_chat(_Request({"message": "x" * 2001}))
            garbage = await buddy_routes.buddy_chat(_Request(["not", "a", "dict"]))
        self.assertEqual(empty.status, 400)
        self.assertEqual(overlong.status, 400)
        self.assertEqual(garbage.status, 400)

    async def test_happy_path_chats_and_persists_pair(self):
        context = {
            "title": "Dark", "kind": "tv", "seriesTitle": "Dark",
            "season": 3, "episode": 5, "completed": False,
            "cutoffLabel": "S03E06", "_prompt": {"overview": "secret"},
        }
        with ExitStack() as stack:
            patches = self._enter(stack, context=context)
            response = await buddy_routes.buddy_chat(
                _Request({"message": "is Jonas okay?", "messageId": 105}),
            )
        self.assertEqual(response.status, 200)
        payload = json.loads(response.text)
        self.assertEqual(payload["reply"], "canned reply")
        # Prompt-only metadata is stripped from the echoed context.
        self.assertNotIn("_prompt", payload["context"])
        self.assertEqual(payload["context"]["cutoffLabel"], "S03E06")
        patches["append"].assert_awaited_once_with(
            7, "m:105", "is Jonas okay?", "canned reply",
        )
        generate = patches["generate"]
        self.assertEqual(generate.await_args.kwargs["model"],
                         buddy_routes.Var.GEMINI_BUDDY_MODEL)
        # Guardrails ride as a system instruction, and output is hard-capped.
        self.assertEqual(generate.await_args.kwargs["system_instruction"], "sys rules")
        self.assertEqual(generate.await_args.kwargs["max_output_tokens"], 400)
        self.assertEqual(generate.await_args.args[0],
                         [{"role": "user", "parts": [{"text": "x"}]}])

    async def test_502_on_gemini_failure_without_dangling_history(self):
        with ExitStack() as stack:
            patches = self._enter(stack, gemini_reply=None)
            response = await buddy_routes.buddy_chat(_Request({"message": "hi"}))
        self.assertEqual(response.status, 502)
        patches["append"].assert_not_awaited()

    async def test_general_chat_has_null_context(self):
        with ExitStack() as stack:
            patches = self._enter(stack, context=None)
            response = await buddy_routes.buddy_chat(_Request({"message": "hi"}))
        self.assertEqual(json.loads(response.text)["context"], None)
        patches["append"].assert_awaited_once_with(7, "general", "hi", "canned reply")


class HistoryRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_history_passthrough_and_session_key(self):
        messages = [
            {"role": "user", "text": "u", "t": 1},
            {"role": "buddy", "text": "b", "t": 1},
        ]
        get_history = AsyncMock(return_value=messages)
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            enabled(True),
            patch.object(buddy_routes.buddy_store, "get_history", get_history),
        ):
            response = await buddy_routes.buddy_history(
                _Request(query={"itemId": "movie:x", "messageId": "42"}),
            )
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), {"messages": messages})
        get_history.assert_awaited_once_with(7, "m:42")

    async def test_history_bare_is_general_session(self):
        get_history = AsyncMock(return_value=[])
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            enabled(True),
            patch.object(buddy_routes.buddy_store, "get_history", get_history),
        ):
            response = await buddy_routes.buddy_history(_Request())
        self.assertEqual(response.status, 200)
        get_history.assert_awaited_once_with(7, "general")

    async def test_history_403_when_flag_off(self):
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            enabled(False),
        ):
            response = await buddy_routes.buddy_history(_Request())
        self.assertEqual(response.status, 403)

    async def test_history_401_unauthenticated(self):
        with (
            patch.object(buddy_routes, "get_user", return_value=None),
            patch.object(buddy_routes.gemini, "available", return_value=True),
        ):
            response = await buddy_routes.buddy_history(_Request())
        self.assertEqual(response.status, 401)


class HistoryDeleteRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_delete_all_sessions_when_bare(self):
        delete = AsyncMock(return_value=True)
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            enabled(True),
            patch.object(buddy_routes.buddy_store, "delete_history", delete),
        ):
            response = await buddy_routes.buddy_history_delete(_Request())
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), {"ok": True})
        delete.assert_awaited_once_with(7)

    async def test_delete_single_session_with_item_reference(self):
        delete = AsyncMock(return_value=True)
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            enabled(True),
            patch.object(buddy_routes.buddy_store, "delete_history", delete),
        ):
            response = await buddy_routes.buddy_history_delete(
                _Request(query={"itemId": "movie:x", "messageId": "42"}),
            )
        self.assertEqual(response.status, 200)
        delete.assert_awaited_once_with(7, "m:42")

    async def test_delete_gates(self):
        # 401 unauthenticated
        with (
            patch.object(buddy_routes, "get_user", return_value=None),
            patch.object(buddy_routes.gemini, "available", return_value=True),
        ):
            self.assertEqual((await buddy_routes.buddy_history_delete(_Request())).status, 401)
        # 404 when Gemini not configured
        with patch.object(buddy_routes.gemini, "available", return_value=False):
            self.assertEqual((await buddy_routes.buddy_history_delete(_Request())).status, 404)
        # 403 when flag off
        with (
            patch.object(buddy_routes, "get_user", return_value={"sub": 7}),
            patch.object(buddy_routes.gemini, "available", return_value=True),
            enabled(False),
        ):
            self.assertEqual((await buddy_routes.buddy_history_delete(_Request())).status, 403)


class _FakeGeminiResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}


class _FakeGeminiSession:
    def __init__(self):
        self.payloads = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, url, json=None, timeout=None):
        self.payloads.append(json)
        return _FakeGeminiResponse()


class GeminiPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_system_instruction_and_output_cap_reach_the_payload(self):
        from main.utils import gemini as gemini_mod

        session = _FakeGeminiSession()
        with (
            patch.object(gemini_mod.Var, "GEMINI_API_KEY", "key"),
            patch.object(gemini_mod.aiohttp, "ClientSession", return_value=session),
        ):
            result = await gemini_mod.generate_content(
                [{"role": "user", "parts": [{"text": "hi"}]}],
                system_instruction="RULES",
                max_output_tokens=400,
            )
        self.assertIsNotNone(result)
        payload = session.payloads[0]
        self.assertEqual(payload["systemInstruction"], {"parts": [{"text": "RULES"}]})
        self.assertEqual(payload["generationConfig"], {"maxOutputTokens": 400})

    async def test_defaults_omit_guardrail_keys(self):
        from main.utils import gemini as gemini_mod

        session = _FakeGeminiSession()
        with (
            patch.object(gemini_mod.Var, "GEMINI_API_KEY", "key"),
            patch.object(gemini_mod.aiohttp, "ClientSession", return_value=session),
        ):
            result = await gemini_mod.generate_content(
                [{"role": "user", "parts": [{"text": "hi"}]}],
            )
        self.assertIsNotNone(result)
        payload = session.payloads[0]
        self.assertNotIn("systemInstruction", payload)
        self.assertNotIn("generationConfig", payload)


class ApiMeBuddyFlagTest(unittest.IsolatedAsyncioTestCase):
    async def test_buddy_true_when_signed_in_configured_and_enabled(self):
        with (
            patch.object(spa_routes, "get_user", return_value={"sub": 7}),
            patch.object(spa_routes.Var, "GEMINI_API_KEY", "key"),
            patch.object(
                spa_routes.buddy_store, "get_enabled", new=AsyncMock(return_value=True),
            ),
        ):
            response = await spa_routes.api_me(object())
        self.assertTrue(json.loads(response.text)["buddy"])

    async def test_buddy_false_when_store_unavailable(self):
        with (
            patch.object(spa_routes, "get_user", return_value={"sub": 7}),
            patch.object(spa_routes.Var, "GEMINI_API_KEY", "key"),
            patch.object(
                spa_routes.buddy_store, "get_enabled",
                new=AsyncMock(side_effect=Exception("mongo down")),
            ),
        ):
            response = await spa_routes.api_me(object())
        self.assertEqual(response.status, 200)
        self.assertFalse(json.loads(response.text)["buddy"])

    async def test_buddy_false_when_signed_out_or_unconfigured(self):
        with (
            patch.object(spa_routes, "get_user", return_value=None),
            patch.object(spa_routes.Var, "GEMINI_API_KEY", "key"),
        ):
            response = await spa_routes.api_me(object())
        self.assertFalse(json.loads(response.text)["buddy"])
        with (
            patch.object(spa_routes, "get_user", return_value={"sub": 7}),
            patch.object(spa_routes.Var, "GEMINI_API_KEY", ""),
        ):
            response = await spa_routes.api_me(object())
        self.assertFalse(json.loads(response.text)["buddy"])


if __name__ == "__main__":
    unittest.main()
