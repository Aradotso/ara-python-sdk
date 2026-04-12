from __future__ import annotations

from ara_sdk import App, Secret, runtime


def build_app(*, dotenv_file: str, local_openai_key: str) -> App:
    app = App(
        "sdk-secret-reconcile-probe",
        runtime_profile=runtime(
            secrets=[
                Secret.from_dotenv(filename=dotenv_file),
                Secret.from_dict({"OPENAI_API_KEY": local_openai_key}),
            ],
        ),
    )

    @app.agent(
        entrypoint=True,
    )
    def probe_agent(input: dict) -> str:
        return "Respond with a short probe confirmation."

    return app
