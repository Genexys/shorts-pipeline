"""What each Claude call costs, and the per-video total the worker stores."""

import pytest

import writer


class _Usage:
    def __init__(self, input_tokens, output_tokens, cache_read=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read


@pytest.fixture(autouse=True)
def fresh_total():
    writer.reset_spend()
    yield
    writer.reset_spend()


def test_price_of_uses_the_model_rates():
    # Opus 5.5: $4 in, $20 out per million.
    assert writer.price_of("claude-opus-5-5", 1_000_000, 0) == pytest.approx(4.0)
    assert writer.price_of("claude-opus-5-5", 0, 1_000_000) == pytest.approx(20.0)
    # Sonnet 5.5: $2 / $10.
    assert writer.price_of("claude-sonnet-5-5", 2_000, 1_000) == pytest.approx(0.014)


def test_a_dated_model_id_takes_its_family_price():
    assert writer.price_of("claude-haiku-4-5-20251001", 1_000_000, 0) == pytest.approx(1.0)


def test_opus_5_is_not_priced_as_opus_5_5():
    # The longer name must win: claude-opus-5-5 starts with claude-opus-5.
    assert writer.price_of("claude-opus-5", 1_000_000, 0) == pytest.approx(5.0)


def test_an_unknown_model_has_no_price():
    assert writer.price_of("claude-something-new", 1000, 1000) is None


def test_record_usage_adds_up_until_reset():
    writer.record_usage("claude-opus-5-5", _Usage(10_000, 2_000))
    writer.record_usage("claude-sonnet-5-5", _Usage(5_000, 1_000, cache_read=5_000))

    # 10k*4 + 2k*20 = 80k micro-dollars; (5k+5k)*2 + 1k*10 = 30k.
    assert writer.spent() == pytest.approx(0.11)
    writer.reset_spend()
    assert writer.spent() == 0.0


def test_record_usage_never_raises_on_a_missing_usage():
    writer.record_usage("claude-opus-5-5", None)
    writer.record_usage("claude-something-new", _Usage(100, 100))
    assert writer.spent() == 0.0


def test_a_call_is_billed_even_when_its_text_is_unusable(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")

    class _Response:
        stop_reason = "refusal"
        content = []
        model = "claude-opus-5-5"
        usage = _Usage(1_000_000, 0)

    class _Messages:
        def create(self, **kwargs):
            return _Response()

    class _Beta:
        messages = _Messages()

    class _Client:
        beta = _Beta()

    monkeypatch.setattr(writer, "_client", lambda max_retries: _Client())

    written, kind = writer._attempt("claude-opus-5-5", "prompt", max_retries=0)

    assert written is None and kind == writer.REFUSED
    assert writer.spent() == pytest.approx(4.0)


def test_check_env_vars_requires_the_claude_key(monkeypatch):
    import utils

    monkeypatch.setenv("PEXELS_API_KEY", "p")
    monkeypatch.setenv("TIKTOK_SESSION_ID", "t")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(SystemExit):
        utils.check_env_vars()
