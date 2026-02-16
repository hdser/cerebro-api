import asyncio
import threading
from typing import Optional, List, Dict, Any
from fastapi import FastAPI

from app.config import settings
from app.factory import build_router
from app.manifest import manifest


class RouterManager:
    def __init__(self, app: FastAPI):
        self.app = app
        self._lock = threading.Lock()
        self._dynamic_routes: List[Any] = []
        self._refresh_task: Optional[asyncio.Task] = None

    def install_initial_routes(self) -> None:
        router = build_router()
        with self._lock:
            self._swap_routes(router)

    def _swap_routes(self, router) -> None:
        # Capture current routes to isolate newly-added ones
        before_ids = {id(r) for r in self.app.router.routes}
        self.app.include_router(router, prefix="/v1")
        added = [r for r in self.app.router.routes if id(r) not in before_ids]

        if self._dynamic_routes:
            self.app.router.routes = [
                r for r in self.app.router.routes if r not in self._dynamic_routes
            ]

        self._dynamic_routes = added
        self.app.openapi_schema = None

    def refresh_sync(self) -> Dict[str, Any]:
        with self._lock:
            changed, error = manifest.reload_if_changed()
            if error:
                return {"status": "error", "models": manifest.model_count(), "detail": error}
            if not changed:
                return {"status": "unchanged", "models": manifest.model_count()}

            router = build_router()
            self._swap_routes(router)
            return {"status": "reloaded", "models": manifest.model_count()}

    async def refresh_async(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.refresh_sync)

    def start_background_refresh(self) -> None:
        if not settings.DBT_MANIFEST_REFRESH_ENABLED:
            return
        if self._refresh_task and not self._refresh_task.done():
            return

        async def _loop():
            try:
                while True:
                    await asyncio.sleep(settings.DBT_MANIFEST_REFRESH_INTERVAL_SECONDS)
                    await self.refresh_async()
            except asyncio.CancelledError:
                return

        self._refresh_task = asyncio.create_task(_loop())

    async def stop_background_refresh(self) -> None:
        if not self._refresh_task:
            return
        self._refresh_task.cancel()
        try:
            await self._refresh_task
        except asyncio.CancelledError:
            pass
        self._refresh_task = None
