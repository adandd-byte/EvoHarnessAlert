from unittest.mock import Mock, patch
from types import SimpleNamespace
import json

import pytest

from app.core.config import Settings
from app.schemas.dtos import AiMessage
from app.services.ai import AiClient
from app.services.alerting import AlertIngestService


@pytest.mark.parametrize('terminal,success', [('response.completed',True),('response.failed',False),('response.incomplete',False),('[DONE]',False)])
def test_responses_stream_requires_complete_text(terminal, success):
    response = Mock()
    response.iter_lines.return_value = [
        'data: ' + json.dumps({'type':'response.output_text.delta','delta':'成功'}),
        'data: ' + (terminal if terminal == '[DONE]' else json.dumps({'type':terminal})),
    ]
    with patch('app.services.ai.httpx.Client') as client:
        client.return_value.__enter__.return_value.stream.return_value.__enter__.return_value = response
        ai = AiClient(Settings(ai_provider='openai',openai_wire_api='responses',openai_responses_stream=True))
        if success:
            assert ai.complete([]) == '成功'
        else:
            with pytest.raises(ValueError):
                ai.complete([])


def test_responses_protocol_and_output():
    settings = Settings(ai_provider='openai', openai_wire_api='responses', openai_responses_stream=False, openai_api_key='test-key', openai_base_url='https://example.test/v1/')
    response = Mock()
    response.json.return_value = {'status':'completed', 'output':[{'type':'reasoning'}, {'type':'message','content':[{'type':'output_text','text':'成功'}]}]}
    with patch('app.services.ai.httpx.post', return_value=response) as post:
        assert AiClient(settings).complete([AiMessage(role='user',content='测试')]) == '成功'
        assert post.call_args.args[0] == 'https://example.test/v1/responses'
        assert post.call_args.kwargs['json']['store'] is False


@pytest.mark.parametrize('data', [{'status':'incomplete'}, {'status':'completed','output':[]}])
def test_responses_rejects_incomplete_or_empty(data):
    response = Mock()
    response.json.return_value = data
    with patch('app.services.ai.httpx.post', return_value=response):
        with pytest.raises(ValueError):
            AiClient(Settings(ai_provider='openai',openai_wire_api='responses',openai_responses_stream=False)).complete([])


def test_webhook_model_assessment_cached_and_failure_visible():
    alert = SimpleNamespace(id=1,title='测试告警',description='联系人 13812345678',severity='P1',alert_type='PROBLEM',status='FIRING')
    service = AlertIngestService(Mock(),Settings(ai_provider='openai'))
    result = {k:'待验证' for k in ['summary','impact','rootCauseHint','runbookHint']}
    with patch('app.services.alerting.AiClient.complete',return_value=json.dumps(result)) as call:
        first = service._assessment(alert,{}, {})
        assert first['modelStatus'] == 'SUCCESS'
        assert service._assessment(alert,{}, {}) is first
        assert call.call_count == 1
        assert '13812345678' not in str(call.call_args)
    alert.id = 2
    with patch('app.services.alerting.AiClient.complete',side_effect=ValueError('secret')):
        result = service._assessment(alert,{}, {})
        assert result['modelStatus'] == 'FALLBACK'
        assert result['modelError'] == 'ValueError'
        assert 'secret' not in str(result)
