"""Local read-only API. Importing this module performs no input reads or networking."""

from datetime import date
import re
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from pydantic import BeforeValidator

from whoop_product.repository import CsvProductRepository, InvalidAnchor, SnapshotUnavailable
from whoop_product.models import TrendName
from whoop_product.today_service import get_today
from whoop_product.sleep_service import get_sleep
from whoop_product.trend_service import get_trend, InvalidTrend
from .schemas import Health, Error, TodayResponse, SleepResponse, TrendResponse, serialize_product

ERRORS = {
    403: ('local_access_only', 'Local access only.'),
    404: ('not_found', 'Route not found.'),
    405: ('read_only', 'This API accepts GET only.'),
    422: ('invalid_query', 'Invalid query parameters.'),
    500: ('internal_error', 'Internal service error.'),
    503: ('snapshot_unavailable', 'Local snapshot unavailable.'),
}
ERROR_RESPONSES = {status: {'model': Error, 'description': message}
                   for status, (_, message) in ERRORS.items()}


def canonical_date(value):
    if value is not None and (not isinstance(value, str) or
                             not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value)):
        raise ValueError('Expected YYYY-MM-DD.')
    return value


AnchorDate = Annotated[date | None, BeforeValidator(canonical_date)]


def create_app(repository=None):
    repo = repository if repository is not None else CsvProductRepository()
    application = FastAPI(title='WHOOP read-only product API',version='v1',
                          docs_url=None,redoc_url=None,openapi_url=None,
                          responses=ERROR_RESPONSES)

    def error(request, status, code, message, retryable=False):
        result = Error(code=code,message=message,retryable=retryable,request_id=request.state.request_id)
        return JSONResponse(status_code=status,content=result.model_dump())

    @application.middleware('http')
    async def boundary(request: Request, call_next):
        request.state.request_id = str(uuid4())
        hosts = request.headers.getlist('host')
        host = hosts[0] if len(hosts)==1 else ''
        allowed = re.fullmatch(r'(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?',host)
        origin = request.headers.getlist('origin')
        if (not allowed or not request.client or request.client.host not in ('127.0.0.1','::1')
                or (origin and origin != ['http://'+host])
                or request.headers.get('sec-fetch-site')=='cross-site'):
            response = error(request,403,'local_access_only','Local access only.')
        elif request.method != 'GET':
            response = error(request,405,'read_only','This API accepts GET only.')
        elif request.url.path.rstrip('/') == '/api/v1/trends' and (
                set(request.query_params) - {'metric', 'anchor_date', 'window_days'} or
                any(len(request.query_params.getlist(name)) != 1 for name in request.query_params)):
            response = error(request,422,'invalid_query','Invalid query parameters.')
        else:
            try:
                response = await call_next(request)
            except (InvalidAnchor,InvalidTrend):
                response = error(request,422,'invalid_query','Unsupported metric, window or anchor date.')
            except SnapshotUnavailable:
                response = error(request,503,'snapshot_unavailable','Local snapshot unavailable.',True)
            except Exception:
                response = error(request,500,'internal_error','Internal service error.')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Request-ID'] = request.state.request_id
        return response

    @application.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return error(request,422,'invalid_query','Invalid query parameters.')

    @application.exception_handler(HTTPException)
    async def http_error(request, exc):
        status = exc.status_code if exc.status_code in ERRORS else 500
        code, message = ERRORS[status]
        return error(request, status, code, message, status == 503)

    @application.get('/api/v1/health',response_model=Health,operation_id='get_health')
    def health():
        return Health(status='ok', schema_version='v1')

    @application.get('/api/v1/today',response_model=TodayResponse,operation_id='get_today')
    def today():
        return serialize_product(get_today(repo),TodayResponse)

    @application.get('/api/v1/sleep/latest',response_model=SleepResponse,operation_id='get_latest_sleep')
    def sleep():
        return serialize_product(get_sleep(repo),SleepResponse)

    @application.get('/api/v1/trends',response_model=TrendResponse,operation_id='get_trends')
    def trends(metric: TrendName, anchor_date: AnchorDate = None, window_days: Literal['7','14','30'] = '14'):
        return serialize_product(get_trend(repo,metric,anchor_date,int(window_days)),TrendResponse)

    return application


app = create_app()
