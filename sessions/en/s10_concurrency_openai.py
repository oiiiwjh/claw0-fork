"""OpenAI variant of s10_concurrency.py."""

from _openai_bootstrap import run_original_session


if __name__ == "__main__":
    run_original_session(__file__, "s10_concurrency.py")
