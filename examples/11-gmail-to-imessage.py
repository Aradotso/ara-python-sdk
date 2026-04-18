import ara_sdk as ara

# HOW TO RUN:
# 1) ara auth login
# 2) Connect Gmail at app.ara.so/connect
# 3) ara deploy examples/11-gmail-to-imessage.py
# 4) ara run examples/11-gmail-to-imessage.py
# 5) Check your iMessage — the latest email summary will arrive instantly.

ara.Automation(
    "gmail-to-imessage",
    system_instructions=(
        "1. Use gmail_search_emails to find the single most recent email in the inbox. "
        "2. Summarize it in 2-3 sentences (sender, subject, key point). "
        "3. Send that summary to the user via linq_send_message. "
        "Do not ask questions — just do it."
    ),
)
