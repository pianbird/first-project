import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, Any, Optional, List


class KiwoomRESTClient:
    """
    키움증권 REST API 통신 및 OAuth 토큰 관리자 (No-pip 표준 라이브러리)
    - 모의투자 환경(is_mock=True, 접미사 01) 최적화
    - OAuth2 접근 토큰 발급 및 data/token_cache.json 캐시 관리
    - 초당 호출 제한(Rate Limiting, 0.25초) 준수
    - 주식 시세, 일봉 차트, 모의 계좌 잔고 및 주문 전송
    """

    MOCK_BASE_URL = "https://mockapi.kiwoom.com"
    REAL_BASE_URL = "https://api.kiwoom.com"

    def __init__(self, config_path: Optional[str] = None):
        if not config_path:
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
        self.config_path = config_path
        self.config = self._load_config(config_path)

        # 모의/실전 모드 설정 (환경변수 TRADING_MODE 또는 config.json 기준)
        trading_mode_env = os.getenv("TRADING_MODE", "").upper()
        if trading_mode_env in ["MOCK", "REAL"]:
            self.is_mock = (trading_mode_env != "REAL")
        else:
            self.is_mock = self.config.get("is_mock", True)

        self.base_url = self.MOCK_BASE_URL if self.is_mock else self.REAL_BASE_URL

        creds_key = "mock_credentials" if self.is_mock else "real_credentials"
        creds = self.config.get(creds_key, {})

        # 환경변수 우선 -> config.json 로드
        if self.is_mock:
            self.app_key = (
                os.getenv("KIWOOM_MOCK_APP_KEY")
                or os.getenv("KIWOOM_APP_KEY")
                or creds.get("app_key")
                or ""
            ).strip()
            self.app_secret = (
                os.getenv("KIWOOM_MOCK_APP_SECRET")
                or os.getenv("KIWOOM_APP_SECRET")
                or creds.get("app_secret")
                or ""
            ).strip()
        else:
            self.app_key = (
                os.getenv("KIWOOM_REAL_APP_KEY")
                or os.getenv("KIWOOM_APP_KEY")
                or creds.get("app_key")
                or ""
            ).strip()
            self.app_secret = (
                os.getenv("KIWOOM_REAL_APP_SECRET")
                or os.getenv("KIWOOM_APP_SECRET")
                or creds.get("app_secret")
                or ""
            ).strip()

        # 데이터 디렉터리 및 토큰 캐시 설정
        self.data_dir = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.token_cache_path = os.path.join(self.data_dir, "token_cache.json")

        self.last_api_call_time = 0.0
        self.rate_limit_delay = 0.25  # 초당 4회 이하 호출 제어

    @staticmethod
    def _safe_int(val: Any) -> int:
        """문자열 부호, 공백, 쉼표를 제거하고 안전하게 정수로 변환"""
        if val is None:
            return 0
        s = str(val).strip().replace(",", "").lstrip("+")
        try:
            return int(float(s))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _safe_float(val: Any) -> float:
        """문자열을 안전하게 부동소수점으로 변환"""
        if val is None:
            return 0.0
        s = str(val).strip().replace(",", "").lstrip("+")
        try:
            return float(s)
        except (ValueError, TypeError):
            return 0.0

    def is_credentials_valid(self) -> bool:
        """App Key 및 App Secret 설정 여부 검증"""
        k = (self.app_key or "").strip()
        s = (self.app_secret or "").strip()
        invalid_keys = [
            "your_app_key_here",
            "your_mock_app_key_here",
            "your_real_app_key_here",
        ]
        invalid_secrets = [
            "your_app_secret_here",
            "your_mock_app_secret_here",
            "your_real_app_secret_here",
        ]
        return bool(k and s and k not in invalid_keys and s not in invalid_secrets)

    def _load_config(self, path: str) -> Dict[str, Any]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[KiwoomClient] config.json 로드 예외: {e}")
                return {}
        return {}

    def _enforce_rate_limit(self):
        """API 호출간 딜레이(초당 4회 제한) 보장"""
        elapsed = time.time() - self.last_api_call_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self.last_api_call_time = time.time()

    def _request_with_backoff(
        self, req: urllib.request.Request, max_retries: int = 3, timeout: int = 10, is_order: bool = False
    ) -> Dict[str, Any]:
        """
        네트워크 순단 대비 지수 백오프 재시도 및 Rate Limit(0.25초) 보장
        - is_order=True (주문 전송 API) 인 경우 중복 주문 방지를 위해 네트워크 에러 시 자동 백오프 재시도를 금지합니다.
        """
        delays = [1, 2, 4, 8]
        last_exception = None

        if not req.has_header("User-agent"):
            req.add_header(
                "User-Agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )

        # 주문 API의 경우 무조건적 자동 재시도 0회 (1회 시도 후 네트워크 예외 즉시 발생)
        effective_retries = 0 if is_order else max_retries

        for attempt in range(effective_retries + 1):
            try:
                self._enforce_rate_limit()
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last_exception = e
                # HTTP 401/403 (토큰 만료/권한 오류) 시 상위 래퍼에서 갱신 후 재시도하도록 예외 상향 전달
                if e.code in [401, 403]:
                    raise e
                if attempt < effective_retries:
                    sleep_time = delays[min(attempt, len(delays) - 1)]
                    print(
                        f"[KiwoomClient Backoff] HTTP {e.code} 오류 발생({e.reason}). {sleep_time}초 후 재시도 ({attempt + 1}/{effective_retries})..."
                    )
                    time.sleep(sleep_time)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_exception = e
                if is_order:
                    # 주문 전송 API 통신 타임아웃/네트워크 오류 시 중복 주문 전송을 막기 위해 상위로 예외 전달
                    raise RuntimeError(f"ORDER_NETWORK_TIMEOUT: 주문 전송 중 네트워크 통신 예외 발생 (중복 재시도 안함): {e}") from e

                if attempt < effective_retries:
                    sleep_time = delays[min(attempt, len(delays) - 1)]
                    print(
                        f"[KiwoomClient Backoff] 네트워크 오류 발생({e}). {sleep_time}초 후 재시도 ({attempt + 1}/{effective_retries})..."
                    )
                    time.sleep(sleep_time)

        raise RuntimeError(f"지수 백오프 재시도 실패: {last_exception}")

    def clear_token_cache(self):
        """저장된 토큰 캐시 파일 삭제"""
        if os.path.exists(self.token_cache_path):
            try:
                os.remove(self.token_cache_path)
            except Exception:
                pass

    def get_access_token(self, force_refresh: bool = False) -> str:
        """OAuth2 모의투자 토큰 발급 및 로컬 캐시 관리"""
        if force_refresh:
            self.clear_token_cache()

        # 1. 로컬 토큰 캐시 검증
        if not force_refresh and os.path.exists(self.token_cache_path):
            try:
                with open(self.token_cache_path, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                    token = cache_data.get("token")
                    expires_at = cache_data.get("expires_at", 0)
                    cached_is_mock = cache_data.get("is_mock")
                    if token and cached_is_mock == self.is_mock and time.time() < (expires_at - 60):
                        return token
            except Exception:
                pass

        # 2. 신규 토큰 요청
        self._enforce_rate_limit()
        url = f"{self.base_url}/oauth2/token"
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        }

        try:
            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))

            token = res_data.get("token") or res_data.get("access_token")
            if not token:
                err_msg = res_data.get("return_msg") or str(res_data)
                raise RuntimeError(f"토큰 발급 실패: {err_msg}")

            expires_in = int(res_data.get("expires_in", 86400))
            expires_at = time.time() + expires_in

            cache_info = {
                "token": token,
                "is_mock": self.is_mock,
                "expires_at": expires_at,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(self.token_cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_info, f, indent=2)

            return token

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"키움 OAuth HTTP 에러[{e.code}]: {err_body}") from e
        except Exception as e:
            raise RuntimeError(f"키움 OAuth 토큰 요청 예외: {e}") from e

    def _post_api(self, url: str, api_id: str, body: dict, retry_on_token_error: bool = True, is_order: bool = False) -> dict:
        """키움 REST API POST 요청 래퍼 (토큰 만료 / 401 / 429 인터셉터 감지 시 1회 자동 재발급 & 재시도)"""
        token = self.get_access_token()
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
            "appkey": self.app_key,
            "secretkey": self.app_secret,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        data_bytes = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        try:
            res_json = self._request_with_backoff(req, is_order=is_order)
        except urllib.error.HTTPError as he:
            if retry_on_token_error and he.code in [401, 403]:
                if is_order:
                    raise RuntimeError(f"주문 API HTTP {he.code} 토큰 에러 발생: 중복주문 방지를 위해 자동 재전송을 금지하며 UNKNOWN 처리합니다.")
                print("[KiwoomClient] HTTP 401/403 토큰 만료 감지: 토큰 재발급 후 재요청 중...")
                new_token = self.get_access_token(force_refresh=True)
                headers["authorization"] = f"Bearer {new_token}"
                req_retry = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
                return self._request_with_backoff(req_retry, is_order=is_order)
            raise he

        ret_code = str(res_json.get("return_code", ""))
        ret_msg = str(res_json.get("return_msg", ""))

        if retry_on_token_error and ("8005" in ret_msg or "Token이 유효하지 않습니다" in ret_msg or ret_code in ["3", "-8005"]):
            if is_order:
                raise RuntimeError(f"주문 API 키움 토큰 에러[{ret_code}: {ret_msg}] 발생: 중복주문 방지를 위해 자동 재전송을 금지하며 UNKNOWN 처리합니다.")
            print("[KiwoomClient] 토큰 만료(8005) 감지: 토큰 갱신 후 재요청 중...")
            new_token = self.get_access_token(force_refresh=True)
            headers["authorization"] = f"Bearer {new_token}"
            req_retry = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            res_json = self._request_with_backoff(req_retry, is_order=is_order)

        return res_json

    def get_account_numbers(self) -> Dict[str, Any]:
        """ka00001 (계좌번호조회) TR로 모의투자 계좌 목록 확인"""
        fallback_acc = str(self.config.get("account_no", "")).strip()
        if not self.is_credentials_valid():
            return {"success": False, "account_list": [fallback_acc] if fallback_acc else [], "account_no": fallback_acc}

        try:
            url = f"{self.base_url}/api/dostk/acnt"
            res_json = self._post_api(url, api_id="ka00001", body={})
            if res_json and str(res_json.get("return_code", "-1")) == "0":
                acct_data = res_json.get("acctNo") or res_json.get("acnt_no") or res_json.get("output", [])
                accounts = []
                if isinstance(acct_data, list):
                    accounts = [str(x).strip() for x in acct_data if str(x).strip()]
                elif isinstance(acct_data, str) and acct_data.strip():
                    accounts = [acct_data.strip()]

                if accounts:
                    return {"success": True, "account_list": accounts, "account_no": accounts[0]}
        except Exception as e:
            print(f"[KiwoomClient] 계좌번호 조회 실패: {e}")

        return {"success": False, "account_list": [fallback_acc] if fallback_acc else [], "account_no": fallback_acc}

    def sync_positions(self, account_no: Optional[str] = None, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """하위 호환 계좌 잔고 리스트 반환 래퍼"""
        res = self.get_positions(account_no, force_refresh=force_refresh)
        if isinstance(res, dict):
            return res.get("positions") or res.get("held_positions", [])
        return []

    def get_positions(self, account_no: Optional[str] = None, force_refresh: bool = False) -> Dict[str, Any]:
        """
        kt00018 (계좌평가잔고내역요청) TR을 사용하여 모의투자 계좌 잔고 및 종목 목록 조회
        - force_refresh: 현재 KiwoomRESTClient는 내부 캐시 없이 매 요청 시 REST API를 직접 호출하므로 force_refresh는 시그니처 호환성 파라미터로 동작합니다.
        """
        # 1. 대상 계좌 번호 결정
        target_account = (account_no or "").strip()
        if not target_account:
            acc_info = self.get_account_numbers()
            if acc_info.get("success") and acc_info.get("account_no"):
                target_account = acc_info["account_no"]
            else:
                target_account = self.normalize_account_no(self.config.get("account_no"))

        raw_digits = "".join(filter(str.isdigit, target_account))
        base_8 = raw_digits[:8] if len(raw_digits) >= 8 else raw_digits

        # 모의투자는 01, 11 접미사 및 원본 계좌 탐색
        candidates = []
        if raw_digits:
            candidates.append(raw_digits)
        if base_8:
            candidates.append(base_8 + "11")  # 모의투자 10자리 규격(11)
            candidates.append(base_8 + "01")  # 모의투자 10자리 규격(01)
            candidates.append(base_8)         # 8자리 순수 번호

        acc_candidates = list(dict.fromkeys(candidates))

        if self.is_credentials_valid() and acc_candidates:
            last_err_msg = "[🧪 모의투자] 응답 없음"
            first_valid_response = None

            for acc_no in acc_candidates:
                body = {
                    "acnt_no": acc_no,
                    "pwd": "",
                    "dmst_stex_tp": "1",
                    "qry_tp": "1",
                }

                try:
                    url = f"{self.base_url}/api/dostk/acnt"
                    res_json = self._post_api(url, api_id="kt00018", body=body)

                    raw_ret_code = res_json.get("return_code")
                    if raw_ret_code is None:
                        raw_ret_code = res_json.get("rt_cd")
                    if raw_ret_code is None:
                        raw_ret_code = "-1"
                    ret_code = str(raw_ret_code).strip()

                    ret_msg = str(res_json.get("return_msg") or res_json.get("msg1") or "")
                    if ret_msg:
                        last_err_msg = f"[🧪 모의투자 / {acc_no}] {ret_msg}"

                    if ret_code in ["0", "0000"]:
                        raw_list = []
                        for key in ["output1", "output", "acnt_evlt_remn_indv_tot", "acnt_evl_lst", "grid"]:
                            val = res_json.get(key)
                            if isinstance(val, list):
                                raw_list = val
                                break

                        positions = []
                        for item in raw_list:
                            if not isinstance(item, dict):
                                continue

                            code = str(item.get("stk_cd") or item.get("pdno") or "").strip().lstrip("A").zfill(6)
                            raw_name = str(item.get("stk_nm") or item.get("prdt_name") or code).strip()
                            name = raw_name
                            try:
                                from stock_master import STOCK_MASTER_DB
                                if code in STOCK_MASTER_DB:
                                    name = STOCK_MASTER_DB[code]
                            except Exception:
                                pass
                            qty = self._safe_int(item.get("rmnd_qty") or item.get("hldg_qty") or item.get("hold_qty"))

                            if qty <= 0:
                                continue

                            avg_price = self._safe_int(item.get("pur_pric") or item.get("pchs_unpr"))
                            cur_price = self._safe_int(item.get("cur_prc") or item.get("prpr"))
                            purchase_amt = self._safe_int(item.get("pur_amt")) or (avg_price * qty)
                            eval_amt = self._safe_int(item.get("evlt_amt") or item.get("evl_amt")) or (cur_price * qty)
                            eval_pnl = self._safe_int(item.get("evltv_prft") or item.get("evl_pnl")) or (eval_amt - purchase_amt)
                            pnl_rate = self._safe_float(item.get("prft_rt") or item.get("evl_pnl_rt"))

                            positions.append({
                                "stock_code": code,
                                "stock_name": name,
                                "qty": qty,
                                "avg_price": avg_price,
                                "current_price": cur_price,
                                "purchase_amount": purchase_amt,
                                "valuation_amount": eval_amt,
                                "pnl": eval_pnl,
                                "pnl_rate": pnl_rate,
                            })

                        resp_payload = {
                            "success": True,
                            "account_no": acc_no,
                            "deposit": self._safe_int(
                                res_json.get("prsm_dpst_aset_amt")
                                or res_json.get("entr_dps_amt")
                                or res_json.get("dnca_tot_amt")
                            ),
                            "total_purchase": self._safe_int(res_json.get("tot_pur_amt")),
                            "total_valuation": self._safe_int(res_json.get("tot_evlt_amt") or res_json.get("tot_evl_amt")),
                            "total_eval_pnl": self._safe_int(res_json.get("tot_evlt_pl") or res_json.get("tot_evl_pnl")),
                            "total_eval_rate": self._safe_float(res_json.get("tot_prft_rt") or res_json.get("tot_evl_pnl_rt")),
                            "positions": positions,
                            "held_positions": positions,
                            "message": f"[🧪 모의투자 / {acc_no}] {ret_msg or f'{len(positions)}개 보유'}",
                            "source": "MOCK_SERVER",
                        }

                        # 보유 종목이 있으면 즉시 반환
                        if len(positions) > 0:
                            return resp_payload

                        # 빈 계좌여도 정상 수신이면 보관 후 루프 진행
                        if first_valid_response is None:
                            first_valid_response = resp_payload

                except Exception as e:
                    last_err_msg = f"모의 잔고 호출 실패: {e}"

            if first_valid_response:
                return first_valid_response

        # 실패 시 빈 구조 반환
        return {
            "success": False,
            "account_no": target_account,
            "deposit": 0,
            "total_purchase": 0,
            "total_valuation": 0,
            "total_eval_pnl": 0,
            "total_eval_rate": 0.0,
            "positions": [],
            "held_positions": [],
            "message": last_err_msg if "last_err_msg" in locals() else "계좌 정보 없음",
            "source": "MOCK_SERVER_FAILED",
        }

    def get_account_balance(self, account_no: Optional[str] = None, force_refresh: bool = False) -> Dict[str, Any]:
        """get_positions()의 별칭 메서드"""
        return self.get_positions(account_no, force_refresh=force_refresh)

    def get_stock_price(self, stock_code: str = "005930") -> Dict[str, Any]:
        """주식 현재가 및 기본 정보 조회 (ka10001)"""
        clean_code = stock_code.strip()
        url = f"{self.base_url}/api/dostk/stkinfo"
        body = {"stk_cd": clean_code}

        try:
            res_json = self._post_api(url, api_id="ka10001", body=body)
            return_code = res_json.get("return_code")
            if return_code is not None and int(return_code) != 0:
                return {"success": False, "code": clean_code, "error": f"API 에러[{return_code}]: {res_json.get('return_msg')}"}

            cur_prc_str = str(res_json.get("cur_prc", "0")).strip()
            cur_price = self._safe_int(cur_prc_str)
            change_val = self._safe_int(res_json.get("pred_pre", 0))
            if cur_prc_str.startswith("-"):
                change_val = -abs(change_val)

            prev_close = self._safe_int(res_json.get("base_pric", 0)) or (cur_price - change_val)

            return {
                "success": True,
                "code": clean_code,
                "name": res_json.get("stk_nm") or clean_code,
                "current_price": cur_price,
                "prev_close_price": prev_close,
                "change": change_val,
                "change_rate": self._safe_float(res_json.get("flu_rt", 0.0)),
                "open_price": self._safe_int(res_json.get("open_pric", 0)),
                "high_price": self._safe_int(res_json.get("high_pric", 0)),
                "low_price": self._safe_int(res_json.get("low_pric", 0)),
                "volume": self._safe_int(res_json.get("trde_qty", 0)),
                "raw": res_json,
            }
        except Exception as e:
            return {"success": False, "code": clean_code, "error": str(e)}

    def get_daily_chart(self, stock_code: str = "005930", base_dt: Optional[str] = None) -> List[Dict[str, Any]]:
        """일봉 차트 데이터 조회 (ka10081)"""
        clean_code = stock_code.strip()
        if not base_dt:
            base_dt = time.strftime("%Y%m%d")

        url = f"{self.base_url}/api/dostk/chart"
        body = {"stk_cd": clean_code, "base_dt": base_dt, "upd_stkpc_tp": "1"}

        try:
            res_json = self._post_api(url, api_id="ka10081", body=body)
            raw_candles = res_json.get("stk_dt_pole_chart_qry", [])
            parsed_candles = []
            for item in raw_candles:
                parsed_candles.append({
                    "date": item.get("dt", ""),
                    "open_price": self._safe_int(item.get("open_pric", 0)),
                    "high_price": self._safe_int(item.get("high_pric", 0)),
                    "low_price": self._safe_int(item.get("low_pric", 0)),
                    "close_price": self._safe_int(item.get("cur_prc", 0)),
                    "volume": self._safe_int(item.get("trde_qty", 0)),
                    "change": self._safe_int(item.get("pred_pre", 0)),
                })
            return parsed_candles
        except Exception:
            return []

    @staticmethod
    def normalize_account_no(acc_str: Optional[str] = None) -> str:
        """
        계좌번호 정규화 단일 함수
        하드코딩된 특정 계좌번호를 완전히 제거하고 KIWOOM_ACCOUNT_NO 또는 입력값을 기준으로 정규화
        """
        if not acc_str:
            acc_str = os.getenv("KIWOOM_ACCOUNT_NO", "")
        raw_digits = "".join(filter(str.isdigit, str(acc_str or "").strip()))
        if len(raw_digits) >= 10:
            return raw_digits[:10]
        elif len(raw_digits) >= 8:
            return raw_digits[:8] + "01"
        return raw_digits

    def get_masked_credentials(self) -> Dict[str, Any]:
        """
        보안 원칙 준수: Dashboard API 등에 응답할 때 secret/token을 절대 노출하지 않는 마스킹된 크리덴셜 정보 반환
        """
        app_key_str = (self.app_key or "").strip()
        masked_key = ""
        if len(app_key_str) >= 8:
            masked_key = f"{app_key_str[:4]}****{app_key_str[-4:]}"
        elif app_key_str:
            masked_key = "****"

        return {
            "configured": self.is_credentials_valid(),
            "trading_mode": "MOCK" if self.is_mock else "REAL",
            "app_key": masked_key,
            "account_no": self.normalize_account_no(self.config.get("account_no"))
        }

    def place_order(
        self,
        account_no: str,
        order_type: str,
        stock_code: str,
        qty: int,
        price: int = 0,
        ord_dvsn: str = "03",
    ) -> Dict[str, Any]:
        """
        키움 주식 주문 전송 (매수: kt10000 / 매도: kt10001)
        ord_dvsn: "00"(지정가), "03" 또는 "3"(시장가), "06" 또는 "6"(최유리지정가)
        """
        clean_code = stock_code.strip().zfill(6)
        target_account = (account_no or "").strip()
        if not target_account:
            acc_info = self.get_account_numbers()
            if acc_info.get("success") and acc_info.get("account_no"):
                target_account = acc_info["account_no"]
            else:
                target_account = self.normalize_account_no(self.config.get("account_no"))

        acc_no = self.normalize_account_no(target_account)

        if self.is_credentials_valid():
            try:
                is_buy = order_type.upper() == "BUY"
                api_id = "kt10000" if is_buy else "kt10001"
                url = f"{self.base_url}/api/dostk/ordr"

                trde_tp = "0"
                if str(ord_dvsn) in ["03", "3", "01"]:
                    trde_tp = "3"
                elif str(ord_dvsn) in ["06", "6"]:
                    trde_tp = "6"

                body = {
                    "dmst_stex_tp": "KRX",
                    "stk_cd": clean_code,
                    "ord_qty": str(qty),
                    "ord_uv": str(price) if trde_tp == "0" else "0",
                    "trde_tp": trde_tp,
                }
                res_json = self._post_api(url, api_id=api_id, body=body, is_order=True)
                if res_json:
                    ret_code = str(res_json.get("return_code", "-1"))
                    if ret_code == "0":
                        output_obj = res_json.get("output") or res_json.get("output1") or {}
                        if isinstance(output_obj, list) and len(output_obj) > 0:
                            output_obj = output_obj[0]
                        elif not isinstance(output_obj, dict):
                            output_obj = {}
                        ord_no = str(res_json.get("ord_no") or res_json.get("odno") or output_obj.get("ord_no") or output_obj.get("odno") or f"ORD_{int(time.time()*1000)}")
                        return {
                            "success": True,
                            "order_id": ord_no,
                            "account_no": acc_no,
                            "stock_code": clean_code,
                            "order_type": order_type.upper(),
                            "ord_dvsn": ord_dvsn,
                            "qty": qty,
                            "price": price,
                            "status": "ACCEPTED",
                            "message": f"주문 접수 완료 (주문번호: {ord_no})",
                            "source": "KIWOOM_SERVER",
                        }
                    else:
                        ret_msg = res_json.get("return_msg", "주문 접수 거부")
                        return {
                            "success": False,
                            "order_id": "",
                            "account_no": acc_no,
                            "stock_code": clean_code,
                            "order_type": order_type.upper(),
                            "status": "REJECTED",
                            "message": f"주문 에러[{ret_code}]: {ret_msg}",
                            "source": "KIWOOM_SERVER",
                        }
            except Exception as e:
                print(f"[Kiwoom Order API Error] {e}")
                # 네트워크 예외/타임아웃 발생 시 상위 OrderManager로 전달하여 UNKNOWN 상태 처리
                raise

        # 인증키 미설정 시: 명시적 시뮬레이션 전용 모드가 아닌 이상 가상 주문 자동 성공 폴백 차단
        if getattr(self, "is_simulation_mode", False):
            return {
                "success": True,
                "order_id": f"ORD_SIM_{int(time.time()*1000)}",
                "account_no": acc_no or "0000000001",
                "stock_code": clean_code,
                "order_type": order_type.upper(),
                "qty": qty,
                "price": price,
                "status": "ACCEPTED",
                "message": f"가상 주문 완료 (종목: {clean_code}, {qty}주)",
                "source": "SIMULATION_MODE",
            }

        return {
            "success": False,
            "order_id": "",
            "account_no": acc_no,
            "stock_code": clean_code,
            "order_type": order_type.upper(),
            "status": "REJECTED",
            "message": "ORDER REJECTED: Kiwoom API credentials invalid or not configured. (가상 주문 자동 성공 폴백 차단)",
            "source": "KIWOOM_SERVER",
        }