"""公开网页抓取契约；不包含正文解析、资料保存或后台爬取。"""

from dataclasses import dataclass
from typing import Protocol


class WebFetchError(ValueError):
    """可向用户展示的抓取失败，不携带网址中的查询参数或底层异常。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FetchedWebPage:
    requested_url: str
    final_url: str
    content_type: str
    body: bytes


class WebFetcher(Protocol):
    def fetch(self, requested_url: str) -> FetchedWebPage:
        """只读取调用方明确提交的公开网址及其有限跳转，不跟随正文链接。"""
        ...
