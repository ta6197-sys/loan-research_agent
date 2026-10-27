"""FastAPI에서 금융 전용 MCP 연결을 조립한다. 키는 금융 자식 프로세스만 받는다."""
import os
from pathlib import Path

from agent.mcp_client import MCPClient


def create_finance_client(db_path, source_root):
    script = Path(__file__).resolve().parents[2] / 'mcp_server_finance.py'
    environment = {'APP_DB_PATH':str(Path(db_path).resolve()),
                   'APP_SOURCE_ROOT':str(Path(source_root).resolve()),
                   'OPENDART_API_KEY':os.environ.get('OPENDART_API_KEY','')}
    for name in ('DART_MAX_RESPONSE_BYTES','DART_MAX_EXPANDED_BYTES','DART_MAX_ARCHIVE_MEMBERS'):
        if name in os.environ:
            environment[name] = os.environ[name]
    return MCPClient(server_scripts={'finance':script},
                     server_env={'finance':environment})
