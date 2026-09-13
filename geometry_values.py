"""XML geometry values interpreted before applying export transforms."""
from localization import tr
import re

def box_size(value):
    tokens = value.split()
    if len(tokens) > 3:
        raise ValueError(tr('voxbox size 超过三个分量'))
    result = []
    for i in range(3):
        token = tokens[i] if i < len(tokens) else '0'
        match = re.match('^[+-]?\\d+', token)
        if not match:
            raise ValueError(tr('无效 voxbox size：') + value)
        result.append(max(1, int(match[0])))
    return tuple(result)
