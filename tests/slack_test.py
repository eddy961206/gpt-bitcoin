import sys
import os
from pathlib import Path

# 루트 디렉토리의 경로를 sys.path에 추가
root_directory = Path(__file__).parent.parent
sys.path.append(str(root_directory))

# 이제 autotrade 모듈을 import 할 수 있습니다.
from autotrade import post_message

# 테스트를 위한 환경 변수 설정 (실제 토큰과 채널을 사용해야 합니다)
slackToken = os.getenv("SLACK_TOKEN")
SLACK_CHANNEL="#bitcoin-gpt"

# post_message 함수 테스트
def test_post_message():
    # 테스트용 메시지
    test_message = "테스트 메시지가 성공적으로 전송되었습니다."
    
    # 함수 호출
    post_message(slackToken,SLACK_CHANNEL, test_message)

# 테스트 실행
if __name__ == "__main__":
    test_post_message()
