"""Exercise the real local API, queue, MySQL records and Redis memory."""
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx
from redis import Redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings
from app.services.memory import RedisShortTermMemoryStore


def main():
    run = uuid4().hex
    checks = []
    with httpx.Client(base_url='http://127.0.0.1:8092', auth=('admin', 'admin123'), timeout=30) as client:
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        def post(path, body):
            response = client.post(path, json=body)
            response.raise_for_status()
            return response.json()

        assert get('/actuator/health')['service'] == 'EvoHarnessAlert'
        assert get('/api/profile')['username'] == 'admin'
        payload = {'source':'local-integration', 'fingerprint':f'local-{run}', 'title':'真实联调：团购支付接口持续出现 5xx', 'description':'本地集成测试输入，验证真实入库和队列；不是生产故障。', 'severity':'P0', 'alertType':'problem', 'labels':{'service':'group-buy-payment', 'env':'local-integration'}}
        first = post('/api/alerts/webhook', payload)
        second = post('/api/alerts/webhook', payload)
        assert first['incidentId'] == second['incidentId']
        ids = {first['alertId'], second['alertId']}
        checks.extend(['health', 'authentication', 'webhook_p0', 'fingerprint_aggregation'])
        deadline = time.monotonic() + 40
        while True:
            jobs = [j for j in get('/api/admin/tool-jobs') if j['alertId'] in ids]
            if len(jobs) == 6 and all(j['status'] == 'SUCCESS' for j in jobs):
                break
            if time.monotonic() > deadline:
                raise AssertionError(jobs)
            time.sleep(0.5)
        assert {j['kind'] for j in jobs} == {'LEDGER_WRITE', 'INCIDENT_UPSERT', 'NOTIFICATION_SEND'}
        for endpoint in ['alerts', 'agent-traces', 'ledger-records', 'notifications']:
            rows = get(f'/api/admin/{endpoint}')
            assert any((r.get('alertId') if endpoint != 'alerts' else r['id']) in ids for r in rows), endpoint
        checks.extend(['six_queue_jobs_success', 'mysql_alert_trace_ledger_notification_records'])
        incident = first['incidentId']
        assert post(f'/api/admin/incidents/{incident}/ack', {'actor':'local-test'})['status'] == 'ACKNOWLEDGED'
        post(f'/api/admin/incidents/{incident}/notes', {'actor':'local-test', 'note':'真实 MySQL 备注写入验证'})
        assert get(f'/api/admin/incidents/{incident}/notes')
        assert post(f'/api/admin/incidents/{incident}/resolve', {'actor':'local-test', 'note':'联调验证完成，关闭测试事件'})['status'] == 'RESOLVED'
        checks.append('ack_notes_resolve')
    redis = Redis.from_url('redis://127.0.0.1:16379/1', decode_responses=True)
    assert redis.ping()
    store = RedisShortTermMemoryStore(Settings(), redis_client=redis)
    key = store._key('integration', run)
    try:
        for i in range(45):
            store.append('integration', run, 'user', f'消息 {i} 联系人 13812345678')
        assert redis.llen(key) == 40
        assert 86300 < redis.ttl(key) <= 86400
        assert '13812345678' not in ''.join(redis.lrange(key, 0, -1))
        assert len(store.load_recent('integration', run)) == 40
        store.replace('integration', run, [{'role':'assistant', 'content':'已恢复'}])
        assert len(store.messages_from_owner_roles('integration', run, ['assistant'])) == 1
        checks.append('real_redis_append_trim_ttl_sanitize_load_replace')
    finally:
        redis.delete(key)
    result = {'passed':True, 'runId':run, 'checks':checks, 'alertIds':sorted(ids), 'incidentId':incident, 'ai':'mock', 'notification':'log, no real email', 'database':'real MySQL', 'redis':'real Redis'}
    output = Path(__file__).resolve().parents[1] / 'target/local-stack/integration-result.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
