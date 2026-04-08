from personal_slack_agent.slack.auth import SlackApiSession
from personal_slack_agent.slack.auth import extract_api_session_from_request


def test_extract_api_session_from_request_parses_token_and_origin():
    session = extract_api_session_from_request(
        "https://example.enterprise.slack.com/api/conversations.history?_x_id=abc",
        "------WebKitFormBoundary\r\nContent-Disposition: form-data; name=\"token\"\r\n\r\nxoxc-demo-token\r\n------WebKitFormBoundary--\r\n",
    )

    assert session == SlackApiSession(
        origin="https://example.enterprise.slack.com",
        token="xoxc-demo-token",
    )
