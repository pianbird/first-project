import os
import sys

# 1. 절대 경로 설정 (pianbird 계정 및 magic_trader 디렉터리 기준)
project_home = "/home/pianbird/magic_trader"
backend_dir = os.path.join(project_home, "web_app", "backend")

# sys.path에 프로젝트 및 백엔드 경로 등록
if project_home not in sys.path:
    sys.path.insert(0, project_home)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# 2. 작업 디렉터리(cwd) 설정
# 상대 경로로 지정된 DB(web_magictrader.db) 및 정적 파일 경로 정합성 보장
os.chdir(project_home)

# 3. a2wsgi 어댑터를 활용한 ASGI -> WSGI 변환
# FastAPI app 객체를 PythonAnywhere WSGI 컨테이너가 서빙할 수 있도록 application으로 래핑
try:
    from a2wsgi import ASGIMiddleware
    from web_app.backend.main import app as fastapi_app

    application = ASGIMiddleware(fastapi_app)
except Exception as e:
    import traceback
    print(f"[WSGI Error] Failed to load FastAPI app: {e}", file=sys.stderr)
    traceback.print_exc()
    raise e