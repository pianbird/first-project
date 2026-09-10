import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, Any, Optional

# requests 패키지가 있으면 사용하고, 없으면 표준 라이브러리(urllib) 사용
HAS_REQUESTS = False
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# python-dotenv가 있으면 사용하고, 없으면 수동 .env 파싱
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
        except Exception:
            pass

class KiwoomRESTClient:
    """
    키움증권 REST API 연동 클라이언트 (파이썬 표준 라이브러리 및 requests 호환)
    
    - OAuth2 접근 토큰(Access Token) 발급 및 관리
    - 주식 시세/기본 정보 조회 (opt10001 등)
    - API 키 미설정 시 테스트용 시뮬레이션 모드 지원
    """

    MOCK_BASE_URL = "https://mockapi.kiwoom.com"
    REAL_BASE_URL = "https://api.kiwoom.com"

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        is_mock: Optional[bool] = None
    ):
        if is_mock is None:
            use_mock_str = os.getenv("KIWOOM_USE_MOCK", "True").lower()
            self.is_mock = use_mock_str in ("true", "1", "yes")
        else:
            self.is_mock = is_mock

        self.base_url = self.MOCK_BASE_URL if self.is_mock else self.REAL_BASE_URL

        # 환경(모의투자 / 실전투자)에 따라 환경 변수 키 자동 선택
        if self.is_mock:
            default_key = os.getenv("KIWOOM_MOCK_APP_KEY") or os.getenv("KIWOOM_APP_KEY", "")
            default_secret = os.getenv("KIWOOM_MOCK_APP_SECRET") or os.getenv("KIWOOM_APP_SECRET", "")
        else:
            default_key = os.getenv("KIWOOM_REAL_APP_KEY") or os.getenv("KIWOOM_APP_KEY", "")
            default_secret = os.getenv("KIWOOM_REAL_APP_SECRET") or os.getenv("KIWOOM_APP_SECRET", "")

        raw_key = app_key if app_key is not None else default_key
        raw_secret = app_secret if app_secret is not None else default_secret

        self.app_key = raw_key.strip().strip('"').strip("'")
        self.app_secret = raw_secret.strip().strip('"').strip("'")

        self.access_token: Optional[str] = None
        self.token_expires_at: float = 0.0

    def is_credentials_valid(self) -> bool:
        """App Key 및 App Secret 설정 여부 확인"""
        dummy_keys = {
            "your_app_key_here", "your_app_secret_here",
            "your_actual_app_key", "your_actual_app_secret",
            "your_mock_app_key_here", "your_mock_app_secret_here",
            "your_real_app_key_here", "your_real_app_secret_here",
            "your_app_key", "your_app_secret",
            "test_app_key", "test_app_secret", ""
        }
        key = (self.app_key or "").strip()
        secret = (self.app_secret or "").strip()
        return bool(key and secret and key not in dummy_keys and secret not in dummy_keys)

    def get_access_token(self) -> str:
        """
        OAuth2 접근 토큰(Access Token) 발급 및 캐싱
        """
        # 토큰 유효 시간 체크 (만료 60초 전 재발급)
        if self.access_token and time.time() < (self.token_expires_at - 60):
            return self.access_token

        if not self.is_credentials_valid():
            raise ValueError("유효한 KIWOOM_APP_KEY 또는 KIWOOM_APP_SECRET이 .env 파일에 설정되지 않았습니다.")

        url = f"{self.base_url}/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret
        }

        try:
            if HAS_REQUESTS:
                headers = {"Content-Type": "application/json; charset=UTF-8"}
                response = requests.post(url, headers=headers, json=payload, timeout=10)
                if response.status_code == 415:
                    headers_form = {"Content-Type": "application/x-www-form-urlencoded"}
                    response = requests.post(url, headers=headers_form, data=payload, timeout=10)
                response.raise_for_status()
                data = response.json()
            else:
                headers = {"Content-Type": "application/json; charset=UTF-8"}
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

            token_val = data.get("token") or data.get("access_token")
            if not token_val:
                err_msg = data.get("return_msg") or data.get("msg") or str(data)
                raise RuntimeError(f"키움 API 토큰 발급 응답 에러: {err_msg}")

            self.access_token = token_val
            expires_in = int(data.get("expires_in", 86400))
            self.token_expires_at = time.time() + expires_in
            return self.access_token

        except Exception as e:
            raise RuntimeError(f"키움 REST API 토큰 발급 실패: {e}") from e

    def get_daily_chart(self, stock_code: str = "005930", base_dt: Optional[str] = None) -> list:
        """
        주식 일봉 차트 데이터 조회 (ka10081)
        """
        clean_code = stock_code.strip()
        if not base_dt:
            base_dt = time.strftime("%Y%m%d")

        token = self.get_access_token()
        url = f"{self.base_url}/api/dostk/chart"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "ka10081"
        }
        body = {
            "stk_cd": clean_code,
            "base_dt": base_dt,
            "upd_stkpc_tp": "1"
        }

        if HAS_REQUESTS:
            response = requests.post(url, headers=headers, json=body, timeout=10)
            response.raise_for_status()
            res_json = response.json()
        else:
            data_bytes = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))

        raw_candles = res_json.get("stk_dt_pole_chart_qry", [])
        parsed_candles = []
        for item in raw_candles:
            def parse_int(val):
                if not val:
                    return 0
                s = str(val).strip().lstrip("+").lstrip("-")
                return int(s) if s.isdigit() else 0

            parsed_candles.append({
                "date": item.get("dt", ""),
                "open_price": parse_int(item.get("open_pric", 0)),
                "high_price": parse_int(item.get("high_pric", 0)),
                "low_price": parse_int(item.get("low_pric", 0)),
                "close_price": parse_int(item.get("cur_prc", 0)),
                "volume": parse_int(item.get("trde_qty", 0)),
                "change": parse_int(item.get("pred_pre", 0))
            })
        return parsed_candles

    def get_stock_price(self, stock_code: str = "005930") -> Dict[str, Any]:
        """
        주식 종목 기본정보 및 현재가/전일 정보 조회 (기본값: 삼성전자 005930)

        :param stock_code: 종목코드 (6자리)
        :return: 종목명, 현재가, 전일대비, 등락률, 시가, 고가, 저가, 전일 시가/고가/저가/종가 등
        """
        clean_code = stock_code.strip()

        try:
            token = self.get_access_token()
        except Exception as e:
            return {
                "success": False,
                "code": clean_code,
                "error": str(e)
            }

        url = f"{self.base_url}/api/dostk/stkinfo" # 키움 REST API 주식기본정보요청 엔드포인트
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "ka10001"
        }
        body = {"stk_cd": clean_code}

        try:
            if HAS_REQUESTS:
                response = requests.post(url, headers=headers, json=body, timeout=10)
                response.raise_for_status()
                res_json = response.json()
            else:
                data_bytes = json.dumps(body).encode("utf-8")
                req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))

            return_code = res_json.get("return_code")
            if return_code is not None and int(return_code) != 0:
                return_msg = res_json.get("return_msg", "알 수 없는 오류")
                return {
                    "success": False,
                    "code": clean_code,
                    "error": f"키움 API 에러[{return_code}]: {return_msg}"
                }

            def parse_int(val):
                if not val:
                    return 0
                s = str(val).strip().lstrip("+").lstrip("-")
                return int(s) if s.isdigit() else 0

            def parse_float(val):
                if not val:
                    return 0.0
                try:
                    return float(str(val).strip())
                except ValueError:
                    return 0.0

            cur_prc_str = str(res_json.get("cur_prc", "0")).strip()
            cur_price = parse_int(cur_prc_str)

            change_val = parse_int(res_json.get("pred_pre", 0))
            if cur_prc_str.startswith("-"):
                change_val = -abs(change_val)

            # 일봉 차트(ka10081)로 전일 시가, 고가, 저가, 종가 추출
            prev_open = 0
            prev_high = 0
            prev_low = 0
            prev_close = parse_int(res_json.get("base_pric", 0)) or (cur_price - change_val)

            try:
                chart = self.get_daily_chart(clean_code)
                if len(chart) > 1:
                    prev_candle = chart[1]
                    prev_open = prev_candle.get("open_price", 0)
                    prev_high = prev_candle.get("high_price", 0)
                    prev_low = prev_candle.get("low_price", 0)
                    if prev_candle.get("close_price"):
                        prev_close = prev_candle.get("close_price")
            except Exception:
                pass

            return {
                "success": True,
                "code": clean_code,
                "name": res_json.get("stk_nm") or ("삼성전자" if clean_code == "005930" else clean_code),
                "current_price": cur_price,
                "prev_close_price": prev_close,
                "prev_open_price": prev_open,
                "prev_high_price": prev_high,
                "prev_low_price": prev_low,
                "change": change_val,
                "change_rate": parse_float(res_json.get("flu_rt", 0.0)),
                "open_price": parse_int(res_json.get("open_pric", 0)),
                "high_price": parse_int(res_json.get("high_pric", 0)),
                "low_price": parse_int(res_json.get("low_pric", 0)),
                "volume": parse_int(res_json.get("trde_qty", 0)),
                "raw": res_json
            }

        except Exception as e:
            return {
                "success": False,
                "code": clean_code,
                "error": str(e)
            }
