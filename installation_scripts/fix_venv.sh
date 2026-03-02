#!/bin/bash
# Workaround para bug na imagem base reasoning-engine-py311:prod (atualizada em 2026-03-02).
#
# O step 20/21 do Dockerfile executa:
#   RUN .venv/bin/python -m compileall "$(/code/.venv/bin/python -c "import site; print(site.getsitepackages()[0])")" .
#
# Dois problemas introduzidos pela nova imagem:
#   1. /code/.venv/bin/python nao existe mais (imagem nao cria mais o symlink).
#   2. O container agora roda como usuario nao-root: o compileall falha com
#      PermissionError ao tentar escrever __pycache__ em /usr/local/lib/python3.11/site-packages/
#
# Solucao:
#   Criar /code/.venv/bin/python como um WRAPPER Python que intercepta a query
#   "import site; print(site.getsitepackages()[0])" e retorna um diretorio
#   gravavel (/tmp/compileall-sp) em vez dos site-packages do sistema.
#   Assim, o compileall opera em um diretorio gravavel e nao falha com PermissionError.
#   Para todos os outros usos, o wrapper faz exec do Python real.

set -e

echo "[fix_venv.sh] Iniciando workaround para reasoning-engine-py311:prod..."

# Detecta o Python real disponivel no sistema
REAL_PYTHON=""
for candidate in /usr/local/bin/python3.11 /usr/bin/python3.11 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$candidate" ]; then
        REAL_PYTHON="$candidate"
        break
    fi
done

if [ -z "$REAL_PYTHON" ]; then
    REAL_PYTHON=$(which python3.11 2>/dev/null || which python3 2>/dev/null || "")
fi

if [ -z "$REAL_PYTHON" ]; then
    echo "[fix_venv.sh] ERRO: Nenhum Python encontrado no sistema."
    ls -la /usr/local/bin/python* /usr/bin/python* 2>/dev/null || true
    exit 1
fi

echo "[fix_venv.sh] Python real encontrado: $REAL_PYTHON"

# Cria o diretorio /code/.venv/bin se nao existir
mkdir -p /code/.venv/bin

# Cria o diretorio gravavel que sera retornado ao compileall
mkdir -p /tmp/compileall-sp

# Cria o wrapper Python em /code/.venv/bin/python
# O wrapper intercepta a query de site-packages e redireciona para /tmp/compileall-sp
cat > /code/.venv/bin/python << WRAPPER_EOF
#!$REAL_PYTHON
import sys, os

# Detecta a chamada interna do compileall para obter o site-packages
if (len(sys.argv) == 3
        and sys.argv[1] == '-c'
        and 'getsitepackages' in sys.argv[2]):
    # Retorna um diretorio gravavel em vez de /usr/local/lib/python3.11/site-packages
    sp = '/tmp/compileall-sp'
    os.makedirs(sp, exist_ok=True)
    print(sp)
    sys.exit(0)

# Para todos os outros usos, exec o Python real
os.execv('$REAL_PYTHON', ['$REAL_PYTHON'] + sys.argv[1:])
WRAPPER_EOF

chmod +x /code/.venv/bin/python
echo "[fix_venv.sh] Wrapper criado: /code/.venv/bin/python"
echo "[fix_venv.sh] Workaround concluido com sucesso."
