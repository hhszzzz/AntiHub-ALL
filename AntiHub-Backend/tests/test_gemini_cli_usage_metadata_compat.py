import unittest

from app.services.gemini_cli_api_service import (
    _OpenAIStreamState,
    _gemini_cli_event_to_openai_chunks,
    _gemini_cli_response_to_openai_response,
)


class TestGeminiCLIUsageMetadataCompat(unittest.TestCase):
    def test_cpa_usage_metadata_is_supported_in_stream(self) -> None:
        event = {
            "response": {
                "responseId": "r1",
                "modelVersion": "gemini-test",
                "createTime": "2026-01-31T00:00:00Z",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": "hi"}]},
                    }
                ],
                # upstream 可能会把 usageMetadata 改名为 cpaUsageMetadata
                "cpaUsageMetadata": {
                    "promptTokenCount": 10,
                    "cachedContentTokenCount": 1,
                    "candidatesTokenCount": 5,
                    "totalTokenCount": 16,
                    "thoughtsTokenCount": 2,
                },
            }
        }
        state = _OpenAIStreamState(created=0, function_index=0)
        chunks = _gemini_cli_event_to_openai_chunks(event, state=state)
        self.assertTrue(chunks)

        usage = chunks[0].get("usage") or {}
        self.assertEqual(usage.get("prompt_tokens"), 11)  # (10-1)+2
        self.assertEqual(usage.get("completion_tokens"), 5)
        self.assertEqual(usage.get("total_tokens"), 16)
        self.assertEqual(
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"), 2
        )

    def test_cached_content_token_count_is_subtracted_in_non_stream(self) -> None:
        raw = {
            "response": {
                "responseId": "r2",
                "modelVersion": "gemini-test",
                "createTime": "2026-01-31T00:00:00Z",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": "ok"}]},
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "cachedContentTokenCount": 1,
                    "candidatesTokenCount": 5,
                    "totalTokenCount": 16,
                    "thoughtsTokenCount": 2,
                },
            }
        }
        out = _gemini_cli_response_to_openai_response(raw)
        usage = out.get("usage") or {}
        self.assertEqual(usage.get("prompt_tokens"), 11)  # (10-1)+2
        self.assertEqual(usage.get("completion_tokens"), 5)
        self.assertEqual(usage.get("total_tokens"), 16)
        self.assertEqual(
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"), 2
        )


if __name__ == "__main__":
    unittest.main()

