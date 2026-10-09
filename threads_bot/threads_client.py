import time
from collections.abc import Callable
from typing import Any

import httpx

from .config import ThreadsCredentials
from .settings import (
    STATUS_CHECK_ATTEMPTS,
    STATUS_CHECK_INTERVAL_SECONDS,
    THREADS_API_VERSION,
    THREADS_TIMEOUT_SECONDS,
)


class ThreadsError(RuntimeError):
    pass


class PublicationUnknown(ThreadsError):
    pass


class ThreadsClient:
    def __init__(
        self,
        credentials: ThreadsCredentials,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.credentials = credentials
        self.client = client or httpx.Client(timeout=THREADS_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self.sleep = sleep
        self.base = f"https://graph.threads.net/{THREADS_API_VERSION}"
        self.headers = {"Authorization": f"Bearer {credentials.access_token}"}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    @staticmethod
    def _id(response: httpx.Response, stage: str) -> str:
        try:
            value = response.json()["id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ThreadsError(f"Threadsの{stage}応答にIDがありません。") from exc
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
            raise ThreadsError(f"Threadsの{stage}応答のIDが不正です。")
        return value

    @staticmethod
    def _http_error(response: httpx.Response, stage: str) -> ThreadsError:
        code: Any = None
        try:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                code = body["error"].get("code")
        except ValueError:
            pass
        suffix = f" (APIコード: {code})" if isinstance(code, int) else ""
        return ThreadsError(f"Threadsの{stage}に失敗しました (HTTP {response.status_code}){suffix}。")

    def create_container(self, text: str) -> str:
        try:
            response = self.client.post(
                f"{self.base}/{self.credentials.user_id}/threads",
                data={"media_type": "TEXT", "text": text},
                headers=self.headers,
            )
        except httpx.RequestError as exc:
            raise ThreadsError("Threadsのコンテナ作成で通信に失敗しました。") from exc
        if response.status_code >= 400:
            raise self._http_error(response, "コンテナ作成")
        return self._id(response, "コンテナ作成")

    def wait_until_ready(self, container_id: str) -> None:
        for attempt in range(STATUS_CHECK_ATTEMPTS):
            try:
                response = self.client.get(
                    f"{self.base}/{container_id}",
                    params={"fields": "id,status,error_message"},
                    headers=self.headers,
                )
            except httpx.RequestError as exc:
                raise ThreadsError("Threadsのコンテナ状態を確認できませんでした。") from exc
            if response.status_code >= 400:
                raise self._http_error(response, "コンテナ状態確認")
            try:
                status = response.json()["status"]
            except (ValueError, KeyError, TypeError) as exc:
                raise ThreadsError("Threadsのコンテナ状態が不明です。") from exc
            if status == "FINISHED":
                return
            if status in {"ERROR", "EXPIRED", "PUBLISHED"}:
                raise ThreadsError(f"Threadsのコンテナ状態が{status}です。")
            if status != "IN_PROGRESS":
                raise ThreadsError("Threadsのコンテナ状態が不明です。")
            if attempt + 1 < STATUS_CHECK_ATTEMPTS:
                self.sleep(STATUS_CHECK_INTERVAL_SECONDS)
        raise ThreadsError("Threadsのコンテナ準備が時間内に終わりませんでした。")

    def publish(self, container_id: str) -> str:
        try:
            response = self.client.post(
                f"{self.base}/{self.credentials.user_id}/threads_publish",
                data={"creation_id": container_id},
                headers=self.headers,
            )
        except httpx.RequestError as exc:
            raise PublicationUnknown("公開要求後に通信が切れました。投稿済みか確認してください。") from exc
        if 400 <= response.status_code < 500:
            raise self._http_error(response, "公開")
        if response.status_code >= 500 or response.status_code < 200 or response.status_code >= 300:
            raise PublicationUnknown(f"公開結果を確定できません (HTTP {response.status_code})。")
        try:
            return self._id(response, "公開")
        except ThreadsError as exc:
            raise PublicationUnknown("公開応答に投稿IDがありません。投稿済みか確認してください。") from exc

