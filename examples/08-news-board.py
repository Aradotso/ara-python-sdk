import ara_sdk as ara

# HOW TO RUN (minimal):
# 1) export EXA_API_KEY="..."
# 2) ara auth login
# 3) ara deploy examples/08-news-board.py
# 4) ara run examples/08-news-board.py

SYSTEM_INSTRUCTIONS = "Call refresh_news_board once each run and respond with the saved file path and top headline."


@ara.tool
def refresh_news_board() -> dict:
    from pathlib import Path
    import subprocess
    import sys

    key = ara.secret("EXA_API_KEY")
    topic = ara.env("DIGEST_TOPIC", default="AI agents and developer tools")

    try:
        from exa_py import Exa
    except ImportError:
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "--break-system-packages", "exa-py"],
                check=True,
            )
            from exa_py import Exa
        except (subprocess.CalledProcessError, ImportError) as exc:
            return {"ok": False, "error": "missing_exa_py", "details": str(exc)}

    exa = Exa(key)
    try:
        data = exa.search(f"latest updates about {topic}", type="auto", num_results=3)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "exa_search_failed", "details": str(exc)}

    results = data.results if hasattr(data, "results") and isinstance(data.results, list) else []
    lines = [f"# {topic}", ""]
    for i, row in enumerate(results[:3], start=1):
        title = str(getattr(row, "title", "") or "Untitled").strip()
        url = str(getattr(row, "url", "") or "").strip()
        lines.append(f"{i}. {title} - {url}" if url else f"{i}. {title}")

    if len(lines) == 2:
        lines.append("No results returned.")

    markdown = "\n".join(lines)
    news_path = Path("/tmp/news-board.md")
    news_path.write_text(markdown)
    return {"ok": True, "path": str(news_path), "preview": markdown}


app = ara.Job(
    "news-board-agent",
    system_instructions=SYSTEM_INSTRUCTIONS,
)
