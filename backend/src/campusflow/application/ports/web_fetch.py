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
    """公开网页读取；只有显式调用才联网，不携带账号、Cookie 或代理。

    实现必须逐跳校验解析出的地址为公网地址，拒绝本机、内网与保留地址，并限制
    跳转次数、响应大小与总等待时间；可预期失败抛 WebFetchError，供界面展示。
    """

    def fetch(self, requested_url: str) -> FetchedWebPage:
        """只读取调用方明确提交的公开网址及其有限跳转，不跟随正文链接。

        不自动重试；需要登录或返回非网页内容时直接失败，由用户改用上传/粘贴。
        """
        ...
