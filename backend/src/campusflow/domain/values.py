"""字段取值的规范化比较：同一段 JSON 数据只有一种比较文本。

字典键顺序、空白等书写差异不影响“是否相同”，而 ``1`` 与 ``true``、
``"1"`` 与 ``1`` 必须区分。冲突判断（X008）与版本差异（N011）共用本规则。
"""

import json

from campusflow.domain.candidates import JsonValue


def canonical_json(value: JsonValue) -> str:
    """把已校验的字段取值转成可比较文本；调用方负责保证取值可序列化。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
