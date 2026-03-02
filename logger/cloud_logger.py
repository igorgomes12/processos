"""
logger/cloud_logger.py
----------------------
Implementação de ProcessLogger que envia eventos para o Google Cloud Logging
(Cloud Logging / Stackdriver).

Autenticação:
  - Usa o arquivo credentials.json da raiz do projeto (service account)
    via variável de ambiente GOOGLE_APPLICATION_CREDENTIALS.
  - Se a variável já estiver definida no ambiente, essa definição prevalece.

Configuração (via variáveis de ambiente):
  GCP_PROJECT_ID  — ID do projeto GCP (padrão: steady-computer-487217-p6)
  GCP_LOG_NAME    — Nome do log no Cloud Logging (padrão: agente-processos)
"""

from __future__ import annotations

import os
from pathlib import Path

from logger.base import LogEvent, ProcessLogger

# Garante que as credenciais do service account são encontradas
# mesmo que GOOGLE_APPLICATION_CREDENTIALS não esteja definida no ambiente.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_CREDENTIALS_PATH = _REPO_ROOT / "credentials.json"

if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and _CREDENTIALS_PATH.exists():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_CREDENTIALS_PATH)

_DEFAULT_PROJECT = "steady-computer-487217-p6"
_DEFAULT_LOG_NAME = "agente-processos"

# Mapeamento event_type → severidade Cloud Logging
_SEVERITY_MAP = {
    "tool_start": "INFO",
    "tool_end": "INFO",
    "tool_error": "ERROR",
}


class CloudLogger(ProcessLogger):
    """
    Logger que publica eventos estruturados no Google Cloud Logging.

    O cliente `google.cloud.logging` é criado de forma lazy (na primeira
    chamada de log) para não atrasar a inicialização do sistema se o
    Cloud Logging não estiver disponível.

    Parâmetros
    ----------
    project_id : str, opcional
        ID do projeto GCP. Lido de GCP_PROJECT_ID ou usa o padrão.
    log_name : str, opcional
        Nome do log no Cloud Logging. Lido de GCP_LOG_NAME ou usa o padrão.
    """

    def __init__(
        self,
        project_id: str | None = None,
        log_name: str | None = None,
    ) -> None:
        self._project_id = project_id or os.getenv("GCP_PROJECT_ID", _DEFAULT_PROJECT)
        self._log_name = log_name or os.getenv("GCP_LOG_NAME", _DEFAULT_LOG_NAME)
        self._cloud_logger = None  # criado de forma lazy

    # ──────────────────────────────────────────────────────────────
    # Interface
    # ──────────────────────────────────────────────────────────────

    def log_tool_start(self, event: LogEvent) -> None:
        self._emit(event)

    def log_tool_end(self, event: LogEvent) -> None:
        self._emit(event)

    def log_tool_error(self, event: LogEvent) -> None:
        self._emit(event)

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    def _get_logger(self):
        """Cria (ou retorna o existente) client do Cloud Logging."""
        if self._cloud_logger is None:
            try:
                import google.cloud.logging as cloud_logging  # type: ignore
                client = cloud_logging.Client(project=self._project_id)
                self._cloud_logger = client.logger(self._log_name)
            except Exception as exc:
                # Não falha o sistema inteiro — apenas registra no stderr
                import sys
                print(
                    f"[CloudLogger] Falha ao inicializar google-cloud-logging: {exc}",
                    file=sys.stderr,
                )
        return self._cloud_logger

    def _emit(self, event: LogEvent) -> None:
        logger = self._get_logger()
        if logger is None:
            return
        severity = _SEVERITY_MAP.get(event.event_type, "INFO")
        try:
            logger.log_struct(
                event.to_dict(),
                severity=severity,
                labels={
                    "tool_name": event.tool_name,
                    "session_id": event.session_id,
                    "event_type": event.event_type,
                },
            )
        except Exception as exc:
            import sys
            print(
                f"[CloudLogger] Falha ao publicar log: {exc}",
                file=sys.stderr,
            )
