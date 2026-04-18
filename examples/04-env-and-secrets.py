import ara_sdk as ara

# HOW TO RUN (minimal):
# 1) export CRON_EMAIL_FROM="alerts@yourdomain.com"
# 2) ara auth login
# 3) ara deploy examples/04-env-and-secrets.py
# 4) ara run examples/04-env-and-secrets.py
#
# Notes:
# - ara.env("KEY", default="...") is optional config.
# - ara.secret("KEY") is required secret; deploy/runtime fails if missing.


@ara.tool
def read_runtime_config() -> dict:
    region = ara.env("APP_REGION", default="us-east-1")
    sender = ara.secret("CRON_EMAIL_FROM")
    return {
        "ok": True,
        "region": region,
        "from_address": sender,
    }


ara.Automation(
    "env-secrets-agent",
    system_instructions="Use read_runtime_config and explain values in one short reply.",
    tools=[read_runtime_config],
)
