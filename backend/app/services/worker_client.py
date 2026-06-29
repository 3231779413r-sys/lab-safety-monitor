from __future__ import annotations

import asyncio
from typing import Any, Optional

import requests
from fastapi import HTTPException

from ..core.config import settings
from .worker_sharding import camera_worker_index, extract_camera_id_from_internal_path


class WorkerProxyError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def worker_proxy_enabled() -> bool:
    return settings.BACKEND_MODE.strip().lower() == "api"


def _worker_base_urls() -> list[str]:
    configured_urls = [
        str(url).strip()
        for url in getattr(settings, "WORKER_INTERNAL_BASE_URLS", [])
        if str(url).strip()
    ]
    if configured_urls:
        return configured_urls
    return [settings.WORKER_INTERNAL_BASE_URL.strip()]


def _select_worker_base_url(path: str) -> str:
    base_urls = _worker_base_urls()
    if len(base_urls) == 1:
        return base_urls[0]
    camera_id = extract_camera_id_from_internal_path(path)
    if not camera_id:
        return base_urls[0]
    return base_urls[camera_worker_index(camera_id, len(base_urls))]


def _build_url(path: str) -> str:
    base = _select_worker_base_url(path).rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def _headers() -> dict[str, str]:
    return {"X-Worker-Token": settings.WORKER_INTERNAL_TOKEN}


def _response_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        detail = payload.get("detail")
        if detail:
            return str(detail)

    text = response.text.strip()
    return text or "Worker request failed"


def _request_json_sync(
    method: str,
    path: str,
    *,
    json_body: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
    data: Optional[dict[str, Any]] = None,
    files: Optional[dict[str, Any]] = None,
) -> Any:
    response = requests.request(
        method=method,
        url=_build_url(path),
        headers=_headers(),
        json=json_body,
        params=params,
        data=data,
        files=files,
        timeout=settings.WORKER_INTERNAL_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        detail = _response_detail(response)
        raise WorkerProxyError(response.status_code, detail)
    return response.json()


async def request_worker_json(
    method: str,
    path: str,
    *,
    json_body: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
    data: Optional[dict[str, Any]] = None,
    files: Optional[dict[str, Any]] = None,
) -> Any:
    try:
        return await asyncio.to_thread(
            _request_json_sync,
            method,
            path,
            json_body=json_body,
            params=params,
            data=data,
            files=files,
        )
    except WorkerProxyError:
        raise
    except requests.RequestException as exc:
        raise WorkerProxyError(503, f"Worker unavailable: {exc}") from exc


async def request_all_workers_json(
    method: str,
    path: str,
    *,
    json_body: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
    data: Optional[dict[str, Any]] = None,
    files: Optional[dict[str, Any]] = None,
) -> list[Any]:
    async def _request_to_base(base_url: str) -> Any:
        def _runner() -> Any:
            response = requests.request(
                method=method,
                url=f"{base_url.rstrip('/')}/{path.lstrip('/')}",
                headers=_headers(),
                json=json_body,
                params=params,
                data=data,
                files=files,
                timeout=settings.WORKER_INTERNAL_TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                detail = _response_detail(response)
                raise WorkerProxyError(response.status_code, detail)
            return response.json()

        return await asyncio.to_thread(_runner)

    try:
        return await asyncio.gather(*[_request_to_base(base_url) for base_url in _worker_base_urls()])
    except WorkerProxyError:
        raise
    except requests.RequestException as exc:
        raise WorkerProxyError(503, f"Worker unavailable: {exc}") from exc


def stream_worker_response(path: str, *, params: Optional[dict[str, Any]] = None) -> requests.Response:
    try:
        response = requests.get(
            _build_url(path),
            headers=_headers(),
            params=params,
            stream=True,
            timeout=(settings.WORKER_INTERNAL_TIMEOUT_SECONDS, None),
        )
        if response.status_code >= 400:
            detail = _response_detail(response)
            raise WorkerProxyError(response.status_code, detail)
        return response
    except WorkerProxyError:
        raise
    except requests.RequestException as exc:
        raise WorkerProxyError(503, f"Worker unavailable: {exc}") from exc


def raise_http_from_worker_error(exc: WorkerProxyError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail)
