"""原文件存储端口（D002）。

存储键由服务端生成，应用层与 API 层只通过本接口读写文件；
存储实现不生成任何公开访问地址，文件只能经授权的业务接口读取。
"""

from typing import Protocol


class FileStorage(Protocol):
    def save(self, content: bytes, extension: str) -> str:
        """保存文件内容，返回服务端生成的存储键。extension 必须已通过领域净化。"""
        ...

    def read(self, storage_key: str) -> bytes:
        """按存储键读取文件内容；不存在时抛出 FileNotFoundError。"""
        ...

    def delete(self, storage_key: str) -> bool:
        """按存储键删除文件；存在并删除返回 True，不存在返回 False。"""
        ...
