import unittest
import sys
import os
from kiwoom_client import KiwoomRESTClient

class TestKiwoomSamsungPrice(unittest.TestCase):
    """
    키움 REST API 삼성전자(005930) 주가 조회 테스트 케이스
    """

    def setUp(self):
        self.invalid_client = KiwoomRESTClient(app_key="", app_secret="", is_mock=True)
        self.default_client = KiwoomRESTClient()
        self.samsung_code = "005930"

    def test_invalid_credentials_raises_error(self):
        """API 키 미설정 시 가상 데이터 대신 예외가 발생하는지 검증"""
        with self.assertRaises(ValueError):
            self.invalid_client.get_access_token()

    def test_invalid_credentials_stock_price_fails(self):
        """API 키 미설정 시 주가 조회가 실패(success=False)를 반환하는지 검증"""
        result = self.invalid_client.get_stock_price(self.samsung_code)
        self.assertFalse(result.get("success"))
        self.assertIn("error", result)

    def test_client_initialization(self):
        """클라이언트 초기화 상태 테스트"""
        self.assertIsNotNone(self.default_client.base_url)

def print_stock_info_summary():
    """삼성전자 주가 정보를 화면에 출력하는 함수"""
    client = KiwoomRESTClient()
    samsung_code = "005930"
    
    print("=" * 60)
    print(" [키움 REST API] 삼성전자(005930) 주가 조회 테스트 실행")
    print("=" * 60)

    mode_str = "모의투자 (Mock)" if client.is_mock else "실전투자 (Real)"
    key_status = "설정됨" if client.is_credentials_valid() else "미설정 (API 키 필요)"
    print(f"[*] 접속 환경: {mode_str}")
    print(f"[*] API Key 상태: {key_status}")
    print("-" * 60)

    price_info = client.get_stock_price(samsung_code)

    if price_info.get("success"):
        sign = "+" if price_info['change'] > 0 else ""
        print(f"[-] 종목명    : {price_info['name']} ({price_info['code']})")
        print(f"[-] 현재가    : {price_info['current_price']:,} 원")
        print(f"[-] 전일대비  : {sign}{price_info['change']:,} 원 ({sign}{price_info['change_rate']}%)")
        print(f"[-] 전일 시가 : {price_info.get('prev_open_price', 0):,} 원")
        print(f"[-] 전일 고가 : {price_info.get('prev_high_price', 0):,} 원")
        print(f"[-] 전일 저가 : {price_info.get('prev_low_price', 0):,} 원")
        print(f"[-] 전일 종가 : {price_info.get('prev_close_price', 0):,} 원")
        print(f"[-] 당일 시가 : {price_info['open_price']:,} 원")
        print(f"[-] 당일 고가 : {price_info['high_price']:,} 원")
        print(f"[-] 당일 저가 : {price_info['low_price']:,} 원")
        print(f"[-] 당일 거래량: {price_info['volume']:,} 주")
    else:
        print(f"[X] 주가 조회 실패: {price_info.get('error')}")

    print("=" * 60)

if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    print_stock_info_summary()
    print("\n[단위 테스트 실행 결과]")
    unittest.main(argv=[sys.argv[0]], exit=False)
