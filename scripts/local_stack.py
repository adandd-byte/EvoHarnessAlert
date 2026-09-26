"""Start isolated, loopback-only MySQL/Redis/API for local integration testing."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'target/local-stack'
MYSQL = '/opt/homebrew/opt/mysql@8.4/bin/mysqld'
REDIS = '/opt/homebrew/bin/redis-server'


def listening(port):
    with socket.socket() as connection:
        return connection.connect_ex(('127.0.0.1', port)) == 0


def wait_for(port):
    for _ in range(100):
        if listening(port):
            return
        time.sleep(0.2)
    raise RuntimeError(f'Service did not start on {port}; inspect {DATA}')


def main():
    import pymysql
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    mysql_data = DATA / 'mysql'
    socket_path = '/private/tmp/evoalert-local-mysql.sock'
    if not mysql_data.exists():
        mysql_data.mkdir(mode=0o700)
        subprocess.run([MYSQL, '--no-defaults', '--initialize-insecure', f'--datadir={mysql_data}'], check=True)
    if not listening(13306):
        subprocess.run([MYSQL, '--no-defaults', f'--datadir={mysql_data}', '--bind-address=127.0.0.1', '--port=13306', f'--socket={socket_path}', '--mysqlx=OFF', f'--pid-file={DATA / "mysql.pid"}', f'--log-error={DATA / "mysql.log"}', '--daemonize'], check=True)
    wait_for(13306)
    credential_file = DATA / 'root.json'
    password = json.loads(credential_file.read_text())['password'] if credential_file.exists() else ''
    db = pymysql.connect(unix_socket=socket_path, user='root', password=password, autocommit=True)
    with db.cursor() as cursor:
        cursor.execute('SELECT @@datadir')
        if Path(cursor.fetchone()[0]).resolve() != mysql_data.resolve():
            raise RuntimeError('Socket belongs to another MySQL instance; refusing to provision it')
        if not password:
            password = secrets.token_urlsafe(32)
            credential_file.write_text(json.dumps({'password': password}))
            credential_file.chmod(0o600)
            cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (password,))
        for name in ['evoharness_alert', 'evoharness_alert_test']:
            cursor.execute(f'CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4')
        cursor.execute("CREATE USER IF NOT EXISTS 'evoalert'@'127.0.0.1' IDENTIFIED BY 'evoalert'")
        for name in ['evoharness_alert', 'evoharness_alert_test']:
            cursor.execute(f"GRANT ALL ON `{name}`.* TO 'evoalert'@'127.0.0.1'")
    db.close()
    if not listening(16379):
        subprocess.run([REDIS, '--bind', '127.0.0.1', '--port', '16379', '--protected-mode', 'yes', '--dir', str(DATA), '--dbfilename', 'redis.rdb', '--pidfile', str(DATA / 'redis.pid'), '--logfile', str(DATA / 'redis.log'), '--daemonize', 'yes'], check=True)
    wait_for(16379)
    from redis import Redis
    cache = Redis(host='127.0.0.1', port=16379, decode_responses=True)
    if Path(cache.config_get('dir')['dir']).resolve() != DATA.resolve():
        raise RuntimeError('Redis port belongs to another instance')
    env = {**os.environ, 'DATABASE_URL':'mysql+pymysql://evoalert:evoalert@127.0.0.1:13306/evoharness_alert?charset=utf8mb4', 'REDIS_URL':'redis://127.0.0.1:16379/0', 'ALERT_EMAIL_DELIVERY_MODE':'log', 'TOOL_QUEUE_ENABLED':'true', 'KNOWLEDGE_VECTOR_ENABLED':'false', 'EXCEL_PATH':str(DATA / 'integration-ledger.xlsx')}
    # The desktop shell may inject unrelated model credentials. This local stack
    # explicitly uses the project's model configuration, never that account.
    from dotenv import dotenv_values
    for key, value in dotenv_values(ROOT / '.env').items():
        if value is not None and key.startswith(('AI_', 'OPENAI_', 'OLLAMA_')):
            env[key] = value
    if not listening(8092):
        with (DATA / 'api.log').open('a') as log:
            process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8092'], cwd=ROOT, env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        (DATA / 'api.pid').write_text(str(process.pid))
    wait_for(8092)
    import httpx
    health = httpx.get('http://127.0.0.1:8092/actuator/health', timeout=5)
    health.raise_for_status()
    if health.json().get('service') != 'EvoHarnessAlert':
        raise RuntimeError('Port 8092 belongs to another application')
    print('MySQL: 127.0.0.1:13306; Redis: 127.0.0.1:16379; API: http://127.0.0.1:8092')
    print('Local development only. AI uses .env/environment; email=log. Logs:', DATA)


if __name__ == '__main__':
    main()
