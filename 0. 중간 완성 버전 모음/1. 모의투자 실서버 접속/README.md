# 키움증권 REST API 삼성전자 주가 조회 테스트

키움증권 REST API를 활용하여 삼성전자(`005930`)의 현재가, 전일대비, 등락률, 거래량 등 주가 데이터를 조회하는 파이썬 테스트 프로젝트입니다.

---

## 📁 주요 파일 구성

- **[kiwoom_client.py](file:///c:/Python_Project/1.%20매직%20트레이더%20개발/kiwoom_client.py)**: 키움 REST API 연동 클라이언트 (OAuth2 토큰 발급/캐싱, 주가 조회, 시뮬레이션 모드 지원)
- **[test_samsung_price.py](file:///c:/Python_Project/1.%20매직%20트레이더%20개발/test_samsung_price.py)**: 삼성전자 주가 조회 테스트 및 결과 출력 스크립트
- **[.env.example](file:///c:/Python_Project/1.%20매직%20트레이더%20개발/.env.example)**: 환경변수(App Key, App Secret) 설정 템플릿
- **[requirements.txt](file:///c:/Python_Project/1.%20매직%20트레이더%20개발/requirements.txt)**: 의존성 패키지 목록 (`requests`, `python-dotenv`)

---

## ⚙️ 사용 방법

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. API Key 설정 (선택사항)

실제 키움증권 REST API를 통해 데이터 시세를 요청하려면 `.env` 파일을 작성해야 합니다.

1. `.env.example` 파일을 복사하여 `.env` 파일로 저장합니다.
2. 키움증권 REST API 포털에서 발급받은 App Key와 App Secret을 입력합니다.

```env
KIWOOM_APP_KEY=your_actual_app_key
KIWOOM_APP_SECRET=your_actual_app_secret
KIWOOM_USE_MOCK=True
```

*API Key가 없는 경우에도 기본 시뮬레이션 데이터 모드로 테스트 코드가 정상 작동합니다.*

---

## 🚀 테스트 실행

### 직접 실행 및 주가 정보 출력

```bash
python test_samsung_price.py
```

### Unittest 단위 테스트 실행

```bash
python -m unittest test_samsung_price.py
```
