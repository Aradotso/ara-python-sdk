import ara_sdk as ara

# HOW TO RUN (minimal):
# 1) ara auth login
# 2) ara deploy examples/05-entrypoint-install.py
# 3) ara run examples/05-entrypoint-install.py
#
# This script uses entrypoint to install a package before tools run.


@ara.tool
def requests_version() -> dict:
    import requests

    return {"ok": True, "requests_version": requests.__version__}


ara.Automation(
    "entrypoint-agent",
    system_instructions="Use requests_version tool and reply with the installed version.",
    tools=[requests_version],
    entrypoint="./05-entrypoint.sh",
)
