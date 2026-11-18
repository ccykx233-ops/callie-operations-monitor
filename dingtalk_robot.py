"""钉钉群自定义机器人：发送文本消息（可选加签、可选 @ 人）。

官方限额：每个机器人每分钟最多 20 条消息，超出会限流 10 分钟（钉钉官方文档）。
本模块自带滑动窗口限速（配置值默认低于 20），且只发送"转人工"这一类消息。
webhook 网址与密钥只来自本地配置文件：不进聊天、不进代码库、不进日志。
@ 人：可选传 at_mobiles（手机号）/ at_user_ids（钉钉用户ID）；钉钉要求正文里
包含 "@目标" 文本才会点亮提醒，本模块自动在正文末尾追加。被 @ 的人必须已在
该群里，否则钉钉静默跳过（不报错；非群成员的手机号会被脱敏显示）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from urllib.parse import quote_plus, urlparse

import requests

_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
_WEBHOOK_HOST = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.dingtalk\.com$", re.IGNORECASE)
_MAX_BODY_BYTES = 20000
_AT_MOBILE = re.compile(r"^\+?[0-9]{6,20}$")
_AT_USER_ID = re.compile(r"^[A-Za-z0-9@._-]{1,64}$")


class DingTalkRobotError(RuntimeError):
    def __init__(self, error_code: str, *, retryable: bool = False, delivery_uncertain: bool = False) -> None:
        if _SAFE_ERROR_CODE.fullmatch(error_code) is None:
            raise ValueError("invalid error code")
        self.error_code = error_code
        self.retryable = retryable and not delivery_uncertain
        self.delivery_uncertain = delivery_uncertain
        super().__init__(error_code)


def validate_webhook_url(url: object) -> str:
    if not isinstance(url, str) or not url.strip():
        raise DingTalkRobotError("DINGTALK_WEBHOOK_INVALID")
    stripped = url.strip()
    parsed = urlparse(stripped)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not _WEBHOOK_HOST.fullmatch(parsed.hostname)
        or not parsed.path.startswith("/robot/send")
        or "access_token=" not in (parsed.query or "")
    ):
        raise DingTalkRobotError("DINGTALK_WEBHOOK_INVALID")
    return stripped


def _sign(secret: str, timestamp_ms: int) -> str:
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    return quote_plus(base64.b64encode(digest).decode("ascii"))


def with_at_mentions(content: str, at_mobiles=(), at_user_ids=()) -> str:
    """把 "@目标" 文本追加到消息末尾（钉钉要求正文含 @ 文本才会点亮提醒）。"""

    mentions = [f"@{token}" for token in at_mobiles]
    mentions += [f"@{token}" for token in at_user_ids]
    if not mentions:
        return content
    return content.rstrip() + "\n" + " ".join(mentions)


def _normalize_at_items(items, pattern) -> tuple[str, ...]:
    if items is None:
        return ()
    if isinstance(items, str) or not hasattr(items, "__iter__"):
        raise DingTalkRobotError("DINGTALK_AT_INVALID")
    cleaned: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise DingTalkRobotError("DINGTALK_AT_INVALID")
        token = item.strip()
        if not token:
            continue
        if pattern.fullmatch(token) is None:
            raise DingTalkRobotError("DINGTALK_AT_INVALID")
        if token not in cleaned:
            cleaned.append(token)
    return tuple(cleaned)


def _normalize_at_targets(
    at_mobiles, at_user_ids
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return (
        _normalize_at_items(at_mobiles, _AT_MOBILE),
        _normalize_at_items(at_user_ids, _AT_USER_ID),
    )


class DingTalkRobot:
    """一条 webhook 发一条群消息；重试与节流策略由调用方负责。"""

    def __init__(
        self,
        webhook_url: str,
        secret: str | None = None,
        *,
        timeout: float = 10.0,
        session=None,
        now=time.time,
    ) -> None:
        self._webhook_url = validate_webhook_url(webhook_url)
        if secret is not None and (
            not isinstance(secret, str) or not secret.strip() or len(secret.strip()) > 255
        ):
            raise DingTalkRobotError("DINGTALK_SECRET_INVALID")
        self._secret = secret.strip() if isinstance(secret, str) and secret.strip() else None
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise DingTalkRobotError("DINGTALK_CONFIG_INVALID")
        self._timeout = float(timeout)
        if session is None:
            session = requests.Session()
            session.trust_env = False
        self._session = session
        self._now = now

    def signed_url(self) -> str:
        if self._secret is None:
            return self._webhook_url
        timestamp_ms = int(self._now() * 1000)
        return (
            f"{self._webhook_url}&timestamp={timestamp_ms}"
            f"&sign={_sign(self._secret, timestamp_ms)}"
        )

    def send_text(self, content: str, *, at_mobiles=(), at_user_ids=()) -> None:
        if not isinstance(content, str) or not content.strip():
            raise DingTalkRobotError("DINGTALK_CONTENT_INVALID")
        mobiles, user_ids = _normalize_at_targets(at_mobiles, at_user_ids)
        if mobiles or user_ids:
            content = with_at_mentions(content, mobiles, user_ids)
        payload: dict = {"msgtype": "text", "text": {"content": content}}
        if mobiles or user_ids:
            at: dict = {}
            if mobiles:
                at["atMobiles"] = list(mobiles)
            if user_ids:
                at["atUserIds"] = list(user_ids)
            payload["at"] = at
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(body) > _MAX_BODY_BYTES:
            raise DingTalkRobotError("DINGTALK_CONTENT_TOO_LARGE")
        try:
            response = self._session.post(
                self.signed_url(),
                data=body,
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.Timeout:
            raise DingTalkRobotError("DINGTALK_TIMEOUT", delivery_uncertain=True) from None
        except requests.ConnectionError:
            raise DingTalkRobotError("DINGTALK_CONNECTION_ERROR", delivery_uncertain=True) from None
        except requests.RequestException:
            raise DingTalkRobotError("DINGTALK_REQUEST_ERROR", delivery_uncertain=True) from None
        if response.status_code != 200:
            raise DingTalkRobotError(
                "DINGTALK_HTTP_ERROR", delivery_uncertain=response.status_code >= 500
            )
        try:
            payload = response.json()
        except ValueError:
            raise DingTalkRobotError("DINGTALK_RESPONSE_INVALID", delivery_uncertain=True) from None
        if not isinstance(payload, dict) or type(payload.get("errcode")) is not int:
            raise DingTalkRobotError("DINGTALK_RESPONSE_INVALID", delivery_uncertain=True)
        if payload["errcode"] != 0:
            if payload["errcode"] < 0:
                raise DingTalkRobotError("DINGTALK_REMOTE_REJECTED")
            raise DingTalkRobotError(f"DINGTALK_ERRCODE_{payload['errcode']}")


class RateLimiter:
    """滑动窗口限速器；now / sleep 可注入以便离线测试。"""

    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        *,
        now=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        if not (isinstance(max_events, int) and 1 <= max_events <= 100):
            raise ValueError("invalid limiter config")
        if isinstance(window_seconds, bool) or not 0 < window_seconds <= 3600:
            raise ValueError("invalid limiter config")
        self._max = max_events
        self._window = float(window_seconds)
        self._now = now
        self._sleep = sleep
        self._events: list[float] = []

    def acquire(self) -> float:
        """必要时阻塞到窗口内可用；返回本次等待秒数（供测试断言）。"""

        waited = 0.0
        while True:
            now = self._now()
            self._events = [t for t in self._events if now - t < self._window]
            if len(self._events) < self._max:
                self._events.append(now)
                return waited
            delay = self._window - (now - self._events[0]) + 0.05
            delay = max(0.05, delay)
            self._sleep(delay)
            waited += delay
