from __future__ import annotations

from ara_sdk import App, Secret, runtime


def build_app(*, dotenv_file: str, local_openai_key: str) -> App:
    app = App(
        "Programmatic Secrets Redeploy Probe",
        project_name="sdk-secret-reconcile-probe",
        runtime_profile=runtime(
            secrets=[
                Secret.from_dotenv(filename=dotenv_file),
                Secret.from_dict({"OPENAI_API_KEY": local_openai_key}),
            ],
        ),
    )

    @app.agent(
        id="probe-agent",
        entrypoint=True,
        task="Respond with a short probe confirmation.",
    )
    def probe_agent():
        """Probe entrypoint for redeploy validation."""

    return app
