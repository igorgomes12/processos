"""
Testes unitários para logger/factory.py.
Cobre: create_logger, _make_local, _make_cloud via variáveis de ambiente.
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock

from logger.factory import create_logger
from logger.local_logger import LocalFileLogger
from logger.composite_logger import CompositeLogger


class TestCreateLogger:
    def test_default_returns_local_file_logger(self, tmp_path):
        with patch.dict(os.environ, {"LOG_BACKEND": "local", "LOG_DIR": str(tmp_path)}):
            logger = create_logger()
        assert isinstance(logger, LocalFileLogger)

    def test_unset_log_backend_returns_local(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "LOG_BACKEND"}
        env["LOG_DIR"] = str(tmp_path)
        with patch.dict(os.environ, env, clear=True):
            logger = create_logger()
        assert isinstance(logger, LocalFileLogger)

    def test_unknown_backend_falls_back_to_local(self, tmp_path):
        with patch.dict(os.environ, {"LOG_BACKEND": "unknown_backend", "LOG_DIR": str(tmp_path)}):
            logger = create_logger()
        assert isinstance(logger, LocalFileLogger)

    def test_local_backend_returns_local_file_logger(self, tmp_path):
        with patch.dict(os.environ, {"LOG_BACKEND": "local", "LOG_DIR": str(tmp_path)}):
            logger = create_logger()
        assert isinstance(logger, LocalFileLogger)

    def test_cloud_backend_returns_cloud_logger(self, tmp_path):
        mock_cloud = MagicMock()
        with patch.dict(os.environ, {"LOG_BACKEND": "cloud"}):
            with patch("logger.factory._make_cloud", return_value=mock_cloud):
                logger = create_logger()
        assert logger is mock_cloud

    def test_both_backend_returns_composite_logger(self, tmp_path):
        mock_local = MagicMock()
        mock_cloud = MagicMock()
        with patch.dict(os.environ, {"LOG_BACKEND": "both"}):
            with patch("logger.factory._make_local", return_value=mock_local):
                with patch("logger.factory._make_cloud", return_value=mock_cloud):
                    logger = create_logger()
        assert isinstance(logger, CompositeLogger)
        assert mock_local in logger._loggers
        assert mock_cloud in logger._loggers

    def test_log_dir_env_var_used(self, tmp_path):
        custom_dir = tmp_path / "custom_logs"
        with patch.dict(os.environ, {"LOG_BACKEND": "local", "LOG_DIR": str(custom_dir)}):
            logger = create_logger()
        # O diretório deve ter sido criado
        assert custom_dir.exists()

    def test_log_file_env_var_used(self, tmp_path):
        with patch.dict(
            os.environ,
            {"LOG_BACKEND": "local", "LOG_DIR": str(tmp_path), "LOG_FILE": "custom.log"},
        ):
            logger = create_logger()
        # Deve aceitar o nome personalizado sem erro
        assert isinstance(logger, LocalFileLogger)

    def test_backend_value_is_case_insensitive(self, tmp_path):
        with patch.dict(os.environ, {"LOG_BACKEND": "LOCAL", "LOG_DIR": str(tmp_path)}):
            logger = create_logger()
        assert isinstance(logger, LocalFileLogger)

    def test_backend_strips_whitespace(self, tmp_path):
        with patch.dict(os.environ, {"LOG_BACKEND": "  local  ", "LOG_DIR": str(tmp_path)}):
            logger = create_logger()
        assert isinstance(logger, LocalFileLogger)
