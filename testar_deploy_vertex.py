"""
Testa o agente publicado no Vertex AI Agent Engine, enviando um documento real.

Uso:
    python testar_deploy_vertex.py caminho/do/documento.pdf
    python testar_deploy_vertex.py caminho/do/documento.docx
"""

import base64
import mimetypes
import sys
from pathlib import Path

import vertexai
from vertexai import agent_engines

RESOURCE_NAME = "projects/658891422743/locations/us-central1/reasoningEngines/416039257031835648"
PROJECT_ID = "steady-computer-487217-p6"
LOCATION = "us-central1"
STAGING_BUCKET = "gs://agente-processos-staging"


def main() -> None:
    if len(sys.argv) != 2:
        print("Uso: python testar_deploy_vertex.py <caminho_do_documento>")
        sys.exit(1)

    doc_path = Path(sys.argv[1])
    if not doc_path.exists():
        print(f"Arquivo nao encontrado: {doc_path}")
        sys.exit(1)

    mime_type, _ = mimetypes.guess_type(doc_path)
    mime_type = mime_type or "application/octet-stream"

    vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)
    agent = agent_engines.get(RESOURCE_NAME)

    session = agent.create_session(user_id="teste-manual")
    print(f"Sessao criada: {session['id']}")
    print(f"Enviando '{doc_path.name}' ({mime_type})...\n")

    doc_b64 = base64.b64encode(doc_path.read_bytes()).decode("ascii")

    for event in agent.stream_query(
        user_id="teste-manual",
        session_id=session["id"],
        message={
            "parts": [
                {"text": "Segue o documento de processo para análise."},
                {"inline_data": {"mime_type": mime_type, "data": doc_b64}},
            ]
        },
    ):
        content = event.get("content", {})
        for part in content.get("parts", []):
            if "text" in part:
                print(part["text"])
            elif "function_call" in part:
                print(f"[tool call] {part['function_call'].get('name')}")
            elif "function_response" in part:
                resp = part["function_response"]
                print(f"[tool result] {resp.get('name')}: {resp.get('response')}")
            else:
                print(f"[evento nao reconhecido] {part}")


if __name__ == "__main__":
    main()
