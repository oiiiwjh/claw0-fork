"""OpenAI variant of s07_heartbeat_cron.py."""

from _openai_bootstrap import run_original_session


if __name__ == "__main__":
    run_original_session(__file__, "s07_heartbeat_cron.py")
