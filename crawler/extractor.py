import re
import json
from typing import Dict, Any, Optional
from datetime import datetime

class LooseExtractor:
    FIELD_ALIASES = {
        'nickname': ['昵称', '名字', '网名', 'ID'],
        'province': ['省份', '省', '所在省'],
        'city': ['城市', '市', '所在城市'],
        'age': ['年龄', '年纪', '岁数'],
        'height': ['身高'],
        'weight': ['体重'],
        'cup': ['罩杯', '胸围'],
        'occupation': ['职业', '工作'],
        'monthly_allowance': ['月生活费', '生活费', '月薪', '月费', '包月'],
        'intro_fee': ['介绍费', '费用', '中介费'],
        'code': ['编号', 'ID', '代码'],
    }

    BOOL_ALIASES = {
        'is_virgin': ['是否是chu女', '是否处女', 'chu女', '处女'],
        'oral': ['可不可以口', '能否口', '口'],
        'creampie': ['能不能内射', '能否内射', '内射'],
        'condomless': ['能不能无套', '能否无套', '无套'],
        'sm': ['SM能不能', '能否SM', 'SM'],
        'tattoo': ['有没有纹身', '纹身', '刺青'],
        'out_province': ['是否可外省', '能否外省', '外省'],
        'overnight': ['是否可过夜', '能否过夜', '过夜'],
        'cohabitation': ['可不可以同居', '能否同居', '同居'],
    }

    def __init__(self):
        self._compile_patterns()

    def _compile_patterns(self):
        self.patterns = {}
        for field, aliases in self.FIELD_ALIASES.items():
            combined = '|'.join(re.escape(a) for a in aliases)
            self.patterns[field] = re.compile(
                rf'(?:{combined})[：:\s]+([^\n#]+?)(?=\n|#|$)',
                re.IGNORECASE
            )
        for field, aliases in self.BOOL_ALIASES.items():
            combined = '|'.join(re.escape(a) for a in aliases)
            self.patterns[field] = re.compile(
                rf'(?:{combined})[：:\s]+([^\n#]+?)(?=\n|#|$)',
                re.IGNORECASE
            )

    def extract(self, text: str) -> Dict[str, Any]:
        if not text or len(text) < 20:
            return {'_empty': True, 'confidence': 0.0}

        result = {'_raw_length': len(text)}
        found_count = 0
        expected_count = len(self.FIELD_ALIASES) + len(self.BOOL_ALIASES)

        for field, pattern in self.patterns.items():
            matches = pattern.findall(text)
            if matches:
                value = matches[0].strip()
                value = re.sub(r'[​\s]+', ' ', value).strip()

                if field in self.BOOL_ALIASES:
                    result[field] = self._parse_loose_bool(value)
                elif field in ['age', 'height', 'weight']:
                    result[field] = self._extract_number(value)
                elif field in ['monthly_allowance', 'intro_fee']:
                    result[field] = self._extract_money(value)
                else:
                    result[field] = value
                found_count += 1

        code_alt = re.search(r'\b([A-Z]\d{4,})\b', text)
        if code_alt and 'code' not in result:
            result['code'] = code_alt.group(1)
            found_count += 1

        contacts = re.findall(r'[@]([a-zA-Z0-9_]{5,})', text)
        if contacts:
            result['contacts'] = list(set(contacts))

        tags = re.findall(r'#([^\s#]+)', text)
        if tags:
            result['tags'] = tags

        result['confidence'] = round(found_count / max(expected_count, 1), 2)
        result['_found_fields'] = found_count
        result['_expected_fields'] = expected_count

        if result['confidence'] >= 0.7:
            result['_status'] = 'parsed'
        elif result['confidence'] >= 0.4:
            result['_status'] = 'review'
        else:
            result['_status'] = 'failed'

        return result

    def _parse_loose_bool(self, value: str) -> Optional[bool]:
        value = value.strip().lower()
        if any(x in value for x in ['可', '是', '能', '有', 'yes', 'true', '🉑', '✅', '行', '好']):
            return True
        if any(x in value for x in ['否', '不', '没', '无', 'no', 'false', '❌', '不可']):
            return False
        return None

    def _extract_number(self, value: str) -> Optional[int]:
        nums = re.findall(r'\d+', value)
        return int(nums[0]) if nums else None

    def _extract_money(self, value: str) -> Optional[float]:
        value = value.replace(',', '').replace(' ', '')
        match = re.search(r'([\d.]+)\s*[wW万]', value)
        if match:
            return float(match.group(1)) * 10000
        match = re.search(r'([\d.]+)', value)
        if match:
            return float(match.group(1))
        return None
