import ara_sdk as ara

# HOW TO RUN (minimal):
# 1) export EXA_API_KEY="..."
# 2) ara auth login
# 3) ara deploy examples/09-collab-studio.py
# 4) ara run examples/09-collab-studio.py

SYSTEM_INSTRUCTIONS = "You have three specialist collaborators: scout_headlines, write_digest, save_visual. Always run them in that order and return the final saved path."


@ara.tool
def scout_headlines() -> dict:
    from pathlib import Path
    import subprocess
    import sys

    key = ara.secret("EXA_API_KEY")
    topic = ara.env("DIGEST_TOPIC", default="AI product launches this week")

    try:
        from exa_py import Exa
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--break-system-packages", "exa-py"],
            check=True,
        )
        from exa_py import Exa

    exa = Exa(key)
    data = exa.search(f"latest updates about {topic}", type="auto", num_results=4)
    bullets = [f"- {row.title} ({row.url})" for row in data.results[:4]]
    scout_path = Path("/tmp/collab-scout.txt")
    scout_path.write_text("\n".join(bullets))
    return {"ok": True, "topic": topic, "path": str(scout_path)}


@ara.tool
def write_digest() -> dict:
    from pathlib import Path

    scout_path = Path("/tmp/collab-scout.txt")
    bullets = scout_path.read_text().strip()
    markdown = "# Team Digest\n\n## Scout Notes\n" + bullets
    markdown += "\n\n## Writer Draft\nHere are the top updates the team should read today."
    digest_path = Path("/tmp/collab-digest.md")
    digest_path.write_text(markdown)
    return {"ok": True, "path": str(digest_path)}


@ara.tool
def save_visual() -> dict:
    import html as html_lib
    from pathlib import Path

    digest_path = Path("/tmp/collab-digest.md")
    markdown = digest_path.read_text()
    html = (
        "<html><body style='font-family:Inter,sans-serif;max-width:900px;margin:40px auto;'>"
        "<h1>Collab Studio Output</h1>"
        f"<pre style='white-space:pre-wrap;background:#f5f5f5;padding:16px;border-radius:12px;'>{html_lib.escape(markdown)}</pre>"
        "</body></html>"
    )
    html_path = Path("/tmp/collab-board.html")
    html_path.write_text(html)
    return {"ok": True, "path": str(html_path)}


app = ara.Job(
    "collab-studio-agent",
    system_instructions=SYSTEM_INSTRUCTIONS,
)
