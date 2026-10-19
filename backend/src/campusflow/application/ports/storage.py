"""原文件存储端口（D002）。

存储键由服务端生成，应用层与 API 层只通过本接口读写文件；
存储实现不生成任何公开访问地址，文件只能经授权的业务接口读取。
"""

from typing import Protocol


class FileStorage(Protocol):
    """私有文件存储；实现不得接受调用方文件名，也不得生成公开访问地址。

    存储键必须由服务端生成；读取与删除必须拒绝绝对路径、盘符和越出根目录的
    片段（路径穿越），不能把非法键解析成根目录外的路径。
    """

    def save(self, content: bytes, extension: str) -> str:
        """保存文件内容，返回服务端生成的存储键。extension 必须已通过领域净化。

        实现仍须拒绝绝对路径与越界片段；大小上限由调用方在写入前校验。
        """
        ...

    def read(self, storage_key: str) -> bytes:
        """按存储键读取文件内容；不存在时抛出 FileNotFoundError，非法键必须拒绝。"""
        ...

    def delete(self, storage_key: str) -> bool:
        """按存储键删除文件；存在并删除返回 True，不存在返回 False。"""
        ...
